"""MCP サーバー(Streamable HTTP の JSON 応答のみ・tools 機能のみ)。

Claude Code(司令塔)が AI相棒のツールを使うための窓口。ツール呼び出しは会話側と同じ
ChatService.run_tool を通るので、権限判定・承認・作業履歴・可視化がそのまま効く。
- 実行ごとの使い捨てトークン(Authorization: Bearer)で保護し、ループバックからの接続のみ受け付ける
- delegate_task(司令塔への委任)は公開しない(再帰の防止)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..agent.runs import RunRegistry
from ..chat.service import ChatService
from ..llm.base import ToolCallRequest

log = logging.getLogger("buddy.mcp")
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
HIDDEN_TOOLS = {"delegate_task"}
MAX_RESULT_CHARS = 4000  # 司令塔へ返す結果の上限(コンテキスト節約)
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def _err(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _ok(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def build_mcp_router(service: ChatService, runs: RunRegistry) -> APIRouter:
    router = APIRouter()

    def tool_list() -> list[dict]:
        out = []
        for fn in service.tools.openai_schemas():
            f = fn["function"]
            if f["name"] in HIDDEN_TOOLS:
                continue
            out.append({"name": f["name"], "description": f["description"], "inputSchema": f["parameters"]})
        return out

    async def call_tool(run, rid: Any, params: dict) -> dict:
        name = params.get("name", "")
        if name in HIDDEN_TOOLS:
            return _err(rid, -32602, f"tool not available: {name}")
        if run.calls >= runs.MAX_CALLS:
            return _ok(rid, {"content": [{"type": "text", "text": f"上限({runs.MAX_CALLS}回)に達したため、これ以上ツールを使えません。報告して終了してください。"}], "isError": True})
        run.calls += 1
        call = ToolCallRequest(id=f"mcp-{run.id}-{run.calls}", name=name,
                               arguments=json.dumps(params.get("arguments") or {}, ensure_ascii=False))
        outcome = None
        async for item in service.run_tool(call, run.ctx):
            if isinstance(item, dict):
                run.ctx.emit({**item, "via": "orchestrator"})  # 承認待ち・作業中などの状態も画面へ
            else:
                outcome = item
        run.records.append(outcome.record)
        text = outcome.brief if outcome.brief else outcome.content
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n…(省略)"
        return _ok(rid, {"content": [{"type": "text", "text": text}], "isError": not outcome.ok})

    async def handle(run, msg: Any) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _err(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        rid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if rid is None:
            return None  # 通知(notifications/initialized など)は応答不要
        if method == "initialize":
            want = params.get("protocolVersion")
            return _ok(rid, {
                "protocolVersion": want if want in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "buddy", "version": __version__},
            })
        if method == "ping":
            return _ok(rid, {})
        if method == "tools/list":
            return _ok(rid, {"tools": tool_list()})
        if method == "tools/call":
            return await call_tool(run, rid, params)
        return _err(rid, -32601, f"method not found: {method}")

    @router.post("/mcp/{run_id}", include_in_schema=False)
    async def mcp_post(run_id: str, request: Request) -> Response:
        host = request.client.host if request.client else ""
        if host not in LOOPBACK:
            return JSONResponse({"error": "loopback only"}, status_code=403)
        auth = request.headers.get("authorization", "")
        run = runs.authenticate(run_id, auth.removeprefix("Bearer ").strip()) if auth.startswith("Bearer ") else None
        if run is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(_err(None, -32700, "parse error"), status_code=400)
        if isinstance(body, list):
            replies = [r for r in [await handle(run, m) for m in body] if r is not None]
            return JSONResponse(replies) if replies else Response(status_code=202)
        reply = await handle(run, body)
        return JSONResponse(reply) if reply is not None else Response(status_code=202)

    @router.get("/mcp/{run_id}", include_in_schema=False)
    async def mcp_get(run_id: str) -> Response:
        return Response(status_code=405)  # サーバー起点のストリームは提供しない

    @router.delete("/mcp/{run_id}", include_in_schema=False)
    async def mcp_delete(run_id: str) -> Response:
        return Response(status_code=405)

    return router
