"""端末の学習: 学習データは端末で集め、重みの計算は PC に預け、採否は端末で PC 版と同じ規則で決める。

RAM 4GB の端末に PyTorch と学習中の勾配は載らない [合理的推定] ので、LoRA の学習そのものは PC (GPU) で行う:

    端末  /export  … 学習標本 (やり取り・自律調査で確定した知識・判定) と検証標本を zip で書き出す
    PC    finetune/android_adapter.py … 端末と同じ基盤モデルで LoRA を学習し、GGUF のアダプタにする
    端末  取り込み … アダプタを当てて、健全性検査 (JSON が出せる・日本語で返せる) と
                    検証標本の採点を行い、現行より悪くなければ採用 (PC 版の Learner と同じ _evaluate)

採用した版は /rollback で戻せる。基盤モデルに戻すこともできる。
"""

import os
import shutil
import threading
import time
import zipfile

from ..learner import Aborted, Learner


class AndroidLearner(Learner):
    GUIDE = ("端末では重みの学習の計算はできないんだ (RAM が足りない)。`/export` で学習データを書き出して、"
             "PC のタチコマで学習したアダプタを取り込んでね。")

    def should_train(self, idle_s, now=None):
        return False

    def start(self, reason=""):
        return self.GUIDE

    def export(self, out_dir):
        """学習標本と検証標本を zip にまとめる。PC の finetune/android_adapter.py がそのまま読める形。"""
        os.makedirs(out_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tmp = os.path.join(out_dir, f"export-{stamp}")
        os.makedirs(tmp, exist_ok=True)
        train_ids, hold = self.data.export(os.path.join(tmp, "train.jsonl"), os.path.join(tmp, "holdout.jsonl"))
        path = os.path.join(out_dir, f"tachikoma-train-{stamp}.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for n in ("train.jsonl", "holdout.jsonl"):
                zf.write(os.path.join(tmp, n), n)
            zf.writestr("base_model.txt", self.cfg["hf_base_model"])
        shutil.rmtree(tmp, ignore_errors=True)
        return path, len(train_ids), len(hold)

    def import_adapter(self, src):
        """取り込んだアダプタを、PC 版と同じ規則で検証する (結果は poll() で反映)。"""
        if self.busy:
            return None
        n = len(self.data.versions()) + 1
        version = f"{self.cfg['model_prefix']}-android-v{n}"
        os.makedirs(self.cfg["adapters_dir"], exist_ok=True)
        shutil.copyfile(src, self.llm.adapter_path(version))
        hold = self.data.samples(holdout=1)
        current = self.active_model()
        self.busy, self.stage = True, "アダプタの検証"
        self._abort.clear()
        self._thread = threading.Thread(target=self._check, args=(version, hold, current), daemon=True)
        self._thread.start()
        return f"アダプタを取り込んだよ。{version} として検証します (検証標本 {len(hold)} 件)。"

    def _check(self, version, hold, current):
        result = {"version": version, "train_ids": [], "ok": False, "adopt": False}
        try:
            result.update(self._evaluate(version, hold, current), ok=True)
        except Aborted:
            result["aborted"] = True
        except Exception as e:  # noqa: BLE001 — 検証の失敗で本体を止めない
            result["note"] = f"検証に失敗: {e}"
        if not result.get("adopt"):
            try:
                os.remove(self.llm.adapter_path(version))      # 採用しない版は置いておかない
            except OSError:
                pass
        self._results.put(result)
