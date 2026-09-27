"""仲間 (絆) の記憶: 一緒に過ごした時間の中で出てきた人・組織・ソフトウェア・場所。

タチコマにとっての「かつての仲間」は、相棒が話したり、一緒に作業したりした相手のこと。
話しかけ・声・編集・端末に出てきた固有名を、関わりの深さ (重み) つきで積み上げる。
ニュースに仲間の名前が出たら、どんなに遠い話題でも注意を向ける (concern.py)。

- ニュースに出てきただけの名前は仲間にしない (毎日の見出しの有名人が仲間になってしまう)
- 絆はゆっくりしか薄れない (かつての仲間を忘れない)。はっきり教えてもらった仲間 (/friend) は重い
"""

import json
import math
import re
import time
import unicodedata

SCHEMA = """
CREATE TABLE IF NOT EXISTS bonds (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT, aliases TEXT DEFAULT '[]', score REAL DEFAULT 0,
    first_seen REAL, last_seen REAL, mentions INTEGER DEFAULT 0, note TEXT DEFAULT ''
);
"""

# どういう関わりで名前が出たか → 絆の重み
WEIGHT = {"user_message": 1.0, "speech": 0.7, "file_changed": 0.3, "file_created": 0.3,
          "terminal_output": 0.2, "explicit": 5.0}
KINDS = ("person", "organization", "software", "place", "other")
KIND_JA = {"person": "人", "organization": "組織", "software": "ソフトウェア", "place": "場所", "other": "その他"}
_GENERIC = {"ユーザー", "私", "僕", "ボク", "俺", "自分", "タチコマ", "あなた", "キミ", "みんな", "今日", "明日"}
HALF_LIFE_S = 365 * 86400


def norm(name):
    return unicodedata.normalize("NFKC", name or "").strip().lower()


def _contains(text, name):
    if name.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", text) is not None
    return name in text


class Bonds:
    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock
        self.db.executescript(SCHEMA)
        self.db.commit()

    def strength(self, row):
        """0-1。関わりの積み重ね (1 年で半分に薄れる) から。"""
        age = max(0.0, self.clock() - (row["last_seen"] or self.clock()))
        return 1.0 - math.exp(-(row["score"] or 0) * 0.5 ** (age / HALF_LIFE_S) / 4)

    def mention(self, name, kind="other", how="user_message"):
        name = (name or "").strip()
        if len(norm(name)) < 2 or name in _GENERIC:
            return None
        kind = kind if kind in KINDS else "other"
        now = self.clock()
        row = self.get(name)
        if row is None:
            self.db.execute("INSERT INTO bonds(name, kind, score, first_seen, last_seen, mentions) VALUES (?,?,?,?,?,1)",
                            (name, kind, WEIGHT.get(how, 0.2), now, now))
        else:
            self.db.execute("UPDATE bonds SET score=score+?, last_seen=?, mentions=mentions+1,"
                            " kind=CASE WHEN kind='other' THEN ? ELSE kind END WHERE id=?",
                            (WEIGHT.get(how, 0.2), now, kind, row["id"]))
        self.db.commit()
        return self.get(name)

    def befriend(self, name, kind="person", note=""):
        row = self.mention(name, kind, "explicit")
        if row is not None and note:
            self.db.execute("UPDATE bonds SET note=? WHERE id=?", (note, row["id"]))
            self.db.commit()
        return row

    def forget(self, name):
        row = self.get(name)
        if row is not None:
            self.db.execute("DELETE FROM bonds WHERE id=?", (row["id"],))
            self.db.commit()
        return row is not None

    def get(self, name):
        n = norm(name)
        for row in self.db.execute("SELECT * FROM bonds"):
            if norm(row["name"]) == n or n in [norm(a) for a in json.loads(row["aliases"] or "[]")]:
                return row
        return None

    def get_id(self, bid):
        return self.db.execute("SELECT * FROM bonds WHERE id=?", (bid,)).fetchone()

    def companions(self, min_strength=0.0):
        rows = [(r, self.strength(r)) for r in self.db.execute("SELECT * FROM bonds")]
        return sorted([x for x in rows if x[1] >= min_strength], key=lambda x: -x[1])

    def find_in(self, text, min_strength):
        """文中に名前が出てくる仲間 [(row, 強さ)] (強い順)。"""
        t = norm(text)
        hits = []
        for row, s in self.companions(min_strength):
            names = [row["name"]] + json.loads(row["aliases"] or "[]")
            if any(len(norm(n)) >= 2 and _contains(t, norm(n)) for n in names):
                hits.append((row, s))
        return hits

    def describe(self, row):
        s = self.strength(row)
        days = int((self.clock() - (row["first_seen"] or self.clock())) / 86400)
        return (f"{row['name']} ({KIND_JA.get(row['kind'], row['kind'])}、絆 {s:.2f}、{row['mentions']} 回、"
                f"{days} 日前から){' ' + row['note'] if row['note'] else ''}")
