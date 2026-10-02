"""FastAPI アプリ。認証・入力検証・SSE 変換だけを担当する。"""
from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..chat.service import MAX_USER_CHARS, Busy, ChatService
from ..config import Settings
from ..llm.base import LLMProvider
from ..storage.db import Database, NotFound

log = logging.getLogger("buddy.api")
STATIC_DIR = Path(__file__).parent / "static"


class NewConversation(BaseModel):
    title: str = Field(default="新しい会話", max_length=100)


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_USER_CHARS)


def create_app(settings: Settings, provider: LLMProvider, db: Optional[Database] = None) -> FastAPI:
    database = db or Database(settings.db_path)
    service = ChatService(
        database, provider, settings.load_system_prompt(), settings.max_context_chars
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info(
            "AI相棒 v%s 起動: provider=%s model=%s host=%s",
            __version__, provider.name, provider.model, settings.host,
        )
        yield
        await provider.aclose()
        database.close()

    app = FastAPI(title="AI Buddy", version=__version__, lifespan=lifespan)
    app.state.service = service

    def require_auth(x_buddy_token: str = Header(default="")) -> None:
        if not settings.access_token:
            return  # ループバック限定時のみ許される(Settings.validate で保証)
        if not hmac.compare_digest(x_buddy_token.encode(), settings.access_token.encode()):
            raise HTTPException(status_code=401, detail="アクセストークンが無効です。")

    auth = [Depends(require_auth)]

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status", dependencies=auth)
    async def status() -> dict:
        ok, detail = await provider.health()
        return {**service.status(), "llm_reachable": ok, "llm_detail": detail, "version": __version__}

    @app.get("/api/conversations", dependencies=auth)
    def list_conversations() -> list[dict]:
        return database.list_conversations()

    @app.post("/api/conversations", dependencies=auth, status_code=201)
    def create_conversation(body: NewConversation) -> dict:
        return database.create_conversation(body.title)

    @app.delete("/api/conversations/{cid}", dependencies=auth, status_code=204)
    def delete_conversation(cid: str) -> None:
        try:
            database.delete_conversation(cid)
        except NotFound:
            raise HTTPException(404, "会話が見つかりません。") from None
        log.info("conversation deleted: %s", cid)

    @app.get("/api/conversations/{cid}/messages", dependencies=auth)
    def list_messages(cid: str) -> list[dict]:
        try:
            return database.list_messages(cid)
        except NotFound:
            raise HTTPException(404, "会話が見つかりません。") from None

    @app.post("/api/conversations/{cid}/messages", dependencies=auth)
    async def post_message(cid: str, body: NewMessage) -> StreamingResponse:
        if not body.content.strip():
            raise HTTPException(422, "空のメッセージは送れません。")
        try:
            database.get_conversation(cid)
        except NotFound:
            raise HTTPException(404, "会話が見つかりません。") from None
        if cid in service._active:
            raise HTTPException(409, "この会話は応答生成中です。")

        async def sse() -> AsyncIterator[str]:
            try:
                async for event in service.reply_stream(cid, body.content):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Busy:
                yield f"data: {json.dumps({'type': 'error', 'message': 'この会話は応答生成中です。'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app
