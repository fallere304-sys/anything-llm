"""起動: python -m buddy"""
from __future__ import annotations

import sys

import uvicorn

from .api.app import create_app
from .config import ConfigError, load_settings
from .llm.base import LLMError
from .llm.registry import build_provider
from .logging_setup import setup_logging


def main() -> int:
    try:
        settings = load_settings()
        setup_logging(secrets=[settings.access_token, settings.llm_api_key])
        app = create_app(settings, build_provider(settings))
    except (ConfigError, LLMError) as exc:
        print(f"[設定エラー] {exc}", file=sys.stderr)
        return 2
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
