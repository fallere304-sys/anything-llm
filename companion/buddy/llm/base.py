"""LLMProvider 抽象。特定ベンダー・モデルに依存しない境界。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional

ROLES = ("system", "user", "assistant")


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role: {self.role!r}")


class LLMError(Exception):
    """プロバイダ呼び出しの失敗。握り潰さず、利用者に分かる文で上位へ伝える。"""


class LLMProvider(ABC):
    """会話生成プロバイダ。stream() だけ実装すればよい。"""

    name: str = "base"

    @property
    @abstractmethod
    def model(self) -> str:
        """現在使用するモデル名(表示用)。"""

    @abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """応答テキストの断片を順次返す。失敗時は LLMError。"""

    async def complete(self, messages: list[ChatMessage], **kwargs) -> str:
        return "".join([chunk async for chunk in self.stream(messages, **kwargs)])

    async def health(self) -> tuple[bool, str]:
        """疎通確認。(ok, 説明)。既定は常にOK。"""
        return True, "ok"

    async def aclose(self) -> None:
        return None

    @property
    def sends_data_externally(self) -> bool:
        """会話内容が端末外に送信されるか(UIでの明示用)。"""
        return False
