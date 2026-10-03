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


def test_env_example_is_valid_b_plan(tmp_path):
    """同梱の .env.example が読めて、キーとモデルを埋めれば B案で起動できること。"""
    from pathlib import Path
    from buddy.config import load_dotenv
    from buddy.llm.registry import build_providers
    from buddy.memory.service import MemoryService
    from buddy.memory.store import MemoryStore
    from buddy.storage.db import Database
    from buddy.tools.builtin import build_registry
    example = load_dotenv(Path(__file__).resolve().parent.parent / ".env.example")
    assert example["LLM_PROVIDER"] == "gemini" and example["ORCHESTRATOR"] == "claude_code"
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):  # キー未設定なら分かるエラーで止まる
        build_providers(load_settings(env=example))
    filled = {**example, "GEMINI_API_KEY": "g", "LLM_MODEL": "gf", "GEMINI_TEXT_MODEL": "gt",
              "GEMINI_IMAGE_MODEL": "gi", "BUDDY_WORKSPACE_DIR": str(tmp_path / "ws")}
    s = load_settings(env=filled)
    assert build_providers(s)["fast"].name == "gemini"
    names = build_registry(s, MemoryService(MemoryStore(Database(":memory:")))).names()
    assert {"create_document", "research_web", "generate_image"} <= set(names)
