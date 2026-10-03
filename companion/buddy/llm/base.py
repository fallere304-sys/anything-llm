"""LLMProvider 抽象。特定ベンダー・モデルに依存しない境界。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional

ROLES = ("system", "user", "assistant", "tool")


@dataclass(frozen=True)
class ToolCallRequest:
    """LLM が要求したツール呼び出し。arguments は JSON 文字列(未検証)。"""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str
    tool_calls: tuple[ToolCallRequest, ...] = ()  # role=assistant のみ
    tool_call_id: Optional[str] = None  # role=tool のみ

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role: {self.role!r}")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool message requires tool_call_id")


class LLMError(Exception):
    """プロバイダ呼び出しの失敗。握り潰さず、利用者に分かる文で上位へ伝える。"""


class LLMProvider(ABC):
    """会話生成プロバイダ。stream() だけ実装すればよい。"""

    name: str = "base"

    @property
    @abstractmethod
    def model(self) -> str:
        """現在使用するモデル名(表示用)。"""

    supports_tools: bool = False

    @abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict]] = None,
    ) -> AsyncIterator["str | ToolCallRequest"]:
        """応答テキストの断片(str)を順次返し、最後にツール呼び出し要求があれば ToolCallRequest を返す。

        tools は OpenAI 形式の関数定義。対応しないプロバイダは無視してよい。失敗時は LLMError。
        """

    async def complete(self, messages: list[ChatMessage], **kwargs) -> str:
        return "".join([c async for c in self.stream(messages, **kwargs) if isinstance(c, str)])

    async def health(self) -> tuple[bool, str]:
        """疎通確認。(ok, 説明)。既定は常にOK。"""
        return True, "ok"

    async def aclose(self) -> None:
        return None

    @property
    def sends_data_externally(self) -> bool:
        """会話内容が端末外に送信されるか(UIでの明示用)。"""
        return False
