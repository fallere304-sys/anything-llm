"""LLM不要の動作確認用プロバイダ。決定的な応答を返す。"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from .base import ChatMessage, LLMProvider


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, delay: float = 0.0, model: str = "mock-echo") -> None:
        self._delay = delay
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict]] = None,
    ) -> AsyncIterator[str]:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        history_turns = sum(1 for m in messages if m.role == "user")
        text = f"[mock] 「{last_user}」を受け取りました。(この会話のユーザー発言: {history_turns}件)"
        for i in range(0, len(text), 6):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield text[i : i + 6]
