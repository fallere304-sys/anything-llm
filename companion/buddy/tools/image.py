"""画像担当: Gemini(Gemini API generateContent, REST)。

送受信の形式は公式 Python SDK(google-genai 2.28)が実際に送る内容と突き合わせて確認済み:
  POST {base}/v1beta/models/{model}:generateContent  ヘッダー x-goog-api-key
  {"contents":[{"role":"user","parts":[{"text":...}]}],
   "generationConfig":{"responseModalities":[...],"imageConfig":{"aspectRatio":...}}}
  画像は candidates[].content.parts[].inlineData{mimeType,data(base64)}
"""
from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Optional

import httpx

from .outputs import save_output
from .registry import Level, Tool, ToolContext, ToolError, ToolResult

ASPECTS = ["1:1", "3:4", "4:3", "9:16", "16:9"]
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 20_000_000


class GeminiImageClient:
    def __init__(self, api_key: str, model: str, base_url: str = "https://generativelanguage.googleapis.com",
                 timeout: float = 180.0, client: Optional[httpx.AsyncClient] = None) -> None:
        if not api_key or not model:
            raise ValueError("GEMINI_API_KEY と GEMINI_IMAGE_MODEL が必要です。")
        self.model = model
        self._key = api_key
        self._url = f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))

    async def generate(self, prompt: str, aspect_ratio: Optional[str] = None) -> tuple[bytes, str, str]:
        """(画像バイト列, MIME, モデルの添え書き) を返す。"""
        config: dict = {"responseModalities": ["TEXT", "IMAGE"]}
        if aspect_ratio:
            config["imageConfig"] = {"aspectRatio": aspect_ratio}
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
        try:
            resp = await self._client.post(self._url, json=body, headers={"x-goog-api-key": self._key})
        except httpx.TimeoutException as exc:
            raise ToolError("Gemini の応答がタイムアウトしました。") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"Gemini に接続できません: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            raise ToolError("Gemini の APIキーが無効か、このモデルの利用権限がありません。")
        if resp.status_code == 404:
            raise ToolError(f"Gemini のモデル {self.model} が見つかりません(GEMINI_IMAGE_MODEL を確認)。")
        if resp.status_code == 429:
            raise ToolError("Gemini の利用上限(無料枠の回数制限など)に達しました。時間をおいて再試行してください。")
        if resp.status_code != 200:
            raise ToolError(f"Gemini がエラーを返しました (HTTP {resp.status_code}): {resp.text[:200]}")
        try:
            obj = resp.json()
        except ValueError as exc:
            raise ToolError("Gemini の応答を解釈できません。") from exc
        return _extract(obj)

    async def aclose(self) -> None:
        await self._client.aclose()


def _extract(obj: dict) -> tuple[bytes, str, str]:
    block = (obj.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise ToolError(f"Gemini が依頼を拒否しました(理由: {block})。内容を見直してください。")
    texts, finish = [], None
    for cand in obj.get("candidates") or []:
        finish = cand.get("finishReason") or finish
        for part in (cand.get("content") or {}).get("parts") or []:
            if part.get("text"):
                texts.append(part["text"])
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                try:
                    data = base64.b64decode(inline["data"], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ToolError("Gemini の画像データが壊れています。") from exc
                if len(data) > MAX_IMAGE_BYTES:
                    raise ToolError("Gemini の画像が大きすぎます。")
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                return data, mime, " ".join(texts).strip()
    reason = f"(終了理由: {finish})" if finish else ""
    note = f" Gemini のコメント: {' '.join(texts)[:200]}" if texts else ""
    raise ToolError(f"Gemini が画像を返しませんでした{reason}。{note}".strip())


def image_tool(client: GeminiImageClient, workspace: Path) -> Tool:
    outputs = workspace / "outputs"

    async def run(args: dict, ctx: ToolContext) -> ToolResult:
        data, mime, comment = await client.generate(args["prompt"], args.get("aspect_ratio"))
        ext = EXT.get(mime)
        if ext is None:
            raise ToolError(f"想定外の画像形式です: {mime}")
        path = save_output(outputs, args.get("title") or "image", ext, data)
        rel = f"outputs/{path.name}"
        return ToolResult(
            content=f"画像を生成し {rel} に保存しました({mime}, {len(data) // 1024}KB, {client.model})。"
                    + (f"\nGemini のコメント: {comment[:500]}" if comment else ""),
            summary=f"{rel} を生成",
            data={"file": rel, "name": path.name, "kind": "image", "mime": mime, "bytes": len(data), "model": client.model},
        )

    return Tool(
        name="generate_image",
        description=(
            "Gemini に画像の生成を依頼し、作業フォルダの outputs/ に新規保存する。"
            "prompt は被写体・構図・画風・色・用途を具体的に書く(英語の方が安定することがある)。"
            "実在の人物の写真風画像や、権利を侵害する画像は依頼しないこと。"
        ),
        input_schema={"type": "object", "properties": {
            "prompt": {"type": "string", "maxLength": 4000, "description": "生成する画像の具体的な説明"},
            "aspect_ratio": {"type": "string", "enum": ASPECTS, "description": "縦横比(省略時はモデル既定)"},
            "title": {"type": "string", "maxLength": 60, "description": "ファイル名に使う短い題名"},
        }, "required": ["prompt"]},
        level=Level.IMPORTANT, execute=run, node="image", external="Google (Gemini)",
        preview=lambda a: f"[{a.get('aspect_ratio', '既定')}] {str(a.get('prompt', ''))[:300]}",
    )
