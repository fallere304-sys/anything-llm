"""独りの時間の自習: 字幕付き動画で「耳」を鍛える。

好奇心の定義 (低確度推定を減らすための情報収集) を聴覚に当てはめたもの:
    自分の認識結果 = 推定、人間が付けた字幕 = それを確かめる情報。

    動画 → 音声 (16kHz mono) を抽出 → 字幕の各行の区間を切り出す → 今の耳で認識
      → 字幕と比べて CER (文字誤り率) を測る → 学べる誤りだけを学習標本として残す

標本の選び方 (learnability):
- CER がほぼ 0        … もう聞き取れている。忘却防止用に一部 (easy) だけ残す
- CER が中くらい      … 聞き間違えた = 学べる (hard)。これが主な教材
- CER が大きすぎる    … 字幕が要約・意訳・時刻ずれの可能性が高い。学ぶと害 → 捨てる

動画は再生しない (スピーカーから音を出さない)。部屋を騒がせず、マイクにも回り込まない。
自動生成字幕は使わない (他社の音声認識の誤りを学ぶことになる)。
"""

import hashlib
import json
import os
import re
import subprocess
import time
import wave

from .text import cer, missed_terms, normalize_transcript

MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".wav", ".opus", ".ogg", ".flac", ".mov")
SUB_EXTS = (".srt", ".vtt")

SCHEMA = """
CREATE TABLE IF NOT EXISTS asr_samples (
    id INTEGER PRIMARY KEY, ts REAL, source TEXT, clip TEXT, ref TEXT, hyp TEXT,
    cer REAL, kind TEXT, holdout INTEGER, trained_in TEXT
);
CREATE TABLE IF NOT EXISTS asr_vocab (term TEXT PRIMARY KEY, misses INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS asr_log (id INTEGER PRIMARY KEY, ts REAL, model TEXT, source TEXT,
    cues INTEGER, mean_cer REAL);
"""

_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
_TAG = re.compile(r"<[^>]*>|\{\\[^}]*\}")


def _seconds(m):
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_subtitles(text):
    """SRT / WebVTT を [(開始秒, 終了秒, テキスト)] にする。"""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("﻿", "")):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if "-->" in line:
                ts = _TS.findall(line)
                if len(ts) < 2:
                    break
                a, b = (_TS.search(part) for part in line.split("-->", 1))
                body = " ".join(_TAG.sub("", l).strip() for l in lines[i + 1:])
                if body.strip():
                    cues.append((_seconds(a), _seconds(b), body.strip()))
                break
    return cues


def find_subtitle(media_path, lang="ja"):
    stem = os.path.splitext(media_path)[0]
    for suffix in (f".{lang}", ""):
        for ext in SUB_EXTS:
            p = stem + suffix + ext
            if os.path.exists(p):
                return p
    return None


def _holdout(key, ratio):
    return 1 if int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % 1000 < ratio * 1000 else 0


def _keep_easy(key, ratio):
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16) % 1000 < ratio * 1000


class Study:
    """1 tick ごとに少しずつ進む (いつでも止められる) 自習セッション。"""

    def __init__(self, cfg, memory, asr, clock=time.time, popen=subprocess.Popen, log=print):
        self.cfg, self.memory, self.asr = cfg, memory, asr
        self.db = memory.db
        self.db.executescript(SCHEMA)
        self.clock, self.popen, self.log = clock, popen, log
        self.dir = os.path.abspath(cfg["study_dir"])
        self.clips = os.path.join(self.dir, "clips")
        os.makedirs(self.clips, exist_ok=True)
        self.state = "idle"          # idle / fetching / extracting / listening
        self.proc = None
        self.media = self.wav = None
        self.cues, self.pos = [], 0
        self.cers = []
        self.fetch_backoff_until = 0.0

    # ------------------------------------------------------------ 教材
    def _done(self):
        return set(self._kv("study_done") or [])

    def _kv(self, k, v=None):
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        if v is None:
            row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return json.loads(row[0]) if row else None
        self.db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, json.dumps(v)))
        self.db.commit()

    def pending_media(self):
        done = self._done()
        out = []
        for d in [self.dir] + list(self.cfg.get("study_media_dirs", [])):
            if not os.path.isdir(d):
                continue
            for root, _, files in os.walk(d):
                if os.path.abspath(root).startswith(self.clips):
                    continue
                for fn in sorted(files):
                    p = os.path.join(root, fn)
                    if fn.lower().endswith(MEDIA_EXTS) and p not in done and find_subtitle(p, self.cfg["asr_language"]):
                        out.append(p)
        return out

    def _mark_done(self, path):
        done = self._done()
        done.add(path)
        self._kv("study_done", sorted(done))

    def _start_fetch(self):
        """yt-dlp で次の 1 本を取得する (人手の字幕付きのものだけ)。"""
        urls = self.cfg.get("study_urls") or []
        if not urls or self.clock() < self.fetch_backoff_until:
            return False
        lang = self.cfg["asr_language"]
        args = [self.cfg["ytdlp_bin"], "-x", "--audio-format", "m4a",
                "--write-subs", "--no-write-auto-subs", "--sub-langs", lang, "--sub-format", "vtt/srt",
                "--max-downloads", "1", "--download-archive", os.path.join(self.dir, "archive.txt"),
                "-o", os.path.join(self.dir, "%(id)s.%(ext)s")]
        if self.cfg.get("study_match_filter"):
            args += ["--match-filter", self.cfg["study_match_filter"]]
        args += list(urls)
        try:
            self.proc = self.popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.log(f"yt-dlp を起動できません: {e}")
            self.fetch_backoff_until = self.clock() + 6 * 3600
            return False
        self.state, self.fetch_started = "fetching", self.clock()
        return True

    def _start_extract(self, media):
        self.media = media
        self.wav = os.path.join(self.dir, "current.wav")
        try:
            self.proc = self.popen([self.cfg["ffmpeg_bin"], "-y", "-loglevel", "error", "-i", media,
                                    "-ac", "1", "-ar", "16000", "-f", "wav", self.wav],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.log(f"ffmpeg を起動できません: {e}")
            self._mark_done(media)
            return
        with open(find_subtitle(media, self.cfg["asr_language"]), encoding="utf-8", errors="replace") as f:
            self.cues = [c for c in parse_subtitles(f.read())
                         if 0.8 <= c[1] - c[0] <= 20 and len(normalize_transcript(c[2])) >= 3]
        self.pos, self.cers = 0, []
        self.state = "extracting"

    # ------------------------------------------------------------ 進行
    def step(self, budget_s=1.5):
        """独りのときに毎 tick 呼ぶ。進んだら True。"""
        if self.state == "idle":
            media = self.pending_media()
            if media:
                self._start_extract(media[0])
                return True
            return self._start_fetch()
        if self.state == "fetching":
            if self.proc.poll() is None:
                if self.clock() - self.fetch_started > 1800:
                    self.proc.kill()
                    self.state = "idle"
                return False
            self.state = "idle"
            if not self.pending_media():   # 条件に合う (人手字幕付きの) 動画が無かった / 失敗
                self.fetch_backoff_until = self.clock() + 3600
            return True
        if self.state == "extracting":
            if self.proc.poll() is None:
                return False
            if self.proc.returncode != 0 or not os.path.exists(self.wav):
                self.log(f"音声抽出に失敗: {self.media}")
                self._finish()
                return True
            self.state = "listening"
        if self.state == "listening":
            deadline = time.monotonic() + budget_s
            while self.pos < len(self.cues) and time.monotonic() < deadline:
                self._listen(*self.cues[self.pos])
                self.pos += 1
            if self.pos >= len(self.cues):
                self._finish()
            return True
        return False

    def pause(self):
        """人が来た。進行中の子プロセスを止め、今の動画は途中から再開できるようにする。"""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            if self.state == "extracting":
                self.state = "idle"     # 抽出からやり直す (done にはしない)
            elif self.state == "fetching":
                self.state = "idle"
        # listening 中は pos を保持しているので、次に独りになったら続きから

    def _cut(self, start, end, path):
        pad = 0.15
        with wave.open(self.wav, "rb") as w:
            rate, n = w.getframerate(), w.getnframes()
            a = max(0, int((start - pad) * rate))
            b = min(n, int((end + pad) * rate))
            if b - a < rate * 0.5:
                return False
            w.setpos(a)
            frames = w.readframes(b - a)
            params = w.getparams()
        with wave.open(path, "wb") as o:
            o.setparams(params)
            o.writeframes(frames)
        return True

    def _listen(self, start, end, text):
        key = f"{self.media}|{start:.2f}"
        clip = os.path.join(self.clips, hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".wav")
        if not self._cut(start, end, clip):
            return
        hyp = self.asr.transcribe(clip, prompt=self.prompt()).text
        c = cer(text, hyp)
        self.cers.append(c)
        cfg = self.cfg
        if cfg["study_cer_min"] <= c <= cfg["study_cer_max"]:
            kind = "hard"
            for term in missed_terms(text, hyp)[:3]:
                self.db.execute(
                    "INSERT INTO asr_vocab(term, misses, ts) VALUES (?, 1, ?)"
                    " ON CONFLICT(term) DO UPDATE SET misses=misses+1, ts=excluded.ts",
                    (term, self.clock()))
        elif c < cfg["study_cer_min"] and _keep_easy(key, cfg["study_easy_ratio"]):
            kind = "easy"
        else:
            os.remove(clip)
            return
        self.db.execute(
            "INSERT INTO asr_samples(ts, source, clip, ref, hyp, cer, kind, holdout) VALUES (?,?,?,?,?,?,?,?)",
            (self.clock(), os.path.basename(self.media), clip, text, hyp, c, kind,
             _holdout(key, cfg["study_holdout_ratio"])))
        self.db.commit()

    def _finish(self):
        if self.cers:
            mean = sum(self.cers) / len(self.cers)
            self.db.execute("INSERT INTO asr_log(ts, model, source, cues, mean_cer) VALUES (?,?,?,?,?)",
                            (self.clock(), self.asr.name, os.path.basename(self.media), len(self.cers), mean))
            self.db.commit()
            self.log(f"自習: {os.path.basename(self.media)} {len(self.cers)} 行を聞いた (CER {mean:.3f})")
        self._mark_done(self.media)
        if self.wav and os.path.exists(self.wav):
            os.remove(self.wav)
        self.state, self.media, self.cues, self.pos = "idle", None, [], 0

    # ------------------------------------------------------------ 語彙
    def prompt(self, k=None):
        """よく聞き逃す語を認識プロンプト (Whisper の initial_prompt) に入れる。学習なしで効く補正。"""
        k = k or self.cfg["asr_prompt_terms"]
        rows = self.db.execute("SELECT term FROM asr_vocab ORDER BY misses DESC, ts DESC LIMIT ?", (k,)).fetchall()
        terms = [r[0] for r in rows]
        return "、".join(self.cfg.get("wake_words", [])[:1] + terms) or None

    # ------------------------------------------------------------ 集計
    def samples(self, holdout=None, untrained_only=False):
        q, args = "SELECT * FROM asr_samples WHERE 1=1", []
        if holdout is not None:
            q, args = q + " AND holdout=?", [holdout]
        if untrained_only:
            q += " AND trained_in IS NULL"
        return self.db.execute(q + " ORDER BY id", args).fetchall()

    def count_new_hard(self):
        return self.db.execute("SELECT COUNT(*) FROM asr_samples WHERE holdout=0 AND kind='hard'"
                               " AND trained_in IS NULL").fetchone()[0]

    def recent_cer(self, n=20):
        rows = self.db.execute("SELECT cues, mean_cer FROM asr_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        total = sum(r[0] for r in rows)
        return sum(r[0] * r[1] for r in rows) / total if total else None
