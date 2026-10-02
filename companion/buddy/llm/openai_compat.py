"""OpenAI互換 Chat Completions プロバイダ(Ollama / LM Studio / OpenAI など)。"""
from __future__ import annotations

import json
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

import httpx

from .base import ChatMessage, LLMError, LLMProvider, ToolCallRequest


def _wire(m: ChatMessage) -> dict:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    out: dict = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["content"] = m.content or None
        out["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in m.tool_calls
        ]
    return out


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"
    supports_tools = True

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        client: Optional[httpx.AsyncClient] = None,
        name: Optional[str] = None,
        force_external: bool = False,
    ) -> None:
        if name:
            self.name = name
        self._force_external = force_external
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
        if self._force_external:
            return True
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
        tools: Optional[list[dict]] = None,
    ) -> AsyncIterator["str | ToolCallRequest"]:
        payload: dict = {
            "model": self._model,
            "messages": [_wire(m) for m in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        calls: dict[int, dict] = {}  # index -> {id, name, arguments}
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
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta") or {}
                        text = delta.get("content")
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "arguments": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["arguments"] += fn.get("arguments") or ""
                    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                        raise LLMError(f"LLM の応答を解釈できません: {data[:200]}") from exc
                    if text:
                        yield text
        except httpx.ConnectError as exc:
            raise LLMError(
                f"LLM に接続できません ({self._base_url})。Ollama 等が起動しているか確認してください。"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("LLM の応答がタイムアウトしました。") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM との通信に失敗しました: {type(exc).__name__}") from exc
        for i in sorted(calls):
            c = calls[i]
            if not c["name"]:
                raise LLMError("LLM のツール呼び出しに関数名がありません。")
            yield ToolCallRequest(id=c["id"] or f"call_{i}", name=c["name"], arguments=c["arguments"] or "{}")

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
