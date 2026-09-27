"""好奇心 = 「目の前の状況についての低確度推定」を減らすための情報収集の駆動力。

    uncertainty(b) = relevance(b) × H(p_now(b)) × learnability(b)

- H は二値エントロピー。p=0.5 (全くわからない) で最大。
- relevance は「目の前」性。新しい出来事で上がり、時間で下がる。
- learnability は「調べれば減るか」。調べても減らない不確実性
  (noisy-TV 問題) に好奇心を吸い尽くされないよう、試行ごとに割り引く。

プローブの選択は期待情報利得 / コストで行う。
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
    "ask_user": ProbeSpec(
        "ask_user", "ユーザーに短く質問する (割り込みコストが高い)", 3.0, 1.0, "user"),
}


def learnability(b, max_attempts):
    if b.irreducible:
        return 0.0
    return max(0.0, 1.0 - b.attempts / (max_attempts + 1))


def uncertainty(b, max_attempts, now=None):
    if b.label(now) not in (SPECULATION, INFERENCE):
        return 0.0
    return b.relevance * entropy(b.p_now(now)) * learnability(b, max_attempts)


def drive(beliefs, max_attempts, now=None):
    """いま調べたい気持ちの強さ (0-1)。最も気になっている1件の不確実性。"""
    return max((uncertainty(b, max_attempts, now) for b in beliefs), default=0.0)


def pick_target(beliefs, max_attempts, now=None):
    scored = [(uncertainty(b, max_attempts, now), b) for b in beliefs]
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
