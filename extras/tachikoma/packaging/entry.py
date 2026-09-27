"""tachikoma.exe の入口 (PyInstaller 用)。exe のある場所を作業フォルダにして起動する。"""

import os
import sys

if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))
    from tachikoma.__main__ import main
    sys.exit(main())
