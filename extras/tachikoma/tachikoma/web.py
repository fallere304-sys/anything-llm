"""ネット検索 (外の知識)。キー不要の Wikipedia API か、自前の SearXNG を使う。

ネットは「世界の一般知識」の裏付けにはなるが、「目の前で何が起きているか」の裏付けにはならない。
そのため出所は web (確信の上限 0.85) とし、[観測事実] にはしない。

検索語は外に出る前に、関所 (kernel/egress.py) で個人情報を含まない一般的な問いに直す。
直せなければ送らない。関所は urllib の通信すべてにも掛かっている。
"""

import json
import urllib.parse
import urllib.request

from .kernel import egress
from .text import clip

UA = "Tachikoma/0.1"          # 送るのは名前だけ (利用者や環境を特定できる情報は付けない)


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
