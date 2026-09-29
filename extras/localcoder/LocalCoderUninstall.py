"""PyInstaller の入口 (LocalCoderUninstall.exe)。LocalCoder を取り除き、入れる前の姿に戻す。"""
import sys

from localcoder.uninstall import main

if __name__ == "__main__":
    sys.exit(main())
