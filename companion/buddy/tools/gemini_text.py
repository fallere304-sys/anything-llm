"""Gemini による文書作成(書き手)と Web 調査(Google 検索付き回答)。"""
from __future__ import annotations

from typing import Optional

from .documents import WRITER_SYSTEM, Draft
from .gemini_api import EXTERNAL, GeminiAPI, check_blocked, system_instruction, texts_and_finish, user_content
from .registry import ToolError
from .research import RESEARCH_SYSTEM

RECENCY = {"day": "直近1日", "week": "直近1週間", "month": "直近1か月", "year": "直近1年"}


class GeminiWriter:
    external = EXTERNAL
    label = "Gemini"

    def __init__(self, api: GeminiAPI, model: str) -> None:
        if not model:
            raise ValueError("GEMINI_TEXT_MODEL が必要です。")
        self.api, self.model = api, model

    async def write(self, prompt: str) -> Draft:
        obj = await self.api.generate(self.model, {
            "contents": user_content(prompt), "systemInstruction": system_instruction(WRITER_SYSTEM)})
        check_blocked(obj)
        texts, finish = texts_and_finish(obj)
        text = "".join(texts).strip()
        if not text:
            raise ToolError(f"Gemini が空の成果物を返しました(終了理由: {finish})。")
        return Draft(text=text, model=self.model, truncated=finish == "MAX_TOKENS")


class GeminiResearch:
    external = EXTERNAL
    label = "Gemini(Google 検索)"

    def __init__(self, api: GeminiAPI, model: str) -> None:
        if not model:
            raise ValueError("GEMINI_TEXT_MODEL が必要です。")
        self.api, self.model = api, model

    async def ask(self, query: str, recency: Optional[str] = None) -> tuple[str, list[dict]]:
        q = query + (f"\n(できるだけ{RECENCY[recency]}の情報を優先すること)" if recency in RECENCY else "")
        obj = await self.api.generate(self.model, {
            "contents": user_content(q), "systemInstruction": system_instruction(RESEARCH_SYSTEM),
            "tools": [{"googleSearch": {}}]})
        check_blocked(obj)
        texts, finish = texts_and_finish(obj)
        answer = "".join(texts).strip()
        if not answer:
            raise ToolError(f"Gemini が調査結果を返しませんでした(終了理由: {finish})。")
        return answer, _grounding_sources(obj)


def _grounding_sources(obj: dict) -> list[dict]:
    out, seen = [], set()
    for cand in obj.get("candidates") or []:
        for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks") or []:
            web = ch.get("web") or {}
            uri = web.get("uri")
            if uri and uri not in seen:
                seen.add(uri)
                out.append({"title": web.get("title") or uri, "url": uri, "date": None})
    return out[:20]
