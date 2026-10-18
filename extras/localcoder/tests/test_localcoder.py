"""LocalCoder のテスト: 道具・対話ループ (偽のモデルサーバー)・モデル選び・サーバーの起動と切り替え。"""

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from localcoder import bundle, cli, models, processes, server, toolchain, uninstall  # noqa: E402
from localcoder.agent import Agent, Client, Status, rescue_calls  # noqa: E402
from localcoder.tools import ToolError, Tools  # noqa: E402


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.t = Tools(self.tmp)

    def test_stays_inside_the_workspace(self):
        for p in ("../x.txt", "/etc/passwd", "a/../../x"):
            with self.assertRaises(ToolError):
                self.t.write_file(p, "x")

    def test_links_inside_the_workspace_cannot_reach_outside(self):
        outside = tempfile.mkdtemp()
        try:
            os.symlink(outside, os.path.join(self.tmp, "link"), target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"リンクを作れない: {e}")
        with self.assertRaises(ToolError):
            self.t.write_file("link/escape.txt", "x")
        self.assertFalse(os.path.exists(os.path.join(outside, "escape.txt")))

    def test_reads_shift_jis_files(self):
        with open(os.path.join(self.tmp, "karte.csv"), "wb") as f:
            f.write("患者ID,実施内容\n001,身体拘束\n".encode("cp932"))
        self.assertIn("身体拘束", self.t.read_file("karte.csv"))

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
        time.sleep(step.get("delay", 0))              # 頼みを読み込んでいる間 (返事の最初の文字までの待ち)
        late = step.get("late", 0)                    # 返事の頭だけ届き、中身がなかなか来ない
        chunks = []
        for part in step.get("think", []):            # 考えてから答える型のモデルの、考えの部分
            chunks.append({"choices": [{"delta": {"reasoning_content": part}}]})
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
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            time.sleep(late)
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        except OSError:
            pass                                      # 相棒が止めて、接続が切れた


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        self.srv.daemon_threads = True
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


class ProgressAndStopTest(AgentTest):
    def events_agent(self):
        self.events = []
        return Agent(Client(self.url), Tools(self.tmp), on_event=lambda k, d: self.events.append((k, d)))

    def test_model_reasoning_is_shown_apart_from_the_reply(self):
        FakeModel.script = [{"think": ["まず中身を", "確かめる"], "text": "読みます。",
                             "calls": [("list_files", {})]},
                            {"text": "<think>もう十分</think>終わりました。"}]
        a = self.events_agent()
        self.assertEqual(a.ask("見て"), "終わりました。")
        think = "".join(d for k, d in self.events if k == "think")
        text = "".join(d for k, d in self.events if k == "text")
        self.assertEqual(think, "まず中身を確かめるもう十分")
        self.assertNotIn("think", text)
        self.assertNotIn("十分", text)                 # 考えは本文 (モデルへ戻す会話) に混ぜない
        self.assertNotIn("十分", a.messages[-1]["content"])
        self.assertIn(("tool", ("list_files", "")), self.events)
        self.assertTrue(any(k == "progress" and d[0] == "list_files" for k, d in self.events))
        self.assertTrue(any(k == "speed" for k, d in self.events))

    def test_stop_while_the_model_is_still_reading(self):
        for wait in ({"delay": 3}, {"late": 3}):       # 返事の頭が届く前 / 頭は届いて中身を待っている
            with self.subTest(wait=wait):
                self._stop_while_waiting(wait)

    def _stop_while_waiting(self, wait):
        FakeModel.script = [dict(wait, text="遅い返事")]
        a = self.events_agent()
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", a.ask("ゆっくり")))
        t0 = time.time()
        t.start()
        time.sleep(0.3)
        a.cancel()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertLess(time.time() - t0, 2)
        self.assertEqual(out["r"], "")
        self.assertIn("止め", a.messages[-1]["content"])
        self.assertFalse(a.finished)
        FakeModel.script = [{"text": "はい"}]          # 止めた後も、次の頼みはふつうに通る
        self.assertEqual(a.ask("続けて"), "はい")

    def test_stop_kills_a_running_command(self):
        t = Tools(self.tmp)
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("r", t.run("sleep 30" if os.name != "nt" else
                                                                         "Start-Sleep -Seconds 30")))
        t0 = time.time()
        th.start()
        time.sleep(0.5)
        t.cancel()
        th.join(10)
        self.assertFalse(th.is_alive())
        self.assertLess(time.time() - t0, 10)
        self.assertIn("止めました", out["r"])

    def test_think_tag_split_across_chunks(self):
        got = {"think": [], "text": []}
        lines = [f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}".encode()
                 for p in ("<th", "ink>考え", "中</th", "ink>答え")] + [b"data: [DONE]"]
        r = Client(self.url)._stream(iter(lines), on_text=got["text"].append, on_think=got["think"].append)
        self.assertEqual("".join(got["think"]), "考え中")
        self.assertEqual(r["content"], "答え")


class ForceStopTest(unittest.TestCase):
    def long_child(self):
        import subprocess
        cmd = ["powershell", "-NoProfile", "-Command", "Start-Sleep -Seconds 60"] if os.name == "nt" else ["sleep", "60"]
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, **processes.popen_group_options())
        self.addCleanup(lambda: p.poll() is None and p.kill())
        time.sleep(0.5)
        return p

    def test_force_stop_kills_only_what_is_still_the_recorded_process(self):
        home = tempfile.mkdtemp()
        a, b = self.long_child(), self.long_child()
        processes.record_process(home, "llama-server", a.pid)
        processes.record_process(home, "LocalCoder", b.pid)
        # b の番号が別のプログラムに使い回された、という場面を作る (起動時刻が違う)
        state = os.path.join(home, "logs", processes.STATE)
        with open(state, encoding="utf-8") as f:
            data = json.load(f)
        data["LocalCoder"]["created"] += 1
        with open(state, "w", encoding="utf-8") as f:
            json.dump(data, f)
        said = []
        self.assertEqual(processes.force_stop(home, said.append), 1)
        a.wait(10)
        self.assertIsNotNone(a.poll())
        self.assertIsNone(b.poll())                    # 身元が違うので止めない
        self.assertIn("止めました: llama-server", "\n".join(said))

    def test_terminate_process_tree_and_identity(self):
        p = self.long_child()
        self.assertIsNotNone(processes.process_identity(p.pid))
        processes.terminate_process_tree(p, timeout=2)
        self.assertIsNotNone(p.poll())
        self.assertIsNone(processes.process_identity(p.pid))

    def test_cli_force_stop_uses_every_known_home(self):
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "c.json")
        cli.save({"home": os.path.join(d, "a"), "homes": [os.path.join(d, "a"), os.path.join(d, "b")]}, cfg)
        seen = []
        orig = processes.force_stop
        processes.force_stop = lambda home, say: seen.append(home) or 0
        try:
            self.assertEqual(cli.main(["--force-stop", "--config", cfg]), 0)
        finally:
            processes.force_stop = orig
        self.assertEqual(seen, [os.path.join(d, "a"), os.path.join(d, "b")])


class BundleTest(unittest.TestCase):
    def tearDown(self):
        server.OFFLINE = False

    def fake_pypi(self, pkgs):
        """pkgs: {名前: {版: (wheel のタグ, [requires_dist])}}"""
        def opener(req, timeout=None):
            url = req.full_url
            m = __import__("re").match(r"https://pypi.org/pypi/([^/]+)/(?:([^/]+)/)?json", url)
            name, ver = m.group(1), m.group(2)
            rels = pkgs[name]
            if ver:
                body = {"info": {"name": name, "requires_dist": rels[ver][1]}}
            else:
                body = {"info": {"name": name}, "releases": {
                    v: [{"filename": f"{name}-{v}-{tag}.whl", "url": f"https://files/{name}-{v}.whl"}]
                    for v, (tag, _) in rels.items()}}
            return io.BytesIO(json.dumps(body).encode())
        return opener

    def test_resolve_wheels_follows_windows_requirements(self):
        pkgs = {"pyinstaller": {"6.1.0": ("py3-none-win_amd64", ["pefile>=2022.5.30,!=2024.8.26; sys_platform == \"win32\"",
                                                                   "macholib>=1.8; sys_platform == \"darwin\"",
                                                                   "importlib-metadata; python_version < \"3.10\"",
                                                                   "pytest; extra == \"hook-testing\"", "altgraph"])},
                "pefile": {"2024.8.26": ("py3-none-any", []), "2023.2.7": ("py3-none-any", []),
                           "2025.1.0rc1": ("py3-none-any", [])},
                "altgraph": {"0.17.5": ("py2.py3-none-any", []), "0.18.0": ("cp313-cp313-manylinux_x86_64", [])}}
        got = {n: v for n, v, f, u in bundle.resolve_wheels(["pyinstaller"], self.fake_pypi(pkgs))}
        self.assertEqual(got, {"pyinstaller": "6.1.0", "pefile": "2023.2.7", "altgraph": "0.17.5"})

    def test_markers_and_versions(self):
        self.assertTrue(bundle._marker('sys_platform == "win32" and python_version >= "3.8"'))
        self.assertFalse(bundle._marker('platform_system == "Darwin"'))
        self.assertFalse(bundle._marker('__import__("os").system("x") == "1"'))
        self.assertTrue(bundle._satisfies("2026.8", ">=2026.7"))
        self.assertFalse(bundle._satisfies("1.0rc1", ">=0.1"))
        self.assertTrue(bundle._satisfies("1.4.2", "~=1.4"))

    def make_bundle_dir(self):
        b = os.path.join(tempfile.mkdtemp(), "bundle")
        os.makedirs(os.path.join(b, "llama", "cpu"))
        open(os.path.join(b, "llama", "cpu", "llama-server.exe" if os.name == "nt" else "llama-server"), "w").close()
        os.makedirs(os.path.join(b, "vcruntime"))
        open(os.path.join(b, "vcruntime", "vcomp140.dll"), "w").close()
        os.makedirs(os.path.join(b, "models", "unsloth__Small"))
        with open(os.path.join(b, "models", "unsloth__Small", "small-Q4_K_M.gguf"), "wb") as f:
            f.write(b"GGUF" + b"0" * 1000)
        with open(os.path.join(b, bundle.MANIFEST), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "models": {"small": ["unsloth__Small/small-Q4_K_M.gguf"]}, "python": None}, f)
        return b

    def test_offline_prepare_copies_from_the_usb_and_never_downloads(self):
        b = self.make_bundle_dir()
        home = tempfile.mkdtemp()
        d = tempfile.mkdtemp()
        args = cli.parser().parse_args(["--bundle", b, "--config", os.path.join(d, "c.json")])
        cfg = {"home": home, "model_key": "standard", "toolchain": False}
        self.assertEqual([m["key"] for m in cli.catalog(args)], ["small"])
        model = cli.prepare(cfg, args, ["cuda", "vulkan", "cpu"])
        self.assertEqual(model, os.path.join(home, "models", "unsloth__Small", "small-Q4_K_M.gguf"))
        self.assertEqual(os.path.getsize(model), 1004)
        self.assertEqual(cfg["model_key"], "small")                  # USB に無いモデルは選べない
        self.assertTrue(os.path.exists(os.path.join(home, "llama", "cpu", "vcomp140.dll")))
        self.assertTrue(server.OFFLINE)
        with self.assertRaises(OSError) as e:
            server.download("https://example.com/x", os.path.join(home, "x"))
        self.assertIn("外へは取りに行きません", str(e.exception))
        with self.assertRaises(OSError):
            models.siblings("someone/model")

    def fake_sources(self, pypi=None):
        """GitHub (llama.cpp)・Hugging Face (モデル)・python.org (導入器) の偽物。pypi を渡せば PyPI はそちらへ。"""
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("llama-server.exe" if os.name == "nt" else "llama-server", "server")
            z.writestr("ggml.dll", "x")
        llama_zip = buf.getvalue()
        releases = [{"tag_name": "b9999", "assets": [
            {"name": "llama-b9999-bin-win-cpu-x64.zip", "browser_download_url": "https://gh/cpu.zip"}]}]
        tiny = models.by_key("tiny")

        class Resp(io.BytesIO):
            status = 200
            headers = {}

            def geturl(self):
                return "https://github.com/ggml-org/llama.cpp/releases/tag/b9999"

        def opener(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            if url.startswith("https://pypi.org/"):
                return pypi(req, timeout=timeout)
            if url.startswith("https://files/"):
                return Resp(b"wheel")
            if "api.github.com" in url:
                return Resp(json.dumps(releases).encode())
            if url == "https://gh/cpu.zip":
                return Resp(llama_zip)
            if url.startswith(models.API):
                return Resp(json.dumps({"siblings": [{"rfilename": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"}]}).encode())
            if "huggingface.co" in url:
                return Resp(b"GGUF" + b"1" * 2000)
            if "python.org" in url:
                return Resp(b"MZ installer")
            raise AssertionError(f"思わぬ取得: {url}")
        return opener, tiny

    def test_make_bundle_then_prepare_offline(self):
        pypi = self.fake_pypi({"pyinstaller": {"6.1.0": ("py3-none-win_amd64", ["altgraph"])},
                               "altgraph": {"0.17.5": ("py2.py3-none-any", [])}})
        opener, tiny = self.fake_sources(pypi)
        usb = os.path.join(tempfile.mkdtemp(), "USB")
        exe_dir = tempfile.mkdtemp()
        for name in bundle.EXES:
            open(os.path.join(exe_dir, name), "w").close()
        b = bundle.make(usb, ["tiny"], ["cpu"], say=lambda *a, **k: None, opener=opener, exe_dir=exe_dir)
        for name in bundle.EXES + ["非常停止.bat", "はじめにお読みください.txt"]:
            self.assertTrue(os.path.exists(os.path.join(usb, name)), name)
        m = bundle.manifest(b)
        self.assertEqual(m["kinds"], ["cpu"])
        self.assertEqual(list(m["models"]), ["tiny"])
        self.assertEqual(sorted(m["wheels"]), ["altgraph-0.17.5-py2.py3-none-any.whl", "pyinstaller-6.1.0-py3-none-win_amd64.whl"])
        with open(os.path.join(usb, "非常停止.bat"), encoding="utf-8-sig") as f:
            self.assertIn("--force-stop", f.read())
        # オフラインの PC: USB から導入。外へは一度も取りに行かない
        home = tempfile.mkdtemp()
        args = cli.parser().parse_args(["--bundle", b, "--config", os.path.join(tempfile.mkdtemp(), "c.json")])
        cfg = {"home": home, "toolchain": False}
        model = cli.prepare(cfg, args, list(server.KINDS))
        self.assertTrue(model.endswith("qwen2.5-coder-1.5b-instruct-q4_k_m.gguf") and os.path.getsize(model) == 2004)
        self.assertTrue(server.find_server(os.path.join(home, "llama", "cpu")))
        installer, wheels = bundle.toolchain_sources(b)
        self.assertTrue(installer.endswith("python-3.12.10-amd64.exe") and os.path.isdir(wheels))

    def test_uninstall_keeps_the_usb_copy(self):
        b = self.make_bundle_dir()
        exe = os.path.join(os.path.dirname(b), "LocalCoder.exe")
        open(exe, "w").close()
        d = tempfile.mkdtemp()
        cfgp = os.path.join(d, "LocalCoder", "config.json")
        cli.save({"home": os.path.join(d, "home"), "launched_from": [exe]}, cfgp)
        un = uninstall.Uninstaller(config=cfgp, env={"LOCALAPPDATA": d, "TEMP": d}, system=FakeSystem(FakeRegistry()),
                                   out=lambda m: None, pythons=lambda minor: [])
        self.assertFalse(any(exe in it.label for it in un.plan()))
        un.run(yes=True, include_unsure=True)
        self.assertTrue(os.path.exists(exe))


class GuiTest(unittest.TestCase):
    """画面の自己点検 (exe の --selftest と同じもの)。画面を出せない環境では飛ばす。"""

    def test_window_selftest(self):
        try:
            import tkinter
            root = tkinter.Tk()
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"画面を出せない: {e}")
        from localcoder import selftest
        out = os.path.join(tempfile.mkdtemp(), "selftest.txt")
        code = selftest.run(root, cli.parser().parse_args(["--config", os.path.join(tempfile.mkdtemp(), "c.json")]),
                            out)
        with open(out, encoding="utf-8") as f:
            report = f.read()
        self.assertEqual(code, 0, report)
        self.assertTrue(report.strip().endswith("OK"), report)


class ChunkedModel(BaseHTTPRequestHandler):
    """llama-server と同じく HTTP/1.1 の chunked で流す偽サーバー (塊の切れ目は行の途中にも来る)。"""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not body["stream"]:
            data = json.dumps({"choices": [{"message": {"content": "まとめて"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        sse = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in (
            {"choices": [{"delta": {"reasoning_content": "考えて"}}]},
            {"choices": [{"delta": {"content": "こんにちは"}}]},
            {"choices": [{"delta": {"content": "、世界"}}]})) + "data: [DONE]\n\n"
        raw = sse.encode()
        for i in range(0, len(raw), 7):             # 7 バイトずつ: 文字や行の途中で切れる
            part = raw[i:i + 7]
            self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")


class ClientTransferTest(unittest.TestCase):
    def test_chunked_stream_and_fixed_length(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ChunkedModel)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = Client(f"http://127.0.0.1:{srv.server_address[1]}")
            text, think = [], []
            r = c.chat([{"role": "user", "content": "x"}], on_text=text.append, on_think=think.append)
            self.assertEqual(r["content"], "こんにちは、世界")
            self.assertEqual("".join(think), "考えて")
            self.assertEqual(c.chat([{"role": "user", "content": "x"}])["content"], "まとめて")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_unreachable_server_is_a_model_error(self):
        from localcoder.agent import ModelError
        with self.assertRaises(ModelError):
            Client("http://127.0.0.1:9").chat([{"role": "user", "content": "x"}], on_text=lambda t: None)


class StatusTest(unittest.TestCase):
    def test_waiting_line_is_shown_then_cleared_and_text_flows(self):
        out = []
        now = [100.0]
        st = Status(out.append, clock=lambda: now[0], interval=3600)
        st.begin()
        now[0] = 112
        st.show()
        self.assertIn("考えています… 12 秒", out[-1])
        st.text("はい、作ります")
        self.assertEqual(out[-1], "はい、作ります")
        self.assertEqual(out[-2].strip(), "")                  # 待ちの表示を消してから本文
        st.tool("write_file", 1234)
        st.show()
        self.assertEqual(out[-2], "\n")                        # 本文の行は消さずに改行してから
        self.assertIn("write_file を準備しています (1,234 字)", out[-1])
        st.end()
        self.assertEqual(out[-1].strip(), "")


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
            def chat(self, messages, tools=None, on_text=None, **kw):
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

    def test_loading_can_be_stopped(self):
        home = self.home_with("cuda")

        class Loading:                                 # 読み込みが終わらない (health が ok にならない)
            pid = None

            def poll(self):
                return None

            def terminate(self):
                self.dead = True

            def wait(self, t=None):
                return 0
        s = server.Server(home, say=lambda *a, **k: None, popen=lambda *a, **k: Loading(),
                          opener=lambda *a, **k: (_ for _ in ()).throw(OSError("not yet")), sleep=lambda t: time.sleep(0.05))
        threading.Timer(0.3, s.cancel_event.set).start()
        t0 = time.time()
        self.assertIsNone(s.start_best("m.gguf", ["cuda", "cpu"]))
        self.assertLess(time.time() - t0, 5)

    def test_args(self):
        a = server.server_args("cuda", "m.gguf", 8090, 32768, 4)
        self.assertIn("--cpu-moe", a)
        self.assertIn("--jinja", a)
        self.assertNotIn("-ngl", server.server_args("cpu", "m.gguf", 8090, 32768, 4))
        self.assertNotIn("--cpu-moe", server.server_args("vulkan", "m.gguf", 8090, 32768, 4, minimal=True))
        shared = server.server_args("cuda", "m.gguf", 8090, 32768, 4, shared=True)
        self.assertIn("-ngl", shared)
        self.assertNotIn("--cpu-moe", shared)         # 専門家も GPU 側へ (入りきらない分は共有 GPU メモリ)

    def test_driver_versions_and_gpu_info(self):
        self.assertTrue(server.driver_has_sysmem_fallback("581.57"))
        self.assertTrue(server.driver_has_sysmem_fallback("536.40"))
        self.assertFalse(server.driver_has_sysmem_fallback("531.79"))
        self.assertFalse(server.driver_has_sysmem_fallback(""))
        import subprocess

        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, "NVIDIA GeForce GTX 1060 6GB, 581.57, 6144\n", "")
        self.assertEqual(server.gpu_info(run), {"name": "NVIDIA GeForce GTX 1060 6GB", "driver": "581.57",
                                                "vram_mb": 6144})

        def missing(cmd, **kw):
            raise FileNotFoundError(cmd[0])
        self.assertIsNone(server.gpu_info(missing))
        info = {"name": "GTX 1060", "driver": "531.79", "vram_mb": 6144}
        self.assertEqual(cli.check_gpu(["cuda", "cpu"], "shared", info), "normal")    # 古いドライバでは使えない
        self.assertEqual(cli.check_gpu(["cuda", "cpu"], "shared", dict(info, driver="581.57")), "shared")

    def test_shared_gpu_memory_falls_back_to_the_usual_layout(self):
        home = self.home_with("cuda")
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

        def popen(cmd, stdout=None, **kw):
            calls.append(cmd)
            if "--cpu-moe" not in cmd:           # 共有 GPU メモリの置き方: ドライバの設定で入りきらず落ちる
                stdout.write("CUDA error: out of memory\ncudaMalloc failed\n")
                stdout.flush()
                return Dead()
            return Alive()
        s = server.Server(home, say=lambda *a, **k: None, sleep=lambda s: None, popen=popen, gpu_memory="shared")
        s.opener = lambda req, timeout=None: io.BytesIO(
            b'{"status": "ok"}' if (req if isinstance(req, str) else req.full_url).endswith("/health")
            else b'{"choices": []}')
        self.assertTrue(s.start("cuda", "m.gguf"))
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--cpu-moe", calls[0])
        self.assertIn("--cpu-moe", calls[1])
        self.assertFalse(s.shared)
        calls.clear()
        s.popen = lambda cmd, stdout=None, **kw: calls.append(cmd) or Alive()
        self.assertTrue(s.start("cuda", "m.gguf"))
        self.assertTrue(s.shared)
        self.assertEqual(len(calls), 1)

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
        kw.setdefault("pythons", lambda minor: [])
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
        un = uninstall.Uninstaller(env=self.env, homes=[self.home], system=self.sys, out=again.append,
                                   pythons=lambda minor: [])
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

    def test_python_uninstall_is_refused_when_another_312_exists(self):
        theirs = [("3.12", os.path.join(self.home, "python")), ("3.12", r"C:\\Python312")]
        self.assertEqual(self.un(pythons=lambda minor: theirs).run(yes=True), 4)
        self.assertEqual(self.sys.ran, [])                      # 巻き込むおそれがあるので動かさない
        self.assertTrue(any("巻き込まない" in x for x in self.lines))

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
