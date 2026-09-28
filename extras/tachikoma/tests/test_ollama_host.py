"""GPU の部品が落ちる PC (NVIDIA のドライバが古いなど) でも、CPU だけの Ollama に切り替えて動く。"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma.kernel import ollama_host  # noqa: E402
from tachikoma.llm import LLMError  # noqa: E402

# 実際に相棒の PC (GTX 1060) で出たエラー
PTX = ('HTTP 500: {"error":"llama-server process has terminated: exit status 0xc0000409: The system detected an '
       'overrun of a stack-based buffer in this application. This overrun could potentially allow a malicious user '
       'to gain control of this application.: CUDA error: the provided PTX was compiled with an unsupported toolchain."}')


class FakeCpu:
    def __init__(self, url="http://127.0.0.1:11435"):
        self.url, self.started = url, 0

    def start(self):
        self.started += 1
        return self.url


class Client:
    def __init__(self, cfg):
        self.url = cfg["ollama_url"]


class ConnectTest(unittest.TestCase):
    def ping_fails_on(self, bad_url, error):
        def ping(c):
            if c.url == bad_url:
                raise LLMError(error)
        return ping

    def test_gpu_failure_switches_to_cpu(self):
        cfg = {"ollama_url": "http://127.0.0.1:11434"}
        out, cpu = [], FakeCpu()
        client, note = ollama_host.connect(cfg, Client, self.ping_fails_on("http://127.0.0.1:11434", PTX),
                                           out=out.append, cpu=cpu, info=lambda: ("NVIDIA GeForce GTX 1060 6GB", "456.71"))
        self.assertEqual(client.url, "http://127.0.0.1:11435")
        self.assertEqual(cfg["ollama_url"], "http://127.0.0.1:11435")
        self.assertIn("CPU", note)
        self.assertIn("GTX 1060 6GB / ドライバ 456.71", out[0])
        self.assertIn("ドライバ", out[0])

    def test_gpu_works_nothing_changes(self):
        cfg = {"ollama_url": "http://127.0.0.1:11434"}
        cpu = FakeCpu()
        client, note = ollama_host.connect(cfg, Client, lambda c: None, out=print, cpu=cpu)
        self.assertEqual((client.url, note, cpu.started), ("http://127.0.0.1:11434", "", 0))

    def test_other_failures_are_not_hidden(self):
        for err in ("Ollama に接続できません (http://127.0.0.1:11434/api/chat): refused",
                    'HTTP 404: {"error":"model \'gemma4:e2b\' not found"}'):
            cpu = FakeCpu()
            with self.assertRaises(LLMError):
                ollama_host.connect({"ollama_url": "http://127.0.0.1:11434"}, Client,
                                    self.ping_fails_on("http://127.0.0.1:11434", err), out=lambda m: None, cpu=cpu)
            self.assertEqual(cpu.started, 0)

    def test_cpu_server_cannot_start(self):
        with self.assertRaises(LLMError):
            ollama_host.connect({"ollama_url": "http://127.0.0.1:11434"}, Client,
                                self.ping_fails_on("http://127.0.0.1:11434", PTX), out=lambda m: None,
                                cpu=FakeCpu(url=None), info=lambda: ("", ""))

    def test_recognizes_gpu_failures(self):
        self.assertTrue(ollama_host.is_gpu_failure(PTX))
        self.assertTrue(ollama_host.is_gpu_failure("llama runner process has terminated: exit status 2"))
        self.assertFalse(ollama_host.is_gpu_failure("HTTP 404: model not found"))


class CpuOllamaTest(unittest.TestCase):
    def test_starts_a_second_ollama_that_cannot_see_the_gpu(self):
        started, state = [], {"up": False}

        def popen(cmd, env=None, **kw):
            started.append((cmd, env))
            state["up"] = True
            return type("P", (), {"terminate": lambda self: None})()

        def opener(url, timeout=None):
            if not state["up"]:
                raise OSError("refused")
            return io.BytesIO(json.dumps({"version": "0.12.0"}).encode())

        cpu = ollama_host.CpuOllama({"ollama_bin": "ollama"}, popen=popen, opener=opener, sleep=lambda s: None,
                                    env={}, which=lambda n: "/usr/bin/ollama")
        self.assertEqual(cpu.start(), "http://127.0.0.1:11435")
        [(cmd, env)] = started
        self.assertEqual(cmd, ["/usr/bin/ollama", "serve"])
        self.assertEqual((env["CUDA_VISIBLE_DEVICES"], env["OLLAMA_HOST"]), ("-1", "127.0.0.1:11435"))
        self.assertEqual(cpu.start(), "http://127.0.0.1:11435")        # 立っていればそのまま使う
        self.assertEqual(len(started), 1)
        cpu.stop()

    def test_no_ollama_exe(self):
        cpu = ollama_host.CpuOllama({}, opener=lambda *a, **k: (_ for _ in ()).throw(OSError()),
                                    env={}, which=lambda n: None)
        self.assertIsNone(cpu.start())


class SupervisorSetupTest(unittest.TestCase):
    def test_setup_problem_stops_instead_of_looping(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import supervisor
        calls = []
        code = supervisor.main([], popen=lambda *a, **k: calls.append(1) or 3, max_runs=5)
        self.assertEqual((code, len(calls)), (3, 1))


if __name__ == "__main__":
    unittest.main()
