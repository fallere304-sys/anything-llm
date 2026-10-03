"""設定の読み込み。秘密情報は環境変数 / .env からのみ受け取る。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(Exception):
    """設定が不正で起動できない。"""


def load_dotenv(path: Path) -> dict[str, str]:
    """最小限の .env パーサ(KEY=VALUE, # コメント, 前後の引用符)。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} は整数で指定してください: {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    access_token: str = field(default="", repr=False)
    data_dir: Path = Path("./data")
    system_prompt_file: Path = Path("./prompts/system.md")
    max_context_chars: int = 12000
    max_memory_chars: int = 3000  # 記憶として文脈に入れる最大文字数
    llm_provider: str = "mock"
    llm_base_url: str = ""  # 空ならプロバイダ既定(registry で解決)
    llm_model: str = ""  # profile "fast"
    llm_model_strong: str = ""  # profile "strong"(任意)
    llm_api_key: str = field(default="", repr=False)  # repr から除外(ログ漏洩防止)
    llm_timeout: float = 120.0
    tts_provider: str = "none"  # none | mock | voiceroid2
    tts_voice_name: str = ""
    tts_language: str = "standard"
    tts_speed: float = 1.0
    tts_pitch: float = 1.0
    tts_volume: float = 1.0
    # --- エージェント / ツール ---
    workspace_dir: Path = Path("./workspace")
    agent_max_steps: int = 6
    agent_tool_timeout: float = 600.0
    agent_approval_timeout: float = 900.0
    tool_auto_max_level: int = 1  # これ以下のレベルは自動許可(外部送信と Lv4 は除く)
    tool_auto_approve: tuple[str, ...] = ()  # 外部送信でも自動許可するツール名(Lv4 は不可)
    # --- Claude(成果物) ---
    anthropic_api_key: str = field(default="", repr=False)
    claude_model: str = ""
    claude_effort: str = "high"
    claude_max_tokens: int = 32000
    claude_refusal_fallback: str = "default"  # default | off
    claude_base_url: str = "https://api.anthropic.com"
    # --- Perplexity(調査) ---
    perplexity_api_key: str = field(default="", repr=False)
    perplexity_model: str = ""
    perplexity_base_url: str = "https://api.perplexity.ai"
    # --- Gemini(画像) ---
    gemini_api_key: str = field(default="", repr=False)
    gemini_image_model: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "buddy.sqlite3"

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    def validate(self) -> None:
        if not self.is_loopback and not self.access_token:
            raise ConfigError(
                f"BUDDY_HOST={self.host} はLANに公開されます。"
                "BUDDY_ACCESS_TOKEN を設定してください(未設定のままの公開は拒否します)。"
            )
        if self.max_context_chars < 500:
            raise ConfigError("BUDDY_MAX_CONTEXT_CHARS は 500 以上にしてください。")
        if not 0 <= self.tool_auto_max_level <= 3:
            raise ConfigError("TOOL_AUTO_APPROVE_MAX_LEVEL は 0〜3 です(Lv4 は常に承認が必要)。")
        if not 1 <= self.agent_max_steps <= 20:
            raise ConfigError("AGENT_MAX_STEPS は 1〜20 です。")
        if self.claude_refusal_fallback not in ("default", "off"):
            raise ConfigError("CLAUDE_REFUSAL_FALLBACK は default / off です。")
        if not 0 <= self.max_memory_chars < self.max_context_chars:
            raise ConfigError("BUDDY_MAX_MEMORY_CHARS は 0 以上、BUDDY_MAX_CONTEXT_CHARS 未満にしてください。")

    def load_system_prompt(self) -> str:
        if not self.system_prompt_file.is_file():
            raise ConfigError(f"システムプロンプトが見つかりません: {self.system_prompt_file}")
        return self.system_prompt_file.read_text(encoding="utf-8").strip()


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} は数値で指定してください: {raw!r}") from exc


def load_settings(
    env: Optional[Mapping[str, str]] = None, dotenv_path: Optional[Path] = Path(".env")
) -> Settings:
    """優先順位: 実環境変数 > .env > 既定値。env を渡した場合は .env を読まない。"""
    if env is None:
        merged: dict[str, str] = {}
        if dotenv_path is not None:
            merged.update(load_dotenv(dotenv_path))
        merged.update(os.environ)
        env = merged
    d = Settings()
    settings = Settings(
        host=env.get("BUDDY_HOST", "").strip() or d.host,
        port=_int(env, "BUDDY_PORT", d.port),
        access_token=env.get("BUDDY_ACCESS_TOKEN", "").strip(),
        data_dir=Path(env.get("BUDDY_DATA_DIR", "").strip() or d.data_dir),
        system_prompt_file=Path(
            env.get("BUDDY_SYSTEM_PROMPT_FILE", "").strip() or d.system_prompt_file
        ),
        max_context_chars=_int(env, "BUDDY_MAX_CONTEXT_CHARS", d.max_context_chars),
        max_memory_chars=_int(env, "BUDDY_MAX_MEMORY_CHARS", d.max_memory_chars),
        llm_provider=(env.get("LLM_PROVIDER", "").strip() or d.llm_provider).lower(),
        llm_base_url=env.get("LLM_BASE_URL", "").strip(),
        llm_model=env.get("LLM_MODEL", "").strip(),
        llm_model_strong=env.get("LLM_MODEL_STRONG", "").strip(),
        llm_api_key=env.get("LLM_API_KEY", "").strip(),
        llm_timeout=float(_int(env, "LLM_TIMEOUT_SECONDS", int(d.llm_timeout))),
        tts_provider=(env.get("TTS_PROVIDER", "").strip() or d.tts_provider).lower(),
        tts_voice_name=env.get("TTS_VOICE_NAME", "").strip(),
        tts_language=env.get("TTS_LANGUAGE", "").strip() or d.tts_language,
        tts_speed=_float(env, "TTS_SPEED", d.tts_speed),
        tts_pitch=_float(env, "TTS_PITCH", d.tts_pitch),
        tts_volume=_float(env, "TTS_VOLUME", d.tts_volume),
        workspace_dir=Path(env.get("BUDDY_WORKSPACE_DIR", "").strip() or d.workspace_dir),
        agent_max_steps=_int(env, "AGENT_MAX_STEPS", d.agent_max_steps),
        agent_tool_timeout=_float(env, "AGENT_TOOL_TIMEOUT_SECONDS", d.agent_tool_timeout),
        agent_approval_timeout=_float(env, "AGENT_APPROVAL_TIMEOUT_SECONDS", d.agent_approval_timeout),
        tool_auto_max_level=_int(env, "TOOL_AUTO_APPROVE_MAX_LEVEL", d.tool_auto_max_level),
        tool_auto_approve=tuple(t.strip() for t in env.get("TOOL_AUTO_APPROVE", "").split(",") if t.strip()),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip(),
        claude_model=env.get("CLAUDE_MODEL", "").strip(),
        claude_effort=env.get("CLAUDE_EFFORT", d.claude_effort).strip(),
        claude_max_tokens=_int(env, "CLAUDE_MAX_TOKENS", d.claude_max_tokens),
        claude_refusal_fallback=(env.get("CLAUDE_REFUSAL_FALLBACK", "").strip() or d.claude_refusal_fallback).lower(),
        claude_base_url=env.get("CLAUDE_BASE_URL", "").strip() or d.claude_base_url,
        perplexity_api_key=env.get("PERPLEXITY_API_KEY", "").strip(),
        perplexity_model=env.get("PERPLEXITY_MODEL", "").strip(),
        perplexity_base_url=env.get("PERPLEXITY_BASE_URL", "").strip() or d.perplexity_base_url,
        gemini_api_key=env.get("GEMINI_API_KEY", "").strip(),
        gemini_image_model=env.get("GEMINI_IMAGE_MODEL", "").strip(),
        gemini_base_url=env.get("GEMINI_BASE_URL", "").strip() or d.gemini_base_url,
    )
    settings.validate()
    return settings
