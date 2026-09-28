"""Tachikoma.exe — 1 つのアプリとして「入れる・更新する・準備を整える・起動する」を行う入口。

    Tachikoma.exe               初回は %LOCALAPPDATA%\\Tachikoma に入れて、準備を整えて起動する。2 回目以降はそのまま起動
    Tachikoma.exe --setup       追加機能 (音声・カメラ・学習・自己進化) の選び直し
    Tachikoma.exe --selftest    入れたあと、同梱の Python でテスト一式を走らせる (動作確認)
    Tachikoma.exe --uninstall   取り除く (記憶・設定も消える)

中身 (payload.zip):
    python/   Windows 用の組み込み版 Python 3.11 (+ pip)。重い追加機能はここに pip で入れる
    app/      タチコマ本体のソース。自己進化はここを書き換える (だから一時フォルダではなく、書き込める場所に置く)
    manifest.json  版と、各ファイルのハッシュ・進化してよいファイルか

更新するとき、自己進化で書き換わったファイル (進化してよいファイルだけ) は残し、同梱の新しい版を *.new として横に置く。
記憶 (tachikoma.db)・設定 (config.json)・学習の成果・モデル・プラグインには触れない。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile

OLLAMA_DOWNLOAD = "https://ollama.com/download/windows"
DOCKER_DOWNLOAD = "https://www.docker.com/products/docker-desktop/"
TORCH_INDEX = "https://download.pytorch.org/whl/cu126"      # GTX 1060 (Pascal) を含む CUDA 版
# 自己進化の CPU の脳 (GGUF)。候補を順に確かめて、あるものを使う
GGUF = {
    "qwen2.5-coder-7b-instruct-q4_k_m.gguf": [
        "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
    ],
    "RakutenAI-7B-instruct-q4_K_M.gguf": [
        "https://huggingface.co/mmnga/RakutenAI-7B-instruct-gguf/resolve/main/RakutenAI-7B-instruct-q4_K_M.gguf",
    ],
}
FEATURES = {
    "voice": ("音声会話と耳の自習 (マイク・faster-whisper など 約 1GB)", "voice"),
    "learning": ("目 (日本語 OCR) の自習と、頭・耳・目の学習 (PyTorch など 約 5GB。GPU 用)", "learning"),
    "evolution": ("自己進化 (Docker Desktop と CPU 用の考えるモデル 約 9GB)", "evolution"),
}


def say(msg=""):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, "backslashreplace").decode(enc), flush=True)


def ask(question, default=True, interactive=True):
    if not interactive:
        return default
    hint = "[Y/n]" if default else "[y/N]"
    try:
        a = input(f"{question} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not a else a in ("y", "yes", "はい", "h")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def payload_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "payload.zip")


class App:
    def __init__(self, home, interactive=True):
        self.home = home
        self.interactive = interactive
        self.py = os.path.join(home, "python", "python.exe")
        self.app = os.path.join(home, "app")
        self.cfg_path = os.path.join(self.app, "config.json")
        self.state_path = os.path.join(home, "state.json")
        self.state = self._load(self.state_path, {})

    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def _save(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ 入れる・更新する
    def install(self, payload):
        with zipfile.ZipFile(payload) as zf:
            new = json.loads(zf.read("manifest.json"))
            if self.state.get("version") == new["version"] and os.path.exists(self.py):
                return False
            if "version" not in self.state:
                say(f"タチコマを入れています: {self.home}")
            else:
                say(f"タチコマを更新しています ({self.state['version']} → {new['version']}): {self.home}")
            if self.state.get("python") != new["python"] or not os.path.exists(self.py):
                shutil.rmtree(os.path.join(self.home, "python"), ignore_errors=True)
                for m in zf.namelist():
                    if m.startswith("python/"):
                        zf.extract(m, self.home)
                self.state["python"] = new["python"]
                self.state["features_installed"] = []          # Python を入れ直したら追加機能も入れ直す
            kept = self._merge_app(zf, new)
        self.state["version"] = new["version"]
        self._save(self.state_path, self.state)
        for rel in kept:
            say(f"  自己進化で書き換わった {rel} は残しました (同梱の版は {rel}.new)")
        return True

    def _merge_app(self, zf, new):
        old = self._load(os.path.join(self.app, ".manifest.json"), {}).get("files", {})
        kept = []
        for rel, meta in new["files"].items():
            dst = os.path.join(self.app, *rel.split("/"))
            data = zf.read("app/" + rel)
            if os.path.exists(dst):
                cur = sha256(dst)
                if cur == meta["sha"]:
                    continue
                old_sha = old.get(rel, {}).get("sha")
                locally_changed = old_sha is None or cur != old_sha     # 前の版と違う = ここで書き換えられた
                if meta["evolvable"] and locally_changed:
                    with open(dst + ".new", "wb") as f:
                        f.write(data)
                    kept.append(rel)
                    continue
                if not meta["evolvable"] and locally_changed:
                    shutil.copy2(dst, dst + ".local-backup")      # 承認したカーネル変更など。念のため残す
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
        for rel, meta in old.items():
            dst = os.path.join(self.app, *rel.split("/"))
            if rel not in new["files"] and os.path.exists(dst) and sha256(dst) == meta["sha"]:
                os.remove(dst)
        self._save(os.path.join(self.app, ".manifest.json"), new)
        return kept

    def ensure_config(self):
        if os.path.exists(self.cfg_path):
            return
        cfg = self._load(os.path.join(self.app, "config.example.json"), {})
        cfg.update({"watch_dirs": [], "terminal_logs": [], "voice": False, "study": False, "eye": False,
                    "camera": False, "ui": True, "finetune_enabled": False, "asr_finetune_enabled": False,
                    "ocr_finetune_enabled": False, "evolution_enabled": False, "verbose": True})
        self._save(self.cfg_path, cfg)
        say(f"設定ファイルを作りました: {self.cfg_path}")

    def update_config(self, **kw):
        cfg = self._load(self.cfg_path, {})
        cfg.update(kw)
        self._save(self.cfg_path, cfg)

    def shortcut(self):
        """スタートメニューに登録し、自分の写しをインストール先に置く (どこから起動しても同じアプリになる)。"""
        me = sys.executable
        target = os.path.join(self.home, "Tachikoma.exe")
        if getattr(sys, "frozen", False) and os.path.abspath(me) != os.path.abspath(target):
            try:
                shutil.copy2(me, target)
            except OSError:
                return
        link = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs",
                            "Tachikoma.lnk")
        if os.path.exists(link) or not os.path.exists(target):
            return
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
              f"$s.TargetPath='{target}';$s.WorkingDirectory='{self.home}';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)

    # ------------------------------------------------------------ 準備を整える
    def ollama(self, model):
        url = "http://127.0.0.1:11434"
        tags = self._get_json(url + "/api/tags")
        if tags is None:
            exe = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
            exe = exe if os.path.exists(exe) else shutil.which("ollama")
            if exe:
                say("Ollama を起動します…")
                subprocess.Popen([exe, "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for _ in range(20):
                    time.sleep(1)
                    tags = self._get_json(url + "/api/tags")
                    if tags is not None:
                        break
        while tags is None:
            say("\n考える力 (Gemma) を動かす Ollama が見つかりません。")
            say(f"  {OLLAMA_DOWNLOAD} から入れてください (入れると自動で起動します)。")
            if not self.interactive:
                return False
            if ask("ダウンロードのページを開きますか？"):
                webbrowser.open(OLLAMA_DOWNLOAD)
            try:
                input("入れ終わったら Enter を押してください (やめるなら Ctrl+C)… ")
            except EOFError:
                return False
            tags = self._get_json(url + "/api/tags")
        names = {m.get("name") for m in tags.get("models", [])}
        if model in names or f"{model}:latest" in names:
            return True
        say(f"\n考える力のモデル {model} を取得します (数 GB。初回だけ)…")
        return self._pull(url, model)

    @staticmethod
    def _get_json(url, timeout=3):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _pull(url, model):
        req = urllib.request.Request(url + "/api/pull", data=json.dumps({"model": model}).encode(),
                                     headers={"Content-Type": "application/json"})
        last = ""
        try:
            with urllib.request.urlopen(req, timeout=3600) as r:
                for line in r:
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if m.get("error"):
                        say(f"  取得に失敗: {m['error']}")
                        return False
                    msg = m.get("status", "")
                    if m.get("total"):
                        msg += f" {100 * m.get('completed', 0) / m['total']:.0f}%"
                    if msg != last:
                        print("\r  " + msg.ljust(60), end="", flush=True)
                        last = msg
        except OSError as e:
            say(f"\n  取得に失敗: {e}")
            return False
        say("\n  取得しました。")
        return True

    def pip(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PIP_DISABLE_PIP_VERSION_CHECK="1")
        return subprocess.run([self.py, "-m", "pip", "install", "--no-warn-script-location", *args],
                              cwd=self.app, env=env).returncode == 0

    def choose_features(self):
        """追加機能を選んで入れる。選んだものは覚えておき、Python を入れ直したときに自動で入れ直す。"""
        wanted = list(self.state.get("features", []))
        if self.interactive:
            say("\n追加機能を選んでください (あとで `Tachikoma.exe --setup` で選び直せます)。")
            wanted = [k for k, (label, _) in FEATURES.items() if ask(f"- {label} を使いますか？", k in wanted)]
        self.state["features"] = wanted
        self._save(self.state_path, self.state)

    def install_features(self):
        done = set(self.state.get("features_installed", []))
        for k in self.state.get("features", []):
            if k in done:
                continue
            ok = {"voice": self._voice, "learning": self._learning, "evolution": self._evolution}[k]()
            if ok:
                done.add(k)
                self.state["features_installed"] = sorted(done)
                self._save(self.state_path, self.state)

    def _voice(self):
        say("\n音声会話の部品を入れます…")
        ok = self.pip("-r", "requirements-voice.txt", "nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*")
        if ok:
            self.update_config(voice=True, study=True, camera=True)
            if not shutil.which("ffmpeg"):
                say("  字幕付き動画の自習には ffmpeg も要ります (https://ffmpeg.org から入れて PATH に置く)。")
        return ok

    def _learning(self):
        say("\n学習の部品 (PyTorch など) を入れます。時間がかかります…")
        ok = (self.pip("torch", "torchvision", "--index-url", TORCH_INDEX)
              and self.pip("-r", os.path.join("finetune", "requirements.txt"))
              and self.pip("-r", "requirements-eye.txt"))
        if ok:
            self.update_config(eye=True, finetune_enabled=True, asr_finetune_enabled=True, ocr_finetune_enabled=True)
            say("  頭 (Gemma) の学習には、Hugging Face で Gemma の利用規約に同意してログインが要ります:")
            say(f"    \"{self.py}\" -c \"from huggingface_hub import login; login()\"")
        return ok

    def _evolution(self):
        docker_ok = bool(shutil.which("docker")) and \
            subprocess.run(["docker", "info"], capture_output=True).returncode == 0
        if not docker_ok:
            say("\n自己進化には Docker Desktop が要ります (隔離テストと CPU の脳に使う)。")
            say(f"  {DOCKER_DOWNLOAD}")
            if self.interactive and ask("ダウンロードのページを開きますか？"):
                webbrowser.open(DOCKER_DOWNLOAD)
            say("  入れて起動したら、もう一度 `Tachikoma.exe --setup` を実行してください。")
            return False
        models = os.path.join(self.app, "models")
        os.makedirs(models, exist_ok=True)
        for name, urls in GGUF.items():
            dst = os.path.join(models, name)
            if os.path.exists(dst):
                continue
            if not self._download(urls, dst):
                say(f"  {name} を自動で取得できませんでした。Hugging Face から GGUF を入手して {dst} に置いてください。")
        self.update_config(evolution_enabled=True)
        return True

    def _download(self, urls, dst):
        for url in urls:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
                    total = int(r.headers.get("Content-Length") or 0)
            except (OSError, ValueError):
                continue
            say(f"\n  {os.path.basename(dst)} を取得します ({total / 2**30:.1f} GB)…")
            part = dst + ".part"
            have = os.path.getsize(part) if os.path.exists(part) else 0
            req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
            try:
                with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if have else "wb") as f:
                    while True:
                        chunk = r.read(1 << 22)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        if total:
                            print(f"\r  {100 * have / total:.0f}%", end="", flush=True)
            except OSError as e:
                say(f"\n  途中で止まりました ({e})。もう一度 --setup すると続きから取得します。")
                return False
            os.replace(part, dst)
            say("\n  取得しました。")
            return True
        return False

    # ------------------------------------------------------------ 起動する
    def env(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", TACHIKOMA_APP="1")
        site = os.path.join(self.home, "python", "Lib", "site-packages", "nvidia")
        if os.path.isdir(site):     # faster-whisper (CUDA) が cuBLAS / cuDNN の DLL を見つけられるように
            dlls = [os.path.join(site, d, "bin") for d in os.listdir(site) if os.path.isdir(os.path.join(site, d, "bin"))]
            env["PATH"] = os.pathsep.join(dlls + [env.get("PATH", "")])
        return env

    def open_ui_when_ready(self):
        cfg = self._load(self.cfg_path, {})
        if not cfg.get("ui", True) or not cfg.get("ui_open_browser", True):
            return
        url = f"http://127.0.0.1:{cfg.get('ui_port', 8765)}/"

        def wait():
            for _ in range(120):
                try:
                    urllib.request.urlopen(url, timeout=2).close()
                    webbrowser.open(url)
                    return
                except (OSError, urllib.error.URLError):
                    time.sleep(1)
        threading.Thread(target=wait, daemon=True).start()

    def run(self):
        self.open_ui_when_ready()
        say("タチコマを起動します (終わるときは Ctrl+C)。")
        try:
            return subprocess.call([self.py, "supervisor.py", "--config", self.cfg_path, "--verbose"],
                                   cwd=self.app, env=self.env())
        except KeyboardInterrupt:
            return 0

    def selftest(self):
        say("同梱の Python でテスト一式を走らせます…")
        return subprocess.call([self.py, "-B", "-m", "unittest", "discover", "-s", "tests"], cwd=self.app, env=self.env())

    def uninstall(self):
        if self.interactive and not ask(f"{self.home} を消します (記憶・設定・学習の成果も消えます)。よいですか？", False):
            return 1
        link = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs",
                            "Tachikoma.lnk")
        if os.path.exists(link):
            os.remove(link)
        shutil.rmtree(self.home, ignore_errors=True)
        say("取り除きました。")
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="Tachikoma")
    ap.add_argument("--home", default=os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Tachikoma"))
    ap.add_argument("--setup", action="store_true", help="追加機能を選び直す")
    ap.add_argument("--selftest", action="store_true", help="入れたあと、テスト一式を走らせる")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--no-shortcut", action="store_true")
    ap.add_argument("--no-start", action="store_true", help="準備だけして起動しない")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    interactive = sys.stdin is not None and sys.stdin.isatty()
    app = App(args.home, interactive)
    if args.uninstall:
        return app.uninstall()
    app.install(payload_path())
    app.ensure_config()
    if args.selftest:
        return app.selftest()
    if not args.no_shortcut:
        app.shortcut()
    if "features" not in app.state or args.setup:
        app.choose_features()
    app.install_features()
    model = app._load(app.cfg_path, {}).get("model", "gemma4:e2b")
    if not app.ollama(model):
        say("Ollama と Gemma の準備ができていないので、起動をやめます。")
        return 3
    if args.no_start:
        return 0
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
