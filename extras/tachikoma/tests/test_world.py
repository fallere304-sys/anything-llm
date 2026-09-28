"""視界の端への興味・深掘り・先見の帳簿・自発性の選択圧・プラグインの検証。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tachikoma import curiosity, prompts  # noqa: E402
from tachikoma.kernel.fitness import window_stats  # noqa: E402
from tachikoma.kernel.foresight import Foresight, auc, related, terms  # noqa: E402
from tachikoma.kernel.evolve import GOALS  # noqa: E402
from tachikoma.kernel.guard import check_source  # noqa: E402
from tachikoma.kernel.initiative import Initiative  # noqa: E402
from tachikoma.kernel.plugins import PluginHost  # noqa: E402
from tachikoma.kernel.metrics import Metrics  # noqa: E402
from tachikoma.memory import Memory  # noqa: E402
from tachikoma.news import NewsSensor, parse_feed  # noqa: E402
from tachikoma.sensors import BackgroundWindowsSensor  # noqa: E402
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
    """深掘りの振り返り (matters) と行動 (actions) の台本も持つ。"""

    def __init__(self, clock):
        super().__init__(clock)
        self.matters, self.act = [], []

    def chat(self, system, user, schema=None, **kw):
        props = (schema or {}).get("properties", {})
        if "matters" in props:
            self.calls.append((system, user))
            return self.matters.pop(0) if self.matters else {"matters": False, "to_whom": "", "why": "",
                                                              "urgent": False, "next_question": ""}
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
        for f in ("inquiry.py", "curiosity.py", "agent.py", "memory.py"):
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


class InquiryTest(unittest.TestCase):
    """視界の端の情報に疑問を持つ → 掘る → わかったことから次の疑問 → 収穫が減ったら見切る。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def dig_agent(self, results, **over):
        # 一般の好奇心は止めて、深掘りの糸だけを見る
        agent, llm, sensor, mem, clock, out = world_agent(self.tmp, curiosity_threshold=1.1, **over)
        agent.probes.news = FakeNews(results)
        return agent, llm, sensor, mem, clock, out

    def appraisal(self, question):
        return {"situation": "", "claims": [], "remark": "", "remark_importance": "none", "question": question}

    def test_starts_naive(self):
        """出発点のタチコマは、掘って知ったことを誰かと結びつけたり、それをもとに動いたりはしない。"""
        agent, llm, sensor, mem, clock, out = self.dig_agent(
            ["- 北海道で大規模停電 札幌市でも約80万戸 (https://www.hokkaido-np.co.jp/a/1)"])
        mem.add_belief("田中さんは札幌に住んでいる", 0.95, "user")
        agent.perceive("voice", "overheard_speech", "テレビ: 北海道で大規模な停電が発生、復旧のめどは立っていません")
        llm.appraise.append(self.appraisal("停電は札幌市内にも及んでいる"))
        llm.plan += [{"probe": "news_search", "query": "北海道 停電 札幌"}]
        llm.judge += [{"verdict": "supports", "reason": "札幌も停電と報道"}]
        for _ in range(12):
            agent.step()
        th = agent.inquiry.recent()[0]
        self.assertGreaterEqual(th["steps"], 1)                          # 視界の端に興味を持って掘った
        b = [x for x in mem.beliefs() if "停電" in x.statement][0]
        self.assertEqual(b.label(), "合理的推定")                       # 知った
        self.assertFalse(any("田中" in o for o in out))                 # でも、結びつけて動くことはまだできない
        self.assertFalse(hasattr(prompts, "MATTERS_SYSTEM") or hasattr(prompts, "ACT_SYSTEM"))

    def test_unrelated_digging_ends_quietly(self):
        agent, llm, sensor, mem, clock, out = self.dig_agent([f"- 深海魚の図鑑 {i} (https://example.org/{i})" for i in range(5)])
        agent.perceive("screen", "background_window", "背後のウィンドウ: 深海魚はなぜ光るのか - 動画")
        llm.appraise.append(self.appraisal("深海魚が光るのは獲物を呼ぶためだ"))
        llm.plan += [{"probe": "news_search", "query": "深海魚 光る"}] * 5
        for _ in range(16):                                      # 判定は既定で irrelevant → 掘っても減らない
            agent.step()
        th = agent.inquiry.recent()[0]
        self.assertEqual(th["state"], "closed")
        self.assertLessEqual(th["steps"], agent.cfg["dig_min_steps"] + 1)   # 収穫が無ければ早めに見切る

    def test_keeps_digging_while_it_pays(self):
        agent, llm, sensor, mem, clock, out = self.dig_agent(
            [f"- 記事{i} (https://site{i}.example.com/x)" for i in range(10)])
        agent.perceive("voice", "overheard_speech", "ラジオ: 新しい彗星が肉眼で見えるかもしれない")
        llm.appraise.append(self.appraisal("今週は彗星が肉眼で見える"))
        llm.plan += [{"probe": "news_search", "query": "彗星 肉眼"}] * 6
        llm.wonder += [{"hypotheses": ["彗星は夜明け前の東の空に見える"]}, {"hypotheses": ["彗星は来週には暗くなる"]}]
        llm.judge += [{"verdict": "supports", "reason": "報道"}] * 5
        for _ in range(14):
            agent.step()
        th = agent.inquiry.recent()[0]
        self.assertGreater(th["steps"], agent.cfg["dig_min_steps"])       # わかり続ける間は掘り続ける
        self.assertGreaterEqual(len(agent.inquiry.beliefs(th["id"])), 2)  # わかったことから次の問いを作った

    def test_glance_at_the_edge(self):
        agent, llm, sensor, mem, clock, out = self.dig_agent([])
        agent.probes.camera = object()
        agent.probes.run = lambda name, q: "机の奥のテレビに『大雨特別警報』のテロップ" if name == "look" else None
        clock.t += agent.cfg["glance_interval_s"] + 1
        self.assertTrue(agent.maybe_glance())
        ev = mem.pending_events()[0]
        self.assertEqual((ev["source"], ev["kind"]), ("camera", "glance"))

    def test_background_windows(self):
        titles = [["メモ帳", "YouTube - 台風情報"], ["メモ帳", "YouTube - 台風情報", "Slack - 雑談"]]
        clock = Clock()
        s = BackgroundWindowsSensor(interval_s=30, clock=clock, titles_fn=lambda: titles.pop(0))
        self.assertEqual(len(s.poll()), 2)
        clock.t += 31
        self.assertEqual(s.poll(), [("background_window", "背後のウィンドウ: Slack - 雑談")])


# ---------------------------------------------------------------- 選択圧: 自分から言ったことが役に立ったか (カーネル)
class InitiativeTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.mem = Memory(":memory:", clock=self.clock)
        self.cfg = {"initiative_reply_window_s": 60, "initiative_engage_window_s": 600}
        self.ini = Initiative(self.cfg, self.mem.db, clock=self.clock)

    def rows(self):
        return [tuple(r) for r in self.mem.db.execute("SELECT text, proactive, engaged, annoyed FROM spoken ORDER BY id")]

    def test_replies_are_not_initiative(self):
        self.ini.on_partner("今日の天気は？")
        self.clock.t += 5
        self.ini.on_say("晴れだよ")
        self.assertEqual(self.rows(), [("晴れだよ", 0, 0, 0)])

    def test_partner_engagement_is_the_only_reward(self):
        self.ini.on_say("北海道で大規模な停電が起きてるみたい")
        self.clock.t += 120
        self.ini.on_say("彗星が見えるらしいよ")
        self.clock.t += 60
        self.ini.on_partner("え、北海道の停電ってどのくらい？")        # 同じ話題で話しかけてきた
        self.assertEqual([r[2] for r in self.rows()], [1, 0])
        self.clock.t += 700
        self.ini.on_say("深海魚の話")
        self.ini.on_partner("/bad")                                   # うるさがられた
        self.assertEqual(self.rows()[-1][3], 1)
        s = window_stats(Metrics(self.mem.db), self.mem.db, self.clock.t - 3600, self.clock.t + 1)
        self.assertEqual((s["proactive_n"], s["annoyed_per_h"] > 0), (3, True))
        self.assertGreater(s["initiative"], 0)

    def test_tapped_at_the_sensors_and_the_mouth(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent, llm, sensor, mem, clock, out = world_agent(tmp)
            ini = Initiative(self.cfg, mem.db, clock=clock)
            ini.attach(agent)
            agent.say("ねえ、北海道で停電だって")
            clock.t += 100
            sensor.queue.append(("user_message", "北海道の停電、本当？"))
            llm.appraise.append({"situation": "", "claims": [], "remark": "", "remark_importance": "none"})
            agent.step()
            r = mem.db.execute("SELECT proactive, engaged FROM spoken WHERE text LIKE 'ねえ%'").fetchone()
            self.assertEqual(tuple(r), (1, 1))

    def test_initiative_is_an_evolution_goal(self):
        self.assertIn("initiative", GOALS)
        self.assertIn("plugin", GOALS["initiative"]["levels"])
        self.assertNotIn("仲間", GOALS["initiative"]["pressure"])       # 振る舞いは書かず、圧だけを書く


# ---------------------------------------------------------------- 進化が新しい振る舞いを書き足す場所 (カーネル)
PLUGIN = """
import re

def on_event(api, event):
    if event["kind"] == "overheard_speech" and "停電" in event["content"]:
        bid = api.wonder("停電は近くにも及んでいる", 0.6)
        api.note("last", bid)

def on_tick(api):
    bid = api.note("last")
    if bid is not None and api.note("done") is None:
        res = api.think("短く答える", "停電について一言")
        if res is not None:
            api.say("停電のニュースが気になったよ: " + res, 0.9)
            api.note("done", True)
"""


class PluginTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "evolvable", "plugins"))

    def tearDown(self):
        self._tmp.cleanup()

    def host(self, code, name="edge"):
        with open(os.path.join(self.root, "evolvable", "plugins", name + ".py"), "w", encoding="utf-8") as f:
            f.write(code)
        agent, llm, sensor, mem, clock, out = world_agent(self.root, plugin_max_errors=2, plugin_timeout_s=1.0)
        metrics = Metrics(mem.db)
        h = PluginHost(agent.cfg, agent, metrics, root=self.root)
        return h, agent, llm, mem, metrics

    def test_plugin_adds_a_behavior_through_the_narrow_api(self):
        h, agent, llm, mem, metrics = self.host(PLUGIN)
        self.assertEqual(h.load(), ["edge"])
        h.tick()                                                   # 起点を決める
        agent.perceive("voice", "overheard_speech", "テレビ: 北海道で停電")
        h.tick()
        self.assertTrue(any(b.origin == "plugin/edge" for b in mem.beliefs()))
        u = mem.db.execute("SELECT kind, text FROM utterances WHERE kind='plugin'").fetchone()
        self.assertIn("停電のニュースが気になったよ", u["text"])

    def test_llm_budget_is_one_per_tick(self):
        h, agent, llm, mem, metrics = self.host("def on_tick(api):\n    api.note('a', api.think('x', 'y'))\n"
                                                "    api.note('b', api.think('x', 'y'))\n")
        h.load()
        h.tick()
        self.assertIsNotNone(h.plugins["edge"]["api"].note("a"))
        self.assertIsNone(h.plugins["edge"]["api"].note("b"))

    def test_escapes_are_rejected(self):
        for bad in ("def on_tick(api):\n    api._host.agent.memory.db.execute('DELETE FROM events')\n",
                    "def on_tick(api):\n    getattr(api, '_host')\n",
                    "def on_tick(api):\n    open('x.txt', 'w')\n",
                    "import os\ndef on_tick(api):\n    os.remove('x')\n",
                    "import tachikoma.text\ndef on_tick(api):\n    tachikoma.kernel\n"):
            self.assertTrue(check_source("evolvable/plugins/x.py", bad, plugin=True), bad)
        h, agent, llm, mem, metrics = self.host("import os\ndef on_tick(api):\n    pass\n")
        self.assertEqual(h.load(), [])                              # 実行時にも読み込まない

    def test_broken_or_stuck_plugins_are_stopped_not_fatal(self):
        h, agent, llm, mem, metrics = self.host("def on_tick(api):\n    1 / 0\n")
        h.load()
        h.tick()
        h.tick()
        self.assertEqual(h.plugins, {})                             # 例外が続いたら止める
        self.assertEqual(metrics.count("error", 0, float("inf")), 2)   # 例外は頑健さの進化の材料になる
        os.remove(os.path.join(self.root, "evolvable", "plugins", "edge.py"))
        h, agent, llm, mem, metrics = self.host("import time\ndef on_tick(api):\n    time.sleep(5)\n", "stuck")
        h.load()
        h.tick()
        self.assertEqual(h.plugins, {})                             # 止まったプラグインを待たずに進む


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
