"""アクセス可視化のためのノード定義とアクティビティイベント。

UI はここで定義された「実在する処理」だけを光らせる。未実装の機能は available=False で
返し、UI 側で暗く表示する(やっていない処理をやっているように見せない)。
イベントの summary には会話本文を含めない(ログにも出るため)。
"""
from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Optional

from .llm.base import LLMProvider
from .tts.base import TTSProvider

if TYPE_CHECKING:
    from .tools.registry import ToolRegistry

def describe_nodes(
    providers: dict[str, LLMProvider], tts: Optional[TTSProvider], tools: Optional["ToolRegistry"] = None
) -> list[dict]:
    names = set(tools.names()) if tools is not None else set()

    def via(tool_name: str, fallback: str) -> str:
        """登録済みツールの送信先から表示名を決める(例: Google (Gemini) -> Gemini)。"""
        t = tools.get(tool_name) if tools is not None else None
        if t is None or not t.external:
            return fallback
        return {"Google (Gemini)": "Gemini", "Anthropic (Claude API)": "Claude API",
                "Anthropic (Claude Code)": "Claude Code"}.get(t.external, t.external)

    def node(id_, short, label, desc, available, phase=None, external=False, note=None):
        return {
            "id": id_, "short": short, "label": label, "description": desc,
            "available": available, "planned_phase": phase, "external": external, "note": note,
        }

    # 可視化パネルの表記は英語(ユーザー指定)。チャット等の他の画面は日本語のまま。
    nodes = [
        node("persona", "PERSONA", "Persona", "System prompt and behavior rules (prompts/system.md)", True),
        node("history", "HISTORY", "Context", "Current conversation (short-term memory)", True),
        node("memory", "MEMORY", "Long-term", "Information the user approved to keep (incl. pinned items)", True),
        node("project", "PROJECT", "Project", "Memories of the project linked to this conversation", True),
    ]
    for profile, short, label in (("fast", "FAST", "Fast model"), ("strong", "DEEP", "Deep model")):
        p = providers.get(profile)
        nodes.append(node(
            f"llm:{profile}", short, label,
            f"Conversation, reasoning and task dispatch: {p.name} / {p.model}" if p else "Not set (LLM_MODEL_STRONG in .env)",
            p is not None, external=bool(p and p.sends_data_externally),
        ))
    nodes += [
        node("orchestrator", "COMMAND", "Claude Code",
             "Plans and dispatches multi-step tasks with a minimal context (Claude subscription)",
             "delegate_task" in names, external=True),
        node("research", "RESEARCH", via("research_web", "Gemini"), "Web research with sources (RESEARCH_PROVIDER)",
             "research_web" in names, external=True),
        node("create", "CREATE", via("create_document", "Gemini"), "Documents saved to outputs/ (WRITER_PROVIDER)",
             "create_document" in names, external=True),
        node("image", "IMAGE", "Gemini", "Image generation saved to outputs/ (GEMINI_API_KEY / GEMINI_IMAGE_MODEL)",
             "generate_image" in names, external=True),
        node("files", "FILES", "Workspace", "Read-only access to the workspace folder (BUDDY_WORKSPACE_DIR)",
             "read_workspace_file" in names),
        node(
            "tts", "VOICE", "Voice",
            f"{tts.name} / {tts.voice}" if tts else "Not set (TTS_PROVIDER in .env)",
            tts is not None, external=bool(tts and tts.sends_data_externally),
        ),
    ]
    return nodes


def activity(
    target: str, phase: str, summary: str, aid: Optional[str] = None, **extra
) -> dict:
    """phase: start(開始) / end(終了) / pulse(瞬間的な参照)。"""
    return {
        "type": "activity", "id": aid or uuid.uuid4().hex[:12], "target": target,
        "phase": phase, "summary": summary, "ts": time.time(), **extra,
    }
