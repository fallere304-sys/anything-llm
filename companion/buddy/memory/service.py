"""記憶のルール層(Memory Tool の実体)。

原則:
- ユーザーの操作(save / update / delete / approve / reject)は即時反映し、作業履歴に残す。
- AI が使える入口は propose() だけ。提案は status=pending で保存され、
  ユーザーが承認するまで文脈に入らない(AI が勝手に永久保存しない)。
- 文脈に入るのは status=active の記憶のみ。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..storage.db import NotFound
from .retrieval import relevance
from .store import MemoryStore

KINDS = {"long_term": "長期記憶", "project": "プロジェクト記憶"}
SOURCES = {"user": "ユーザー入力", "conversation": "会話から", "ai_proposal": "AIの提案"}
MAX_MEMORY_CHARS = 2000


class MemoryValidationError(ValueError):
    pass


@dataclass
class Retrieval:
    items: list[dict] = field(default_factory=list)
    searched: int = 0  # 検索対象(active)件数
    dropped: int = 0  # 予算超過で入らなかった件数
    project_name: Optional[str] = None

    def count(self, kind: str) -> int:
        return sum(1 for m in self.items if m["kind"] == kind)

    @property
    def important(self) -> int:
        return sum(1 for m in self.items if m["important"])


class MemoryService:
    def __init__(self, store: MemoryStore, max_chars: int = 3000) -> None:
        self.store = store
        self.max_chars = max_chars

    # ---------- 検証 ----------
    def _validate(self, content: str, kind: str, project_id: Optional[str], confidence: float) -> str:
        text = (content or "").strip()
        if not text:
            raise MemoryValidationError("記憶の内容が空です。")
        if len(text) > MAX_MEMORY_CHARS:
            raise MemoryValidationError(f"記憶は {MAX_MEMORY_CHARS} 文字以内にしてください。")
        if kind not in KINDS:
            raise MemoryValidationError(f"不明な種類です: {kind}")
        if kind == "project":
            if not project_id:
                raise MemoryValidationError("プロジェクト記憶にはプロジェクトの指定が必要です。")
            try:
                self.store.get_project(project_id)
            except NotFound:
                raise MemoryValidationError("指定のプロジェクトが存在しません。") from None
        elif project_id:
            raise MemoryValidationError("長期記憶にプロジェクトは指定できません。")
        if not 0.0 <= confidence <= 1.0:
            raise MemoryValidationError("信頼度は 0〜1 で指定してください。")
        return text

    # ---------- ユーザー操作(即時反映) ----------
    def save(
        self, content: str, *, kind: str = "long_term", project_id: Optional[str] = None,
        important: bool = False, confidence: float = 1.0, source: str = "user",
        source_ref: Optional[str] = None,
    ) -> dict:
        if source not in ("user", "conversation"):
            raise MemoryValidationError("ユーザー保存の情報源は user / conversation のみです。")
        text = self._validate(content, kind, project_id, confidence)
        mem = self.store.create_memory(
            kind=kind, content=text, project_id=project_id, important=important,
            source=source, source_ref=source_ref, confidence=confidence, status="active",
        )
        self.store.add_log("user", "memory_save", f"memory:{mem['id'][:8]}", f"{KINDS[kind]}を保存")
        return mem

    def update(self, mid: str, *, content: Optional[str] = None, important: Optional[bool] = None,
               confidence: Optional[float] = None) -> dict:
        cur = self.store.get_memory(mid)
        if content is not None:
            content = self._validate(content, cur["kind"], cur["project_id"], cur["confidence"])
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise MemoryValidationError("信頼度は 0〜1 で指定してください。")
        mem = self.store.update_memory(mid, content=content, important=important, confidence=confidence)
        self.store.add_log("user", "memory_update", f"memory:{mid[:8]}", "記憶を更新")
        return mem

    def delete(self, mid: str) -> None:
        self.store.delete_memory(mid)
        self.store.add_log("user", "memory_delete", f"memory:{mid[:8]}", "記憶を削除")

    def approve(self, mid: str) -> dict:
        mem = self.store.get_memory(mid)
        if mem["status"] != "pending":
            raise MemoryValidationError("承認待ちの記憶ではありません。")
        mem = self.store.update_memory(mid, status="active")
        self.store.add_log("user", "memory_approve", f"memory:{mid[:8]}", "AIの記憶提案を承認")
        return mem

    def reject(self, mid: str) -> None:
        mem = self.store.get_memory(mid)
        if mem["status"] != "pending":
            raise MemoryValidationError("承認待ちの記憶ではありません。")
        self.store.delete_memory(mid)
        self.store.add_log("user", "memory_reject", f"memory:{mid[:8]}", "AIの記憶提案を却下")

    # ---------- AI の入口(承認待ちになる) ----------
    def propose(
        self, content: str, *, kind: str = "long_term", project_id: Optional[str] = None,
        important: bool = False, confidence: float = 0.7, conversation_id: Optional[str] = None,
    ) -> dict:
        text = self._validate(content, kind, project_id, confidence)
        mem = self.store.create_memory(
            kind=kind, content=text, project_id=project_id, important=important,
            source="ai_proposal", source_ref=f"conversation:{conversation_id}" if conversation_id else None,
            confidence=confidence, status="pending",
        )
        self.store.add_log("ai", "memory_propose", f"memory:{mem['id'][:8]}",
                           "記憶の保存を提案(承認待ち)", conversation_id=conversation_id)
        return mem

    # ---------- 文脈への取り込み ----------
    def retrieve(self, query: str, project_id: Optional[str] = None) -> Retrieval:
        """重要事項 → 現プロジェクトの記憶 → 関連度 → 新しさ の順で予算内に詰める。"""
        active = [
            m for m in self.store.list_memories(status="active")
            if m["kind"] == "long_term" or m["project_id"] == project_id
        ]
        result = Retrieval(searched=len(active))
        if project_id:
            try:
                result.project_name = self.store.get_project(project_id)["name"]
            except NotFound:
                result.project_name = None
        scored = [(relevance(query, m["content"]), m) for m in active]
        scored.sort(key=lambda sm: sm[1]["updated_at"], reverse=True)  # 同点は新しい順(安定ソート)
        scored.sort(key=lambda sm: (sm[1]["important"], sm[1]["kind"] == "project", sm[0]), reverse=True)
        used = 0
        for _score, m in scored:
            size = len(m["content"]) + 40
            if used + size > self.max_chars:
                result.dropped += 1
                continue
            result.items.append(m)
            used += size
        return result

    @staticmethod
    def format_for_prompt(items: list[dict], project_name: Optional[str] = None) -> str:
        lines = [
            "## 記憶(ユーザーが保存を許可した情報)",
            "古い・不正確な可能性がある。会話内容と矛盾する場合は、断定せずユーザーに確認すること。",
        ]
        for m in items:
            tag = "[重要] " if m["important"] else ""
            where = f"プロジェクト「{project_name}」" if m["kind"] == "project" and project_name else KINDS[m["kind"]]
            text = " ".join(m["content"].split())
            lines.append(
                f"- #{m['id'][:8]} {tag}({where} / 信頼度{m['confidence']:.1f} / "
                f"{m['updated_at'][:10]}) {text}"
            )
        return "\n".join(lines)
