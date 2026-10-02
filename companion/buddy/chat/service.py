"""チャットのユースケース層。保存・文脈構築・LLM呼び出し・状態イベントを束ねる。"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from ..llm.base import LLMError, LLMProvider
from ..storage.db import Database
from .context import build_context

log = logging.getLogger("buddy.chat")

MAX_USER_CHARS = 8000
TITLE_CHARS = 30


class Busy(Exception):
    """同じ会話で応答生成中に新しい送信があった。"""


class ChatService:
    def __init__(
        self, db: Database, provider: LLMProvider, system_prompt: str, max_context_chars: int
    ) -> None:
        self.db = db
        self.provider = provider
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
            "active_conversations": sorted(self._active),
        }

    async def reply_stream(self, cid: str, user_text: str) -> AsyncIterator[dict]:
        """ユーザー発言を保存し、応答をイベント列として返す。

        イベント: status / delta / done / error。内部の思考は出さない。
        """
        self.db.get_conversation(cid)  # NotFound はここで上位へ
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
            yield {"type": "status", "state": "thinking", "dropped_history": dropped}
            parts: list[str] = []
            try:
                async for chunk in self.provider.stream(messages):
                    if not parts:
                        self.state = "responding"
                        yield {"type": "status", "state": "responding"}
                    parts.append(chunk)
                    yield {"type": "delta", "text": chunk}
            except LLMError as exc:
                self.state, self.last_error = "error", str(exc)
                log.error("LLM error in conversation %s: %s", cid, exc)
                yield {"type": "error", "message": str(exc)}
                return
            text = "".join(parts)
            if not text.strip():
                self.state, self.last_error = "error", "LLM が空の応答を返しました。"
                log.error("empty LLM response in conversation %s", cid)
                yield {"type": "error", "message": self.last_error}
                return
            saved = self.db.add_message(
                cid, "assistant", text, provider=self.provider.name, model=self.provider.model
            )
            self.state = "idle"
            yield {"type": "status", "state": "idle"}
            yield {"type": "done", "message": saved}
        finally:
            # クライアント切断(キャンセル)でも状態を確実に戻す。
            self._active.discard(cid)
            if self.state in ("thinking", "responding"):
                self.state = "idle"
