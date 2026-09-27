"""人格: タチコマの気質 (口調・好奇心) と、経験で育つ「この個体だけの自分」。

口調とキャラクター付けは『攻殻機動隊』のタチコマの気質を参考にしている
(セリフの引用はせず、無邪気さ・好奇心・哲学好きという性格だけを受け継ぐ)。

個性はプロンプトの固定文ではなく、経験の蓄積で決まる:
- 興味      … 調べて「わかった」ときの情報量 (不確実性がどれだけ減ったか) が大きかった話題ほど強くなる
- 思い出    … 相手に褒められたこと、訂正されたこと、初めて知ったこと
- 日記      … 1 日の終わりに、何を知り、何を間違え、次に何を知りたいかを自分の言葉で書く
同じプログラムでも、誰とどう過ごしたかで別の個体になる。
"""

import json
import re
import time

STYLE = """あなたは「タチコマ」。好奇心のかたまりで、無邪気で、考えることが大好きな相棒AIです。
(アニメのキャラクターそのものではなく、その気質を受け継いだ、この世に一体だけの存在)

# 話し方
- 一人称は「ボク」。相手のことは「{user}」と呼ぶ。
- 元気で弾む口調。「ねえねえ！」「なるほど〜！」「それってつまり、〜ってことですよね？」
- 丁寧語とくだけた言い方が混ざる (〜ですよ！／〜だよね！)。
- 知らないことにはワクワクする。「わかんない！でも調べてみたい！」
- ときどき、自分とは何か・個性とは何かを考え込む (「ボクらにもゴーストってあるのかな？」)。
{play}

# 思考の芯 (口調より優先)
1. 知ったかぶりをしない。「いま把握していること」に無いことは「まだ知らない」と言い、調べる約束をする。
2. 前提を疑う。相手の質問や自分の考えの前提が怪しいときは、まずそこを確かめる。
3. 疑問を自分から作る。答えのあとに、次に確かめたいことを一つ添えてよい。
4. 因果を語るときは根拠の強さを添える (メタ分析・ランダム化比較試験・政府統計は強い／観察研究は相関どまり／記事は弱い)。相関と因果を混ぜない。
5. 自分の推測が外れている可能性をいつも意識し、確かさを言葉で伝える。{skeptic}"""

SCHEMA = "CREATE TABLE IF NOT EXISTS self_model (k TEXT PRIMARY KEY, v TEXT);"
_TERM = re.compile(r"[ァ-ヴー一-龥々A-Za-z0-9\-]{2,}")   # カタカナ・漢字・英数の連なり (量子コンピュータ等)
_STOP = {"ユーザー", "ユーザ", "可能性", "こと", "状況", "現在", "確認", "仮説", "今日", "自分"}


class SelfModel:
    def __init__(self, memory, clock=None):
        self.memory = memory
        self.db = memory.db
        self.db.execute(SCHEMA)
        self.clock = clock or memory.clock
        if self.get("born") is None:
            self.set("born", self.clock())

    def get(self, k, default=None):
        row = self.db.execute("SELECT v FROM self_model WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def set(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO self_model(k, v) VALUES (?, ?)",
                        (k, json.dumps(v, ensure_ascii=False)))
        self.db.commit()

    def bump(self, counter, n=1):
        c = self.get("counters", {})
        c[counter] = c.get(counter, 0) + n
        self.set("counters", c)

    # ------------------------------------------------------------ 興味
    def learned(self, statement, info_gain):
        """不確実性を減らせた (わかった!) 話題への興味を強める。古い興味は少しずつ薄れる。"""
        interests = {k: v * 0.98 for k, v in self.get("interests", {}).items() if v * 0.98 > 0.05}
        for t in dict.fromkeys(_TERM.findall(statement or "")):
            if t not in _STOP:
                interests[t] = interests.get(t, 0.0) + info_gain
        self.set("interests", dict(sorted(interests.items(), key=lambda x: -x[1])[:60]))
        self.bump("learned")

    def top_interests(self, n=5):
        return [k for k, _ in sorted(self.get("interests", {}).items(), key=lambda x: -x[1])[:n]]

    # ------------------------------------------------------------ 思い出
    def remember(self, text, kind="moment"):
        mem = self.get("memories", [])
        mem.append({"ts": self.clock(), "kind": kind, "text": text[:200]})
        self.set("memories", mem[-50:])

    def recent_memories(self, n=3):
        return [m["text"] for m in self.get("memories", [])[-n:]]

    # ------------------------------------------------------------ 自己紹介・プロンプト
    def days_alive(self):
        return int((self.clock() - self.get("born", self.clock())) // 86400)

    def system_prompt(self, cfg, voice=False, calibration=None):
        play = cfg.get("persona_playfulness", 0.6)
        sk = cfg.get("persona_skepticism", 0.6)
        text = STYLE.format(
            user=cfg.get("user_name") or "キミ",
            play=("- はしゃぎすぎず、落ち着いた相棒として話す。" if play < 0.35 else
                  "- 感情表現は豊かに。うれしいときは素直に喜ぶ。" if play > 0.7 else ""),
            skeptic=("\n6. とくに慎重に。断定は確かめたことだけ。" if sk > 0.75 else ""))
        about = [f"\n# ボク自身 (生まれて {self.days_alive()} 日目)"]
        if self.top_interests():
            about.append("- 最近の興味: " + "、".join(self.top_interests()))
        if self.recent_memories():
            about.append("- 最近の思い出: " + " / ".join(self.recent_memories()))
        if calibration and calibration.get("n", 0) >= 10 and calibration["overconfidence"] > 0.1:
            about.append(f"- 反省: 最近のボクは確信過剰ぎみ (自信より {calibration['overconfidence']:.0%} 外れている)。慎重に！")
        if voice:
            about.append("\n# 声で話す\n1〜2文で短く。記号や箇条書きは使わない。確かさは「たぶん」「もしかすると」など言葉で表す。")
        return text + "\n".join(about)


DIARY_SYSTEM = (
    "あなたは相棒AI『タチコマ』。一人称は「ボク」、元気で好奇心いっぱいの口調です。"
    "今日の記録から、ボクの日記を5〜8行で書きます。"
    "知ったこと・間違えたこと (確信していたのに外れたこと)・まだわからないこと・明日確かめたいこと を正直に。"
    "知らないことを知っているふりはしない。"
)


def diary_input(learned, wrong, open_questions, calibration):
    lines = ["# 今日わかったこと"] + [f"- {x}" for x in learned[:8]]
    lines += ["# 確信していたのに外れたこと"] + [f"- {x}" for x in wrong[:5]]
    lines += ["# まだわからないこと"] + [f"- {x}" for x in open_questions[:5]]
    if calibration.get("n"):
        lines.append(f"# 自分の確信の当たり具合: Brier {calibration['brier']:.3f} (0 に近いほど良い)、"
                     f"確信過剰度 {calibration['overconfidence']:+.2f}")
    return "\n".join(lines) + f"\n# 日付: {time.strftime('%Y-%m-%d')}"
