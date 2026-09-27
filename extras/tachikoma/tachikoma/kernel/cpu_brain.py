"""CPU の脳: 自己進化のための「考える力」を、GPU ではなく CPU と RAM で動かす。

GPU (6GB) は会話・耳・目のために空けておき、自分のコードやプロンプトを書き直す仕事は
Docker の中の llama.cpp サーバー (CPU 専用) にやらせる。資源の上限はコンテナで強制する:

    docker run --cpus <evolution_threads> --memory <cpu_brain_ram_gb>g  ghcr.io/ggml-org/llama.cpp:server
        -m /models/<コード生成モデル.gguf> -t <threads> -c <ctx>

- 会話中はコンテナを一時停止 (docker pause) して CPU を即座に返し、会話が終わったら再開する
- 役割ごとにモデルを使い分ける (RAM 8GB に 2 つは載らないので、必要なときに入れ替える):
    code … コードの改良: Qwen2.5-Coder-7B-Instruct (Q4_K_M 約 4.7GB)。HumanEval 88% 台と報告されるコード特化モデル
    ja   … 日本語の指示文・アイデア出し: RakutenAI-7B-instruct (Q4_K_M 約 4.4GB)。日本語に強いが、
           元の Mistral-7B 系はコード生成が弱いのでコードの改良には使わない
  片方しか無ければ、ある方を両方の役に使う。GGUF は利用者が用意し、読み取り専用でマウントする
- mode="external" なら、利用者が自分で起動した OpenAI 互換サーバー (llama-server.exe 等) を使う
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request


class CpuBrainError(RuntimeError):
    pass


class CpuBrain:
    NAME = "tachikoma-cpu-brain"

    def __init__(self, cfg, budget, run=subprocess.run, opener=urllib.request.urlopen, clock=time.monotonic):
        self.cfg, self.budget, self._run, self.opener, self.clock = cfg, budget, run, opener, clock
        self.url = cfg["cpu_brain_url"].rstrip("/")
        self.mode = cfg["cpu_brain_mode"]
        self.paused = False
        self.role = None            # いま載っているモデルの役割

    def model_for(self, role):
        models = self.cfg.get("cpu_brain_models") or {}
        path = models.get(role) or next((m for m in models.values() if m), None)
        return path if path and os.path.exists(path) else None

    def use(self, role):
        """その役割のモデルで動いている状態にする (必要ならコンテナを入れ替える)。"""
        if self.mode == "external":
            return
        if self.role != role or not self.healthy():
            want = self.model_for(role)
            cur = self.model_for(self.role) if self.role else None
            if want is None:
                raise CpuBrainError(f"CPU の脳のモデル ({role}) がありません")
            if want != cur or not self.healthy():
                self.ensure_running(role)
            self.role = role

    # ------------------------------------------------------------ 起動・停止
    def run_command(self, role="code"):
        model = os.path.abspath(self.model_for(role) or "")
        threads = min(self.cfg["evolution_threads"], self.budget.threads)
        ram = min(self.cfg["cpu_brain_ram_gb"], self.budget.ram_gb)
        port = self.url.rsplit(":", 1)[-1]
        return [self.cfg["docker_bin"], "run", "-d", "--rm", "--name", self.NAME,
                "--cpus", str(threads), "--memory", f"{ram:g}g", "--memory-swap", f"{ram:g}g",
                "-p", f"127.0.0.1:{port}:8080",
                "-v", f"{os.path.dirname(model)}:/models:ro",
                self.cfg["cpu_brain_image"],
                "-m", f"/models/{os.path.basename(model)}", "-t", str(threads),
                "-c", str(self.cfg["cpu_brain_ctx"]), "--host", "0.0.0.0", "--port", "8080"]

    def _docker(self, *args, timeout=60):
        try:
            return self._run([self.cfg["docker_bin"], *args], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise CpuBrainError(f"docker を実行できません: {e}")

    def healthy(self):
        try:
            with self.opener(urllib.request.Request(self.url + "/health"), timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def available(self):
        """使える状態か (設定が揃っているか)。起動はしない。"""
        if self.mode == "external":
            return True
        return self.mode == "docker" and self.model_for("code") is not None

    def ensure_running(self, role="code", wait_s=240):
        if self.mode == "external":
            return
        if not self.available():
            raise CpuBrainError("CPU の脳のモデル (cpu_brain_models の GGUF) がありません")
        self._docker("rm", "-f", self.NAME)
        self.paused = False
        r = self._docker(*self.run_command(role)[1:], timeout=300)
        if r.returncode != 0:
            raise CpuBrainError(f"CPU の脳を起動できません: {(r.stderr or r.stdout)[-400:]}")
        deadline = self.clock() + wait_s
        while self.clock() < deadline:
            if self.healthy():
                return
            time.sleep(2)
        raise CpuBrainError("CPU の脳が時間内に起動しなかった")

    def pause(self):
        """会話が始まった: CPU を即座に返す。"""
        if self.mode == "docker" and not self.paused:
            if self._docker("pause", self.NAME, timeout=20).returncode == 0:
                self.paused = True

    def resume(self):
        if self.mode == "docker" and self.paused:
            self._docker("unpause", self.NAME, timeout=20)
            self.paused = False

    def stop(self):
        if self.mode == "docker":
            self._docker("rm", "-f", self.NAME, timeout=60)

    # ------------------------------------------------------------ 推論
    def chat(self, system, user, schema=None, max_tokens=1500, temperature=0.2):
        body = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens, "temperature": temperature, "stream": False}
        if schema is not None:
            body["response_format"] = {"type": "json_object", "schema": schema}
        req = urllib.request.Request(self.url + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=self.cfg["cpu_brain_timeout_s"]) as r:
                data = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise CpuBrainError(f"CPU の脳に問い合わせできません: {e}")
        text = data["choices"][0]["message"]["content"]
        if schema is None:
            return text.strip()
        return parse_json(text)


def parse_json(text):
    """JSON だけを返す約束でも、前後に説明やコードフェンスが付くことがあるので取り出す。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                pass
    raise CpuBrainError(f"JSON を解釈できません: {text[:200]}")
