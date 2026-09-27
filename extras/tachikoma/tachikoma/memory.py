"""記憶: 出来事 (events) と信念 (beliefs) を SQLite に保持する。

信念は「命題 + 確率 p + 出所」で表し、ラベル
    [観測事実] / [合理的推定] / [低確度仮説]
は p と出所から *コードで* 決める。LLM に自己申告させないのが肝。
"""

import json
import math
import sqlite3
import time

from .epistemics import claim_type
from .text import jaccard, overlap

FACT, INFERENCE, SPECULATION, REFUTED = "観測事実", "合理的推定", "低確度仮説", "反証済み"

# 出所ごとの信頼の上限。LLM の内省だけでは事実に到達できない。
SOURCE_CAP = {
    "observation": 0.99,   # ファイル・ログ・画面など直接観測
    "user": 0.99,          # ユーザーの明示的な回答
    "memory": 0.85,        # 過去の記憶からの想起
    "web": 0.85,           # ネット情報。世界の一般知識の裏付けにはなるが、目の前の事実にはならない
    "research": 0.9,       # 論文・政府文書。強い根拠だが「自分で観測した事実」ではないので [合理的推定] 止まり
    "reflection": 0.75,    # LLM の推論のみ
}

# 揮発性: 何秒で確信が半減する (0.5 に戻る) か。状況は時間とともに古くなる。
DEFAULT_HALF_LIFE = 6 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts REAL, source TEXT, kind TEXT,
    content TEXT, novelty REAL DEFAULT 0, appraised INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS beliefs (
    id INTEGER PRIMARY KEY, statement TEXT, p REAL, source TEXT,
    relevance REAL, created REAL, updated REAL, verified REAL,
    half_life REAL, attempts INTEGER DEFAULT 0, irreducible INTEGER DEFAULT 0,
    evidence TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS utterances (
    id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT,
    value REAL, belief_id INTEGER, delivered INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def entropy(p):
    """二値エントロピー (bit)。p=0.5 で最大 1、確信するほど 0 に近づく。"""
    if p <= 0 or p >= 1:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


# 因果の主張の上限: 因果を示せる研究デザイン (RCT・メタ分析・自然実験) の根拠が無い限り、
# 何度「そうらしい」と確かめても相関の域を出ないので [観測事実] にはしない
CAUSAL_CAP = 0.8
MIGRATIONS = (("claim_type", "TEXT DEFAULT 'descriptive'"), ("causal_ok", "INTEGER DEFAULT 0"),
              ("challenged", "INTEGER DEFAULT 0"), ("promised", "INTEGER DEFAULT 0"))


class Belief:
    __slots__ = ("id", "statement", "p", "source", "relevance", "created", "updated",
                 "verified", "half_life", "attempts", "irreducible", "evidence",
                 "claim_type", "causal_ok", "challenged", "promised", "clock")

    def __init__(self, row, clock=time.time):
        for k in self.__slots__[:-1]:
            setattr(self, k, row[k])
        self.clock = clock
        self.evidence = json.loads(self.evidence or "[]")

    def p_now(self, now=None):
        """時間経過で確信を 0.5 へ減衰させた実効確率。"""
        now = now or self.clock()
        age = max(0.0, now - (self.verified or self.created))
        return 0.5 + (self.p - 0.5) * 0.5 ** (age / (self.half_life or DEFAULT_HALF_LIFE))

    def label(self, now=None):
        p = self.p_now(now)
        if p <= 0.1:
            return REFUTED
        if p >= 0.9 and self.source in ("observation", "user"):
            return FACT
        if p >= 0.7:
            return INFERENCE
        return SPECULATION

    def __repr__(self):
        return f"[{self.label()}] {self.statement} (p={self.p_now():.2f})"


class Memory:
    def __init__(self, path=":memory:", clock=time.time):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(beliefs)")}
        for name, decl in MIGRATIONS:
            if name not in cols:
                self.db.execute(f"ALTER TABLE beliefs ADD COLUMN {name} {decl}")
        self.db.commit()
        self.clock = clock

    # ---------- events ----------
    def add_event(self, source, kind, content, novelty=0.0):
        cur = self.db.execute(
            "INSERT INTO events(ts, source, kind, content, novelty, appraised) VALUES (?,?,?,?,?,?)",
            (self.clock(), source, kind, content, novelty, 1 if source == "self" else 0))
        self.db.commit()
        return cur.lastrowid

    def recent_events(self, limit=20, source=None):
        q, args = "SELECT * FROM events", []
        if source:
            q, args = q + " WHERE source=?", [source]
        rows = self.db.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
        return list(reversed(rows))

    def novelty(self, source, content, lookback=30):
        """同じ感覚器の直近の出来事とどれだけ違うか (1=完全に新しい)。"""
        prev = self.recent_events(lookback, source)
        if not prev:
            return 1.0
        return 1.0 - max(jaccard(content, r["content"]) for r in prev)

    def pending_events(self):
        return self.db.execute(
            "SELECT * FROM events WHERE appraised=0 ORDER BY novelty DESC, id DESC").fetchall()

    def mark_appraised(self, event_id, value=1):
        self.db.execute("UPDATE events SET appraised=? WHERE id=?", (value, event_id))
        self.db.commit()

    def search_events(self, query, k=5, min_score=0.2):
        """自分の発話 (source=self) は根拠にしない。自作自演で確信が上がるのを防ぐ。"""
        rows = self.db.execute(
            "SELECT * FROM events WHERE source != 'self' ORDER BY id DESC LIMIT 2000").fetchall()
        scored = [(overlap(query, r["content"]), r) for r in rows]
        scored = [s for s in scored if s[0] >= min_score]
        scored.sort(key=lambda s: -s[0])
        return [r for _, r in scored[:k]]

    # ---------- beliefs ----------
    def get_belief(self, bid):
        row = self.db.execute("SELECT * FROM beliefs WHERE id=?", (bid,)).fetchone()
        return Belief(row, self.clock) if row else None

    def beliefs(self, include_irreducible=True):
        q = "SELECT * FROM beliefs"
        if not include_irreducible:
            q += " WHERE irreducible=0"
        return [Belief(r, self.clock) for r in self.db.execute(q).fetchall()]

    def find_similar(self, statement, threshold=0.6):
        best, best_s = None, threshold
        for b in self.beliefs():
            s = jaccard(statement, b.statement)
            if s >= best_s:
                best, best_s = b, s
        return best

    def add_belief(self, statement, p, source, relevance=1.0, half_life=DEFAULT_HALF_LIFE,
                   evidence=None):
        """似た信念があれば新しい根拠として統合し、なければ追加する。"""
        same = self.find_similar(statement)
        if same:
            self.update_belief(same.id, logit(p) - logit(0.5), source, evidence,
                               relevance=max(same.relevance, relevance))
            return same.id
        now = self.clock()
        kind = claim_type(statement)
        cap = SOURCE_CAP.get(source, 0.75)
        if kind == "causal":
            cap = min(cap, CAUSAL_CAP)
        p = min(p, cap)
        cur = self.db.execute(
            "INSERT INTO beliefs(statement,p,source,relevance,created,updated,verified,half_life,evidence,"
            "claim_type) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (statement, p, source, relevance, now, now, now, half_life,
             json.dumps([evidence] if evidence else [], ensure_ascii=False), kind))
        self.db.commit()
        return cur.lastrowid

    def update_belief(self, bid, delta_logit, source, evidence=None, relevance=None,
                      count_attempt=False, causal_evidence=False):
        """対数オッズに根拠の重みを足す (ナイーブベイズ的更新)。

        上限は「これまでに得た最も強い出所」で決まる。内省を何度重ねても
        observation 級の確信には届かない。因果の主張は、因果を示せる研究デザインの
        根拠 (causal_evidence=True) を一度でも得るまで CAUSAL_CAP 止まり。"""
        b = self.get_belief(bid)
        if not b:
            return None
        now = self.clock()
        rank = ["reflection", "web", "memory", "research", "observation", "user"]
        best_source = max([b.source, source], key=lambda s: rank.index(s) if s in rank else 0)
        cap = SOURCE_CAP.get(best_source, 0.75)
        causal_ok = bool(b.causal_ok) or bool(causal_evidence)
        if b.claim_type == "causal" and not causal_ok:
            cap = min(cap, CAUSAL_CAP)
        p = sigmoid(logit(b.p_now(now)) + delta_logit)
        p = min(max(p, 1 - cap), cap)
        ev = b.evidence + ([evidence] if evidence else [])
        self.db.execute(
            "UPDATE beliefs SET p=?, source=?, updated=?, verified=?, evidence=?, relevance=?,"
            " attempts=attempts+?, causal_ok=? WHERE id=?",
            (p, best_source, now, now, json.dumps(ev[-8:], ensure_ascii=False),
             b.relevance if relevance is None else relevance, 1 if count_attempt else 0,
             1 if causal_ok else 0, bid))
        self.db.commit()
        return self.get_belief(bid)

    def set_flag(self, bid, flag, value=1):
        """challenged (反証を探した) / promised (ユーザーに調べると約束した) の印。"""
        if flag not in ("challenged", "promised"):
            raise ValueError(flag)
        self.db.execute(f"UPDATE beliefs SET {flag}=? WHERE id=?", (value, bid))
        self.db.commit()

    def mark_irreducible(self, bid):
        self.db.execute("UPDATE beliefs SET irreducible=1 WHERE id=?", (bid,))
        self.db.commit()

    def touch_relevance(self, text, boost=0.5, threshold=0.25):
        """新しい出来事に関係する信念の関連度を引き上げる (注意の移動)。"""
        for b in self.beliefs():
            if overlap(b.statement, text) >= threshold:
                self.db.execute("UPDATE beliefs SET relevance=MIN(1.0, relevance+?) WHERE id=?",
                                (boost, b.id))
        self.db.commit()

    def decay_relevance(self, dt, half_life):
        self.db.execute("UPDATE beliefs SET relevance=relevance*?", (0.5 ** (dt / half_life),))
        self.db.commit()

    # ---------- utterances ----------
    def add_utterance(self, kind, text, value, belief_id=None):
        self.db.execute("INSERT INTO utterances(ts,kind,text,value,belief_id) VALUES (?,?,?,?,?)",
                        (self.clock(), kind, text, value, belief_id))
        self.db.commit()

    def pending_utterances(self):
        return self.db.execute(
            "SELECT * FROM utterances WHERE delivered=0 ORDER BY value DESC").fetchall()

    def mark_delivered(self, uid, value=1):
        self.db.execute("UPDATE utterances SET delivered=? WHERE id=?", (value, uid))
        self.db.commit()

    # ---------- sleep ----------
    def prune(self, retention_days, min_relevance=0.05):
        now = self.clock()
        self.db.execute("DELETE FROM events WHERE ts < ?", (now - retention_days * 86400,))
        removed = 0
        for b in self.beliefs():
            lab = b.label(now)
            if lab == REFUTED or (lab != FACT and b.relevance < min_relevance):
                self.db.execute("DELETE FROM beliefs WHERE id=?", (b.id,))
                removed += 1
        self.db.commit()
        return removed
