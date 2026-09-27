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
