"""成果物作成担当: Claude(Anthropic 公式 Python SDK)。

生成物は作業フォルダの outputs/ に「新規ファイル」として保存する(既存ファイルは上書きしない)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import anthropic

from .registry import Level, Tool, ToolContext, ToolError, ToolResult

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
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Draft:
    text: str
    model: str
    truncated: bool


class ClaudeWriter:
    def __init__(self, api_key: str, model: str, *, effort: str = "high", max_tokens: int = 32000,
                 fallback: str = "default", base_url: str = "https://api.anthropic.com",
                 timeout: float = 600.0, client: Optional[Any] = None) -> None:
        if not api_key or not model:
            raise ValueError("ANTHROPIC_API_KEY と CLAUDE_MODEL が必要です。")
        self.model, self.effort, self.max_tokens, self.fallback = model, effort, max_tokens, fallback
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, timeout=timeout)

    async def write(self, prompt: str) -> Draft:
        kwargs: dict = {
            "model": self.model, "max_tokens": self.max_tokens, "system": WRITER_SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        try:
            if self.fallback == "default":
                # 安全上の辞退(refusal)時にサーバー側で別モデルへ自動フォールバック
                stream_cm = self._client.beta.messages.stream(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                stream_cm = self._client.messages.stream(**kwargs)
            async with stream_cm as stream:
                msg = await stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise ToolError("Claude の APIキーが無効です。") from exc
        except anthropic.RateLimitError as exc:
            raise ToolError("Claude の利用上限に達しました。時間をおいて再試行してください。") from exc
        except anthropic.BadRequestError as exc:
            raise ToolError(f"Claude へのリクエストが不正です(モデル名・設定を確認): {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise ToolError(f"Claude がエラーを返しました (HTTP {exc.status_code})。") from exc
        except anthropic.APITimeoutError as exc:
            raise ToolError("Claude の応答がタイムアウトしました。") from exc
        except anthropic.APIConnectionError as exc:
            raise ToolError("Claude に接続できません。ネットワークを確認してください。") from exc
        if msg.stop_reason == "refusal":
            raise ToolError("Claude がこの依頼を辞退しました(安全上の理由)。依頼内容を見直してください。")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise ToolError("Claude が空の成果物を返しました。")
        return Draft(text=text, model=getattr(msg, "model", self.model), truncated=msg.stop_reason == "max_tokens")


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_FENCE = re.compile(r"^```[\w+-]*\n(.*)\n```\s*$", re.S)


def slugify(title: str) -> str:
    s = _BAD.sub("_", title).strip(" ._") or "output"
    return s[:40]


def save_output(outputs: Path, title: str, ext: str, text: str) -> Path:
    outputs.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}_{slugify(title)}"
    path, n = outputs / f"{stem}{ext}", 1
    while path.exists():  # 上書きしない
        n += 1
        path = outputs / f"{stem}-{n}{ext}"
    path.write_text(text, encoding="utf-8")
    return path


def deliverable_tool(writer: ClaudeWriter, workspace: Path) -> Tool:
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
        return ToolResult(
            content=f"成果物を作成し {rel} に保存しました({len(text)}文字, {draft.model}){note}\n\n--- 冒頭 ---\n{preview}",
            summary=f"{rel} を作成({len(text)}文字)" + ("・途中終了" if draft.truncated else ""),
            data={"file": rel, "name": path.name, "chars": len(text), "model": draft.model, "truncated": draft.truncated},
        )

    return Tool(
        name="create_deliverable",
        description=(
            "Claude に成果物(文書・報告書・企画書・コード・表など)の作成を依頼し、作業フォルダの outputs/ に新規保存する。"
            "会話で十分な短い回答には使わない。必要な材料(調査結果・要件)は materials に全部入れること"
            "(Claude は会話履歴を見られない)。"
        ),
        input_schema={"type": "object", "properties": {
            "title": {"type": "string", "maxLength": 100, "description": "成果物の題名(ファイル名にも使う)"},
            "instructions": {"type": "string", "maxLength": 8000, "description": "目的・構成・分量・読み手などの具体的な指示"},
            "materials": {"type": "string", "maxLength": 60000, "description": "成果物に使う材料(調査結果・メモ・要件など)"},
            "format": {"type": "string", "enum": sorted(FORMATS), "description": "出力形式(既定 markdown)"},
        }, "required": ["title", "instructions"]},
        level=Level.IMPORTANT, execute=run, node="claude", external="Anthropic (Claude)",
        preview=lambda a: f"『{a.get('title', '')}』({a.get('format', 'markdown')})\n{str(a.get('instructions', ''))[:300]}",
    )
