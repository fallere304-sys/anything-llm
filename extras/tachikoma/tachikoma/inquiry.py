"""深掘り: 視界の端の情報に興味を持ち、掘り下げ、その途中で「これは誰かに関係する」と気づいたら動く。

たとえ話: 攻殻機動隊のタチコマは、視界の端に映っていたテレビのニュースから、かつての仲間のピンチを知り、
自分から情報を集めて行動した。仲間の名前を待ち構えていたのではない。**端の情報にも興味を持って深掘りできた**
から、その途中で仲間とのつながりに気づけた。ここではその順番をそのまま仕組みにする。

    気になる   周辺の出来事 (聞こえてきたテレビ・ニュース・目の端に映るもの・背後のウィンドウ…) のうち、
               興味 (新しさ × 役立ちそう・なじみのある名前・自分の興味) が高いものに、問いを立てる
    掘る       問いを調べ → わかったことから次の問いを作り → また調べる。1 本の「探究の糸」として続ける
    見切る     掘っても減らせる不確実性が、ふだんの調べもの 1 回あたりの平均を下回ったら、やめて次へ行く
               (最適採餌の限界値定理: 餌場の収穫率が環境の平均を下回ったら移る)
    気づく     ときどき立ち止まって「わかってきたこと」が、相棒・仲間・自分の関心に関係するかを考える
    動く       関係するなら深掘りの予算を増やして裏付けを取り、できる行動をして相棒に知らせる

関係しない深掘りは黙って終わる (知識として残り、あとで役立ったかは先見の帳簿が記録する)。
行動の範囲は、知らせる・読み取りだけの調査・続報の見張り・下書き・提案。外に何かを送る・買う・投稿することはしない。
"""

import json
import re

from . import prompts
from .memory import FACT, INFERENCE, REFUTED, SPECULATION
from .text import clip, site

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY, seed TEXT, origin TEXT, link TEXT, root_bid INTEGER, state TEXT,
    interest REAL, created REAL, updated REAL, steps INTEGER DEFAULT 0, checked INTEGER DEFAULT 0,
    gain_ema REAL, bits REAL DEFAULT 0, matters TEXT DEFAULT '', urgent INTEGER DEFAULT 0,
    significant_at REAL, sources TEXT DEFAULT '[]', told INTEGER DEFAULT 0, report TEXT, remind_at REAL,
    calls INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS thread_beliefs (thread_id INTEGER, belief_id INTEGER, PRIMARY KEY (thread_id, belief_id));
"""
ACTIVE = ("digging", "significant")
# どこで目にしたか (相棒への知らせの言い出し)
SEEN_AT = {"news": "ニュース", "overheard_speech": "聞こえてきた話", "glance": "目の端に映ってたもの",
           "background_window": "画面の端のウィンドウ", "window_focus": "画面"}
ACTIONS = {
    "check_workspace": "作業フォルダで関係するところを調べる (detail に探す語)",
    "keep_watching": "しばらく続報を見張る (detail に見張る名前や語)",
    "draft_message": "相棒が誰かに連絡するときの文面を下書きする (detail に文面。送るのは相棒)",
    "remind_later": "あとでもう一度様子を確かめて知らせる",
    "suggest": "相棒にできることを提案する (detail に提案)",
}
_URL = re.compile(r"https?://([^/\s)]+)")


def sites_in(text):
    return {site(d) for d in _URL.findall(text or "")}


class Inquiry:
    def __init__(self, agent, bonds):
        self.a, self.bonds = agent, bonds
        self.db = agent.memory.db
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------ 記録
    def _set(self, tid, **kw):
        kw["updated"] = self.a.clock()
        self.db.execute(f"UPDATE threads SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), tid))
        self.db.commit()

    def get(self, tid):
        return self.db.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()

    def active(self):
        return self.db.execute("SELECT * FROM threads WHERE state IN ('digging','significant')"
                               " ORDER BY state='significant' DESC, urgent DESC, gain_ema DESC, id").fetchall()

    def recent(self, n=5):
        return self.db.execute("SELECT * FROM threads ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def beliefs(self, tid):
        ids = [r[0] for r in self.db.execute("SELECT belief_id FROM thread_beliefs WHERE thread_id=?", (tid,))]
        return [b for b in (self.a.memory.get_belief(i) for i in ids) if b is not None]

    def owns(self, belief_id):
        """深掘りの糸に属する仮説か (個々の「確かめた」は言わず、関係があるとわかったときにまとめて伝える)。"""
        return self.db.execute("SELECT 1 FROM thread_beliefs WHERE belief_id=?", (belief_id,)).fetchone() is not None

    def _link(self, tid, statement, origin, relevance):
        bid = self.a._hypothesis(statement, 0.5, relevance=relevance, basis="dig", origin=origin)
        self.db.execute("INSERT OR IGNORE INTO thread_beliefs(thread_id, belief_id) VALUES (?, ?)", (tid, bid))
        self.db.commit()
        return bid

    # ------------------------------------------------------------ 気になる
    def open(self, question, seed, origin, interest, meta=None):
        """周辺の出来事に問いを立て、探究の糸を始める。糸が多すぎるときは、今ある最も弱い糸より面白い場合だけ。"""
        question = (question or "").strip()
        if not question:
            return None
        act = self.active()
        if len(act) >= self.a.cfg["dig_max_threads"]:
            weakest = min((t for t in act if t["state"] == "digging"), key=lambda t: t["interest"] or 0, default=None)
            if weakest is None or (weakest["interest"] or 0) >= interest:
                return None
            self._close(weakest, "もっと気になることができた")
        now = self.a.clock()
        meta = meta or {}
        cur = self.db.execute(
            "INSERT INTO threads(seed, origin, link, state, interest, created, updated, gain_ema, sources)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (seed, origin, meta.get("link", ""), "digging", interest, now, now, self.a.gain_rate,
             json.dumps([meta["source"]] if meta.get("source") else [])))
        tid = cur.lastrowid
        bid = self._link(tid, question, origin, self.a.cfg["dig_relevance"])
        self._set(tid, root_bid=bid)
        self.a.log(f"視界の端が気になる ({interest:.2f}): 「{clip(seed, 50)}」→ 問い「{question}」")
        return tid

    # ------------------------------------------------------------ 掘る / 見切る / 気づく / 動く
    def step(self):
        """毎 tick。LLM を使ったら True。"""
        self._reminders()
        for th in self.active():
            used = self._advance(th)
            if used:
                self.db.execute("UPDATE threads SET calls=calls+1 WHERE id=?", (th["id"],))
                self.db.commit()
            if used is not None:
                return used
        return False

    def _advance(self, th):
        """その糸を 1 歩進める。LLM を使ったら True、使わずに状態だけ変えたら None 以外の False。"""
        cfg, now = self.a.cfg, self.a.clock()
        bs = self.beliefs(th["id"])
        if th["state"] == "significant":
            for b in bs:
                self.a.memory.set_relevance(b.id, 1.0)       # 関係があるとわかったら、注意を離さない
            best = max(bs, key=lambda b: b.p_now(), default=None)
            if best is None or best.p_now() <= cfg["dig_dismiss_p"] or best.label() == REFUTED:
                self._set(th["id"], state="dismissed")
                if th["told"]:
                    self.a.memory.add_utterance("report", "さっきの話、調べたら違ったみたい。よかった！", 0.8)
                return None
            confirmed = best.label() in (FACT, INFERENCE) and len(json.loads(th["sources"])) >= 2
            if confirmed or th["steps"] - th["checked"] >= cfg["dig_confirm_steps"] \
                    or now - (th["significant_at"] or now) >= cfg["dig_timeout_s"]:
                return self._act(th, bs, best, confirmed)
            target = self._open_question(bs, confirm=True)
            if target is None:
                return self._act(th, bs, best, confirmed)
            return self._dig(th, target)
        # --- digging
        if th["calls"] >= 3 * cfg["dig_max_steps"]:
            self._close(th, "考えすぎた")
            return None
        # 見切る前に、必ず一度は「わかってきたことは誰かに関係するか」を考える (不確かなままでも)
        unreflected = th["checked"] < th["steps"]
        if th["steps"] >= cfg["dig_min_steps"] and (
                th["gain_ema"] < cfg["dig_leave_ratio"] * self.a.gain_rate or th["steps"] >= cfg["dig_max_steps"]):
            if unreflected:
                return self._reflect(th, bs)
            self._close(th, "掘っても新しくわかることが減ってきた")
            return None
        if th["steps"] - th["checked"] >= cfg["dig_check_every"]:
            return self._reflect(th, bs)
        target = self._open_question(bs)
        if target is None:
            if unreflected:
                return self._reflect(th, bs)
            return self._wonder(th, bs) or self._close(th, "問いが尽きた")
        return self._dig(th, target)

    def _open_question(self, bs, confirm=False):
        """次に調べる問い。深掘り中は [低確度仮説] だけ (推定まで行けば次の問いへ進む)、
        誰かに関係するとわかった後は [合理的推定] も裏付けを重ねる。"""
        labels = (SPECULATION, INFERENCE) if confirm else (SPECULATION,)
        # 誰かに関係するとわかったら、同じ問いにかける手間の上限を広げる (「今は確かめられない」で諦めない)
        limit = self.a.cfg["max_probe_attempts"] + (self.a.cfg["dig_confirm_steps"] if confirm else 0)
        open_q = [b for b in bs if b.label() in labels and (confirm or not b.irreducible)
                  and b.attempts < limit and b.id not in (self.a.asked, self.a.question_outstanding)]
        open_q.sort(key=lambda b: abs(0.5 - b.p_now()))          # いちばんわからないものから
        return open_q[0] if open_q else None

    def _dig(self, th, target):
        allowed = [p for p in self.a.allowed_probes() if p != "ask_user"]   # 端の話で相棒の手を止めない
        verdict, evidence, gain = self.a.investigate(target, th["interest"] or 0.5, allowed=allowed,
                                                     situation=f"周辺で気になったこと: {clip(th['seed'], 200)}")
        ema = gain if th["steps"] == 0 else 0.5 * (th["gain_ema"] or 0) + 0.5 * gain
        kw = {"steps": th["steps"] + 1, "gain_ema": ema, "bits": (th["bits"] or 0) + gain}
        if evidence and verdict.get("verdict") in ("supports", "partially_supports"):
            kw["sources"] = json.dumps(sorted(set(json.loads(th["sources"])) | sites_in(evidence)))
        self._set(th["id"], **kw)
        return True

    def _wonder(self, th, bs):
        """掘ってわかったことから、次の問いを作る。"""
        known = [b for b in bs if b.label() in (FACT, INFERENCE, REFUTED)]
        if not known:
            return False
        res = self.a.llm.chat(prompts.WONDER_SYSTEM, f"# きっかけ\n{clip(th['seed'], 200)}\n\n# わかったこと\n"
                              + "\n".join(f"- [{b.label()}] {b.statement}" for b in known[-4:]),
                              schema=prompts.WONDER_SCHEMA, max_tokens=200, temperature=0.8)
        made = 0
        for h in (res.get("hypotheses") or [])[:2]:
            if (h or "").strip():
                bid = self._link(th["id"], h.strip(), th["origin"], self.a.cfg["dig_relevance"])
                b = self.a.memory.get_belief(bid)
                made += b is not None and b.label() == SPECULATION     # 既に知っていることの言い直しは数えない
        if made:
            self.a.selfm.bump("questions", made)
        else:
            self._close(th, "新しい問いが浮かばない")
        return True

    def context_for_reflection(self, bs):
        """相棒・仲間・自分の関心を、深掘りの結果と照らせる形にする。"""
        lines = [f"# 相棒\n{self.a.cfg.get('user_name') or 'キミ'} (ボクの相棒)"]
        facts = [b for b in self.a.memory.beliefs() if b.source == "user" and b.label() == FACT][:4]
        lines += [f"- {b.statement}" for b in facts]
        comps = self.bonds.companions(self.a.cfg["bond_min_strength"])[:8]
        if comps:
            lines.append("# 仲間")
            for row, _ in comps:
                known = [b.statement for b in self.a.memory.beliefs()
                         if row["name"] in b.statement and b.label() in (FACT, INFERENCE)][:2]
                lines.append(f"- {self.bonds.describe(row)}" + (" / " + " / ".join(known) if known else ""))
        interests = self.a.selfm.top_interests()
        if interests:
            lines.append("# ボクの興味\n" + "、".join(interests[:8]))
        return "\n".join(lines)

    def _reflect(self, th, bs):
        """立ち止まって考える: わかってきたことは、誰かに関係するか。"""
        found = "\n".join(f"- [{b.label()}] {b.statement}" for b in bs)
        res = self.a.llm.chat(prompts.MATTERS_SYSTEM,
                              f"# きっかけ ({SEEN_AT.get(th['origin'].split('/')[-1], '周り')})\n{clip(th['seed'], 300)}"
                              f"\n\n# 深掘りしてわかってきたこと\n{found}\n\n{self.context_for_reflection(bs)}",
                              schema=prompts.MATTERS_SCHEMA, max_tokens=250)
        kw = {"checked": th["steps"]}
        nxt = (res.get("next_question") or "").strip()
        if res.get("matters"):
            who = (res.get("to_whom") or "").strip()
            kw.update(state="significant", significant_at=self.a.clock(), urgent=int(bool(res.get("urgent"))),
                      matters=json.dumps({"to_whom": who, "why": (res.get("why") or "").strip()}, ensure_ascii=False))
            self.a.log(f"深掘りの途中で気づいた: {who} に関係する — {res.get('why', '')}")
            if res.get("urgent") and not th["told"]:
                self.a.memory.add_utterance(
                    "report", f"ねえ、{SEEN_AT.get(th['origin'].split('/')[-1], '周り')}で気になることがあって、"
                              f"{who or 'キミ'}に関係するかもしれない。いま調べてるね", 0.9)
                kw["told"] = 1
        if nxt:
            self._link(th["id"], nxt, th["origin"], 1.0 if res.get("matters") else self.a.cfg["dig_relevance"])
        self._set(th["id"], **kw)
        return True

    def allowed_actions(self):
        acts = ["suggest", "draft_message", "remind_later"]
        if self.a.cfg["watch_dirs"]:
            acts.insert(0, "check_workspace")
        if getattr(self.a.probes, "news", None) is not None:
            acts.insert(0, "keep_watching")
        return acts

    def _act(self, th, bs, best, confirmed):
        matters = json.loads(th["matters"] or "{}")
        who = matters.get("to_whom") or ""
        allowed = self.allowed_actions()
        ctx = (f"# きっかけ\n{clip(th['seed'], 300)}\n\n# わかったこと\n"
               + "\n".join(f"- [{b.label()}] {b.statement}" for b in bs)
               + f"\n\n# 誰に関係するか\n{who}: {matters.get('why', '')}"
               + f"\n\n# 確かめられた度合い\n{'複数の出どころで確認できた' if confirmed else '確かめきれていない'}"
               + "\n\n# できる行動\n" + "\n".join(f"- {k}: {ACTIONS[k]}" for k in allowed))
        plan = self.a.llm.chat(prompts.ACT_SYSTEM, ctx, schema=prompts.act_schema(allowed), max_tokens=400)
        done, remind_at = [], None
        for act in (plan.get("actions") or [])[:3]:
            kind, detail = act.get("type"), (act.get("detail") or "").strip()
            if kind not in allowed:
                continue
            if kind == "check_workspace":
                hit = self.a.probes.run("grep_workspace", detail or who)
                done.append(f"作業フォルダで「{detail or who}」を探したよ: "
                            + (clip(hit, 300) if hit else "関係しそうなところは見当たらなかった"))
            elif kind == "keep_watching" and (detail or who):
                self.a.probes.news.watch(detail or who, self.a.cfg["dig_watch_days"])
                done.append(f"しばらく「{detail or who}」の続報を見張っておくね")
            elif kind == "draft_message" and detail:
                done.append(f"連絡するなら、こんな文面はどう？「{clip(detail, 200)}」(送るのはキミにまかせるね)")
            elif kind == "remind_later":
                remind_at = self.a.clock() + self.a.cfg["dig_remind_s"]
                done.append("あとでもう一度様子を確かめて教えるね")
            elif kind == "suggest" and detail:
                done.append(f"できること: {clip(detail, 200)}")
        summary = (plan.get("summary") or "").strip() or best.statement
        where = SEEN_AT.get(th["origin"].split("/")[-1], "周りのこと")
        note = "" if confirmed else " (まだ確かめきれてないよ)"
        report = f"ねえねえ！{where}が気になって調べてたんだけど、{summary} [{best.label()}]{note}" + (
            " / " + " / ".join(done) if done else "")
        self.a.memory.add_utterance("report", report, 0.95, best.id)
        self._set(th["id"], state="reported", report=report, remind_at=remind_at)
        self.a.selfm.remember(f"{where}から{who or '誰か'}に関係することに気づいて知らせた: {clip(summary, 60)}", "insight")
        self.a.log(f"深掘りから行動: {report}")
        return True

    def _close(self, th, why):
        """糸を閉じる (LLM は使わない。None を返す)。"""
        bs = self.beliefs(th["id"])
        learned = [b for b in bs if b.label() in (FACT, INFERENCE, REFUTED)]
        self._set(th["id"], state="closed", report=why)
        self.a.log(f"深掘りを終える: 「{clip(th['seed'], 40)}」({why}。{th['steps']} 歩、わかったこと {len(learned)} 件)")
        if learned:
            self.a.selfm.remember(f"{SEEN_AT.get(th['origin'].split('/')[-1], '周り')}から掘って知った: "
                                  f"{clip(learned[-1].statement, 60)}", "discovery")

    def _reminders(self):
        now = self.a.clock()
        for th in self.db.execute("SELECT * FROM threads WHERE state='reported' AND remind_at IS NOT NULL"
                                  " AND remind_at<=?", (now,)).fetchall():
            self._set(th["id"], remind_at=None, state="significant", significant_at=now, checked=th["steps"])
            self.a.memory.add_utterance("report", "前に話したこと、その後どうなったか気になってるんだ。続報を探してみるね",
                                        0.7, th["root_bid"])
