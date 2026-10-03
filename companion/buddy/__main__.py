"""起動: python -m buddy(配布版は AI-Buddy.exe)"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from .api.app import create_app
from .bootstrap import bundle_dir, enter_home, is_frozen, prepare_home, use_utf8_console
from .config import ConfigError, load_settings
from .llm.base import LLMError
from .llm.registry import build_providers
from .logging_setup import setup_logging
from .tts.registry import build_tts


def _pause() -> None:
    """配布版でエラー時にウィンドウがすぐ閉じないようにする。"""
    if is_frozen():
        try:
            input("\nEnter キーで終了します...")
        except (EOFError, KeyboardInterrupt):
            pass


def _open_in_notepad(path) -> None:
    """配布版の初回のみ、作成した設定ファイルをメモ帳で開く(失敗しても起動は続ける)。"""
    try:
        subprocess.Popen(["notepad.exe", str(path)])
    except OSError:
        pass


def _open_browser_when_ready(host: str, port: int) -> None:
    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host

    def wait_and_open() -> None:
        for _ in range(100):  # 最大 10 秒待つ
            try:
                with socket.create_connection((url_host, port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        webbrowser.open(f"http://{url_host}:{port}/")

    threading.Thread(target=wait_and_open, daemon=True).start()


def main() -> int:
    use_utf8_console()
    home = enter_home()
    created = prepare_home(home, bundle_dir())
    if created:
        print(f"[初回準備] 次のファイルを作成しました: {', '.join(created)}")
        print(f"           {home / '.env'} を開き、GEMINI_API_KEY などを設定してから再起動してください。", flush=True)
        if ".env" in created and is_frozen() and sys.platform == "win32":
            _open_in_notepad(home / ".env")
    try:
        settings = load_settings()
        setup_logging(secrets=[settings.access_token, settings.llm_api_key,
                                settings.anthropic_api_key, settings.perplexity_api_key,
                                settings.gemini_api_key])
        app = create_app(settings, build_providers(settings), tts=build_tts(settings))
    except (ConfigError, LLMError) as exc:
        print(f"[設定エラー] {exc}", file=sys.stderr)
        print(f"            設定ファイル: {home / '.env'}", file=sys.stderr)
        _pause()
        return 2
    if os.environ.get("BUDDY_OPEN_BROWSER", "1") != "0":
        _open_browser_when_ready(settings.host, settings.port)
    print(f"AI相棒を起動します: http://127.0.0.1:{settings.port}/  (終了は Ctrl+C)")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
