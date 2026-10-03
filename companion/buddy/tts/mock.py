"""動作確認用: 文字数に比例した長さの単純なビープ WAV を返す(標準ライブラリのみ)。"""
from __future__ import annotations

import io
import math
import struct
import wave

from .base import AudioClip, TTSProvider


class MockTTSProvider(TTSProvider):
    name = "mock"

    @property
    def voice(self) -> str:
        return "mock-beep"

    async def synthesize(self, text: str) -> AudioClip:
        rate = 16000
        seconds = min(0.05 * max(len(text), 1), 5.0)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(rate * seconds))
        )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(frames)
        return AudioClip(buf.getvalue(), "audio/wav")
