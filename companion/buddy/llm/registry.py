"""設定から LLMProvider を生成するファクトリ。新プロバイダはここに登録する。"""
from __future__ import annotations

from typing import Callable

from ..config import ConfigError, Settings
from .base import LLMProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider

_FACTORIES: dict[str, Callable[[Settings], LLMProvider]] = {
    "mock": lambda s: MockProvider(),
    "openai_compat": lambda s: OpenAICompatProvider(
        base_url=s.llm_base_url, model=s.llm_model, api_key=s.llm_api_key, timeout=s.llm_timeout
    ),
}


def build_provider(settings: Settings) -> LLMProvider:
    try:
        factory = _FACTORIES[settings.llm_provider]
    except KeyError:
        raise ConfigError(
            f"未対応の LLM_PROVIDER: {settings.llm_provider!r} (対応: {', '.join(sorted(_FACTORIES))})"
        ) from None
    return factory(settings)
