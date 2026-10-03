"""PyInstaller の入口 (LocalCoder.exe・画面)。コマンドプロンプトで使うときは LocalCoderCLI.exe。"""
import sys

from localcoder.gui import main

if __name__ == "__main__":
    sys.exit(main())
