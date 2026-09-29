"""ネット検索 (外の知識)。キー不要の Wikipedia API か、自前の SearXNG を使う。

ネットは「世界の一般知識」の裏付けにはなるが、「目の前で何が起きているか」の裏付けにはならない。
そのため出所は web (確信の上限 0.85) とし、[観測事実] にはしない。

検索語は外に出る前に、関所 (kernel/egress.py) で個人情報を含まない一般的な問いに直す。
直せなければ送らない。関所は urllib の通信すべてにも掛かっている。
"""

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from .kernel import egress
from .text import clip

UA = "Tachikoma/0.1"          # 送るのは名前だけ (利用者や環境を特定できる情報は付けない)

# 日本語のブログを探す入口: はてなブックマークの人気・新着エントリー (分野別の RSS)。問い合わせは送らない
BLOG_FEEDS = [f"https://b.hatena.ne.jp/hotentry/{c}.rss" for c in
              ("all", "general", "life", "knowledge", "it", "fun", "entertainment", "game", "social", "economics")] + \
             [f"https://b.hatena.ne.jp/entrylist/{c}.rss" for c in ("all", "life", "knowledge", "it", "fun")]
# ブログらしい場所 (個人が書いた地の文が多い)
BLOG_HOSTS = ("hatenablog.com", "hatenablog.jp", "hateblo.jp", "hatenadiary.com", "hatenadiary.jp", "hatenadiary.org",
              "note.com", "ameblo.jp", "livedoor.blog", "blog.jp", "blog.livedoor.jp", "fc2.com", "seesaa.net",
              "goo.ne.jp", "jugem.jp", "exblog.jp", "zenn.dev", "qiita.com", "blogspot.com", "wordpress.com")
_JA = re.compile(r"[ぁ-んァ-ヴ一-龥]")
_RSS1 = "{http://purl.org/rss/1.0/}"


def is_blog(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in BLOG_HOSTS) or "/blog" in url.lower()


def feed_links(data):
    """RSS 1.0 (はてな) / 2.0 / Atom から [(題, URL)]。"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out = []
    for tag, title, link in ((_RSS1 + "item", _RSS1 + "title", _RSS1 + "link"), ("item", "title", "link")):
        for it in root.iter(tag):
            t, l = it.find(title), it.find(link)
            if l is not None and (l.text or "").startswith("http"):
                out.append(((t.text or "").strip() if t is not None else "", l.text.strip()))
    return out


class _Text(HTMLParser):
    """本文らしい段落 (p・li・h2〜h4・blockquote) の文字を集める。script / style / nav / 足回りは読まない。"""
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg", "button"}
    BLOCK = {"p", "li", "h2", "h3", "h4", "blockquote", "dd", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.stack = []
        self.buf = []
        self.paras = []
        self.title = ""
        self._in_title = False
        self.in_article = 0
        self.article_paras = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in ("article", "main"):
            self.in_article += 1
        if tag in self.BLOCK:
            self._flush()
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self._flush()
        if tag in ("article", "main") and self.in_article:
            self.in_article -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip and self.stack:
            self.buf.append(data)

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if len(text) >= 20 and _JA.search(text):
            self.paras.append(text)
            if self.in_article:
                self.article_paras.append(text)


def extract_text(page):
    """HTML から (題, 本文の段落のリスト)。記事 (article / main) の中があればそれを優先する。"""
    p = _Text()
    try:
        p.feed(page)
        p.close()
    except Exception:  # noqa: BLE001  (壊れた HTML は読めたところまで)
        pass
    paras = p.article_paras if sum(map(len, p.article_paras)) >= 300 else p.paras
    seen, out = set(), []
    for x in paras:
        if x not in seen:
            seen.add(x)
            out.append(html.unescape(x))
    return re.sub(r"\s+", " ", p.title).strip(), out


def sanitize(query, limit=80):
    """検索語を、個人情報を含まない一般的な問いにする (関所の規則)。送れなければ None。"""
    q, _ = egress.clean(query, limit)
    return q


class WebSearch:
    def __init__(self, cfg, opener=urllib.request.urlopen):
        self.cfg, self.opener = cfg, opener

    def _get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with self.opener(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    def search(self, query):
        q = sanitize(query)
        if q is None:
            return None
        try:
            if self.cfg.get("searxng_url"):
                return self._searxng(q)
            return self._wikipedia(q)
        except (OSError, ValueError, KeyError):
            return None

    # ------------------------------------------------------------ 読む (文章を取ってくるだけ。問い合わせは送らない)
    def blog_links(self, feed_url):
        req = urllib.request.Request(feed_url, headers={"User-Agent": UA})
        with self.opener(req, timeout=15) as r:
            return feed_links(r.read(2_000_000))

    def read_page(self, url, max_bytes=1_500_000):
        """ページを取ってきて (題, 段落のリスト)。日本語の本文が無ければ段落は空。"""
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
        with self.opener(req, timeout=20) as r:
            if "html" not in (r.headers.get("Content-Type") or "text/html"):
                return "", []
            raw = r.read(max_bytes)
            charset = r.headers.get_content_charset() or "utf-8"
        try:
            page = raw.decode(charset, "replace")
        except LookupError:
            page = raw.decode("utf-8", "replace")
        return extract_text(page)

    def _searxng(self, q):
        base = self.cfg["searxng_url"].rstrip("/")
        data = self._get(f"{base}/search?format=json&q={urllib.parse.quote(q)}")
        items = data.get("results", [])[:3]
        return "\n".join(f"- {i.get('title', '')}: {clip(i.get('content', ''), 300)} ({i.get('url', '')})"
                         for i in items) or None

    def _wikipedia(self, q):
        lang = self.cfg.get("web_language", "ja")
        api = f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=2&srsearch="
        hits = self._get(api + urllib.parse.quote(q)).get("query", {}).get("search", [])
        out = []
        for h in hits:
            title = h["title"]
            s = self._get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                          + urllib.parse.quote(title.replace(" ", "_")))
            out.append(f"- Wikipedia「{title}」: {clip(s.get('extract', ''), 400)}")
        return "\n".join(out) or None
