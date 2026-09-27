"""資源の予算: VRAM 6GB だけに頼らず、RAM・CPU・SSD・Docker を配分して使い切る。

上限は利用者が config で決める。タチコマは自分の上限を上げられない (カーネル)。

    VRAM   6 GB  … 会話用の Gemma (全層) と Whisper を優先。溢れた層は RAM へ (num_gpu)
    RAM    8 GB  … 部分オフロードした層・大きめのコード生成モデル・Docker コンテナ
    CPU    6 スレッド … i7-7700 は 4 コア 8 スレッド。「6 コア」は論理 6 スレッドと解釈する
    SSD   20 GB  … 学習成果・自習クリップ・圧縮実験・進化の履歴 (超えたら古い順に掃除)
"""

import os
import shutil


class Budget:
    def __init__(self, cfg):
        self.cfg = cfg
        self.vram_gb = cfg["budget_vram_gb"]
        self.ram_gb = cfg["budget_ram_gb"]
        self.threads = min(cfg["budget_threads"], os.cpu_count() or cfg["budget_threads"])
        self.disk_gb = cfg["budget_disk_gb"]

    # ------------------------------------------------------------ CPU
    def llm_threads(self, mode):
        """会話中は Whisper と VAD に 2 スレッド残す。独りの時間は全部使う。"""
        return self.threads if mode == "alone" else max(2, self.threads - 2)

    def asr_threads(self, mode):
        return 2 if mode != "alone" else max(2, self.threads // 2)

    # ------------------------------------------------------------ Docker
    def docker_flags(self, ram_gb=None, threads=None):
        ram = min(ram_gb or self.ram_gb, self.ram_gb)
        cpus = min(threads or self.threads, self.threads)
        return ["--cpus", str(cpus), "--memory", f"{ram:g}g", "--memory-swap", f"{ram:g}g",
                "--pids-limit", "512"]

    # ------------------------------------------------------------ RAM
    def ram_in_use_gb(self, ollama_ps=None):
        """自分のプロセス群 + Ollama が RAM 側に置いているモデルの大きさ (推定)。"""
        used = 0.0
        try:
            import psutil
            me = psutil.Process()
            used += sum(p.memory_info().rss for p in [me] + me.children(recursive=True)) / 2**30
        except Exception:  # noqa: BLE001 — psutil は任意
            pass
        for m in (ollama_ps or []):
            used += max(0, m.get("size", 0) - m.get("size_vram", 0)) / 2**30
        return used

    def ram_available_gb(self, ollama_ps=None):
        return max(0.0, self.ram_gb - self.ram_in_use_gb(ollama_ps))


class Storage:
    """SSD の予算。管理下のフォルダ + Ollama に作った自分のモデルを合計する。"""

    def __init__(self, cfg, extra_bytes_fn=None):
        self.cfg = cfg
        self.limit = cfg["budget_disk_gb"] * 2**30
        self.roots = [cfg[k] for k in ("finetune_dir", "study_dir", "lab_dir", "evolution_dir") if cfg.get(k)]
        self.extra_bytes_fn = extra_bytes_fn or (lambda: 0)

    @staticmethod
    def dir_bytes(path):
        total = 0
        for dirpath, _, files in os.walk(path):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
        return total

    def used(self):
        return sum(self.dir_bytes(r) for r in self.roots if os.path.isdir(r)) + self.extra_bytes_fn()

    def free(self):
        return self.limit - self.used()

    def can_allocate(self, nbytes):
        disk_free = shutil.disk_usage(os.path.abspath(".")).free
        return self.free() >= nbytes and disk_free >= nbytes + 2 * 2**30
