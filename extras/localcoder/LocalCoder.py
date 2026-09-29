"""PyInstaller の入口 (LocalCoder.exe)。"""
import sys

from localcoder.cli import main

if __name__ == "__main__":
    sys.exit(main())
