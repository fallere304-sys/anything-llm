import pytest

from buddy.config import ConfigError, load_settings
from buddy.llm.registry import build_providers
from buddy.tts.registry import build_tts


def test_mock_has_both_profiles():
    p = build_providers(load_settings(env={}))
    assert p["fast"].model == "mock-echo" and p["strong"].model == "mock-strong"


def test_openai_requires_key():
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        build_providers(load_settings(env={"LLM_PROVIDER": "openai", "LLM_MODEL": "m"}))


def test_openai_requires_model():
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        build_providers(load_settings(env={"LLM_PROVIDER": "openai", "LLM_API_KEY": "k"}))


def test_openai_profiles_and_external_flag():
    p = build_providers(load_settings(env={
        "LLM_PROVIDER": "openai", "LLM_API_KEY": "k", "LLM_MODEL": "small-x", "LLM_MODEL_STRONG": "big-y"}))
    assert p["fast"].model == "small-x" and p["strong"].model == "big-y"
    assert p["fast"].name == "openai" and p["fast"].sends_data_externally
    assert p["fast"]._base_url == "https://api.openai.com/v1"


def test_no_strong_when_unset():
    p = build_providers(load_settings(env={"LLM_PROVIDER": "openai", "LLM_API_KEY": "k", "LLM_MODEL": "m"}))
    assert list(p) == ["fast"]


def test_unknown_provider():
    with pytest.raises(ConfigError):
        build_providers(load_settings(env={"LLM_PROVIDER": "nope"}))


def test_tts_registry():
    assert build_tts(load_settings(env={})) is None
    assert build_tts(load_settings(env={"TTS_PROVIDER": "mock"})).name == "mock"
    assert build_tts(load_settings(env={"TTS_PROVIDER": "voiceroid2"})).name == "voiceroid2"
    with pytest.raises(ConfigError):
        build_tts(load_settings(env={"TTS_PROVIDER": "x"}))
    with pytest.raises(ConfigError):
        load_settings(env={"TTS_SPEED": "fast"})
