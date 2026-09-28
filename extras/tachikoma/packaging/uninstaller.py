"""TachikomaUninstall.exe — タチコマを取り除き、PC を入れる前の姿に戻す。

    TachikomaUninstall.exe                 何を消すか見せて、確かめてから消す。最後に「残っていないか」を確かめる
    TachikomaUninstall.exe --dry-run       見るだけ (何も消さない)
    TachikomaUninstall.exe --yes           確かめずに消す (タチコマが持ち込んだと確かなものだけ)
    TachikomaUninstall.exe --yes --all     持ち込んだか分からないもの (入れる前からあったかもしれないもの) も消す
    --backup フォルダ   消す前に、記憶・設定・自己進化の成果を zip にして残す (入れ直したあと app\\ に展開すれば続きから)
    --remove-ollama    Ollama 本体も取り除く (タチコマのために入れた場合)

Tachikoma.exe --uninstall と、Windows の「設定 → アプリ」の一覧からも同じものが動く。

消すかどうかは台帳 (footprint.json) で決める: 入れる前からあったもの (前から使っていた Ollama のモデル・
Hugging Face のキャッシュなど) は残し、タチコマが持ち込んだものだけを消す。台帳が無い・古い版から更新した場合は、
持ち込んだか分からないものを一つずつ尋ねる。
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
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import footprint  # noqa: E402

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)
# Windows が「使ったアプリ」を覚えている場所 (マイク・カメラの許可の記録、アプリ名の記録、互換性の記録)
CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
MUICACHE = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache"
COMPAT = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store"
BACKUP_SKIP = {"models", "study", "cache", "__pycache__"}


def say(msg=""):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def _inside(path, root):
    p, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def rmtree(path):
    """読み取り専用のファイルも消す。消せなかったものの数を返す。"""
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
    return 0 if not os.path.exists(path) else sum(len(f) for _, _, f in os.walk(path)) or 1


# ---------------------------------------------------------------- PC への操作 (テストでは差し替える)
class System:
    def __init__(self):
        self.windows = os.name == "nt"
        self.registry = footprint.registry()

    def run(self, cmd, timeout=300):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, creationflags=CREATE_NO_WINDOW)
            return r.returncode, r.stdout
        except (OSError, subprocess.SubprocessError):
            return 127, ""

    def which(self, name):
        return shutil.which(name)

    def powershell(self, script, timeout=120):
        return self.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)

    def processes_under(self, root):
        if not self.windows:
            return []
        r = root.rstrip("\\").lower().replace("'", "''") + "\\"
        code, out = self.powershell(
            "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
            f"$_.ExecutablePath.ToLower().StartsWith('{r}') }} | "
            "ForEach-Object { \"$($_.ProcessId)`t$($_.ExecutablePath)\" }")
        procs = []
        for line in out.splitlines():
            pid, _, path = line.partition("\t")
            if pid.strip().isdigit() and int(pid) != os.getpid():
                procs.append((int(pid), path.strip()))
        return procs

    def kill(self, pid):
        if self.windows:
            self.run(["taskkill", "/F", "/T", "/PID", str(pid)])
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    def firewall_rules(self, root):
        if not self.windows:
            return []
        r = root.rstrip("\\").replace("'", "''")
        _, out = self.powershell(f"Get-NetFirewallApplicationFilter | Where-Object {{ $_.Program -like '{r}\\*' }} | "
                                 "Get-NetFirewallRule | ForEach-Object { $_.Name }")
        return [x.strip() for x in out.splitlines() if x.strip()]

    def remove_firewall_rule(self, name):
        self.powershell(f"Remove-NetFirewallRule -Name '{name}'")

    def delete_later(self, path):
        """動いている自分自身など、いま消せないものを少し後で消す。"""
        if self.windows:
            verb = f'rmdir /s /q "{path}"' if os.path.isdir(path) else f'del /f /q "{path}"'
            # 放されるまで (PyInstaller の後片付けが終わるまで) 1 秒おきに最大 60 回試す
            subprocess.Popen(f'cmd /c for /L %i in (1,1,60) do @(ping -n 2 127.0.0.1 >nul & {verb} >nul 2>&1 & '
                             f'if not exist "{path}" exit /b 0)', creationflags=DETACHED | CREATE_NO_WINDOW,
                             close_fds=True)


class Ollama:
    """Ollama のモデルを消す窓口。Ollama が止まっていれば一時的に起動し、終わったら止める。"""

    def __init__(self, env, popen=subprocess.Popen, opener=urllib.request.urlopen, sleep=time.sleep,
                 url=footprint.OLLAMA_URL):
        self.env, self.popen, self.opener, self.sleep, self.url = env, popen, opener, sleep, url
        self.proc = None

    def _tags(self):
        try:
            with self.opener(self.url + "/api/tags", timeout=3) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError):
            return None

    def models(self):
        tags = self._tags()
        if tags is not None:
            return {footprint.normalize_model(m.get("name", "")) for m in tags.get("models", [])}
        return footprint.ollama_models(self.env)

    def ensure_up(self):
        if self._tags() is not None:
            return True
        exe = footprint.ollama_exe(self.env)
        if not exe:
            return False
        if self.proc is None:
            self.proc = self.popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=CREATE_NO_WINDOW)
        for _ in range(30):
            self.sleep(1)
            if self._tags() is not None:
                return True
        return False

    def delete(self, name):
        if not self.ensure_up():
            raise RuntimeError("Ollama を起動できないので、モデルを消せません")
        body = json.dumps({"model": name, "name": name}).encode()
        req = urllib.request.Request(self.url + "/api/delete", data=body, method="DELETE",
                                     headers={"Content-Type": "application/json"})
        self.opener(req, timeout=120).close()

    def close(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except OSError:
                pass
            self.proc = None


# ---------------------------------------------------------------- 消すもの
class Item:
    """消す候補。sure=False は「タチコマが持ち込んだか分からない」(尋ねる / --all のときだけ消す)。"""

    def __init__(self, label, present, remove, sure=True, default=True, why="", flag=None):
        self.label, self.present, self.remove = label, present, remove
        self.sure, self.default, self.why, self.flag = sure, default, why, flag


class Uninstaller:
    def __init__(self, home, env=None, system=None, ollama=None, out=say, clock=time.time, self_exe=None):
        self.home = os.path.abspath(home)
        self.env = dict(os.environ if env is None else env)
        self.sys = system or System()
        self.ollama = ollama or Ollama(self.env)
        self.out, self.clock = out, clock
        self.self_exe = os.path.abspath(self_exe) if self_exe else None
        self.ledger = footprint.Ledger(self.home)
        self.cfg = self._json(os.path.join(self.home, "app", "config.json"))
        self.state = self._json(os.path.join(self.home, "state.json"))
        self.kept = []          # 入れる前からあったので残すもの

    @staticmethod
    def _json(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _judge(self, key, value, label, unknown_default):
        """(消す候補にするか, 確かか, 既定)。入れる前からあったものは残す。"""
        was = self.ledger.was_there(key, value)
        if was:
            self.kept.append(label)
            return False, True, False
        if was is False:
            return True, True, True
        return True, False, unknown_default

    def _paths(self):
        """タチコマを起動した場所 (インストール先と、ダウンロードした Tachikoma.exe)。"""
        return [self.home] + [p for p in self.ledger.created("launched_from") if not _inside(p, self.home)]

    def _matches(self, path_text):
        t = os.path.normcase(path_text)
        for p in self._paths():
            p = os.path.normcase(os.path.abspath(p))
            if t == p or t.startswith(p.rstrip("\\/") + os.sep) or t.startswith(p + "."):
                return True
        return False

    # ------------------------------------------------------------ 候補を集める
    def plan(self):
        self.kept = []
        items = [Item("動いているタチコマ (止める)", lambda: bool(self.sys.processes_under(self.home)),
                      self._stop)]
        items += self._docker_items()
        items += self._ollama_items()
        items += self._hf_items()
        items += self._pip_items()
        items += self._windows_items()
        items += self._temp_items()
        items += self._download_items()
        items.append(Item(f"インストール先 {self.home} (記憶・設定・学習と進化の成果・同梱の Python・キャッシュ)",
                          lambda: os.path.exists(self.home), self._remove_home))
        return items

    def _stop(self):
        for pid, _ in self.sys.processes_under(self.home):
            self.sys.kill(pid)
        time.sleep(0 if not self.sys.windows else 2)

    def _docker_items(self):
        if not self.sys.which("docker"):
            return []
        items = []
        name = footprint.CONTAINER

        def container():
            code, out = self.sys.run(["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"])
            return code == 0 and name in out
        items.append(Item(f"Docker のコンテナ {name} (自己進化の CPU の脳)", container,
                          lambda: self.sys.run(["docker", "rm", "-f", name])))
        images = dict.fromkeys(list(footprint.DOCKER_IMAGES) +
                               [self.cfg.get("sandbox_image"), self.cfg.get("cpu_brain_image")])
        evolution = "evolution" in self.state.get("features", [])
        for img in (i for i in images if i):
            label = f"Docker のイメージ {img}"
            use, sure, default = self._judge("docker_images", img, label, evolution)
            if use:
                items.append(Item(label, lambda img=img: img in (footprint.docker_images(self._run_sp) or []),
                                  lambda img=img: self.sys.run(["docker", "rmi", img]), sure, default,
                                  "自己進化の隔離テスト・CPU の脳で使う"))
        return items

    def _run_sp(self, cmd, **kw):
        code, out = self.sys.run(cmd)
        return subprocess.CompletedProcess(cmd, code, out, "")

    def _ollama_items(self):
        items = []
        try:
            now = self.ollama.models()
        except Exception:  # noqa: BLE001
            now = set()
        prefix = self.cfg.get("model_prefix", "tachikoma") + "-v"
        pulled = {footprint.normalize_model(m) for m in self.ledger.created("ollama_models")}
        base = footprint.normalize_model(self.cfg.get("model", "gemma4:e2b"))
        for m in sorted(now):
            label = f"Ollama のモデル {m}"
            if m.startswith(prefix):
                items.append(Item(label + " (タチコマが学習して作った版)", lambda m=m: m in self.ollama.models(),
                                  lambda m=m: self.ollama.delete(m)))
            elif m in pulled or m == base:
                use, sure, default = (True, True, True) if m in pulled else \
                    self._judge("ollama_models", m, label, True)
                if use:
                    items.append(Item(label, lambda m=m: m in self.ollama.models(), lambda m=m: self.ollama.delete(m),
                                      sure, default, "タチコマの考える力として取ったモデル"))
        exe = os.path.join(footprint.local_appdata(self.env), "Programs", "Ollama", "ollama.exe")
        if os.path.exists(exe):
            use, sure, default = self._judge("ollama_exe", None, "Ollama 本体", False)
            if use:
                items.append(Item("Ollama 本体 (と、入れる前に無かったならモデルの置き場 .ollama)",
                                  lambda: os.path.exists(exe), self._remove_ollama, False, default,
                                  "タチコマのために入れたのなら", flag="remove_ollama"))
        return items

    def _remove_ollama(self):
        self.ollama.close()
        for name in ("ollama app.exe", "ollama.exe"):
            self.sys.run(["taskkill", "/F", "/IM", name])
        root = os.path.join(footprint.local_appdata(self.env), "Programs", "Ollama")
        unins = sorted(glob.glob(os.path.join(root, "unins*.exe")))
        if unins:
            self.sys.run([unins[0], "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=600)
        if self.ledger.was_there("ollama_dir") is False:
            rmtree(os.path.dirname(footprint.ollama_models_dir(self.env)))

    def _hf_items(self):
        hub = footprint.hf_hub_dir(self.env)
        ids = [self.cfg.get("hf_base_model", "google/gemma-4-E2B-it"), self.cfg.get("asr_hf_base", "openai/whisper-small"),
               self.cfg.get("ocr_model", "kha-white/manga-ocr-base")]
        asr = self.cfg.get("asr_model", "small")
        if asr and "/" not in asr and "\\" not in asr:
            ids.append(f"Systran/faster-whisper-{asr}")
        items = []
        for repo in dict.fromkeys(i for i in ids if i and "/" in i):
            d = footprint.hf_repo_dir(repo)
            path = os.path.join(hub, d)
            if not os.path.isdir(path):
                continue
            label = f"Hugging Face のキャッシュ {repo}"
            use, sure, default = self._judge("hf_repos", d, label, True)
            if use:
                items.append(Item(label, lambda p=path: os.path.exists(p),
                                  lambda p=path, d=d: (rmtree(p), rmtree(os.path.join(hub, ".locks", d))),
                                  sure, default, "耳・目・頭の学習のために取った (古い版はここに置いていた)"))
        token = footprint.hf_token_file(self.env)
        if os.path.exists(token):
            use, sure, default = self._judge("hf_token", None, "Hugging Face のログイン情報", False)
            if use:
                items.append(Item("Hugging Face のログイン情報 (token)", lambda: os.path.exists(token),
                                  lambda: rmtree(token), False, default, "Gemma の学習のためにログインしたのなら"))
        return items

    def _pip_items(self):
        path = footprint.pip_cache_dir(self.env)
        if not os.path.isdir(path):
            return []
        use, sure, default = self._judge("pip_cache", None, "pip のキャッシュ", False)
        if not use:
            return []
        return [Item(f"pip のキャッシュ {path}", lambda: os.path.isdir(path), lambda: rmtree(path), sure, default,
                     "古い版が追加機能 (PyTorch など) を入れたときのもの")]

    def _windows_items(self):
        items = []
        link = footprint.start_menu_link(self.env)
        items.append(Item("スタートメニューの Tachikoma", lambda: os.path.exists(link), lambda: rmtree(link)))
        reg = self.sys.registry
        if reg is not None:
            items.append(Item("「設定 → アプリ」の一覧への登録", lambda: reg.exists(footprint.UNINSTALL_KEY),
                              lambda: reg.delete_tree(footprint.UNINSTALL_KEY)))
            items.append(Item("Windows に残った使用の記録 (マイク・カメラの許可、アプリ名、互換性の記録)",
                              lambda: bool(self._traces()), self._clear_traces))
        items.append(Item("Windows のファイアウォールの規則 (タチコマの Python に許可したもの)",
                          lambda: bool(self.sys.firewall_rules(self.home)),
                          lambda: [self.sys.remove_firewall_rule(n) for n in self.sys.firewall_rules(self.home)]))
        return items

    def _traces(self):
        reg = self.sys.registry
        found = []
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
        reg = self.sys.registry
        for kind, path, name in self._traces():
            try:
                reg.delete_tree(path) if kind == "key" else reg.delete_value(path, name)
            except OSError:
                pass

    def _temp_items(self):
        tmp = self.env.get("TEMP") or self.env.get("TMP") or tempfile.gettempdir()
        me = os.path.normcase(os.path.abspath(getattr(sys, "_MEIPASS", "") or "-"))
        own = os.path.normcase(self.self_exe or "-")

        def leftovers():
            out = [p for p in (os.path.join(tmp, "tachikoma_tts.wav"),) if os.path.exists(p)]
            out += glob.glob(os.path.join(tmp, "tachikoma-android-*"))
            out += [p for p in glob.glob(os.path.join(tmp, "tachikoma-uninstall-*.exe")) if os.path.normcase(p) != own]
            out += [p for p in glob.glob(os.path.join(tmp, "_MEI*"))
                    if os.path.exists(os.path.join(p, "payload.zip")) and os.path.normcase(os.path.abspath(p)) != me]
            return out
        return [Item("一時フォルダに残ったもの (声の一時ファイル・展開の残り)", lambda: bool(leftovers()),
                     lambda: [rmtree(p) for p in leftovers()])]

    def _download_items(self):
        items = []
        for p in self.ledger.created("launched_from"):
            if _inside(p, self.home) or not p.lower().endswith(".exe"):
                continue
            items.append(Item(f"ダウンロードした {p}", lambda p=p: os.path.exists(p), lambda p=p: self._remove_file(p),
                              False, False, "入れ直すときにまた使うなら残す"))
        return items

    def _remove_file(self, p):
        if self.self_exe and os.path.normcase(os.path.abspath(p)) == os.path.normcase(self.self_exe):
            self.sys.delete_later(p)
        else:
            rmtree(p)

    def _remove_home(self):
        os.chdir(os.path.dirname(self.home) or os.sep)
        left = rmtree(self.home)
        if left and self.sys.windows:     # 止めたばかりのプロセスが放すのを待って、もう一度
            time.sleep(3)
            rmtree(self.home)

    # ------------------------------------------------------------ 記憶を残す
    def backup(self, dst_dir):
        os.makedirs(dst_dir, exist_ok=True)
        path = os.path.join(dst_dir, time.strftime("Tachikoma-backup-%Y%m%d-%H%M%S.zip", time.localtime(self.clock())))
        app = os.path.join(self.home, "app")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(app):
                dirs[:] = [d for d in dirs if d not in BACKUP_SKIP]
                for f in files:
                    full = os.path.join(root, f)
                    zf.write(full, "app/" + os.path.relpath(full, app).replace("\\", "/"))
            for f in ("state.json", footprint.LEDGER):
                if os.path.exists(os.path.join(self.home, f)):
                    zf.write(os.path.join(self.home, f), f)
        return path

    # ------------------------------------------------------------ 実行
    def run(self, yes=False, include_unsure=False, dry=False, backup=None, flags=(), ask=None):
        ask = ask or (lambda q, d: d)
        items = [i for i in self.plan() if i.present()]
        b = self.ledger.before
        if b is None:
            self.out("台帳: なし (入れる前の姿の記録が無いので、持ち込んだか分からないものは尋ねます)")
        else:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.get("ts", 0)))
            self.out(f"台帳: {when} の記録" + (" (古い版から更新したときに写したもの。入れる前の姿としては不確か)"
                                              if b.get("legacy") else " (入れる前の姿)"))
        for k in dict.fromkeys(self.kept):
            self.out(f"  残す: {k} (入れる前からあった)")
        if not items:
            self.out("タチコマが残したものは、もう何もありません。")
            return 0
        chosen = []
        self.out("\n取り除くもの:")
        for it in items:
            if it.sure:
                self.out(f"  - {it.label}")
                chosen.append(it)
        unsure = [it for it in items if not it.sure]
        if unsure:
            self.out("\nタチコマが持ち込んだか分からないもの:")
            for it in unsure:
                self.out(f"  ? {it.label} ({it.why})")
        if dry:
            self.out("\n(--dry-run なので、何も消していません)")
            return 0
        for it in unsure:
            if yes:
                pick = include_unsure or (it.flag in flags)
            else:
                pick = ask(f"{it.label} も取り除きますか？", it.default)
            if pick:
                chosen.append(it)
        if not yes and not ask("\nこれらを取り除きます。よいですか？", False):
            self.out("やめました。何も消していません。")
            return 1
        if backup is None and not yes and os.path.isdir(os.path.join(self.home, "app")) and \
                ask("消す前に、記憶・設定・進化の成果を zip で残しますか？ (ドキュメントに置きます)", False):
            backup = os.path.join(self.env.get("USERPROFILE") or os.path.expanduser("~"), "Documents")
        if backup and os.path.isdir(os.path.join(self.home, "app")):
            self.out(f"残しました: {self.backup(backup)}")
        chosen.sort(key=items.index)
        errors = {}
        for it in chosen:
            self.out(f"取り除いています: {it.label}")
            try:
                it.remove()
            except Exception as e:  # noqa: BLE001
                errors[it.label] = str(e)
        self.ollama.close()

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
        skipped = [it for it in items if it not in chosen]
        for it in skipped:
            self.out(f"  残した      {it.label}")
        if left:
            self.out("\n取り除けなかったものがあります。PC を再起動してから、もう一度実行してください。")
            return 4
        self.out("\nタチコマが持ち込んだものは、すべて取り除きました。")
        if not self.sys.which("docker") and self.ledger.was_there("docker_desktop") is False and \
                footprint.docker_desktop(self.env):
            self.out("Docker Desktop をタチコマのために入れたのなら、「設定 → アプリ」から取り除いてください。")
        return 0


def ask_console(question, default):
    hint = "[Y/n]" if default else "[y/N]"
    try:
        a = input(f"{question} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not a else a in ("y", "yes", "はい")


def relocate(args_after, prefix=()):
    """インストール先の中の exe から動いているなら、一時フォルダに写して、そちらで続ける (自分は消せないので)。"""
    tmp = os.path.join(tempfile.gettempdir(), f"tachikoma-uninstall-{os.getpid()}.exe")
    shutil.copy2(sys.executable, tmp)
    subprocess.Popen([tmp, *prefix, *args_after, "--relocated"],
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), close_fds=True)
    return 0


def main(argv=None, prefix=(), system=None, env=None):
    env = os.environ if env is None else env
    default_home = os.path.join(footprint.local_appdata(env), footprint.APP_NAME)
    ap = argparse.ArgumentParser(prog="TachikomaUninstall", description="タチコマを取り除き、入れる前の姿に戻す")
    ap.add_argument("--home", default=default_home)
    ap.add_argument("--yes", action="store_true", help="確かめずに消す (持ち込んだと確かなものだけ)")
    ap.add_argument("--all", action="store_true", help="持ち込んだか分からないものも消す")
    ap.add_argument("--dry-run", action="store_true", help="見るだけ")
    ap.add_argument("--backup", default=None, help="消す前に記憶などを zip にして置くフォルダ")
    ap.add_argument("--remove-ollama", action="store_true", help="Ollama 本体も取り除く")
    ap.add_argument("--relocated", action="store_true", help=argparse.SUPPRESS)
    argv = list(sys.argv[1:] if argv is None else argv)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    frozen = getattr(sys, "frozen", False)
    if frozen and os.name == "nt" and not args.relocated and _inside(sys.executable, args.home):
        return relocate(argv, prefix)
    interactive = sys.stdin is not None and sys.stdin.isatty() and not args.yes
    say(f"タチコマを取り除きます: {args.home}\n")
    un = Uninstaller(args.home, env=env, system=system, self_exe=sys.executable if frozen else None)
    flags = {"remove_ollama"} if args.remove_ollama else set()
    try:
        code = un.run(yes=args.yes, include_unsure=args.all, dry=args.dry_run, backup=args.backup, flags=flags,
                      ask=ask_console if interactive else None)
    finally:
        un.ollama.close()
    if args.relocated:
        un.sys.delete_later(sys.executable)
    if interactive:
        try:
            input("\nEnter で閉じます… ")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
