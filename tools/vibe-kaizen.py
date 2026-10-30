#!/usr/bin/env python3
"""vibe-kaizen — 学び続けるループの「抽出」(stdlibのみ)

vibe-router が --events に書いたJSONL (1応答1行・本文なし) を集計し、
モデル × 失敗パターンごとの件数と率を、前の期間と比べて出す。
改善の候補に印をつけるだけで、プロンプトや設定は書き換えない
(格上げは人が決める)。

使い方:
  vibe-kaizen.py report  [--events PATH] [--days 7] [--min 2]
  vibe-kaizen.py summary [--events PATH] [--days 7] [--min 2]   # 起動時の1行
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

DEFAULT_EVENTS = os.path.expanduser("~/.local/state/vibe-local/kaizen/events.jsonl")

# id: (説明, ROADMAPの番号, 分母, 目標率%)
#   分母 agent = ツールを渡したエージェント応答 / all = 全応答
#   目標率がある指標は「起きて当然の率」を見る (件数でなく率が目標を超えたら候補)
PATTERNS = {
    "text_only":          ("ツールが使えるのに文章だけで返した", "P0-1", "agent", 20.0),
    "ends_with_question": ("最後が質問で終わった",               "P0-2", "agent", None),
    "asks_user_to_run":   ("コマンドの実行をユーザーに頼んだ",   "P0-3", "agent", None),
    "simplified_zh":      ("日本語の返答に簡体字が混ざった",     "P1-4", "all",   None),
    "empty_response":     ("本文もツール呼び出しもない応答",     "-",    "agent", None),
    "slow":               ("応答に60秒以上かかった",             "P2",   "all",   None),
    "upstream_error":     ("上流(Ollama)のエラー",               "-",    "all",   None),
    "parse_error":        ("応答を読み取れなかった",             "-",    "all",   None),
}


def load(path):
    events = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    e["_ts"] = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%S")
                    events.append(e)
                except Exception:
                    continue   # 壊れた行は飛ばす
    except FileNotFoundError:
        pass
    return events


def tally(events):
    """model -> {"all": n, "agent": n, "flags": Counter}"""
    t = defaultdict(lambda: {"all": 0, "agent": 0, "flags": Counter()})
    for e in events:
        s = t[e.get("model") or "?"]
        s["all"] += 1
        if (e.get("n_tools") or 0) > 0:
            s["agent"] += 1
        s["flags"].update(set(e.get("flags") or []))
    return t


def rate(n, d):
    return 100.0 * n / d if d else None


def analyze(events, now, days, min_count):
    cur_from = now - timedelta(days=days)
    prev_from = cur_from - timedelta(days=days)
    cur = tally(e for e in events if e["_ts"] >= cur_from)
    prev = tally(e for e in events if prev_from <= e["_ts"] < cur_from)
    rows = []
    for model, s in sorted(cur.items()):
        for pid, (label, ref, denom, target) in PATTERNS.items():
            n = s["flags"][pid]
            d = s[denom]
            r = rate(n, d)
            p = prev.get(model)
            pr = rate(p["flags"][pid], p[denom]) if p else None
            if target is not None:
                cand = r is not None and r > target
                why = f"率が目標{target:.0f}%を超えた" if cand else ""
            else:
                cand = n >= min_count
                why = f"{n}回起きた" if cand else ""
            if cand and pr is not None and r is not None and r > pr:
                why += "・前の期間より悪化"
            if n or cand:
                rows.append(dict(model=model, pid=pid, label=label, ref=ref,
                                 n=n, d=d, rate=r, prev=pr, cand=cand, why=why))
    return cur, rows


def fmt_rate(r):
    return "-" if r is None else f"{r:.0f}%"


def report(events, now, days, min_count, out=sys.stdout):
    cur, rows = analyze(events, now, days, min_count)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    n_all = sum(s["all"] for s in cur.values())
    n_agent = sum(s["agent"] for s in cur.values())
    w = out.write
    w(f"# vibe-local kaizen 報告（{start} 以降の{days}日間、前の{days}日間と比較）\n\n")
    w(f"記録: 応答 {n_all} 件 / うちエージェント応答 {n_agent} 件"
      "（会話の本文は記録していません）\n\n")
    if not n_all:
        w("この期間の記録はありません。\n")
        return 0
    cands = [r for r in rows if r["cand"]]
    w(f"## 改善の候補（{len(cands)} 件）\n\n")
    if cands:
        w("| モデル | パターン | ROADMAP | 件数 | 率 | 前の期間 | 根拠 |\n")
        w("|---|---|---|---|---|---|---|\n")
        for r in cands:
            w(f"| {r['model']} | {r['label']} (`{r['pid']}`) | {r['ref']} | "
              f"{r['n']}/{r['d']} | {fmt_rate(r['rate'])} | {fmt_rate(r['prev'])} | {r['why']} |\n")
        w("\n格上げ(プロンプト・ルーターへの反映)は人が判断します。"
          "このコマンドは何も書き換えません。\n\n")
    else:
        w("なし\n\n")
    w("## モデル別の件数\n\n| モデル | 応答 | エージェント応答 |\n|---|---|---|\n")
    for model, s in sorted(cur.items()):
        w(f"| {model} | {s['all']} | {s['agent']} |\n")
    return len(cands)


def summary(events, now, days, min_count, out=sys.stdout):
    _, rows = analyze(events, now, days, min_count)
    n = sum(1 for r in rows if r["cand"])
    if n:
        out.write(f"kaizen: 改善の候補が {n} 件あります（vibe-local --kaizen で表示）\n")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="vibe-local kaizen 集計")
    ap.add_argument("cmd", choices=["report", "summary"])
    ap.add_argument("--events", default=DEFAULT_EVENTS)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min", type=int, default=2, dest="min_count")
    ap.add_argument("--now", default="", help=argparse.SUPPRESS)   # テスト用
    a = ap.parse_args(argv)
    now = (datetime.strptime(a.now, "%Y-%m-%dT%H:%M:%S") if a.now
           else datetime.now())
    fn = report if a.cmd == "report" else summary
    fn(load(a.events), now, a.days, a.min_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
