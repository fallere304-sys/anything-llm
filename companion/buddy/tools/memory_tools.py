"""記憶ツール: AI は「提案」だけできる。保存はユーザーの承認後(MemoryService.propose)。"""
from __future__ import annotations

from ..memory.service import MemoryService, MemoryValidationError
from .registry import Level, Tool, ToolContext, ToolError, ToolResult


def propose_memory_tool(memory: MemoryService) -> Tool:
    async def run(args: dict, ctx: ToolContext) -> ToolResult:
        kind = args.get("kind", "long_term")
        if kind == "project" and not ctx.project_id:
            raise ToolError("この会話はプロジェクトに紐付いていないため、プロジェクト記憶は提案できません。")
        try:
            mem = memory.propose(
                args["content"], kind=kind,
                project_id=ctx.project_id if kind == "project" else None,
                important=bool(args.get("important", False)),
                confidence=float(args.get("confidence", 0.7)),
                conversation_id=ctx.conversation_id,
            )
        except MemoryValidationError as exc:
            raise ToolError(str(exc)) from exc
        return ToolResult(
            content="記憶の保存を提案しました。ユーザーが承認するまで保存・使用されません。",
            summary="記憶の保存を提案(承認待ち)",
            data={"memory_id": mem["id"]},
        )

    return Tool(
        name="propose_memory",
        description=(
            "ユーザーについて今後も役立つ情報(好み・前提・決定事項など)を記憶として保存するよう提案する。"
            "提案は承認待ちになり、ユーザーが承認するまで保存されない。一時的な話題や推測は提案しないこと。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "maxLength": 2000, "description": "記憶する内容(簡潔な1〜2文)"},
                "kind": {"type": "string", "enum": ["long_term", "project"],
                         "description": "long_term=ユーザー全般 / project=この会話のプロジェクト"},
                "important": {"type": "boolean", "description": "常に参照すべき重要事項か"},
                "confidence": {"type": "number", "description": "確からしさ 0〜1"},
            },
            "required": ["content"],
        },
        level=Level.READ,  # 提案のみで実保存はしない
        execute=run,
        node="memory",
        preview=lambda a: str(a.get("content", ""))[:120],
    )
