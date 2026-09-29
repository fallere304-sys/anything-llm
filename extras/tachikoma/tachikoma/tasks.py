"""頼まれごと: 相棒に「やって」と言われたら、聞き返さずにまずやってみる。

頼みを、いまできる行動に当てはめて、その場で始める。やり方の細部は、ふつうのやり方で決めて「〜でやってみるね」と
一言添えるだけにする。できないことは、できないと一言で言い、できるようにする道を具体的に言う。

    ブログを読む (read_blogs)   日本語のブログを N サイト読んで、自然な日本語の手本を貯める (学習用の標本にもする)
    学習する     (fine_tune)    頭の学習 (LoRA) を始める。部品が無ければ、何が足りないかと入れ方を言う
    調べる       (research)     探究の糸を立てて深掘りする (わかったら報告する)

長い仕事 (ブログを読む) は本体のループで 1 歩ずつ進め、区切りで報告する。

カーネル: 外へ読みに行く・学習を始める、という外界に触れる行動なので、自己進化の外に置く。
"""

import importlib.util
import re
import time

from .text import clip
from .web import BLOG_FEEDS, is_blog

# 頼みの言い方 (命令・依頼)
_REQUEST = re.compile(r"(しろ|せよ|して(くれ|ください|ほしい|欲しい|みて|ね|よ)?|やって|やれ|頼む|お願い|させよ|させて)[。!！\s]*$")
_BLOGS = re.compile(r"(ブログ|記事|サイト).*(読|確認|見|集め|調べ|目を通)")
_TUNE = re.compile(r"ファインチューン|ファインチューニング|fine.?tun|微調整|(学習|訓練)(して|しろ|せよ|を(し|行|始|や))", re.I)
_RESEARCH = re.compile(r"(調べ|検索し|リサーチし|探し)(て|ろ|よ|なさい)(くれ|ください|ほしい|みて|おいて)?(よ|ね)?[。!！\s]*$")
_COUNT = re.compile(r"(\d{1,4})\s*(サイト|件|個|本|記事|ページ)")
_KANJI_NUM = {"十": 10, "二十": 20, "三十": 30, "五十": 50, "百": 100}

SCHEMA = """CREATE TABLE IF NOT EXISTS ja_corpus (
    url TEXT PRIMARY KEY, site TEXT, title TEXT, text TEXT, chars INTEGER, ts REAL);"""
STYLE_SYSTEM = "自然な日本語で、文章の続きを書きます。"


def count_in(text, default, cap):
    m = _COUNT.search(text or "")
    if m:
        return max(1, min(cap, int(m.group(1))))
    for k, v in sorted(_KANJI_NUM.items(), key=lambda kv: -len(kv[0])):
        if k + "サイト" in (text or "") or k + "件" in (text or ""):
            return min(cap, v)
    return default


def site_of(url):
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    return host


def learning_missing(cfg, find=importlib.util.find_spec):
    """頭の学習に足りないもの (空なら学習できる)。"""
    missing = []
    if not cfg.get("finetune_enabled"):
        missing.append("学習が切ってある (config.json の finetune_enabled)")
    for mod, label in (("torch", "PyTorch"), ("transformers", "transformers"), ("peft", "peft")):
        if find(mod) is None:
            missing.append(f"{label} が入っていない")
    return missing


class ReadBlogs:
    """日本語のブログを N サイト読む。1 歩で 1 回だけネットに出る (フィードを 1 つ、またはページを 1 つ)。"""

    def __init__(self, agent, n):
        self.a, self.n = agent, n
        self.db = agent.memory.db
        self.db.execute(SCHEMA)
        self.feeds = list(BLOG_FEEDS)
        self.queue, self.sites, self.chars, self.samples, self.failed = [], set(), 0, 0, 0
        self.done = False
        self.next_at = 0.0
        self.started = time.time()

    def describe(self):
        return f"日本語のブログを {self.n} サイト読む"

    def step(self):
        web = getattr(self.a.probes, "web", None)
        if web is None:
            self.finish("ネット (web) が切ってあるので、ブログを読めなかった。config.json の web を true にすると読めるよ。")
            return
        now = time.time()
        if now < self.next_at:
            return
        self.next_at = now + 1.5                 # 相手のサーバーに負担をかけない間隔
        if not self.queue:
            if not self.feeds:
                self.finish()
                return
            try:
                links = web.blog_links(self.feeds.pop(0))
            except Exception as e:  # noqa: BLE001  (つながらない・関所で止まった)
                self.failed += 1
                self.a.log(f"ブログの入口を読めなかった: {e}")
                return
            known = {r[0] for r in self.db.execute("SELECT url FROM ja_corpus")}
            blogs = [u for _, u in links if is_blog(u)]
            others = [u for _, u in links if not is_blog(u)]
            self.queue = [u for u in blogs + others if u not in known]
            return
        url = self.queue.pop(0)
        site = site_of(url)
        if site in self.sites:
            return
        try:
            title, paras = web.read_page(url)
        except Exception as e:  # noqa: BLE001
            self.failed += 1
            self.a.log(f"読めなかった: {site} ({clip(str(e), 80)})")
            return
        text = "\n".join(paras)
        if len(text) < 400:
            return
        self.db.execute("INSERT OR REPLACE INTO ja_corpus(url, site, title, text, chars, ts) VALUES (?,?,?,?,?,?)",
                        (url, site, title, text[:20000], len(text), time.time()))
        self.db.commit()
        self.sites.add(site)
        self.chars += len(text)
        self.samples += self._samples(paras, site)
        k = len(self.sites)
        self.a.log(f"ブログを読んだ ({k}/{self.n}): {site}「{clip(title, 40)}」{len(text)} 字")
        if k % 10 == 0 and k < self.n:
            self.a.say(f"ブログ {k} サイト読んだよ (ここまで {self.chars // 1000} 千字)。続けるね。")
        if k >= self.n:
            self.finish()

    def _samples(self, paras, site):
        """段落の前半 → 続き、の形で「自然な日本語で書く」学習の標本にする (1 ページ 2 つまで)。"""
        data = getattr(self.a, "data", None)
        if data is None:
            return 0
        made, i = 0, 0
        while made < 2 and i + 1 < len(paras):
            head, tail = paras[i], paras[i + 1]
            if 60 <= len(head) <= 600 and 60 <= len(tail) <= 600:
                data.add_sample("ja_style", STYLE_SYSTEM, "次の文章の続きを書いてください。\n\n" + head, tail,
                                f"blog:{site}", weight=0.3)
                made += 1
            i += 2
        return made

    def finish(self, note=None):
        self.done = True
        if note:
            self.a.say(note)
            return
        k = len(self.sites)
        if k == 0:
            self.a.say("ブログを読みに行ったけど、1 つも読めなかった。ネットにつながっているか見てほしいな"
                       + (f" (失敗 {self.failed} 回)" if self.failed else "") + "。")
            return
        mins = max(1, int((time.time() - self.started) / 60))
        msg = (f"ブログを {k} サイト読み終わったよ ({self.chars // 1000} 千字・{mins} 分)。"
               f"自然な日本語の手本を {self.samples} 件、学習用に貯めた。")
        missing = learning_missing(self.a.cfg)
        if missing:
            msg += "ただ、頭の学習の部品がまだ無いから、いまは貯めておくだけ。学習できるようになったら「学習して」って言ってね。"
        else:
            msg += "「学習して」って言ってくれたら、これで頭を鍛えるよ。"
        if k < self.n:
            msg += f" (頼まれた {self.n} サイトには届かなかった: 読める入口を使い切った)"
        self.a.say(msg)


class Tasks:
    def __init__(self, agent):
        self.a = agent
        self.current = None
        self._next = None

    def is_request(self, text):
        t = (text or "").strip()
        return bool(_REQUEST.search(t) or _BLOGS.search(t) or _TUNE.search(t))

    def handle(self, text):
        """頼みごとならその場で始めて True。頼みごとでなければ False (ふつうに会話する)。"""
        t = (text or "").strip()
        if _BLOGS.search(t) and ("ブログ" in t or "日本語" in t):
            return self._read_blogs(t)
        if _TUNE.search(t):
            return self._fine_tune()
        m = _RESEARCH.search(t)
        if m:
            topic = re.sub(r"(について|のこと|を|、|。|\s)+$", "", t[:m.start()]).strip() or t
            return self._research(topic)
        return False

    def _read_blogs(self, text):
        n = count_in(text, 20, 100)
        if self.current is not None and not self.current.done:
            self.a.say(f"いまは「{self.current.describe()}」の途中なんだ。終わったら続けて {n} サイト読むね。")
            self._next = ReadBlogs(self.a, n)
            return True
        self.current = ReadBlogs(self.a, n)
        self.a.say(f"わかった、はてなブックマークで話題の記事から、日本語のブログを {n} サイト読んでくるね。"
                   "10 サイトごとに報告して、読んだ文章は自然な日本語の手本として学習用に貯めておく。")
        return True

    def _fine_tune(self):
        learner = getattr(self.a, "learner", None)
        missing = learning_missing(self.a.cfg)
        if learner is None or missing:
            self.a.say("学習 (ファインチューン) はまだできないんだ。足りないもの: " + "、".join(missing or ["学習の係がいない"])
                       + "。Tachikoma.exe --setup で「学習」を選んで入れて、Gemma の利用規約に同意してから "
                       "Tachikoma.exe --hf-login でログインすると、できるようになるよ。それまで学習の材料は貯めておくね。")
            return True
        if learner.busy:
            self.a.say(f"もう学習中だよ (いまは「{learner.stage}」)。終わったら結果を言うね。")
            return True
        msg = learner.start("相棒の頼み")
        self.a.say(msg or "学習に使える標本がまだ足りないみたい。ブログを読んだり、/good や訂正をもらうと増えるよ。")
        return True

    def _research(self, topic):
        inq = getattr(self.a, "inquiry", None)
        if inq is None:
            return False
        tid = inq.open(topic, seed=topic, origin="request", interest=1.0)
        if tid is None:
            self.a.say(f"「{clip(topic, 40)}」、いま調べものが詰まってるから、ひとつ終わったらすぐ取りかかるね。")
        else:
            self.a.say(f"わかった、「{clip(topic, 40)}」を調べてくる。わかったら報告するね。")
        return True

    def step(self):
        """長い仕事を 1 歩進める。進めたら True。"""
        if self.current is None:
            return False
        if self.current.done:
            nxt = getattr(self, "_next", None)
            self._next = None
            self.current = nxt
            if nxt is None:
                return False
            self.a.say(f"次は「{nxt.describe()}」に取りかかるね。")
        self.current.step()
        return True

