"""作ったプログラムを動かし、.exe に固めるための Python 一式。

python.org の正式な Python (画面を作る tkinter も入っている) を、選んだフォルダ (home/python) に静かに入れ、
PyInstaller を足す。PC にもともと入っている Python には触れない:
同じ 3.12 系がもう入っている PC では、正式な入れる道具はそれを「更新・修復」してしまうので使わず、
もとからある Python から置き場所の中に仮想環境 (venv) を作り、そこに PyInstaller を入れる。
"""

import os
import re
import shutil
import subprocess

from .server import download

PY_VERSION = "3.12.10"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
INSTALLER = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-amd64.exe"


def python_dir(home):
    return os.path.join(home, "python")


def registered_pythons(minor=None):
    """PC に登録された Python ([(版, 場所)]、今の利用者と PC 全体の両方)。minor="3.12" ならその系だけ。

    python.org の入れる道具は、同じ系 (3.12 なら 3.12.x) がもう入っていると、それを別の場所に入れ直して
    もとの場所から消してしまう (GitHub の Windows 機で、もとの Python が消えるのを確かめた)。だから入れる前に見る。
    """
    if os.name != "nt":
        return []
    import winreg
    out = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for base in (r"Software\Python\PythonCore", r"Software\WOW6432Node\Python\PythonCore"):
            try:
                with winreg.OpenKey(hive, base) as k:
                    i = 0
                    while True:
                        try:
                            ver = winreg.EnumKey(k, i)
                        except OSError:
                            break
                        i += 1
                        if minor and not (ver == minor or ver.startswith(minor + "-")):
                            continue
                        try:
                            with winreg.OpenKey(k, ver + r"\InstallPath") as ip:
                                out.append((ver, winreg.QueryValueEx(ip, "")[0]))
                        except OSError:
                            out.append((ver, ""))
            except OSError:
                pass
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):     # 「アプリ」一覧の登録も見る
        for base in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                with winreg.OpenKey(hive, base) as k:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(k, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(k, sub) as e:
                                name = str(winreg.QueryValueEx(e, "DisplayName")[0])
                        except OSError:
                            continue
                        m = re.match(r"Python (\d+\.\d+)\.", name)
                        if m and (not minor or m.group(1) == minor):
                            try:
                                with winreg.OpenKey(k, sub) as e:
                                    place = str(winreg.QueryValueEx(e, "InstallLocation")[0])
                            except OSError:
                                place = ""
                            out.append((m.group(1), place))
            except OSError:
                pass
    return out


def python_exe(home):
    """置き場所の中の Python (正式な Python か、仮想環境)。PC にもともとある Python は直接は使わない。"""
    for exe in (os.path.join(python_dir(home), "python.exe"), os.path.join(python_dir(home), "Scripts", "python.exe")):
        if os.path.exists(exe):
            return exe
    return None


def _base_python(found):
    """venv の元にする、もとからある Python。"""
    for _, place in found:
        exe = os.path.join(place, "python.exe") if place else ""
        if exe and os.path.exists(exe):
            return exe
    exe = shutil.which("python")
    return exe if exe and "WindowsApps" not in exe else None


def ensure(home, say=print, run=subprocess.run, found=None):
    """Python と PyInstaller を用意する。使える python.exe のパスを返す (用意できなければ None)。"""
    minor = ".".join(PY_VERSION.split(".")[:2])
    found = registered_pythons(minor) if found is None else found
    mine = [f for f in found if f[1] and os.path.normcase(os.path.abspath(f[1])).startswith(
        os.path.normcase(os.path.abspath(python_dir(home))))]
    others = [f for f in found if f not in mine]
    if python_exe(home) is None and not others and os.name == "nt":
        dl = os.path.join(home, "downloads")
        os.makedirs(dl, exist_ok=True)
        inst = os.path.join(dl, os.path.basename(INSTALLER))
        if not os.path.exists(inst):
            say(f"プログラムを .exe にする道具 (Python {PY_VERSION}) を用意します")
            download(INSTALLER, inst, say)
        run([inst, "/quiet", "InstallAllUsers=0", f"TargetDir={python_dir(home)}", "Include_launcher=0",
             "InstallLauncherAllUsers=0", "PrependPath=0", "Include_test=0", "Include_doc=0", "Shortcuts=0",
             "AssociateFiles=0", "Include_tcltk=1", "Include_pip=1"], capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if python_exe(home) is None:
        # 同じ 3.12 系がもうある (正式な入れる道具はそれを更新・修復してしまう) か、入れられなかった:
        # もとからある Python には触れず、置き場所の中に仮想環境を作る
        base = _base_python(others)
        if base:
            say(f"もとからある Python ({base}) には触れず、置き場所の中に仮想環境を作ります")
            run([base, "-m", "venv", python_dir(home)], capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    exe = python_exe(home)
    if exe is None:
        say("Python を用意できませんでした。.exe にする機能は使えません (ほかの作業はできます)")
        return None
    r = run([exe, "-m", "PyInstaller", "--version"], capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    if r.returncode != 0:
        say("PyInstaller を入れます")
        run([exe, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
             "pyinstaller"], capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, env=env(home))
    return exe


def env(home, base=None):
    """道具 (run) に渡す環境: 用意した Python を先に探す。"""
    e = dict(os.environ if base is None else base)
    exe = python_exe(home)
    if exe:
        d = os.path.dirname(exe)
        e["PATH"] = os.pathsep.join([d, os.path.join(d, "Scripts"), e.get("PATH", "")])
    e["PYTHONIOENCODING"] = "utf-8"
    # キャッシュは置き場所の中に (PC の共用の場所に残さない。取り除くとき一緒に消える)
    e["PIP_CACHE_DIR"] = os.path.join(home, "cache", "pip")
    e["PYINSTALLER_CONFIG_DIR"] = os.path.join(home, "cache", "pyinstaller")
    e["PYTHONUTF8"] = "1"
    return e
