"""エントリーポイント。ログ設定・Windows 固有の初期化を行い UI を起動する。"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import config


def _setup_logging():
    log_file = config.logs_dir() / "app.log"
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    ]
    # --windowed でビルドした exe では sys.stdout / sys.stderr が None になり、
    # tqdm や llama.cpp が書き込もうとして落ちるため、ファイルに差し替える。
    if sys.stdout is None or sys.stderr is None:
        sink = open(config.logs_dir() / "console.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = sink
        if sys.stderr is None:
            sys.stderr = sink
    else:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _windows_init():
    if sys.platform != "win32":
        return
    try:  # 高 DPI 環境で文字がぼやけないようにする
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        from .selftest import run

        sys.exit(run(sys.argv[2] if len(sys.argv) >= 3 else "selftest.txt"))
    _setup_logging()
    # huggingface_hub を import する前に設定する（共有キャッシュ ~/.cache/huggingface を汚さない）
    os.environ["HF_HOME"] = str(config.hf_home())
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    _windows_init()
    log = logging.getLogger("main")
    log.info("start: python=%s frozen=%s", sys.version.split()[0], getattr(sys, "frozen", False))
    config.record_exe_location()
    from .ui import App

    App().run()
