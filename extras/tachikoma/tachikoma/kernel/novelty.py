"""新しさの評価: 「ネットにも論文にも見当たらないやり方」を試すことを高く評価する。

    novelty = web_weight × (ネット・論文で見つからない度合い)
            + (1 − web_weight) × (自分が過去に試したやり方と似ていない度合い)

- ネット・論文: 改良アイデアのキーワード (英語) で Scholar (OpenAlex / arXiv / PubMed / 政府文書) と
  Web (Wikipedia / SearXNG) を検索し、題名・要旨がキーワードの大半を含む「同じやり方らしき」結果を数える。
  見つからないほど 1 に近い。検索できないときは不明 (0.5) として扱う
- 自分の過去: これまでに試したアイデアの説明との文字 bigram の類似度 (新規性探索: Lehman & Stanley)

注意: 「検索で見つからない」は「世界で誰も試していない」の証明ではない (検索の網羅性に限界がある)。
あくまで [低確度仮説] の新しさとして扱い、安全の関門 (検査・テスト・試用期間) は新しさで緩めない。
"""

import re

from ..text import jaccard

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def keywords_of(text):
    return [w.lower() for w in dict.fromkeys(_WORD.findall(text or ""))][:8]


class NoveltyJudge:
    def __init__(self, cfg, scholar=None, web=None):
        self.cfg, self.scholar, self.web = cfg, scholar, web

    def web_novelty(self, keywords):
        """(新しさ 0-1 または None, 見つかった似たやり方の例)"""
        kws = [k.lower() for k in keywords if k][:8]
        if not kws or (self.scholar is None and self.web is None):
            return None, []
        query = " ".join(kws)
        texts, examples = [], []
        try:
            if self.scholar is not None:
                for e in self.scholar.search(query, k=8):
                    texts.append((e.title, f"{e.title} {e.summary}"))
            if self.web is not None:
                r = self.web.search(query)
                if r:
                    texts += [(line[:80], line) for line in r.splitlines() if line.strip()]
        except Exception:  # noqa: BLE001 — 検索の失敗は「不明」
            return None, []
        need = max(2, int(len(kws) * 0.6 + 0.5))
        hits = 0
        for title, t in texts:
            low = t.lower()
            if sum(k in low for k in kws) >= need:
                hits += 1
                examples.append(title)
        return 1.0 / (1.0 + hits), examples[:3]

    @staticmethod
    def archive_novelty(description, past):
        if not past:
            return 1.0
        return 1.0 - max(jaccard(description, p) for p in past)

    def score(self, idea, past):
        """idea: {"name", "keywords", "description"}。past: 過去に試したアイデアの説明のリスト。"""
        w = self.cfg["novelty_web_weight"]
        web, examples = self.web_novelty(idea.get("keywords") or keywords_of(idea.get("description", "")))
        arch = self.archive_novelty(idea.get("description", ""), past)
        web_v = 0.5 if web is None else web
        return {"novelty": w * web_v + (1 - w) * arch, "web": web, "archive": arch, "similar": examples}
