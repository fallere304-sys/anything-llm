"""独りの時間に何をするかを、タチコマ自身が決める。

判断基準 (利用者が指定):
    より合理的に          … 推定値と理由を記録し、同じ基準で比べる。気分では選ばない
    知識を効率よく増やす  … 「伸び」を 1 Wh あたりで測る
    性能 (速度・認識精度) … 耳・目の文字誤り率 (CER) の改善を「伸び」に数える
    電力を抑える          … 分母は消費電力量。どれも割に合わなければ「休む」を選ぶ

候補の活動 (同時には 1 つだけ。GPU を取り合わない):
    ear_study   字幕付き動画で聞き取り練習 (聞き間違いの標本を集める)
    eye_study   文字情報付き PDF で読み取り練習 (読み間違いの標本を集める)
    ear_train   耳 (Whisper) の学習        ─┐
    eye_train   目 (OCR) の学習             ├ 学習は重いので、どれか 1 つが終わるまで他はしない
    brain_train 頭 (Gemma) の学習          ─┘
    reading     気になっている仮説を論文・資料で調べる (好奇心の続き)
    rest        何もしない (省電力)

「伸び」の単位 (progress point):
    練習: 集めた学べる標本の数 × 1 標本あたりの改善見込み (前回の学習の実績から更新)
    学習: 相対 CER 改善 × 100 (採用されなかったら 0)
    読書: 解消した不確実性 (bit) × reading_weight
各活動の「1 Wh あたりの伸び」を指数移動平均で持ち、UCB (試したことの少ない活動を少し優遇) で選ぶ。
これは発達ロボティクスの「学習進捗 (learning progress)」に基づく内発的動機づけの考え方に近い。
"""

import json
import math
import time

ACTIVITIES = ("ear_study", "eye_study", "ear_train", "eye_train", "brain_train", "reading", "rest")
TRAINS = ("ear_train", "eye_train", "brain_train")

SCHEMA = """
CREATE TABLE IF NOT EXISTS idle_log (id INTEGER PRIMARY KEY, ts REAL, activity TEXT, reason TEXT,
    seconds REAL, wh REAL, gain REAL);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

# 事前の見込み (まだ実績が無いときの 1 Wh あたりの伸び)。学習はやや楽観的にして、一度は試させる
PRIOR = {"ear_study": 1.0, "eye_study": 1.0, "ear_train": 3.0, "eye_train": 3.0, "brain_train": 2.0,
         "reading": 1.0, "rest": 0.0}


class IdleScheduler:
    def __init__(self, cfg, memory, power, clock=time.time):
        self.cfg, self.memory, self.power, self.clock = cfg, memory, power, clock
        self.db = memory.db
        self.db.executescript(SCHEMA)
        self.current = None          # いま取り組んでいる活動
        self.session = None          # {activity, start, wh, gain0, last}
        self.handlers = {}           # name -> Handler

    # ------------------------------------------------------------ 記録
    def _kv(self, k, v=None):
        if v is None:
            row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return json.loads(row[0]) if row else None
        self.db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, json.dumps(v)))
        self.db.commit()

    def stats(self):
        return self._kv("idle_stats") or {a: {"rate": PRIOR[a], "n": 0} for a in ACTIVITIES}

    def register(self, name, handler):
        """handler: ready() / begin() / step(budget) / busy() / progress() / end() を持つ。"""
        self.handlers[name] = handler

    # ------------------------------------------------------------ 選択
    def choose(self):
        st = self.stats()
        ready = [a for a, h in self.handlers.items() if h.ready()]
        if not ready:
            return "rest", "できることが無い"
        total = sum(st.get(a, {}).get("n", 0) for a in ready) + 1
        c = self.cfg["idle_explore"]

        def ucb(a):
            s = st.get(a, {"rate": PRIOR.get(a, 1.0), "n": 0})
            return s["rate"] + c * math.sqrt(math.log(total + 1) / (s["n"] + 1))
        best = max(ready, key=ucb)
        rest_value = self.cfg["idle_rest_value"]
        if ucb(best) < rest_value:
            return "rest", f"どれも 1Wh あたりの伸びが小さい (最大 {ucb(best):.2f} < 休む価値 {rest_value})"
        s = st.get(best, {"rate": PRIOR.get(best, 1.0), "n": 0})
        return best, f"1Wh あたりの伸びの見込みが最大 ({s['rate']:.2f}, 試行 {s['n']} 回)"

    # ------------------------------------------------------------ 実行
    def step(self, alone):
        """毎 tick 呼ぶ。独りでなければ今の活動を中断する。進めた活動名を返す。"""
        now = self.clock()
        if not alone:
            if self.current:
                self._end(interrupted=True)
            return None
        if self.current and self.current != "rest":
            h = self.handlers[self.current]
            self._account(now, gpu=True)
            if h.busy():                       # 学習など、別スレッドで走っている
                return self.current
            if self.current in TRAINS:          # 学習が終わった
                self._end()
            elif now - self.session["start"] >= self.cfg["idle_session_s"] or not h.ready():
                self._end()
            else:
                h.step(self.cfg["study_step_budget_s"])
                return self.current
        if self.current == "rest":
            self._account(now, gpu=False)
            if now - self.session["start"] < self.cfg["idle_rest_s"]:
                return "rest"
            self._end()
        act, why = self.choose()
        self._begin(act, why, now)
        if act != "rest":
            self.handlers[act].begin()
        return act

    def _begin(self, act, why, now):
        self.current = act
        h = self.handlers.get(act)
        self.session = {"activity": act, "start": now, "last": now, "wh": 0.0,
                        "gain0": h.progress() if h else 0.0, "why": why}
        self.db.execute("INSERT INTO idle_log(ts, activity, reason) VALUES (?,?,?)", (now, act, why))
        self.db.commit()

    def _account(self, now, gpu):
        dt = now - self.session["last"]
        self.session["last"] = now
        self.session["wh"] += self.power.watts(gpu_active_guess=gpu) * dt / 3600

    def _end(self, interrupted=False):
        act, s = self.current, self.session
        h = self.handlers.get(act)
        if h is not None and act != "rest":
            if interrupted:
                h.pause()
            if hasattr(h, "end"):
                h.end()
            gain = max(0.0, h.progress() - s["gain0"])
            wh = max(s["wh"], 1e-3)
            if not (interrupted and gain == 0):    # 邪魔されて何もできなかった回は評価に入れない
                st = self.stats()
                a = st.setdefault(act, {"rate": PRIOR.get(act, 1.0), "n": 0})
                alpha = self.cfg["idle_ema"]
                a["rate"] = (1 - alpha) * a["rate"] + alpha * gain / wh if a["n"] else gain / wh
                a["n"] += 1
                self._kv("idle_stats", st)
            self.db.execute("UPDATE idle_log SET seconds=?, wh=?, gain=? WHERE id=(SELECT MAX(id) FROM idle_log)",
                            (self.clock() - s["start"], s["wh"], gain))
            self.db.commit()
        self.current, self.session = None, None

    def describe(self):
        """いま何をしていて、なぜそれを選んだか (UI と /status 用)。"""
        if not self.current:
            return None
        return {"activity": self.current, "why": self.session["why"],
                "minutes": (self.clock() - self.session["start"]) / 60, "wh": self.session["wh"]}
