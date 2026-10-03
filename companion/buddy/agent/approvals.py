"""承認の仲介。ツール実行を一時停止し、ユーザーの承認/拒否を待つ。"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PendingApproval:
    id: str
    conversation_id: str
    tool: str
    level: int
    level_label: str
    purpose: str
    preview: str
    external: Optional[str]
    reason: str
    created_at: float = field(default_factory=time.time)
    future: asyncio.Future = field(default=None, repr=False)

    def public(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "future"}


class ApprovalBroker:
    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    def create(self, **info) -> PendingApproval:
        ap = PendingApproval(id=uuid.uuid4().hex[:12], **info)
        ap.future = asyncio.get_running_loop().create_future()
        self._pending[ap.id] = ap
        return ap

    async def wait(self, ap: PendingApproval, timeout: float) -> str:
        """'approved' / 'denied' / 'timeout'。キャンセル(切断)時は取り消して再送出。"""
        try:
            approved = await asyncio.wait_for(asyncio.shield(ap.future), timeout)
            return "approved" if approved else "denied"
        except asyncio.TimeoutError:
            return "timeout"
        finally:
            self._pending.pop(ap.id, None)
            if not ap.future.done():
                ap.future.cancel()

    def resolve(self, approval_id: str, approved: bool) -> bool:
        ap = self._pending.get(approval_id)
        if ap is None or ap.future.done():
            return False
        ap.future.set_result(bool(approved))
        return True

    def list(self) -> list[dict]:
        return [ap.public() for ap in self._pending.values()]
