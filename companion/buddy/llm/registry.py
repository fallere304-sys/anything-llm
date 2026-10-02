"""設定から LLMProvider を生成するファクトリ。新プロバイダはここに登録する。

profile: "fast"(普段使い)と "strong"(深く考える)。モデル名はすべて設定から来る。
"""
from __future__ import annotations

from typing import Callable

from ..config import ConfigError, Settings
from .base import LLMError, LLMProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider

PROFILES = ("fast", "strong")
OPENAI_BASE_URL = "https://api.openai.com/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"


def _mock(s: Settings, model: str) -> LLMProvider:
    return MockProvider(model=model or "mock-echo")


def _openai_compat(s: Settings, model: str) -> LLMProvider:
    return OpenAICompatProvider(
        base_url=s.llm_base_url or OLLAMA_BASE_URL, model=model, api_key=s.llm_api_key, timeout=s.llm_timeout
    )


def _openai(s: Settings, model: str) -> LLMProvider:
    if not s.llm_api_key:
        raise ConfigError("LLM_PROVIDER=openai には LLM_API_KEY(OpenAI APIキー)が必要です。")
    return OpenAICompatProvider(
        base_url=s.llm_base_url or OPENAI_BASE_URL,
        model=model,
        api_key=s.llm_api_key,
        timeout=s.llm_timeout,
        name="openai",
        force_external=True,
    )


_FACTORIES: dict[str, Callable[[Settings, str], LLMProvider]] = {
    "mock": _mock,
    "openai_compat": _openai_compat,
    "openai": _openai,
}


def build_providers(settings: Settings) -> dict[str, LLMProvider]:
    """利用可能な profile -> provider。strong 未設定なら fast のみ。"""
    try:
        factory = _FACTORIES[settings.llm_provider]
    except KeyError:
        raise ConfigError(
            f"未対応の LLM_PROVIDER: {settings.llm_provider!r} (対応: {', '.join(sorted(_FACTORIES))})"
        ) from None
    try:
        providers = {"fast": factory(settings, settings.llm_model)}
        if settings.llm_model_strong:
            providers["strong"] = factory(settings, settings.llm_model_strong)
        elif settings.llm_provider == "mock":
            providers["strong"] = factory(settings, "mock-strong")
    except LLMError as exc:
        raise ConfigError(str(exc)) from exc
    return providers
