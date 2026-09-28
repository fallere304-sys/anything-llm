"""好奇心 = 「目の前の状況についての低確度推定」を減らすための情報収集の駆動力。

    uncertainty(b) = relevance(b) × H(p_now(b)) × learnability(b)

- H は二値エントロピー。p=0.5 (全くわからない) で最大。
- relevance は「目の前」性。新しい出来事で上がり、時間で下がる。
- learnability は「調べれば減るか」。調べても減らない不確実性
  (noisy-TV 問題) に好奇心を吸い尽くされないよう、試行ごとに割り引く。

プローブの選択は期待情報利得 / コストで行う。

周辺への好奇心 (目の前と関係なさそうな情報にも目を向ける):
    relevance を max(relevance, peripheral × 出どころの有用性) に置き換える。
    出どころの有用性は「その感覚器・種類の情報が、あとで相棒の行動に出てきた割合」の楽観的な
    見込み (kernel/foresight.py が記録)。まだよく知らない出どころは高めに見積もるので、
    ノイズに見える情報もいったん調べてみて、役に立たなかった出どころは次第に後回しになる。
"""

from dataclasses import dataclass

from .memory import SPECULATION, INFERENCE, entropy


@dataclass
class ProbeSpec:
    name: str
    description: str
    cost: float          # 相対コスト (GPU 時間・ユーザーへの割り込みなど)
    reliability: float   # 当たれば不確実性をどれだけ潰せるか (0-1)
    source: str          # 得られた根拠の出所ラベル


PROBES = {
    "search_memory": ProbeSpec(
        "search_memory", "過去の出来事や記憶を検索する", 0.2, 0.5, "memory"),
    "grep_workspace": ProbeSpec(
        "grep_workspace", "監視フォルダ内のファイルをキーワードで検索する", 0.4, 0.8, "observation"),
    "read_file": ProbeSpec(
        "read_file", "監視フォルダ内の特定ファイルを読む (query にパス)", 0.3, 0.9, "observation"),
    "wait_observe": ProbeSpec(
        "wait_observe", "何もせず、次の出来事で自然に判明するのを待つ", 0.5, 0.15, "observation"),
    "web_search": ProbeSpec(
        "web_search", "ネットで一般的な知識を調べる (個人的なことは調べられない)", 0.6, 0.6, "web"),
    "research": ProbeSpec(
        "research", "論文・政府文書で調べる (根拠の強さを格付けする)", 0.8, 0.8, "research"),
    "challenge": ProbeSpec(
        "challenge", "反証を探す (論文・ネットで、この仮説に反する根拠を探す)", 0.8, 0.7, "research"),
    "look": ProbeSpec(
        "look", "カメラで今の様子を見る (query に見たい点)", 0.8, 0.7, "observation"),
    "news_search": ProbeSpec(
        "news_search", "ニュースを検索する (出来事の続報・別の報道)", 0.5, 0.7, "web"),
    "ask_user": ProbeSpec(
        "ask_user", "ユーザーに短く質問する (割り込みコストが高い)", 3.0, 1.0, "user"),
}


def learnability(b, max_attempts):
    if b.irreducible:
        return 0.0
    return max(0.0, 1.0 - b.attempts / (max_attempts + 1))


def attention_weight(b, usefulness=None, peripheral=0.0):
    """目の前の関連度と、周辺 (出どころの有用性の見込み) の大きい方。"""
    rel = b.relevance
    origin = getattr(b, "origin", "") or ""
    if usefulness is not None and origin and peripheral > 0:
        rel = max(rel, peripheral * usefulness(origin))
    return rel


def uncertainty(b, max_attempts, now=None, usefulness=None, peripheral=0.0):
    if b.label(now) not in (SPECULATION, INFERENCE):
        return 0.0
    return attention_weight(b, usefulness, peripheral) * entropy(b.p_now(now)) * learnability(b, max_attempts)


def drive(beliefs, max_attempts, now=None, usefulness=None, peripheral=0.0):
    """いま調べたい気持ちの強さ (0-1)。最も気になっている1件の不確実性。"""
    return max((uncertainty(b, max_attempts, now, usefulness, peripheral) for b in beliefs), default=0.0)


def interest(novelty, usefulness, familiarity=0.0, fascination=0.0):
    """出来事への興味 (考える順番と、深掘りするかの基準)。

        興味 = max( 新しさ × (0.4 + 0.6 × 惹かれる度合い),  なじみ )
        惹かれる度合い = max(あとで役立ちそう, なじみのある名前, 自分の興味の話題)

    なじみのある名前 (仲間) は、新しくなくても注意を引く (カクテルパーティー効果:
    聞き流している会話でも自分や知り合いの名前には気づく)。"""
    pull = max(usefulness, familiarity, fascination)
    return max(novelty * (0.4 + 0.6 * pull), familiarity)


def pick_target(beliefs, max_attempts, now=None, usefulness=None, peripheral=0.0):
    scored = [(uncertainty(b, max_attempts, now, usefulness, peripheral), b) for b in beliefs]
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        return None, 0.0
    u, b = max(scored, key=lambda s: s[0])
    return b, u


def probe_value(b, spec, now=None):
    """期待情報利得 / コスト。"""
    return entropy(b.p_now(now)) * spec.reliability * b.relevance / spec.cost


def rank_probes(b, allowed, now=None):
    return sorted((PROBES[n] for n in allowed if n in PROBES),
                  key=lambda s: -probe_value(b, s, now))
