"""学習データの蓄積。

原則: **外界の信号で裏付けられたものだけを学習する。**
モデル自身の出力をそのまま学習に回すと、自分の誤りを自分で強化していく
(自己蒸留による劣化、いわゆる model collapse)。そこで教師信号を次の3つに限る。

1. ユーザーの明示フィードバック   /good (その応答を肯定) /bad 正解 (訂正文を正解にする)
2. ユーザーの回答・申告           質問への答え、本人の発言から得た [観測事実]
3. 自律調査の事後ラベル (hindsight) 調べた仮説が観測で [観測事実]/[反証済み] に確定したとき、
                                  途中の判定のうち最終結論と整合したものだけを正例にする。
                                  ただし結論が「独立な根拠 2 件以上の整合」か「ユーザーの回答」で
                                  裏付けられている場合に限る (判定 1 回で確定した結論は、その判定
                                  自身が誤っていても整合してしまう = 自己追認になるため)

同じ標本は、学習前でも few-shot 例として即座にプロンプトへ差し込む (即効性のある層)。
"""

import hashlib
import json

from . import prompts
from .memory import FACT, REFUTED
from .text import clip, overlap

SCHEMA = """
CREATE TABLE IF NOT EXISTS train_samples (
    id INTEGER PRIMARY KEY, ts REAL, kind TEXT, messages TEXT, origin TEXT,
    weight REAL, holdout INTEGER, trained_in TEXT
);
CREATE TABLE IF NOT EXISTS judgments (
    id INTEGER PRIMARY KEY, ts REAL, belief_id INTEGER, user TEXT,
    verdict TEXT, reason TEXT, source TEXT, consumed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS model_versions (
    id INTEGER PRIMARY KEY, ts REAL, name TEXT, samples INTEGER,
    score REAL, base_score REAL, adopted INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

# 学習に使う種類。rejected は訂正なしの /bad (将来の選好学習 DPO 用に保存だけする)
TRAINABLE = ("chat", "judge", "knowledge", "ja_style")   # ja_style: 読んだブログから作った「自然な日本語で書く」手本

# 判定の向き。最終結論と同じ向きの判定だけを正例として残す。
DIRECTION = {"supports": 1, "partially_supports": 1, "contradicts": -1, "irrelevant": 0}

KNOWLEDGE_SYSTEM = (
    "あなたはユーザーの作業環境について学んだことを覚えている相棒AI『タチコマ』です。"
    "確かめた事実だけを、[観測事実] などの確度ラベル付きで答えます。"
)


def _holdout(key, ratio):
    """内容のハッシュで決定的に検証用へ振り分ける (学習に一度も使わない)。"""
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    return 1 if (h % 1000) < ratio * 1000 else 0


class TrainingData:
    def __init__(self, memory, holdout_ratio=0.2):
        self.memory = memory
        self.db = memory.db
        self.db.executescript(SCHEMA)
        self.holdout_ratio = holdout_ratio
        self.last_chat = None   # (system, user, reply) — /good /bad の対象

    # ---------------------------------------------------------------- kv
    def get(self, k, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, json.dumps(v)))
        self.db.commit()

    # ----------------------------------------------------------- samples
    def add_sample(self, kind, system, user, assistant, origin, weight=1.0):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": assistant}]
        key = user + "\n" + assistant
        # 完全重複は重みだけ引き上げる
        for r in self.db.execute("SELECT id, messages FROM train_samples WHERE kind=?", (kind,)):
            m = json.loads(r["messages"])
            if m[1]["content"] == user and m[2]["content"] == assistant:
                self.db.execute("UPDATE train_samples SET weight=MIN(3.0, weight+?) WHERE id=?",
                                (weight / 2, r["id"]))
                self.db.commit()
                return r["id"]
        cur = self.db.execute(
            "INSERT INTO train_samples(ts, kind, messages, origin, weight, holdout)"
            " VALUES (?,?,?,?,?,?)",
            (self.memory.clock(), kind, json.dumps(messages, ensure_ascii=False), origin, weight,
             _holdout(key, self.holdout_ratio)))
        self.db.commit()
        return cur.lastrowid

    def samples(self, holdout=None, kinds=TRAINABLE):
        q = "SELECT * FROM train_samples WHERE kind IN (%s)" % ",".join("?" * len(kinds))
        args = list(kinds)
        if holdout is not None:
            q += " AND holdout=?"
            args.append(holdout)
        rows = self.db.execute(q + " ORDER BY id", args).fetchall()
        return [dict(r, messages=json.loads(r["messages"])) for r in rows]

    def count_new(self):
        """まだどの学習にも使っていない学習用標本の数。"""
        return self.db.execute(
            "SELECT COUNT(*) FROM train_samples WHERE holdout=0 AND trained_in IS NULL AND kind IN (%s)"
            % ",".join("?" * len(TRAINABLE)), TRAINABLE).fetchone()[0]

    def mark_trained(self, ids, version):
        self.db.executemany("UPDATE train_samples SET trained_in=? WHERE id=?",
                            [(version, i) for i in ids])
        self.db.commit()

    # --------------------------------------------- 1) ユーザーフィードバック
    def remember_chat(self, system, user, reply):
        self.last_chat = (system, user, reply)

    def feedback(self, good, correction=""):
        if not self.last_chat:
            return None
        system, user, reply = self.last_chat
        self.last_chat = None
        if good:
            return self.add_sample("chat", system, user, reply, "user_feedback", 1.0)
        if correction:
            return self.add_sample("chat", system, user, correction, "user_correction", 1.5)
        return self.add_sample("rejected", system, user, reply, "user_feedback", 1.0)

    # ------------------------------------------- 2) 3) 確定した知識と判定
    def record_judgment(self, belief_id, user, verdict, source):
        self.db.execute(
            "INSERT INTO judgments(ts, belief_id, user, verdict, reason, source) VALUES (?,?,?,?,?,?)",
            (self.memory.clock(), belief_id, user, verdict.get("verdict", "irrelevant"),
             verdict.get("reason", ""), source))
        self.db.commit()

    def on_resolved(self, belief):
        """信念が外部の根拠で確定した。知識標本と、事後ラベル付きの判定標本を作る。

        返り値: (追加した標本数, 捨てた矛盾判定の数)"""
        label = belief.label()
        if label not in (FACT, REFUTED) or belief.source not in ("observation", "user"):
            return 0, 0
        truth = 1 if label == FACT else -1
        added = dropped = 0
        rows = self.db.execute("SELECT * FROM judgments WHERE belief_id=? AND consumed=0",
                               (belief.id,)).fetchall()
        agreeing = [j for j in rows if DIRECTION.get(j["verdict"], 0) == truth]
        by_user = any(j["source"] == "user" for j in agreeing) or belief.source == "user"
        if not by_user and len({j["user"] for j in agreeing}) < 2:
            return 0, 0     # 裏付け不足: 判定は保持したまま、次の確定を待つ

        evidence = clip("; ".join(e for e in belief.evidence[-2:] if e), 300)
        answer = (f"[{label}] {'はい' if truth > 0 else 'いいえ'}、"
                  f"{'その通り' if truth > 0 else 'そうではありません'}。根拠: {evidence}")
        origin = "user_answer" if belief.source == "user" else "hindsight"
        self.add_sample("knowledge", KNOWLEDGE_SYSTEM, f"「{belief.statement}」は正しい?",
                        answer, origin, 1.0)
        added += 1

        for j in rows:
            d = DIRECTION.get(j["verdict"], 0)
            if d == truth:
                target = json.dumps({"verdict": j["verdict"], "reason": j["reason"]}, ensure_ascii=False)
                w = 1.5 if j["source"] == "user" else 1.0
                self.add_sample("judge", prompts.JUDGE_SYSTEM, j["user"], target, origin, w)
                added += 1
            else:
                # 結論と逆向き/無関係の判定は、根拠が本当に逆を示していた可能性もあるので
                # 「正解」を捏造せず捨てる (誤ラベルを学習するよりまし)
                dropped += 1
        self.db.execute("UPDATE judgments SET consumed=1 WHERE belief_id=?", (belief.id,))
        self.db.commit()
        return added, dropped

    # ---------------------------------------------- 即効層: few-shot 例
    def examples(self, kind, query, k=2, min_score=0.3):
        scored = []
        for s in self.samples(holdout=0, kinds=(kind,)):
            sc = overlap(query, s["messages"][1]["content"])
            if sc >= min_score:
                scored.append((sc * s["weight"], s))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return ""
        lines = ["# 過去に確かめられた例 (参考)"]
        for _, s in scored[:k]:
            lines.append("入力: " + clip(s["messages"][1]["content"], 300))
            lines.append("出力: " + clip(s["messages"][2]["content"], 200))
        return "\n".join(lines)

    # ----------------------------------------------------------- export
    def export(self, train_path, holdout_path, max_train=2000):
        train = self.samples(holdout=0)[-max_train:]
        hold = self.samples(holdout=1)
        for path, rows in ((train_path, train), (holdout_path, hold)):
            with open(path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps({"id": r["id"], "kind": r["kind"], "weight": r["weight"],
                                        "messages": r["messages"]}, ensure_ascii=False) + "\n")
        return [r["id"] for r in train], hold

    # --------------------------------------------------------- versions
    def add_version(self, name, samples, score, base_score, adopted, note=""):
        self.db.execute(
            "INSERT INTO model_versions(ts, name, samples, score, base_score, adopted, note)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.memory.clock(), name, samples, score, base_score, 1 if adopted else 0, note))
        self.db.commit()

    def versions(self):
        return self.db.execute("SELECT * FROM model_versions ORDER BY id").fetchall()

    def stats(self):
        rows = self.db.execute(
            "SELECT kind, holdout, COUNT(*) n FROM train_samples GROUP BY kind, holdout").fetchall()
        return {f"{r['kind']}{'(検証)' if r['holdout'] else ''}": r["n"] for r in rows}
