"""Ollama への薄いクライアントと、GPU を 1 本の資源として配分するゲート。

Ollama / LM Studio は「呼ばれたら答える」サーバーでしかない。
ここではそれを単なる推論関数として扱い、いつ・何のために呼ぶかは
上位 (agent.py) が決める。
"""

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque


class LLMError(RuntimeError):
    pass


class GpuGate:
    """GPU は 1 枚・同時 1 推論。ユーザー対話は常に優先し、背景思考は
    直近 window 秒のうち duty_cycle 以下しか GPU を使わない。"""

    def __init__(self, duty_cycle=0.5, window=60.0, clock=time.monotonic):
        self.duty_cycle = duty_cycle
        self.window = window
        self.clock = clock
        self._lock = threading.Lock()
        self._spans = deque()  # (start, end)
        self.busy = False

    def _busy_seconds(self, now):
        while self._spans and self._spans[0][1] < now - self.window:
            self._spans.popleft()
        return sum(min(e, now) - max(s, now - self.window) for s, e in self._spans)

    def can_run_background(self):
        now = self.clock()
        return not self.busy and self._busy_seconds(now) < self.duty_cycle * self.window

    def run(self, fn):
        with self._lock:
            self.busy = True
            start = self.clock()
            try:
                return fn()
            finally:
                self._spans.append((start, self.clock()))
                self.busy = False


class OllamaClient:
    def __init__(self, cfg, gate=None):
        self.base_url = cfg["ollama_url"].rstrip("/")
        self.url = self.base_url + "/api/chat"
        self.model = cfg["model"]
        self.num_ctx = cfg["num_ctx"]
        self.timeout = cfg["request_timeout"]
        self.gate = gate or GpuGate(cfg["gpu_duty_cycle"])
        self._think_supported = True

    def _post(self, body, url=None):
        req = urllib.request.Request(
            url or self.url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise LLMError(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"Ollama に接続できません ({self.url}): {e}") from e

    def unload(self, model=None):
        """VRAM を空ける (学習の前に呼ぶ)。"""
        self._post({"model": model or self.model, "keep_alive": 0}, self.base_url + "/api/generate")

    def chat(self, system, user, schema=None, think=False, max_tokens=512, temperature=0.3,
             model=None):
        """schema を渡すと Ollama の structured output で JSON を強制し、dict を返す。"""
        body = {
            "model": model or self.model,
            "stream": False,
            # -1: モデルを VRAM に常駐させる (毎回のロードで数秒失うのを防ぐ)
            "keep_alive": -1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {
                "num_ctx": self.num_ctx,
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        if schema is not None:
            body["format"] = schema
        if think and self._think_supported:
            body["think"] = True

        def call():
            try:
                return self._post(body)
            except LLMError as e:
                if "think" in body and "think" in str(e).lower():
                    self._think_supported = False
                    body.pop("think")
                    return self._post(body)
                raise

        data = self.gate.run(call)
        content = (data.get("message") or {}).get("content", "")
        if schema is None:
            return content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"JSON を解釈できません: {content[:200]}") from e
