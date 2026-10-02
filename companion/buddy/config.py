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
    )
    settings.validate()
    return settings
