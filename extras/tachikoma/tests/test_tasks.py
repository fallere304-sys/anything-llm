"""頼まれごと: 聞き返さずに、まずやってみる (tachikoma/tasks.py)。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from tachikoma import tasks  # noqa: E402
from tachikoma.web import extract_text, feed_links  # noqa: E402
from test_core import make_agent  # noqa: E402

PARA_A = "今日は朝から雨が降っていたので、家で本を読んで過ごしました。窓の外を眺めながら飲むコーヒーは格別です。" * 2
PARA_B = "午後には晴れてきたので、近所の公園まで散歩に出かけました。子どもたちが元気に走り回っていて、見ていて楽しかったです。" * 2


class FakeWeb:
    def __init__(self, sites=30):
        self.links = [("記事", f"https://blog{i}.hatenablog.com/entry/1") for i in range(sites)]
        self.feeds, self.pages = [], []

    def blog_links(self, feed):
        self.feeds.append(feed)
        out, self.links = self.links[:12], self.links[12:]
        return out

    def read_page(self, url):
        self.pages.append(url)
        tag = url.split("//")[1].split(".")[0]
        return "日記", [PARA_A + tag, PARA_B + tag, PARA_A + tag + "。", PARA_B + tag + "。"]


class TasksTest(unittest.TestCase):
    def setUp(self):
        self.agent, self.llm, _, self.mem, _, self.out = make_agent(tempfile.mkdtemp())
        self.said = []
        self.agent.say = self.said.append

    def run_task(self, max_steps=200):
        for _ in range(max_steps):
            if self.agent.tasks.current is None or self.agent.tasks.current.done:
                break
            self.agent.tasks.current.next_at = 0
            self.agent.tasks.step()

    def test_reads_blogs_without_asking_back(self):
        self.agent.probes.web = FakeWeb()
        self.agent.on_user_message("日本語でかかれたブログを50サイト確認して、日本語の推論の精度を向上させよ")
        self.assertIn("50 サイト読んでくる", self.said[0])                 # 聞き返さずに始める
        self.assertNotIn("?", self.said[0])
        self.assertEqual(self.agent.tasks.current.n, 50)
        self.agent.tasks.current.n = 12
        self.run_task()
        rows = self.mem.db.execute("SELECT COUNT(DISTINCT site) FROM ja_corpus").fetchone()[0]
        self.assertEqual(rows, 12)
        self.assertTrue(any("10 サイト読んだ" in s for s in self.said))       # 区切りで報告
        self.assertIn("12 サイト読み終わった", self.said[-1])
        n = self.mem.db.execute("SELECT COUNT(*) FROM train_samples WHERE kind='ja_style'").fetchone()[0]
        self.assertEqual(n, 24)                                             # 1 ページ 2 つまで
        self.assertIn("学習の部品がまだ無い", self.said[-1])                  # できないことは正直に

    def test_no_web_says_how_to_enable(self):
        self.agent.probes.web = None
        self.agent.on_user_message("ブログを10サイト読んで")
        self.run_task()
        self.assertIn("web を true", self.said[-1])

    def test_fine_tune_explains_what_is_missing(self):
        self.agent.on_user_message("ファインチューンを行えという指示だ。")
        self.assertIn("まだできない", self.said[-1])
        self.assertIn("--hf-login", self.said[-1])

    def test_fine_tune_starts_when_ready(self):
        class Learner:
            busy, stage = False, ""

            def start(self, reason):
                return f"学習を始めます ({reason})"
        self.agent.learner = Learner()
        orig = tasks.learning_missing
        tasks.learning_missing = lambda cfg: []
        try:
            self.agent.on_user_message("学習して")
        finally:
            tasks.learning_missing = orig
        self.assertEqual(self.said[-1], "学習を始めます (相棒の頼み)")

    def test_research_opens_a_thread(self):
        opened = []
        self.agent.inquiry.open = lambda q, seed, origin, interest: opened.append(q) or 1
        self.agent.on_user_message("量子コンピュータについて調べて")
        self.assertEqual(opened, ["量子コンピュータ"])
        self.assertIn("調べてくる", self.said[-1])

    def test_ordinary_talk_is_not_a_task(self):
        self.assertFalse(self.agent.tasks.handle("今日は寒いね"))
        self.assertFalse(self.agent.tasks.handle("ブログって何？"))
        self.assertFalse(self.agent.tasks.handle("それ、もう調べてるの？"))

    def test_counts(self):
        self.assertEqual(tasks.count_in("ブログを50サイト読んで", 20, 100), 50)
        self.assertEqual(tasks.count_in("ブログを五十サイト読んで", 20, 100), 50)
        self.assertEqual(tasks.count_in("ブログを読んで", 20, 100), 20)
        self.assertEqual(tasks.count_in("ブログを5000サイト読んで", 20, 100), 100)


class ReadingTest(unittest.TestCase):
    def test_extract_main_text(self):
        page = ("<html><head><title>日記</title><script>var s='読まない文章です読まない文章です'</script></head><body>"
                "<nav><li>メニューの項目がここにありますメニュー</li></nav><article><p>" + PARA_A + "</p><p>" + PARA_B
                + "</p></article><footer><p>このブログの著作権は筆者に帰属します。無断転載を禁じます。</p></footer></body></html>")
        title, paras = extract_text(page)
        self.assertEqual((title, paras), ("日記", [PARA_A, PARA_B]))

    def test_hatena_rss(self):
        rss = ('<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
               'xmlns="http://purl.org/rss/1.0/"><item rdf:about="https://a.hatenablog.com/entry/1"><title>記事A</title>'
               '<link>https://a.hatenablog.com/entry/1</link></item></rdf:RDF>').encode()
        self.assertEqual(feed_links(rss), [("記事A", "https://a.hatenablog.com/entry/1")])


if __name__ == "__main__":
    unittest.main()


class PartnerIsNotAHypothesisTest(unittest.TestCase):
    """相棒の心や行動は推し量るもので、調べて確かめたり報告したりしない。"""

    def setUp(self):
        self.agent, self.llm, _, self.mem, _, _ = make_agent(tempfile.mkdtemp())

    def test_no_hypotheses_about_the_partner(self):
        for st in ("ユーザーはTachikoma.exeのウィンドウを背景として認識している。",
                   "ユーザーは現在、何らかの文書やウェブページを復元している。", "相棒は疲れている"):
            self.assertIsNone(self.agent._hypothesis(st, 0.5, relevance=1.0, basis="guessed"), st)
        self.assertIsNotNone(self.agent._hypothesis("札幌で停電が起きている", 0.5, relevance=1.0, basis="guessed"))
        self.assertIsNone(self.agent.inquiry.open("ユーザーの意図は何か", "seed", "screen/window", 1.0))

    def test_premises_of_the_partner_are_not_checked(self):
        from tachikoma import prompts  # noqa: F401
        self.llm.inquiry.append({"premises": [{"statement": "ユーザーはブログを読んでいる", "doubtful": True}],
                                 "unknowns": ["ユーザーの意図"], "answerable": False})
        self.assertEqual(self.agent.inquire("ブログってどう思う？"), "")
        self.assertEqual([b.statement for b in self.mem.beliefs()], [])

    def test_findings_are_one_natural_sentence(self):
        from tachikoma.agent import finding_text
        from tachikoma.memory import FACT, REFUTED
        self.assertEqual(finding_text("札幌の停電は復旧した。", FACT, promised=True),
                         "さっき気になってた「札幌の停電は復旧した」、調べたら本当だったよ。")
        self.assertEqual(finding_text("量子コンピュータは既に実用化されている", REFUTED),
                         "ひとつわかったよ。「量子コンピュータは既に実用化されている」、調べたら違ったみたい。")


class LegacyTidyTest(unittest.TestCase):
    def test_old_partner_beliefs_and_old_reports_are_put_away(self):
        agent, llm, _, mem, _, _ = make_agent(tempfile.mkdtemp())
        old = mem.add_belief("ユーザーの画面の背後にはWinRARの体験版が表示されている", 0.5, "reflection")
        img = mem.add_belief("提供された画像は、主に表形式のデータで構成されている", 0.5, "reflection")
        keep = mem.add_belief("札幌で停電が起きている", 0.5, "reflection")
        mem.add_utterance("finding", "確かめた: 提供された画像は表 → [観測事実] 根拠は…", 0.9)
        mem.add_utterance("finding", "ひとつわかったよ。「札幌の停電は復旧した」、調べたら本当だったよ。", 0.5)
        agent._tidy_legacy()
        left = [b.id for b in mem.beliefs(include_irreducible=False)]
        self.assertEqual((old in left, img in left, keep in left), (False, False, True))
        self.assertEqual([u["text"][:5] for u in mem.pending_utterances()], ["ひとつわか"])

    def test_judge_reason_is_short(self):
        from tachikoma import prompts
        self.assertEqual(prompts.JUDGE_SCHEMA["properties"]["reason"]["maxLength"], 120)

