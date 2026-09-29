"""学習データの蓄積と、学習パイプライン (学習→登録→検証→採用) の検証。
実際の学習・Ollama は偽物に差し替える。"""

import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import config, prompts  # noqa: E402
from tachikoma.dataset import TrainingData  # noqa: E402
from tachikoma.learner import Aborted, Learner  # noqa: E402
from tachikoma.llm import GpuGate  # noqa: E402
from tachikoma.memory import FACT, Memory  # noqa: E402
from test_core import Clock, make_agent  # noqa: E402


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "settings.py"), "w", encoding="utf-8") as f:
            f.write("DB_PORT = 5433\n")

    def test_autonomous_investigation_becomes_training_data(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp, max_probe_attempts=6)
        for fn, text in (("README.md", "待ち受けポートは 5433\n"), ("notes.txt", "本番も 5433 に統一した\n")):
            with open(os.path.join(self.tmp, fn), "w", encoding="utf-8") as f:
                f.write(text)
        bid = mem.add_belief("設定ファイルのDBポートは5433", 0.5, "reflection")
        # 独立な根拠3件で支持、途中で1回判定ミス (contradicts) → 観測事実に確定
        llm.plan += [{"probe": "grep_workspace", "query": q} for q in ("DB_PORT", "5433", "待ち受け", "統一")]
        llm.judge += [{"verdict": "supports", "reason": "5433 とある"},
                      {"verdict": "contradicts", "reason": "誤読"},
                      {"verdict": "supports", "reason": "5433 とある"},
                      {"verdict": "supports", "reason": "5433 とある"}]
        for _ in range(4):
            clock.t += 2
            agent.step()
        self.assertEqual(mem.get_belief(bid).label(), FACT)

        kinds = [s["kind"] for s in agent.data.samples()]
        self.assertIn("knowledge", kinds)
        judges = agent.data.samples(kinds=("judge",))
        # 結論と矛盾した判定 (contradicts) は正解を捏造せず捨てる
        self.assertTrue(judges)
        self.assertTrue(all(json.loads(s["messages"][2]["content"])["verdict"] == "supports"
                            for s in judges))
        # 判定の入力には実際に調べた根拠が入っている
        self.assertIn("5433", judges[0]["messages"][1]["content"])

    def test_same_evidence_is_not_counted_twice(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp, max_probe_attempts=6)
        bid = mem.add_belief("設定ファイルのDBポートは5433", 0.5, "reflection")
        llm.plan += [{"probe": "grep_workspace", "query": "DB_PORT"}] * 2
        llm.judge += [{"verdict": "supports", "reason": ""}]
        agent.step()
        p1 = mem.get_belief(bid).p
        clock.t += 2
        agent.step()      # 同じ行しか見つからない → 判定を呼ばず、確信も上げない
        self.assertEqual(llm.judge, [])
        self.assertAlmostEqual(mem.get_belief(bid).p, p1, places=2)

    def test_single_judgment_resolution_is_not_trained(self):
        """判定1回で反証に確定した場合、その判定を正解として学習しない (自己追認の防止)。"""
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        mem.add_belief("設定ファイルのDBポートは5432", 0.5, "reflection")
        llm.plan.append({"probe": "grep_workspace", "query": "DB_PORT"})
        llm.judge.append({"verdict": "contradicts", "reason": "5433 とある"})
        agent.step()
        self.assertEqual(agent.data.samples(), [])

    def test_user_feedback_commands(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        sensor.name = "user"
        llm.appraise.append({"situation": "", "claims": [], "remark": "", "remark_importance": "none"})
        sensor.queue = [("user_message", "ポートは?")]
        agent.step()
        sensor.queue = [("user_message", "/bad 5433 です")]
        clock.t += 2
        agent.step()
        chats = agent.data.samples(kinds=("chat",))
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["messages"][2]["content"], "5433 です")
        self.assertEqual(chats[0]["origin"], "user_correction")
        # コマンドは出来事として記録しない (評価対象にもしない)
        self.assertFalse(any(e["content"].startswith("/") for e in mem.recent_events(50)))

    def test_bad_without_correction_is_not_trained(self):
        mem = Memory(clock=Clock())
        data = TrainingData(mem)
        data.remember_chat("sys", "質問", "間違った答え")
        data.feedback(False)
        self.assertEqual(data.samples(), [])
        self.assertEqual(len(data.samples(kinds=("rejected",))), 1)

    def test_user_answer_becomes_knowledge(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        agent.cfg["watch_dirs"] = []
        bid = mem.add_belief("このリポジトリの設定はステージング環境を向いている", 0.5, "reflection")
        llm.plan.append({"probe": "ask_user", "query": "ステージング?"})
        agent.step()
        sensor.name = "user"
        sensor.queue = [("user_message", "そうだよ")]
        llm.judge.append({"verdict": "supports", "reason": "肯定"})
        llm.appraise.append({"situation": "", "claims": [], "remark": "", "remark_importance": "none"})
        clock.t += 2
        agent.step()
        self.assertEqual(mem.get_belief(bid).label(), FACT)
        know = agent.data.samples(kinds=("knowledge",))
        self.assertEqual(know[0]["origin"], "user_answer")
        self.assertIn("ステージング", know[0]["messages"][1]["content"])

    def test_verified_examples_are_used_as_few_shot(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        agent.data.add_sample("judge", prompts.JUDGE_SYSTEM, "仮説: DB_PORT は 5433\n\n根拠: DB_PORT = 5433",
                              '{"verdict": "supports", "reason": "一致"}', "hindsight")
        b = mem.get_belief(mem.add_belief("DB_PORT は 5433", 0.5, "reflection"))
        llm.judge.append({"verdict": "supports", "reason": ""})
        agent.judge(b, "仮説: DB_PORT は 5433\n\n根拠: settings.py: DB_PORT = 5433", "observation")
        self.assertIn("過去に確かめられた例", llm.calls[-1][1])

    def test_holdout_is_never_exported_for_training(self):
        mem = Memory(clock=Clock())
        data = TrainingData(mem, holdout_ratio=0.5)
        for i in range(40):
            data.add_sample("chat", "s", f"質問{i}", f"答え{i}", "user_feedback")
        tmp = tempfile.mkdtemp()
        ids, hold = data.export(os.path.join(tmp, "t.jsonl"), os.path.join(tmp, "h.jsonl"))
        self.assertTrue(hold and ids)
        self.assertFalse(set(ids) & {h["id"] for h in hold})


class EvalLLM:
    """版ごとに判定の正しさを変える偽 Ollama。"""

    def __init__(self, good_models):
        self.gate = GpuGate(duty_cycle=1.0)
        self.model = "gemma4:e2b"
        self.good = set(good_models)
        self.unloaded = []

    def unload(self, model=None):
        self.unloaded.append(model)

    def chat(self, system, user, schema=None, model=None, **kw):
        model = model or self.model
        if schema is not None:
            if "空は青い" in user:
                return {"verdict": "supports", "reason": ""}
            return {"verdict": "supports" if model in self.good else "irrelevant", "reason": ""}
        return "はい"


def fill(data, n=30):
    for i in range(n):
        data.add_sample("judge", prompts.JUDGE_SYSTEM, f"仮説: ポート{i}\n\n根拠: PORT={i}",
                        '{"verdict": "supports", "reason": ""}', "hindsight")


class LearnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = config.load(None)
        self.cfg.update({"finetune_dir": self.tmp, "finetune_min_new_samples": 5,
                         "finetune_after_idle_s": 100, "finetune_min_holdout": 3})
        self.clock = Clock()
        self.mem = Memory(clock=self.clock)
        self.data = TrainingData(self.mem)
        self.cmds = []

    def runner(self, args, timeout, abort, on_proc):
        self.cmds.append(args)
        if "--out" in args:
            os.makedirs(args[args.index("--out") + 1], exist_ok=True)
        return 0, "ok"

    def run_pipeline(self, learner):
        self.assertTrue(learner.start("test"))
        learner._thread.join(10)
        return learner.poll()

    def test_adopts_better_model_and_switches(self):
        fill(self.data)
        llm = EvalLLM(good_models={"tachikoma-v1"})
        learner = Learner(self.cfg, self.data, llm, runner=self.runner, clock=self.clock)
        msg = self.run_pipeline(learner)
        self.assertIn("切り替えました", msg)
        self.assertEqual(llm.model, "tachikoma-v1")
        self.assertEqual(learner.active_model(), "tachikoma-v1")
        self.assertEqual(llm.unloaded, ["gemma4:e2b"])          # 学習前に VRAM を空けた
        self.assertTrue(any("train_lora.py" in a[1] for a in self.cmds if len(a) > 1))
        self.assertTrue(any(a[:2] == ["ollama", "create"] for a in self.cmds))
        with open(os.path.join(self.tmp, "tachikoma-v1", "Modelfile"), encoding="utf-8") as f:
            self.assertIn("FROM gemma4:e2b", f.read())
        self.assertEqual(self.data.count_new(), 0)

    def test_rejects_worse_model(self):
        fill(self.data)
        llm = EvalLLM(good_models={"gemma4:e2b"})
        learner = Learner(self.cfg, self.data, llm, runner=self.runner, clock=self.clock)
        msg = self.run_pipeline(learner)
        self.assertIn("採用しません", msg)
        self.assertEqual(llm.model, "gemma4:e2b")

    def test_failure_backs_off(self):
        fill(self.data)

        def failing(args, timeout, abort, on_proc):
            return 1, "ModuleNotFoundError: No module named 'torch'"
        learner = Learner(self.cfg, self.data, EvalLLM(()), runner=failing, clock=self.clock)
        msg = self.run_pipeline(learner)
        self.assertIn("失敗", msg)
        self.assertIn("torch", msg)
        self.assertFalse(learner.should_train(idle_s=10_000))
        self.clock.t += self.cfg["finetune_backoff_s"] + 1
        self.assertTrue(learner.should_train(idle_s=10_000))

    def test_should_train_needs_idle_and_new_samples(self):
        learner = Learner(self.cfg, self.data, EvalLLM(()), runner=self.runner, clock=self.clock)
        self.assertFalse(learner.should_train(idle_s=10_000))
        fill(self.data)
        self.assertFalse(learner.should_train(idle_s=10))
        self.assertTrue(learner.should_train(idle_s=10_000))

    def test_rollback(self):
        fill(self.data)
        llm = EvalLLM(good_models={"tachikoma-v1"})
        learner = Learner(self.cfg, self.data, llm, runner=self.runner, clock=self.clock)
        self.run_pipeline(learner)
        self.assertEqual(learner.rollback(), "gemma4:e2b")
        self.assertEqual(learner.active_model(), "gemma4:e2b")

    def test_user_message_aborts_training(self):
        started = threading.Event()

        def slow(args, timeout, abort, on_proc):
            started.set()
            if abort.wait(10):
                raise Aborted("中断")
            return 0, ""
        tmp = tempfile.mkdtemp()
        agent, llm, sensor, mem, clock, out = make_agent(tmp, finetune_dir=tempfile.mkdtemp())
        fill(agent.data)
        learner = Learner(agent.cfg, agent.data, EvalLLM(()), runner=slow, clock=clock)
        agent.learner = learner
        learner.start("test")
        self.assertTrue(started.wait(5))
        agent.step()                         # 学習中は背景推論しない (台本が空でも落ちない)
        sensor.name = "user"
        sensor.queue = [("user_message", "ねえ")]
        llm.appraise.append({"situation": "", "claims": [], "remark": "", "remark_importance": "none"})
        agent.step()
        self.assertFalse(learner.busy)
        self.assertTrue(any("中断" in o for o in out))
        self.assertTrue(any("了解" in o for o in out))   # 中断後にちゃんと返事をした


if __name__ == "__main__":
    unittest.main()
