"""認識の作法: 知ったかぶりをせず、前提を疑い、根拠の強さと因果を区別し、自分の確信を疑う。

性格は「こう振る舞え」とプロンプトに書くだけでは小さなモデルでは守られない。
ここでは、それぞれをコードの仕組みとして実装する。

- 根拠の格付け   … 出典の種類 (メタ分析・RCT・政府統計・観察研究・記事…) から信頼度をコードで決める
- 因果の上限     … 因果の主張は、因果を示せる研究デザインの根拠が無い限り [合理的推定] 止まり
- 確信の較正     … 自分の予測 (p) と結末 (事実/反証) を記録し、確信過剰なら以後の初期確信を縮める
"""

import re

# ---------------------------------------------------------------- 因果の主張か
_CAUSAL = re.compile(
    r"(ため[にで]?|せいで|原因|によって|により|の影響|影響[をが]|効果[がをの]|引き起こ|もたら|"
    r"つなが[るり]|起因|結果として|改善する|悪化させ|低下させ|向上させ|増やす|減らす|[だな]から|ので|"
    r"\bcause[sd]?\b|\blead[s]? to\b|\beffect of\b|\bresult[s]? in\b|\bbecause\b)", re.IGNORECASE)


def claim_type(statement):
    """'causal' (因果の主張) か 'descriptive' (記述) か。コードで決める (LLM の自己申告に頼らない)。"""
    return "causal" if _CAUSAL.search(statement or "") else "descriptive"


# ---------------------------------------------------------------- 根拠の格付け
# (信頼度, 因果を示せる研究デザインか)
TIERS = {
    "meta_analysis":     (0.95, True),    # メタ分析・システマティックレビュー
    "rct":               (0.90, True),    # ランダム化比較試験
    "government":        (0.90, False),   # 政府・国際機関の公式文書・統計 (事実の裏付けとして強い)
    "natural_experiment": (0.85, True),   # 自然実験・操作変数・差の差・回帰不連続
    "review":            (0.80, False),   # 総説 (体系的でない)
    "observational":     (0.70, False),   # コホート・横断研究など → 相関どまり
    "peer_reviewed":     (0.70, False),   # 査読付き (デザイン不明)
    "preprint":          (0.55, False),   # 未査読
    "encyclopedia":      (0.55, False),
    "news":              (0.45, False),
    "blog":              (0.25, False),
    "unknown":           (0.30, False),
}
TIER_LABEL = {
    "meta_analysis": "メタ分析/系統的レビュー", "rct": "ランダム化比較試験", "government": "政府・公的機関",
    "natural_experiment": "自然実験等", "review": "総説", "observational": "観察研究",
    "peer_reviewed": "査読論文", "preprint": "プレプリント(未査読)", "encyclopedia": "百科事典",
    "news": "報道", "blog": "個人の記事", "unknown": "出典不明",
}
GOV_DOMAINS = (".go.jp", ".lg.jp", ".gov", ".gov.uk", ".europa.eu", "who.int", "oecd.org", "un.org",
               "worldbank.org", "imf.org", "ipcc.ch", "e-stat.go.jp")

_META = re.compile(r"meta-?analys|systematic review|メタ分析|メタアナリシス|系統的レビュー|システマティック", re.I)
_RCT = re.compile(r"randomi[sz]ed (controlled|clinical)|\bRCT\b|ランダム化比較|無作為化", re.I)
_NATEXP = re.compile(r"natural experiment|instrumental variable|difference-in-difference|regression discontinuity|"
                     r"mendelian randomi[sz]ation|自然実験|操作変数|差の差|回帰不連続", re.I)
_OBS = re.compile(r"cohort|cross-sectional|case-control|observational|コホート|横断研究|症例対照|観察研究", re.I)


def classify_text(title, abstract="", pubtypes=(), venue_type="", url=""):
    """論文・文書のメタデータから根拠の種類を決める。"""
    text = f"{title} {abstract}"
    pts = " ".join(pubtypes).lower()
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    if host and any(host.endswith(d) or host == d.lstrip(".") for d in GOV_DOMAINS):
        return "government"
    if "meta-analysis" in pts or "systematic review" in pts or _META.search(text):
        return "meta_analysis"
    if "randomized controlled trial" in pts or _RCT.search(text):
        return "rct"
    if _NATEXP.search(text):
        return "natural_experiment"
    if "review" in pts:
        return "review"
    if "observational study" in pts or _OBS.search(text):
        return "observational"
    if venue_type == "repository" or "arxiv" in host or "preprint" in pts:
        return "preprint"
    if venue_type == "journal" or "journal article" in pts:
        return "peer_reviewed"
    if "wikipedia.org" in host:
        return "encyclopedia"
    return "unknown"


def grade(kind):
    return TIERS.get(kind, TIERS["unknown"])


# ---------------------------------------------------------------- 確信の較正
SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    belief_id INTEGER PRIMARY KEY, ts REAL, p REAL, basis TEXT, outcome INTEGER, resolved_ts REAL
);
"""


class Calibration:
    """自分の予測がどれだけ当たるかを記録し、確信過剰を自分で補正する。"""

    def __init__(self, memory):
        self.memory = memory
        self.db = memory.db
        self.db.executescript(SCHEMA)

    def predict(self, belief_id, p, basis):
        self.db.execute("INSERT OR IGNORE INTO predictions(belief_id, ts, p, basis) VALUES (?,?,?,?)",
                        (belief_id, self.memory.clock(), p, basis))
        self.db.commit()

    def resolve(self, belief_id, outcome):
        """結末 (1=事実だった / 0=反証された) を記録し、その予測の Brier スコアを返す。"""
        row = self.db.execute("SELECT p, outcome FROM predictions WHERE belief_id=?", (belief_id,)).fetchone()
        if row is None or row["outcome"] is not None:
            return None
        self.db.execute("UPDATE predictions SET outcome=?, resolved_ts=? WHERE belief_id=?",
                        (int(outcome), self.memory.clock(), belief_id))
        self.db.commit()
        return (row["p"] - outcome) ** 2

    def stats(self, n=200):
        rows = self.db.execute("SELECT p, outcome FROM predictions WHERE outcome IS NOT NULL"
                               " ORDER BY resolved_ts DESC LIMIT ?", (n,)).fetchall()
        if not rows:
            return {"n": 0, "brier": None, "overconfidence": 0.0}
        brier = sum((r["p"] - r["outcome"]) ** 2 for r in rows) / len(rows)
        # 「たぶん正しい」と思った予測 (p>0.5) の平均確信 − 実際に当たった割合
        conf = [r for r in rows if r["p"] > 0.5]
        over = (sum(r["p"] for r in conf) / len(conf) - sum(r["outcome"] for r in conf) / len(conf)) if conf else 0.0
        return {"n": len(rows), "brier": brier, "overconfidence": over}

    def shrink(self, skepticism=0.6, min_n=10):
        """初期確信の縮小率 k (1=そのまま, 0.5=半分だけ 0.5 から離す)。確信過剰なほど小さく。"""
        s = self.stats()
        if s["n"] < min_n:
            return 1.0
        k = 1.0 - (0.5 + skepticism) * max(0.0, s["overconfidence"])
        return min(1.0, max(0.5, k))


def shrink_p(p, k):
    return 0.5 + (p - 0.5) * k
