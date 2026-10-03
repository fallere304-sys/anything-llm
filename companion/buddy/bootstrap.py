"""起動前の準備(.exe 配布向け)。

- 配布版(PyInstaller で固めた .exe)では、.exe のあるフォルダを「ホーム」とし、そこに
  .env / data / workspace / prompts を置く(ダブルクリックやショートカットでも場所がぶれない)
- 初回起動時、同梱の .env.example と prompts/system.md をホームへコピーする(既存ファイルは上書きしない)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """同梱ファイルの場所(配布版は展開先、開発時はプロジェクトルート)。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def app_home() -> Path:
    return Path(sys.executable).resolve().parent if is_frozen() else Path.cwd()


def prepare_home(home: Path, bundle: Path) -> list[str]:
    """初回用ファイルを用意し、作成したもの(相対パス)を返す。"""
    created = []
    pairs = [
        (bundle / ".env.example", home / ".env.example"),
        (bundle / ".env.example", home / ".env"),
        (bundle / "prompts" / "system.md", home / "prompts" / "system.md"),
    ]
    for src, dst in pairs:
        if dst.exists() or not src.is_file():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        created.append(dst.relative_to(home).as_posix())
    return created


def use_utf8_console() -> None:
    """日本語のログで落ちないようにする(コンソールでもリダイレクト先でも)。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def enter_home() -> Path:
    home = app_home()
    os.chdir(home)
    return home
