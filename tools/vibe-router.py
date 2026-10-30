#!/usr/bin/env python3
"""vibe-router — モデル自動振り分けパススルー (stdlibのみ、変換なし)

opencode (OpenAI互換クライアント) と Ollama の間に挟まる極小ルーター。
仮想モデル "vibe-auto" へのリクエストだけ、内容を見て実モデルに振り分ける:

  - 雑談・短い質問            → fast (小型モデル、thinking無効化で即答)
  - コーディング・ツール使用中 → main (大型モデル)

それ以外のパス・モデル名はバイトそのままの素通し。v1(自作プロキシ)のような
API形式変換は一切しない = 壊れる場所がない。

使い方:
  vibe-router.py --port 11435 --upstream http://localhost:11434 \
                 --main vibe-coder --fast vibe-fast --sig <識別文字列>

ヘルスチェック: GET /vibe-router/health → {"sig": ..., "main": ..., "fast": ...}

kaizen (学び続けるループの 検証→記録):
  /chat/completions の応答を素通ししながら写しを取り、ROADMAPの失敗パターン
  (文章だけで返す・質問で終わる・ユーザーに実行させる・簡体字混入 等) を判定して
  --events のJSONLに1応答1行で追記する。記録は種類・件数・時間だけで、
  会話本文は一切残さない (機密データを扱う環境で使うため)。
  集計は tools/vibe-kaizen.py。--events "" で無効。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AUTO_MODEL = "vibe-auto"

# --- kaizen: 応答の判定 ---------------------------------------------------
# 判定の対象はツールを渡されたエージェント応答だけ (タイトル生成等は除く)
SLOW_MS = 60000
ASKS_USER_TO_RUN = re.compile(
    r"ターミナルで(?:実行|入力)|以下のコマンドを(?:実行|入力)してください|"
    r"コマンドを(?:実行|入力)してください|"
    r"run (?:the following|this) command|in your terminal",
    re.IGNORECASE,
)
KANA = re.compile(r"[぀-ヿ]")
# 日本語では使わない簡体字 (P1-4 の実例「或者」等の周辺)
SIMPLIFIED_ONLY = re.compile(r"[这们么说时进还过对发经问实现个吗呢]")


def last_user_text(body):
    for m in reversed(body.get("messages") or []):
        if m.get("role") == "user":
            return text_of(m.get("content"))
    return ""


def kaizen_flags(n_tools, user_text, scan, status):
    """1応答の判定結果 (パターンIDの一覧)。本文はここで捨てる。"""
    flags = []
    if status >= 400:
        flags.append("upstream_error")
        return flags
    if scan.parse_error:
        flags.append("parse_error")
    text = scan.text.strip()
    if n_tools > 0:
        if not text and scan.tool_calls == 0:
            flags.append("empty_response")
        if scan.tool_calls == 0 and text:
            flags.append("text_only")                     # P0-1 (率で見る)
            if text[-1:] in ("?", "？"):
                flags.append("ends_with_question")        # P0-2
        if ASKS_USER_TO_RUN.search(text):
            flags.append("asks_user_to_run")              # P0-3
    if KANA.search(user_text) and SIMPLIFIED_ONLY.search(text):
        flags.append("simplified_zh")                     # P1-4
    if scan.latency_ms > SLOW_MS:
        flags.append("slow")
    return flags


class RespScan:
    """応答 (JSON または SSE) を流れのまま読み、本文とツール呼び出し数を数える"""

    MAX_TEXT = 20000   # 判定に要るのは末尾と語句だけ

    def __init__(self, streaming):
        self.streaming = streaming
        self.buf = b""
        self.text = ""
        self.tool_idx = set()
        self.parse_error = False
        self.t0 = time.monotonic()
        self.latency_ms = 0

    @property
    def tool_calls(self):
        return len(self.tool_idx)

    def _take(self, choice):
        part = choice.get("delta") or choice.get("message") or {}
        c = part.get("content")
        if isinstance(c, str) and c:
            self.text = (self.text + c)[-self.MAX_TEXT:]
        for i, tc in enumerate(part.get("tool_calls") or []):
            self.tool_idx.add(tc.get("index", i) if isinstance(tc, dict) else i)

    def feed(self, chunk):
        try:
            self.buf += chunk
            if not self.streaming:
                return
            *lines, self.buf = self.buf.split(b"\n")
            for line in lines:
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if not data or data == b"[DONE]":
                    continue
                for ch in json.loads(data).get("choices") or []:
                    self._take(ch)
        except Exception:
            self.parse_error = True

    def finish(self):
        self.latency_ms = int((time.monotonic() - self.t0) * 1000)
        try:
            if self.streaming:
                self.feed(b"\n")
            elif self.buf:
                for ch in json.loads(self.buf).get("choices") or []:
                    self._take(ch)
        except Exception:
            self.parse_error = True
        self.buf = b""


class EventLog:
    """1応答1行のJSONL。書き込みに失敗しても転送は止めない"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        if path:
            try:
                os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            except Exception:
                self.path = ""

    def write(self, event):
        if not self.path:
            return
        try:
            line = json.dumps(event, ensure_ascii=False) + "\n"
            with self.lock:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception:
            pass

# コーディング/作業タスクを示す語 (どれかを含めば main 行き)
WORK_WORDS = re.compile(
    r"作っ|書い|実装|修正|直し|なおし|デバッグ|動かし|実行|テスト|ファイル|コード|"
    r"エラー|バグ|関数|リファクタ|インストール|ビルド|デプロイ|解析|分析|調べ|"
    r"読ん|検索|保存|開い|消し|削除|変更|追加|作成|生成して|"
    r"\bcode\b|\bwrite\b|\bcreate\b|\bmake\b|\bbuild\b|\bfix\b|\bdebug\b|\brun\b|"
    r"\btest\b|\binstall\b|\bimplement\b|\brefactor\b|\bfile\b|\berror\b|\bbug\b|"
    r"\bscript\b|\bdeploy\b|\banalyze\b|\bsearch\b|\bread\b|\bgit\b|\bcommit\b|"
    r"\bfunction\b|\bclass\b|\bapi\b",
    re.IGNORECASE,
)


def text_of(content):
    """OpenAIのcontent (str または parts配列) からテキストを取り出す"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def has_non_text(content):
    if isinstance(content, list):
        return any(isinstance(p, dict) and p.get("type") not in (None, "text")
                   for p in content)
    return False


def pick_model(body, main, fast):
    """vibe-auto の振り分け。判断できない時は常に main (安全側)。"""
    msgs = body.get("messages") or []
    # ツールを既に使った会話 = 作業セッション → main 固定 (一貫性・ツール品質)
    for m in msgs:
        if m.get("role") == "tool":
            return main, "tool-in-history"
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return main, "tool-calls-in-history"
    last = None
    for m in reversed(msgs):
        if m.get("role") == "user":
            last = m
            break
    if last is None:
        return main, "no-user-message"
    if has_non_text(last.get("content")):
        return main, "attachment"
    t = text_of(last.get("content")).strip()
    if not t or len(t) > 240:
        return main, f"length={len(t)}"
    if "```" in t or t.count("\n") >= 3:
        return main, "code-block"
    if WORK_WORDS.search(t):
        return main, "work-keyword"
    return fast, "chat"


def make_handler(upstream, main, fast, sig, events=None):
    events = events or EventLog("")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _health(self):
            payload = json.dumps(
                {"sig": sig, "main": main, "fast": fast, "upstream": upstream}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _forward(self, body, meta=None):
            url = upstream + self.path
            scan = None
            status = 0
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "content-length",
                                            "connection", "accept-encoding")}
            req = urllib.request.Request(url, data=body, headers=headers,
                                         method=self.command)
            try:
                resp = urllib.request.urlopen(req, timeout=600)
            except urllib.error.HTTPError as e:
                resp = e   # エラー応答もそのまま返す
            except Exception as e:
                msg = json.dumps({"error": {"message": f"vibe-router: upstream unreachable: {e}"}}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                self._record(meta, None, 502)
                return
            with resp:
                status = resp.status
                self.send_response(resp.status)
                length = resp.headers.get("Content-Length")
                if meta is not None:
                    scan = RespScan(streaming=length is None)
                    scan.t0 = meta["t0"]   # 待ち時間はリクエスト送信から数える
                for k, v in resp.getheaders():
                    if k.lower() in ("transfer-encoding", "connection",
                                     "content-length"):
                        continue
                    self.send_header(k, v)
                if length is not None:
                    self.send_header("Content-Length", length)
                    self.end_headers()
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        if scan:
                            scan.feed(chunk)
                else:
                    # ストリーミング(SSE等) → chunked で流し込む
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        self.wfile.write(b"%x\r\n" % len(chunk))
                        self.wfile.write(chunk)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                        if scan:
                            scan.feed(chunk)
                    self.wfile.write(b"0\r\n\r\n")
            self._record(meta, scan, status)

        def _record(self, meta, scan, status):
            """kaizen: 判定して1行記録。本文は残さない"""
            if meta is None:
                return
            try:
                t0 = meta.pop("t0")
                if scan is None:
                    scan = RespScan(streaming=False)
                    scan.t0 = t0
                scan.finish()
                flags = kaizen_flags(meta["n_tools"], meta.pop("user_text"),
                                     scan, status)
                events.write(dict(meta, status=status,
                                  latency_ms=scan.latency_ms,
                                  out_chars=len(scan.text),
                                  tool_calls=scan.tool_calls,
                                  flags=flags))
            except Exception:
                pass

        def do_GET(self):
            if self.path == "/vibe-router/health":
                self._health()
                return
            self._forward(None)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            meta = None
            if self.path.endswith("/chat/completions"):
                try:
                    d = json.loads(body)
                    size_note = (f"{len(body)//1024}KB "
                                 f"msgs={len(d.get('messages') or [])} "
                                 f"tools={len(d.get('tools') or [])}")
                    why = "pinned"
                    if d.get("model") == AUTO_MODEL:
                        model, why = pick_model(d, main, fast)
                        d["model"] = model
                    # 小型モデル宛は常にthinkingを切る。qwen系小型は思考ループで
                    # タイトル生成に数十秒溶かし、同一モデルのキューを塞ぐ
                    # (実測: thinkingあり15-29s / なし0.2s)
                    if d.get("model") == fast:
                        d.setdefault("reasoning_effort", "none")
                    body = json.dumps(d).encode()
                    print(f"route: {d.get('model')} ({why}) {size_note}", flush=True)
                    meta = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "t0": time.monotonic(),
                            "model": d.get("model"), "why": why,
                            "n_msgs": len(d.get("messages") or []),
                            "n_tools": len(d.get("tools") or []),
                            "user_text": last_user_text(d)}
                except Exception as e:
                    print(f"route: passthrough (parse error: {e})", flush=True)
            self._forward(body, meta)

        def do_DELETE(self):
            self._forward(None)

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--upstream", default="http://localhost:11434")
    ap.add_argument("--main", default="vibe-coder")
    ap.add_argument("--fast", default="vibe-fast")
    ap.add_argument("--sig", default="dev")
    ap.add_argument("--events", default="",
                    help="kaizenの記録先JSONL (空なら記録しない)")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(
        ("127.0.0.1", a.port),
        make_handler(a.upstream.rstrip("/"), a.main, a.fast, a.sig,
                     EventLog(a.events)),
    )
    print(f"vibe-router: 127.0.0.1:{a.port} -> {a.upstream} "
          f"(auto: chat->{a.fast} / work->{a.main})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
