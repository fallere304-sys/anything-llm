"""睡眠中の学習: LoRA ファインチューン → Ollama へ登録 → 検証 → 採用/棄却。

    export (JSONL) → Ollama のモデルを VRAM から降ろす → finetune/train_lora.py (別プロセス)
      → [任意] llama.cpp で GGUF アダプタに変換 → `ollama create <版名>` (FROM 基盤 + ADAPTER)
      → 検証用標本で 新版 と 現行版 を採点 → 劣化していなければ採用

- 毎回「基盤モデル + 全標本」から学習し直す (アダプタを積み重ねない)。
  小さな誤りが版を重ねて累積するのを防ぎ、いつでも基盤に戻せる。
- 重い処理はスレッド + 子プロセスで行い、DB への書き込みは poll() でメインスレッドが行う。
- ユーザーが話しかけたら abort() で即中断する (ユーザー最優先)。
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

from .config import training_python
from . import prompts
from .llm import LLMError
from .text import jaccard

HERE = os.path.dirname(os.path.abspath(__file__))
TRAIN_SCRIPT = os.path.join(os.path.dirname(HERE), "finetune", "train_lora.py")


class Aborted(Exception):
    pass


class StepFailed(Exception):
    pass


def default_runner(args, timeout, abort_event, on_proc):
    """子プロセスを実行し、中断要求があれば terminate する。(returncode, 出力末尾) を返す。"""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    on_proc(proc)
    lines = []

    def reader():
        for line in proc.stdout:
            lines.append(line.rstrip())
            del lines[:-40]
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    start = time.monotonic()
    while proc.poll() is None:
        if abort_event.is_set() or time.monotonic() - start > timeout:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise Aborted("中断" if abort_event.is_set() else "時間切れ")
        time.sleep(0.5)
    t.join(2)
    return proc.returncode, "\n".join(lines[-15:])


class Learner:
    def __init__(self, cfg, data, llm, runner=default_runner, clock=time.time):
        self.cfg, self.data, self.llm = cfg, data, llm
        self.runner, self.clock = runner, clock
        self.busy = False
        self.stage = ""
        self._abort = threading.Event()
        self._results = queue.Queue()
        self._thread = None
        self._proc = None

    # ------------------------------------------------------------- 状態
    def active_model(self):
        return self.data.get("active_model") or self.cfg["model"]

    def should_train(self, idle_s, now=None):
        now = now or self.clock()
        if not self.cfg["finetune_enabled"] or self.busy:
            return False
        if now < (self.data.get("finetune_backoff_until") or 0):
            return False
        return (idle_s >= self.cfg["finetune_after_idle_s"]
                and self.data.count_new() >= self.cfg["finetune_min_new_samples"])

    # ------------------------------------------------------------- 起動
    def start(self, reason=""):
        if self.busy:
            return None
        n = len(self.data.versions()) + 1
        version = f"{self.cfg['model_prefix']}-v{n}"
        vdir = os.path.abspath(os.path.join(self.cfg["finetune_dir"], version))
        os.makedirs(vdir, exist_ok=True)
        train_path, hold_path = os.path.join(vdir, "train.jsonl"), os.path.join(vdir, "holdout.jsonl")
        train_ids, hold = self.data.export(train_path, hold_path)
        if not train_ids:
            return None
        self.busy, self.stage = True, "準備"
        self._abort.clear()
        # スレッド側は DB に触れない: 必要な値はここで読んで渡す
        current = self.active_model()
        self._thread = threading.Thread(
            target=self._pipeline, args=(version, vdir, train_path, train_ids, hold, current),
            daemon=True)
        self._thread.start()
        return f"学習を始めます ({reason or '手動'}): {version} / 学習標本 {len(train_ids)} 件・検証 {len(hold)} 件"

    def abort(self, wait=15):
        if not self.busy:
            return
        self._abort.set()
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        if self._thread is not None:
            self._thread.join(wait)

    # ----------------------------------------------------- パイプライン
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
        return tail

    def _check_abort(self):
        if self._abort.is_set():
            raise Aborted("中断")

    def _pipeline(self, version, vdir, train_path, train_ids, hold, current):
        cfg = self.cfg
        result = {"version": version, "train_ids": train_ids, "ok": False, "adopt": False}
        try:
            self.stage = "VRAM 解放"
            try:
                self.llm.unload(current)
            except LLMError:
                pass    # 既に降りている/Ollama 停止中なら問題なし

            self.stage = "LoRA 学習"
            adapter_dir = os.path.join(vdir, "adapter")
            py = training_python(cfg)
            if py is None:
                raise StepFailed("学習には Python の環境が必要です。config.json の finetune_python に"
                                 " 学習用 venv の python.exe を指定してください")
            self._run([py, TRAIN_SCRIPT, "--base", cfg["hf_base_model"], "--train", train_path,
                       "--out", adapter_dir] + list(cfg["finetune_args"]), cfg["finetune_timeout_s"])
            self._check_abort()

            adapter = adapter_dir
            if cfg["llama_cpp_dir"]:
                self.stage = "GGUF 変換"
                gguf = os.path.join(vdir, "adapter.gguf")
                base = cfg["hf_base_model"]
                base_arg = ["--base", base] if os.path.isdir(base) else ["--base-model-id", base]
                self._run([py, os.path.join(cfg["llama_cpp_dir"], "convert_lora_to_gguf.py"),
                           "--outfile", gguf] + base_arg + [adapter_dir], 1800)
                adapter = gguf

            self.stage = "Ollama 登録"
            modelfile = os.path.join(vdir, "Modelfile")
            with open(modelfile, "w", encoding="utf-8") as f:
                f.write(f"FROM {cfg['model']}\nADAPTER {adapter}\n")
            self._run([cfg["ollama_bin"], "create", version, "-f", modelfile], 1800)
            self._check_abort()

            self.stage = "検証"
            result.update(self._evaluate(version, hold, current))
            result["ok"] = True
        except Aborted as e:
            result["note"] = f"中断: {e}"
            result["aborted"] = True
        except (StepFailed, LLMError, OSError) as e:
            result["note"] = str(e)
        finally:
            self._results.put(result)

    def _score(self, model, samples):
        total = 0.0
        for s in samples:
            self._check_abort()
            system, user, target = (m["content"] for m in s["messages"])
            if s["kind"] == "judge":
                try:
                    out = self.llm.chat(system, user, schema=prompts.JUDGE_SCHEMA,
                                        max_tokens=160, model=model)
                    total += 1.0 if out.get("verdict") == json.loads(target)["verdict"] else 0.0
                except LLMError:
                    pass    # JSON 崩れは 0 点
            else:
                out = self.llm.chat(system, user, max_tokens=200, temperature=0.0, model=model)
                total += jaccard(out, target)
        return total / len(samples)

    def _smoke(self, model):
        """最低限の健全性: JSON を出せる・日本語で空でない返答ができる。"""
        out = self.llm.chat(prompts.JUDGE_SYSTEM, "仮説: 空は青い\n\n根拠: 快晴の空の写真",
                            schema=prompts.JUDGE_SCHEMA, max_tokens=120, model=model)
        if out.get("verdict") not in ("supports", "partially_supports", "contradicts", "irrelevant"):
            return False
        return bool(self.llm.chat(prompts.PERSONA, "こんにちは", max_tokens=40, model=model).strip())

    def _evaluate(self, version, hold, current):
        cfg = self.cfg
        try:
            healthy = self._smoke(version)
        except LLMError:
            healthy = False
        if not healthy:
            return {"adopt": False, "note": "健全性検査に失敗 (JSON/応答が壊れている)"}
        hold = hold[: cfg["finetune_eval_max"]]
        if len(hold) < cfg["finetune_min_holdout"]:
            return {"adopt": True, "score": None, "base_score": None,
                    "note": f"検証標本 {len(hold)} 件のため健全性検査のみで採用"}
        new = self._score(version, hold)
        old = self._score(current, hold)
        adopt = new >= old - cfg["finetune_tolerance"]
        return {"adopt": adopt, "score": new, "base_score": old,
                "note": f"検証スコア 新 {new:.3f} / 現行 {old:.3f}"}

    # ------------------------------------------ 結果の反映 (メインスレッド)
    def poll(self):
        try:
            r = self._results.get_nowait()
        except queue.Empty:
            return None
        self.busy, self.stage = False, ""
        if self._thread is not None:
            self._thread.join(5)
        v = r["version"]
        self.data.add_version(v, len(r["train_ids"]), r.get("score"), r.get("base_score"),
                              r.get("adopt"), r.get("note", ""))
        if r.get("aborted"):
            return f"学習 {v} を中断しました。"
        if not r["ok"]:
            self.data.set("finetune_backoff_until", self.clock() + self.cfg["finetune_backoff_s"])
            return f"学習 {v} に失敗しました (しばらく再試行しません): {r.get('note', '')}"
        self.data.mark_trained(r["train_ids"], v)
        if r["adopt"]:
            self.data.set("active_model", v)
            self.llm.model = v
            return f"学習完了。{v} に切り替えました。{r.get('note', '')}"
        return f"学習した {v} は現行より悪かったので採用しません。{r.get('note', '')}"

    def rollback(self):
        """一つ前に採用した版 (無ければ基盤モデル) に戻す。"""
        adopted = [row["name"] for row in self.data.versions() if row["adopted"]]
        current = self.active_model()
        idx = adopted.index(current) if current in adopted else 0
        target = adopted[idx - 1] if idx > 0 else self.cfg["model"]
        self.data.set("active_model", None if target == self.cfg["model"] else target)
        self.llm.model = target
        return target
