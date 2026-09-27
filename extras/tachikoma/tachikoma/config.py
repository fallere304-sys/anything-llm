"""設定の読み込み。config.json の値で DEFAULTS を上書きする。"""

import json
import os

DEFAULTS = {
    # --- 推論バックエンド (Ollama) ---
    "ollama_url": "http://127.0.0.1:11434",
    "model": "gemma4:e2b",
    # 128K まで扱えるが、GTX1060(6GB) では KV キャッシュが VRAM を圧迫するので絞る
    "num_ctx": 8192,
    "request_timeout": 120,
    # 背景思考が GPU を占有してよい時間割合 (0-1)。残りはユーザー対話と他アプリ用
    "gpu_duty_cycle": 0.5,
    "think_for_hypotheses": False,

    # --- 記憶 ---
    "db_path": "tachikoma.db",

    # --- 感覚器 (すべて opt-in) ---
    "watch_dirs": [],
    "watch_exts": [".py", ".js", ".ts", ".md", ".txt", ".json", ".log", ".yaml", ".yml", ".toml"],
    "max_file_bytes": 200000,
    "terminal_logs": [],
    "clipboard": False,
    "active_window": True,

    # --- 思考ループ ---
    "tick_seconds": 2.0,
    "novelty_threshold": 0.35,
    "curiosity_threshold": 0.35,
    "max_probe_attempts": 3,
    "relevance_half_life_s": 1800,

    # --- 発話ポリシー ---
    "speak_threshold": 0.6,
    "min_speak_interval_s": 90,
    "busy_idle_s": 5,
    "allow_ask_user": True,

    # --- 学習 (ファインチューン) ---
    # 無操作が続いたら、蓄積した「裏付けのある」標本で LoRA を学習し、
    # 検証で劣化していなければ Ollama のモデルを差し替える
    "finetune_enabled": True,
    "finetune_python": None,          # torch 等を入れた venv の python。None なら今の python
    "hf_base_model": "google/gemma-4-E2B-it",
    "finetune_dir": "finetune_runs",   # 学習の成果物 (版ごとの JSONL・アダプタ・Modelfile)
    "finetune_min_new_samples": 20,
    "finetune_after_idle_s": 7200,
    "finetune_timeout_s": 3 * 3600,
    "finetune_backoff_s": 6 * 3600,
    "finetune_args": ["--rank", "8", "--epochs", "2", "--lr", "1e-4", "--max-len", "768",
                      "--load-4bit", "--compute-dtype", "float32", "--gpu-mem-gib", "5"],
    "finetune_eval_max": 20,
    "finetune_min_holdout": 5,
    "finetune_tolerance": 0.02,
    "llama_cpp_dir": "",              # 指定すると LoRA を GGUF に変換してから Ollama に渡す
    "ollama_bin": "ollama",
    "model_prefix": "tachikoma",

    # --- 睡眠(記憶の整理) ---
    "sleep_after_idle_s": 1800,
    "event_retention_days": 7,
}


def load(path=None):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["watch_dirs"] = [os.path.abspath(os.path.expanduser(d)) for d in cfg["watch_dirs"]]
    cfg["terminal_logs"] = [os.path.abspath(os.path.expanduser(p)) for p in cfg["terminal_logs"]]
    return cfg
