"""計測: 進化の選択に使う数値を、思考のコードとは独立に記録する。

記録はカーネルの実行ループ (runtime.py) が agent を外側から包んで行う。
思考のコードが計測を消したり書き換えたりしても、選択の材料は残る。
"""

import json
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY, ts REAL, name TEXT, value REAL);
CREATE INDEX IF NOT EXISTS ix_metrics ON metrics(name, ts);
CREATE TABLE IF NOT EXISTS errors (id INTEGER PRIMARY KEY, ts REAL, file TEXT, line INTEGER,
    func TEXT, message TEXT, trace TEXT, handled INTEGER DEFAULT 0);
"""


class Metrics:
    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock
        self.db.executescript(SCHEMA)

    def record(self, name, value=1.0):
        self.db.execute("INSERT INTO metrics(ts, name, value) VALUES (?,?,?)", (self.clock(), name, float(value)))
        self.db.commit()

    def values(self, name, start, end=None):
        end = end or self.clock()
        return [r[0] for r in self.db.execute(
            "SELECT value FROM metrics WHERE name=? AND ts>=? AND ts<? ORDER BY ts", (name, start, end))]

    def count(self, name, start, end=None):
        return len(self.values(name, start, end))

    def record_error(self, exc, tb_frames, trace):
        """例外の発生場所 (最も内側の、パッケージ内のフレーム) を記録する。"""
        file, line, func = "", 0, ""
        for fr in reversed(tb_frames):
            if "tachikoma" in fr.filename.replace("\\", "/"):
                file, line, func = fr.filename, fr.lineno, fr.name
                break
        self.db.execute("INSERT INTO errors(ts, file, line, func, message, trace) VALUES (?,?,?,?,?,?)",
                        (self.clock(), file, line, func, f"{type(exc).__name__}: {exc}", trace[-4000:]))
        self.record("error")

    def open_errors(self, limit=5):
        return self.db.execute(
            "SELECT file, line, func, message, trace, COUNT(*) n, MAX(ts) ts FROM errors WHERE handled=0"
            " GROUP BY file, line, message ORDER BY n DESC LIMIT ?", (limit,)).fetchall()

    def mark_errors_handled_like(self, filename, line):
        self.db.execute("UPDATE errors SET handled=1 WHERE (file LIKE ? OR file LIKE ?) AND line=?",
                        ("%/" + filename, "%\\" + filename, line))
        self.db.commit()


def summary(values):
    if not values:
        return None
    s = sorted(values)
    return {"n": len(s), "mean": sum(s) / len(s), "p90": s[min(len(s) - 1, int(len(s) * 0.9))]}


def to_json(d):
    return json.dumps(d, ensure_ascii=False)
