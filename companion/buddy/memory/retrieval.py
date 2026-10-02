"""記憶の関連度計算。2文字単位(bigram)の重なりで測る。

日本語でも分かち書き不要・追加ライブラリ不要・API費用なし。意味的な検索(埋め込み)が
必要になったら、この関数を差し替える。
"""
from __future__ import annotations

import math
import re
import unicodedata

_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)


def bigrams(text: str) -> set[str]:
    norm = _STRIP.sub("", unicodedata.normalize("NFKC", text).lower())
    if len(norm) < 2:
        return {norm} if norm else set()
    return {norm[i : i + 2] for i in range(len(norm) - 1)}


def relevance(query: str, content: str) -> float:
    """0..1。集合のコサイン類似度。"""
    q, c = bigrams(query), bigrams(content)
    if not q or not c:
        return 0.0
    return len(q & c) / math.sqrt(len(q) * len(c))
