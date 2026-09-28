"""Android 版の外界との境界 (tachikoma/android) を、偽の端末 (Bridge) と偽の推論 (LlamaEngine) で確かめる。"""

import json
import os
import sys
import tempfile
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from tachikoma import prompts  # noqa: E402
from tachikoma.android.device import AndroidProbes, BridgeSensor, Inbox, Output  # noqa: E402
from tachikoma.android.llm import LocalLLM, cap_strings, schema_to_grammar  # noqa: E402
from tachikoma.llm import LLMError  # noqa: E402
from tachikoma.memory import Memory  # noqa: E402

BOOT = os.path.join(HERE, "android", "app", "src", "main", "python")


def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


class FakeBridge:
    def __init__(self):
        self.inbox, self.said, self.logs, self.offered = [], [], [], []
        self.camera, self.battery = False, True

    def push(self, source, kind, text="", meta=None):
        self.inbox.append({"source": source, "kind": kind, "text": text, "meta": meta or {}})

    def poll(self):
        items, self.inbox = self.inbox, []
        return json.dumps(items, ensure_ascii=False) if items else None

    def say(self, text):
        self.said.append(text)

    def log(self, text):
        self.logs.append(text)

    def state(self, js):
        pass

    def idleSeconds(self):
        return 0.0

    def batteryOk(self, min_percent):
        return self.battery

    def batteryWatts(self):
        return 1.5

    def cameraOn(self):
        return self.camera

    def describeCamera(self, timeout_ms):
        return "Television, Person / 文字: 大雨特別警報" if self.camera else None

    def offerFile(self, path, title):
        self.offered.append((path, title))


class FakeEngine:
    """文法の中身を見て、スキーマに合う JSON を返す偽の推論。"""

    def __init__(self):
        self.calls, self.adapters, self.error = [], [], ""
        self.fail = False
        self.tokens_per_char = 1.0

    def chat(self, system, user, grammar, max_tokens, temperature, allow_abort):
        self.calls.append({"system": system, "user": user, "grammar": grammar, "max_tokens": max_tokens,
                           "allow_abort": allow_abort})
        if self.fail:
            self.error = "boom"
            return None
        if not grammar:
            return "  うん、わかった！  "
        if "verdict" in grammar:
            return json.dumps({"verdict": "supports", "reason": "根拠がある"})
        if "claims" in grammar:
            return json.dumps({"situation": "", "claims": [], "remark": "", "remark_importance": "none", "question": ""})
        if "probe" in grammar:
            return json.dumps({"probe": "wait_observe", "query": ""})
        if "hypotheses" in grammar:
            return json.dumps({"hypotheses": []})
        if "premises" in grammar:
            return json.dumps({"premises": [], "unknowns": [], "answerable": True})
        return "{}"

    def countTokens(self, text):
        return int(len(text) * self.tokens_per_char)

    def setAdapter(self, path, scale):
        self.adapters.append(path)
        return not path.endswith("broken.gguf")

    def lastError(self):
        return self.error

    def abort(self):
        pass


def cfg(**over):
    from tachikoma import config
    c = config.load(None)
    c.update({"android_max_tokens": 384, "android_max_string": 300, "num_ctx": 2048, "gpu_duty_cycle": 1.0,
              "adapters_dir": tempfile.mkdtemp()}, **over)
    return c


class LocalLLMTest(unittest.TestCase):
    def test_structured_output_through_grammar(self):
        eng = FakeEngine()
        llm = LocalLLM(cfg(), eng)
        out = llm.chat(prompts.JUDGE_SYSTEM, "仮説: 空は青い", schema=prompts.JUDGE_SCHEMA)
        self.assertEqual(out["verdict"], "supports")
        g = eng.calls[-1]["grammar"]
        self.assertIn("root ::=", g)
        self.assertIn('"\\"irrelevant\\""', g)                       # 列挙はそのまま文法に
        self.assertEqual(llm.chat("s", "u"), "うん、わかった！")        # 文法なし = 自由な文章
        self.assertEqual(eng.calls[-1]["grammar"], "")

    def test_strings_are_capped_so_json_always_closes(self):
        capped = cap_strings(prompts.APPRAISE_SCHEMA, 300)
        self.assertEqual(capped["properties"]["situation"]["maxLength"], 300)
        self.assertNotIn("maxLength", capped["properties"]["remark_importance"])     # 列挙には付けない
        self.assertNotIn("maxLength", prompts.APPRAISE_SCHEMA["properties"]["situation"])   # 元は変えない
        for schema in (prompts.JUDGE_SCHEMA, prompts.APPRAISE_SCHEMA, prompts.INQUIRY_SCHEMA, prompts.WONDER_SCHEMA,
                       prompts.plan_schema(["search_memory", "wait_observe"])):
            self.assertIn("root ::=", schema_to_grammar(schema))

    def test_long_input_is_shortened_in_the_middle(self):
        eng = FakeEngine()
        llm = LocalLLM(cfg(num_ctx=1000, android_max_tokens=200), eng)
        user = "状況: 最初の部分" + "あ" * 5000 + "新しい出来事: 最後の部分"
        llm.chat("s", user)
        sent = eng.calls[-1]["user"]
        self.assertLess(len(sent), 1000)
        self.assertTrue(sent.startswith("状況: 最初の部分"))
        self.assertTrue(sent.endswith("新しい出来事: 最後の部分"))
        self.assertIn("省略", sent)

    def test_failures_and_bad_json_raise(self):
        eng = FakeEngine()
        llm = LocalLLM(cfg(), eng)
        eng.fail = True
        with self.assertRaises(LLMError):
            llm.chat("s", "u")
        eng.fail = False
        eng.chat = lambda *a: "{壊れた"
        with self.assertRaises(LLMError):
            llm.chat("s", "u", schema=prompts.JUDGE_SCHEMA)

    def test_battery_and_replies(self):
        eng = FakeEngine()
        state = {"ok": False}
        llm = LocalLLM(cfg(), eng, power_ok=lambda: state["ok"])
        self.assertFalse(llm.gate.can_run_background())        # 電池が少ない: 背景思考はしない
        state["ok"] = True
        self.assertTrue(llm.gate.can_run_background())
        llm.chat("s", "u")
        self.assertTrue(eng.calls[-1]["allow_abort"])            # 背景の推論は、話しかけで打ち切れる
        llm.foreground += 1
        llm.chat("s", "u")
        self.assertFalse(eng.calls[-1]["allow_abort"])           # 返事は打ち切らない

    def test_adapters_switch_per_model_name(self):
        eng = FakeEngine()
        c = cfg(model="tinyswallow.gguf")
        llm = LocalLLM(c, eng)
        llm.chat("s", "u", model="tachikoma-android-v1")
        self.assertEqual(eng.adapters[-1], os.path.join(c["adapters_dir"], "tachikoma-android-v1.gguf"))
        llm.chat("s", "u", model="tachikoma-android-v1")
        self.assertEqual(len(eng.adapters), 1)                  # 同じなら当て直さない
        llm.chat("s", "u")                                      # 基盤モデルに戻す
        self.assertEqual(eng.adapters[-1], "")
        with self.assertRaises(LLMError):
            llm.chat("s", "u", model="broken")


class DeviceTest(unittest.TestCase):
    def test_inputs_are_routed_by_source(self):
        b = FakeBridge()
        inbox = Inbox(b)
        user, voice = BridgeSensor("user", inbox), BridgeSensor("voice", inbox)
        b.push("user", "user_message", "こんにちは")
        b.push("voice", "speech", "タチコマ、天気は？", {"avg_logprob": -0.2})
        b.push("camera", "person_appeared", "カメラに人が映った", {"present": True})
        self.assertEqual(voice.poll(), [("speech", "タチコマ、天気は？", {"avg_logprob": -0.2})])
        self.assertEqual(user.poll(), [("user_message", "こんにちは", {})])
        self.assertEqual(BridgeSensor("camera", inbox).poll()[0][0], "person_appeared")
        self.assertEqual(user.poll(), [])

    def test_camera_look_and_text_only_output(self):
        b = FakeBridge()
        p = AndroidProbes(cfg(), Memory(), b)
        self.assertIsNone(p.camera)
        self.assertIsNone(p.run("look", "何が見える"))
        b.camera = True
        self.assertIsNotNone(p.camera)
        self.assertIn("大雨特別警報", p.run("look", "何が見える"))
        out = Output(b)
        out("[タチコマ 10:00] やあ")
        out("  · 好奇心 0.5: …")
        self.assertEqual((b.said, b.logs), (["[タチコマ 10:00] やあ"], ["好奇心 0.5: …"]))


class AppTest(unittest.TestCase):
    """起動から、話しかけ → 返事、学習データの書き出し、アダプタの取り込みと採否まで。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"web": False, "news": False, "finetune_min_holdout": 1, "min_speak_interval_s": 0}, f)

    def tearDown(self):
        self._tmp.cleanup()

    def app(self):
        from tachikoma.android.main import App
        b, e = FakeBridge(), FakeEngine()
        return App(b, e, self.home, "tinyswallow.gguf"), b, e

    def test_conversation_and_export(self):
        app, b, e = self.app()
        self.assertEqual(app.cfg["num_ctx"], 2048)               # 端末向けの既定値
        self.assertFalse(app.cfg["finetune_enabled"])
        b.push("user", "user_message", "こんにちは、タチコマ")
        app.run(max_steps=3, sleep=lambda s: None)
        self.assertTrue(any("うん、わかった" in s for s in b.said), b.said)
        spoken = app.memory.db.execute("SELECT COUNT(*) FROM spoken").fetchone()[0]
        self.assertGreater(spoken, 0)                             # 自発性の選択圧も端末で記録される
        b.push("app", "export")
        app.run(max_steps=1, sleep=lambda s: None)
        path, title = b.offered[-1]
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(sorted(zf.namelist()), ["base_model.txt", "holdout.jsonl", "train.jsonl"])
            self.assertIn("TinySwallow", zf.read("base_model.txt").decode())

    def test_adapter_is_adopted_only_if_not_worse(self):
        app, b, e = self.app()
        app.data.add_sample("chat", prompts.PERSONA, "こんにちは", "うん、わかった！", "user_feedback")
        for i in range(8):
            app.data.add_sample("chat", prompts.PERSONA, f"質問{i}", "うん、わかった！", "user_feedback")
        src = os.path.join(self.home, "incoming.gguf")
        write(src, b"GGUF")
        b.push("app", "import_adapter", "", {"path": src})
        app.run(max_steps=1, sleep=lambda s: None)
        app.learner._thread.join(10)
        app.run(max_steps=1, sleep=lambda s: None)                # 結果の反映 (メインスレッド)
        v = app.data.get("active_model")
        self.assertTrue(v and v.startswith("tachikoma-android-v"), b.said)
        self.assertEqual(app.llm.model, v)
        self.assertTrue(os.path.exists(app.llm.adapter_path(v)))
        self.assertIn(app.llm.adapter_path(v), e.adapters)          # 検証でアダプタを当てた
        self.assertEqual(app.learner.rollback(), "tinyswallow.gguf")   # 基盤モデルに戻せる

    def test_broken_adapter_is_rejected(self):
        app, b, e = self.app()
        e.setAdapter = lambda path, scale: path == ""               # どのアダプタも当てられない
        src = os.path.join(self.home, "x.gguf")
        write(src, b"GGUF")
        b.push("app", "import_adapter", "", {"path": src})
        app.run(max_steps=1, sleep=lambda s: None)
        app.learner._thread.join(10)
        app.run(max_steps=1, sleep=lambda s: None)
        self.assertIsNone(app.data.get("active_model"))
        self.assertTrue(any("採用しません" in s or "失敗" in s for s in b.said), b.said)
        self.assertEqual(os.listdir(app.cfg["adapters_dir"]), [])   # 採用しない版は残さない


class BootTest(unittest.TestCase):
    def setUp(self):
        if not os.path.isdir(BOOT):
            self.skipTest("Android のアプリのソースが無い (入れたあとの PC 版アプリ・隔離環境)")
        sys.path.insert(0, BOOT)
        import tachikoma_boot
        self.boot = tachikoma_boot
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def payload(self, files, version, evolvable=()):
        import hashlib
        p = os.path.join(self._tmp.name, f"payload-{version}.zip")
        man = {"version": version, "files": {}}
        with zipfile.ZipFile(p, "w") as zf:
            for rel, text in files.items():
                data = text.encode()
                zf.writestr("app/" + rel, data)
                man["files"][rel] = {"sha": hashlib.sha256(data).hexdigest(), "evolvable": rel in evolvable}
            zf.writestr("manifest.json", json.dumps(man))
        return p

    def test_update_keeps_evolution_and_memory(self):
        home = os.path.join(self._tmp.name, "home")
        ev = {"tachikoma/curiosity.py"}
        self.assertTrue(self.boot.install(self.payload({"tachikoma/curiosity.py": "v1", "tachikoma/kernel/x.py": "k1"},
                                                       "1", ev), home)[0])
        self.assertFalse(self.boot.install(self.payload({"tachikoma/curiosity.py": "v1"}, "1", ev), home)[0])
        with open(os.path.join(home, "tachikoma", "curiosity.py"), "w") as f:
            f.write("evolved")
        with open(os.path.join(home, "tachikoma.db"), "w") as f:
            f.write("memories")
        updated, kept = self.boot.install(self.payload({"tachikoma/curiosity.py": "v2", "tachikoma/kernel/x.py": "k2"},
                                                       "2", ev), home)
        self.assertEqual(kept, ["tachikoma/curiosity.py"])
        def read(rel):
            with open(os.path.join(home, *rel.split("/")), encoding="utf-8") as f:
                return f.read()
        self.assertEqual((read("tachikoma/curiosity.py"), read("tachikoma/curiosity.py.new")), ("evolved", "v2"))
        self.assertEqual(read("tachikoma/kernel/x.py"), "k2")
        self.assertEqual(read("tachikoma.db"), "memories")


if __name__ == "__main__":
    unittest.main()
