"""Ollama の GPU が動かないときの逃げ道 (カーネル)。

GPU 用の部品 (Ollama の CUDA 版) が落ちる PC がある。よくあるのは、NVIDIA のドライバが Ollama の CUDA より古い場合:
    llama-server process has terminated: exit status 0xc0000409 … CUDA error: the provided PTX was compiled
    with an unsupported toolchain
このときは、GPU を見せない (CUDA_VISIBLE_DEVICES=-1) Ollama をもう 1 つ、別の番号 (127.0.0.1:11435) で立てて、
CPU だけで動かす。相棒がふだん使う Ollama (11434) には触れない。モデルの置き場所は同じなので、取り直しは要らない。
起動のたびにまず GPU を試すので、ドライバを新しくすれば自動で GPU に戻る。
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

GPU_FAILURE = re.compile(r"CUDA|cuda|cuBLAS|PTX|ggml_cuda|0xc0000409|0xc0000005|"
                         r"(?:llama-server|llama runner|runner) process (?:has )?terminated|no kernel image", re.I)
DRIVERS_URL = "https://www.nvidia.com/ja-jp/drivers/"
HIDE_GPU = {"CUDA_VISIBLE_DEVICES": "-1", "HIP_VISIBLE_DEVICES": "-1", "ROCR_VISIBLE_DEVICES": "-1",
            "GGML_VK_VISIBLE_DEVICES": "-1"}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_gpu_failure(text):
    """推論サーバーの GPU の部品が落ちた (つながらない・モデルが無い、とは別) か。"""
    return bool(GPU_FAILURE.search(text or ""))


def gpu_info(run=subprocess.run):
    """(GPU の名前, ドライバの版)。分からなければ ("", "")。"""
    try:
        r = run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return "", ""
    line = (r.stdout or "").strip().splitlines()[:1]
    if r.returncode != 0 or not line:
        return "", ""
    name, _, ver = line[0].partition(",")
    return name.strip(), ver.strip()


def explain(error, info):
    name, ver = info
    gpu = f"{name} / ドライバ {ver}" if name else "(nvidia-smi で調べられませんでした)"
    return ("GPU で考える部品 (Ollama の CUDA 版) が、この PC では動きませんでした。\n"
            f"  GPU: {gpu}\n"
            f"  Ollama の返事: {str(error)[:300]}\n"
            "  よくある原因: NVIDIA のドライバが Ollama より古い (「PTX … unsupported toolchain」はこれ)。\n"
            f"  直し方: {DRIVERS_URL} で GPU (GeForce GTX 1060 など) を選んで最新のドライバを入れ、PC を再起動する。\n"
            "  それまでは CPU だけで動かします (返事が遅くなります)。ドライバを新しくすれば、次の起動から自動で GPU に戻ります。")


def ollama_exe(cfg, env=None, which=shutil.which):
    env = os.environ if env is None else env
    exe = os.path.join(env.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    if env.get("LOCALAPPDATA") and os.path.exists(exe):
        return exe
    return which(cfg.get("ollama_bin") or "ollama")


class CpuOllama:
    """GPU を見せない Ollama を、この PC の中だけ (127.0.0.1) で立てる。"""

    def __init__(self, cfg, popen=subprocess.Popen, opener=urllib.request.urlopen, sleep=time.sleep,
                 env=None, which=shutil.which):
        self.cfg = cfg
        self.port = int(cfg.get("ollama_cpu_port", 11435))
        self.url = f"http://127.0.0.1:{self.port}"
        self.popen, self.opener, self.sleep, self.which = popen, opener, sleep, which
        self.env = os.environ if env is None else env
        self.proc = None

    def alive(self):
        try:
            with self.opener(self.url + "/api/version", timeout=3) as r:
                return "version" in json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError):
            return False

    def start(self, wait_s=40):
        """立てた (または前から立っていた) ら URL を返す。立てられなければ None。"""
        if self.alive():
            return self.url
        exe = ollama_exe(self.cfg, self.env, self.which)
        if not exe:
            return None
        env = dict(self.env, OLLAMA_HOST=f"127.0.0.1:{self.port}", **HIDE_GPU)
        try:
            self.proc = self.popen([exe, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=CREATE_NO_WINDOW)
        except OSError:
            return None
        atexit.register(self.stop)
        for _ in range(wait_s):
            self.sleep(1)
            if self.alive():
                return self.url
        self.stop()
        return None

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except OSError:
                pass
            self.proc = None


def connect(cfg, make_client, ping, out=print, cpu=None, info=gpu_info):
    """推論サーバーにつなぐ。GPU の部品が落ちるなら CPU の Ollama に切り替える。

    (client, 知らせ) を返す。つなげなければ LLMError をそのまま投げる (知らせは out に出してある)。"""
    client = make_client(cfg)
    try:
        ping(client)
        return client, ""
    except Exception as e:  # noqa: BLE001  (LLMError。ここでは種類を問わず原因の文字で見分ける)
        if not is_gpu_failure(str(e)) or cfg.get("ollama_gpu_fallback", True) is False:
            raise
        out(explain(e, info()))
        cpu = cpu or CpuOllama(cfg)
        url = cpu.start()
        if url is None:
            out("CPU だけで動く Ollama を立てられませんでした (ollama.exe が見つからない、または起動しない)。")
            raise
    cfg["ollama_url"] = url
    client = make_client(cfg)
    ping(client)
    return client, "GPU が使えないので、CPU だけで考えています (遅め)。NVIDIA のドライバを新しくすると GPU に戻ります。"
