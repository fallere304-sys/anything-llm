"""先見の帳簿 (カーネル): 拾った情報が、あとで本当に役に立ったかを記録する。

目の前と関係なさそうな情報 (ニュース・他人の会話・ウィンドウ名…) も含めて、感覚器が拾った
出来事をすべて帳簿に載せる。あとで利用者自身の行動 (話しかけ・編集・端末・コピー・見ている画面)
に同じ話題が出てきたら「役に立った」と印を付ける。

    役に立った割合 (出どころ別・話題別) → 情報の優先度の「タネ」(思考のコードが読むだけ)
    拾ったときに付けた優先度 × 後で役立ったか → 先見の成績 (AUC) → 自己進化の目標

判定はここ (カーネル) で行い、思考のコードからは書き換えられない。思考のコードが自分の優先度を
よく見せるために「役に立った」を水増しできると、進化が評価をごまかす方向に進むため。
話題の抽出も、思考側の text.py に頼らずここで独立に持つ。
"""

import math
import re
import time
import unicodedata

SCHEMA = """
CREATE TABLE IF NOT EXISTS info_items (
    id INTEGER PRIMARY KEY, event_id INTEGER UNIQUE, ts REAL, bucket TEXT, terms TEXT,
    priority REAL, used_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_info_items_ts ON info_items(ts);
CREATE TABLE IF NOT EXISTS foresight_kv (k TEXT PRIMARY KEY, v REAL);
"""

# 利用者自身の行動 = 「必要とされた」ことの観測。ここに出た話題を、前に拾っていたら役に立ったとみなす
DEMAND_SOURCES = {"user", "voice", "files", "terminal", "clipboard", "window"}

_TERM = re.compile(r"[ァ-ヴー]{3,}|[一-龥]{2,}|[a-z][a-z0-9_+#.-]{2,}")
_STOP = {"する", "こと", "もの", "ため", "よう", "これ", "それ", "作成", "編集", "カメラ", "ユーザー",
         "the", "and", "for", "with", "this", "that", "from", "you", "are", "was", "not", "but",
         "def", "self", "return", "import", "none", "true", "false", "https", "http", "www", "com"}


def terms(text, limit=12):
    """話題の語 (カタカナ語・漢字語・英単語)。出現順に最大 limit 個。"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    out = []
    for w in _TERM.findall(t):
        w = w.strip(".-")
        if len(w) >= 2 and w not in _STOP and w not in out:
            out.append(w)
            if len(out) >= limit:
                break
    return out


def related(item_terms, demand_terms):
    """同じ話題か: 2 語以上が共通、または 4 文字以上の固有らしい語が 1 つ共通。"""
    common = set(item_terms) & set(demand_terms)
    return len(common) >= 2 or any(len(w) >= 4 for w in common)


def auc(pairs):
    """(優先度, 役立ったか) の組から AUC。役立った情報に高い優先度を付けられていたほど 1 に近い。"""
    pos = [p for p, y in pairs if y]
    neg = [p for p, y in pairs if not y]
    if len(pos) < 3 or len(neg) < 3:
        return None
    ranked = sorted(pairs, key=lambda x: x[0])
    ranks, i = {}, 0
    while i < len(ranked):           # 同点は平均順位
        j = i
        while j + 1 < len(ranked) and ranked[j + 1][0] == ranked[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    r_pos = sum(ranks[k] for k, (_, y) in enumerate(ranked) if y)
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


class Foresight:
    def __init__(self, cfg, db, clock=time.time):
        self.cfg, self.db, self.clock = cfg, db, clock
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._last_sync = 0.0
        self._cache = {}             # 記帳のたびに作り直す (出来事のたびに集計しない)

    def _get(self, k, default=0.0):
        row = self.db.execute("SELECT v FROM foresight_kv WHERE k=?", (k,)).fetchone()
        return row[0] if row else default

    def _set(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO foresight_kv(k, v) VALUES (?, ?)", (k, v))

    # ------------------------------------------------------------ 記帳
    def sync(self, force=False):
        """新しい出来事を帳簿に載せ、利用者の行動と照らして「役に立った」を付ける。"""
        now = self.clock()
        if not force and now - self._last_sync < self.cfg["foresight_sync_s"]:
            return 0
        self._last_sync = now
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            return 0
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
        prio = "priority" if "priority" in cols else "NULL"
        last = int(self._get("last_event_id"))
        rows = self.db.execute(
            f"SELECT id, ts, source, kind, content, novelty, {prio} FROM events WHERE id>? AND source!='self'"
            " ORDER BY id LIMIT 2000", (last,)).fetchall()
        horizon, gap = self.cfg["foresight_horizon_s"], self.cfg["foresight_min_gap_s"]
        for r in rows:
            eid, ts, source, kind, content, novelty, priority = r
            tt = terms(content)
            if source in DEMAND_SOURCES and tt:
                self._mark_used(tt, ts, ts - horizon, ts - gap)
            if kind != "user_message":
                # 相棒からの話しかけは「拾った情報」ではなく「求め」なので帳簿に載せない
                self.db.execute(
                    "INSERT OR IGNORE INTO info_items(event_id, ts, bucket, terms, priority) VALUES (?,?,?,?,?)",
                    (eid, ts, f"{source}/{kind}", " ".join(tt), novelty if priority is None else priority))
            last = eid
        self._set("last_event_id", last)
        # 帳簿は 60 日で捨てる (話題の見込みは直近 30 日、成績は試用期間の分しか使わない)
        self.db.execute("DELETE FROM info_items WHERE ts < ?", (now - 60 * 86400,))
        self._cache = {}
        self.db.commit()
        return len(rows)

    def _mark_used(self, demand_terms, ts, since, until):
        cands = self.db.execute(
            "SELECT id, terms FROM info_items WHERE used_ts IS NULL AND ts>=? AND ts<=? ORDER BY id DESC LIMIT 3000",
            (since, until)).fetchall()
        for iid, t in cands:
            it = (t or "").split()
            if it and related(it, demand_terms):
                self.db.execute("UPDATE info_items SET used_ts=? WHERE id=?", (ts, iid))

    # ------------------------------------------------------------ 読み出し (思考のコードに渡すのはここだけ)
    def bucket_stats(self):
        """出どころ別の (役立った数, 判定済みの数)。判定済み = 役立った、または期限まで使われなかった。"""
        if "buckets" in self._cache:
            return self._cache["buckets"]
        mature = self.clock() - self.cfg["foresight_horizon_s"]
        rows = self.db.execute(
            "SELECT bucket, SUM(used_ts IS NOT NULL), SUM(used_ts IS NOT NULL OR ts<?) FROM info_items GROUP BY bucket",
            (mature,)).fetchall()
        self._cache["buckets"] = {b: (int(u or 0), int(n or 0)) for b, u, n in rows}
        return self._cache["buckets"]

    def term_stats(self, term):
        """その話題の語を含む情報の (役立った数, 判定済みの数)。直近 30 日分。"""
        key = "term:" + term
        if key in self._cache:
            return self._cache[key]
        now = self.clock()
        row = self.db.execute(
            "SELECT SUM(used_ts IS NOT NULL), SUM(used_ts IS NOT NULL OR ts<?) FROM info_items"
            " WHERE ts>=? AND (' ' || terms || ' ') LIKE ?",
            (now - self.cfg["foresight_horizon_s"], now - 30 * 86400, f"% {term} %")).fetchone()
        self._cache[key] = (int(row[0] or 0), int(row[1] or 0))
        return self._cache[key]

    def usefulness(self, bucket, text=None):
        """その出どころ (と話題) の情報が後で役立つ見込み (0-1)。

        出どころの見込みは UCB: 試したことの少ない出どころは楽観的に高くして、ノイズに見える情報にも
        一度は目を向けさせる。話題の見込みは、その語を含む情報が役立った割合 (わからなければ出どころの平均)。"""
        st = self.bucket_stats()
        total = sum(n for _, n in st.values()) + 1
        used, n = st.get(bucket, (0, 0))
        mean = (used + 1) / (n + 2)
        ucb = min(1.0, mean + self.cfg["foresight_explore"] * math.sqrt(math.log(total + 1) / (n + 1)))
        if not text:
            return ucb
        best = None
        for w in terms(text):
            u, m = self.term_stats(w)
            if m:
                r = (u + 1) / (m + 2)
                best = r if best is None else max(best, r)
        return 0.5 * ucb + 0.5 * (mean if best is None else best)

    def rates(self):
        """/foresight 用: 出どころ別の役立った割合。"""
        return {b: {"used": u, "n": n, "rate": (u + 1) / (n + 2)} for b, (u, n) in self.bucket_stats().items()}


def window_auc(db, start, end, horizon):
    """[start, end - horizon) に拾った情報について、horizon 以内に役立ったかと優先度の AUC。"""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='info_items'").fetchone():
        return None
    rows = db.execute("SELECT priority, used_ts, ts FROM info_items WHERE ts>=? AND ts<?",
                      (start, end - horizon)).fetchall()
    if len(rows) < 30:
        return None
    return auc([(p or 0.0, u is not None and u - t <= horizon) for p, u, t in rows])
