"""ネット検索 (外の知識)。キー不要の Wikipedia API か、自前の SearXNG を使う。

ネットは「世界の一般知識」の裏付けにはなるが、「目の前で何が起きているか」の裏付けにはならない。
そのため出所は web (確信の上限 0.85) とし、[観測事実] にはしない。

外に出る唯一の経路なので、検索語に個人情報らしきもの (パス・メール・長い数字・URL・
トークン風の文字列) が含まれていたら送らない。
"""

import json
import re
import urllib.parse
import urllib.request

from .text import clip

UA = "Tachikoma/0.1 (local personal assistant; https://github.com/)"
_PRIVATE = [
    re.compile(r"[A-Za-z]:\\|/(home|Users|mnt|var|etc)/"),     # ファイルパス
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                    # メール
    re.compile(r"\d{6,}"),                                     # 電話・口座・ID など
    re.compile(r"https?://"),
    re.compile(r"[A-Za-z0-9_\-]{24,}"),                        # トークン・鍵
]


def sanitize(query, limit=80):
    q = re.sub(r"\s+", " ", query or "").strip()
    if not q or any(p.search(q) for p in _PRIVATE):
        return None
    return q[:limit]


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
