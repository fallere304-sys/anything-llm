import pytest

from buddy.config import ConfigError, load_dotenv, load_settings


def test_defaults_are_loopback_mock():
    s = load_settings(env={})
    assert s.host == "127.0.0.1" and s.llm_provider == "mock" and s.is_loopback


def test_env_overrides():
    s = load_settings(env={"BUDDY_PORT": "9000", "LLM_PROVIDER": "OpenAI_Compat", "LLM_MODEL": "m"})
    assert s.port == 9000 and s.llm_provider == "openai_compat" and s.llm_model == "m"


def test_non_loopback_requires_token():
    with pytest.raises(ConfigError):
        load_settings(env={"BUDDY_HOST": "0.0.0.0"})
    s = load_settings(env={"BUDDY_HOST": "0.0.0.0", "BUDDY_ACCESS_TOKEN": "abc123"})
    assert s.access_token == "abc123"


def test_bad_int():
    with pytest.raises(ConfigError):
        load_settings(env={"BUDDY_PORT": "x"})


def test_secrets_not_in_repr():
    s = load_settings(env={"LLM_API_KEY": "sk-supersecretvalue", "BUDDY_ACCESS_TOKEN": "tok-secret-1"})
    assert "supersecret" not in repr(s) and "tok-secret-1" not in repr(s)


def test_dotenv_parse(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# c\nA=1\nB="two words"\nC = \'x\'\nbad line\n', encoding="utf-8")
    assert load_dotenv(p) == {"A": "1", "B": "two words", "C": "x"}
    assert load_dotenv(tmp_path / "none") == {}
