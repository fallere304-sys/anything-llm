from __future__ import annotations

from typing import Optional

from ..config import ConfigError, Settings
from .base import TTSProvider
from .mock import MockTTSProvider
from .voiceroid2 import Voiceroid2Provider


def build_tts(settings: Settings) -> Optional[TTSProvider]:
    kind = settings.tts_provider
    if kind in ("", "none"):
        return None
    if kind == "mock":
        return MockTTSProvider()
    if kind == "voiceroid2":
        return Voiceroid2Provider(
            voice_name=settings.tts_voice_name,
            language=settings.tts_language,
            speed=settings.tts_speed,
            pitch=settings.tts_pitch,
            volume=settings.tts_volume,
        )
    raise ConfigError(f"未対応の TTS_PROVIDER: {kind!r} (対応: none, mock, voiceroid2)")
