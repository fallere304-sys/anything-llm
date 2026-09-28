"""深掘り: 視界の端の情報にも疑問を持ち、調べ、わかったことからまた疑問を作る。

    気になる   周辺の出来事 (聞こえてきたテレビ・ニュース・目の端に映るもの・背後のウィンドウ…) のうち、
               興味 (新しさ × 後で役立ちそうか) が高いものに問いを立てる
    掘る       問いを調べ → わかったことから次の問いを作り → また調べる。1 本の「探究の糸」として続ける
    見切る     掘っても減らせる不確実性が、ふだんの調べもの 1 回あたりの平均を下回ったらやめる
               (最適採餌の限界値定理: 餌場の収穫率が環境の平均を下回ったら移る)

これは出発点にすぎない。掘って知ったことを誰かと結びつけたり、それをもとに動いたりする振る舞いは
ここには書かない。そういう振る舞いは、自己進化 (思考のコードの改良・プラグイン) が
「自分から言ったことが相棒の役に立ったか」という選択圧の下で、自分で身につけるもの
(攻殻機動隊のタチコマも、はじめからそうできたわけではない)。
"""

from . import prompts
from .memory import FACT, INFERENCE, REFUTED, SPECULATION
from .text import clip

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY, seed TEXT, origin TEXT, root_bid INTEGER, state TEXT, interest REAL,
    created REAL, updated REAL, steps INTEGER DEFAULT 0, calls INTEGER DEFAULT 0, gain_ema REAL,
    bits REAL DEFAULT 0, note TEXT
);
CREATE TABLE IF NOT EXISTS thread_beliefs (thread_id INTEGER, belief_id INTEGER, PRIMARY KEY (thread_id, belief_id));
"""


class Inquiry:
    def __init__(self, agent):
        self.a = agent
        self.db = agent.memory.db
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------ 記録
    def _set(self, tid, **kw):
        kw["updated"] = self.a.clock()
        self.db.execute(f"UPDATE threads SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), tid))
        self.db.commit()

    def active(self):
        return self.db.execute("SELECT * FROM threads WHERE state='digging' ORDER BY gain_ema DESC, id").fetchall()

    def recent(self, n=5):
        return self.db.execute("SELECT * FROM threads ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def beliefs(self, tid):
        ids = [r[0] for r in self.db.execute("SELECT belief_id FROM thread_beliefs WHERE thread_id=?", (tid,))]
        return [b for b in (self.a.memory.get_belief(i) for i in ids) if b is not None]

    def _link(self, tid, statement, origin):
        bid = self.a._hypothesis(statement, 0.5, relevance=self.a.cfg["dig_relevance"], basis="dig", origin=origin)
        self.db.execute("INSERT OR IGNORE INTO thread_beliefs(thread_id, belief_id) VALUES (?, ?)", (tid, bid))
        self.db.commit()
        return bid

    # ------------------------------------------------------------ 気になる
    def open(self, question, seed, origin, interest):
        """周辺の出来事に問いを立て、探究の糸を始める。糸が多すぎるときは、今ある最も弱い糸より面白い場合だけ。"""
        question = (question or "").strip()
        if not question:
            return None
        act = self.active()
        if len(act) >= self.a.cfg["dig_max_threads"]:
            weakest = min(act, key=lambda t: t["interest"] or 0)
            if (weakest["interest"] or 0) >= interest:
                return None
            self._close(weakest, "もっと気になることができた")
        now = self.a.clock()
        cur = self.db.execute("INSERT INTO threads(seed, origin, state, interest, created, updated, gain_ema)"
                              " VALUES (?,?,?,?,?,?,?)", (seed, origin, "digging", interest, now, now, self.a.gain_rate))
        tid = cur.lastrowid
        self._set(tid, root_bid=self._link(tid, question, origin))
        self.a.log(f"視界の端が気になる ({interest:.2f}): 「{clip(seed, 50)}」→ 問い「{question}」")
        return tid

    # ------------------------------------------------------------ 掘る / 見切る
    def step(self):
        """毎 tick。LLM を使ったら True。"""
        for th in self.active():
            if self._advance(th):
                self.db.execute("UPDATE threads SET calls=calls+1 WHERE id=?", (th["id"],))
                self.db.commit()
                return True
        return False

    def _advance(self, th):
        cfg = self.a.cfg
        if th["calls"] >= 2 * cfg["dig_max_steps"]:
            return self._close(th, "考えすぎた")
        if th["steps"] >= cfg["dig_min_steps"] and (
                th["gain_ema"] < cfg["dig_leave_ratio"] * self.a.gain_rate or th["steps"] >= cfg["dig_max_steps"]):
            return self._close(th, "掘っても新しくわかることが減ってきた")
        bs = self.beliefs(th["id"])
        target = self._open_question(bs)
        if target is None:
            return self._wonder(th, bs) or self._close(th, "問いが尽きた")
        return self._dig(th, target)

    def _open_question(self, bs):
        """次に調べる問い: [低確度仮説] のうち、いちばんわからないもの (推定まで行けば次の問いへ進む)。"""
        open_q = [b for b in bs if b.label() == SPECULATION and not b.irreducible
                  and b.attempts < self.a.cfg["max_probe_attempts"]
                  and b.id not in (self.a.asked, self.a.question_outstanding)]
        open_q.sort(key=lambda b: abs(0.5 - b.p_now()))
        return open_q[0] if open_q else None

    def _dig(self, th, target):
        allowed = [p for p in self.a.allowed_probes() if p != "ask_user"]   # 端の話で相棒の手を止めない
        _, _, gain = self.a.investigate(target, th["interest"] or 0.5, allowed=allowed,
                                        situation=f"周辺で気になったこと: {clip(th['seed'], 200)}")
        ema = gain if th["steps"] == 0 else 0.5 * (th["gain_ema"] or 0) + 0.5 * gain
        self._set(th["id"], steps=th["steps"] + 1, gain_ema=ema, bits=(th["bits"] or 0) + gain)
        return True

    def _wonder(self, th, bs):
        """掘ってわかったことから、次の問いを作る。何もわかっていなければ False。"""
        known = [b for b in bs if b.label() in (FACT, INFERENCE, REFUTED)]
        if not known:
            return False
        res = self.a.llm.chat(prompts.WONDER_SYSTEM, f"# きっかけ\n{clip(th['seed'], 200)}\n\n# わかったこと\n"
                              + "\n".join(f"- [{b.label()}] {b.statement}" for b in known[-4:]),
                              schema=prompts.WONDER_SCHEMA, max_tokens=200, temperature=0.8)
        made = 0
        for h in (res.get("hypotheses") or [])[:2]:
            if (h or "").strip():
                b = self.a.memory.get_belief(self._link(th["id"], h.strip(), th["origin"]))
                made += b is not None and b.label() == SPECULATION     # 既に知っていることの言い直しは数えない
        if made:
            self.a.selfm.bump("questions", made)
        else:
            self._close(th, "新しい問いが浮かばない")
        return True                                                     # LLM は使った

    def _close(self, th, why):
        learned = [b for b in self.beliefs(th["id"]) if b.label() in (FACT, INFERENCE, REFUTED)]
        self._set(th["id"], state="closed", note=why)
        self.a.log(f"深掘りを終える: 「{clip(th['seed'], 40)}」({why}。{th['steps']} 歩、わかったこと {len(learned)} 件)")
        if learned:
            self.a.selfm.remember(f"周りで気になったことを掘って知った: {clip(learned[-1].statement, 60)}", "discovery")
        return False
