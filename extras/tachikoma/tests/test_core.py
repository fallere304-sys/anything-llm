"""Ollama なしで思考ループを検証する。実行: python -m unittest discover -s tests"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import config, curiosity  # noqa: E402
from tachikoma.agent import Tachikoma  # noqa: E402
from tachikoma.llm import GpuGate  # noqa: E402
from tachikoma.memory import FACT, INFERENCE, SPECULATION, Memory, entropy  # noqa: E402
from tachikoma.probes import Probes  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeLLM:
    """スキーマの種類で応答を出し分ける台本付き LLM。"""

    def __init__(self, clock):
        self.gate = GpuGate(duty_cycle=1.0, clock=clock)
        self.appraise, self.plan, self.judge = [], [], []
        self.calls = []

    def chat(self, system, user, schema=None, **kw):
        self.calls.append((system, user))
        props = (schema or {}).get("properties", {})
        if "claims" in props:
            return self.appraise.pop(0)
        if "probe" in props:
            return self.plan.pop(0)
        if "verdict" in props:
            return self.judge.pop(0)
        return "了解。"


class ScriptedSensor:
    name = "terminal"

    def __init__(self):
        self.queue = []

    def poll(self):
        out, self.queue = self.queue, []
        return out


def make_agent(tmp, **over):
    cfg = config.load(None)
    cfg.update({"watch_dirs": [tmp], "min_speak_interval_s": 0, "busy_idle_s": 0}, **over)
    clock = Clock()
    mem = Memory(":memory:", clock=clock)
    llm = FakeLLM(clock)
    sensor = ScriptedSensor()
    out = []
    agent = Tachikoma(cfg, llm, mem, [sensor], Probes(cfg, mem), out=out.append,
                      clock=clock, idle_fn=lambda: 60)
    return agent, llm, sensor, mem, clock, out


class BeliefMath(unittest.TestCase):
    def test_entropy_peaks_at_half(self):
        self.assertAlmostEqual(entropy(0.5), 1.0)
        self.assertLess(entropy(0.95), 0.3)

    def test_reflection_cannot_reach_fact(self):
        clock = Clock()
        mem = Memory(clock=clock)
        bid = mem.add_belief("ユーザーは疲れている", 0.5, "reflection")
        for _ in range(10):
            b = mem.update_belief(bid, 3.0, "reflection")
        self.assertLessEqual(b.p, 0.75)
        self.assertEqual(b.label(), INFERENCE)

    def test_observation_becomes_fact(self):
        clock = Clock()
        mem = Memory(clock=clock)
        bid = mem.add_belief("app.py に main 関数がある", 0.5, "reflection")
        b = mem.update_belief(bid, 3.0, "observation")
        self.assertEqual(b.label(), FACT)

    def test_confidence_decays_with_time(self):
        clock = Clock()
        mem = Memory(clock=clock)
        bid = mem.add_belief("ユーザーはテストを書いている", 0.95, "observation", half_life=3600)
        self.assertEqual(mem.get_belief(bid).label(), FACT)
        clock.t += 4 * 3600
        self.assertEqual(mem.get_belief(bid).label(), SPECULATION)

    def test_similar_beliefs_are_merged(self):
        mem = Memory(clock=Clock())
        a = mem.add_belief("ユーザーはPostgreSQLに接続しようとしている", 0.5, "reflection")
        b = mem.add_belief("ユーザーはPostgreSQLに接続しようとしている。", 0.65, "reflection")
        self.assertEqual(a, b)
        self.assertEqual(len(mem.beliefs()), 1)

    def test_irreducible_beliefs_do_not_drive_curiosity(self):
        mem = Memory(clock=Clock())
        bid = mem.add_belief("明日は雨", 0.5, "reflection")
        self.assertGreater(curiosity.drive(mem.beliefs(), 3), 0.9)
        mem.mark_irreducible(bid)
        self.assertEqual(curiosity.drive(mem.beliefs(), 3), 0.0)


class GateTest(unittest.TestCase):
    def test_duty_cycle_limits_background(self):
        clock = Clock(0.0)
        gate = GpuGate(duty_cycle=0.5, window=60, clock=clock)

        def work():
            clock.t += 40
        gate.run(work)
        self.assertFalse(gate.can_run_background())
        clock.t += 60
        self.assertTrue(gate.can_run_background())


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "settings.py"), "w", encoding="utf-8") as f:
            f.write("DB_HOST = 'localhost'\nDB_PORT = 5433\n")

    def test_error_to_hypothesis_to_verified_finding(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)

        # 1) 知覚 → 評価: エラーを見て、原因の仮説 (低確度) を立てる
        sensor.queue = [("terminal_output", "psycopg2.OperationalError: connection refused port 5432")]
        llm.appraise.append({
            "situation": "DB 接続エラーを調べている",
            "claims": [
                {"statement": "connection refused port 5432 が出ている", "basis": "observed"},
                {"statement": "設定ファイルのポートが5432ではない", "basis": "guessed"},
            ],
            "remark": "", "remark_importance": "none"})
        agent.step()
        hyp = [b for b in mem.beliefs() if "設定ファイル" in b.statement][0]
        obs = [b for b in mem.beliefs() if "refused" in b.statement][0]
        self.assertEqual(hyp.label(), SPECULATION)
        self.assertEqual(obs.label(), FACT)

        # 2) 好奇心: 最も不確実な仮説を、ワークスペース検索で確かめる
        llm.plan.append({"probe": "grep_workspace", "query": "DB_PORT"})
        llm.judge.append({"verdict": "supports", "reason": "settings.py の DB_PORT が 5433"})
        clock.t += 2
        agent.step()
        hyp = mem.get_belief(hyp.id)
        # grep 1件の支持では「合理的推定」止まり (reliability 0.8)。事実扱いにはしない
        self.assertEqual(hyp.label(), INFERENCE)
        self.assertEqual(hyp.source, "observation")
        self.assertIn("5433", llm.calls[-1][1])   # 判定に実際のファイル内容が渡っている

        # 3) 発話: 確認できた発見を伝える
        self.assertTrue(any("確かめた" in o and "設定ファイル" in o for o in out))

    def test_hallucinated_observation_is_downgraded(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        sensor.queue = [("terminal_output", "npm ERR! missing script: start")]
        llm.appraise.append({
            "situation": "npm を実行",
            "claims": [{"statement": "ユーザーは締め切りに追われている", "basis": "observed"}],
            "remark": "", "remark_importance": "none"})
        agent.step()
        b = mem.beliefs()[0]
        self.assertEqual(b.source, "reflection")
        self.assertNotEqual(b.label(), FACT)

    def test_fruitless_probing_is_given_up(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp, allow_ask_user=False)
        mem.add_belief("ユーザーは来週引っ越す", 0.5, "reflection")
        for _ in range(3):
            llm.plan.append({"probe": "grep_workspace", "query": "引っ越し"})
            clock.t += 2
            agent.step()
        self.assertEqual(mem.beliefs()[0].irreducible, 1)
        clock.t += 2
        agent.step()   # もう調べない (plan を消費しない)
        self.assertEqual(llm.plan, [])

    def test_question_and_answer_updates_belief(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        cfg_dirs = agent.cfg["watch_dirs"]
        agent.cfg["watch_dirs"] = []   # 聞くしかない状況
        bid = mem.add_belief("ユーザーは本番環境を触っている", 0.5, "reflection")
        llm.plan.append({"probe": "ask_user", "query": "いま触ってるのは本番環境?"})
        agent.step()
        self.assertTrue(any("❓" in o for o in out))
        self.assertEqual(agent.asked, bid)
        clock.t += 2
        agent.step()   # 回答待ちの間は同じ仮説を調べ直さない (plan が無いので呼べば落ちる)

        llm.judge.append({"verdict": "contradicts", "reason": "ステージングと回答"})
        llm.appraise.append({"situation": "ステージングで作業中",
                             "claims": [], "remark": "", "remark_importance": "none"})
        sensor.queue = [("user_message", "いや、ステージングだよ")]
        sensor.name = "user"
        clock.t += 2
        agent.step()
        self.assertLess(mem.get_belief(bid).p, 0.1)
        self.assertIsNone(agent.asked)
        agent.cfg["watch_dirs"] = cfg_dirs

    def test_own_utterances_are_not_evidence(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        mem.add_event("self", "question", "いまタチコマを試してる?")
        self.assertIsNone(agent.probes.run("search_memory", "タチコマを試してる"))

    def test_busy_user_is_not_interrupted_by_minor_remarks(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp, busy_idle_s=5)
        agent.idle_fn = lambda: 1   # タイピング中
        mem.add_utterance("remark", "ちょっとした気づき", 0.7)
        agent.maybe_speak()
        self.assertEqual(out, [])
        agent.idle_fn = lambda: 30
        agent.maybe_speak()
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
