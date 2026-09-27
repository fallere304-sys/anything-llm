"""独りの時間の活動 (idle.py のスケジューラが選ぶ候補) を、共通の形 (ready/begin/step/busy/progress/pause/end) に揃える。

progress() は「伸び」の累計 (progress point)。スケジューラは活動の前後の差を消費電力量で割って評価する。
"""

P_PER_SAMPLE_PRIOR = 0.01   # 学べる標本 1 つあたりの改善見込み (相対 CER 改善 % の単位)。学習の実績で更新


class StudyActivity:
    """耳 (字幕付き動画) / 目 (文字情報付き PDF) の自習。"""

    def __init__(self, sense, study, data):
        self.sense, self.study, self.data = sense, study, data

    def _p(self):
        return self.data.get(f"p_per_sample_{self.sense}") or P_PER_SAMPLE_PRIOR

    def ready(self):
        if self.sense == "eye":
            return self.study.ready()
        cfg = self.study.cfg
        return bool(self.study.pending_media()) or bool(cfg.get("study_urls")) and \
            self.study.clock() >= self.study.fetch_backoff_until

    def begin(self):
        pass

    def step(self, budget):
        self.study.step(budget)

    def busy(self):
        return False

    def pause(self):
        self.study.pause()

    def progress(self):
        hard = self.study.count_hard() if hasattr(self.study, "count_hard") else \
            self.study.db.execute("SELECT COUNT(*) FROM asr_samples WHERE kind='hard'").fetchone()[0]
        return hard * self._p()


class TrainActivity:
    """耳・目・頭の学習。採用されたときの改善量が「伸び」。1 標本あたりの見込みも実績で更新する。"""

    def __init__(self, sense, learner, data, alone_fn, log=print):
        self.sense, self.learner, self.data, self.alone_fn, self.log = sense, learner, data, alone_fn, log
        self._at_begin = None

    def _versions(self):
        if self.sense == "brain":
            return [{"adopt": r["adopted"], "score": r["score"], "base_score": r["base_score"], "higher": True}
                    for r in self.data.versions()]
        return self.data.get({"ear": "asr_versions", "eye": "ocr_versions"}[self.sense]) or []

    def progress(self):
        pts = 0.0
        for v in self._versions():
            if v.get("adopt") and v.get("score") is not None and v.get("base_score"):
                if v.get("higher"):
                    pts += 100 * (v["score"] - v["base_score"]) / abs(v["base_score"])
                else:
                    pts += 100 * (v["base_score"] - v["score"]) / v["base_score"]
        return pts

    def _new_samples(self):
        if self.sense == "brain":
            return self.data.count_new()
        return self.learner.eyes.count_new_hard() if self.sense == "eye" else self.learner.study.count_new_hard()

    def ready(self):
        return self.learner.should_train(self.alone_fn())

    def begin(self):
        self._at_begin = (self.progress(), self._new_samples())
        msg = self.learner.start(f"独りの時間 ({self.sense})")
        if msg:
            self.log(msg)

    def step(self, budget):
        pass

    def busy(self):
        return self.learner.busy

    def pause(self):
        self.learner.abort()

    def end(self):
        """学習の実績から「標本 1 つあたりの改善見込み」を更新する (次に自習を選ぶときの判断材料)。"""
        if self._at_begin is None or self.sense == "brain":
            return
        gained = self.progress() - self._at_begin[0]
        n = max(1, self._at_begin[1])
        key = f"p_per_sample_{self.sense}"
        old = self.data.get(key) or P_PER_SAMPLE_PRIOR
        self.data.set(key, 0.7 * old + 0.3 * gained / n)
        self._at_begin = None


class ReadingActivity:
    """気になっている仮説を論文・資料で調べる (知識を増やす)。伸び = 解消した不確実性 (bit)。"""

    def __init__(self, agent):
        self.agent = agent

    def ready(self):
        a = self.agent
        return any(b.relevance > 0.2 and b.label() in ("低確度仮説", "合理的推定") and not b.irreducible
                   for b in a.memory.beliefs())

    def begin(self):
        pass

    def step(self, budget):
        a = self.agent
        if a.llm.gate.can_run_background():
            a.curiosity_step() or a.wonder()

    def busy(self):
        return False

    def pause(self):
        pass

    def progress(self):
        return self.agent.bits_resolved * self.agent.cfg["reading_weight"]
