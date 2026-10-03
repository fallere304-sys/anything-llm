"""Gemini API(REST)の共通クライアント。

形式は公式 Python SDK(google-genai 2.28)が実際に送る内容を記録して確認済み:
  POST {base}/v1beta/models/{model}:generateContent   ヘッダー x-goog-api-key
    {"contents":[{"role":"user","parts":[{"text":...}]}],
     "systemInstruction":{"role":"user","parts":[{"text":...}]},
     "tools":[{"googleSearch":{}}],
     "generationConfig":{"responseModalities":[...],"imageConfig":{"aspectRatio":...}}}
  GET  {base}/v1beta/models?pageSize=N
"""
from __future__ import annotations

from typing import Optional

import httpx

from .registry import ToolError

DEFAULT_BASE = "https://generativelanguage.googleapis.com"
EXTERNAL = "Google (Gemini)"


class GeminiAPI:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE, timeout: float = 180.0,
                 client: Optional[httpx.AsyncClient] = None) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY が必要です。")
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))

    def _headers(self) -> dict:
        return {"x-goog-api-key": self._key}

    async def generate(self, model: str, body: dict) -> dict:
        url = f"{self._base}/v1beta/models/{model}:generateContent"
        try:
            resp = await self._client.post(url, json=body, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ToolError("Gemini の応答がタイムアウトしました。") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"Gemini に接続できません: {type(exc).__name__}") from exc
        _raise_for_status(resp, model)
        try:
            return resp.json()
        except ValueError as exc:
            raise ToolError("Gemini の応答を解釈できません。") from exc

    async def list_models(self) -> list[dict]:
        try:
            resp = await self._client.get(f"{self._base}/v1beta/models", params={"pageSize": 200},
                                          headers=self._headers())
        except httpx.HTTPError as exc:
            raise ToolError(f"Gemini に接続できません: {type(exc).__name__}") from exc
        _raise_for_status(resp, "")
        return resp.json().get("models", [])

    async def aclose(self) -> None:
        await self._client.aclose()


def _raise_for_status(resp: httpx.Response, model: str) -> None:
    if resp.status_code == 200:
        return
    if resp.status_code in (401, 403):
        raise ToolError("Gemini の APIキーが無効か、このモデルの利用権限がありません。")
    if resp.status_code == 404:
        raise ToolError(f"Gemini のモデル {model} が見つかりません(.env のモデル名を確認)。")
    if resp.status_code == 429:
        raise ToolError("Gemini の利用上限(無料枠の回数制限など)に達しました。時間をおいて再試行してください。")
    raise ToolError(f"Gemini がエラーを返しました (HTTP {resp.status_code}): {resp.text[:200]}")


def check_blocked(obj: dict) -> None:
    block = (obj.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise ToolError(f"Gemini が依頼を拒否しました(理由: {block})。内容を見直してください。")


def texts_and_finish(obj: dict) -> tuple[list[str], Optional[str]]:
    texts, finish = [], None
    for cand in obj.get("candidates") or []:
        finish = cand.get("finishReason") or finish
        for part in (cand.get("content") or {}).get("parts") or []:
            if part.get("text"):
                texts.append(part["text"])
    return texts, finish


def user_content(text: str) -> list[dict]:
    return [{"role": "user", "parts": [{"text": text}]}]


def system_instruction(text: str) -> dict:
    return {"role": "user", "parts": [{"text": text}]}


if __name__ == "__main__":  # 使えるモデルの一覧(python -m buddy.tools.gemini_api)
    import asyncio

    from ..config import load_settings

    async def main() -> None:
        s = load_settings()
        api = GeminiAPI(s.gemini_api_key, s.gemini_base_url)
        try:
            for m in await api.list_models():
                methods = ",".join(m.get("supportedGenerationMethods", []))
                print(f"{m.get('name', '').removeprefix('models/'):45} {methods}")
        finally:
            await api.aclose()

    asyncio.run(main())
