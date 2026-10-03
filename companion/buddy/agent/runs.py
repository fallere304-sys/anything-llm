"""司令塔(Claude Code)の実行単位。MCP 接続を実行ごとの使い捨てトークンで保護する。"""
from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from ..tools.registry import ToolContext


@dataclass
class OrchestratorRun:
    id: str
    token: str = field(repr=False)
    ctx: ToolContext  # 親(delegate_task)の文脈。emit は親の中継口
    records: list = field(default_factory=list)  # サブツールの作業記録
    calls: int = 0
    created: float = field(default_factory=time.time)


class RunRegistry:
    MAX_CALLS = 30  # 1回の依頼でサブツールを呼べる上限(暴走防止)

    def __init__(self) -> None:
        self._runs: dict[str, OrchestratorRun] = {}

    def create(self, ctx: ToolContext) -> OrchestratorRun:
        run = OrchestratorRun(id=secrets.token_hex(8), token=secrets.token_urlsafe(32), ctx=ctx)
        self._runs[run.id] = run
        return run

    def authenticate(self, run_id: str, token: str) -> Optional[OrchestratorRun]:
        run = self._runs.get(run_id)
        if run is None or not hmac.compare_digest(run.token.encode(), token.encode()):
            return None
        return run

    def close(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    def __len__(self) -> int:
        return len(self._runs)
