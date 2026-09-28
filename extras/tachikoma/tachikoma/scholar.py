"""論文と政府文書を調べる (強い根拠を取りに行く)。すべてキー不要の公開 API。

- OpenAlex   … 分野横断の論文 (要旨・被引用数・掲載先の種類)
- PubMed     … 医学・生命科学 (出版種別に「メタ分析」「RCT」が付いているので格付けが確実)
- arXiv      … 情報科学・物理などのプレプリント (未査読として扱う)
- 政府文書   … SearXNG があれば site:go.jp / site:gov 等で検索 (Docker で自前起動できる)

見つけた根拠は epistemics.classify_text で種類を判定し、信頼度と「因果を示せる研究デザインか」を付ける。
外に出るのは検索語だけで、関所 (kernel/egress.py) が個人情報を含まない一般的な問いに直す (直せなければ送らない)。
連絡先 (メールアドレス) などの利用者の情報は、どの問い合わせにも付けない。
"""

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .epistemics import TIER_LABEL, classify_text, grade
from .text import clip
from .web import UA, sanitize


@dataclass
class Evidence:
    title: str
    summary: str
    url: str
    year: object
    kind: str
    citations: int = 0

    @property
    def reliability(self):
        return grade(self.kind)[0]

    @property
    def causal_design(self):
        return grade(self.kind)[1]

    def cite(self):
        return f"[{TIER_LABEL.get(self.kind, self.kind)}] {self.title} ({self.year}) {self.url}"


def _abstract(inv):
    """OpenAlex の abstract_inverted_index を文章に戻す。"""
    if not inv:
        return ""
    pos = sorted((i, w) for w, idx in inv.items() for i in idx)
    return " ".join(w for _, w in pos)


class Scholar:
    def __init__(self, cfg, opener=urllib.request.urlopen):
        self.cfg, self.opener = cfg, opener

    def _get(self, url, raw=False):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with self.opener(req, timeout=20) as r:
            data = r.read()
        return data if raw else json.loads(data.decode("utf-8"))

    def search(self, query, k=4):
        """強い根拠が先に来るように並べて返す。"""
        q = sanitize(query, limit=120)
        if q is None:
            return []
        found = []
        for fn in (self.openalex, self.pubmed, self.arxiv, self.government):
            try:
                found += fn(q)
            except (OSError, ValueError, KeyError, ET.ParseError):
                continue
        found.sort(key=lambda e: (-e.reliability, -(e.citations or 0)))
        return found[:k]

    def openalex(self, q):
        data = self._get("https://api.openalex.org/works?per_page=5&search=" + urllib.parse.quote(q))
        out = []
        for w in data.get("results", []):
            src = ((w.get("primary_location") or {}).get("source") or {})
            abstract = _abstract(w.get("abstract_inverted_index"))
            url = w.get("doi") or w.get("id", "")
            kind = classify_text(w.get("title") or "", abstract, [w.get("type") or ""], src.get("type") or "",
                                 url if "arxiv" in (src.get("display_name") or "").lower() else "")
            if w.get("type") == "preprint" or (src.get("display_name") or "").lower().startswith("arxiv"):
                kind = "preprint" if kind in ("peer_reviewed", "unknown", "observational") else kind
            out.append(Evidence(w.get("title") or "", clip(abstract, 600), url, w.get("publication_year"),
                                kind, w.get("cited_by_count") or 0))
        return out

    def pubmed(self, q):
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        ids = self._get(base + "esearch.fcgi?db=pubmed&retmode=json&retmax=4&sort=relevance&term="
                        + urllib.parse.quote(q)).get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        res = self._get(base + "esummary.fcgi?db=pubmed&retmode=json&id=" + ",".join(ids)).get("result", {})
        out = []
        for i in ids:
            d = res.get(i, {})
            kind = classify_text(d.get("title", ""), "", d.get("pubtype", []), "journal")
            out.append(Evidence(d.get("title", ""), d.get("fulljournalname", ""),
                                f"https://pubmed.ncbi.nlm.nih.gov/{i}/", (d.get("pubdate") or "")[:4], kind))
        return out

    def arxiv(self, q):
        xml = self._get("https://export.arxiv.org/api/query?max_results=3&search_query=all:"
                        + urllib.parse.quote(q), raw=True)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for e in ET.fromstring(xml).findall("a:entry", ns):
            title = " ".join((e.findtext("a:title", "", ns) or "").split())
            summary = " ".join((e.findtext("a:summary", "", ns) or "").split())
            kind = classify_text(title, summary, url="https://arxiv.org")
            out.append(Evidence(title, clip(summary, 600), e.findtext("a:id", "", ns),
                                (e.findtext("a:published", "", ns) or "")[:4], kind))
        return out

    def government(self, q):
        base = (self.cfg.get("searxng_url") or "").rstrip("/")
        if not base:
            return []
        out = []
        for site in self.cfg.get("gov_sites", ["go.jp", "gov"]):
            data = self._get(f"{base}/search?format=json&q=" + urllib.parse.quote(f"{q} site:{site}"))
            for r in data.get("results", [])[:3]:
                kind = classify_text(r.get("title", ""), r.get("content", ""), url=r.get("url", ""))
                out.append(Evidence(r.get("title", ""), clip(r.get("content", ""), 400), r.get("url", ""), "", kind))
        return out
