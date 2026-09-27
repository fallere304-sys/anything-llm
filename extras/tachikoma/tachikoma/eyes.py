"""目の自習: 文字情報付き PDF を「画像として」読み、埋め込まれた文字情報で答え合わせする。

好奇心の定義を視覚に当てはめたもの: 自分の読み取り = 推定、PDF の文字情報 = それを確かめる情報。

    PDF → ページを画像に描画 (解像度を変えて、画面・印刷・撮影の違いを模す)
      → 文字情報から各行の位置・正解文字列・フォントを取得 → 行を切り出して今の目で読む
      → 正解と比べて CER → 学べる誤りだけ標本にする (耳の自習と同じ帯域選別)
      → よく間違える文字の組 (例: ロ→口) を数え、検証で効果が確かめられた置換だけ「補正」に使う

フォントの多様性: 行ごとのフォント名を記録し、フォント別の誤り率を持つ。
次に読む PDF は「未知のフォント」か「苦手だが伸びているフォント」を含むものを優先する (好奇心)。

教材: study/eye/ に置いた PDF と、(web 有効時) 政府機関の公開 PDF (site:go.jp)。
日本の政府機関のウェブ上の文書の多くは「政府標準利用規約」により出典を示せば複製・加工できる
[利用規約は文書ごとに要確認]。文字情報の壊れた PDF (フォントの対応表が無く文字化けする等) は
正解として使えないので、私用領域の文字・置換文字・日本語の少ない行は捨てる。
"""

import difflib
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

from .text import cer, normalize_transcript

SCHEMA = """
CREATE TABLE IF NOT EXISTS ocr_samples (
    id INTEGER PRIMARY KEY, ts REAL, source TEXT, font TEXT, crop TEXT, ref TEXT, hyp TEXT,
    cer REAL, kind TEXT, holdout INTEGER, trained_in TEXT
);
CREATE TABLE IF NOT EXISTS ocr_confusions (wrong TEXT, right TEXT, n INTEGER, PRIMARY KEY (wrong, right));
CREATE TABLE IF NOT EXISTS ocr_fonts (font TEXT PRIMARY KEY, lines INTEGER, cer_sum REAL, hard INTEGER);
CREATE TABLE IF NOT EXISTS ocr_log (id INTEGER PRIMARY KEY, ts REAL, model TEXT, source TEXT, lines INTEGER,
    mean_cer REAL);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

_JA = re.compile(r"[぀-ヿ一-鿿]")
_BAD = re.compile(r"[-�]")


def valid_truth(text):
    """正解として使える文字情報か (文字化け・私用領域・日本語の無い行を除く)。"""
    t = (text or "").strip()
    if len(normalize_transcript(t)) < 3 or _BAD.search(t):
        return False
    return len(_JA.findall(t)) >= max(2, len(t) * 0.3)


def base_font(name):
    """'ABCDEF+MS-Gothic' のような埋め込みサブセット名から、元のフォント名を取り出す。"""
    return re.sub(r"^[A-Z]{6}\+", "", name or "?")


def extract_lines(page):
    """横書きの行: [(bbox, 正解文字列, 主なフォント, 文字サイズ)]"""
    out = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            if abs(line.get("dir", (1, 0))[0] - 1.0) > 1e-3:   # 縦書き・回転は今は扱わない
                continue
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            main = max(spans, key=lambda s: len(s["text"]))
            out.append((tuple(line["bbox"]), text, base_font(main.get("font")), main.get("size", 0)))
    return out


def char_confusions(ref, hyp):
    """1 文字どうしの置換 (読み間違い) を取り出す。"""
    r, h = normalize_transcript(ref), normalize_transcript(hyp)
    pairs = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, h, r, autojunk=False).get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1:
            pairs += [(h[i1 + k], r[j1 + k]) for k in range(i2 - i1)]
    return pairs


def _h(key, mod=1000):
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % mod


class EyeStudy:
    def __init__(self, cfg, memory, ocr, clock=time.time, pdf_lib=None, opener=urllib.request.urlopen,
                 log=print):
        self.cfg, self.memory, self.ocr, self.clock = cfg, memory, ocr, clock
        self.db = memory.db
        self.db.executescript(SCHEMA)
        self.opener, self.log = opener, log
        self.pdf = pdf_lib
        self.dir = os.path.abspath(cfg["eye_dir"])
        self.crops = os.path.join(self.dir, "crops")
        os.makedirs(self.crops, exist_ok=True)
        self.state = "idle"
        self.doc = self.doc_path = None
        self.page_no, self.queue, self.cers = 0, [], []
        self.fetch_backoff_until = 0.0

    def _lib(self):
        if self.pdf is None:
            import pymupdf
            self.pdf = pymupdf
        return self.pdf

    def _kv(self, k, v=None):
        if v is None:
            row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return json.loads(row[0]) if row else None
        self.db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, json.dumps(v)))
        self.db.commit()

    # ------------------------------------------------------------ 教材
    def pending_pdfs(self):
        done = set(self._kv("eye_done") or [])
        out = []
        for d in [self.dir] + list(self.cfg.get("eye_pdf_dirs", [])):
            if os.path.isdir(d):
                for root, _, files in os.walk(d):
                    out += [os.path.join(root, f) for f in sorted(files)
                            if f.lower().endswith(".pdf") and os.path.join(root, f) not in done]
        return out

    def font_stats(self):
        return {r["font"]: (r["lines"], r["cer_sum"] / max(1, r["lines"]), r["hard"])
                for r in self.db.execute("SELECT * FROM ocr_fonts")}

    def pick_next(self, candidates):
        """未知のフォント・苦手で学べるフォントを多く含む PDF を優先する (目の好奇心)。"""
        stats = self.font_stats()

        def score(path):
            try:
                doc = self._lib().open(path)
                fonts = {base_font(f[3]) for i in range(min(3, len(doc))) for f in doc[i].get_fonts()}
                doc.close()
            except Exception:  # noqa: BLE001 — 壊れた PDF
                return -1
            s = 0.0
            for f in fonts:
                if f not in stats:
                    s += 1.0                       # 見たことのないフォント
                else:
                    n, mean, hard = stats[f]
                    s += min(1.0, hard / max(1, n) * 2) * (1.0 / (1 + n / 500))   # 苦手 × まだ慣れていない
            return s
        scored = sorted(((score(p), p) for p in candidates[:10]), reverse=True)
        return scored[0][1] if scored and scored[0][0] >= 0 else None

    def ready(self):
        return bool(self.pending_pdfs()) or bool(self.cfg.get("web") and self.cfg.get("searxng_url")
                                               and self.clock() >= self.fetch_backoff_until)

    def fetch_one(self):
        """政府機関の公開 PDF を 1 つ取ってくる (SearXNG で filetype:pdf site:go.jp)。"""
        base = (self.cfg.get("searxng_url") or "").rstrip("/")
        if not base:
            return False
        topics = self.cfg["eye_fetch_topics"]
        topic = topics[_h(str(self.clock()), len(topics))]
        try:
            req = urllib.request.Request(f"{base}/search?format=json&q=" +
                                         urllib.parse.quote(f"{topic} filetype:pdf site:go.jp"))
            with self.opener(req, timeout=20) as r:
                results = json.loads(r.read().decode("utf-8")).get("results", [])
            seen = set(self._kv("eye_fetched") or [])
            for res in results:
                url = res.get("url", "")
                host = urllib.parse.urlparse(url).hostname or ""
                if not url.lower().endswith(".pdf") or not host.endswith(".go.jp") or url in seen:
                    continue
                with self.opener(urllib.request.Request(url, headers={"User-Agent": "Tachikoma/0.1"}),
                                 timeout=60) as r:
                    data = r.read(self.cfg["eye_max_pdf_bytes"] + 1)
                seen.add(url)
                self._kv("eye_fetched", sorted(seen))
                if len(data) > self.cfg["eye_max_pdf_bytes"] or not data.startswith(b"%PDF"):
                    continue
                name = hashlib.sha1(url.encode()).hexdigest()[:12] + ".pdf"
                with open(os.path.join(self.dir, name), "wb") as f:
                    f.write(data)
                with open(os.path.join(self.dir, name + ".source.txt"), "w", encoding="utf-8") as f:
                    f.write(url + "\n")   # 出典の記録 (政府標準利用規約は出典の明示が条件)
                return True
        except (OSError, ValueError):
            pass
        self.fetch_backoff_until = self.clock() + 3600
        return False

    # ------------------------------------------------------------ 進行
    def step(self, budget_s=1.5):
        if self.state == "idle":
            cands = self.pending_pdfs()
            if not cands:
                return self.fetch_one()
            path = self.pick_next(cands)
            if path is None:
                self._mark_done(cands[0])
                return True
            try:
                self.doc = self._lib().open(path)
            except Exception:  # noqa: BLE001
                self._mark_done(path)
                return True
            self.doc_path, self.page_no, self.queue, self.cers = path, 0, [], []
            self.state = "reading"
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            if not self.queue:
                if self.page_no >= min(len(self.doc), self.cfg["eye_max_pages"]):
                    self._finish()
                    return True
                page = self.doc[self.page_no]
                self.queue = [(self.page_no, *ln) for ln in extract_lines(page) if valid_truth(ln[1])]
                self.page_no += 1
                continue
            self._read(*self.queue.pop(0))
        return True

    def pause(self):
        pass     # 位置 (ページ・行) を保持しているので、次に独りになったら続きから

    def _read(self, page_no, bbox, text, font, size):
        key = f"{self.doc_path}|{page_no}|{bbox}"
        dpis = self.cfg["eye_dpis"]
        dpi = dpis[_h(key, len(dpis))]          # 解像度を変えて、画面・印刷・撮影の違いを模す
        crop = os.path.join(self.crops, hashlib.sha1(key.encode()).hexdigest()[:16] + ".png")
        x0, y0, x1, y1 = bbox
        pad = max(2.0, size * 0.15)
        page = self.doc[page_no]
        pix = page.get_pixmap(dpi=dpi, clip=self._lib().Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad))
        if pix.width < 16 or pix.height < 8:
            return
        pix.save(crop)
        hyp = self.ocr.recognize(crop)
        c = cer(text, hyp)
        self.cers.append(c)
        cfg = self.cfg
        if cfg["eye_cer_min"] <= c <= cfg["eye_cer_max"]:
            kind = "hard"
            for w, r in char_confusions(text, hyp):
                self.db.execute("INSERT INTO ocr_confusions(wrong, right, n) VALUES (?,?,1)"
                                " ON CONFLICT(wrong, right) DO UPDATE SET n=n+1", (w, r))
        elif c < cfg["eye_cer_min"] and _h(key + "e") < cfg["eye_easy_ratio"] * 1000:
            kind = "easy"
        else:
            kind = None
        self.db.execute("INSERT INTO ocr_fonts(font, lines, cer_sum, hard) VALUES (?,1,?,?)"
                        " ON CONFLICT(font) DO UPDATE SET lines=lines+1, cer_sum=cer_sum+excluded.cer_sum,"
                        " hard=hard+excluded.hard", (font, min(c, 1.0), 1 if kind == "hard" else 0))
        if kind is None:
            os.remove(crop)
        else:
            self.db.execute("INSERT INTO ocr_samples(ts, source, font, crop, ref, hyp, cer, kind, holdout)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            (self.clock(), os.path.basename(self.doc_path), font, crop, text, hyp, c, kind,
                             1 if _h(key + "h") < cfg["eye_holdout_ratio"] * 1000 else 0))
        self.db.commit()

    def _mark_done(self, path):
        done = set(self._kv("eye_done") or [])
        done.add(path)
        self._kv("eye_done", sorted(done))

    def _finish(self):
        if self.cers:
            mean = sum(self.cers) / len(self.cers)
            self.db.execute("INSERT INTO ocr_log(ts, model, source, lines, mean_cer) VALUES (?,?,?,?,?)",
                            (self.clock(), self.ocr.name, os.path.basename(self.doc_path), len(self.cers), mean))
            self.db.commit()
            self.log(f"目の自習: {os.path.basename(self.doc_path)} {len(self.cers)} 行を読んだ (CER {mean:.3f})")
        self.doc.close()
        self._mark_done(self.doc_path)
        self.learn_corrections()
        self.state, self.doc, self.doc_path, self.queue = "idle", None, None, []

    # ------------------------------------------------------------ 補正 (学習なしで効く層)
    def learn_corrections(self):
        """よくある読み間違いの置換のうち、検証標本で CER を下げるものだけを採用する。"""
        cands = self.db.execute("SELECT wrong, right, n FROM ocr_confusions WHERE n>=? ORDER BY n DESC LIMIT 50",
                                (self.cfg["eye_rule_min_count"],)).fetchall()
        hold = self.db.execute("SELECT ref, hyp FROM ocr_samples WHERE holdout=1").fetchall()
        rules = []
        for c in cands:
            w, r = c["wrong"], c["right"]
            affected = [h for h in hold if w in h["hyp"]]
            if len(affected) < 2:
                continue
            before = sum(cer(h["ref"], h["hyp"]) for h in affected)
            after = sum(cer(h["ref"], h["hyp"].replace(w, r)) for h in affected)
            if after < before:
                rules.append([w, r])
        self._kv("eye_rules", rules)
        return rules

    def correct(self, text):
        for w, r in self._kv("eye_rules") or []:
            text = text.replace(w, r)
        return text

    # ------------------------------------------------------------ 集計
    def samples(self, holdout=None):
        q, args = "SELECT * FROM ocr_samples", []
        if holdout is not None:
            q, args = q + " WHERE holdout=?", [holdout]
        return self.db.execute(q + " ORDER BY id", args).fetchall()

    def count_new_hard(self):
        return self.db.execute("SELECT COUNT(*) FROM ocr_samples WHERE holdout=0 AND kind='hard'"
                               " AND trained_in IS NULL").fetchone()[0]

    def count_hard(self):
        return self.db.execute("SELECT COUNT(*) FROM ocr_samples WHERE kind='hard'").fetchone()[0]

    def recent_cer(self, n=20):
        rows = self.db.execute("SELECT lines, mean_cer FROM ocr_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        total = sum(r[0] for r in rows)
        return sum(r[0] * r[1] for r in rows) / total if total else None
