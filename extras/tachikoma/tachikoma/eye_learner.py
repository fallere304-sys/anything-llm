"""目の学習: 自習で溜めた (行画像, 正解文字列) の対で OCR モデルを微調整し、検証で改善したときだけ差し替える。

採用条件 (耳と同じく「微増でも確実に」):
- 検証行の平均 CER が現行より小さい (横ばいは不採用)
- フォント別に見て、どのフォントも eye_font_tolerance を超えて悪化していない
  (得意なフォントを伸ばすために、苦手なフォントを犠牲にしていないか)
毎回、元のモデルから学習し直す。/eye_rollback で戻せる。
"""

import json
import os
import queue
import sys
import threading
import time
from collections import defaultdict

from .config import training_python
from .learner import Aborted, StepFailed, default_runner
from .text import cer

HERE = os.path.dirname(os.path.abspath(__file__))
TRAIN_SCRIPT = os.path.join(os.path.dirname(HERE), "finetune", "train_ocr.py")


class EyeLearner:
    def __init__(self, cfg, eyes, data, ocr, ocr_factory, runner=default_runner, clock=time.time, llm=None):
        self.cfg, self.eyes, self.data, self.ocr = cfg, eyes, data, ocr
        self.ocr_factory, self.runner, self.clock, self.llm = ocr_factory, runner, clock, llm
        self.busy, self.stage = False, ""
        self._abort = threading.Event()
        self._results = queue.Queue()
        self._thread = self._proc = None

    def active_model(self):
        return self.data.get("active_ocr") or self.cfg["ocr_model"]

    def should_train(self, alone_s, now=None):
        now = now or self.clock()
        if not self.cfg["ocr_finetune_enabled"] or self.busy:
            return False
        if now < (self.data.get("ocr_backoff_until") or 0):
            return False
        return (alone_s >= self.cfg["eye_train_after_alone_s"]
                and self.eyes.count_new_hard() >= self.cfg["eye_min_new_samples"])

    def start(self, reason=""):
        if self.busy:
            return None
        version = f"eye-v{len(self.data.get('ocr_versions') or []) + 1}"
        vdir = os.path.abspath(os.path.join(self.cfg["finetune_dir"], version))
        os.makedirs(vdir, exist_ok=True)
        train = list(self.eyes.samples(holdout=0))[-self.cfg["eye_max_train"]:]
        hold = [(r["crop"], r["ref"], r["font"]) for r in self.eyes.samples(holdout=1)][-self.cfg["eye_eval_max"]:]
        if not train or len(hold) < self.cfg["eye_min_holdout"]:
            return None
        path = os.path.join(vdir, "train.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in train:
                f.write(json.dumps({"image": r["crop"], "text": r["ref"],
                                    "weight": 1.5 if r["kind"] == "hard" else 1.0}, ensure_ascii=False) + "\n")
        self.busy, self.stage = True, "準備"
        self._abort.clear()
        current = self.active_model()
        self._thread = threading.Thread(target=self._pipeline,
                                        args=(version, vdir, path, [r["id"] for r in train], hold, current),
                                        daemon=True)
        self._thread.start()
        return f"目の学習を始めます ({reason or '手動'}): {version} / 学習 {len(train)} 行・検証 {len(hold)} 行"

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

    def _cer_by_font(self, model, hold):
        ocr = self.ocr_factory(model)
        per = defaultdict(list)
        for crop, ref, font in hold:
            if self._abort.is_set():
                raise Aborted("中断")
            per[font].append(cer(ref, ocr.recognize(crop)))
        allv = [v for vs in per.values() for v in vs]
        return sum(allv) / len(allv), {f: sum(v) / len(v) for f, v in per.items()}

    def _pipeline(self, version, vdir, train_path, train_ids, hold, current):
        cfg = self.cfg
        result = {"version": version, "train_ids": train_ids, "ok": False, "adopt": False}
        try:
            if self.llm is not None:
                self.stage = "VRAM 解放"
                try:
                    self.llm.unload()
                except Exception:  # noqa: BLE001
                    pass
            self.stage = "OCR 学習"
            out = os.path.join(vdir, "model")
            py = training_python(cfg)
            if py is None:
                raise StepFailed("学習には Python の環境が必要です。config.json の finetune_python に"
                                 " 学習用 venv の python.exe を指定してください")
            self._run([py, TRAIN_SCRIPT, "--base", cfg["ocr_model"],   # 毎回、元のモデルから学習し直す
                       "--train", train_path, "--out", out] + list(cfg["ocr_finetune_args"]),
                      cfg["finetune_timeout_s"])
            self.stage = "検証"
            new, new_f = self._cer_by_font(out, hold)
            old, old_f = self._cer_by_font(current, hold)
            worst = max(((new_f[f] - old_f[f]), f) for f in new_f)
            note = f"検証 CER 新 {new:.4f} / 現行 {old:.4f} / 最も悪化したフォント {worst[1]} {worst[0]:+.4f}"
            adopt = new < old and worst[0] <= cfg["eye_font_tolerance"]
            result.update(ok=True, adopt=adopt, path=out, score=new, base_score=old, note=note)
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
        versions = self.data.get("ocr_versions") or []
        versions.append({k: r.get(k) for k in ("version", "path", "score", "base_score", "adopt", "note")})
        self.data.set("ocr_versions", versions)
        v = r["version"]
        if r.get("aborted"):
            return f"目の学習 {v} を中断しました。"
        if not r["ok"]:
            self.data.set("ocr_backoff_until", self.clock() + self.cfg["finetune_backoff_s"])
            return f"目の学習 {v} に失敗しました: {r.get('note', '')}"
        self.eyes.db.executemany("UPDATE ocr_samples SET trained_in=? WHERE id=?", [(v, i) for i in r["train_ids"]])
        self.eyes.db.commit()
        if r["adopt"]:
            self.data.set("active_ocr", r["path"])
            self.ocr.load(r["path"])
            return f"目の学習完了。{v} に切り替えました ({r['note']})"
        return f"目の学習 {v} は改善しなかったので採用しません ({r['note']})"

    def rollback(self):
        adopted = [v["path"] for v in (self.data.get("ocr_versions") or []) if v.get("adopt")]
        current = self.active_model()
        idx = adopted.index(current) if current in adopted else 0
        target = adopted[idx - 1] if idx > 0 else self.cfg["ocr_model"]
        self.data.set("active_ocr", None if target == self.cfg["ocr_model"] else target)
        self.ocr.load(target)
        return target
