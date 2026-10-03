"""PyInstaller の入口 (LocalCoderCLI.exe・コマンドプロンプト)。--task での 1 回きりの頼みもこちら。"""
import sys

from localcoder.cli import main

if __name__ == "__main__":
    sys.exit(main())
