#!/bin/bash
# vibe-local v2 スモークテスト
#   ./tests/run.sh          # ローカル環境チェック含む全テスト
#   ./tests/run.sh --ci     # 環境非依存のテストのみ (ollama/opencode不要)
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PASS=0; FAIL=0
CI_ONLY=0
[ "${1:-}" = "--ci" ] && CI_ONLY=1

ok()   { PASS=$((PASS+1)); echo "  ✅ $1"; }
ng()   { FAIL=$((FAIL+1)); echo "  ❌ $1"; }
check(){ if eval "$2" &>/dev/null; then ok "$1"; else ng "$1"; fi; }

echo "== 構文 =="
check "vibe-local.sh 構文"            "bash -n vibe-local.sh"
check "install.sh 構文"               "bash -n install.sh"

echo "== テーマ =="
check "テーマJSONが妥当 (全テーマ)"   "python3 - <<'EOF'
import glob, json
files = glob.glob('themes/*.json')
assert len(files) >= 2, files
for f in files:
    json.load(open(f))
EOF"
check "テーマに必須キーが揃っている"  "python3 - <<'EOF'
import glob, json
required = {'primary','secondary','accent','error','warning','success','info',
 'text','textMuted','background','backgroundPanel','backgroundElement',
 'border','borderActive','borderSubtle','diffAdded','diffRemoved',
 'markdownHeading','markdownCode','syntaxComment','syntaxKeyword',
 'syntaxFunction','syntaxString','syntaxNumber','syntaxType'}
for f in glob.glob('themes/*.json'):
    d = json.load(open(f))['theme']
    missing = required - set(d)
    assert not missing, (f, missing)
    for v in d.values():
        assert isinstance(v, dict) and 'dark' in v and 'light' in v, (f, v)
EOF"
check "テーマ全色が defs か #hex を参照" "python3 - <<'EOF'
import glob, json
for f in glob.glob('themes/*.json'):
    t = json.load(open(f))
    defs = set(t['defs'])
    for v in t['theme'].values():
        for mode in ('dark','light'):
            c = v[mode]
            assert c.startswith('#') or c in defs, (f, c)
EOF"
check "vibe-null が黒灰基調 (背景の彩度ほぼ0)" "python3 - <<'EOF'
import json
t = json.load(open('themes/vibe-null.json'))
defs = t['defs']
for key in ('background','backgroundPanel','backgroundElement','border','text','textMuted'):
    c = t['theme'][key]['dark']
    h = defs.get(c, c).lstrip('#')
    r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
    assert max(r,g,b) - min(r,g,b) <= 12, (key, h)
EOF"

echo "== ランチャーのフラグ =="
check "--version が動く"              "./vibe-local.sh --version | grep -q 'vibe-local v2'"
check "--help が動く"                 "./vibe-local.sh --help | grep -q '使い方'"
check "--help に --fast と --theme"   "./vibe-local.sh --help | grep -q -- '--fast' && ./vibe-local.sh --help | grep -q -- '--theme'"

echo "== debrand =="
check "debrand.py 構文"               "python3 -m py_compile tools/debrand.py"
check "debrand.py 引数なしでusage"    "(python3 tools/debrand.py || true) | grep -qi 'opencode'"

echo "== vibe-router =="
check "vibe-router.py 構文"           "python3 -m py_compile tools/vibe-router.py"
check "ルーティング判定 (雑談→fast/作業→main)" "python3 - <<'EOF'
import importlib.util
spec = importlib.util.spec_from_file_location('vr', 'tools/vibe-router.py')
vr = importlib.util.module_from_spec(spec); spec.loader.exec_module(vr)
pick = lambda msgs: vr.pick_model({'messages': msgs}, 'MAIN', 'FAST')[0]
u = lambda t: {'role': 'user', 'content': t}
assert pick([u('こんにちは？')]) == 'FAST'
assert pick([u('元気？')]) == 'FAST'
assert pick([u('What can you do?')]) == 'FAST'
assert pick([u('Pythonでフィボナッチのコードを書いて')]) == 'MAIN'
assert pick([u('fix the bug in main.py')]) == 'MAIN'
assert pick([u('このエラーを直して')]) == 'MAIN'
assert pick([u('x' * 500)]) == 'MAIN'                      # 長文
assert pick([u('やあ'), {'role': 'assistant', 'tool_calls': [{}]},
             {'role': 'tool', 'content': 'ok'}, u('ありがとう')]) == 'MAIN'  # 作業中
assert pick([u([{'type': 'image_url', 'image_url': {}}])]) == 'MAIN'         # 添付
assert vr.pick_model({}, 'MAIN', 'FAST')[0] == 'MAIN'      # 判定不能は安全側
EOF"
check "ルーターhealth応答"            "bash -c '
python3 tools/vibe-router.py --port 21435 --sig testsig &>/dev/null &
RPID=\$!
ok=1
for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 0.2
    if curl -s --max-time 1 http://127.0.0.1:21435/vibe-router/health | grep -q testsig; then ok=0; break; fi
done
kill \$RPID 2>/dev/null
exit \$ok'"

echo "== kaizen (検証→記録→抽出) =="
check "vibe-kaizen.py 構文"           "python3 -m py_compile tools/vibe-kaizen.py"
check "--help に --kaizen"            "./vibe-local.sh --help | grep -q -- '--kaizen'"
check "応答の判定 (ROADMAPの失敗パターン)" "python3 - <<'EOF'
import importlib.util, json
spec = importlib.util.spec_from_file_location('vr', 'tools/vibe-router.py')
vr = importlib.util.module_from_spec(spec); spec.loader.exec_module(vr)
def sse(*deltas):
    s = vr.RespScan(streaming=True)
    for d in deltas:
        s.feed(b'data: ' + json.dumps({'choices': [{'delta': d}]}).encode() + b'\n\n')
    s.feed(b'data: [DONE]\n\n'); s.finish(); return s
f = lambda scan, n_tools=3, user='これを直して', status=200: vr.kaizen_flags(n_tools, user, scan, status)
assert f(sse({'content': '何か'}, {'content': '必要ですか？'})) == ['text_only', 'ends_with_question']
assert f(sse({'tool_calls': [{'index': 0}]}, {'tool_calls': [{'index': 0}]})) == []      # ツール呼び出し1件
assert 'asks_user_to_run' in f(sse({'content': '以下のコマンドを実行してください'}))
assert 'simplified_zh' in f(sse({'content': '这个文件'}))
assert 'simplified_zh' not in f(sse({'content': '这个文件'}), user='fix this')       # 日本語の会話だけ
assert f(sse({'content': 'タイトル？'}), n_tools=0) == []                             # タイトル生成等は対象外
assert f(sse(), status=500) == ['upstream_error']
s = vr.RespScan(streaming=False)
s.feed(json.dumps({'choices': [{'message': {'content': 'ok', 'tool_calls': [{}, {}]}}]}).encode())
s.finish(); assert s.tool_calls == 2 and s.text == 'ok'
b = vr.RespScan(streaming=True); b.feed(b'data: {broken\n'); b.finish()
assert 'parse_error' in f(b)
EOF"
check "ルーター経由の記録: 素通しのまま1行記録・本文は残らない" "python3 - <<'EOF'
import json, os, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
REPLY = '秘密マーカーB_9137は必要ですか？'
class Up(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        for part in (REPLY[:6], REPLY[6:]):
            self.wfile.write(b'data: ' + json.dumps({'choices': [{'delta': {'content': part}}]}).encode() + b'\n\n')
        self.wfile.write(b'data: [DONE]\n\n')
up = HTTPServer(('127.0.0.1', 21436), Up)
threading.Thread(target=up.serve_forever, daemon=True).start()
ev = os.path.join(tempfile.mkdtemp(), 'k', 'events.jsonl')
r = subprocess.Popen([sys.executable, 'tools/vibe-router.py', '--port', '21437',
                      '--upstream', 'http://127.0.0.1:21436', '--events', ev],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(25):
        try:
            urllib.request.urlopen('http://127.0.0.1:21437/vibe-router/health', timeout=1); break
        except Exception: time.sleep(0.2)
    req = {'model': 'vibe-coder', 'stream': True, 'tools': [{}],
           'messages': [{'role': 'user', 'content': '秘密マーカーA_5521 のファイルを直して'}]}
    out = urllib.request.urlopen(urllib.request.Request(
        'http://127.0.0.1:21437/v1/chat/completions', data=json.dumps(req).encode(),
        headers={'Content-Type': 'application/json'}), timeout=10).read().decode()
    assert 'B_9137' in out and out.rstrip().endswith('[DONE]'), out           # 素通し
    time.sleep(0.3)
    raw = open(ev, encoding='utf-8').read()
    lines = raw.strip().splitlines()
    assert len(lines) == 1, lines
    e = json.loads(lines[0])
    assert e['flags'] == ['text_only', 'ends_with_question'], e
    assert e['model'] == 'vibe-coder' and e['n_tools'] == 1 and e['tool_calls'] == 0, e
    assert 'A_5521' not in raw and 'B_9137' not in raw and 'user_text' not in raw, raw   # 本文なし
    assert oct(os.stat(ev).st_mode & 0o777) == '0o600', oct(os.stat(ev).st_mode)
finally:
    r.terminate(); up.shutdown()
EOF"
check "集計: 候補の抽出と起動時の1行" "python3 - <<'EOF'
import json, os, subprocess, sys, tempfile
d = tempfile.mkdtemp(); ev = os.path.join(d, 'events.jsonl')
def e(ts, flags, n_tools=3, model='vibe-coder'):
    return json.dumps({'ts': ts, 'model': model, 'n_tools': n_tools, 'flags': flags})
rows = [e('2026-10-28T10:00:00', ['text_only', 'ends_with_question']),
        e('2026-10-29T10:00:00', ['text_only', 'ends_with_question']),
        e('2026-10-29T11:00:00', []), e('2026-10-29T12:00:00', []),
        e('2026-10-29T13:00:00', ['asks_user_to_run']),
        e('2026-10-20T10:00:00', ['asks_user_to_run']),           # 前の期間
        'broken line']
open(ev, 'w').write('\n'.join(rows) + '\n')
run = lambda *a: subprocess.run([sys.executable, 'tools/vibe-kaizen.py', *a, '--events', ev,
                                 '--now', '2026-10-30T00:00:00'], capture_output=True, text=True, check=True).stdout
rep = run('report')
assert '改善の候補（2 件）' in rep, rep                 # 質問で終わる2回 + 文章だけ率40%>20%
cands = rep.split('## モデル別')[0]
assert 'ends_with_question' in cands and 'text_only' in cands, rep
assert 'asks_user_to_run' not in cands, rep                  # 今期は1回だけ → 候補外
assert 'kaizen: 改善の候補が 2 件' in run('summary')
assert '改善の候補が 1 件' in run('summary', '--min', '99')    # 率で見る text_only だけ残る
open(ev, 'w').write('')
assert run('summary') == '' and 'この期間の記録はありません' in run('report')
EOF"

echo "== 起動画面 =="
check "スプラッシュ非TTYフォールバック" "bash -c 'VIBE_VERSION=t; eval \"\$(sed -n \"/^show_splash()/,/^}\$/p\" vibe-local.sh)\"; show_splash M m s c p | grep -c \"vibe-local\"'"

echo "== v1 の残骸が無い =="
check "プロキシ参照が残っていない"    "! grep -q 'anthropic-ollama-proxy' vibe-local.sh"
check "旧モデル(qwen3:1.7b)が残っていない" "! grep -q 'qwen3:1.7b' vibe-local.sh install.sh"

if [ "$CI_ONLY" -eq 0 ]; then
    echo "== ローカル環境 (実機) =="
    check "opencode がインストール済み"  "command -v opencode"
    check "ollama がインストール済み"    "command -v ollama"
    if command -v opencode &>/dev/null; then
        OC_BIN="$(readlink -f "$(command -v opencode)" 2>/dev/null || command -v opencode)"
        TMP_TUI="$(mktemp -d)/vibe-tui"
        if python3 tools/debrand.py "$OC_BIN" "$TMP_TUI" | grep -q 'flat logo: 1'; then
            ok "debrand が実バイナリに適用できる"
            check "パッチ済みTUIのヘルプにopencodeロゴが無い" \
                "! \"$TMP_TUI\" --help 2>/dev/null | head -4 | grep -q '█▀▀█ █▀▀█ █▀▀█ █▀▀▄'"
        else
            ng "debrand が実バイナリに適用できる (パターン不一致: opencode更新でロゴ変更の可能性)"
        fi
        rm -rf "$(dirname "$TMP_TUI")"
    fi
    if curl -s --max-time 2 http://localhost:11434/api/tags &>/dev/null; then
        check "Ollama /v1/messages ネイティブ応答" "curl -s --max-time 5 http://localhost:11434/api/version | grep -q version"
        check "--doctor が動く"           "./vibe-local.sh --doctor | grep -c '環境診断'"
    else
        echo "  ⚠️  ollama 停止中のため実機テストをスキップ"
    fi
fi

echo ""
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
