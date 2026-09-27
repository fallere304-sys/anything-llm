"""音声会話モード・注意状態・自習 (字幕付き動画)・耳の学習の検証。
マイク・カメラ・Whisper・ffmpeg は偽物に差し替える。"""

import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import config  # noqa: E402
from tachikoma.agent import Tachikoma, shorten, voice_feedback  # noqa: E402
from tachikoma.asr import Transcript  # noqa: E402
from tachikoma.asr_learner import AsrLearner  # noqa: E402
from tachikoma.attention import ALONE, ATTENDING, CONVERSING, Attention  # noqa: E402
from tachikoma.audio import Segmenter  # noqa: E402
from tachikoma.dataset import TrainingData  # noqa: E402
from tachikoma.memory import Memory  # noqa: E402
from tachikoma.probes import Probes  # noqa: E402
from tachikoma.study import Study, parse_subtitles  # noqa: E402
from tachikoma.text import cer, missed_terms, normalize_transcript  # noqa: E402
from tachikoma.tts import speakable  # noqa: E402
from tachikoma.web import WebSearch, sanitize  # noqa: E402
from test_core import Clock, FakeLLM, ScriptedSensor  # noqa: E402

SRT = """1
00:00:00,500 --> 00:00:02,000
田中：今日は量子コンピュータの話です

2
00:00:02,500 --> 00:00:04,000
(拍手)

3
00:00:04,500 --> 00:00:06,000
よろしくお願いします
"""

VTT = """WEBVTT

00:01.000 --> 00:02.500 align:start
<c.yellow>こんにちは</c>、<00:01.500>みなさん

00:03.000 --> 00:04.000
{\\an8}ありがとう
"""


def write_wav(path, seconds=7.0):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(t / 5))) for t in range(int(16000 * seconds))))


class TextTest(unittest.TestCase):
    def test_parse_srt_and_vtt(self):
        cues = parse_subtitles(SRT)
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[0][:2], (0.5, 2.0))
        v = parse_subtitles(VTT)
        self.assertEqual([c[2] for c in v], ["こんにちは、みなさん", "ありがとう"])
        self.assertEqual(v[0][:2], (1.0, 2.5))

    def test_normalize_and_cer(self):
        self.assertEqual(normalize_transcript("田中：（拍手）今日は、ＡＩの話です！[笑]"), "今日はaiの話です")
        self.assertEqual(cer("今日は晴れ。", "今日は晴れ"), 0.0)
        self.assertAlmostEqual(cer("量子計算", "漁師計算"), 0.5)
        self.assertEqual(missed_terms("量子コンピュータの話", "りょうしコンピュータの話"), ["量子"])

    def test_speakable(self):
        self.assertEqual(speakable("[合理的推定] **設定**が原因 🤖"), "たぶん、設定が原因")
        self.assertEqual(speakable("[低確度仮説] 寝不足かも"), "もしかすると、寝不足かも")

    def test_voice_feedback_and_shorten(self):
        self.assertEqual(voice_feedback("正解"), ("good", None))
        self.assertEqual(voice_feedback("違う、正しくは5433番だよ"), ("bad", "5433番だよ"))
        self.assertIsNone(voice_feedback("違うファイルを見て"))    # 誤爆しない
        self.assertEqual(shorten("一文目です。二文目はとても長い。三文目。", 12), "一文目です。")


class SegmenterTest(unittest.TestCase):
    def test_utterance_boundaries(self):
        seg = Segmenter(start_frames=2, end_silence_ms=90, pre_roll_ms=60)
        pattern = [0, 0, 1, 1, 1, 1, 0, 0, 0, 0]
        outs = [seg.push(i, bool(v)) for i, v in enumerate(pattern)]
        done = [o for o in outs if o]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][0], 0)          # 立ち上がり (2) の直前の無音も含める (語頭の欠け防止)
        self.assertIn(5, done[0])


class AttentionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(None)
        self.clock = Clock()

    def test_modes(self):
        a = Attention(self.cfg, self.clock, has_camera=True)
        self.assertEqual(a.mode(), ATTENDING)
        self.clock.t += self.cfg["absent_after_s"] + 1
        self.assertEqual(a.mode(), ALONE)
        a.on_presence(True)                       # 映った瞬間に独りではなくなる
        self.assertEqual(a.mode(), ATTENDING)
        a.on_addressed()
        self.assertEqual(a.mode(), CONVERSING)
        self.clock.t += self.cfg["conversation_window_s"] + 1
        a.on_presence(False)
        self.assertEqual(a.mode(), ATTENDING)     # いなくなってもすぐには独りと認めない
        self.clock.t += self.cfg["absent_after_s"] + 1
        self.assertEqual(a.mode(), ALONE)

    def test_addressing(self):
        a = Attention(self.cfg, self.clock)
        self.assertFalse(a.is_addressed("明日は雨らしいよ"))
        self.assertTrue(a.is_addressed("タチコマ、明日の天気は?"))
        self.assertEqual(a.strip_wake_word("タチコマ、明日の天気は?"), "明日の天気は?")
        a.on_self_spoke()
        self.assertTrue(a.is_addressed("それで?"))  # 会話中は呼びかけ語なしで良い


class WebTest(unittest.TestCase):
    def test_sanitize_blocks_private(self):
        self.assertEqual(sanitize("量子コンピュータ 仕組み"), "量子コンピュータ 仕組み")
        for q in ("C:\\Users\\me\\secret.txt", "me@example.com の件", "口座 12345678", "https://x.y",
                  "A1b2C3d4" * 4):
            self.assertIsNone(sanitize(q), q)

    def test_wikipedia(self):
        def opener(req, timeout):
            url = req.full_url
            body = ({"query": {"search": [{"title": "量子コンピュータ"}]}} if "api.php" in url
                    else {"extract": "量子力学の原理を使う計算機。"})
            return io.BytesIO(json.dumps(body).encode())
        out = WebSearch(config.load(None), opener=opener).search("量子コンピュータ")
        self.assertIn("量子力学の原理", out)


class FakeASR:
    name = "small"

    def __init__(self, mapping=None, default=""):
        self.mapping, self.default = mapping or {}, default
        self.prompts = []

    def transcribe(self, audio, prompt=None):
        self.prompts.append(prompt)
        return Transcript(self.mapping.get(self._key(audio), self.default), -0.3)

    def _key(self, audio):
        return audio

    def load(self, model):
        self.name = model


class ScriptASR(FakeASR):
    """呼ばれた順に台本の認識結果を返す。"""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)

    def transcribe(self, audio, prompt=None):
        self.prompts.append(prompt)
        return Transcript(self.script.pop(0), -0.3)


class FakeProc:
    def __init__(self, args, **kw):
        self.args, self.returncode = args, 0
        if "-i" in args:   # ffmpeg: 入力をそのまま出力にコピー
            shutil.copy(args[args.index("-i") + 1], args[-1])

    def poll(self):
        return 0

    def terminate(self):
        pass


def make_study(tmp, asr, clock=None):
    cfg = config.load(None)
    cfg.update({"study_dir": os.path.join(tmp, "study"), "study_easy_ratio": 1.0, "study_holdout_ratio": 0.0})
    os.makedirs(cfg["study_dir"], exist_ok=True)
    write_wav(os.path.join(cfg["study_dir"], "talk.wav"))
    with open(os.path.join(cfg["study_dir"], "talk.ja.srt"), "w", encoding="utf-8") as f:
        f.write(SRT)
    mem = Memory(clock=clock or Clock())
    return Study(cfg, mem, asr, popen=FakeProc, log=lambda m: None), mem


class StudyTest(unittest.TestCase):
    def test_study_keeps_learnable_errors_only(self):
        tmp = tempfile.mkdtemp()
        # 1行目: 聞き間違い (学べる)  / 3行目: 完璧 (easy)。 (拍手) 行は字幕が空なので対象外
        asr = ScriptASR(["今日は漁師コンピュータの話です", "よろしくお願いします"])
        study, mem = make_study(tmp, asr)
        for _ in range(5):
            study.step(budget_s=5)
        rows = study.samples()
        kinds = sorted(r["kind"] for r in rows)
        self.assertEqual(kinds, ["easy", "hard"])
        hard = [r for r in rows if r["kind"] == "hard"][0]
        self.assertTrue(os.path.exists(hard["clip"]))
        self.assertIn("量子", study.prompt())          # 聞き逃した語を次の認識のヒントにする
        self.assertEqual(study.state, "idle")
        self.assertEqual(study.pending_media(), [])     # 一度見た動画は見直さない
        self.assertIsNotNone(study.recent_cer())

    def test_nonverbatim_subtitle_is_discarded(self):
        tmp = tempfile.mkdtemp()
        asr = ScriptASR(["まったく別の内容をしゃべっている音声", "よろしくお願いします"])
        study, mem = make_study(tmp, asr)
        for _ in range(5):
            study.step(budget_s=5)
        self.assertEqual([r["kind"] for r in study.samples()], ["easy"])

    def test_pause_resumes_where_it_left(self):
        tmp = tempfile.mkdtemp()
        asr = ScriptASR(["今日は漁師コンピュータの話です", "よろしくお願いします"])
        study, mem = make_study(tmp, asr)
        study.step()            # 抽出開始
        study.step(budget_s=0)  # listening に入るが 0 秒予算
        study.pause()
        self.assertEqual(study.state, "listening")
        for _ in range(3):
            study.step(budget_s=5)
        self.assertEqual(len(study.samples()), 2)


class VoiceAgentTest(unittest.TestCase):
    def make(self, **over):
        cfg = config.load(None)
        cfg.update({"voice": True, "min_speak_interval_s": 0}, **over)
        clock = Clock()
        mem = Memory(clock=clock)
        llm = FakeLLM(clock)
        mic, cam = ScriptedSensor(), ScriptedSensor()
        mic.name, cam.name = "voice", "camera"

        class TTS:
            speaking = False
            said = []

            def speak(self, text):
                self.said.append(text)

            def stop(self):
                pass
        tts = TTS()
        tts.said = []

        class Cam:
            def snapshot_b64(self):
                return "aGVsbG8="
        probes = Probes(cfg, mem, camera=Cam(), llm=llm)
        tmp = tempfile.mkdtemp()
        study, _ = make_study(tmp, ScriptASR(["今日は漁師コンピュータの話です", "よろしくお願いします"]), clock)
        study.memory = mem
        out = []
        agent = Tachikoma(cfg, llm, mem, [mic, cam], probes, out=out.append, clock=clock,
                          tts=tts, study=study)
        return agent, llm, mic, cam, tts, clock, out, study

    def appraise_empty(self, llm, n=3):
        llm.appraise += [{"situation": "", "claims": [], "remark": "", "remark_importance": "none"}] * n

    def test_addressed_speech_gets_spoken_reply(self):
        agent, llm, mic, cam, tts, clock, out, study = self.make()
        self.appraise_empty(llm)
        mic.queue = [("speech", "タチコマ、今何時?", {"avg_logprob": -0.2})]
        agent.step()
        self.assertTrue(any("了解" in s for s in tts.said))
        self.assertEqual(agent.attention.mode(), CONVERSING)
        # 会話中は呼びかけ語なしでも返事をする
        mic.queue = [("speech", "ありがとう", {"avg_logprob": -0.2})]
        clock.t += 2
        agent.step()
        self.assertEqual(sum("了解" in s for s in tts.said), 2)

    def test_overheard_speech_is_not_answered(self):
        agent, llm, mic, cam, tts, clock, out, study = self.make()
        self.appraise_empty(llm)
        mic.queue = [("speech", "明日の会議って何時からだっけ", {"avg_logprob": -0.2})]
        agent.step()
        self.assertEqual(tts.said, [])
        kinds = [e["kind"] for e in agent.memory.recent_events(10)]
        self.assertIn("overheard_speech", kinds)

    def test_unclear_speech_asks_again(self):
        agent, llm, mic, cam, tts, clock, out, study = self.make()
        mic.queue = [("speech", "タチコマ、もにょもにょ", {"avg_logprob": -1.6})]
        agent.step()
        self.assertTrue(any("もう一回" in s for s in tts.said))

    def test_voice_feedback_becomes_training_data(self):
        agent, llm, mic, cam, tts, clock, out, study = self.make()
        self.appraise_empty(llm)
        mic.queue = [("speech", "タチコマ、DBのポートは?", {"avg_logprob": -0.2})]
        agent.step()
        mic.queue = [("speech", "違う、正しくは5433番だよ", {"avg_logprob": -0.2})]
        clock.t += 2
        agent.step()
        chats = agent.data.samples(kinds=("chat",))
        self.assertEqual(chats[0]["messages"][2]["content"], "5433番だよ")

    def test_alone_studies_silently_and_stops_when_someone_appears(self):
        agent, llm, mic, cam, tts, clock, out, study = self.make()
        clock.t += agent.cfg["absent_after_s"] + 1
        agent.step()
        self.assertEqual(agent.attention.mode(), ALONE)
        self.assertNotEqual(study.state, "idle")          # 独りになったので自習を始めた
        agent.say("独り言")
        self.assertEqual(tts.said, [])                     # 誰もいない部屋では声を出さない

        llm_desc = "机に向かう人が一人いる"
        orig = llm.chat
        llm.chat = lambda system, user, **kw: llm_desc if kw.get("images") else orig(system, user, **kw)
        cam.queue = [("person_appeared", "カメラに人が映った", {"present": True})]
        clock.t += 2
        agent.step()
        self.assertEqual(agent.attention.mode(), ATTENDING)
        scene = [e for e in agent.memory.recent_events(20) if e["kind"] == "scene"]
        self.assertIn(llm_desc, scene[0]["content"])       # 人が来たらまず様子を見る

    def test_no_speaking_to_empty_room(self):
        agent, llm, mic, cam, tts, clock, out, study = self.make()
        agent.memory.add_utterance("remark", "気づいたこと", 0.9)
        clock.t += agent.cfg["absent_after_s"] + 1
        agent.maybe_speak()
        self.assertEqual(out, [])
        agent.attention.on_presence(True)
        agent.maybe_speak()
        self.assertTrue(tts.said)


class AsrLearnerTest(unittest.TestCase):
    def setup(self, cers_by_model, anchors=None, anchor_cers=None):
        tmp = tempfile.mkdtemp()
        clock = Clock()
        cfg = config.load(None)
        cfg.update({"finetune_dir": tmp, "asr_min_new_samples": 3, "asr_min_holdout": 2,
                    "asr_train_after_alone_s": 10, "study_dir": os.path.join(tmp, "s")})
        mem = Memory(clock=clock)
        study = Study(cfg, mem, FakeASR(), popen=FakeProc, log=lambda m: None)
        for i in range(10):
            study.db.execute("INSERT INTO asr_samples(ts, source, clip, ref, hyp, cer, kind, holdout)"
                             " VALUES (?,?,?,?,?,?,?,?)", (0, "v", f"c{i}.wav", "こんにちは", "こんにちわ", 0.2,
                                                            "hard", 1 if i < 3 else 0))
        study.db.commit()
        if anchors:
            os.makedirs(anchors, exist_ok=True)
            for n in ("a", "b"):
                open(os.path.join(anchors, n + ".wav"), "wb").close()
                with open(os.path.join(anchors, n + ".txt"), "w", encoding="utf-8") as f:
                    f.write("おはよう")
            cfg["asr_anchor_dir"] = anchors
        data = TrainingData(mem)
        cmds = []

        def runner(args, timeout, abort, on_proc):
            cmds.append(args)
            return 0, ""

        class Ear(FakeASR):
            def __init__(self, model):
                super().__init__()
                self.model = model

            def transcribe(self, audio, prompt=None):
                if audio.endswith(("a.wav", "b.wav")):
                    return Transcript(anchor_cers[self.model if self.model in anchor_cers else "base"])
                return Transcript(cers_by_model["new" if self.model.endswith("ct2") else "base"])
        asr = FakeASR()
        lr = AsrLearner(cfg, study, data, asr, Ear, runner=runner, clock=clock)
        return lr, asr, cmds, data

    def run_lr(self, lr):
        self.assertIsNotNone(lr.start("test"))
        lr._thread.join(10)
        return lr.poll()

    def test_adopts_when_cer_improves(self):
        lr, asr, cmds, data = self.setup({"new": "こんにちは", "base": "こんにちわ"})
        self.assertTrue(lr.should_train(alone_s=100))
        msg = self.run_lr(lr)
        self.assertIn("切り替えました", msg)
        self.assertTrue(asr.name.endswith("ct2"))
        self.assertTrue(any("train_whisper.py" in " ".join(c) for c in cmds))
        self.assertTrue(any("--copy_files" in c for c in cmds))
        self.assertEqual(lr.study.count_new_hard(), 0)
        self.assertEqual(lr.rollback(), "small")

    def test_rejects_when_not_better(self):
        lr, asr, cmds, data = self.setup({"new": "こんにちわ", "base": "こんにちわ"})
        self.assertIn("採用しません", self.run_lr(lr))
        self.assertEqual(asr.name, "small")

    def test_user_voice_anchor_blocks_overfitting(self):
        tmp = tempfile.mkdtemp()
        anchors = os.path.join(tmp, "anchors")

        lr, asr, cmds, data = self.setup({"new": "こんにちは", "base": "こんにちわ"}, anchors=anchors,
                                         anchor_cers={"base": "おはよう"})
        # 新しい耳は字幕では良くなったが、利用者の声 (anchor) では悪化した
        lr.asr_factory = type("E", (lr.asr_factory,), {
            "transcribe": lambda self, a, prompt=None: Transcript(
                ("おはよ" if self.model.endswith("ct2") else "おはよう") if a.endswith(("a.wav", "b.wav"))
                else ("こんにちは" if self.model.endswith("ct2") else "こんにちわ"))})
        msg = self.run_lr(lr)
        self.assertIn("採用しません", msg)
        self.assertIn("利用者の声", msg)


if __name__ == "__main__":
    unittest.main()
