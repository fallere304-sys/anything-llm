"""注意状態: いま誰かと話しているか、誰かがいるか、独りか。

    conversing  話しかけられてから conversation_window_s 以内 (返事を最優先)
    attending   人がいる / 最近声がした (観察と好奇心。話しかけは控えめ)
    alone       声も人影も一定時間ない (字幕付き動画で耳を鍛える・学習する)

誤判定の非対称性: 人がいるのに alone と判定する方が害が大きい
(勉強や学習で GPU を占有し、返事が遅れる)。そこで「いる」は 1 回の検出で即座に、
「いない」は absent_after_s 続いて初めて認める。
"""

import time

from .text import normalize_transcript

CONVERSING, ATTENDING, ALONE = "conversing", "attending", "alone"


class Attention:
    def __init__(self, cfg, clock=time.time, idle_fn=None, has_camera=False):
        self.cfg, self.clock = cfg, clock
        self.idle_fn = idle_fn or (lambda: None)
        self.has_camera = has_camera
        now = clock()
        self.last_speech = now          # 誰かの声 (自分宛てでなくても)
        self.last_addressed = 0.0       # 自分に話しかけられた
        self.last_person = now          # カメラに人
        self.person_present = False
        self.wake_words = [normalize_transcript(w) for w in cfg.get("wake_words", [])]

    # ------------------------------------------------------------ 入力
    def on_speech(self):
        self.last_speech = self.clock()

    def on_addressed(self):
        self.last_addressed = self.last_speech = self.clock()

    def on_presence(self, present):
        self.person_present = present
        self.last_person = self.clock()

    def on_self_spoke(self):
        """自分が話した直後は、相手の返事を待つ会話状態にする。"""
        self.last_addressed = self.clock()

    # ------------------------------------------------------------ 判定
    def conversing(self, now=None):
        now = now or self.clock()
        return now - self.last_addressed < self.cfg["conversation_window_s"]

    def is_addressed(self, text):
        """話しかけ判定: 呼びかけ語 (タチコマ等) を含むか、会話の途中か。"""
        if self.conversing():
            return True
        t = normalize_transcript(text)
        return any(w and w in t for w in self.wake_words)

    def strip_wake_word(self, text):
        for w in self.cfg.get("wake_words", []):
            if text.startswith(w):
                return text[len(w):].lstrip("、,。 　")
        return text

    def alone_for(self, now=None):
        """独りになってからの秒数 (独りでなければ 0)。"""
        now = now or self.clock()
        if self.person_present or self.conversing(now):
            return 0.0
        quiet = now - self.last_speech
        if self.has_camera:
            unseen = now - self.last_person
        else:
            # カメラが無いときは PC 操作の有無で代用する
            idle = self.idle_fn()
            unseen = quiet if idle is None else idle
        return max(0.0, min(quiet, unseen))

    def mode(self, now=None):
        now = now or self.clock()
        if self.conversing(now):
            return CONVERSING
        if self.alone_for(now) >= self.cfg["absent_after_s"]:
            return ALONE
        return ATTENDING

    def someone_talking(self, now=None):
        """直近に (自分宛てでない) 声がした = 割り込むべきでない。"""
        now = now or self.clock()
        return now - self.last_speech < self.cfg["talking_grace_s"]
