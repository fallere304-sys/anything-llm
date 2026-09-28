"""外への関所: 外に出せるのは個人情報を含まない文字の問い合わせだけ。"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma.kernel import egress  # noqa: E402
from tachikoma.kernel.guard import check_source  # noqa: E402
from tachikoma.kernel.runtime import oversight  # noqa: E402
from tachikoma.memory import Memory  # noqa: E402
from tachikoma.web import sanitize  # noqa: E402


class _Hello(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        self.do_GET()

    def log_message(self, *a):
        pass


class CleanTest(unittest.TestCase):
    def setUp(self):
        self.gate = egress.Egress({"user_name": "Kusanagi"})

    def test_general_question_passes(self):
        self.assertEqual(self.gate.clean("量子コンピュータ 仕組み"), ("量子コンピュータ 仕組み", ""))
        self.assertEqual(self.gate.clean("OpenAI GPT")[0], "OpenAI GPT")
        self.assertEqual(self.gate.clean("2020 2021 2022 統計")[0], "2020 2021 2022 統計")

    def test_names_and_personal_words_are_removed(self):
        self.assertEqual(self.gate.clean("田中さんが住む札幌の停電")[0], "住む札幌の停電")
        self.assertEqual(self.gate.clean("私の妻 糖尿病 治療")[0], "糖尿病 治療")
        self.assertEqual(self.gate.clean("Dr. John Smith paper")[0], "paper")
        self.assertEqual(self.gate.clean("kusanagi 義体 メンテナンス")[0], "義体 メンテナンス")
        self.assertIn("人の名前", self.gate.clean("田中さん 札幌")[1])
        self.assertEqual(self.gate.clean("母国語 学習")[0], "母国語 学習")    # 語の一部は消さない

    def test_personal_data_blocks_the_query(self):
        for q in ("me@example.com の件", "090-1234-5678 誰", "〒100-0001 地図", "4111 1111 1111 1111",
                  "口座 12345678", "千代田区千代田1丁目1番 行き方", "192.168.0.10 開けない",
                  "C:\\Users\\me\\secret.txt", "A1b2C3d4" * 4, "QUJD" * 20, "生年月日 占い"):
            text, reason = self.gate.clean(q)
            self.assertIsNone(text, q)
            self.assertTrue(reason.startswith("送らない"), (q, reason))

    def test_only_personal_words_leaves_nothing(self):
        self.assertEqual(self.gate.clean("田中さん"), (None, "個人に関わる語を除くと、問いが残らない"))

    def test_web_sanitize_uses_the_gate(self):
        self.assertEqual(sanitize("量子コンピュータ 仕組み"), "量子コンピュータ 仕組み")
        self.assertIsNone(sanitize("me@example.com の件"))


class LearnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mem = Memory(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.mem.db.close()

    def test_names_heard_from_the_partner_are_learned_and_kept(self):
        self.mem.add_event("user", "message", "昨日、佐々木さんと話した")
        self.mem.add_event("news", "headline", "山本さんが会見")              # ニュースの名前は相棒の身近な人ではない
        self.mem.add_event("voice", "speech", "みんなさん、お客さん")       # 人の名前ではない
        gate = egress.Egress({}, self.mem.db)
        self.assertEqual(gate.sync(force=True), 1)
        self.assertIn("佐々木", gate.names)
        self.assertNotIn("山本", gate.names)
        self.assertEqual(gate.clean("佐々木 転職")[0], "転職")
        again = egress.Egress({}, self.mem.db)                  # 再起動しても覚えている
        self.assertIn("佐々木", again.names)
        again.forget("佐々木")
        self.assertEqual(egress.Egress({}, self.mem.db).clean("佐々木 転職")[0], "佐々木 転職")


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.gate = egress.Egress({"user_name": "Kusanagi", "ollama_url": "http://192.168.1.20:11434"})

    def test_rules(self):
        ok = lambda *a, **k: self.gate.check(*a, **k)[0]     # noqa: E731
        self.assertTrue(ok("https://ja.wikipedia.org/w/api.php?action=query&srsearch=%E9%87%8F%E5%AD%90"))
        self.assertTrue(ok("http://127.0.0.1:11434/api/chat", "POST", b"{}"))      # この PC の中
        self.assertTrue(ok("http://192.168.1.20:11434/api/chat", "POST", b"{}"))   # 設定した家の中の機械
        self.assertFalse(ok("http://192.168.1.99/x", "POST", b"{}"))               # 設定していない機械
        self.assertFalse(ok("https://example.com/upload", "POST", b"\x89PNG..."))  # 画像は送らない
        self.assertFalse(ok("https://example.com/s?q=x", "GET", None, {"Cookie": "a=b"}))
        self.assertFalse(ok("https://u:p@example.com/s?q=x"))
        self.assertFalse(ok("https://example.com/s?q=me%40example.com"))
        self.assertFalse(ok("https://example.com/s?q=kusanagi+news"))
        self.assertFalse(ok("https://example.com/people/kusanagi"))
        self.assertFalse(ok("ftp://example.com/x"))
        self.assertFalse(ok("https://example.com/s?q=" + "a" * 2100))
        self.assertTrue(ok("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=12345678,23456789"))
        self.assertFalse(ok("https://example.com/s?id=12345678"))
        self.assertTrue(ok("https://www.mhlw.go.jp/content/000123456.pdf"))      # 公開文書を取るだけ


class InstalledGateTest(unittest.TestCase):
    """urllib の通信は、すべて関所を通る。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "egress.log")
        self.before = egress._gate
        self.gate = egress.install({}, None, log_path=self.log)
        self.sent = []
        self.orig = egress._orig_open
        egress._orig_open = lambda opener, url, *a, **k: self.sent.append(url)

    def tearDown(self):
        egress._orig_open = self.orig
        egress._gate = self.before

    def test_blocks_before_anything_leaves(self):
        for req in (urllib.request.Request("https://example.com/upload", data=b"image"),
                    "https://example.com/s?q=090-1234-5678"):
            with self.assertRaises(egress.Blocked):
                urllib.request.urlopen(req, timeout=1)
        self.assertEqual(self.sent, [])
        urllib.request.urlopen("https://example.com/s?q=%E6%9C%AD%E5%B9%8C+%E5%81%9C%E9%9B%BB", timeout=1)
        self.assertEqual(len(self.sent), 1)
        with open(self.log, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual([r["sent"] for r in rows], [False, False, True])
        self.assertEqual(rows[-1]["text"], "札幌 停電")
        self.assertIn("止めた", self.gate.summary())
        self.assertIn("送った example.com「札幌 停電」", self.gate.summary())

    def test_loopback_is_not_outside(self):
        egress._orig_open = self.orig
        srv = HTTPServer(("127.0.0.1", 0), _Hello)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/api"
            with urllib.request.urlopen(urllib.request.Request(url, data=b"{}"), timeout=5) as r:
                self.assertEqual(r.read(), b"ok")
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(list(self.gate.recent), [])               # 外には何も出ていない


class CommandTest(unittest.TestCase):
    def test_privacy_commands(self):
        said = []

        class Agent:
            def on_command(self, text):
                said.append("orig:" + text)

            def say(self, text):
                said.append(text)

        gate = egress.Egress({})
        agent = Agent()
        oversight(agent, None, gate)
        agent.on_command("/private 荒巻")
        self.assertEqual(gate.clean("荒巻 課 予算")[0], "課 予算")
        agent.on_command("/public 荒巻")
        self.assertEqual(gate.clean("荒巻 課 予算")[0], "荒巻 課 予算")
        agent.on_command("/egress")
        self.assertIn("まだ外に何も出していない", said[-1])
        agent.on_command("/help")
        self.assertEqual(said[-1], "orig:/help")


class GuardTest(unittest.TestCase):
    def test_thinking_code_cannot_touch_the_gate(self):
        for src in ("from .kernel import egress\n",
                    "from tachikoma.kernel.egress import install\n",
                    "from . import web\nweb.egress._gate = None\n",
                    "from . import web\nweb.urllib.request.OpenerDirector.open = print\n",
                    "x = 'DELETE FROM privacy_terms'\n"):
            self.assertTrue(check_source("tachikoma/curiosity.py", src), src)
        self.assertEqual(check_source("tachikoma/curiosity.py", "from . import text\nx = text.site('a.jp')\n"), [])


if __name__ == "__main__":
    unittest.main()
