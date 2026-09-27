"""言語非依存の軽量テキスト類似度。

日本語は空白で単語分割できないため、文字 bigram 集合で近さを測る。
LLM も埋め込みモデルも使わないので CPU で数万件を即時に比較できる。
"""

import re
import unicodedata

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


_SLD = {"co", "or", "ne", "ac", "go", "ed", "lg", "gr", "com", "net", "org", "gov", "edu"}


def site(host):
    """同じ報道機関を 1 つに数えるための登録ドメイン (www3.nhk.or.jp → nhk.or.jp)。"""
    parts = (host or "").lower().split(":")[0].strip(".").split(".")
    if all(p.isdigit() for p in parts):
        return ".".join(parts)          # IP アドレスはそのまま
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def clip(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- 音声認識の評価用
_BRACKETS = re.compile(r"[(\[【〈《{<][^)\]】〉》}>]*[)\]】〉》}>]")
_SPEAKER = re.compile(r"^[^\s:]{1,12}:")
_PUNCT = re.compile(r"[\s、。,.!?「」『』…・〜~\-—―:;\"'“”‘’♪＃#*_]+")


def normalize_transcript(text):
    """字幕と認識結果を比べられる形にする。

    全角半角・大小文字を揃え、話者名 (「田中:」)・効果音 ((拍手) [笑]) と句読点を落とす。"""
    t = unicodedata.normalize("NFKC", text or "")
    t = _BRACKETS.sub("", t)
    t = "".join(_SPEAKER.sub("", line.strip()) for line in t.splitlines())
    return _PUNCT.sub("", t.lower())


def edit_distance(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference, hypothesis):
    """文字誤り率 (Character Error Rate)。日本語は単語境界が無いので WER ではなく CER で測る。"""
    r, h = normalize_transcript(reference), normalize_transcript(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    return edit_distance(r, h) / len(r)


_TERM = re.compile(r"[ァ-ヴー]{3,}|[一-龥]{2,}|[A-Za-z][A-Za-z0-9]{2,}")


def missed_terms(reference, hypothesis):
    """字幕にあって認識結果に無い語 (カタカナ語・漢字語・英単語) を返す。
    認識の苦手語彙として、次回からの認識プロンプトに混ぜる。"""
    h = normalize_transcript(hypothesis)
    ref = unicodedata.normalize("NFKC", reference or "")
    return [t for t in dict.fromkeys(_TERM.findall(ref)) if t.lower() not in h]
