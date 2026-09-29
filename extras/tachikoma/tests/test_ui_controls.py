"""画面のカメラ・マイクのスイッチと、タチコマの画像の差し替え。"""

import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import config  # noqa: E402
from tachikoma.kernel.guard import check_source  # noqa: E402
from tachikoma.kernel.switches import Switches  # noqa: E402
from tachikoma.ui import PNG, UISensor, UIServer  # noqa: E402


class Store(dict):
    def set(self, k, v):
        self[k] = v


class SwitchesTest(unittest.TestCase):
    def test_turning_devices_on_and_off_is_remembered(self):
        calls, pushed, store = [], [], Store()
        sw = Switches(store=store, log=lambda m: None, push=pushed.append)
        sw.register("camera", "カメラ", lambda: calls.append("on"), lambda: calls.append("off"), on=True)
        self.assertTrue(sw.is_on("camera"))
        sw.set("camera", False)
        sw.flush()
        self.assertEqual((calls, store["switch_camera"], sw.is_on("camera")), (["off"], False, False))
        self.assertFalse(Switches(store=store).wanted("camera", True))         # 次の起動でも切ったまま
        sw.set("camera", True)
        self.assertEqual(calls, ["off", "on"])
        self.assertTrue(pushed[-1]["devices"]["camera"]["on"])

    def test_unavailable_device_explains_why(self):
        sw = Switches(log=lambda m: None)
        sw.register("mic", "マイク", why="音声の部品が入っていません")
        self.assertIn("使えません", sw.set("mic", True))
        self.assertEqual(sw.state()["devices"]["mic"], {"label": "マイク", "on": False, "available": False,
                                                        "why": "音声の部品が入っていません"})

    def test_device_that_fails_to_open_stays_off(self):
        sw = Switches(log=lambda m: None)

        def broken():
            raise RuntimeError("カメラ 0 を開けません")
        sw.register("camera", "カメラ", broken, lambda: None)
        self.assertIn("入れられませんでした", sw.set("camera", True))
        d = sw.state()["devices"]["camera"]
        self.assertEqual((d["on"], d["available"]), (False, True))
        self.assertIn("開けません", d["why"])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = config.load(None)
        cfg["ui_assets_dir"] = self.tmp
        self.ui = UIServer(cfg, port=0)
        self.url = self.ui.start().rstrip("/")

    def tearDown(self):
        self.ui.close()

    def post(self, path, data, ctype="application/json", headers=None):
        h = {"Content-Type": ctype, "X-Tachikoma": "1"}
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, data=data, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_switch_happens_at_once_even_while_the_main_loop_is_busy(self):
        # CPU で考え込んでいると本体のループは数分回らない。それでもスイッチはその場で切り替わる
        calls, store = [], Store()
        sw = Switches(store=store, log=lambda m: None)
        sw.register("camera", "カメラ", lambda: calls.append("on"), lambda: calls.append("off"), on=True)
        self.ui.switches = sw
        req = urllib.request.Request(self.url + "/switch", data=json.dumps({"name": "camera", "on": False}).encode(),
                                     headers={"Content-Type": "application/json", "X-Tachikoma": "1"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            out = json.loads(r.read().decode("utf-8"))
        self.assertEqual(calls, ["off"])                                # 本体のループを待たない
        self.assertFalse(out["devices"]["camera"]["on"])
        self.assertIn("切った", out["message"])
        self.assertEqual(store, {})                                     # 覚えるのは本体のループで (DB を取り合わない)
        UISensor(self.ui).poll()
        self.assertEqual(store, {"switch_camera": False})
        self.assertEqual(self.post("/switch", json.dumps({"name": "disk", "on": True}).encode()), 400)
        self.assertEqual(self.post("/switch", json.dumps({"name": "mic", "on": "yes"}).encode()), 400)
        self.assertEqual(self.post("/switch", json.dumps({"name": "camera", "on": True}).encode(),
                                   headers={"X-Tachikoma": "0"}), 403)
        self.assertEqual(self.post("/switch", json.dumps({"name": "camera", "on": True}).encode(),
                                   headers={"Origin": "https://evil.example"}), 403)
        self.assertEqual(calls, ["off"])

    def test_avatar_is_saved_locally_and_can_be_reset(self):
        png = PNG + b"\x00" * 64
        self.assertEqual(self.post("/avatar", png, "image/png"), 204)
        with open(os.path.join(self.tmp, "tachikoma.png"), "rb") as f:
            self.assertEqual(f.read(), png)
        self.assertEqual(self.post("/avatar", b"<svg onload=alert(1)>", "image/png"), 415)   # PNG だけ
        self.assertEqual(self.post("/avatar", b"", "image/png"), 413)
        self.assertEqual(self.post("/avatar", png, "image/png", headers={"Origin": "https://evil.example"}), 403)
        open(os.path.join(self.tmp, "tachikoma_happy.png"), "wb").close()
        open(os.path.join(self.tmp, "other.png"), "wb").close()
        self.assertEqual(self.post("/avatar/reset", b""), 204)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["other.png"])


class FakeCapture:
    opened = []

    def __init__(self, index, backend):
        FakeCapture.opened.append(self)
        self.released = False

    def isOpened(self):
        return True

    def release(self):
        self.released = True

    def read(self):
        return True, "frame"


class CameraTest(unittest.TestCase):
    def test_pause_releases_the_camera(self):
        cv2 = types.SimpleNamespace(VideoCapture=FakeCapture, CAP_DSHOW=1, CAP_ANY=0)
        sys.modules["cv2"] = cv2
        try:
            from tachikoma.camera import PresenceSensor
            cfg = {"camera_index": 0, "camera_interval_s": 0, "camera_absent_s": 5, "camera_motion_ratio": .02}
            cam = PresenceSensor(cfg, clock=lambda: 10.0)
            first = FakeCapture.opened[-1]
            cam.pause()
            self.assertTrue(first.released)
            self.assertTrue(cam.paused)
            self.assertEqual(cam.poll(), [])
            self.assertIsNone(cam.snapshot_b64())                  # 切っている間は 1 枚も取らない
            cam.resume()
            self.assertFalse(cam.paused)
            self.assertIsNot(FakeCapture.opened[-1], first)
        finally:
            del sys.modules["cv2"]


class MicTest(unittest.TestCase):
    def test_pause_drops_what_was_heard(self):
        from tachikoma.audio import VoiceSensor
        cfg = {"vad_end_silence_ms": 700, "vad_aggressiveness": 2}
        v = VoiceSensor(cfg, asr=None, listen=False)
        self.assertTrue(v.paused)
        v.q.put("audio")
        v.pause()
        self.assertEqual(v.poll(), [])
        self.assertTrue(v.q.empty())


class GuardTest(unittest.TestCase):
    def test_thinking_code_cannot_flip_the_switches(self):
        for src in ("def f(self):\n    self.ui.switches.set('camera', True)\n",
                    "x = 'switch_camera'\n",
                    "from .kernel.switches import Switches\n"):
            self.assertTrue(check_source("tachikoma/agent.py", src), src)


if __name__ == "__main__":
    unittest.main()
