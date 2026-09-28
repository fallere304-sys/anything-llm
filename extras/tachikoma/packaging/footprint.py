"""タチコマが PC に残すものの台帳 — 原状回復のために「入れる前の姿」と「入れてから作ったもの」を記録する。

初回に入れるとき、入れる前の状態 (Ollama とそのモデル・Hugging Face と pip のキャッシュ・Docker のイメージ) を
写しておき、外に何かを作るたび (スタートメニュー・アプリ一覧への登録・Ollama に取ったモデル・起動した場所) に書き足す。
アンインストーラ (uninstaller.py) はこの台帳を見て、「入れる前からあったものは残し、タチコマが持ち込んだものだけを消す」。

タチコマが使うキャッシュ (pip・Hugging Face・PyTorch) は、インストール先の cache/ に置く (cache_env)。
だからインストール先のフォルダを消せば、その分は跡形もなくなる。
"""

import json
import os
import shutil
import subprocess
import time

LEDGER = "footprint.json"
APP_NAME = "Tachikoma"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Tachikoma"
CONTAINER = "tachikoma-cpu-brain"                                   # kernel/cpu_brain.py の NAME
DOCKER_IMAGES = ("python:3.11-slim", "ghcr.io/ggml-org/llama.cpp:server")   # config の既定
OLLAMA_URL = "http://127.0.0.1:11434"


# ---------------------------------------------------------------- 場所
def home_dir(env=None):
    env = os.environ if env is None else env
    return env.get("USERPROFILE") or os.path.expanduser("~")


def local_appdata(env=None):
    env = os.environ if env is None else env
    return env.get("LOCALAPPDATA") or os.path.join(home_dir(env), "AppData", "Local")


def start_menu_link(env=None):
    env = os.environ if env is None else env
    appdata = env.get("APPDATA") or os.path.join(home_dir(env), "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Tachikoma.lnk")


def ollama_exe(env=None):
    exe = os.path.join(local_appdata(env), "Programs", "Ollama", "ollama.exe")
    return exe if os.path.exists(exe) else shutil.which("ollama")


def ollama_models_dir(env=None):
    env = os.environ if env is None else env
    return env.get("OLLAMA_MODELS") or os.path.join(home_dir(env), ".ollama", "models")


def hf_hub_dir(env=None):
    """相棒がふだん使う (タチコマがキャッシュを自分の中に移す前の) Hugging Face のキャッシュ。"""
    env = os.environ if env is None else env
    if env.get("HF_HUB_CACHE"):
        return env["HF_HUB_CACHE"]
    hf_home = env.get("HF_HOME") or os.path.join(home_dir(env), ".cache", "huggingface")
    return os.path.join(hf_home, "hub")


def hf_token_file(env=None):
    env = os.environ if env is None else env
    hf_home = env.get("HF_HOME") or os.path.join(home_dir(env), ".cache", "huggingface")
    return os.path.join(hf_home, "token")


def pip_cache_dir(env=None):
    env = os.environ if env is None else env
    return env.get("PIP_CACHE_DIR") or os.path.join(local_appdata(env), "pip", "Cache")


def docker_desktop(env=None):
    env = os.environ if env is None else env
    exe = os.path.join(env.get("ProgramFiles") or r"C:\Program Files", "Docker", "Docker", "Docker Desktop.exe")
    return os.path.exists(exe)


def cache_env(home):
    """タチコマが使うキャッシュをインストール先の中に置く環境変数。"""
    cache = os.path.join(home, "cache")
    return {"PIP_CACHE_DIR": os.path.join(cache, "pip"), "HF_HOME": os.path.join(cache, "huggingface"),
            "TORCH_HOME": os.path.join(cache, "torch"), "HF_HUB_DISABLE_TELEMETRY": "1"}


# ---------------------------------------------------------------- いまの姿を調べる
def ollama_models(env=None):
    """Ollama のモデル一覧 (名前:タグ)。Ollama が動いていなくても、置き場所のファイルから読む。"""
    root = os.path.join(ollama_models_dir(env), "manifests")
    out = set()
    if not os.path.isdir(root):
        return out
    for dirpath, _, files in os.walk(root):
        for tag in files:
            parts = os.path.relpath(os.path.join(dirpath, tag), root).replace("\\", "/").split("/")
            if len(parts) < 4:
                continue
            host, ns, name = parts[0], parts[1], "/".join(parts[2:-1])
            full = name if (host == "registry.ollama.ai" and ns == "library") else \
                (f"{ns}/{name}" if host == "registry.ollama.ai" else f"{host}/{ns}/{name}")
            out.add(f"{full}:{parts[-1]}")
    return out


def hf_repos(env=None):
    root = hf_hub_dir(env)
    try:
        return {d for d in os.listdir(root) if d.startswith(("models--", "datasets--"))}
    except OSError:
        return set()


def hf_repo_dir(repo_id):
    return "models--" + repo_id.replace("/", "--")


def docker_images(run=subprocess.run):
    """Docker のイメージ一覧。Docker が無い・動いていなければ None (分からない)。"""
    try:
        r = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True, text=True,
                timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return sorted({line.strip() for line in r.stdout.splitlines() if line.strip()})


def normalize_model(name):
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


# ---------------------------------------------------------------- 台帳
class Ledger:
    def __init__(self, home):
        self.path = os.path.join(home, LEDGER)
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    @property
    def before(self):
        return self.data.get("before")

    @property
    def legacy(self):
        """入れたあとで写した台帳 (古い版から更新した場合)。入れる前の姿としては当てにならない。"""
        return bool((self.before or {}).get("legacy", True))

    def snapshot(self, env=None, legacy=False, clock=time.time):
        if self.before is not None:
            return False
        self.data["before"] = {
            "ts": clock(), "legacy": legacy,
            "ollama_exe": bool(ollama_exe(env)),
            "ollama_dir": os.path.isdir(os.path.dirname(ollama_models_dir(env))),
            "ollama_models": sorted(ollama_models(env)),
            "hf_repos": sorted(hf_repos(env)),
            "hf_token": os.path.exists(hf_token_file(env)),
            "pip_cache": os.path.isdir(pip_cache_dir(env)),
            "docker_desktop": docker_desktop(env),
        }
        self.save()
        return True

    def snapshot_docker(self, run=subprocess.run):
        """自己進化を選んだときに、Docker のイメージを写す (Docker は後から入ることが多いので、初回とは別に)。"""
        if "docker_images" in (self.before or {}) or self.before is None:
            return False
        images = docker_images(run)
        if images is None:
            return False
        self.data["before"]["docker_images"] = images
        self.save()
        return True

    def note(self, kind, value):
        items = self.data.setdefault("created", {}).setdefault(kind, [])
        if value not in items:
            items.append(value)
            self.save()

    def created(self, kind):
        return list(self.data.get("created", {}).get(kind, []))

    def was_there(self, key, value=None):
        """入れる前からあったか: True / False / None (分からない)。"""
        b = self.before
        if b is None or b.get("legacy", True) or key not in b:
            return None
        if value is None:
            return bool(b[key])
        return value in b[key]


# ---------------------------------------------------------------- レジストリ (Windows・今の利用者の範囲 HKCU だけ)
class WinRegistry:
    def __init__(self):
        import winreg
        self.w = winreg
        self.root = winreg.HKEY_CURRENT_USER

    def exists(self, path):
        try:
            self.w.CloseKey(self.w.OpenKey(self.root, path))
            return True
        except OSError:
            return False

    def subkeys(self, path):
        out = []
        try:
            with self.w.OpenKey(self.root, path) as k:
                i = 0
                while True:
                    try:
                        out.append(self.w.EnumKey(k, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            pass
        return out

    def values(self, path):
        out = []
        try:
            with self.w.OpenKey(self.root, path) as k:
                i = 0
                while True:
                    try:
                        out.append(self.w.EnumValue(k, i)[0])
                    except OSError:
                        break
                    i += 1
        except OSError:
            pass
        return out

    def set_values(self, path, values):
        with self.w.CreateKey(self.root, path) as k:
            for name, v in values.items():
                if isinstance(v, int):
                    self.w.SetValueEx(k, name, 0, self.w.REG_DWORD, v)
                else:
                    self.w.SetValueEx(k, name, 0, self.w.REG_SZ, str(v))

    def delete_value(self, path, name):
        with self.w.OpenKey(self.root, path, 0, self.w.KEY_SET_VALUE) as k:
            self.w.DeleteValue(k, name)

    def delete_tree(self, path):
        for sub in self.subkeys(path):
            self.delete_tree(path + "\\" + sub)
        self.w.DeleteKey(self.root, path)


def registry():
    return WinRegistry() if os.name == "nt" else None
