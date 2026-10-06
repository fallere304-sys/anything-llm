"""設定値とアプリ用ディレクトリの管理。

設定は %LOCALAPPDATA%\\DualMicTranscriber\\config.json に保存され、
初回起動時に既定値で生成される。ユーザーはこのファイルを編集して
Whisper モデルや LLM モデルを変更できる。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

APP_NAME = "DualMicTranscriber"

DEFAULTS: dict = {
    # --- 文字起こし (faster-whisper) ---
    "language": "ja",                 # 空文字なら自動判定
    "whisper_model": "medium",        # tiny / base / small / medium / large-v3 など
    "whisper_compute_type": "int8",   # CPU 向け量子化
    "whisper_beam_size": 5,
    # --- 補正用ローカル LLM (llama.cpp / GGUF) ---
    "llm_repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
    "llm_file": "qwen2.5-3b-instruct-q4_k_m.gguf",
    "llm_n_ctx": 8192,
    "llm_chunk_seconds": 120,         # この秒数ごとに区切って LLM に渡す
    "llm_max_tokens": 2048,
    "llm_temperature": 0.2,
    # --- 音量比較による話者識別 ---
    "frame_ms": 50,                   # 音量を測る1フレームの長さ
    "vad_threshold_db": 8.0,          # 雑音下限からこれ以上大きければ「発話あり」
    "dominance_margin_db": 3.0,       # 2マイクの差がこれ未満なら「判別不能(?)」
    "smoothing_ms": 400,              # 音量差の移動平均窓
    "min_segment_ms": 300,            # これより短い話者区間は前後に吸収
    "utterance_gap_s": 1.0,           # 同一話者でもこれ以上空けば別発話
    # --- その他 ---
    "threads": 0,                     # 0 = 自動 (論理コア数 - 1)
}

log = logging.getLogger(__name__)


def app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def models_dir() -> Path:
    p = app_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = app_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sessions_dir() -> Path:
    p = Path.home() / "Documents" / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return app_dir() / "config.json"


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    path = config_path()
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        except Exception:  # 壊れた設定ファイルでも起動はさせる
            log.exception("config.json の読み込みに失敗。既定値を使用します")
    try:
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        log.exception("config.json の書き込みに失敗")
    return cfg


def thread_count(cfg: dict) -> int:
    n = int(cfg.get("threads") or 0)
    if n > 0:
        return n
    return max(1, (os.cpu_count() or 2) - 1)
