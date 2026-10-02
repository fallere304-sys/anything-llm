"""VOICEROID2(琴葉茜・葵 等)アダプタ。Windows + pyvcroid2 が必要。

!! 実機未検証 !! 開発環境(Linux)には VOICEROID2 が無く、pyvcroid2 の API は
ライブラリの公開仕様に基づく想定で書いている。Windows 実機で TTS_PROVIDER=voiceroid2 を
試し、`python -m buddy.tts.voiceroid2` で声の一覧を確認すること。

DLL はスレッド親和性が不明なため、専用の単一スレッドで初期化・実行する。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from .base import AudioClip, TTSError, TTSProvider


def _import_pyvcroid2() -> Any:
    try:
        import pyvcroid2  # type: ignore
    except ImportError as exc:
        raise TTSError(
            "pyvcroid2 が見つかりません。Windows で `pip install pyvcroid2` を実行し、"
            "VOICEROID2 がインストールされていることを確認してください。"
        ) from exc
    return pyvcroid2


class Voiceroid2Provider(TTSProvider):
    name = "voiceroid2"

    def __init__(
        self,
        voice_name: str = "",
        language: str = "standard",
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        loader: Callable[[], Any] = _import_pyvcroid2,
    ) -> None:
        self._voice_name, self._language = voice_name, language
        self._params = {"speed": speed, "pitch": pitch, "volume": volume}
        self._loader = loader
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voiceroid2")
        self._vc: Any = None

    @property
    def voice(self) -> str:
        return self._voice_name or "(未設定)"

    # --- 以下は専用スレッド内でのみ呼ぶ ---
    def _ensure(self) -> Any:
        if self._vc is not None:
            return self._vc
        mod = self._loader()
        try:
            vc = mod.VcRoid2()
            languages = vc.listLanguages()
            if self._language not in languages:
                raise TTSError(f"言語 {self._language!r} がありません。利用可能: {languages}")
            vc.loadLanguage(self._language)
            voices = vc.listVoices()
            if not self._voice_name:
                raise TTSError(f"TTS_VOICE_NAME が未設定です。利用可能な声: {voices}")
            if self._voice_name not in voices:
                raise TTSError(f"声 {self._voice_name!r} がありません。利用可能な声: {voices}")
            vc.loadVoice(self._voice_name)
            for key, value in self._params.items():
                setattr(vc.param, key, value)
        except TTSError:
            raise
        except Exception as exc:  # DLL/ライセンス/初期化の失敗は種類が多い
            raise TTSError(f"VOICEROID2 の初期化に失敗しました: {type(exc).__name__}: {exc}") from exc
        self._vc = vc
        return vc

    def _synth(self, text: str) -> bytes:
        vc = self._ensure()
        try:
            speech, _events = vc.textToSpeech(text)
        except Exception as exc:
            raise TTSError(f"VOICEROID2 の音声生成に失敗しました: {type(exc).__name__}: {exc}") from exc
        return bytes(speech)

    async def synthesize(self, text: str) -> AudioClip:
        loop = asyncio.get_running_loop()
        return AudioClip(await loop.run_in_executor(self._exec, self._synth, text), "audio/wav")

    async def health(self) -> tuple[bool, str]:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._exec, self._ensure)
        except TTSError as exc:
            return False, str(exc)
        return True, "ok"

    async def aclose(self) -> None:
        self._exec.shutdown(wait=False)


if __name__ == "__main__":  # 声の一覧表示(実機確認用)
    vc = _import_pyvcroid2().VcRoid2()
    print("languages:", vc.listLanguages())
    vc.loadLanguage("standard")
    print("voices:", vc.listVoices())
