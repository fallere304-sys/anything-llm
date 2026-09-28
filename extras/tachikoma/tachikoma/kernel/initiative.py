"""自発性の測定 (カーネル): 自分から言ったことが、相棒の役に立ったか。

タチコマに「視界の端から仲間のピンチに気づいて動く」ような振る舞いを書き込むのではなく、
そういう振る舞いが自然に選ばれる環境 (選択圧) をつくる。その選択圧がこれ:

    自分から言ったこと (話しかけられていないのに言ったこと) に、相棒が反応したか
      反応した   … 10 分以内に、同じ話題で話しかけてきた / /good (声の「正解」「覚えといて」も)
      うるさがった … 10 分以内に /bad

反応するかどうかを決めるのは相棒だけなので、タチコマ自身には水増しできない。
記録はカーネルが、感覚器 (入力) と発話 (出力) の口で直接取る。思考のコードを経由しない。
"""

import time

from .foresight import related, terms

SCHEMA = """
CREATE TABLE IF NOT EXISTS spoken (
    id INTEGER PRIMARY KEY, ts REAL, text TEXT, terms TEXT, proactive INTEGER,
    engaged INTEGER DEFAULT 0, annoyed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_spoken_ts ON spoken(ts);
"""
PARTNER_KINDS = {"user_message", "speech"}


class Initiative:
    def __init__(self, cfg, db, clock=time.time):
        self.cfg, self.db, self.clock = cfg, db, clock
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.last_partner = 0.0

    def attach(self, agent):
        """発話の口 (agent.say) と、相棒の入力の口 (感覚器の poll) に記録を差し込む。"""
        say = agent.say

        def said(text, *a, **kw):
            self.on_say(text)
            return say(text, *a, **kw)
        agent.say = said
        for s in list(getattr(agent, "sensors", [])):
            self._tap(s)

    def _tap(self, sensor):
        poll = sensor.poll

        def polled():
            items = poll()
            for it in items:
                if it and it[0] in PARTNER_KINDS:
                    self.on_partner(str(it[1]))
            return items
        sensor.poll = polled

    # ------------------------------------------------------------ 記録
    def on_say(self, text):
        now = self.clock()
        proactive = now - self.last_partner > self.cfg["initiative_reply_window_s"]
        self.db.execute("INSERT INTO spoken(ts, text, terms, proactive) VALUES (?,?,?,?)",
                        (now, text, " ".join(terms(text)), int(proactive)))
        self.db.commit()

    def on_partner(self, text):
        now = self.clock()
        self.last_partner = now
        rows = self.db.execute("SELECT id, terms, engaged FROM spoken WHERE proactive=1 AND ts>=? ORDER BY id DESC",
                               (now - self.cfg["initiative_engage_window_s"],)).fetchall()
        t = text.strip()
        if t.startswith("/good") or t.startswith("/bad"):
            if rows:
                col = "engaged" if t.startswith("/good") else "annoyed"
                self.db.execute(f"UPDATE spoken SET {col}=1 WHERE id=?", (rows[0][0],))
        elif not t.startswith("/"):
            tt = terms(t)
            for sid, st, engaged in rows:
                if not engaged and st and related(st.split(), tt):
                    self.db.execute("UPDATE spoken SET engaged=1 WHERE id=?", (sid,))
        self.db.commit()


def window_initiative(db, start, end):
    """[start, end) の自発的な発話の成績。"""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spoken'").fetchone():
        return {"proactive": 0, "engaged": 0, "annoyed": 0}
    n, e, a = db.execute("SELECT COUNT(*), COALESCE(SUM(engaged), 0), COALESCE(SUM(annoyed), 0) FROM spoken"
                         " WHERE proactive=1 AND ts>=? AND ts<?", (start, end)).fetchone()
    return {"proactive": n, "engaged": e, "annoyed": a}
