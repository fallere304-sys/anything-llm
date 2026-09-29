"""LocalCoderUninstall.exe — LocalCoder を取り除き、PC を入れる前の姿に戻す。

    LocalCoderUninstall.exe               何を消すか見せて、確かめてから消す。最後に「残っていないか」を確かめる
    LocalCoderUninstall.exe --dry-run     見るだけ (何も消さない)
    LocalCoderUninstall.exe --yes         確かめずに消す (LocalCoder が持ち込んだと確かなものだけ)
    LocalCoderUninstall.exe --yes --all   作業フォルダ (作ったプログラム)・LocalCoder.exe・持ち込んだか分からないものも消す
    --home フォルダ    設定が消えていても、この置き場所を片付ける (何度でも指定できる)

LocalCoder.exe --uninstall でも同じものが動く。

消すもの: モデル・llama.cpp・記録・キャッシュ・置き場所に入れた Python (Windows の「アプリ」一覧の登録ごと、
Python 自身の取り除き方で)・設定・Windows に残る使用の記録。
作業フォルダ (作ったプログラム) は尋ねてから (既定は残す)。置き場所のフォルダは、空になったときだけ消す
(LocalCoder が作った中身だけを消し、もともとあったものには触れない)。
入れる前からあったキャッシュ (pip など) は、初回に取った記録 (config.json の before) を見て残す。
"""

import argparse
import glob
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from .server import download
from .toolchain import INSTALLER, PY_VERSION, registered_pythons

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OWNED = ("llama", "models", "downloads", "logs", "python", "cache")    # 置き場所の中で LocalCoder が作るもの
PY_SHORT = ".".join(PY_VERSION.split(".")[:2])
PY_CORE = rf"Software\Python\PythonCore\{PY_SHORT}"
UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
# Windows が「使ったアプリ」を覚えている場所 (マイク・カメラの許可、アプリ名、互換性の記録)
CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
MUICACHE = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache"
COMPAT = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store"


def say(msg=""):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def local_appdata(env):
    return env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")


def default_config(env=None):
    return os.path.join(local_appdata(os.environ if env is None else env), "LocalCoder", "config.json")


def global_caches(env):
    """{名前: (場所, 説明)}。LocalCoder の古い版や、作ったプログラムの .exe 化が置いたかもしれないもの。"""
    la = local_appdata(env)
    appdata = env.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return {"pip_cache": (os.path.join(la, "pip", "Cache"), "pip のキャッシュ"),
            "pyinstaller": (os.path.join(la, "pyinstaller"), "PyInstaller のキャッシュ"),
            "user_site": (os.path.join(appdata, "Python", "Python" + PY_SHORT.replace(".", "")),
                          f"Python {PY_SHORT} の利用者ごとの追加部品")}


def snapshot(env=None, clock=time.time):
    """初回の起動で、入れる前の姿を記録する (config.json の before)。"""
    env = os.environ if env is None else env
    out = {k: os.path.exists(p) for k, (p, _) in global_caches(env).items()}
    out["ts"] = int(clock())
    return out


def _inside(path, root):
    p, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def rmtree(path):
    """読み取り専用のファイルも消す。"""
    def onerror(func, p, _):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=onerror)
    elif os.path.exists(path):
        try:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
        except OSError:
            pass


def rmdir_if_empty(path):
    try:
        os.rmdir(path)
    except OSError:
        pass


# ---------------------------------------------------------------- PC への操作 (テストでは差し替える)
class WinRegistry:
    """今の利用者の範囲 (HKCU) だけを読む・消す。"""

    def __init__(self):
        import winreg
        self.w = winreg
        self.root = winreg.HKEY_CURRENT_USER

    def _enum(self, path, fn):
        out = []
        try:
            with self.w.OpenKey(self.root, path) as k:
                i = 0
                while True:
                    try:
                        out.append(fn(k, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            pass
        return out

    def subkeys(self, path):
        return self._enum(path, self.w.EnumKey)

    def values(self, path):
        return self._enum(path, lambda k, i: self.w.EnumValue(k, i)[0])

    def get(self, path, name=""):
        try:
            with self.w.OpenKey(self.root, path) as k:
                return self.w.QueryValueEx(k, name)[0]
        except OSError:
            return None

    def delete_value(self, path, name):
        with self.w.OpenKey(self.root, path, 0, self.w.KEY_SET_VALUE) as k:
            self.w.DeleteValue(k, name)

    def delete_tree(self, path):
        for sub in self.subkeys(path):
            self.delete_tree(path + "\\" + sub)
        self.w.DeleteKey(self.root, path)


class System:
    def __init__(self):
        self.windows = os.name == "nt"
        self.registry = WinRegistry() if self.windows else None

    def run(self, cmd, timeout=300):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, creationflags=CREATE_NO_WINDOW)
            return r.returncode, r.stdout
        except (OSError, subprocess.SubprocessError):
            return 127, ""

    def powershell(self, script, timeout=120):
        return self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)

    def processes(self):
        """[(pid, 実行ファイルのパス)]。"""
        if not self.windows:
            return []
        _, out = self.powershell("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath } | "
                                 "ForEach-Object { \"$($_.ProcessId)`t$($_.ExecutablePath)\" }")
        procs = []
        for line in out.splitlines():
            pid, _, path = line.partition("\t")
            # 自分と、自分を起こした親 (PyInstaller の 1 ファイル版は親子 2 つで動く) は止めない
            if pid.strip().isdigit() and int(pid) not in (os.getpid(), os.getppid()):
                procs.append((int(pid), path.strip()))
        return procs

    def kill(self, pid):
        if self.windows:
            self.run(["taskkill", "/F", "/T", "/PID", str(pid)])

    def firewall_rules(self, root):
        if not self.windows:
            return []
        r = root.rstrip("\\").replace("'", "''")
        _, out = self.powershell(f"Get-NetFirewallApplicationFilter | Where-Object {{ $_.Program -like '{r}\\*' }} | "
                                 "Get-NetFirewallRule | ForEach-Object { $_.Name }")
        return [x.strip() for x in out.splitlines() if x.strip()]

    def remove_firewall_rule(self, name):
        self.powershell(f"Remove-NetFirewallRule -Name '{name}'")

    def drives(self):
        if not self.windows:
            return []
        return [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]


# ---------------------------------------------------------------- 消すもの
class Item:
    """消す候補。sure=False は「消してよいか分からない」(尋ねる / --all のときだけ消す)。"""

    def __init__(self, label, present, remove, sure=True, default=True, why=""):
        self.label, self.present, self.remove = label, present, remove
        self.sure, self.default, self.why = sure, default, why


class Uninstaller:
    def __init__(self, config=None, homes=(), env=None, system=None, out=say, self_exe=None, sleep=time.sleep):
        self.env = dict(os.environ if env is None else env)
        self.config = os.path.abspath(config or default_config(self.env))
        self.sys = system or System()
        self.reg = self.sys.registry
        self.out, self.sleep = out, sleep
        self.self_exe = os.path.abspath(self_exe) if self_exe else None
        try:
            with open(self.config, encoding="utf-8") as f:
                self.cfg = json.load(f)
        except (OSError, ValueError):
            self.cfg = {}
        found = list(homes) + [self.cfg.get("home")] + list(self.cfg.get("homes", [])) + self._scan()
        self.homes = list(dict.fromkeys(os.path.abspath(h) for h in found if h))
        self.launched = [os.path.abspath(p) for p in self.cfg.get("launched_from", [])]
        self.kept = []

    def _scan(self):
        """設定が無くても見つかるように、各ドライブの \\LocalCoder (中に llama か models があるもの) を探す。"""
        out = []
        for d in self.sys.drives():
            h = os.path.join(d, "LocalCoder")
            if any(os.path.isdir(os.path.join(h, s)) for s in ("llama", "models")):
                out.append(h)
        return out

    # ------------------------------------------------------------ 候補を集める
    def plan(self):
        self.kept = []
        items = [Item("動いている LocalCoder・llama.cpp・作ったプログラム (止める)", lambda: bool(self._procs()),
                      self._stop)]
        for home in self.homes:
            items += self._home_items(home)
        items.append(Item(f"設定 {self.config}", lambda: os.path.exists(self.config), self._remove_config))
        items += self._cache_items()
        if self.reg is not None:
            items.append(Item("Windows に残った使用の記録 (アプリ名・互換性の記録・マイクやカメラの許可)",
                              lambda: bool(self._traces()), self._clear_traces))
        if self.sys.windows:
            items.append(Item("Windows のファイアウォールの規則 (置き場所の中のプログラムに許可したもの)",
                              lambda: any(self.sys.firewall_rules(h) for h in self.homes),
                              lambda: [self.sys.remove_firewall_rule(n) for h in self.homes
                                       for n in self.sys.firewall_rules(h)]))
        for p in self.launched:
            if self.self_exe and os.path.normcase(p) == os.path.normcase(self.self_exe):
                continue
            items.append(Item(f"ダウンロードした {p}", lambda p=p: os.path.exists(p), lambda p=p: rmtree(p),
                              False, True, "入れ直すときにまた使うなら残す"))
        return items

    def _procs(self):
        roots = self.homes
        exes = {os.path.normcase(p) for p in self.launched}
        return [(pid, p) for pid, p in self.sys.processes()
                if any(_inside(p, r) for r in roots) or os.path.normcase(p) in exes]

    def _stop(self):
        for pid, _ in self._procs():
            self.sys.kill(pid)
        if self.sys.windows:
            self.sleep(2)

    def _home_items(self, home):
        items = []
        py = self._our_python(home)
        if py:
            items.append(Item(f"置き場所に入れた Python {PY_VERSION} (「設定 → アプリ」の登録ごと)",
                              lambda: bool(self._our_python(home)), lambda: self._remove_python(home)))
            items.append(Item(f"Python {PY_VERSION} を入れた・取り除いたときの記録 (一時フォルダ)",
                              lambda: bool(self._py_logs()), lambda: [rmtree(p) for p in self._py_logs()]))
        parts = lambda: [os.path.join(home, s) for s in OWNED if os.path.exists(os.path.join(home, s))]  # noqa: E731
        items.append(Item(f"{home} の中の モデル・llama.cpp・Python・記録・キャッシュ",
                          lambda: bool(parts()), lambda: [rmtree(p) for p in parts()]))
        ws = os.path.join(home, "workspace")
        items.append(Item(f"作業フォルダ {ws} (作ったプログラム)", lambda: os.path.isdir(ws),
                          lambda: rmtree(ws), False, False, "あなたが作ったものなので、既定は残す"))
        return items

    def _our_python(self, home):
        """PC に登録された Python がこの置き場所の中のものなら、そのパス (LocalCoder が入れたもの)。"""
        if self.reg is None:
            return None
        path = self.reg.get(PY_CORE + r"\InstallPath", "")
        return path if path and _inside(path, os.path.join(home, "python")) else None

    def _python_entry(self):
        """「アプリ」一覧の Python の登録 (取り除き方を持つ本体の登録) → (キー, 取り除きに使う exe)。"""
        if self.reg is None:
            return None
        for sub in self.reg.subkeys(UNINSTALL):
            key = f"{UNINSTALL}\\{sub}"
            name = self.reg.get(key, "DisplayName") or ""
            cache = self.reg.get(key, "BundleCachePath")
            if name.startswith(f"Python {PY_VERSION} ") and cache:
                return key, cache
        return None

    def _other_pythons(self, home):
        """置き場所の外にある、同じ系の Python の登録 (あれば、取り除きがそれを巻き込むおそれがある)。"""
        return [p for v, p in registered_pythons(PY_SHORT) if not (p and _inside(p, os.path.join(home, "python")))]

    def _remove_python(self, home):
        """Python 自身の取り除き方 (/uninstall) で取り除く。登録・部品の記録も一緒に消える。"""
        others = [p for p in self._other_pythons(home) if p]
        if others:
            raise RuntimeError(f"ほかにも Python {PY_SHORT} ({', '.join(others)}) があるので、巻き込まないよう "
                               f"Python の取り除きはしませんでした。「設定 → アプリ」で場所を確かめて取り除いてください")
        for pid, p in self.sys.processes():
            if _inside(p, os.path.join(home, "python")):
                self.sys.kill(pid)
        entry = self._python_entry()
        exe = entry[1] if entry and os.path.exists(entry[1]) else None
        if exe is None:
            local = sorted(glob.glob(os.path.join(home, "downloads", f"python-{PY_VERSION}-*.exe")))
            exe = local[0] if local else None
        if exe is None:          # 取り除き方の exe が残っていない: 同じ版の入れる道具を取ってきて、それで取り除く
            exe = os.path.join(tempfile.gettempdir(), os.path.basename(INSTALLER))
            self.out(f"  Python {PY_VERSION} の取り除き方を取ってきます (python.org)")
            download(INSTALLER, exe, self.out)
        code, _ = self.sys.run([exe, "/uninstall", "/quiet"], timeout=900)
        if code != 0:
            raise RuntimeError(f"Python の取り除きが止まりました (終了コード {code})")

    def _py_logs(self):
        tmp = self.env.get("TEMP") or self.env.get("TMP") or tempfile.gettempdir()
        return glob.glob(os.path.join(tmp, f"Python {PY_VERSION} (64-bit)_*.log"))

    def _remove_config(self):
        rmtree(self.config)
        d = os.path.dirname(self.config)
        if os.path.basename(d) == "LocalCoder":
            rmdir_if_empty(d)

    def _cache_items(self):
        if not self.cfg:          # 設定が無い (もう取り除いた後など): 共用のキャッシュが LocalCoder のものか分からない
            return []
        before = self.cfg.get("before")
        items = []
        for key, (path, label) in global_caches(self.env).items():
            if not os.path.exists(path):
                continue
            was = before.get(key) if isinstance(before, dict) else None
            if was:
                self.kept.append(f"{label} {path}")
                continue
            sure = was is False
            items.append(Item(f"{label} {path}", lambda p=path: os.path.exists(p), lambda p=path: rmtree(p),
                              sure, False, "入れる前からあったかもしれない (ほかの Python と共用)"))
        return items

    def _paths(self):
        return self.homes + self.launched

    def _matches(self, text):
        return any(_inside(text, p) or os.path.normcase(text).startswith(os.path.normcase(p) + ".")
                   for p in self._paths() if text)

    def _traces(self):
        reg, found = self.reg, []
        for cap in reg.subkeys(CONSENT):
            base = f"{CONSENT}\\{cap}\\NonPackaged"
            for sub in reg.subkeys(base):
                if self._matches(sub.replace("#", os.sep)):
                    found.append(("key", f"{base}\\{sub}", None))
        for path in (MUICACHE, COMPAT):
            for name in reg.values(path):
                if self._matches(name):
                    found.append(("value", path, name))
        return found

    def _clear_traces(self):
        for kind, path, name in self._traces():
            try:
                self.reg.delete_tree(path) if kind == "key" else self.reg.delete_value(path, name)
            except OSError:
                pass

    # ------------------------------------------------------------ 実行
    def run(self, yes=False, include_unsure=False, dry=False, ask=None):
        ask = ask or (lambda q, d: d)
        if not self.homes and not os.path.exists(self.config):
            self.out("LocalCoder の置き場所が見つかりません (設定も無い)。置き場所が分かるなら --home で指定してください。")
        items = [i for i in self.plan() if i.present()]
        before = self.cfg.get("before")
        if isinstance(before, dict) and "ts" in before:
            self.out("台帳: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(before["ts"])) + " の記録 (入れる前の姿)")
        elif self.cfg:
            self.out("台帳: なし (古い版で始めたので、入れる前からあったか分からないものは尋ねます)")
        for k in dict.fromkeys(self.kept):
            self.out(f"  残す: {k} (入れる前からあった)")
        if not items:
            self.out("LocalCoder が残したものは、もう何もありません。")
            return 0
        chosen = [it for it in items if it.sure]
        self.out("\n取り除くもの:")
        for it in chosen:
            self.out(f"  - {it.label}")
        unsure = [it for it in items if not it.sure]
        if unsure:
            self.out("\n尋ねるもの:")
            for it in unsure:
                self.out(f"  ? {it.label} ({it.why})")
        if dry:
            self.out("\n(--dry-run なので、何も消していません)")
            return 0
        for it in unsure:
            if include_unsure if yes else ask(f"{it.label} も取り除きますか？", it.default):
                chosen.append(it)
        if not yes and not ask("\nこれらを取り除きます。よいですか？", False):
            self.out("やめました。何も消していません。")
            return 1
        chosen.sort(key=items.index)
        errors = {}
        for it in chosen:
            self.out(f"取り除いています: {it.label}")
            try:
                it.remove()
            except Exception as e:  # noqa: BLE001
                errors[it.label] = str(e)
        for home in self.homes:
            rmdir_if_empty(home)          # 空になったときだけ (もともとあったフォルダ・残した作業フォルダには触れない)

        self.out("\n確かめています…")
        left = []
        for it in chosen:
            try:
                still = it.present()
            except Exception:  # noqa: BLE001
                still = False
            self.out(f"  {'残っている' if still else '済み      '}  {it.label}"
                     + (f"  ({errors[it.label]})" if still and it.label in errors else ""))
            if still:
                left.append(it)
        for it in items:
            if it not in chosen:
                self.out(f"  残した      {it.label}")
        if left:
            self.out("\n取り除けなかったものがあります。PC を再起動してから、もう一度実行してください。")
            return 4
        self.out("\nLocalCoder が持ち込んだものは、すべて取り除きました。")
        if self.self_exe:
            self.out(f"この消去ツール ({os.path.basename(self.self_exe)}) は、最後にごみ箱へ入れてください。")
        return 0


def ask_console(question, default):
    hint = "[Y/n]" if default else "[y/N]"
    try:
        a = input(f"{question} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not a else a in ("y", "yes", "はい")


def main(argv=None, system=None, env=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(prog="LocalCoderUninstall", description="LocalCoder を取り除き、入れる前の姿に戻す")
    ap.add_argument("--config", default=default_config(env))
    ap.add_argument("--home", action="append", default=[], help="片付ける置き場所 (何度でも)")
    ap.add_argument("--yes", action="store_true", help="確かめずに消す (持ち込んだと確かなものだけ)")
    ap.add_argument("--all", action="store_true", help="作業フォルダ・LocalCoder.exe・分からないものも消す")
    ap.add_argument("--dry-run", action="store_true", help="見るだけ")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding=None if sys.stdout.isatty() else "utf-8", errors="backslashreplace")
    interactive = sys.stdin is not None and sys.stdin.isatty() and not args.yes
    frozen = getattr(sys, "frozen", False)
    say("LocalCoder を取り除きます\n")
    un = Uninstaller(args.config, args.home, env=env, system=system, self_exe=sys.executable if frozen else None)
    code = un.run(yes=args.yes, include_unsure=args.all, dry=args.dry_run, ask=ask_console if interactive else None)
    if interactive:
        try:
            input("\nEnter で閉じます… ")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
