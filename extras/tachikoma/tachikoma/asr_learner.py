"""耳の学習: 自習で溜めた (音声クリップ, 字幕) の対で Whisper を LoRA 微調整し、
字幕の検証セットで CER が下がったときだけ差し替える。

    export → train_whisper.py (LoRA → 基盤に統合して保存) → ct2-transformers-converter (int8)
      → 新旧の耳で検証クリップを認識して CER を比較 → 改善していれば採用

「微増」を確実に取りにいく設計:
- 採用条件は「検証 CER が現行より小さい」こと。悪化も横ばいも採用しない
- 利用者の声の録音 (asr_anchor_dir の wav + 同名 txt) があれば、そちらも悪化していないことを要求する
  (動画の話者に過適応して、肝心の利用者の声が聞き取れなくなるのを防ぐ)
- 毎回、元の Whisper から学習し直す。/ear_rollback で戻せる
"""

import json
import os
import queue
import sys
import threading
import time

from .config import training_python
from .learner import Aborted, StepFailed, default_runner
from .text import cer

HERE = os.path.dirname(os.path.abspath(__file__))
TRAIN_SCRIPT = os.path.join(os.path.dirname(HERE), "finetune", "train_whisper.py")


def anchor_set(directory):
    """利用者の声の検証セット: foo.wav と foo.txt の対。"""
    out = []
    if directory and os.path.isdir(directory):
        for fn in sorted(os.listdir(directory)):
            if fn.lower().endswith(".wav"):
                txt = os.path.join(directory, fn[:-4] + ".txt")
                if os.path.exists(txt):
                    with open(txt, encoding="utf-8") as f:
                        out.append((os.path.join(directory, fn), f.read().strip()))
    return out


class AsrLearner:
    def __init__(self, cfg, study, data, asr, asr_factory, runner=default_runner, clock=time.time,
                 llm=None):
        self.cfg, self.study, self.data, self.asr = cfg, study, data, asr
        self.llm = llm                      # 学習前に Gemma を VRAM から降ろすため
        self.asr_factory = asr_factory      # モデルのパス → 認識器 (検証用に別インスタンスを作る)
        self.runner, self.clock = runner, clock
        self.busy, self.stage = False, ""
        self._abort = threading.Event()
        self._results = queue.Queue()
        self._thread = self._proc = None

    def active_model(self):
        return self.data.get("active_asr") or self.cfg["asr_model"]

    def should_train(self, alone_s, now=None):
        now = now or self.clock()
        if not self.cfg["asr_finetune_enabled"] or self.busy:
            return False
        if now < (self.data.get("asr_backoff_until") or 0):
            return False
        return (alone_s >= self.cfg["asr_train_after_alone_s"]
                and self.study.count_new_hard() >= self.cfg["asr_min_new_samples"])

    def start(self, reason=""):
        if self.busy:
            return None
        n = len(self.data.get("asr_versions") or []) + 1
        version = f"ear-v{n}"
        vdir = os.path.abspath(os.path.join(self.cfg["finetune_dir"], version))
        os.makedirs(vdir, exist_ok=True)
        train = [r for r in self.study.samples(holdout=0)][-self.cfg["asr_max_train"]:]
        hold = [(r["clip"], r["ref"]) for r in self.study.samples(holdout=1)][-self.cfg["asr_eval_max"]:]
        if not train or len(hold) < self.cfg["asr_min_holdout"]:
            return None
        path = os.path.join(vdir, "train.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in train:
                f.write(json.dumps({"audio": r["clip"], "text": r["ref"],
                                    "weight": 1.5 if r["kind"] == "hard" else 1.0}, ensure_ascii=False) + "\n")
        self.busy, self.stage = True, "準備"
        self._abort.clear()
        current = self.active_model()
        self._thread = threading.Thread(
            target=self._pipeline, args=(version, vdir, path, [r["id"] for r in train], hold, current),
            daemon=True)
        self._thread.start()
        return f"耳の学習を始めます ({reason or '手動'}): {version} / 学習 {len(train)} 件・検証 {len(hold)} 件"

    def abort(self, wait=15):
        if not self.busy:
            return
        self._abort.set()
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        if self._thread is not None:
            self._thread.join(wait)

    def _run(self, args, timeout):
        def on_proc(p):
            self._proc = p
        try:
            rc, tail = self.runner(args, timeout, self._abort, on_proc)
        except FileNotFoundError as e:
            raise StepFailed(f"実行できません: {args[0]} ({e})")
        finally:
            self._proc = None
        if rc != 0:
            raise StepFailed(f"{os.path.basename(args[1]) if len(args) > 1 else args[0]} が失敗 (rc={rc})\n{tail}")

    def _mean_cer(self, model, pairs):
        asr = self.asr_factory(model)
        total = 0.0
        for clip, ref in pairs:
            if self._abort.is_set():
                raise Aborted("中断")
            total += cer(ref, asr.transcribe(clip).text)
        return total / len(pairs)

    def _pipeline(self, version, vdir, train_path, train_ids, hold, current):
        cfg = self.cfg
        result = {"version": version, "train_ids": train_ids, "ok": False, "adopt": False}
        try:
            py = training_python(cfg)
            if py is None:
                raise StepFailed("学習には Python の環境が必要です。config.json の finetune_python に"
                                 " 学習用 venv の python.exe を指定してください")
            merged, ct2 = os.path.join(vdir, "merged"), os.path.join(vdir, "ct2")
            if self.llm is not None:
                self.stage = "VRAM 解放"
                try:
                    self.llm.unload()
                except Exception:   # noqa: BLE001 — 既に降りていれば問題なし
                    pass
            self.stage = "Whisper 学習"
            self._run([py, TRAIN_SCRIPT, "--base", cfg["asr_hf_base"], "--train", train_path,
                       "--out", merged, "--language", cfg["asr_language"]] + list(cfg["asr_finetune_args"]),
                      cfg["finetune_timeout_s"])
            self.stage = "CTranslate2 変換"
            conv = cfg.get("ct2_converter") or os.path.join(
                os.path.dirname(py), "ct2-transformers-converter" + (".exe" if sys.platform == "win32" else ""))
            self._run([conv, "--model", merged, "--output_dir", ct2, "--quantization", cfg["asr_compute_type"],
                       "--copy_files", "tokenizer.json", "preprocessor_config.json", "--force"], 1800)
            self.stage = "検証"
            new, old = self._mean_cer(ct2, hold), self._mean_cer(current, hold)
            note = f"字幕検証 CER 新 {new:.4f} / 現行 {old:.4f}"
            adopt = new < old
            anchors = anchor_set(cfg.get("asr_anchor_dir"))
            if adopt and anchors:
                a_new, a_old = self._mean_cer(ct2, anchors), self._mean_cer(current, anchors)
                note += f" / 利用者の声 CER 新 {a_new:.4f} / 現行 {a_old:.4f}"
                adopt = a_new <= a_old + cfg["asr_anchor_tolerance"]
            result.update(ok=True, adopt=adopt, path=ct2, score=new, base_score=old, note=note)
        except Aborted as e:
            result.update(note=f"中断: {e}", aborted=True)
        except (StepFailed, OSError, RuntimeError, ImportError, ValueError) as e:
            result["note"] = str(e)
        finally:
            self._results.put(result)

    def poll(self):
        try:
            r = self._results.get_nowait()
        except queue.Empty:
            return None
        self.busy, self.stage = False, ""
        if self._thread is not None:
            self._thread.join(5)
        versions = self.data.get("asr_versions") or []
        versions.append({k: r.get(k) for k in ("version", "path", "score", "base_score", "adopt", "note")})
        self.data.set("asr_versions", versions)
        v = r["version"]
        if r.get("aborted"):
            return f"耳の学習 {v} を中断しました。"
        if not r["ok"]:
            self.data.set("asr_backoff_until", self.clock() + self.cfg["finetune_backoff_s"])
            return f"耳の学習 {v} に失敗しました: {r.get('note', '')}"
        self.study.db.executemany("UPDATE asr_samples SET trained_in=? WHERE id=?",
                                  [(v, i) for i in r["train_ids"]])
        self.study.db.commit()
        if r["adopt"]:
            self.data.set("active_asr", r["path"])
            self.asr.load(r["path"])
            return f"耳の学習完了。{v} に切り替えました ({r['note']})"
        return f"耳の学習 {v} は改善しなかったので採用しません ({r['note']})"

    def rollback(self):
        adopted = [v["path"] for v in (self.data.get("asr_versions") or []) if v.get("adopt")]
        current = self.active_model()
        idx = adopted.index(current) if current in adopted else 0
        target = adopted[idx - 1] if idx > 0 else self.cfg["asr_model"]
        self.data.set("active_asr", None if target == self.cfg["asr_model"] else target)
        self.asr.load(target)
        return target
