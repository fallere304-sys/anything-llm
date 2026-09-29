"""作ったプログラムを動かし、.exe に固めるための Python 一式。

python.org の正式な Python (画面を作る tkinter も入っている) を、選んだフォルダ (home/python) に静かに入れ、
PyInstaller を足す。PC にもともと入っている Python には触れない (入れられなければ、それを借りる)。
"""

import os
import shutil
import subprocess

from .server import download

PY_VERSION = "3.12.10"
INSTALLER = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-amd64.exe"


def python_dir(home):
    return os.path.join(home, "python")


def python_exe(home):
    exe = os.path.join(python_dir(home), "python.exe")
    if os.path.exists(exe):
        return exe
    found = shutil.which("python")
    if found and "WindowsApps" not in found:      # Microsoft Store の入口 (本物ではない) は使わない
        return found
    return None


def ensure(home, say=print, run=subprocess.run):
    """Python と PyInstaller を用意する。使える python.exe のパスを返す (用意できなければ None)。"""
    exe = os.path.join(python_dir(home), "python.exe")
    if not os.path.exists(exe) and os.name == "nt":
        dl = os.path.join(home, "downloads")
        os.makedirs(dl, exist_ok=True)
        inst = os.path.join(dl, os.path.basename(INSTALLER))
        if not os.path.exists(inst):
            say(f"プログラムを .exe にする道具 (Python {PY_VERSION}) を用意します")
            download(INSTALLER, inst, say)
        run([inst, "/quiet", "InstallAllUsers=0", f"TargetDir={python_dir(home)}", "Include_launcher=0",
             "InstallLauncherAllUsers=0", "PrependPath=0", "Include_test=0", "Include_doc=0", "Shortcuts=0",
             "AssociateFiles=0", "Include_tcltk=1", "Include_pip=1"], capture_output=True)
    exe = python_exe(home)
    if exe is None:
        say("Python を用意できませんでした。.exe にする機能は使えません (ほかの作業はできます)")
        return None
    r = run([exe, "-m", "PyInstaller", "--version"], capture_output=True)
    if r.returncode != 0:
        say("PyInstaller を入れます")
        run([exe, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
             "pyinstaller"], capture_output=True)
    return exe


def env(home, base=None):
    """道具 (run) に渡す環境: 用意した Python を先に探す。"""
    e = dict(os.environ if base is None else base)
    exe = python_exe(home)
    if exe:
        d = os.path.dirname(exe)
        e["PATH"] = os.pathsep.join([d, os.path.join(d, "Scripts"), e.get("PATH", "")])
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    return e
