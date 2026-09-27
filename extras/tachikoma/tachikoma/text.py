"""言語非依存の軽量テキスト類似度。

日本語は空白で単語分割できないため、文字 bigram 集合で近さを測る。
LLM も埋め込みモデルも使わないので CPU で数万件を即時に比較できる。
"""

import re

_WS = re.compile(r"\s+")


def grams(text, n=2):
    t = _WS.sub(" ", (text or "").lower()).strip()
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def jaccard(a, b):
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def overlap(query, text):
    """query の bigram が text にどれだけ含まれるか (検索用、0-1)。"""
    gq, gt = grams(query), grams(text)
    if not gq or not gt:
        return 0.0
    return len(gq & gt) / len(gq)


def clip(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"
