"""llama.cpp (llama-server) の取得と起動。

- 取得: GitHub の最新リリースから Windows 用の組を選ぶ。GPU を使える順に cuda → vulkan → cpu
  (NVIDIA のドライバが古くて CUDA 版が動かない PC でも、Vulkan 版なら GPU が使えることが多い)
- 起動: モデルは mmap で開く。PC のメモリに入りきらない分は、必要なときに SSD から読まれる (速度は落ちる)。
  MoE モデルは「専門家」の重みを CPU 側 (メモリ・SSD) に置き、共通部分と文脈 (KV) を GPU に置く
- 読み込めても、最初の 1 回の推論で落ちる GPU がある (古いドライバ)。短い推論で確かめてから使い、
  だめなら次の組に切り替える
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import zipfile

RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
KINDS = ("cuda", "vulkan", "cpu")
ASSETS = {
    "cuda": re.compile(r"^llama-.*-bin-win-cuda-12[\d.]*-x64\.zip$"),
    "vulkan": re.compile(r"^llama-.*-bin-win-vulkan-x64\.zip$"),
    "cpu": re.compile(r"^llama-.*-bin-win-cpu-x64\.zip$"),
}
CUDART = re.compile(r"^cudart-llama-bin-win-cuda-12[\d.]*-x64\.zip$")
UA = {"User-Agent": "LocalCoder"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _get(url, opener=urllib.request.urlopen, timeout=60):
    with opener(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def latest_assets(opener=urllib.request.urlopen):
    """{名前: URL} と版の名前。"""
    rel = json.loads(_get(RELEASES, opener))
    return rel.get("tag_name", ""), {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}


def pick(assets, kind):
    names = sorted(n for n in assets if ASSETS[kind].match(n))
    if not names:
        return []
    out = [names[-1]]
    if kind == "cuda":
        rt = sorted(n for n in assets if CUDART.match(n))
        out += rt[-1:]
    return out


def download(url, dst, say=print, opener=urllib.request.urlopen):
    """途中からの再開つきで取ってくる。"""
    part = dst + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = dict(UA, **({"Range": f"bytes={have}-"} if have else {}))
    with opener(urllib.request.Request(url, headers=headers), timeout=120) as r:
        resumed = getattr(r, "status", 200) == 206
        total = int(r.headers.get("Content-Length") or 0) + (have if resumed else 0)
        mode = "ab" if resumed else "wb"
        done = have if resumed else 0
        last = 0.0
        with open(part, mode) as f:
            while True:
                chunk = r.read(1 << 22)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 2:
                    say(f"\r  {os.path.basename(dst)}: {done / 2**30:.2f} / {total / 2**30:.2f} GB", end="")
                    last = time.time()
    os.replace(part, dst)
    say(f"\r  {os.path.basename(dst)}: 取得しました ({done / 2**30:.2f} GB)          ")


def install(home, kinds=KINDS, say=print, opener=urllib.request.urlopen):
    """llama.cpp の組を取ってきて home/llama/<kind>/ に広げる。広げた組の名前のリストを返す。"""
    tag, assets = latest_assets(opener)
    say(f"llama.cpp {tag} を用意します")
    ready = []
    for kind in kinds:
        dst = os.path.join(home, "llama", kind)
        if find_server(dst):
            ready.append(kind)
            continue
        names = pick(assets, kind)
        if not names:
            say(f"  {kind} 版が見つかりませんでした")
            continue
        os.makedirs(dst, exist_ok=True)
        for n in names:
            z = os.path.join(home, "llama", n)
            download(assets[n], z, say, opener)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dst)
            os.remove(z)
        if find_server(dst):
            ready.append(kind)
    return ready


def find_server(folder):
    for d, _, files in os.walk(folder):
        for f in files:
            if f.lower() in ("llama-server.exe", "llama-server"):
                return os.path.join(d, f)
    return None


def server_args(kind, model, port, ctx, threads, minimal=False):
    a = ["-m", model, "--host", "127.0.0.1", "--port", str(port), "-c", str(ctx), "--jinja", "-t", str(threads)]
    if kind != "cpu":
        a += ["-ngl", "99"]
        if not minimal:
            a += ["--cpu-moe"]          # MoE の専門家はメモリ・SSD 側に。GPU (6GB) には共通部分と文脈を置く
    if not minimal:
        a += ["-fa", "auto"]
    return a


class Server:
    def __init__(self, home, port=8090, ctx=32768, threads=None, say=print, popen=subprocess.Popen,
                 opener=urllib.request.urlopen, sleep=time.sleep):
        self.home, self.port, self.ctx = home, port, ctx
        self.threads = threads or max(1, (os.cpu_count() or 4) // 2)
        self.say, self.popen, self.opener, self.sleep = say, popen, opener, sleep
        self.proc, self.kind = None, None
        self.url = f"http://127.0.0.1:{port}"
        self.log_path = os.path.join(home, "logs", "llama-server.log")

    def _health(self):
        try:
            with self.opener(self.url + "/health", timeout=5) as r:
                return json.loads(r.read().decode("utf-8")).get("status") == "ok"
        except (OSError, ValueError):
            return False

    def _probe(self):
        """短い推論を 1 回。読み込めても推論で落ちる GPU を見分ける。"""
        body = json.dumps({"model": "local", "messages": [{"role": "user", "content": "OK とだけ返して"}],
                           "max_tokens": 4}).encode()
        req = urllib.request.Request(self.url + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=1800) as r:
                return "choices" in json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError):
            return False

    def _tail(self):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                return f.read()[-1500:]
        except OSError:
            return ""

    def start(self, kind, model, wait_s=3600):
        exe = find_server(os.path.join(self.home, "llama", kind))
        if not exe:
            return False
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        for minimal in (False, True):
            self.stop()
            log = open(self.log_path, "w", encoding="utf-8", errors="replace")
            self.proc = self.popen([exe] + server_args(kind, model, self.port, self.ctx, self.threads, minimal),
                                   cwd=os.path.dirname(exe), stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=CREATE_NO_WINDOW)
            t0 = time.time()
            while time.time() - t0 < wait_s:
                if self._health():
                    break
                if self.proc.poll() is not None:
                    break
                self.sleep(2)
            log.close()
            if self.proc.poll() is None and self._health():
                if self._probe():
                    self.kind = kind
                    return True
                self.say(f"  {kind} 版は読み込めたが、推論で失敗した")
                self.stop()
                return False
            tail = self._tail()
            if not minimal and re.search(r"invalid argument|unknown argument|unrecognized|error: (invalid|unknown)",
                                         tail, re.I):
                continue                 # 古い / 新しい llama.cpp で使えない引数があった: 最小の引数でもう一度
            self.say(f"  {kind} 版は起動できなかった: {tail.strip().splitlines()[-1][:200] if tail.strip() else '?'}")
            self.stop()
            return False
        return False

    def start_best(self, model, kinds=KINDS):
        for kind in kinds:
            if not find_server(os.path.join(self.home, "llama", kind)):
                continue
            self.say(f"モデルを読み込んでいます ({kind} 版)… 大きいモデルは数分かかります")
            if self.start(kind, model):
                self.say(f"準備できました ({kind} 版)")
                return kind
        return None

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


def free_gb(path):
    try:
        return shutil.disk_usage(path).free / 2**30
    except OSError:
        return 0.0
