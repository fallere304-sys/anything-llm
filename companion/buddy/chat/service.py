"""チャットのユースケース層。保存・文脈構築・LLM呼び出し・状態イベントを束ねる。"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from ..activity import activity

from ..llm.base import LLMError, LLMProvider
from ..storage.db import Database
from .context import build_context

log = logging.getLogger("buddy.chat")

MAX_USER_CHARS = 8000
TITLE_CHARS = 30


class Busy(Exception):
    """同じ会話で応答生成中に新しい送信があった。"""


class UnknownProfile(Exception):
    """設定されていないモデルプロファイルが指定された。"""


class ChatService:
    def __init__(
        self,
        db: Database,
        providers: dict[str, LLMProvider],
        system_prompt: str,
        max_context_chars: int,
    ) -> None:
        self.db = db
        self.providers = providers
        self.provider = providers["fast"]  # 既定(状態表示用)
        self.system_prompt = system_prompt
        self.max_context_chars = max_context_chars
        self.state = "idle"  # idle | thinking | responding | error
        self.last_error = ""
        self._active: set[str] = set()

    def status(self) -> dict:
        return {
            "state": self.state,
            "last_error": self.last_error,
            "provider": self.provider.name,
            "model": self.provider.model,
            "external": self.provider.sends_data_externally,
            "profiles": {
                name: {"provider": p.name, "model": p.model, "external": p.sends_data_externally}
                for name, p in self.providers.items()
            },
            "active_conversations": sorted(self._active),
        }

    async def reply_stream(
        self, cid: str, user_text: str, profile: str = "fast"
    ) -> AsyncIterator[dict]:
        """ユーザー発言を保存し、応答をイベント列として返す。

        イベント: status / activity / delta / done / error。
        activity は「何に実際にアクセスしたか」の要約のみ。内部の思考は出さない。
        """
        self.db.get_conversation(cid)  # NotFound はここで上位へ
        if profile not in self.providers:
            raise UnknownProfile(profile)
        provider = self.providers[profile]
        if cid in self._active:
            raise Busy(cid)
        self._active.add(cid)
        try:
            history = self.db.list_messages(cid)
            self.db.add_message(cid, "user", user_text)
            if not history:
                self.db.rename_conversation(cid, user_text.strip().replace("\n", " ")[:TITLE_CHARS])
            history.append({"role": "user", "content": user_text})
            messages, dropped = build_context(self.system_prompt, history, self.max_context_chars)

            self.state, self.last_error = "thinking", ""
            yield {"type": "status", "state": "thinking", "dropped_history": dropped,
                   "profile": profile, "model": provider.model}
            for ev in self._context_activities(messages, dropped):
                yield ev
            t0 = time.monotonic()
            llm_ev = activity(
                f"llm:{profile}", "start", f"{provider.model} に問い合わせ",
                external=provider.sends_data_externally,
            )
            llm_id = llm_ev["id"]
            log.info("activity llm:%s start model=%s", profile, provider.model)
            yield llm_ev
            parts: list[str] = []
            try:
                async for chunk in provider.stream(messages):
                    if not parts:
                        self.state = "responding"
                        yield {"type": "status", "state": "responding"}
                    parts.append(chunk)
                    yield {"type": "delta", "text": chunk}
            except LLMError as exc:
                self.state, self.last_error = "error", str(exc)
                log.error("LLM error in conversation %s: %s", cid, exc)
                yield activity(f"llm:{profile}", "end", "失敗", llm_id, ok=False)
                yield {"type": "error", "message": str(exc)}
                return
            text = "".join(parts)
            elapsed = time.monotonic() - t0
            if not text.strip():
                self.state, self.last_error = "error", "LLM が空の応答を返しました。"
                log.error("empty LLM response in conversation %s", cid)
                yield activity(f"llm:{profile}", "end", "空の応答", llm_id, ok=False)
                yield {"type": "error", "message": self.last_error}
                return
            log.info("activity llm:%s end chars=%d sec=%.1f", profile, len(text), elapsed)
            yield activity(
                f"llm:{profile}", "end", f"応答受信 {len(text)}文字 / {elapsed:.1f}秒", llm_id, ok=True
            )
            saved = self.db.add_message(
                cid, "assistant", text, provider=provider.name, model=provider.model
            )
            self.state = "idle"
            yield {"type": "status", "state": "idle"}
            yield {"type": "done", "message": saved}
        finally:
            # クライアント切断(キャンセル)でも状態を確実に戻す。
            self._active.discard(cid)
            if self.state in ("thinking", "responding"):
                self.state = "idle"

    @staticmethod
    def _context_activities(messages: list, dropped: int) -> list[dict]:
        has_system = bool(messages) and messages[0].role == "system"
        kept = len(messages) - (1 if has_system else 0)
        events = []
        if has_system:
            events.append(activity("persona", "pulse", "人格・行動規則を適用"))
        note = f"(古い{dropped}件は省略)" if dropped else ""
        events.append(activity("history", "pulse", f"会話履歴 {kept}件を参照{note}"))
        return events
