#!/usr/bin/env python3
"""
vibe-secure.py — `vibe-local --secure` 用の OpenCode 権限ロック (stdlib のみ)

OpenCode の設定は「グローバル → OPENCODE_CONFIG → プロジェクト → インライン」の順に
合成され、さらにエージェント別の permission がトップレベルの permission より後に
評価される (後勝ち)。そのため「既定設定に deny と書く」だけでは、
~/.config/opencode のエージェント別設定やカスタムエージェントで上書きされうる。

このスクリプトは次を行う:
  1. 呼び出し元の環境 (OPENCODE_DISABLE_PROJECT_CONFIG=1 等を設定済み) で
     `opencode debug config` を実行し、解決済みの agent / mcp / plugin を得る
  2. 全エージェント (組み込み + 設定由来) に同じ権限を上書きし、
     全 MCP を enabled:false にするインライン設定 (OPENCODE_CONFIG_CONTENT) を作る
  3. そのインライン設定を入れた状態で `opencode agent list` を実行し、
     実効ルールセットを検査する。1つでも外れていれば失敗 (fail closed)

使い方:
  vibe-secure.py prepare --opencode BIN --out FILE
      成功: FILE にインライン設定 JSON を書き、終了コード 0
      失敗: 理由を stderr に出し、終了コード 1 (FILE は作らない)
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

# 全エージェントで強制する実効アクション
REQUIRED = {
    "bash": "ask",
    "edit": "ask",
    "webfetch": "deny",
    "websearch": "deny",
    "codesearch": "deny",
}

# 組み込みエージェント (設定に現れなくても常に上書き対象にする)
BUILTIN_AGENTS = ["build", "plan", "general", "explore", "compaction", "summary", "title"]

AGENT_HEADER = re.compile(r"^(\S+) \((\w+)\)\s*$")


def run_oc(binary, args, env, timeout=60):
    p = subprocess.run([binary] + args, env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"`opencode {' '.join(args)}` failed (rc={p.returncode}): {p.stderr.strip()[:400]}")
    return p.stdout


def build_inline(resolved):
    """解決済み設定から、全エージェント上書き + 全MCP無効化のインライン設定を作る。"""
    names = list(BUILTIN_AGENTS)
    for n in (resolved.get("agent") or {}):
        if n not in names:
            names.append(n)
    perm = dict(REQUIRED)
    inline = {
        "permission": perm,
        "agent": {n: {"permission": dict(perm)} for n in names},
        "mcp": {n: {"enabled": False} for n in (resolved.get("mcp") or {})},
        "share": "disabled",
        "autoupdate": False,
    }
    return inline


def parse_agent_list(text):
    """`opencode agent list` の出力を {name: [rules]} に分解する。"""
    agents, name, buf = {}, None, []

    def flush():
        if name is not None:
            raw = "\n".join(buf).strip()
            agents[name] = json.loads(raw) if raw else []

    for line in text.splitlines():
        m = AGENT_HEADER.match(line)
        if m:
            flush()
            name, buf = m.group(1), []
        elif name is not None:
            buf.append(line)
    flush()
    return agents


def effective(rules, permission):
    """OpenCode の評価規則 (後勝ち) で、permission に最後に一致するルールを返す。"""
    last = None
    for r in rules:
        if fnmatch.fnmatchcase(permission, r.get("permission", "")):
            last = r
    return last


def verify(agents, resolved):
    problems = []
    if not agents:
        problems.append("エージェント一覧を取得できませんでした")
    for name, rules in agents.items():
        for perm, want in REQUIRED.items():
            r = effective(rules, perm)
            if r is None or r.get("action") != want or r.get("pattern") != "*":
                problems.append(f"agent '{name}': {perm} の実効値が {want} ではありません ({r})")
    for n, m in (resolved.get("mcp") or {}).items():
        if not isinstance(m, dict) or m.get("enabled", True) is not False:
            problems.append(f"MCP '{n}' が無効化されていません")
    if resolved.get("plugin"):
        problems.append(f"プラグインが読み込まれます: {resolved.get('plugin')}")
    return problems


def prepare(binary, out_path):
    env = dict(os.environ)
    env.pop("OPENCODE_CONFIG_CONTENT", None)
    resolved = json.loads(run_oc(binary, ["debug", "config"], env))
    inline = build_inline(resolved)

    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline)
    resolved2 = json.loads(run_oc(binary, ["debug", "config"], env))
    # インライン適用後にエージェントが増えていれば (通常ありえない) 追加して再構成
    extra = [n for n in (resolved2.get("agent") or {}) if n not in inline["agent"]]
    if extra:
        resolved.setdefault("agent", {}).update({n: {} for n in extra})
        inline = build_inline(resolved)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline)
        resolved2 = json.loads(run_oc(binary, ["debug", "config"], env))

    agents = parse_agent_list(run_oc(binary, ["agent", "list"], env))
    problems = verify(agents, resolved2)
    if problems:
        for p in problems:
            print(f"vibe-secure: {p}", file=sys.stderr)
        return 1

    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(inline, f)
    os.replace(tmp, out_path)
    print(f"vibe-secure: OK ({len(agents)} agents, {len(resolved2.get('mcp') or {})} MCP disabled)",
          file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--opencode", required=True)
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    try:
        return prepare(a.opencode, a.out)
    except Exception as e:  # 失敗は常に起動拒否側へ
        print(f"vibe-secure: 検証に失敗しました: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
