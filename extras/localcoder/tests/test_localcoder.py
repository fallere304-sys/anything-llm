"""LocalCoder のテスト: 道具・対話ループ (偽のモデルサーバー)・モデル選び・サーバーの起動と切り替え。"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from localcoder import cli, models, server, toolchain, uninstall  # noqa: E402
from localcoder.agent import Agent, Client, rescue_calls  # noqa: E402
from localcoder.tools import ToolError, Tools  # noqa: E402


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.t = Tools(self.tmp)

    def test_stays_inside_the_workspace(self):
        for p in ("../x.txt", "/etc/passwd", "a/../../x"):
            with self.assertRaises(ToolError):
                self.t.write_file(p, "x")

    def test_write_read_edit(self):
        self.t.write_file("src/app.py", "a = 1\nb = 2\n")
        self.assertIn("    1  a = 1", self.t.read_file("src/app.py"))
        self.t.edit_file("src/app.py", "b = 2", "b = 3")
        self.assertIn("b = 3", self.t.read_file("src/app.py"))
        with self.assertRaises(ToolError):
            self.t.edit_file("src/app.py", "nothing", "x")
        self.t.write_file("dup.py", "x\nx\n")
        with self.assertRaises(ToolError):
            self.t.edit_file("dup.py", "x", "y")

    def test_edit_across_line_endings(self):
        with open(os.path.join(self.tmp, "w.txt"), "w", newline="") as f:
            f.write("one\r\ntwo\r\n")
        self.t.edit_file("w.txt", "one\ntwo", "1\n2")
        with open(os.path.join(self.tmp, "w.txt"), newline="") as f:
            self.assertEqual(f.read(), "1\r\n2\r\n")

    def test_search_list_run(self):
        self.t.write_file("a.py", "def hello():\n    return 1\n")
        self.assertIn("a.py:1: def hello():", self.t.search(r"def \w+"))
        self.assertIn("a.py", self.t.list_files(pattern="*.py"))
        out = self.t.run("echo こんにちは")
        self.assertIn("(終了コード 0)", out)
        self.assertIn("こんにちは", out)


class FakeModel(BaseHTTPRequestHandler):
    """OpenAI 互換の偽サーバー。台本どおりに道具を呼び、最後に報告する (ストリーミング)。"""
    script = []
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeModel.seen.append(body)
        step = FakeModel.script.pop(0)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = []
        if "text" in step:
            for part in (step["text"][:3], step["text"][3:]):
                chunks.append({"choices": [{"delta": {"content": part}}]})
        for i, (name, args) in enumerate(step.get("calls", [])):
            raw = json.dumps(args, ensure_ascii=False)
            chunks.append({"choices": [{"delta": {"tool_calls": [
                {"index": i, "id": f"c{i}", "function": {"name": name, "arguments": raw[:5]}}]}}]})
            chunks.append({"choices": [{"delta": {"tool_calls": [
                {"index": i, "function": {"arguments": raw[5:]}}]}}]})
        chunks.append({"choices": [], "timings": {"predicted_per_second": 7.5}})
        for c in chunks:
            self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.tmp = tempfile.mkdtemp()
        FakeModel.seen = []

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def agent(self, confirm=None):
        self.printed, self.lines = [], []
        return Agent(Client(self.url), Tools(self.tmp), confirm=confirm, out=self.lines.append,
                     write=self.printed.append)

    def test_writes_runs_and_reports(self):
        FakeModel.script = [
            {"text": "作ります。", "calls": [("write_file", {"path": "hello.py", "content": "print('こんにちは')\n"})]},
            {"calls": [("run", {"command": "python3 hello.py" if os.name != "nt" else "python hello.py"})]},
            {"text": "hello.py を作って、動くことを確かめました。"},
        ]
        a = self.agent()
        final = a.ask("こんにちはと表示するプログラムを作って")
        self.assertEqual(final, "hello.py を作って、動くことを確かめました。")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "hello.py")))
        tool_results = [m["content"] for m in a.messages if m["role"] == "tool"]
        self.assertIn("書きました", tool_results[0])
        self.assertIn("こんにちは", tool_results[1])                     # 実行した結果がモデルに返っている
        self.assertEqual("".join(self.printed).strip().splitlines()[0], "作ります。")   # 届いた順に表示
        self.assertTrue(FakeModel.seen[0]["stream"])
        self.assertEqual({t["function"]["name"] for t in FakeModel.seen[0]["tools"]},
                         {"list_files", "read_file", "write_file", "edit_file", "search", "run"})
        self.assertTrue(any("トークン/秒" in line for line in self.lines))

    def test_refused_command_is_reported_to_the_model(self):
        FakeModel.script = [{"calls": [("run", {"command": "del important.txt"})]}, {"text": "やめておきます。"}]
        a = self.agent(confirm=lambda c: False)
        a.ask("消して")
        self.assertIn("断りました", [m for m in a.messages if m["role"] == "tool"][0]["content"])

    def test_errors_go_back_to_the_model(self):
        FakeModel.script = [{"calls": [("read_file", {"path": "../secret"})]}, {"calls": [("nope", {})]},
                            {"text": "できませんでした。"}]
        a = self.agent()
        a.ask("読んで")
        results = [m["content"] for m in a.messages if m["role"] == "tool"]
        self.assertIn("作業フォルダの外", results[0])
        self.assertIn("そんな道具はありません", results[1])


class RepeatTest(AgentTest):
    def test_text_calls_get_plain_results_and_repeats_stop(self):
        block = '```json\n{"name": "write_file", "arguments": {"path": "hello.txt", "content": "こんにちは"}}\n```'
        FakeModel.script = [{"text": block}, {"text": block}, {"text": block}]
        a = self.agent()
        self.assertEqual(a.ask("hello.txt を作って"), "")
        self.assertFalse(a.finished)
        users = [m["content"] for m in a.messages if m["role"] == "user"][1:]
        self.assertIn("[道具 write_file の結果]", users[0])                 # ふつうの言葉で結果を返す
        self.assertIn("少し前にもしました", users[1])                       # 2 回目は促す
        self.assertFalse(any(m["role"] == "tool" for m in a.messages))
        self.assertTrue(any("繰り返している" in line for line in self.lines))   # 3 回目で止める
        self.assertEqual(len(FakeModel.seen), 3)

    def test_alternating_actions_are_caught(self):
        a_call = '```json\n{"name": "run", "arguments": {"command": "echo a"}}\n```'
        b_call = '```json\n{"name": "run", "arguments": {"command": "echo b"}}\n```'
        FakeModel.script = [{"text": a_call}, {"text": b_call}, {"text": a_call}, {"text": b_call}, {"text": a_call}]
        a = self.agent()
        a.ask("やって")
        self.assertFalse(a.finished)
        self.assertEqual(len(FakeModel.seen), 5)                 # A,B,A(促す),B(促す),A(止める)

    def test_context_overflow_is_trimmed_and_retried(self):
        calls = []

        class Tight:
            def chat(self, messages, tools=None, on_text=None):
                calls.append(sum(len(m.get("content") or "") for m in messages))
                if len(calls) == 1:
                    from localcoder.agent import ModelError
                    raise ModelError('HTTP 400: {"error": "request exceeds the available context size"}')
                return {"content": "できました", "tool_calls": [], "timings": None}
        a = Agent(Tight(), Tools(self.tmp), out=lambda m: None, write=lambda s: None, ctx_chars=20000)
        a.messages += [{"role": "user", "content": "前"}, {"role": "tool", "content": "x" * 15000}]
        self.assertEqual(a.ask("続けて"), "できました")
        self.assertLess(calls[1], calls[0])

    def test_finishes_after_a_text_call(self):
        FakeModel.script = [{"text": '<tool_call>{"name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}'
                                     '</tool_call>'}, {"text": "a.txt を作りました。"}]
        a = self.agent()
        self.assertEqual(a.ask("a.txt を作って"), "a.txt を作りました。")
        self.assertTrue(a.finished)


class RescueAndContextTest(unittest.TestCase):
    def test_rescue_tool_calls_written_in_text(self):
        calls = rescue_calls('では書きます。<tool_call>{"name": "write_file", "arguments": {"path": "a.txt", '
                             '"content": "x"}}</tool_call>')
        self.assertEqual(calls[0]["function"]["name"], "write_file")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["path"], "a.txt")
        self.assertEqual(rescue_calls("ふつうの返事です。"), [])

    def test_old_tool_results_are_trimmed(self):
        a = Agent(Client("http://127.0.0.1:9"), Tools(tempfile.mkdtemp()), ctx_chars=3000)
        a.messages += [{"role": "user", "content": "前の頼み"}, {"role": "tool", "content": "x" * 5000},
                       {"role": "user", "content": "新しい頼み"}]
        a._compact()
        self.assertLessEqual(a._size(), 3000)
        self.assertEqual(a.messages[-1]["content"], "新しい頼み")
        self.assertEqual(a.messages[0]["role"], "system")


class ModelsTest(unittest.TestCase):
    def test_picks_preferred_quant_and_all_split_parts(self):
        sib = ["README.md", "Q4_K_M/GLM-4.5-Air-Q4_K_M-00002-of-00002.gguf",
               "Q4_K_M/GLM-4.5-Air-Q4_K_M-00001-of-00002.gguf", "Q3_K_M/GLM-4.5-Air-Q3_K_M.gguf", "mmproj-F16.gguf"]
        self.assertEqual(models.pick_files(sib, ["Q4_K_M", "Q3_K_M"]),
                         ["Q4_K_M/GLM-4.5-Air-Q4_K_M-00001-of-00002.gguf",
                          "Q4_K_M/GLM-4.5-Air-Q4_K_M-00002-of-00002.gguf"])
        self.assertEqual(models.pick_files(sib, ["Q2_K", "Q3_K_M"]), ["Q3_K_M/GLM-4.5-Air-Q3_K_M.gguf"])
        self.assertEqual(models.pick_files(["a.bin"], ["Q4_K_M"]), [])

    def test_local_models_lists_first_parts_only(self):
        d = tempfile.mkdtemp()
        for f in ("a-00001-of-00002.gguf", "a-00002-of-00002.gguf", "b.gguf"):
            open(os.path.join(d, f), "w").close()
        self.assertEqual(sorted(os.path.basename(p) for p in models.local_models(d)),
                         ["a-00001-of-00002.gguf", "b.gguf"])


class ServerTest(unittest.TestCase):
    def test_picks_windows_builds(self):
        assets = {n: "u" for n in ("llama-b7000-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip",
                                    "llama-b7000-bin-win-vulkan-x64.zip", "llama-b7000-bin-win-cpu-x64.zip",
                                    "llama-b7000-bin-ubuntu-x64.zip", "llama-b7000-bin-win-cpu-arm64.zip")}
        self.assertEqual(server.pick(assets, "cuda"), ["llama-b7000-bin-win-cuda-12.4-x64.zip",
                                                       "cudart-llama-bin-win-cuda-12.4-x64.zip"])
        self.assertEqual(server.pick(assets, "vulkan"), ["llama-b7000-bin-win-vulkan-x64.zip"])
        self.assertEqual(server.pick(assets, "cpu"), ["llama-b7000-bin-win-cpu-x64.zip"])

    def test_finds_windows_builds_in_an_older_release_and_new_names(self):
        rels = [{"tag_name": "v0.5.0", "assets": [{"name": "llama-v0.5.0-macos-arm64.zip", "browser_download_url": "m"}]},
                {"tag_name": "b7000", "assets": [
                    {"name": "llama-b7000-win-cpu-x64.zip", "browser_download_url": "c"},
                    {"name": "llama-b7000-win-vulkan-x64.zip", "browser_download_url": "v"}]}]
        tag, assets = server.latest_assets(lambda req, timeout=None: io.BytesIO(json.dumps(rels).encode()))
        self.assertEqual(tag, "b7000")
        self.assertEqual(server.pick(assets, "cpu"), ["llama-b7000-win-cpu-x64.zip"])
        with self.assertRaises(OSError):
            server.latest_assets(lambda req, timeout=None: io.BytesIO(json.dumps(rels[:1]).encode()))

    def test_api_rate_limit_falls_back_to_the_latest_page(self):
        class Resp(io.BytesIO):
            def geturl(self):
                return "https://github.com/ggml-org/llama.cpp/releases/tag/b11255"

        def opener(req, timeout=None):
            if req.full_url.startswith("https://api.github.com/"):
                raise OSError("HTTP 403 rate limit exceeded")
            return Resp(b"")
        tag, assets = server.latest_assets(opener)
        self.assertEqual(tag, "b11255")
        self.assertEqual(server.pick(assets, "cpu"), ["llama-b11255-bin-win-cpu-x64.zip"])
        self.assertEqual(assets["llama-b11255-bin-win-cpu-x64.zip"],
                         "https://github.com/ggml-org/llama.cpp/releases/download/b11255/llama-b11255-bin-win-cpu-x64.zip")

    def test_args(self):
        a = server.server_args("cuda", "m.gguf", 8090, 32768, 4)
        self.assertIn("--cpu-moe", a)
        self.assertIn("--jinja", a)
        self.assertNotIn("-ngl", server.server_args("cpu", "m.gguf", 8090, 32768, 4))
        self.assertNotIn("--cpu-moe", server.server_args("vulkan", "m.gguf", 8090, 32768, 4, minimal=True))

    def home_with(self, *kinds):
        home = tempfile.mkdtemp()
        for k in kinds:
            os.makedirs(os.path.join(home, "llama", k))
            open(os.path.join(home, "llama", k, "llama-server.exe"), "w").close()
        return home

    def test_gpu_that_fails_on_first_inference_falls_back(self):
        home = self.home_with("cuda", "cpu")
        started = []

        class Proc:
            def __init__(self, cmd, **kw):
                started.append(cmd[0])
                self.kind = "cuda" if os.sep + "cuda" + os.sep in cmd[0] else "cpu"

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, t=None):
                return 0

        current = {}

        def popen(cmd, **kw):
            p = Proc(cmd)
            current["kind"] = p.kind
            return p

        def opener(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            if url.endswith("/health"):
                return io.BytesIO(b'{"status": "ok"}')
            if current["kind"] == "cuda":
                raise OSError("HTTP 500 CUDA error: the provided PTX was compiled with an unsupported toolchain")
            return io.BytesIO(b'{"choices": [{"message": {"content": "OK"}}]}')
        s = server.Server(home, say=lambda *a, **k: None, popen=popen, opener=opener, sleep=lambda s: None)
        self.assertEqual(s.start_best("m.gguf"), "cpu")
        self.assertEqual(len(started), 2)

    def test_unknown_flags_retry_with_minimal_args(self):
        home = self.home_with("vulkan")
        calls = []

        class Dead:
            def poll(self):
                return 1

            def terminate(self):
                pass

            def wait(self, t=None):
                return 1

        class Alive(Dead):
            def poll(self):
                return None

        s = server.Server(home, say=lambda *a, **k: None, sleep=lambda s: None)

        def popen(cmd, stdout=None, **kw):
            calls.append(cmd)
            if "--cpu-moe" in cmd:
                stdout.write("error: invalid argument: --cpu-moe\n")
                stdout.flush()
                return Dead()
            return Alive()
        s.popen = popen
        s.opener = lambda req, timeout=None: io.BytesIO(
            b'{"status": "ok"}' if (req if isinstance(req, str) else req.full_url).endswith("/health")
            else b'{"choices": []}')
        self.assertTrue(s.start("vulkan", "m.gguf"))
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--cpu-moe", calls[1])


class SetupTest(unittest.TestCase):
    def test_non_interactive_setup_uses_defaults(self):
        d = tempfile.mkdtemp()
        cfg_path = os.path.join(d, "config.json")
        orig = cli.drives
        cli.drives = lambda: [(d, 500.0, "SSD")]
        orig_save = cli.save
        cli.save = lambda cfg, path=cfg_path: orig_save(cfg, cfg_path)
        try:
            cfg = cli.setup({}, interactive=False, model_key="tiny")
        finally:
            cli.drives, cli.save = orig, orig_save
        self.assertEqual(cfg["home"], os.path.join(d, "LocalCoder"))
        self.assertEqual(cfg["model_key"], "tiny")
        self.assertEqual(cli.load(cfg_path)["model_key"], "tiny")


class ToolchainTest(unittest.TestCase):
    def test_existing_python_312_is_not_upgraded_a_venv_is_made_instead(self):
        d = tempfile.mkdtemp()
        base = os.path.join(d, "theirs")
        os.makedirs(base)
        open(os.path.join(base, "python.exe"), "w").close()
        ran = []

        def run(cmd, **kw):
            ran.append(cmd)
            if cmd[1:3] == ["-m", "venv"]:
                os.makedirs(os.path.join(cmd[3], "Scripts"))
                open(os.path.join(cmd[3], "Scripts", "python.exe"), "w").close()
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        home = os.path.join(d, "LocalCoder")
        exe = toolchain.ensure(home, say=lambda *a, **k: None, run=run, found=[("3.12", base)])
        self.assertEqual(exe, os.path.join(home, "python", "Scripts", "python.exe"))
        self.assertEqual(ran[0][:3], [os.path.join(base, "python.exe"), "-m", "venv"])
        self.assertFalse(any("/quiet" in c for c in ran))          # 正式な入れる道具は動かさない


class FakeRegistry:
    def __init__(self):
        self.keys = {}                     # パス → {名前: 値}

    def subkeys(self, path):
        pre = path + "\\"
        return sorted({k[len(pre):].split("\\")[0] for k in self.keys if k.startswith(pre)})

    def values(self, path):
        return list(self.keys.get(path, {}))

    def get(self, path, name=""):
        return self.keys.get(path, {}).get(name)

    def delete_value(self, path, name):
        del self.keys[path][name]

    def delete_tree(self, path):
        for k in [k for k in self.keys if k == path or k.startswith(path + "\\")]:
            del self.keys[k]


class FakeSystem:
    windows = False

    def __init__(self, reg):
        self.registry, self.ran, self.procs = reg, [], []

    def run(self, cmd, timeout=300):
        self.ran.append(cmd)
        if cmd[1:] == ["/uninstall", "/quiet"]:          # Python の取り除き: 登録が消える
            self.registry.delete_tree(uninstall.PY_CORE)
            self.registry.delete_tree(uninstall.UNINSTALL + "\\{py}")
        return 0, ""

    def processes(self):
        return list(self.procs)

    def kill(self, pid):
        self.procs = [p for p in self.procs if p[0] != pid]

    def drives(self):
        return []


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        la = os.path.join(self.d, "la")
        self.env = {"LOCALAPPDATA": la, "APPDATA": os.path.join(self.d, "roaming"), "TEMP": os.path.join(self.d, "t")}
        self.home = os.path.join(self.d, "D", "LocalCoder")
        for sub in ("llama/cpu", "models/x", "python", "logs", "downloads", "cache/pip", "workspace/default"):
            os.makedirs(os.path.join(self.home, sub))
        open(os.path.join(self.home, "workspace", "default", "tool.py"), "w").close()
        self.pip = os.path.join(la, "pip", "Cache")
        self.pyi = os.path.join(la, "pyinstaller")
        os.makedirs(self.pip)
        os.makedirs(self.pyi)
        os.makedirs(self.env["TEMP"])
        self.log = os.path.join(self.env["TEMP"], f"Python {uninstall.PY_VERSION} (64-bit)_20260929.log")
        open(self.log, "w").close()
        self.exe = os.path.join(self.d, "Downloads", "LocalCoder.exe")
        os.makedirs(os.path.dirname(self.exe))
        open(self.exe, "w").close()
        bundle = os.path.join(self.d, "pkgcache", "python-3.12.10-amd64.exe")
        os.makedirs(os.path.dirname(bundle))
        open(bundle, "w").close()
        self.reg = FakeRegistry()
        self.reg.keys[uninstall.PY_CORE + "\\InstallPath"] = {"": os.path.join(self.home, "python") + os.sep}
        self.reg.keys[uninstall.UNINSTALL + "\\{py}"] = {"DisplayName": f"Python {uninstall.PY_VERSION} (64-bit)",
                                                         "BundleCachePath": bundle}
        self.reg.keys[uninstall.MUICACHE] = {os.path.join(self.home, "llama", "cpu", "llama-server.exe"): "x",
                                             self.exe + ".FriendlyAppName": "LocalCoder",
                                             r"C:\Windows\notepad.exe": "Notepad"}
        self.config = uninstall.default_config(self.env)
        os.makedirs(os.path.dirname(self.config))
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"home": self.home, "homes": [self.home], "launched_from": [self.exe],
                       "before": {"pip_cache": True, "pyinstaller": False, "user_site": False, "ts": 0}}, f)
        self.sys = FakeSystem(self.reg)
        self.lines = []

    def un(self, **kw):
        return uninstall.Uninstaller(env=self.env, system=self.sys, out=self.lines.append, sleep=lambda s: None, **kw)

    def test_removes_what_it_brought_and_keeps_the_rest(self):
        self.sys.procs = [(11, os.path.join(self.home, "llama", "cpu", "llama-server.exe")), (12, r"C:\x\other.exe")]
        self.assertEqual(self.un().run(yes=True), 0, "\n".join(self.lines))
        self.assertEqual([p for p, _ in self.sys.procs], [12])
        self.assertIn(["/uninstall", "/quiet"], [c[1:] for c in self.sys.ran])        # Python 自身の取り除き方で
        self.assertIsNone(self.reg.get(uninstall.PY_CORE + "\\InstallPath"))
        for sub in uninstall.OWNED:
            self.assertFalse(os.path.exists(os.path.join(self.home, sub)), sub)
        self.assertTrue(os.path.exists(os.path.join(self.home, "workspace", "default", "tool.py")))  # 作ったものは残す
        self.assertFalse(os.path.exists(self.config))
        self.assertFalse(os.path.exists(os.path.dirname(self.config)))
        self.assertTrue(os.path.exists(self.pip))              # 入れる前からあった
        self.assertFalse(os.path.exists(self.pyi))             # 入れる前は無かった
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(list(self.reg.values(uninstall.MUICACHE)), [r"C:\Windows\notepad.exe"])
        self.assertTrue(os.path.exists(self.exe))              # 分からないもの: --all のときだけ
        self.assertTrue(any("残す: pip のキャッシュ" in x for x in self.lines))

    def test_all_removes_workspace_exe_and_the_empty_folder(self):
        self.assertEqual(self.un().run(yes=True, include_unsure=True), 0, "\n".join(self.lines))
        self.assertFalse(os.path.exists(self.home))
        self.assertFalse(os.path.exists(self.exe))
        self.assertTrue(os.path.exists(self.pip))
        again = []
        un = uninstall.Uninstaller(env=self.env, homes=[self.home], system=self.sys, out=again.append)
        self.assertEqual(un.run(yes=True), 0)
        self.assertIn("もう何もありません", again[-1])

    def test_folder_that_was_already_there_is_kept(self):
        mine = os.path.join(self.home, "memo.txt")
        open(mine, "w").close()
        self.un().run(yes=True, include_unsure=True)
        self.assertTrue(os.path.exists(mine))
        self.assertFalse(os.path.exists(os.path.join(self.home, "models")))

    def test_python_installed_elsewhere_is_not_touched(self):
        self.reg.keys[uninstall.PY_CORE + "\\InstallPath"] = {"": r"C:\Users\me\Python312"}
        self.un().run(yes=True)
        self.assertEqual(self.sys.ran, [])
        self.assertIsNotNone(self.reg.get(uninstall.PY_CORE + "\\InstallPath"))

    def test_dry_run_and_decline_change_nothing(self):
        self.assertEqual(self.un().run(dry=True), 0)
        self.assertEqual(self.un().run(ask=lambda q, d: False), 1)
        self.assertTrue(os.path.exists(os.path.join(self.home, "models")))
        self.assertTrue(os.path.exists(self.config))
        self.assertEqual(self.sys.ran, [])

    def test_without_a_ledger_shared_caches_are_asked(self):
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"home": self.home}, f)
        self.un().run(yes=True)
        self.assertTrue(os.path.exists(self.pip) and os.path.exists(self.pyi))
        self.assertFalse(os.path.exists(os.path.join(self.home, "models")))

    def test_snapshot_and_cli_passthrough(self):
        snap = uninstall.snapshot(self.env)
        self.assertTrue(snap["pip_cache"])
        self.assertFalse(snap["user_site"])
        seen = []
        orig = uninstall.main
        uninstall.main = lambda argv: seen.append(argv) or 7
        try:
            self.assertEqual(cli.main(["--uninstall", "--dry-run"]), 7)
        finally:
            uninstall.main = orig
        self.assertEqual(seen, [["--dry-run"]])


if __name__ == "__main__":
    unittest.main()
