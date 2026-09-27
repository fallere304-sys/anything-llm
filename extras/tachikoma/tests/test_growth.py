"""目の自習・学習、独りの時間のスケジューラ (タチコマが自分で選ぶ)、電力計、UI の検証。
PDF ライブラリ・OCR・nvidia-smi は偽物に差し替える。"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import config, expression  # noqa: E402
from tachikoma.activities import StudyActivity, TrainActivity  # noqa: E402
from tachikoma.dataset import TrainingData  # noqa: E402
from tachikoma.eye_learner import EyeLearner  # noqa: E402
from tachikoma.eyes import EyeStudy, base_font, char_confusions, valid_truth  # noqa: E402
from tachikoma.idle import IdleScheduler  # noqa: E402
from tachikoma.memory import Memory  # noqa: E402
from tachikoma.power import PowerMeter  # noqa: E402
from tachikoma.ui import UISensor, UIServer  # noqa: E402
from test_core import Clock  # noqa: E402


# ---------------------------------------------------------------- 偽の PDF ライブラリ
class FakePix:
    width, height = 200, 30

    def __init__(self, text):
        self.text = text

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.text)          # 偽の「画像」: 中身に正解文字列を入れておく


class FakePage:
    def __init__(self, lines):
        self.lines = lines               # [(text, font)]

    def get_text(self, kind):
        return {"blocks": [{"lines": [{"dir": (1.0, 0.0), "bbox": (10, 20 + i * 30, 300, 40 + i * 30),
                                       "spans": [{"text": t, "font": f, "size": 12}]}
                                      for i, (t, f) in enumerate(self.lines)]}]}

    def get_fonts(self):
        return [(0, "", "", f) for f in {f for _, f in self.lines}]

    def get_pixmap(self, dpi, clip):
        i = int((clip[1] - 20 + 3) // 30)
        return FakePix(self.lines[i][0])


class FakeDoc:
    def __init__(self, pages):
        self.pages = pages

    def __len__(self):
        return len(self.pages)

    def __getitem__(self, i):
        return self.pages[i]

    def close(self):
        pass


class FakeLib:
    def __init__(self, docs):
        self.docs = docs

    def open(self, path):
        return self.docs[os.path.basename(path)]

    @staticmethod
    def Rect(*a):
        return a


class ScriptOCR:
    """画像 (中身は正解) を、決めた読み間違いで読む偽 OCR。"""
    name = "base"

    def __init__(self, mistakes=None):
        self.mistakes = mistakes or {}

    def recognize(self, path):
        with open(path, encoding="utf-8") as f:
            t = f.read()
        for a, b in self.mistakes.items():
            t = t.replace(a, b)
        return t

    def load(self, m):
        self.name = m


def eye_setup(docs, mistakes, **over):
    tmp = tempfile.mkdtemp()
    cfg = config.load(None)
    cfg.update({"eye_dir": os.path.join(tmp, "eye"), "eye_holdout_ratio": 0.5, "eye_easy_ratio": 1.0,
                "eye_rule_min_count": 2}, **over)
    os.makedirs(cfg["eye_dir"], exist_ok=True)
    for name in docs:
        open(os.path.join(cfg["eye_dir"], name), "wb").close()
    mem = Memory(clock=Clock())
    eyes = EyeStudy(cfg, mem, ScriptOCR(mistakes), pdf_lib=FakeLib(docs), log=lambda m: None)
    return eyes, mem, cfg


LINES = [("ロボットの口が動いた", "ABCDEF+MS-Mincho"), ("工場の入口に口紅がある", "ABCDEF+MS-Mincho"),
         ("日本語の文字を読む練習", "ABCDEF+MS-Mincho"), ("口の形を見て話を聞く", "ABCDEF+MS-Mincho"),
         ("人口の統計と口座の管理", "ABCDEF+MS-Mincho"), ("文字化け", "Broken"),
         ("ABC only english line", "Arial")]


class EyeTest(unittest.TestCase):
    def test_helpers(self):
        self.assertTrue(valid_truth("日本語の文字を読む"))
        self.assertFalse(valid_truth("文字化け"))      # 私用領域 = 壊れた文字情報
        self.assertFalse(valid_truth("English only text"))
        self.assertEqual(base_font("ABCDEF+MS-Mincho"), "MS-Mincho")
        self.assertEqual(char_confusions("入口に行く", "入ロに行く"), [("ロ", "口")])

    def test_reads_pdf_and_learns_corrections(self):
        eyes, mem, cfg = eye_setup({"a.pdf": FakeDoc([FakePage(LINES)])}, {"口": "ロ"}, eye_holdout_ratio=1.0)
        for _ in range(3):
            eyes.step(budget_s=5)
        rows = eyes.samples()
        self.assertEqual({r["font"] for r in rows}, {"MS-Mincho"})    # 文字化け行・英語だけの行は使わない
        self.assertIn("hard", {r["kind"] for r in rows})
        self.assertEqual(eyes.state, "idle")
        self.assertIn(["ロ", "口"], eyes._kv("eye_rules"))            # 検証で効果のある補正だけ採用
        self.assertEqual(eyes.correct("入ロ"), "入口")
        self.assertIn("MS-Mincho", eyes.font_stats())
        self.assertIsNotNone(eyes.recent_cer())

    def test_curious_about_unknown_fonts(self):
        docs = {"known.pdf": FakeDoc([FakePage([("日本語の文字を読む", "Seen")])]),
                "new.pdf": FakeDoc([FakePage([("日本語の文字を読む", "Unseen-Font")])])}
        eyes, mem, cfg = eye_setup(docs, {})
        mem.db.execute("INSERT INTO ocr_fonts VALUES ('Seen', 1000, 10.0, 0)")
        self.assertTrue(eyes.pick_next(eyes.pending_pdfs()).endswith("new.pdf"))

    def test_fetches_only_government_pdfs(self):
        eyes, mem, cfg = eye_setup({}, {}, web=True, searxng_url="http://127.0.0.1:8080")
        got = []

        def opener(req, timeout):
            url = req.full_url
            got.append(url)
            if "/search" in url:
                return io.BytesIO(json.dumps({"results": [
                    {"url": "https://evil.example.com/x.pdf"},
                    {"url": "https://www.soumu.go.jp/main_content/000123.pdf"}]}).encode())
            return io.BytesIO(b"%PDF-1.7 ...")
        eyes.opener = opener
        self.assertTrue(eyes.fetch_one())
        self.assertFalse(any("evil" in u for u in got))
        saved = os.listdir(cfg["eye_dir"])
        self.assertTrue(any(f.endswith(".pdf") for f in saved))
        self.assertTrue(any(f.endswith(".source.txt") for f in saved))   # 出典を記録


class EyeLearnerTest(unittest.TestCase):
    def make(self, new_by_font):
        tmp = tempfile.mkdtemp()
        cfg = config.load(None)
        cfg.update({"finetune_dir": tmp, "eye_min_holdout": 2, "eye_min_new_samples": 1,
                    "eye_train_after_alone_s": 0, "eye_dir": os.path.join(tmp, "eye")})
        mem = Memory(clock=Clock())
        eyes = EyeStudy(cfg, mem, ScriptOCR(), pdf_lib=FakeLib({}), log=lambda m: None)
        for i, font in enumerate(["Mincho", "Gothic", "Mincho", "Gothic", "Mincho"]):
            crop = os.path.join(tmp, f"{i}.png")
            with open(crop, "w", encoding="utf-8") as f:
                f.write(font)
            mem.db.execute("INSERT INTO ocr_samples(ts, source, font, crop, ref, hyp, cer, kind, holdout)"
                           " VALUES (0,'a',?,?,'日本語の文字','日本言吾の文字',0.3,'hard',?)", (font, crop, 1 if i < 2 else 0))
        mem.db.commit()

        class Model:
            def __init__(self, path):
                self.path = path

            def recognize(self, crop):
                with open(crop, encoding="utf-8") as f:
                    font = f.read()
                return new_by_font[font] if self.path.endswith("model") else "日本言吾の文字"
        runner = lambda args, timeout, abort, on_proc: (0, "")  # noqa: E731
        data = TrainingData(mem)
        return EyeLearner(cfg, eyes, data, ScriptOCR(), Model, runner=runner, clock=Clock()), data

    def run_lr(self, lr):
        self.assertIsNotNone(lr.start("test"))
        lr._thread.join(10)
        return lr.poll()

    def test_adopts_when_all_fonts_improve(self):
        lr, data = self.make({"Mincho": "日本語の文字", "Gothic": "日本語の文字"})
        self.assertIn("切り替えました", self.run_lr(lr))
        self.assertTrue(lr.active_model().endswith("model"))

    def test_rejects_if_a_font_gets_worse(self):
        # 平均は良くなるが、Gothic が悪化 → 苦手なフォントを犠牲にした改善は採用しない
        lr, data = self.make({"Mincho": "日本語の文字", "Gothic": "目本言吾の文宇"})
        msg = self.run_lr(lr)
        self.assertIn("採用しません", msg)
        self.assertIn("Gothic", msg)


# ---------------------------------------------------------------- スケジューラ
class FakeActivity:
    def __init__(self, gain_per_step=1.0, ready=True, busy_steps=0):
        self.gain_per_step, self._ready, self.busy_steps = gain_per_step, ready, busy_steps
        self.total, self.steps, self.paused, self.begun = 0.0, 0, 0, 0

    def ready(self):
        return self._ready

    def begin(self):
        self.begun += 1
        self.left = self.busy_steps

    def step(self, budget):
        self.steps += 1
        self.total += self.gain_per_step

    def busy(self):
        if getattr(self, "left", 0) > 0:
            self.left -= 1
            return True
        return False

    def pause(self):
        self.paused += 1

    def progress(self):
        return self.total


class ConstPower:
    def watts(self, gpu_active_guess=False):
        return 100.0


def scheduler(**over):
    cfg = config.load(None)
    cfg.update({"idle_session_s": 60, "idle_rest_s": 60, "idle_explore": 0.0}, **over)
    clock = Clock()
    return IdleScheduler(cfg, Memory(clock=clock), ConstPower(), clock=clock), clock


class IdleTest(unittest.TestCase):
    def test_learns_which_activity_pays_off_per_wh(self):
        s, clock = scheduler(idle_explore=0.0)
        good, poor = FakeActivity(gain_per_step=5.0), FakeActivity(gain_per_step=0.01)
        s.register("eye_study", good)
        s.register("ear_study", poor)
        for _ in range(200):
            clock.t += 5
            s.step(alone=True)
        st = s.stats()
        self.assertGreater(st["eye_study"]["rate"], st["ear_study"]["rate"])
        self.assertGreater(good.steps, poor.steps)

    def test_only_one_activity_at_a_time_and_training_is_exclusive(self):
        s, clock = scheduler()
        train, study = FakeActivity(busy_steps=5), FakeActivity()
        s.register("ear_train", train)
        s.register("eye_study", study)
        s._kv("idle_stats", {"ear_train": {"rate": 10, "n": 1}, "eye_study": {"rate": 1, "n": 1}})
        s.step(alone=True)
        self.assertEqual(s.current, "ear_train")
        for _ in range(5):
            clock.t += 5
            s.step(alone=True)
            self.assertEqual(study.steps, 0)        # 学習中は目の自習をしない (並行しない)
        clock.t += 5
        train._ready = False                        # 学習すると未学習の標本が無くなる
        s.step(alone=True)                          # 学習が終わったら選び直す
        self.assertNotEqual(s.current, "ear_train")

    def test_rests_when_nothing_is_worth_the_energy(self):
        s, clock = scheduler(idle_rest_value=1.0)
        s.register("eye_study", FakeActivity(gain_per_step=0.0))
        s._kv("idle_stats", {"eye_study": {"rate": 0.001, "n": 5}})
        self.assertEqual(s.step(alone=True), "rest")

    def test_someone_arrives_interrupts(self):
        s, clock = scheduler()
        a = FakeActivity()
        s.register("eye_study", a)
        s.step(alone=True)
        s.step(alone=False)
        self.assertEqual(a.paused, 1)
        self.assertIsNone(s.current)


class ActivitiesTest(unittest.TestCase):
    def test_train_progress_and_per_sample_update(self):
        mem = Memory(clock=Clock())
        data = TrainingData(mem)

        class L:
            busy = False

            class eyes:
                @staticmethod
                def count_new_hard():
                    return 100

            def should_train(self, alone):
                return True

            def start(self, reason):
                data.set("ocr_versions", [{"adopt": True, "score": 0.09, "base_score": 0.10}])
                return "start"
        t = TrainActivity("eye", L(), data, lambda: 9999, log=lambda m: None)
        t.begin()
        self.assertAlmostEqual(t.progress(), 10.0)     # 相対 CER 改善 10% → 10 ポイント
        t.end()
        self.assertGreater(data.get("p_per_sample_eye"), 0.01)   # 標本の価値の見込みが上がる


class PowerTest(unittest.TestCase):
    def test_nvidia_smi_and_fallback(self):
        cfg = config.load(None)

        class R:
            returncode, stdout = 0, "63.52\n"
        p = PowerMeter(cfg, run=lambda *a, **k: R(), cpu_percent=lambda: 50.0)
        self.assertAlmostEqual(p.watts(), 63.52 + 32.5)

        def missing(*a, **k):
            raise FileNotFoundError("nvidia-smi")
        p = PowerMeter(cfg, run=missing, cpu_percent=lambda: None)
        self.assertAlmostEqual(p.watts(gpu_active_guess=True), 120 * 0.8 + 65 * 0.3)


# ---------------------------------------------------------------- UI
class UITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = config.load(None)
        cfg["ui_assets_dir"] = self.tmp
        open(os.path.join(self.tmp, "tachikoma.png"), "wb").close()
        open(os.path.join(self.tmp, "evil.exe"), "wb").close()
        self.ui = UIServer(cfg, port=0)
        self.url = self.ui.start()

    def tearDown(self):
        self.ui.close()

    def get(self, path):
        with urllib.request.urlopen(self.url.rstrip("/") + path, timeout=5) as r:
            return r.read().decode("utf-8")

    def post(self, body, headers):
        req = urllib.request.Request(self.url + "say", data=json.dumps(body).encode(), headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_pages_and_assets(self):
        self.assertIn("タチコマ", self.get("/"))
        self.assertEqual(json.loads(self.get("/assets.json")), ["tachikoma.png"])   # 画像以外は出さない
        self.assertIn("<svg", self.get("/static/avatar.svg"))

    def test_say_requires_header_and_origin(self):
        self.assertEqual(self.post({"text": "やあ"}, {"Content-Type": "application/json"}), 403)
        self.assertEqual(self.post({"text": "やあ"}, {"X-Tachikoma": "1", "Origin": "https://evil.example"}), 403)
        self.assertEqual(self.post({"text": "やあ"}, {"X-Tachikoma": "1"}), 204)
        self.assertEqual(UISensor(self.ui).poll(), [("user_message", "やあ")])

    def test_events_stream(self):
        got = []

        def listen():
            with urllib.request.urlopen(self.url + "events", timeout=5) as r:
                for _ in range(2):
                    line = r.readline().decode("utf-8")
                    while not line.startswith("data:"):
                        line = r.readline().decode("utf-8")
                    got.append(json.loads(line[5:]))
        t = threading.Thread(target=listen)
        t.start()
        for _ in range(50):
            if self.ui.clients:
                break
            threading.Event().wait(0.05)
        self.ui.push({"type": "say", "text": "わかった！", "expression": "proud"})
        t.join(5)
        self.assertEqual(got[1]["expression"], "proud")

    def test_expressions(self):
        self.assertEqual(expression.from_text("ねえねえ、さっきの調べたよ！"), "proud")
        self.assertEqual(expression.from_text("まだ知らないんです"), "puzzled")
        self.assertEqual(expression.from_text("なんでだろう？"), "curious")
        self.assertEqual(expression.from_state("alone", "eye_study"), "studying_eye")
        self.assertEqual(expression.from_state("conversing", thinking=True), "thinking")


if __name__ == "__main__":
    unittest.main()


class AgentIdleTest(unittest.TestCase):
    def test_alone_time_is_scheduled_and_shown(self):
        from test_core import make_agent
        agent, llm, sensor, mem, clock, out = make_agent(tempfile.mkdtemp())

        class UI:
            events = []

            def push(self, ev):
                self.events.append(ev)
        agent.ui = UI()
        agent.idle = IdleScheduler(agent.cfg, mem, ConstPower(), clock=clock)
        eye = FakeActivity()
        agent.idle.register("eye_study", eye)
        agent.attention.idle_fn = lambda: 10_000          # PC も触られていない
        clock.t += agent.cfg["absent_after_s"] + 1
        agent.step()
        self.assertEqual(agent.idle.current, "eye_study")
        states = [e for e in agent.ui.events if e["type"] == "state"]
        self.assertEqual(states[-1]["expression"], "studying_eye")
        agent.say("わかった！")
        self.assertEqual(agent.ui.events[-1]["expression"], "proud")
        # 人が来た → 自習を止める
        agent.attention.on_presence(True)
        agent.step()
        self.assertEqual(eye.paused, 1)
        self.assertIsNone(agent.idle.current)
