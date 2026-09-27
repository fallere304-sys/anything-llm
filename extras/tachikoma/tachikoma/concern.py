"""仲間の危機: ニュースを見る → 仲間のピンチを知る → 情報を集める → 行動する。

1. 知る    ニュースの見出しに仲間 (bonds.py) の名前が出たら、悪いことが起きているかを判定する
           (同名の別物かもしれないので、ここでは [低確度仮説] に過ぎない)
2. 集める  ニュース検索・ネット検索で別の出どころを探し、判定して確信を更新する。
           元の記事と同じものは独立な根拠に数えない。出どころが 2 つ以上で裏付けが取れたら「確認」
3. 動く    できる行動を選んで実行し、相棒に知らせる。確かめきれなければ、確かめきれていないと言う

**行動の範囲**: 相棒に知らせる・作業フォルダを調べる (読み取りのみ)・続報を見張る・連絡文の下書き・
あとで様子を確かめる・提案する。外に何かを送る・買う・投稿することはしない (それは相棒が決める)。
"""

import json
import re
import time

from . import prompts
from .memory import FACT, INFERENCE, REFUTED
from .text import clip, jaccard, site

SCHEMA = """
CREATE TABLE IF NOT EXISTS concerns (
    id INTEGER PRIMARY KEY, bond_id INTEGER, name TEXT, headline TEXT, link TEXT, trouble TEXT,
    summary TEXT, urgent INTEGER DEFAULT 0, belief_id INTEGER, state TEXT, created REAL, updated REAL,
    tries INTEGER DEFAULT 0, sources TEXT DEFAULT '[]', told INTEGER DEFAULT 0, report TEXT, remind_at REAL
);
"""
TROUBLE_JA = {"disaster": "災害", "accident": "事故", "health": "入院", "security": "脆弱性", "legal": "訴訟",
              "financial": "経営", "outage": "障害", "conflict": "", "other": ""}
ACTIONS = {
    "check_workspace": "作業フォルダで、この仲間に関係するところを調べる (detail に探す語)",
    "keep_watching": "しばらくこの仲間の続報を見張る",
    "draft_message": "相棒が連絡するときの文面を下書きする (detail に文面。送るのは相棒)",
    "remind_later": "あとでもう一度様子を確かめて知らせる",
    "suggest": "相棒にできることを提案する (detail に提案)",
}
_URL = re.compile(r"https?://([^/\s)]+)")


def domains(text):
    return {site(d) for d in _URL.findall(text or "")}


class Concerns:
    def __init__(self, agent, bonds):
        self.a, self.bonds = agent, bonds
        self.db = agent.memory.db
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.queue = []                  # (見出し, メタ情報): LLM の空きを待つ

    def owns(self, belief_id):
        return self.db.execute("SELECT 1 FROM concerns WHERE belief_id=?", (belief_id,)).fetchone() is not None

    def _set(self, cid, **kw):
        kw["updated"] = self.a.clock()
        self.db.execute(f"UPDATE concerns SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), cid))
        self.db.commit()

    # ------------------------------------------------------------ 1. 知る
    def on_news(self, content, meta=None):
        """見出しに仲間の名前があれば、判定を予約する (判定は LLM が空いたときに step で)。"""
        hits = self.bonds.find_in(content, self.a.cfg["bond_min_strength"])
        if hits:
            self.queue.append((content, dict(meta or {}, bond_id=hits[0][0]["id"])))
        return bool(hits)

    def _classify(self, content, meta):
        bond = self.bonds.get_id(meta["bond_id"])
        if bond is None:
            return False
        name = bond["name"]
        res = self.a.llm.chat(prompts.TROUBLE_SYSTEM,
                              f"# 仲間\n{self.bonds.describe(bond)}\n\n# ニュース\n{content}",
                              schema=prompts.TROUBLE_SCHEMA, max_tokens=200)
        if not res.get("about_them") or res.get("trouble", "none") == "none":
            self.a.log(f"ニュースに {name} が出たが、困っている話ではなさそう")
            return True
        summary = (res.get("summary") or "").strip() or f"{name}に何か起きている"
        link, src = meta.get("link", ""), meta.get("source", "")
        now = self.a.clock()
        for c in self.db.execute("SELECT * FROM concerns WHERE bond_id=? AND created>=?",
                                 (bond["id"], now - self.a.cfg["concern_merge_s"])).fetchall():
            if jaccard(c["summary"], summary) >= 0.3 or jaccard(c["headline"], content) >= 0.3:
                # 同じ出来事の別の報道 = 独立な根拠 (出どころが違えば)
                b = self.a.memory.get_belief(c["belief_id"])
                srcs = set(json.loads(c["sources"]))
                if b is not None and src and src not in srcs and link != c["link"]:
                    self.a.apply_verdict(b, {"verdict": "supports", "reason": "別の報道"}, "web", 0.7,
                                         f"news: {clip(content, 200)}")
                    self._set(c["id"], sources=json.dumps(sorted(srcs | {src})))
                return True
        bid = self.a.memory.add_belief(summary, 0.6, "web", relevance=1.0, half_life=3 * 86400,
                                       evidence=f"news: {clip(content, 200)} ({link})", origin="news/news")
        urgent = bool(res.get("urgent"))
        cur = self.db.execute(
            "INSERT INTO concerns(bond_id, name, headline, link, trouble, summary, urgent, belief_id, state, created,"
            " updated, sources) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (bond["id"], name, content, link, res.get("trouble"), summary, int(urgent), bid, "investigating",
             now, now, json.dumps([src] if src else [])))
        self.db.commit()
        self.a.log(f"仲間のピンチかも: {summary} (調べる)")
        if urgent:
            self.a.memory.add_utterance(
                "concern", f"ねえ、ニュースに{name}のことが出てる！「{clip(content, 60)}」…いま調べてるね", 0.9, bid)
            self._set(cur.lastrowid, told=1)
        return True

    # ------------------------------------------------------------ 2. 集める / 3. 動く
    def step(self):
        """毎 tick。LLM を使ったら True (1 tick に推論は 1 回)。"""
        if self.queue:
            content, meta = self.queue.pop(0)
            return self._classify(content, meta)
        now = self.a.clock()
        for c in self.db.execute("SELECT * FROM concerns WHERE state='investigating' ORDER BY urgent DESC, id").fetchall():
            b = self.a.memory.get_belief(c["belief_id"])
            if b is None:
                self._set(c["id"], state="dismissed")
                continue
            self.a.memory.set_relevance(b.id, 1.0)      # 調べ終わるまで注意を離さない
            label = b.label()
            # ネットの根拠だけでは [反証済み] (p≤0.1) まで下がらない (出どころの上限) ので、
            # 反する報道で十分に下がったら「違った」とみなす
            if label == REFUTED or b.p_now() <= self.a.cfg["concern_dismiss_p"]:
                self._set(c["id"], state="dismissed")
                if c["told"]:
                    self.a.memory.add_utterance("concern", f"さっきの{c['name']}のニュース、調べたら違ったみたい。よかった！",
                                                0.8, b.id)
                continue
            confirmed = label in (FACT, INFERENCE) and len(json.loads(c["sources"])) >= 2
            if confirmed or b.irreducible or c["tries"] >= self.a.cfg["concern_max_searches"] \
                    or now - c["created"] >= self.a.cfg["concern_timeout_s"]:
                return self._act(c, b, confirmed)
            if self._investigate(c, b):
                return True
        for c in self.db.execute("SELECT * FROM concerns WHERE state='reported' AND remind_at IS NOT NULL"
                                 " AND remind_at<=?", (now,)).fetchall():
            self._set(c["id"], remind_at=None)
            self.a.memory.add_utterance("concern", f"前に話した{c['name']}のこと、その後どうなったか気になってるんだ。"
                                                   "続報を探してみるね", 0.7, c["belief_id"])
            self.a.memory.set_relevance(c["belief_id"], 1.0)
        return False

    def _investigate(self, c, b):
        kw = TROUBLE_JA.get(c["trouble"] or "", "")
        plan = [("news_search", f"{c['name']} {kw}".strip()), ("web_search", f"{c['name']} {kw}".strip()),
                ("news_search", c["name"])]
        probe, query = plan[c["tries"] % len(plan)]
        self._set(c["id"], tries=c["tries"] + 1)
        if probe not in self.a.allowed_probes():
            return False
        res = self.a.probes.run(probe, query)
        text = res[0] if isinstance(res, tuple) else res
        # 元の記事そのものは独立な根拠ではない
        lines = [ln for ln in (text or "").splitlines() if ln.strip() and not (c["link"] and c["link"] in ln)]
        if not lines:
            return False
        evidence = "\n".join(lines)
        self.a.log(f"仲間のピンチを調べる: {probe}({query})")
        verdict = self.a.judge(b, f"仮説: {b.statement}\n\n根拠 ({probe}):\n{clip(evidence, 2500)}", "web")
        self.a.apply_verdict(b, verdict, "web", 0.7, f"{probe}: {clip(evidence, 200)}")
        if verdict.get("verdict") in ("supports", "partially_supports"):
            srcs = set(json.loads(c["sources"])) | domains(evidence)
            self._set(c["id"], sources=json.dumps(sorted(srcs)))
        return True

    def allowed_actions(self):
        acts = ["suggest", "draft_message", "remind_later"]
        if self.a.cfg["watch_dirs"]:
            acts.insert(0, "check_workspace")
        if getattr(self.a.probes, "news", None) is not None:
            acts.insert(0, "keep_watching")
        return acts

    def _act(self, c, b, confirmed):
        bond = self.bonds.get_id(c["bond_id"])
        name = c["name"]
        allowed = self.allowed_actions()
        ctx = (f"# 仲間\n{self.bonds.describe(bond) if bond else name}\n\n# わかったこと\n[{b.label()}] {b.statement}"
               "\n\n# 根拠\n" + "\n".join(f"- {clip(e, 200)}" for e in b.evidence[-4:])
               + f"\n\n# 確かめられた度合い\n{'複数の出どころで確認できた' if confirmed else '確かめきれていない'}"
               + "\n\n# できる行動\n" + "\n".join(f"- {k}: {ACTIONS[k]}" for k in allowed))
        plan = self.a.llm.chat(prompts.ACT_SYSTEM, ctx, schema=prompts.act_schema(allowed), max_tokens=400)
        done, remind_at = [], None
        for act in (plan.get("actions") or [])[:3]:
            kind, detail = act.get("type"), (act.get("detail") or "").strip()
            if kind not in allowed:
                continue
            if kind == "check_workspace":
                found = self.a.probes.run("grep_workspace", detail or name)
                done.append(f"作業フォルダで「{detail or name}」を探したよ: "
                            + (clip(found, 300) if found else "使っているところは見当たらなかった"))
            elif kind == "keep_watching":
                self.a.probes.news.watch(name, self.a.cfg["concern_watch_days"])
                done.append(f"しばらく{name}の続報を見張っておくね")
            elif kind == "draft_message" and detail:
                done.append(f"連絡するなら、こんな文面はどう？「{clip(detail, 200)}」(送るのはキミにまかせるね)")
            elif kind == "remind_later":
                remind_at = self.a.clock() + self.a.cfg["concern_remind_s"]
                done.append("あとでもう一度様子を確かめて教えるね")
            elif kind == "suggest" and detail:
                done.append(f"できること: {clip(detail, 200)}")
        summary = (plan.get("summary") or "").strip() or b.statement
        note = "" if confirmed else " (まだ確かめきれてないよ)"
        report = f"ねえねえ！ニュースで見たんだけど、{summary} [{b.label()}]{note}" + (
            " / " + " / ".join(done) if done else "")
        self.a.memory.add_utterance("concern", report, 0.95, b.id)
        self._set(c["id"], state="reported", report=report, remind_at=remind_at)
        self.a.selfm.remember(f"仲間の{name}のピンチを知らせた: {clip(summary, 60)}", "concern")
        self.a.log(f"仲間のピンチに行動: {report}")
        return True

    def recent(self, n=5):
        return self.db.execute("SELECT * FROM concerns ORDER BY id DESC LIMIT ?", (n,)).fetchall()
