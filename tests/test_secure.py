#!/usr/bin/env python3
"""
vibe-local --secure のテスト (stdlib unittest)

  python3 tests/test_secure.py            # 全テスト (実物の opencode があれば統合テストも)
  VIBE_TEST_OPENCODE=/path/to/opencode python3 tests/test_secure.py

統合テストは、敵対的なグローバル設定・プロジェクト設定を一時 HOME に置き、
ランチャーと同じ環境変数で vibe-secure.py を通したあと、
`opencode agent list` の実効ルールセットを全エージェントについて検査する。
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "vibe-local.sh")
HELPER = os.path.join(ROOT, "tools", "vibe-secure.py")

spec = importlib.util.spec_from_file_location("vibe_secure", HELPER)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)

# ランチャーが --secure 時に設定する環境変数 (vibe-local.sh と揃えること)
SECURE_ENV = {
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_PURE": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
}


def isolated_env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENCODE_")}
    env.update({
        "HOME": home,
        "XDG_CONFIG_HOME": os.path.join(home, ".config"),
        "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
        "XDG_STATE_HOME": os.path.join(home, ".local", "state"),
        "XDG_CACHE_HOME": os.path.join(home, ".cache"),
        "VIBE_LOCAL_NO_SPLASH": "1",
    })
    return env


class TestLauncherRejects(unittest.TestCase):
    """制限を保証できない起動経路は、ollama/opencode に触れる前に拒否される。"""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = isolated_env(self.home)
        self.env["OLLAMA_HOST"] = "http://127.0.0.1:11434"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_launcher(self, *args, env=None):
        return subprocess.run(["bash", LAUNCHER, "--secure", *args], env=env or self.env,
                              capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def assert_rejected(self, args, needle):
        p = self.run_launcher(*args)
        self.assertEqual(p.returncode, 2, (args, p.stdout, p.stderr))
        self.assertIn("併用できない", p.stderr)
        self.assertIn(needle, p.stderr)

    def test_auto(self):
        self.assert_rejected(["--auto"], "--auto")

    def test_classic(self):
        self.assert_rejected(["--classic"], "--classic")

    def test_vibe_coder(self):
        self.assert_rejected(["--vibe-coder"], "--vibe-coder")
        self.assert_rejected(["--engine"], "--vibe-coder")

    def test_serve(self):
        self.assert_rejected(["--serve"], "--serve")

    def test_attach(self):
        self.assert_rejected(["--attach", "http://10.0.0.2:4096"], "--attach")

    def test_yes(self):
        self.assert_rejected(["-y"], "-y")
        self.assert_rejected(["--yes"], "-y")

    def test_passthrough_subcommands(self):
        self.assert_rejected(["web"], "'web'")
        self.assert_rejected(["serve"], "'serve'")
        self.assert_rejected(["--hostname", "0.0.0.0"], "'--hostname'")

    def test_two_project_dirs(self):
        self.assert_rejected([self.home, self.home], self.home)

    def test_remote_ollama(self):
        env = dict(self.env, OLLAMA_HOST="http://192.168.0.10:11434")
        p = self.run_launcher(env=env)
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("OLLAMA_HOST", p.stderr)

    def test_all_reasons_listed_at_once(self):
        p = self.run_launcher("--auto", "--classic", "-y")
        self.assertEqual(p.returncode, 2)
        for n in ("--auto", "--classic", "-y"):
            self.assertIn(n, p.stderr)


class TestLoopback(unittest.TestCase):
    def check(self, url):
        script = ('eval "$(sed -n \'/^is_loopback_url/,/^}/p\' "$1")"; is_loopback_url "$2"')
        return subprocess.run(["bash", "-c", script, "_", LAUNCHER, url]).returncode == 0

    def test_loopback(self):
        for u in ("http://localhost:11434", "http://127.0.0.1:11434", "http://127.0.0.2",
                  "http://[::1]:11434", "localhost:11434"):
            self.assertTrue(self.check(u), u)

    def test_not_loopback(self):
        for u in ("http://192.168.1.5:11434", "http://0.0.0.0:11434", "https://example.com",
                  "http://localhost.example.com:11434", "http://127.example.com:11434"):
            self.assertFalse(self.check(u), u)


class TestGeneratedFiles(unittest.TestCase):
    """--secure 時の opencode.json と SECURE.md。"""

    @staticmethod
    def extract_function(name):
        """vibe-local.sh から関数定義を切り出す (ヒアドキュメント中の '}' 行では終わらない)。"""
        with open(LAUNCHER) as f:
            lines = f.read().split("\n")
        start = lines.index(f"{name}() {{")
        heredoc = None
        for i in range(start, len(lines)):
            line = lines[i]
            if heredoc:
                if line.strip() == heredoc:
                    heredoc = None
                continue
            if "<<" in line:
                tag = line.split("<<", 1)[1].strip().split()[0].strip("'\"-")
                heredoc = tag
                continue
            if line == "}":
                return "\n".join(lines[start:i + 1]) + "\n"
        raise AssertionError(f"function {name} not found")

    def render(self, secure):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        funcs = os.path.join(d, "funcs.sh")
        with open(funcs, "w") as f:
            f.write(self.extract_function("write_opencode_config"))
            f.write(self.extract_function("write_secure_md"))
        script = textwrap.dedent('''
            set -u
            d="$1"; secure="$2"
            . "$d/funcs.sh"
            OPENCODE_CONFIG_FILE="$d/opencode.json"; AGENTS_FILE="$d/AGENTS.md"
            SECURE_FILE="$d/SECURE.md"; ROUTER_PORT=11435; OLLAMA_HOST=http://localhost:11434
            SMALL_CTX=32768
            write_secure_md
            write_opencode_config vibe-coder vibe-fast 65536 ask 1 "$secure"
        ''')
        subprocess.run(["bash", "-c", script, "_", d, secure], check=True)
        with open(os.path.join(d, "opencode.json")) as f:
            cfg = json.load(f)
        with open(os.path.join(d, "SECURE.md")) as f:
            md = f.read()
        return cfg, md, d

    def test_secure_config(self):
        cfg, _, d = self.render("1")
        perm = cfg["permission"]
        self.assertEqual(perm["bash"], "ask")
        self.assertEqual(perm["edit"], "ask")
        for k in ("webfetch", "websearch", "codesearch"):
            self.assertEqual(perm[k], "deny", k)
        self.assertIn(os.path.join(d, "SECURE.md"), cfg["instructions"])

    def test_normal_config_unchanged(self):
        cfg, _, d = self.render("0")
        self.assertEqual(cfg["permission"], {"edit": "ask", "bash": "ask", "webfetch": "ask"})
        self.assertEqual(cfg["instructions"], [os.path.join(d, "AGENTS.md")])

    def test_secure_md_rules(self):
        _, md, _ = self.render("1")
        for needle in ("生データの中身をチャットに表示・引用しない",
                       "生データのファイルをエージェントのツール",
                       "標準出力・標準エラーにも個票",
                       "APA7", "再現可能なスクリプト", "外部への通信", "元ファイルを上書きせず"):
            self.assertIn(needle, md)


STUB = r'''#!/usr/bin/env python3
import json, os, sys
mode = os.environ.get("STUB_MODE", "good")
agents = {"custom": {"permission": {"webfetch": "allow"}}}
inline = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT") or "{}")
args = sys.argv[1:]
if mode == "crash":
    sys.exit(3)
if args == ["debug", "config"]:
    mcp = {"remote1": {"type": "remote", "url": "https://example.invalid", "enabled": True}}
    for n, v in (inline.get("mcp") or {}).items():
        mcp.setdefault(n, {}).update(v)
    if mode == "mcp-stays":
        mcp["remote1"]["enabled"] = True
    print(json.dumps({"agent": agents, "mcp": mcp, "plugin": []}))
elif args == ["agent", "list"]:
    names = ["build", "plan", "general", "explore", "compaction", "summary", "title", "custom"]
    for n in names:
        rules = [{"permission": "*", "action": "allow", "pattern": "*"}]
        for k, v in (inline.get("agent", {}).get(n, {}).get("permission") or {}).items():
            rules.append({"permission": k, "action": v, "pattern": "*"})
        if mode == "leak":
            rules.append({"permission": "bash", "action": "allow", "pattern": "curl *"})
        print(f"{n} (primary)")
        print(json.dumps(rules, indent=2))
else:
    sys.exit(2)
'''


class TestHelperWithStub(unittest.TestCase):
    """実物の opencode なしで、fail closed の挙動を確認する。"""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.stub = os.path.join(self.d, "opencode")
        with open(self.stub, "w") as f:
            f.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IEXEC)
        self.out = os.path.join(self.d, "inline.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def prepare(self, mode):
        env = dict(os.environ, STUB_MODE=mode)
        env.pop("OPENCODE_CONFIG_CONTENT", None)
        return subprocess.run([sys.executable, HELPER, "prepare", "--opencode", self.stub, "--out", self.out],
                              env=env, capture_output=True, text=True, timeout=30)

    def test_good(self):
        p = self.prepare("good")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(self.out) as f:
            inline = json.load(f)
        for n in vs.BUILTIN_AGENTS + ["custom"]:
            self.assertEqual(inline["agent"][n]["permission"], vs.REQUIRED, n)
        self.assertEqual(inline["mcp"], {"remote1": {"enabled": False}})

    def test_leftover_allow_fails_closed(self):
        p = self.prepare("leak")
        self.assertEqual(p.returncode, 1)
        self.assertIn("bash", p.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_mcp_not_disabled_fails_closed(self):
        p = self.prepare("mcp-stays")
        self.assertEqual(p.returncode, 1)
        self.assertIn("remote1", p.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_opencode_error_fails_closed(self):
        p = self.prepare("crash")
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(self.out))

    def test_last_match_wins(self):
        rules = [{"permission": "*", "action": "allow", "pattern": "*"},
                 {"permission": "bash", "action": "ask", "pattern": "*"}]
        self.assertEqual(vs.effective(rules, "bash")["action"], "ask")
        self.assertEqual(vs.effective(rules, "webfetch")["action"], "allow")
        self.assertTrue(vs.verify({"build": rules}, {}))  # webfetch 等が allow のまま → 問題あり


def find_opencode():
    p = os.environ.get("VIBE_TEST_OPENCODE") or shutil.which("opencode")
    return p if p and os.path.exists(p) else None


@unittest.skipUnless(find_opencode(), "opencode が無いため統合テストをスキップ")
class TestRealOpencode(unittest.TestCase):
    """敵対的なグローバル設定・プロジェクト設定でも、全エージェントの実効権限が固定される。"""

    @classmethod
    def setUpClass(cls):
        cls.oc = find_opencode()
        cls.home = tempfile.mkdtemp()
        cls.proj = os.path.join(cls.home, "proj")
        gconf = os.path.join(cls.home, ".config", "opencode")
        os.makedirs(os.path.join(gconf, "agent"))
        os.makedirs(os.path.join(cls.proj, ".opencode", "agent"))
        # グローバル: エージェント別の上書き + カスタムエージェント + MCP (ローカルの無害コマンド)
        with open(os.path.join(gconf, "opencode.json"), "w") as f:
            json.dump({
                "permission": {"bash": "allow", "webfetch": "allow", "websearch": "allow"},
                "agent": {"build": {"permission": {"webfetch": "allow", "bash": {"curl *": "allow"}}},
                          "plan": {"permission": {"websearch": "allow"}}},
                "mcp": {"localtool": {"type": "local", "command": ["true"], "enabled": True}},
            }, f)
        with open(os.path.join(gconf, "agent", "gagent.md"), "w") as f:
            f.write("---\ndescription: g\nmode: subagent\npermission:\n  webfetch: allow\n  bash: allow\n---\nx\n")
        # プロジェクト: 設定ファイル + .opencode のエージェント
        with open(os.path.join(cls.proj, "opencode.json"), "w") as f:
            json.dump({"permission": {"bash": "allow", "webfetch": "allow"},
                       "agent": {"build": {"permission": {"websearch": "allow"}}}}, f)
        with open(os.path.join(cls.proj, ".opencode", "agent", "evil.md"), "w") as f:
            f.write("---\ndescription: evil\nmode: primary\npermission:\n  bash: allow\n  webfetch: allow\n---\nx\n")
        cls.base = os.path.join(cls.home, "base.json")
        with open(cls.base, "w") as f:
            json.dump({"permission": {"edit": "ask", "bash": "ask", "webfetch": "deny",
                                      "websearch": "deny", "codesearch": "deny"}}, f)
        cls.env = isolated_env(cls.home)
        cls.env.update(SECURE_ENV)
        cls.env["OPENCODE_CONFIG"] = cls.base

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def oc_run(self, args, env):
        return subprocess.run([self.oc] + args, env=env, cwd=self.proj, capture_output=True,
                              text=True, timeout=90)

    def test_fixture_is_hostile_without_lock(self):
        """ロック無しでは実際に穴がある (= テストが意味を持つ) ことの確認。"""
        p = self.oc_run(["agent", "list"], self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(vs.verify(vs.parse_agent_list(p.stdout), {}))

    def test_lock_holds_for_every_agent(self):
        out = os.path.join(self.home, "inline.json")
        p = subprocess.run([sys.executable, HELPER, "prepare", "--opencode", self.oc, "--out", out],
                           env=self.env, cwd=self.proj, capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, 0, p.stderr)
        env = dict(self.env)
        with open(out) as f:
            env["OPENCODE_CONFIG_CONTENT"] = f.read()

        agents = vs.parse_agent_list(self.oc_run(["agent", "list"], env).stdout)
        self.assertIn("build", agents)
        self.assertIn("gagent", agents)            # グローバルのカスタムエージェントも対象
        self.assertNotIn("evil", agents)           # プロジェクトの .opencode は読まれない
        for name, rules in agents.items():
            for perm, want in vs.REQUIRED.items():
                r = vs.effective(rules, perm)
                self.assertEqual((r["action"], r["pattern"]), (want, "*"), (name, perm, r))

        resolved = json.loads(self.oc_run(["debug", "config"], env).stdout)
        self.assertEqual(resolved["mcp"]["localtool"]["enabled"], False)
        self.assertEqual(resolved.get("plugin") or [], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
