"""TTSProvider 抽象。音声合成サービス/製品に依存しない境界。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioClip:
    data: bytes
    mime: str = "audio/wav"


class TTSError(Exception):
    """音声合成の失敗。握り潰さず利用者に分かる文で伝える。"""


class TTSProvider(ABC):
    name: str = "base"

    @property
    def voice(self) -> str:
        return ""

    @abstractmethod
    async def synthesize(self, text: str) -> AudioClip:
        """テキストを音声にする。失敗時は TTSError。"""

    async def health(self) -> tuple[bool, str]:
        return True, "ok"

    async def aclose(self) -> None:
        return None

    @property
    def sends_data_externally(self) -> bool:
        return False
