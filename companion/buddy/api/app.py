"""FastAPI アプリ。認証・入力検証・SSE 変換だけを担当する。"""
from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..activity import describe_nodes
from ..chat.service import MAX_USER_CHARS, Busy, ChatService, UnknownProfile
from ..config import Settings
from ..llm.base import LLMProvider
from ..memory.service import MemoryService, MemoryValidationError
from ..memory.store import MemoryStore, ProjectInUse
from ..tts.base import TTSError, TTSProvider
from ..storage.db import Database, NotFound

log = logging.getLogger("buddy.api")
STATIC_DIR = Path(__file__).parent / "static"


class NewConversation(BaseModel):
    title: str = Field(default="新しい会話", max_length=100)


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_USER_CHARS)
    profile: str = Field(default="fast", max_length=20)


MAX_TTS_CHARS = 2000


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)


class ConversationPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=100)
    project_id: Optional[str] = None  # 明示的に null を送ると紐付け解除


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    kind: Literal["long_term", "project"] = "long_term"
    project_id: Optional[str] = None
    important: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_message_id: Optional[int] = None  # 会話の発言から保存する場合


class MemoryPatch(BaseModel):
    content: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    important: Optional[bool] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    description: Optional[str] = Field(default=None, max_length=500)


def create_app(
    settings: Settings,
    providers: LLMProvider | dict[str, LLMProvider],
    db: Optional[Database] = None,
    tts: Optional[TTSProvider] = None,
) -> FastAPI:
    if isinstance(providers, LLMProvider):
        providers = {"fast": providers}
    provider = providers["fast"]
    database = db or Database(settings.db_path)
    memory = MemoryService(MemoryStore(database), settings.max_memory_chars)
    service = ChatService(
        database, providers, settings.load_system_prompt(), settings.max_context_chars, memory
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info(
            "AI相棒 v%s 起動: provider=%s model=%s host=%s",
            __version__, provider.name, provider.model, settings.host,
        )
        yield
        for p in providers.values():
            await p.aclose()
        if tts:
            await tts.aclose()
        database.close()

    app = FastAPI(title="AI Buddy", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.memory = memory
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
        tts_info = {"enabled": False}
        if tts:
            tts_ok, tts_detail = await tts.health()
            tts_info = {
                "enabled": True, "provider": tts.name, "voice": tts.voice,
                "external": tts.sends_data_externally, "ok": tts_ok, "detail": tts_detail,
            }
        return {
            **service.status(), "llm_reachable": ok, "llm_detail": detail,
            "tts": tts_info, "nodes": describe_nodes(providers, tts), "version": __version__,
        }

    @app.post("/api/tts", dependencies=auth)
    async def speak(body: SpeakRequest) -> Response:
        if tts is None:
            raise HTTPException(404, "音声出力は設定されていません(TTS_PROVIDER)。")
        if not body.text.strip():
            raise HTTPException(422, "空のテキストは読み上げできません。")
        try:
            clip = await tts.synthesize(body.text)
        except TTSError as exc:
            log.error("TTS error: %s", exc)
            raise HTTPException(502, str(exc)) from exc
        return Response(clip.data, media_type=clip.mime, headers={"Cache-Control": "no-store"})

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

    @app.patch("/api/conversations/{cid}", dependencies=auth)
    def patch_conversation(cid: str, body: ConversationPatch) -> dict:
        try:
            database.get_conversation(cid)
            if body.title is not None:
                database.rename_conversation(cid, body.title)
            if "project_id" in body.model_fields_set:
                if body.project_id is not None:
                    memory.store.get_project(body.project_id)
                database.set_conversation_project(cid, body.project_id)
            return database.get_conversation(cid)
        except NotFound:
            raise HTTPException(404, "会話またはプロジェクトが見つかりません。") from None

    # ---------- 記憶(Memory Tool のユーザー側の口) ----------
    def _mem_errors(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except NotFound:
            raise HTTPException(404, "見つかりません。") from None
        except MemoryValidationError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/memories", dependencies=auth)
    def list_memories(
        status: Optional[Literal["active", "pending"]] = None,
        kind: Optional[Literal["long_term", "project"]] = None,
        project_id: Optional[str] = None,
        important: Optional[bool] = None,
    ) -> list[dict]:
        return memory.store.list_memories(status=status, kind=kind, project_id=project_id, important=important)

    @app.post("/api/memories", dependencies=auth, status_code=201)
    def create_memory(body: MemoryIn) -> dict:
        source, ref = "user", None
        if body.source_message_id is not None:
            try:
                msg = database.get_message(body.source_message_id)
            except NotFound:
                raise HTTPException(404, "元の発言が見つかりません。") from None
            source, ref = "conversation", f"message:{msg['id']}"
        return _mem_errors(
            memory.save, body.content, kind=body.kind, project_id=body.project_id,
            important=body.important, confidence=body.confidence, source=source, source_ref=ref,
        )

    @app.patch("/api/memories/{mid}", dependencies=auth)
    def patch_memory(mid: str, body: MemoryPatch) -> dict:
        return _mem_errors(memory.update, mid, content=body.content, important=body.important,
                           confidence=body.confidence)

    @app.delete("/api/memories/{mid}", dependencies=auth, status_code=204)
    def delete_memory(mid: str) -> None:
        _mem_errors(memory.delete, mid)

    @app.post("/api/memories/{mid}/approve", dependencies=auth)
    def approve_memory(mid: str) -> dict:
        return _mem_errors(memory.approve, mid)

    @app.post("/api/memories/{mid}/reject", dependencies=auth, status_code=204)
    def reject_memory(mid: str) -> None:
        _mem_errors(memory.reject, mid)

    @app.get("/api/projects", dependencies=auth)
    def list_projects() -> list[dict]:
        return memory.store.list_projects()

    @app.post("/api/projects", dependencies=auth, status_code=201)
    def create_project(body: ProjectIn) -> dict:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "プロジェクト名が空です。")
        if memory.store.find_project_by_name(name):
            raise HTTPException(409, "同じ名前のプロジェクトがあります。")
        return memory.store.create_project(name, body.description.strip())

    @app.patch("/api/projects/{pid}", dependencies=auth)
    def patch_project(pid: str, body: ProjectPatch) -> dict:
        if body.name is not None:
            other = memory.store.find_project_by_name(body.name.strip())
            if other and other["id"] != pid:
                raise HTTPException(409, "同じ名前のプロジェクトがあります。")
        try:
            return memory.store.update_project(
                pid, body.name.strip() if body.name else None, body.description
            )
        except NotFound:
            raise HTTPException(404, "プロジェクトが見つかりません。") from None

    @app.delete("/api/projects/{pid}", dependencies=auth, status_code=204)
    def delete_project(pid: str) -> None:
        try:
            memory.store.delete_project(pid)
        except NotFound:
            raise HTTPException(404, "プロジェクトが見つかりません。") from None
        except ProjectInUse as exc:
            raise HTTPException(
                409, f"このプロジェクトには記憶が {exc.args[0]} 件あります。先に記憶を削除してください。"
            ) from None

    @app.get("/api/worklog", dependencies=auth)
    def worklog(limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
        return memory.store.list_log(limit)

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
        if body.profile not in providers:
            raise HTTPException(400, f"未設定のモデルプロファイルです: {body.profile}")
        if cid in service._active:
            raise HTTPException(409, "この会話は応答生成中です。")

        async def sse() -> AsyncIterator[str]:
            try:
                async for event in service.reply_stream(cid, body.content, body.profile):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except (Busy, UnknownProfile) as exc:
                msg = "この会話は応答生成中です。" if isinstance(exc, Busy) else f"未設定のモデルプロファイルです: {exc}"
                yield f"data: {json.dumps({'type': 'error', 'message': msg}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app
