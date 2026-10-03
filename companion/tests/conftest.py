import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from buddy.api.app import create_app
from buddy.config import Settings
from buddy.llm.mock import MockProvider
from buddy.storage.db import Database

ROOT = Path(__file__).resolve().parent.parent


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(
        data_dir=tmp_path, system_prompt_file=ROOT / "prompts" / "system.md", llm_provider="mock",
        workspace_dir=tmp_path / "workspace", orchestrator="none",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def client(tmp_path):
    app = create_app(make_settings(tmp_path), MockProvider(), Database(":memory:"))
    with TestClient(app) as c:
        yield c


def parse_sse(text: str) -> list[dict]:
    return [json.loads(l[5:]) for l in text.split("\n\n") if l.startswith("data:")]
