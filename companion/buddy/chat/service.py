"""チャットのユースケース層。保存・文脈構築・LLM呼び出し・ツール実行(エージェントループ)を束ねる。

ループ: LLM → (ツール要求) → 権限判定 → 必要なら承認待ち → 実行 → 結果を LLM へ → … → 最終回答。
最大ステップ数・ツール実行タイムアウト・承認タイムアウトで暴走を防ぐ。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from ..activity import activity
from ..agent.approvals import ApprovalBroker
from ..llm.base import ChatMessage, LLMError, LLMProvider, ToolCallRequest
from ..memory.service import MemoryService, Retrieval
from ..storage.db import Database
from ..tools.permissions import PermissionPolicy
from ..tools.registry import LEVEL_LABEL, ToolContext, ToolError, ToolRegistry
from .context import build_context

log = logging.getLogger("buddy.chat")

MAX_USER_CHARS = 8000
TITLE_CHARS = 30
MAX_TOOL_RESULT_CHARS = 40_000


class Busy(Exception):
    """同じ会話で応答生成中に新しい送信があった。"""


class UnknownProfile(Exception):
    """設定されていないモデルプロファイルが指定された。"""


@dataclass
class AgentLimits:
    max_steps: int = 6
    tool_timeout: float = 600.0
    approval_timeout: float = 900.0


@dataclass
class _ToolOutcome:
    content: str  # LLM に返す内容
    record: dict = field(default_factory=dict)  # 会話に残す作業記録


class ChatService:
    def __init__(
        self,
        db: Database,
        providers: dict[str, LLMProvider],
        system_prompt: str,
        max_context_chars: int,
        memory: Optional[MemoryService] = None,
        tools: Optional[ToolRegistry] = None,
        policy: Optional[PermissionPolicy] = None,
        approvals: Optional[ApprovalBroker] = None,
        limits: Optional[AgentLimits] = None,
    ) -> None:
        self.db = db
        self.memory = memory
        self.providers = providers
        self.provider = providers["fast"]  # 既定(状態表示用)
        self.system_prompt = system_prompt
        self.max_context_chars = max_context_chars
        self.tools = tools or ToolRegistry()
        self.policy = policy or PermissionPolicy()
        self.approvals = approvals or ApprovalBroker()
        self.limits = limits or AgentLimits()
        self.state = "idle"  # idle | thinking | responding | working | waiting | error
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
            "tools": self.tools.names(),
            "pending_approvals": self.approvals.list(),
        }

    def _set(self, state: str) -> dict:
        self.state = state
        return {"type": "status", "state": state}

    async def reply_stream(
        self, cid: str, user_text: str, profile: str = "fast"
    ) -> AsyncIterator[dict]:
        """ユーザー発言を保存し、応答をイベント列として返す。

        イベント: status / activity / delta / approval_request / approval_resolved / tool_result / done / error。
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
            history = [self._history_entry(m) for m in self.db.list_messages(cid)]
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

            schemas = self.tools.openai_schemas() if provider.supports_tools and len(self.tools) else None
            ctx = ToolContext(conversation_id=cid, project_id=conv.get("project_id"))
            texts: list[str] = []
            records: list[dict] = []
            stopped_by_limit = False
            for step in range(1, self.limits.max_steps + 1):
                parts: list[str] = []
                calls: list[ToolCallRequest] = []
                t0 = time.monotonic()
                llm_ev = activity(f"llm:{profile}", "start", f"Query -> {provider.model}",
                                  external=provider.sends_data_externally)
                log.info("activity llm:%s start model=%s step=%d", profile, provider.model, step)
                yield llm_ev
                if step > 1:
                    yield self._set("thinking")
                try:
                    async for chunk in provider.stream(messages, tools=schemas):
                        if isinstance(chunk, ToolCallRequest):
                            calls.append(chunk)
                            continue
                        if not parts:
                            if texts:
                                yield {"type": "delta", "text": "\n\n"}  # ツール前後の文章を区切る
                            yield self._set("responding")
                        parts.append(chunk)
                        yield {"type": "delta", "text": chunk}
                except LLMError as exc:
                    self.state, self.last_error = "error", str(exc)
                    log.error("LLM error in conversation %s: %s", cid, exc)
                    yield activity(f"llm:{profile}", "end", "Failed", llm_ev["id"], ok=False)
                    self._log("llm_call", f"llm:{profile}", f"{provider.model}: 失敗", False, cid)
                    yield {"type": "error", "message": str(exc)}
                    return
                text = "".join(parts)
                secs = time.monotonic() - t0
                summary = f"応答受信 {len(text)}文字 / {secs:.1f}秒" + (f" / ツール要求 {len(calls)}件" if calls else "")
                hud = f"Response {len(text)} chars / {secs:.1f}s" + (f" / {len(calls)} tool call(s)" if calls else "")
                yield activity(f"llm:{profile}", "end", hud, llm_ev["id"], ok=True)
                self._log("llm_call", f"llm:{profile}", f"{provider.model}: {summary}", True, cid)
                if text.strip():
                    texts.append(text)
                if not calls:
                    break
                messages.append(ChatMessage("assistant", text, tool_calls=tuple(calls)))
                for call in calls:
                    outcome: Optional[_ToolOutcome] = None
                    async for item in self._run_tool(call, ctx):
                        if isinstance(item, _ToolOutcome):
                            outcome = item
                        else:
                            yield item
                    assert outcome is not None
                    records.append(outcome.record)
                    messages.append(ChatMessage("tool", outcome.content[:MAX_TOOL_RESULT_CHARS],
                                                tool_call_id=call.id))
            else:
                stopped_by_limit = True

            final = "\n\n".join(t.strip() for t in texts)
            if stopped_by_limit:
                note = f"(最大ステップ数 {self.limits.max_steps} に達したため作業を停止しました。続ける場合は指示してください)"
                final = (final + "\n\n" + note).strip()
                yield {"type": "delta", "text": note}
                self._log("agent_limit", "agent", note, False, cid)
            if not final.strip():
                self.state, self.last_error = "error", "LLM が空の応答を返しました。"
                log.error("empty LLM response in conversation %s", cid)
                yield {"type": "error", "message": self.last_error}
                return
            saved = self.db.add_message(
                cid, "assistant", final, provider=provider.name, model=provider.model,
                tools_json=json.dumps(records, ensure_ascii=False) if records else None,
            )
            yield self._set("idle")
            yield {"type": "done", "message": saved}
        finally:
            # クライアント切断(キャンセル)でも状態を確実に戻す。
            self._active.discard(cid)
            if self.state in ("thinking", "responding", "working", "waiting"):
                self.state = "idle"

    async def _run_tool(self, call: ToolCallRequest, ctx: ToolContext) -> AsyncIterator["dict | _ToolOutcome"]:
        cid = ctx.conversation_id
        record = {"tool": call.name, "ok": False, "summary": ""}

        def fail(msg: str, summary: str) -> _ToolOutcome:
            record["summary"] = summary
            self._log("tool_call", call.name, summary, False, cid)
            return _ToolOutcome(f"エラー: {msg}", record)

        tool = self.tools.get(call.name)
        if tool is None:
            yield {"type": "tool_result", "tool": call.name, "ok": False, "summary": "未登録のツール"}
            yield fail(f"ツール {call.name} は存在しません。使えるツール: {', '.join(self.tools.names())}", "未登録のツール")
            return
        try:
            args = json.loads(call.arguments or "{}")
        except ValueError:
            yield {"type": "tool_result", "tool": tool.name, "ok": False, "summary": "引数が不正な JSON"}
            yield fail("引数が JSON として解釈できません。", "引数が不正な JSON")
            return
        purpose = str(args.pop("purpose", "") if isinstance(args, dict) else "")[:300]
        try:
            args = self.tools.validate(tool, args)
        except ToolError as exc:
            yield {"type": "tool_result", "tool": tool.name, "ok": False, "summary": str(exc)}
            yield fail(str(exc), f"引数エラー: {exc}")
            return
        preview = tool.preview(args)
        act = activity(tool.node, "start", f"{tool.name}: preparing", external=tool.external)
        yield act

        if self.policy.requires_approval(tool):
            ap = self.approvals.create(
                conversation_id=cid, tool=tool.name, level=int(tool.level),
                level_label=LEVEL_LABEL[tool.level], purpose=purpose, preview=preview,
                external=tool.external, reason=self.policy.reason(tool),
            )
            yield self._set("waiting")
            yield {"type": "approval_request", **ap.public()}
            self._log("approval_request", tool.name, f"承認を要求: {self.policy.reason(tool)}", True, cid)
            outcome = await self.approvals.wait(ap, self.limits.approval_timeout)
            yield {"type": "approval_resolved", "id": ap.id, "outcome": outcome}
            self._log_user("approval_" + outcome, tool.name,
                           {"approved": "実行を承認", "denied": "実行を拒否", "timeout": "承認が時間切れ"}[outcome], cid)
            if outcome != "approved":
                label = "拒否" if outcome == "denied" else "時間切れ"
                yield activity(tool.node, "end", f"{tool.name}: {'denied' if outcome == 'denied' else 'approval timed out'}",
                               act["id"], ok=False)
                yield {"type": "tool_result", "tool": tool.name, "ok": False, "summary": f"ユーザーが{label}"}
                record["summary"] = f"ユーザーが{label}"
                yield _ToolOutcome(
                    f"ユーザーはこのツールの実行を承認しませんでした({label})。勝手に再試行せず、"
                    "代わりの方法を提案するか、ユーザーに確認してください。", record)
                return

        yield self._set("working")
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(tool.execute(args, ctx), self.limits.tool_timeout)
        except ToolError as exc:
            yield activity(tool.node, "end", f"{tool.name}: failed", act["id"], ok=False)
            yield {"type": "tool_result", "tool": tool.name, "ok": False, "summary": str(exc)}
            yield fail(str(exc), f"失敗: {exc}")
            return
        except asyncio.TimeoutError:
            msg = f"{int(self.limits.tool_timeout)}秒以内に終わらなかったため中止しました。"
            yield activity(tool.node, "end", f"{tool.name}: timed out", act["id"], ok=False)
            yield {"type": "tool_result", "tool": tool.name, "ok": False, "summary": msg}
            yield fail(msg, "時間切れ")
            return
        except Exception as exc:  # 想定外の不具合は握り潰さずログに残し、AI にも失敗を伝える
            log.exception("tool %s crashed", tool.name)
            msg = f"内部エラー({type(exc).__name__})"
            yield activity(tool.node, "end", f"{tool.name}: internal error", act["id"], ok=False)
            yield {"type": "tool_result", "tool": tool.name, "ok": False, "summary": msg}
            yield fail(msg, msg)
            return
        elapsed = time.monotonic() - t0
        record.update(ok=True, summary=result.summary, data=result.data)
        self._log("tool_call", tool.name, f"{result.summary} / {elapsed:.1f}秒", True, cid)
        yield activity(tool.node, "end", f"{tool.name}: done ({elapsed:.1f}s)", act["id"], ok=True)
        yield {"type": "tool_result", "tool": tool.name, "ok": True, "summary": result.summary,
               "data": result.data, "external": tool.external}
        yield _ToolOutcome(result.content, record)

    @staticmethod
    def _history_entry(m: dict) -> dict:
        """過去の作業記録を、次のターンの LLM が参照できる形で本文に添える。"""
        content = m["content"]
        if m.get("tools_json"):
            try:
                recs = json.loads(m["tools_json"])
                lines = [f"- {r['tool']}: {'成功' if r.get('ok') else '失敗'} {r.get('summary', '')}" for r in recs]
                content += "\n\n[この返答で実行した作業]\n" + "\n".join(lines)
            except (ValueError, KeyError, TypeError):
                log.warning("broken tools_json in message %s", m.get("id"))
        return {"role": m["role"], "content": content}

    def _log(self, action: str, target: str, summary: str, ok: bool, cid: str) -> None:
        if self.memory:
            self.memory.store.add_log("ai", action, target, summary, ok, cid)

    def _log_user(self, action: str, target: str, summary: str, cid: str) -> None:
        if self.memory:
            self.memory.store.add_log("user", action, target, summary, True, cid)

    @staticmethod
    def _context_activities(
        messages: list, dropped: int, recalled: Optional[Retrieval] = None
    ) -> list[dict]:
        has_system = bool(messages) and messages[0].role == "system"
        kept = len(messages) - (1 if has_system else 0)
        events = []
        if has_system and messages[0].content:
            events.append(activity("persona", "pulse", "Persona & rules applied"))
        if recalled is not None:
            lt, imp = recalled.count("long_term"), recalled.important
            note = f" ({imp} pinned)" if imp else ""
            text = f"Recalled {lt} memor{'y' if lt == 1 else 'ies'}{note}" if lt else "Memory search: no match"
            if recalled.dropped:
                text += f" / {recalled.dropped} skipped (budget)"
            events.append(activity("memory", "pulse", text))
            if recalled.project_name:
                pj = recalled.count("project")
                events.append(activity(
                    "project", "pulse",
                    f"Project \"{recalled.project_name}\": recalled {pj}" if pj
                    else f"Project \"{recalled.project_name}\": no memories",
                ))
        note = f" ({dropped} older trimmed)" if dropped else ""
        events.append(activity("history", "pulse", f"Context: {kept} message{'' if kept == 1 else 's'}{note}"))
        return events
