"""性格の芯 (知ったかぶりしない・前提を疑う・疑問を作る・根拠と因果・自分を疑う) と
タチコマの人格、論文・政府文書の検索の検証。"""

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import config  # noqa: E402
from tachikoma.epistemics import Calibration, classify_text, claim_type  # noqa: E402
from tachikoma.memory import FACT, INFERENCE, REFUTED, Memory  # noqa: E402
from tachikoma.persona import SelfModel  # noqa: E402
from tachikoma.probes import Probes  # noqa: E402
from tachikoma.scholar import Evidence, Scholar  # noqa: E402
from test_core import Clock, make_agent  # noqa: E402


class EpistemicsTest(unittest.TestCase):
    def test_claim_type(self):
        self.assertEqual(claim_type("睡眠不足が記憶力を低下させる原因になる"), "causal")
        self.assertEqual(claim_type("運動は死亡率を減らす"), "causal")
        self.assertEqual(claim_type("設定ファイルのポートは5433"), "descriptive")

    def test_classify_evidence(self):
        self.assertEqual(classify_text("Exercise and mortality", pubtypes=["Meta-Analysis"]), "meta_analysis")
        self.assertEqual(classify_text("A randomized controlled trial of X"), "rct")
        self.assertEqual(classify_text("令和5年 人口動態統計", url="https://www.mhlw.go.jp/toukei/"), "government")
        self.assertEqual(classify_text("Diet and cancer: a prospective cohort study", venue_type="journal"),
                         "observational")
        self.assertEqual(classify_text("Attention is all you need", url="https://arxiv.org/abs/1706"), "preprint")

    def test_causal_claims_need_causal_designs(self):
        mem = Memory(clock=Clock())
        bid = mem.add_belief("コーヒーを飲むと寿命が延びる効果がある", 0.5, "reflection")
        for _ in range(5):   # 観察研究 (相関) の支持をいくら重ねても
            b = mem.update_belief(bid, 3.0, "research")
        self.assertLessEqual(b.p, 0.8)
        self.assertNotEqual(b.label(), FACT)
        b = mem.update_belief(bid, 3.0, "research", causal_evidence=True)   # RCT/メタ分析が来たら
        self.assertGreater(b.p, 0.85)

    def test_calibration_shrinks_overconfidence(self):
        mem = Memory(clock=Clock())
        cal = Calibration(mem)
        self.assertEqual(cal.shrink(), 1.0)        # データが無いうちは縮めない
        for i in range(12):   # 0.8 の自信で予測したのに半分しか当たらない
            cal.predict(i, 0.8, "inferred")
            cal.resolve(i, i % 2)
        s = cal.stats()
        self.assertAlmostEqual(s["overconfidence"], 0.3, places=2)
        self.assertLess(cal.shrink(), 0.8)


class MindAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_does_not_pretend_and_reports_back(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        sensor.name = "user"
        llm.inquiry.append({"premises": [{"statement": "量子コンピュータはすでに暗号を全部破れる", "doubtful": True}],
                            "unknowns": ["RSA-2048 は量子計算機でまだ破られていない"], "answerable": False})
        llm.appraise.append({"situation": "", "claims": [], "remark": "", "remark_importance": "none"})
        sensor.queue = [("user_message", "量子コンピュータで暗号が全部破れる今、どうする?")]
        agent.step()
        reply_prompt = llm.calls[-1][1] if "点検" in llm.calls[-1][1] else [c[1] for c in llm.calls if "点検" in c[1]][0]
        self.assertIn("怪しい前提", reply_prompt)
        self.assertIn("まだ知らないこと", reply_prompt)
        unknown = [b for b in mem.beliefs() if "RSA" in b.statement][0]
        self.assertEqual(unknown.promised, 1)

        # 好奇心がその未知を論文で確かめる → 自分から報告する
        class FakeScholar:
            def search(self, q):
                return [Evidence("Quantum resource estimates for RSA-2048", "estimates...", "https://doi.org/x",
                                 2025, "peer_reviewed", 50)]
        agent.probes.scholar = FakeScholar()
        # 1回目: 怪しい前提を調べる → 反証 / 2回目: 知らなかったことを調べる → 支持
        for q in ("quantum computer break all encryption", "RSA-2048 quantum resource estimate"):
            llm.plan.append({"probe": "research", "query": q})
        llm.judge += [{"verdict": "contradicts", "reason": "全部は破れない"},
                      {"verdict": "supports", "reason": "まだ必要な量子ビット数に届いていない"}]
        for _ in range(2):
            clock.t += 2
            agent.step()
        premise = [b for b in mem.beliefs() if "全部破れる" in b.statement][0]
        self.assertLess(premise.p, 0.3)                 # 前提を疑って、確かめて、崩した
        self.assertTrue(any("さっきわからなかった" in o and "調べたよ" in o for o in out), out)

    def test_research_evidence_strength_unlocks_causal_claims(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)

        class FakeScholar:
            def search(self, q):
                return [Evidence("Physical activity and all-cause mortality: a meta-analysis", "...",
                                 "https://doi.org/y", 2022, "meta_analysis", 900)]
        agent.probes.scholar = FakeScholar()
        bid = mem.add_belief("運動は死亡リスクを減らす効果がある", 0.5, "reflection")
        llm.plan += [{"probe": "research", "query": "physical activity mortality meta-analysis"}]
        llm.judge += [{"verdict": "supports", "reason": "メタ分析で一貫して低下"}]
        agent.step()
        b = mem.get_belief(bid)
        self.assertEqual(b.causal_ok, 1)
        self.assertEqual(b.source, "research")
        self.assertIn("メタ分析", agent.context_text())

    def test_challenges_own_confident_inference(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp, challenge_share=1.0)
        agent.probes.web = object()     # research を使える状態にする

        class FakeScholar:
            def search(self, q):
                return [Evidence("No effect of X on Y: a randomized controlled trial", "...",
                                 "https://doi.org/z", 2021, "rct", 30)]
        agent.probes.scholar = FakeScholar()
        bid = mem.add_belief("X は Y を改善する効果がある", 0.78, "research")
        self.assertEqual(mem.get_belief(bid).label(), INFERENCE)
        llm.plan.append({"probe": "challenge", "query": "X Y effect"})
        llm.judge.append({"verdict": "contradicts", "reason": "RCT で効果なし"})
        agent.step()
        b = mem.get_belief(bid)
        self.assertEqual(b.challenged, 1)
        self.assertLess(b.p, 0.5)

    def test_wonder_creates_questions(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        mem.add_belief("ユーザーは毎晩2時に寝ている", 0.95, "user")
        llm.wonder.append({"hypotheses": ["ユーザーの寝る時間が遅いのは仕事が夜型だからだ"]})
        clock.t += agent.cfg["wonder_interval_s"] + 1
        agent.wonder()
        q = [b for b in mem.beliefs() if "夜型" in b.statement]
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0].claim_type, "causal")
        self.assertEqual(agent.selfm.get("counters")["questions"], 1)

    def test_diary_and_self(self):
        agent, llm, sensor, mem, clock, out = make_agent(self.tmp)
        bid = agent._hypothesis("ユーザーは猫を飼っている", 0.8, 1.0, "guessed")
        agent.calib.resolve(bid, 0)
        agent.selfm.learned("量子コンピュータの誤り訂正", 0.9)
        agent.write_diary()
        self.assertTrue(any(c[0].startswith("あなたは相棒AI") for c in llm.calls))
        agent.on_command("/diary")
        agent.on_command("/self")
        self.assertIn("量子コンピュータ", out[-1])
        self.assertIn("生まれて", out[-1])


class PersonaTest(unittest.TestCase):
    def test_prompt_grows_with_experience(self):
        mem = Memory(clock=Clock())
        sm = SelfModel(mem)
        cfg = config.load(None)
        cfg["user_name"] = "マスター"
        p = sm.system_prompt(cfg)
        self.assertIn("ボク", p)
        self.assertIn("マスター", p)
        self.assertIn("知ったかぶりをしない", p)
        sm.learned("ガウス過程の回帰", 1.0)
        sm.remember("初めて名前を呼ばれた")
        p = sm.system_prompt(cfg, voice=True, calibration={"n": 20, "overconfidence": 0.25, "brier": 0.3})
        self.assertIn("ガウス過程", p)
        self.assertIn("初めて名前を呼ばれた", p)
        self.assertIn("確信過剰", p)
        self.assertIn("声で話す", p)


class ScholarTest(unittest.TestCase):
    def opener(self, req, timeout):
        url = req.full_url
        if "openalex" in url:
            body = {"results": [{"title": "Sleep and memory: a systematic review and meta-analysis",
                                 "publication_year": 2020, "type": "article", "cited_by_count": 120,
                                 "doi": "https://doi.org/1", "abstract_inverted_index": {"We": [0], "review": [1]},
                                 "primary_location": {"source": {"type": "journal", "display_name": "J"}}}]}
        elif "esearch" in url:
            body = {"esearchresult": {"idlist": ["42"]}}
        elif "esummary" in url:
            body = {"result": {"42": {"title": "Trial of X", "pubtype": ["Randomized Controlled Trial"],
                                      "fulljournalname": "NEJM", "pubdate": "2019 Jan"}}}
        elif "arxiv" in url:
            return io.BytesIO(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New idea</title>'
                              b'<summary>We propose</summary><id>http://arxiv.org/abs/1</id>'
                              b'<published>2024-01-01</published></entry></feed>')
        else:
            body = {"results": [{"title": "睡眠の統計", "content": "...", "url": "https://www.mhlw.go.jp/x"}]}
        return io.BytesIO(json.dumps(body).encode())

    def test_search_ranks_strong_evidence_first(self):
        cfg = config.load(None)
        cfg["searxng_url"] = "http://127.0.0.1:8080"
        found = Scholar(cfg, opener=self.opener).search("sleep memory", k=10)
        kinds = [e.kind for e in found]
        self.assertEqual(kinds[0], "meta_analysis")
        self.assertIn("rct", kinds)
        self.assertIn("government", kinds)
        self.assertEqual(kinds[-1], "preprint")
        self.assertIn("メタ分析", found[0].cite())

    def test_private_queries_are_not_sent(self):
        calls = []
        s = Scholar(config.load(None), opener=lambda req, timeout: calls.append(req) or io.BytesIO(b"{}"))
        self.assertEqual(s.search("C:\\Users\\me\\diary.txt の内容"), [])
        self.assertEqual(calls, [])

    def test_probe_returns_grade(self):
        cfg = config.load(None)
        p = Probes(cfg, Memory(clock=Clock()), scholar=Scholar(cfg, opener=self.opener))
        text, meta = p.run("research", "sleep memory")
        self.assertTrue(meta["causal_design"])
        self.assertGreaterEqual(meta["reliability"], 0.9)
        self.assertIn("メタ分析", text)


if __name__ == "__main__":
    unittest.main()
