"""チャットのユースケース層。保存・文脈構築・LLM呼び出し・状態イベントを束ねる。"""
from __future__ import annotations

import logging
import time
from typing import AsyncIterator, Optional

from ..activity import activity
from ..llm.base import LLMError, LLMProvider
from ..memory.service import MemoryService, Retrieval
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
        memory: Optional[MemoryService] = None,
    ) -> None:
        self.db = db
        self.memory = memory
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
        conv = self.db.get_conversation(cid)  # NotFound はここで上位へ
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

            self.state, self.last_error = "thinking", ""
            system = self.system_prompt
            recalled: Optional[Retrieval] = None
            if self.memory:
                recalled = self.memory.retrieve(user_text, conv.get("project_id"))
                if recalled.items:
                    system += "\n\n" + self.memory.format_for_prompt(recalled.items, recalled.project_name)
            messages, dropped = build_context(system, history, self.max_context_chars)
            yield {"type": "status", "state": "thinking", "dropped_history": dropped,
                   "profile": profile, "model": provider.model}
            for ev in self._context_activities(messages, dropped, recalled):
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
                self._log("llm_call", f"llm:{profile}", f"{provider.model}: 失敗", False, cid)
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
            summary = f"応答受信 {len(text)}文字 / {elapsed:.1f}秒"
            yield activity(f"llm:{profile}", "end", summary, llm_id, ok=True)
            self._log("llm_call", f"llm:{profile}", f"{provider.model}: {summary}", True, cid)
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

    def _log(self, action: str, target: str, summary: str, ok: bool, cid: str) -> None:
        if self.memory:
            self.memory.store.add_log("ai", action, target, summary, ok, cid)

    @staticmethod
    def _context_activities(
        messages: list, dropped: int, recalled: Optional[Retrieval] = None
    ) -> list[dict]:
        has_system = bool(messages) and messages[0].role == "system"
        kept = len(messages) - (1 if has_system else 0)
        events = []
        if has_system and messages[0].content:
            events.append(activity("persona", "pulse", "人格・行動規則を適用"))
        if recalled is not None:
            lt, imp = recalled.count("long_term"), recalled.important
            note = f"(うち重要 {imp}件)" if imp else ""
            text = f"長期記憶 {lt}件を参照{note}" if lt else "長期記憶を検索: 該当なし"
            if recalled.dropped:
                text += f" / 予算超過で{recalled.dropped}件省略"
            events.append(activity("memory", "pulse", text))
            if recalled.project_name:
                pj = recalled.count("project")
                events.append(activity(
                    "project", "pulse",
                    f"プロジェクト「{recalled.project_name}」の記憶 {pj}件を参照" if pj
                    else f"プロジェクト「{recalled.project_name}」: 記憶なし",
                ))
        note = f"(古い{dropped}件は省略)" if dropped else ""
        events.append(activity("history", "pulse", f"会話履歴 {kept}件を参照{note}"))
        return events
