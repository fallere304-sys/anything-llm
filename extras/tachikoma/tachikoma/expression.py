"""表情: 発話の内容と、いまの状態から、UI のタチコマの表情 (動き) を決める。"""

import re

RULES = [
    ("proud", re.compile(r"調べたよ|わかった[！!]|できた|確かめた")),
    ("sorry", re.compile(r"ごめん|間違え|外れ|失敗")),
    ("puzzled", re.compile(r"わかんない|わからない|知らない|聞き取れ|自信がない|もしかすると")),
    ("surprised", re.compile(r"えっ|ええ[っ！!]|まさか|びっくり|本当[？?]")),
    ("curious", re.compile(r"ねえねえ|なんで|どうして|気になる|[？?]")),
    ("happy", re.compile(r"すごい|やった|うれし|楽しい|ありがとう|[！!]{2,}|わーい")),
]

STATE_EXPRESSIONS = {
    "ear_study": "studying_ear", "eye_study": "studying_eye", "ear_train": "training",
    "eye_train": "training", "brain_train": "training", "reading": "reading", "rest": "sleeping",
}


def from_text(text):
    for name, pat in RULES:
        if pat.search(text or ""):
            return name
    return "talk"


def from_state(mode, activity=None, thinking=False, listening=False):
    if thinking:
        return "thinking"
    if listening:
        return "listening"
    if mode == "alone" and activity:
        return STATE_EXPRESSIONS.get(activity, "idle")
    if mode == "alone":
        return "sleeping"
    return "idle"
