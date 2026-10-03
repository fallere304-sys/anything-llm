"""文書作成ツール(create_document)。書き手(writer)は差し替え可能: Gemini / Claude API。

writer は `async write(prompt) -> Draft` と属性 `external`(送信先名)・`label`(表示名)を持つ。
生成物は作業フォルダの outputs/ に「新規ファイル」として保存する(既存ファイルは上書きしない)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .outputs import save_output
from .registry import Level, Tool, ToolContext, ToolResult

FORMATS = {
    "markdown": ".md", "text": ".txt", "html": ".html", "csv": ".csv",
    "json": ".json", "python": ".py", "javascript": ".js",
}
WRITER_SYSTEM = (
    "あなたは成果物の作成担当です。依頼された成果物の本体だけを出力してください。"
    "前置き・後書き・作業の説明は書かないでください。指定形式に厳密に従ってください。"
    "材料に無い事実を作らず、不明な点は成果物内で「要確認」と明記してください。"
)
PREVIEW_CHARS = 2000
BRIEF_CHARS = 300
_FENCE = re.compile(r"^```[\w+-]*\n(.*)\n```\s*$", re.S)


@dataclass
class Draft:
    text: str
    model: str
    truncated: bool


class Writer(Protocol):
    external: str
    label: str

    async def write(self, prompt: str) -> Draft: ...


def document_tool(writer: Writer, workspace: Path) -> Tool:
    outputs = workspace / "outputs"

    async def run(args: dict, ctx: ToolContext) -> ToolResult:
        fmt = args.get("format", "markdown")
        prompt = (
            f"# 成果物\n{args['title']}\n\n# 形式\n{fmt}\n\n# 指示\n{args['instructions']}\n"
            + (f"\n# 材料\n{args['materials']}\n" if args.get("materials") else "")
        )
        draft = await writer.write(prompt)
        text = draft.text
        m = _FENCE.match(text)
        if m and fmt not in ("markdown", "text"):
            text = m.group(1)  # コード形式は外側のコードフェンスを外して保存
        path = save_output(outputs, args["title"], FORMATS[fmt], text)
        rel = f"outputs/{path.name}"
        note = "\n※出力が上限で途中終了しています。続きが必要なら分割して依頼してください。" if draft.truncated else ""
        preview = text[:PREVIEW_CHARS] + ("\n…(以下省略)" if len(text) > PREVIEW_CHARS else "")
        head = text[:BRIEF_CHARS].replace("\n", " ")
        return ToolResult(
            content=f"成果物を作成し {rel} に保存しました({len(text)}文字, {draft.model}){note}\n\n--- 冒頭 ---\n{preview}",
            summary=f"{rel} を作成({len(text)}文字)" + ("・途中終了" if draft.truncated else ""),
            data={"file": rel, "name": path.name, "chars": len(text), "model": draft.model, "truncated": draft.truncated},
            # 司令塔には本文を返さない(コンテキスト節約)。必要なら read_workspace_file で読む
            brief=f"{rel} に保存({len(text)}文字){'・途中終了' if draft.truncated else ''}。冒頭: {head}",
        )

    return Tool(
        name="create_document",
        description=(
            f"{writer.label} に成果物(文書・報告書・企画書・コード・表など)の作成を依頼し、作業フォルダの outputs/ に新規保存する。"
            "会話で十分な短い回答には使わない。必要な材料(調査結果・要件)は materials に全部入れること"
            "(作成担当は会話履歴を見られない)。"
        ),
        input_schema={"type": "object", "properties": {
            "title": {"type": "string", "maxLength": 100, "description": "成果物の題名(ファイル名にも使う)"},
            "instructions": {"type": "string", "maxLength": 8000, "description": "目的・構成・分量・読み手などの具体的な指示"},
            "materials": {"type": "string", "maxLength": 60000, "description": "成果物に使う材料(調査結果・メモ・要件など)"},
            "format": {"type": "string", "enum": sorted(FORMATS), "description": "出力形式(既定 markdown)"},
        }, "required": ["title", "instructions"]},
        level=Level.IMPORTANT, execute=run, node="create", external=writer.external,
        preview=lambda a: f"『{a.get('title', '')}』({a.get('format', 'markdown')})\n{str(a.get('instructions', ''))[:300]}",
    )
