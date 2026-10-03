"""配布用フォルダを作る(Windows では AI-Buddy.exe)。

使い方(プロジェクトの companion フォルダで):
    python -m pip install -r requirements.txt pyinstaller
    python packaging/build_exe.py
出力: dist/AI-Buddy/(AI-Buddy.exe + _internal)と dist/AI-Buddy-<OS>.zip
PyInstaller はクロスビルドできないため、Windows 用は Windows 上で実行する(CI: windows-latest)。
"""
from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
NAME = "AI-Buddy"
README = """AI相棒(AI-Buddy)

■ 起動
  AI-Buddy.exe をダブルクリック。ブラウザで画面が開きます(開かなければ http://127.0.0.1:8765/ )。
  終了は黒いウィンドウで Ctrl+C、またはウィンドウを閉じる。

■ 初回の準備
  1. 初回起動で、このフォルダに .env(設定ファイル)が作られます。メモ帳で開いて次を設定し、再起動してください。
     - GEMINI_API_KEY      : Google AI Studio で発行した Gemini の APIキー
     - LLM_MODEL / GEMINI_TEXT_MODEL / GEMINI_IMAGE_MODEL : 使う Gemini のモデル名
  2. 司令塔(Claude Code)を使う場合: Claude Code をインストールし、一度 claude を起動して
     Claude のサブスク(Pro/Max)でログインしておく。見つかれば自動で有効になります。

■ フォルダの中身(このフォルダごと移動・バックアップできます)
  .env        設定(APIキーを含む。他人に渡さないこと)
  data/       会話・記憶・作業履歴
  workspace/  AI が読める作業フォルダ。成果物は workspace/outputs/ に保存
  prompts/    人格・行動規則(system.md を編集すると反映)

■ 注意
  - 署名なしの exe のため、初回に Windows の警告(SmartScreen)が出ることがあります。
    「詳細情報」→「実行」で起動できます。
  - Gemini の無料枠では、送信内容が Google の製品改善に使われる場合があります。
"""


def main() -> int:
    sep = os.pathsep  # Windows は ';'、それ以外は ':'
    dist, work = ROOT / "dist", ROOT / "build"
    args = [
        str(ROOT / "packaging" / "launcher.py"),
        "--name", NAME, "--onedir", "--console", "--noconfirm", "--clean",
        "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'buddy' / 'api' / 'static'}{sep}buddy/api/static",
        "--add-data", f"{ROOT / 'prompts'}{sep}prompts",
        "--add-data", f"{ROOT / '.env.example'}{sep}.",
        # uvicorn は設定に応じてモジュールを動的に読み込むため、すべて同梱する
        "--collect-submodules", "uvicorn",
    ]
    if importlib.util.find_spec("pyvcroid2"):  # VOICEROID2 連携(任意。入っていれば同梱)
        args += ["--hidden-import", "pyvcroid2"]
    PyInstaller.__main__.run(args)
    out = dist / NAME
    (out / "はじめにお読みください.txt").write_text(README, encoding="utf-8-sig")
    suffix = "windows" if sys.platform == "win32" else platform.system().lower()
    archive = shutil.make_archive(str(dist / f"{NAME}-{suffix}"), "zip", root_dir=dist, base_dir=NAME)
    print(f"作成しました: {out}\n配布用 zip: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
