"""調査担当: Perplexity(Sonar API, OpenAI 互換の Chat Completions)。"""
from __future__ import annotations

from typing import Optional

import httpx

from .registry import Level, Tool, ToolContext, ToolError, ToolResult

RESEARCH_SYSTEM = (
    "あなたは調査担当です。質問について Web を調べ、事実を簡潔に日本語でまとめてください。"
    "不確かな点は不確かと明記し、推測と事実を区別してください。"
)


class PerplexityClient:
    external = "Perplexity"
    label = "Perplexity"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.perplexity.ai",
                 timeout: float = 120.0, client: Optional[httpx.AsyncClient] = None) -> None:
        if not api_key or not model:
            raise ValueError("PERPLEXITY_API_KEY と PERPLEXITY_MODEL が必要です。")
        self.model = model
        self._key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))

    async def ask(self, query: str, recency: Optional[str] = None) -> tuple[str, list[dict]]:
        body: dict = {"model": self.model, "messages": [
            {"role": "system", "content": RESEARCH_SYSTEM}, {"role": "user", "content": query}]}
        if recency:
            body["search_recency_filter"] = recency
        try:
            resp = await self._client.post(self._url, json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TimeoutException as exc:
            raise ToolError("Perplexity の応答がタイムアウトしました。") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"Perplexity に接続できません: {type(exc).__name__}") from exc
        if resp.status_code == 401:
            raise ToolError("Perplexity の APIキーが無効です。")
        if resp.status_code == 429:
            raise ToolError("Perplexity の利用上限に達しました。時間をおいて再試行してください。")
        if resp.status_code != 200:
            raise ToolError(f"Perplexity がエラーを返しました (HTTP {resp.status_code}): {resp.text[:200]}")
        try:
            obj = resp.json()
            answer = obj["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ToolError("Perplexity の応答を解釈できません。") from exc
        return answer, _sources(obj)

    async def aclose(self) -> None:
        await self._client.aclose()


def _sources(obj: dict) -> list[dict]:
    """search_results(title/url/date)を優先し、無ければ citations(URL 配列)を使う。"""
    out = []
    for r in obj.get("search_results") or []:
        if isinstance(r, dict) and r.get("url"):
            out.append({"title": r.get("title") or r["url"], "url": r["url"], "date": r.get("date")})
    if not out:
        for url in obj.get("citations") or []:
            if isinstance(url, str):
                out.append({"title": url, "url": url, "date": None})
    return out[:20]


def research_tool(client) -> Tool:
    """client: `async ask(query, recency) -> (answer, sources)` と external / label / model を持つ。"""
    async def run(args: dict, ctx: ToolContext) -> ToolResult:
        answer, sources = await client.ask(args["query"], args.get("recency"))
        refs = "\n".join(f"[{i}] {s['title']} {s['url']}" for i, s in enumerate(sources, 1))
        content = answer + (f"\n\n出典:\n{refs}" if refs else "\n\n(出典情報なし)")
        return ToolResult(content=content, summary=f"調査完了(出典 {len(sources)}件)",
                          data={"sources": sources, "model": client.model})  # 調査結果は材料なので司令塔にも全文を返す

    return Tool(
        name="research_web",
        description=(
            f"{client.label} に Web 調査を依頼する。最新情報・事実確認・出典が必要なときに使う。"
            "質問は具体的に1つのテーマに絞ること。結果には出典 URL が付く。"
        ),
        input_schema={"type": "object", "properties": {
            "query": {"type": "string", "maxLength": 2000, "description": "調べる内容(具体的な質問文)"},
            "recency": {"type": "string", "enum": ["day", "week", "month", "year"],
                        "description": "情報の新しさで絞り込む場合のみ指定"},
        }, "required": ["query"]},
        level=Level.IMPORTANT, execute=run, node="research", external=client.external,
        preview=lambda a: str(a.get("query", ""))[:300],
    )
