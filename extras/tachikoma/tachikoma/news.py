"""ニュース (耳目の外の世界)。RSS / Atom を定期的に読み、見出しを出来事として感覚器に流す。

タチコマがテレビのニュースで仲間の危機を知ったように、目の前と関係のない情報も拾い続ける。
ほとんどはノイズだが、どれが後で役に立つかは拾ってみないとわからない (kernel/foresight.py が記録する)。

外に出るのは (1) 設定した RSS の取得と (2) ニュース検索の語だけ。検索語は web.sanitize() で
個人情報らしきもの (パス・メール・長い数字・URL・トークン) を止める。ネットに触れるのでカーネル。
"""

import queue
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .text import clip, site
from .web import UA, sanitize

_ATOM = "{http://www.w3.org/2005/Atom}"


def _text(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def _strip_html(s):
    out, depth = [], 0
    for ch in s or "":
        if ch == "<":
            depth += 1
        elif ch == ">" and depth:
            depth -= 1
        elif not depth:
            out.append(ch)
    return " ".join("".join(out).split())


def parse_feed(data):
    """RSS 2.0 / Atom の中身から [{title, summary, link, published}] を返す。壊れていれば []。"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    items = []
    for it in root.iter("item"):
        items.append({"title": _text(it, "title"), "summary": _strip_html(_text(it, "description")),
                      "link": _text(it, "link"), "published": _text(it, "pubDate")})
    for it in root.iter(_ATOM + "entry"):
        link = it.find(_ATOM + "link")
        items.append({"title": _text(it, _ATOM + "title"),
                      "summary": _strip_html(_text(it, _ATOM + "summary") or _text(it, _ATOM + "content")),
                      "link": link.get("href", "") if link is not None else "",
                      "published": _text(it, _ATOM + "updated")})
    return [i for i in items if i["title"]]


def domain(url):
    try:
        return site(urllib.parse.urlparse(url).netloc)
    except ValueError:
        return ""


class NewsFeed:
    def __init__(self, cfg, opener=urllib.request.urlopen, clock=time.time):
        self.cfg, self.opener, self.clock = cfg, opener, clock
        self.watching = {}          # 名前 -> 見張りの期限 (仲間の続報を追う)

    def _get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with self.opener(req, timeout=15) as r:
            return r.read(2 * 2**20)      # 異常に大きい応答は読まない

    def fetch(self, url):
        try:
            return parse_feed(self._get(url))[: self.cfg["news_max_items"]]
        except (OSError, ValueError):
            return []

    def search(self, query):
        """ニュース検索。根拠テキスト (見出し + 出どころ) を返す。見つからなければ None。"""
        q = sanitize(query)
        tmpl = self.cfg.get("news_search_url")
        if q is None or not tmpl:
            return None
        items = self.fetch(tmpl.format(q=urllib.parse.quote(q)))
        if not items:
            return None
        return "\n".join(f"- {clip(i['title'], 160)}: {clip(i['summary'], 200)} ({i['link']})" for i in items[:5])

    def watch(self, name, days):
        """仲間の名前をしばらく見張る (定期的にニュース検索する)。"""
        if sanitize(name) is not None:
            self.watching[name] = self.clock() + days * 86400

    def watched(self):
        now = self.clock()
        self.watching = {k: v for k, v in self.watching.items() if v > now}
        return list(self.watching)


class NewsSensor:
    """一定間隔で RSS (と見張り中の名前の検索) を読み、初めて見た見出しを出来事にする。
    取得は別スレッド、記録は poll() (本体のスレッド) で行う。"""
    name = "news"

    def __init__(self, cfg, feed, db, clock=time.time):
        self.cfg, self.feed, self.db, self.clock = cfg, feed, db, clock
        self.db.execute("CREATE TABLE IF NOT EXISTS news_seen (k TEXT PRIMARY KEY, ts REAL)")
        self.db.commit()
        self._q = queue.Queue()
        self._busy = False
        self._next = 0.0

    def _fetch_all(self):
        try:
            for url in self.cfg["news_feeds"]:
                for i in self.feed.fetch(url):
                    self._q.put(dict(i, feed=domain(url)))
            tmpl = self.cfg.get("news_search_url")
            for name in self.feed.watched() if tmpl else []:
                q = sanitize(name)
                if q:
                    for i in self.feed.fetch(tmpl.format(q=urllib.parse.quote(q))):
                        self._q.put(dict(i, feed="search", watched=name))
        finally:
            self._busy = False

    def poll(self):
        now = self.clock()
        if not self._busy and now >= self._next:
            self._next = now + self.cfg["news_interval_s"]
            self._busy = True
            threading.Thread(target=self._fetch_all, daemon=True).start()
        out = []
        while True:
            try:
                i = self._q.get_nowait()
            except queue.Empty:
                break
            key = i["link"] or i["title"]
            if self.db.execute("SELECT 1 FROM news_seen WHERE k=?", (key,)).fetchone():
                continue
            self.db.execute("INSERT INTO news_seen(k, ts) VALUES (?, ?)", (key, now))
            text = i["title"] + (f" — {clip(i['summary'], 300)}" if i["summary"] else "")
            out.append(("news", text, {"link": i["link"], "source": domain(i["link"]) or i["feed"],
                                       "watched": i.get("watched")}))
        if out:
            self.db.execute("DELETE FROM news_seen WHERE ts < ?", (now - 30 * 86400,))
            self.db.commit()
        return out
