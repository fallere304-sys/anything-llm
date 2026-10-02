"""OpenAI互換 Chat Completions プロバイダ(Ollama / LM Studio / OpenAI など)。"""
from __future__ import annotations

import json
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

import httpx

from .base import ChatMessage, LLMError, LLMProvider


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not model:
            raise LLMError("LLM_MODEL が未設定です(.env を確認してください)。")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))

    @property
    def model(self) -> str:
        return self._model

    @property
    def sends_data_externally(self) -> bool:
        host = urlparse(self._base_url).hostname or ""
        return host not in {"localhost", "127.0.0.1", "::1"}

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        payload: dict = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        try:
            async with self._client.stream(
                "POST", f"{self._base_url}/chat/completions", json=payload, headers=self._headers()
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise LLMError(f"LLM がエラーを返しました (HTTP {resp.status_code}): {body}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0].get("delta", {}).get("content")
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise LLMError(f"LLM の応答を解釈できません: {data[:200]}") from exc
                    if delta:
                        yield delta
        except httpx.ConnectError as exc:
            raise LLMError(
                f"LLM に接続できません ({self._base_url})。Ollama 等が起動しているか確認してください。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("LLM の応答がタイムアウトしました。") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM との通信に失敗しました: {type(exc).__name__}") from exc

    async def health(self) -> tuple[bool, str]:
        try:
            resp = await self._client.get(f"{self._base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            return False, f"接続できません: {type(exc).__name__}"
        if resp.status_code == 200:
            return True, "ok"
        return False, f"HTTP {resp.status_code}"

    async def aclose(self) -> None:
        await self._client.aclose()
