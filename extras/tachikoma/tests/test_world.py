"""周辺への好奇心・先見の帳簿・仲間の危機 (ニュース → 知る → 集める → 行動する) の検証。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import curiosity  # noqa: E402
from tachikoma.bonds import Bonds  # noqa: E402
from tachikoma.kernel.fitness import window_stats  # noqa: E402
from tachikoma.kernel.foresight import Foresight, auc, related, terms  # noqa: E402
from tachikoma.kernel.guard import check_source  # noqa: E402
from tachikoma.kernel.metrics import Metrics  # noqa: E402
from tachikoma.memory import INFERENCE, SPECULATION, Memory  # noqa: E402
from tachikoma.news import NewsSensor, parse_feed  # noqa: E402
from test_core import Clock, FakeLLM, make_agent  # noqa: E402

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>OpenSSL に深刻な脆弱性</title><link>https://www3.nhk.or.jp/news/a1</link>
<description>&lt;p&gt;広く使われる暗号ライブラリ&lt;/p&gt;</description></item>
<item><title>明日の天気</title><link>https://www3.nhk.or.jp/news/a2</link><description>晴れ</description></item>
</channel></rss>""".encode("utf-8")
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Python 3.14 released</title><link href="https://blog.python.org/x"/><summary>new</summary></entry>
</feed>""".encode("utf-8")


class WorldLLM(FakeLLM):
    """仲間の危機の判定 (about_them) と行動 (actions) の台本も持つ。"""

    def __init__(self, clock):
        super().__init__(clock)
        self.trouble, self.act = [], []

    def chat(self, system, user, schema=None, **kw):
        props = (schema or {}).get("properties", {})
        if "about_them" in props:
            self.calls.append((system, user))
            return self.trouble.pop(0)
        if "actions" in props:
            self.calls.append((system, user))
            return self.act.pop(0)
        # 台本が尽きたら「何も思いつかない」を返す (長く回す検証のため)
        if "claims" in props and not self.appraise:
            return {"situation": "", "claims": [], "remark": "", "remark_importance": "none"}
        if "probe" in props and not self.plan:
            return {"probe": "wait_observe", "query": ""}
        if "verdict" in props and not self.judge:
            return {"verdict": "irrelevant", "reason": ""}
        return super().chat(system, user, schema, **kw)


class FakeNews:
    def __init__(self, results):
        self.results, self.queries, self.watching = list(results), [], {}

    def search(self, q):
        self.queries.append(q)
        return self.results.pop(0) if self.results else None

    def watch(self, name, days):
        self.watching[name] = days


def world_agent(tmp, **over):
    agent, llm, sensor, mem, clock, out = make_agent(tmp, **over)
    wl = WorldLLM(clock)
    agent.llm = wl
    return agent, wl, sensor, mem, clock, out


# ---------------------------------------------------------------- 先見の帳簿 (カーネル)
class ForesightTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.mem = Memory(":memory:", clock=self.clock)
        self.cfg = {"foresight_sync_s": 0, "foresight_horizon_s": 3 * 86400, "foresight_min_gap_s": 60,
                    "foresight_explore": 0.5}
        self.fs = Foresight(self.cfg, self.mem.db, clock=self.clock)

    def test_terms_and_relation(self):
        self.assertIn("openssl", terms("OpenSSL に深刻な脆弱性"))
        self.assertIn("脆弱性", terms("OpenSSL に深刻な脆弱性"))
        self.assertTrue(related(terms("OpenSSL に深刻な脆弱性"), terms("openssl を更新しよう")))
        self.assertFalse(related(terms("明日の天気は晴れ"), terms("openssl を更新しよう")))

    def test_later_user_action_marks_news_as_useful(self):
        self.mem.add_event("news", "news", "OpenSSL に深刻な脆弱性", 1.0, priority=0.2)
        self.mem.add_event("news", "news", "明日の天気は晴れ", 1.0, priority=0.9)
        self.fs.sync(force=True)
        self.clock.t += 3600
        self.mem.add_event("user", "user_message", "openssl のバージョンを上げたい", 1.0)
        self.fs.sync(force=True)
        used = dict(self.mem.db.execute(
            "SELECT e.content, i.used_ts IS NOT NULL FROM info_items i JOIN events e ON e.id=i.event_id").fetchall())
        self.assertEqual(used, {"OpenSSL に深刻な脆弱性": 1, "明日の天気は晴れ": 0})   # 話しかけ自体は載せない

    def test_useful_topics_become_priority_seeds(self):
        for i in range(3):
            self.mem.add_event("news", "news", f"OpenSSL 第{i}報", 1.0)
            self.mem.add_event("news", "news", f"明日の天気 第{i}報", 1.0)
        self.fs.sync(force=True)
        self.clock.t += 3600
        self.mem.add_event("files", "file_changed", "requirements.txt を編集 openssl を更新", 1.0)
        self.fs.sync(force=True)
        self.clock.t += 4 * 86400           # 天気の話は期限まで使われなかった
        # 同じ出どころでも、役立った話題の情報は優先度が上がる
        self.assertGreater(self.fs.usefulness("news/news", "OpenSSL の続報です"),
                           self.fs.usefulness("news/news", "明日の天気は雨"))

    def test_action_right_after_seeing_is_not_foresight(self):
        self.mem.add_event("window", "window_title", "OpenSSL 脆弱性 - ブラウザ", 1.0)
        self.clock.t += 5
        self.mem.add_event("user", "user_message", "openssl 脆弱性って何", 1.0)
        self.fs.sync(force=True)
        n = self.mem.db.execute("SELECT COUNT(*) FROM info_items WHERE used_ts IS NOT NULL").fetchone()[0]
        self.assertEqual(n, 0)

    def test_unknown_source_is_optimistic_and_bad_source_fades(self):
        fresh = self.fs.usefulness("camera/scene")
        for i in range(40):
            self.mem.add_event("window", "window_title", f"無関係な画面 {i} 番", 1.0)
        self.fs.sync(force=True)
        self.clock.t += 4 * 86400           # 期限まで使われなかった = 役立たなかった
        self.assertGreater(fresh, self.fs.usefulness("window/window_title"))

    def test_auc(self):
        self.assertEqual(auc([(0.9, True)] * 3 + [(0.1, False)] * 3), 1.0)
        self.assertEqual(auc([(0.1, True)] * 3 + [(0.9, False)] * 3), 0.0)
        self.assertIsNone(auc([(0.9, True), (0.1, False)]))

    def test_window_stats_reports_foresight(self):
        for i in range(40):
            useful = i % 4 == 0
            self.mem.add_event("news", "news", f"話題{i}号 クラウド障害" if useful else f"芸能{i}号", 1.0,
                               priority=0.9 if useful else 0.1)
        self.fs.sync(force=True)
        self.clock.t += 3600
        self.mem.add_event("user", "user_message", "クラウド障害の影響を調べて", 1.0)
        self.fs.sync(force=True)
        self.clock.t += 12 * 3600
        s = window_stats(Metrics(self.mem.db), self.mem.db, self.clock.t - 13 * 3600 - 10, self.clock.t)
        self.assertEqual(s["foresight_auc"], 1.0)

    def test_thinking_code_cannot_touch_the_ledger(self):
        self.assertTrue(check_source("tachikoma/agent.py", "x = 'UPDATE info_items SET used_ts=1'\n"))
        self.assertTrue(check_source("tachikoma/agent.py", "from .kernel import foresight\n"))
        self.assertTrue(check_source("tachikoma/agent.py", "from .kernel.foresight import Foresight\n"))
        for f in ("bonds.py", "concern.py", "curiosity.py", "agent.py", "memory.py"):
            path = os.path.join(os.path.dirname(__file__), "..", "tachikoma", f)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(check_source("tachikoma/" + f, fh.read()), [], f)


# ---------------------------------------------------------------- ニュース
class NewsTest(unittest.TestCase):
    def test_parse_rss_and_atom(self):
        items = parse_feed(RSS)
        self.assertEqual([i["title"] for i in items], ["OpenSSL に深刻な脆弱性", "明日の天気"])
        self.assertEqual(items[0]["summary"], "広く使われる暗号ライブラリ")
        self.assertEqual(parse_feed(ATOM)[0]["link"], "https://blog.python.org/x")
        self.assertEqual(parse_feed(b"<not xml"), [])

    def test_sensor_reports_each_headline_once(self):
        mem = Memory(":memory:")

        class Feed:
            def fetch(self, url):
                return parse_feed(RSS)

            def watched(self):
                return []
        cfg = {"news_feeds": ["https://www3.nhk.or.jp/rss/news/cat0.xml"], "news_interval_s": 1800,
               "news_search_url": ""}
        s = NewsSensor(cfg, Feed(), mem.db, clock=Clock())
        s._next = float("inf")               # スレッドを使わずに取得する
        s._fetch_all()
        got = s.poll()
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][0], "news")
        self.assertEqual(got[0][2]["source"], "nhk.or.jp")     # www3. も同じ報道機関として数える
        s._fetch_all()
        self.assertEqual(s.poll(), [])


# ---------------------------------------------------------------- 仲間
class BondsTest(unittest.TestCase):
    def test_bonds_grow_with_interaction(self):
        clock = Clock()
        b = Bonds(Memory(":memory:", clock=clock).db, clock)
        b.mention("田中さん", "person", "user_message")
        self.assertEqual(b.find_in("田中さんが入院", 0.3), [])     # 1 回ではまだ仲間ではない
        b.mention("田中さん", "person", "user_message")
        self.assertEqual(len(b.find_in("田中さんが入院", 0.3)), 1)
        self.assertIsNone(b.mention("ユーザー", "person"))           # 一般名詞は仲間にしない
        b.befriend("SSL", "software")
        self.assertEqual(b.find_in("OpenSSL に脆弱性", 0.3), [])   # 英字は語の境界で照合
        self.assertEqual(len(b.find_in("SSL の証明書", 0.3)), 1)

    def test_tell_a_companion_by_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, llm, sensor, mem, clock, out = world_agent(tmp)
            agent.on_user_message("田中さんは仲間だよ", voice=True)
            self.assertEqual(len(agent.bonds.find_in("田中さんが表彰された", 0.3)), 1)
            self.assertIn("仲間！覚えたよ", out[-1])

    def test_only_shared_experience_makes_companions(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, llm, sensor, mem, clock, out = world_agent(tmp)
            ent = {"situation": "", "claims": [], "remark": "", "remark_importance": "none",
                   "entities": [{"name": "Rust", "kind": "software"}]}
            llm.appraise += [dict(ent), dict(ent)]
            agent.perceive("news", "news", "Rust の新しい版が出た")
            agent.appraise_next()
            self.assertIsNone(agent.bonds.get("Rust"))              # ニュースに出ただけの名前は仲間にしない
            agent.perceive("terminal", "terminal_output", "cargo build (Rust) 成功")
            agent.appraise_next()
            self.assertIsNotNone(agent.bonds.get("Rust"))


# ---------------------------------------------------------------- 仲間の危機
class ConcernTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        with open(os.path.join(self.tmp, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write("requests\nopenssl-python==1.0\n")

    def tearDown(self):
        self._tmp.cleanup()

    def news_agent(self, results):
        agent, llm, sensor, mem, clock, out = world_agent(self.tmp)
        sensor.name = "news"
        agent.probes.news = FakeNews(results)
        agent.on_command("/friend OpenSSL software")
        return agent, llm, sensor, mem, clock, out

    def test_news_to_action(self):
        """ニュースを見る → 仲間のピンチを知る → 情報を集める → 行動する。"""
        agent, llm, sensor, mem, clock, out = self.news_agent(
            ["- OpenSSL 脆弱性の修正版が公開: 更新を推奨 (https://www.security-next.com/1)"])
        sensor.queue.append(("news", "OpenSSL に深刻な脆弱性 — 広く使われる暗号ライブラリ",
                             {"link": "https://www3.nhk.or.jp/news/a1", "source": "nhk.or.jp"}))
        llm.trouble.append({"about_them": True, "trouble": "security", "summary": "OpenSSLに深刻な脆弱性が見つかった",
                            "urgent": False})
        llm.judge.append({"verdict": "supports", "reason": "別の媒体も同じ脆弱性を報じている"})
        llm.act.append({"summary": "OpenSSLに深刻な脆弱性。別の媒体でも確認できた",
                        "actions": [{"type": "check_workspace", "detail": "openssl"},
                                    {"type": "keep_watching", "detail": ""},
                                    {"type": "suggest", "detail": "使っているライブラリを最新版に更新する"}]})
        agent.step()                                   # 知る: 名前が出た → 判定 → 仮説
        c = agent.concerns.recent()[0]
        self.assertEqual(c["state"], "investigating")
        b = mem.get_belief(c["belief_id"])
        self.assertEqual(b.label(), SPECULATION)       # 1 本の報道だけでは確信しない
        agent.step()                                   # 集める: 別の出どころで裏付け
        self.assertIn("OpenSSL", agent.probes.news.queries[0])
        self.assertEqual(mem.get_belief(c["belief_id"]).label(), INFERENCE)
        agent.step()                                   # 動く
        c = agent.concerns.recent()[0]
        self.assertEqual(c["state"], "reported")
        self.assertEqual(agent.probes.news.watching, {"OpenSSL": agent.cfg["concern_watch_days"]})
        said = "\n".join(out)
        self.assertIn("ねえねえ！ニュースで見たんだけど", said)
        self.assertIn("requirements.txt", said)       # 作業フォルダで使っている箇所を見つけた
        self.assertIn("[合理的推定]", said)
        self.assertNotIn("まだ確かめきれてない", said)
        self.assertNotIn("確かめた:", said)             # 一般の「確かめた」報告と二重にしない

    def test_unconfirmed_is_reported_as_unconfirmed(self):
        agent, llm, sensor, mem, clock, out = self.news_agent([])
        agent.perceive("voice", "overheard_speech", "テレビ: OpenSSL の開発団体で障害が起きているようです")
        llm.trouble.append({"about_them": True, "trouble": "outage", "summary": "OpenSSLの開発団体で障害",
                            "urgent": False})
        llm.act.append({"summary": "テレビで障害と言っていたが、裏付けは見つからなかった", "actions": []})
        for _ in range(6):
            agent.step()
        said = "\n".join(out)
        self.assertIn("まだ確かめきれてない", said)
        self.assertIn("[低確度仮説]", said)

    def test_not_about_them_is_ignored(self):
        agent, llm, sensor, mem, clock, out = self.news_agent([])
        agent.perceive("news", "news", "OpenSSL 財団が新しい理事を発表")
        llm.trouble.append({"about_them": True, "trouble": "none", "summary": "", "urgent": False})
        agent.step()
        self.assertEqual(agent.concerns.recent(), [])

    def test_refuted_concern_is_withdrawn(self):
        agent, llm, sensor, mem, clock, out = self.news_agent(
            ["- OpenSSL: 報道は誤りと開発者が否定 (https://example.org/x)"] * 3)
        agent.perceive("news", "news", "OpenSSL 開発終了か", {"link": "https://a.example/1", "source": "a.example"})
        llm.trouble.append({"about_them": True, "trouble": "other", "summary": "OpenSSLの開発が終了する",
                            "urgent": True})
        llm.judge += [{"verdict": "contradicts", "reason": "開発者が否定"}] * 3
        for _ in range(5):
            agent.step()
        c = agent.concerns.recent()[0]
        self.assertEqual(c["state"], "dismissed")
        said = "\n".join(out)
        self.assertIn("いま調べてるね", said)          # 急ぎの知らせは先に一言
        self.assertIn("違ったみたい", said)             # 言ったことは撤回まで責任を持つ

    def test_report_waits_for_the_partner(self):
        agent, llm, sensor, mem, clock, out = self.news_agent([])
        mem.add_utterance("concern", "ねえねえ！ニュースで見たんだけど…", 0.95)
        mem.add_utterance("remark", "ふつうの気づき", 0.95)
        clock.t += 3600
        agent.maybe_speak()
        said = "\n".join(out)
        self.assertIn("ねえねえ", said)
        self.assertNotIn("ふつうの気づき", said)


# ---------------------------------------------------------------- 周辺への好奇心
class PeripheralTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_sometimes_looks_at_low_priority_events(self):
        agent, llm, sensor, mem, clock, out = world_agent(self.tmp, peripheral_share=1.0)
        agent._rand = lambda: 0.0
        agent.usefulness = lambda bucket, text=None: 1.0 if bucket.startswith("terminal") else 0.0
        agent.perceive("terminal", "terminal_output", "ビルドが失敗した")
        agent.perceive("window", "window_title", "天気予報 - ブラウザ")
        pend = mem.pending_events()
        self.assertEqual(pend[0]["source"], "terminal")         # 優先度: 役立ちそうな出どころが先
        llm.appraise.append({"situation": "", "claims": [{"statement": "ユーザーは週末の天気を気にしている",
                                                          "basis": "guessed"}],
                             "remark": "", "remark_importance": "none"})
        agent.appraise_next()
        b = [x for x in mem.beliefs() if "天気" in x.statement][0]
        self.assertEqual(b.origin, "window/window_title")
        self.assertAlmostEqual(b.relevance, agent.cfg["peripheral_relevance"])

    def test_useful_origins_pull_attention(self):
        agent, llm, sensor, mem, clock, out = world_agent(self.tmp)
        bid = mem.add_belief("この曲は作業用BGMだ", 0.5, "reflection", relevance=0.01, origin="voice/overheard_speech")
        b = mem.get_belief(bid)
        low = curiosity.uncertainty(b, 3)
        high = curiosity.uncertainty(b, 3, usefulness=lambda o: 1.0, peripheral=0.3)
        self.assertLess(low, 0.05)
        self.assertGreater(high, 0.25)

    def test_resolving_a_question_raises_the_next(self):
        agent, llm, sensor, mem, clock, out = world_agent(self.tmp)
        bid = mem.add_belief("隣の工事は今週で終わる", 0.5, "reflection", origin="voice/overheard_speech")
        b = mem.get_belief(bid)
        agent.apply_verdict(b, {"verdict": "supports", "reason": "掲示"}, "observation", 1.0, "look: 掲示")
        llm.wonder.append({"hypotheses": ["工事が終わると昼間の騒音が減り、相棒の集中が上がる"]})
        clock.t += agent.cfg["wonder_followup_s"] + 1       # 通常の間隔 (15 分) を待たずに
        self.assertTrue(agent.wonder())
        self.assertIn("隣の工事", llm.calls[-1][1].splitlines()[0])
        q = [x for x in mem.beliefs() if "騒音" in x.statement][0]
        self.assertEqual(q.origin, "voice/overheard_speech")  # 周辺から生まれた疑問は、周辺の成績に数える


if __name__ == "__main__":
    unittest.main()
