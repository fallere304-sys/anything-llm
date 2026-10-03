import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from buddy.config import load_settings
from buddy.llm.base import ChatMessage, ToolCallRequest
from buddy.llm.openai_compat import OpenAICompatProvider
from buddy.memory.service import MemoryService
from buddy.memory.store import MemoryStore
from buddy.storage.db import Database
from buddy.tools.builtin import build_policy, build_registry
from buddy.tools.deliverable import ClaudeWriter
from buddy.tools.documents import document_tool
from buddy.tools.outputs import save_output, slugify
from buddy.tools.memory_tools import propose_memory_tool
from buddy.tools.registry import Level, ToolContext, ToolError, ToolRegistry
from buddy.tools.research import PerplexityClient, research_tool
from buddy.tools.workspace import list_files_tool, read_file_tool, safe_path

CTX = ToolContext(conversation_id="c1")


# ---------- 作業フォルダ ----------
def test_safe_path_blocks_escape(tmp_path):
    (tmp_path / "ws").mkdir()
    root = tmp_path / "ws"
    assert safe_path(root, "a/b.txt") == (root / "a/b.txt").resolve()
    for bad in ("../x", "a/../../x", str(tmp_path / "other")):
        with pytest.raises(ToolError):
            safe_path(root, bad)


async def test_list_and_read(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("こんにちは", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\0\1\2")
    r = await list_files_tool(tmp_path).execute({"recursive": True}, CTX)
    assert "docs/a.md (15 bytes)" in r.content
    r = await read_file_tool(tmp_path).execute({"path": "docs/a.md"}, CTX)
    assert r.content == "こんにちは"
    with pytest.raises(ToolError, match="テキスト"):
        await read_file_tool(tmp_path).execute({"path": "bin.dat"}, CTX)
    with pytest.raises(ToolError, match="外"):
        await read_file_tool(tmp_path).execute({"path": "../../etc/passwd"}, CTX)


# ---------- 記憶ツール ----------
async def test_propose_memory_tool_creates_pending():
    mem = MemoryService(MemoryStore(Database(":memory:")))
    tool = propose_memory_tool(mem)
    r = await tool.execute({"content": "コーヒー派"}, CTX)
    assert "承認" in r.content and mem.store.list_memories(status="pending")[0]["content"] == "コーヒー派"
    with pytest.raises(ToolError, match="プロジェクト"):
        await tool.execute({"content": "x", "kind": "project"}, CTX)


# ---------- Perplexity ----------
def pplx(handler):
    return PerplexityClient("pk", "sonar-x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_research_parses_search_results():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "答え"}}],
            "search_results": [{"title": "記事A", "url": "https://a.example", "date": "2026-09-01"}]})

    r = await research_tool(pplx(handler)).execute({"query": "最新動向", "recency": "week"}, CTX)
    assert seen["auth"] == "Bearer pk" and seen["body"]["model"] == "sonar-x"
    assert seen["body"]["search_recency_filter"] == "week"
    assert "[1] 記事A https://a.example" in r.content and r.data["sources"][0]["date"] == "2026-09-01"


async def test_research_citations_fallback_and_errors():
    ok = pplx(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}],
                                                  "citations": ["https://c.example"]}))
    r = await research_tool(ok).execute({"query": "q"}, CTX)
    assert r.data["sources"][0]["url"] == "https://c.example"
    for status, msg in ((401, "APIキー"), (429, "上限"), (500, "HTTP 500")):
        with pytest.raises(ToolError, match=msg):
            await pplx(lambda r, s=status: httpx.Response(s, text="e")).ask("q")

    def boom(req):
        raise httpx.ConnectError("x")
    with pytest.raises(ToolError, match="接続"):
        await pplx(boom).ask("q")


# ---------- Claude ----------
class FakeStream:
    def __init__(self, msg, record, kw):
        self.msg, self.record, self.kw = msg, record, kw

    async def __aenter__(self):
        self.record.append(self.kw)
        if isinstance(self.msg, Exception):
            raise self.msg
        return self

    async def __aexit__(self, *a):
        return False

    async def get_final_message(self):
        return self.msg


class FakeAnthropic:
    def __init__(self, msg):
        self.calls = []
        mk = lambda kind: SimpleNamespace(stream=lambda **kw: FakeStream(msg, self.calls, {"_kind": kind, **kw}))
        self.messages = mk("std")
        self.beta = SimpleNamespace(messages=mk("beta"))


def claude_msg(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, model="claude-x", content=[SimpleNamespace(type="text", text=text)])


async def test_claude_writer_uses_fallback_and_effort(tmp_path):
    fake = FakeAnthropic(claude_msg("# 報告書\n本文"))
    tool = document_tool(ClaudeWriter("k", "claude-x", client=fake), tmp_path)
    r = await tool.execute({"title": "週次/報告:案", "instructions": "まとめて", "materials": "材料"}, CTX)
    call = fake.calls[0]
    assert call["_kind"] == "beta" and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"] == {"effort": "high"} and call["model"] == "claude-x"
    assert "材料" in call["messages"][0]["content"]
    saved = tmp_path / r.data["file"]
    assert saved.read_text(encoding="utf-8") == "# 報告書\n本文"
    assert "/" not in r.data["name"] and ":" not in r.data["name"]


async def test_claude_writer_without_fallback_and_code_fence(tmp_path):
    fake = FakeAnthropic(claude_msg("```python\nprint(1)\n```"))
    w = ClaudeWriter("k", "claude-x", fallback="off", effort="", client=fake)
    r = await document_tool(w, tmp_path).execute({"title": "t", "instructions": "i", "format": "python"}, CTX)
    assert fake.calls[0]["_kind"] == "std" and "output_config" not in fake.calls[0]
    assert (tmp_path / r.data["file"]).read_text(encoding="utf-8") == "print(1)"


@pytest.mark.parametrize("msg,match", [
    (claude_msg("", "refusal"), "辞退"),
    (claude_msg("   "), "空"),
    (anthropic.APIConnectionError(request=__import__("httpx2").Request("POST", "https://x")), "接続"),
])
async def test_claude_writer_errors(tmp_path, msg, match):
    with pytest.raises(ToolError, match=match):
        await ClaudeWriter("k", "m", client=FakeAnthropic(msg)).write("p")


async def test_claude_truncated_flag(tmp_path):
    w = ClaudeWriter("k", "m", client=FakeAnthropic(claude_msg("途中", "max_tokens")))
    r = await document_tool(w, tmp_path).execute({"title": "t", "instructions": "i"}, CTX)
    assert r.data["truncated"] and "途中終了" in r.summary


def test_save_output_never_overwrites(tmp_path):
    a = save_output(tmp_path, "同じ", ".md", "1")
    b = save_output(tmp_path, "同じ", ".md", "2")
    assert a != b and a.read_text(encoding="utf-8") == "1"
    assert slugify('a<b>:"c"/d') == "a_b_c_d" and slugify("...") == "output"


# ---------- OpenAI 互換のツール呼び出し ----------
async def test_openai_streaming_tool_calls_and_wire_format():
    seen = {}
    chunks = [
        {"choices": [{"delta": {"content": "調べます"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "research_web", "arguments": "{\"qu"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "ery\": \"天気\"}"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "id": "call_2", "function": {"name": "list_workspace_files", "arguments": "{}"}}]}}]},
    ]
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, text=sse)

    p = OpenAICompatProvider("http://x/v1", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    history = [ChatMessage("user", "q"),
               ChatMessage("assistant", "", tool_calls=(ToolCallRequest("old", "f", "{}"),)),
               ChatMessage("tool", "結果", tool_call_id="old")]
    out = [c async for c in p.stream(history, tools=[{"type": "function", "function": {"name": "f"}}])]
    assert out[0] == "調べます"
    assert out[1] == ToolCallRequest("call_1", "research_web", '{"query": "天気"}')
    assert out[2].name == "list_workspace_files"
    wire = seen["body"]["messages"]
    assert wire[1]["tool_calls"][0]["function"]["name"] == "f" and wire[1]["content"] is None
    assert wire[2] == {"role": "tool", "tool_call_id": "old", "content": "結果"}
    assert seen["body"]["tools"][0]["function"]["name"] == "f"


# ---------- 組み立て ----------
def test_build_registry_registers_only_configured(tmp_path):
    mem = MemoryService(MemoryStore(Database(":memory:")))
    base = {"BUDDY_WORKSPACE_DIR": str(tmp_path / "ws")}
    reg = build_registry(load_settings(env=base), mem)
    assert reg.names() == ["list_workspace_files", "propose_memory", "read_workspace_file"]
    # 任意の経路: 調査=Perplexity、作成=Claude API
    full = build_registry(load_settings(env={**base, "RESEARCH_PROVIDER": "perplexity", "WRITER_PROVIDER": "claude_api",
                                             "PERPLEXITY_API_KEY": "p", "PERPLEXITY_MODEL": "sonar",
                                             "ANTHROPIC_API_KEY": "a", "CLAUDE_MODEL": "claude-x"}), mem)
    assert {"research_web", "create_document"} <= set(full.names())
    assert full.get("research_web").external == "Perplexity"
    assert full.get("create_document").external == "Anthropic (Claude API)"
    assert full.get("create_document").level == Level.IMPORTANT
    # B案の既定: 作成・調査・画像とも Gemini
    b = build_registry(load_settings(env={**base, "GEMINI_API_KEY": "g", "GEMINI_TEXT_MODEL": "t",
                                          "GEMINI_IMAGE_MODEL": "i"}), mem)
    for name in ("create_document", "research_web", "generate_image"):
        assert b.get(name).external == "Google (Gemini)", name
    # Perplexity キーがあっても、担当が gemini なら Perplexity は使わない
    b2 = build_registry(load_settings(env={**base, "PERPLEXITY_API_KEY": "p", "PERPLEXITY_MODEL": "sonar"}), mem)
    assert "research_web" not in b2.names()
    pol = build_policy(load_settings(env={"TOOL_AUTO_APPROVE": "research_web, x"}))
    assert pol.auto_tools == frozenset({"research_web", "x"})


def test_registry_rejects_duplicates_and_bad_schema():
    reg = ToolRegistry()
    t = propose_memory_tool(MemoryService(MemoryStore(Database(":memory:"))))
    reg.register(t)
    with pytest.raises(ValueError):
        reg.register(t)


def test_outputs_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from buddy.api.app import create_app
    from buddy.llm.mock import MockProvider
    from .conftest import make_settings
    s = make_settings(tmp_path)
    (s.workspace_dir / "outputs").mkdir(parents=True)
    (s.workspace_dir / "outputs" / "r.md").write_text("中身", encoding="utf-8")
    (s.workspace_dir / "secret.txt").write_text("x", encoding="utf-8")
    with TestClient(create_app(s, MockProvider(), Database(":memory:"))) as c:
        r = c.get("/api/outputs/r.md")
        assert r.status_code == 200 and r.text == "中身"
        assert c.get("/api/outputs/none.md").status_code == 404
        assert c.get("/api/outputs/..").status_code in (400, 404)
        assert c.get("/api/outputs/..%2Fsecret.txt").status_code in (400, 404)


# ---------- Gemini(画像) ----------
import base64 as _b64

from buddy.tools.image import GeminiImageClient, image_tool

PNG = bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
                    "0000000D4944415478DA63F8CFC0F01F0005000201E2B2B3A40000000049454E44AE426082")


def gemini(handler):
    return GeminiImageClient("AIza-test", "gemini-img-x",
                             client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def gemini_ok(parts=None, **extra):
    parts = parts if parts is not None else [
        {"text": "描きました"}, {"inlineData": {"mimeType": "image/png", "data": _b64.b64encode(PNG).decode()}}]
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}], **extra}


async def test_gemini_request_matches_official_sdk_and_saves(tmp_path):
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), key=req.headers.get("x-goog-api-key"), body=json.loads(req.content))
        return httpx.Response(200, json=gemini_ok())

    r = await image_tool(gemini(handler), tmp_path).execute(
        {"prompt": "夕焼けの猫", "aspect_ratio": "16:9", "title": "猫/夕焼け"}, CTX)
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-img-x:generateContent"
    assert seen["key"] == "AIza-test"
    # 公式 SDK(google-genai 2.28)が送る形と同じ構造
    assert seen["body"]["contents"] == [{"role": "user", "parts": [{"text": "夕焼けの猫"}]}]
    assert seen["body"]["generationConfig"]["imageConfig"] == {"aspectRatio": "16:9"}
    assert "IMAGE" in seen["body"]["generationConfig"]["responseModalities"]
    saved = tmp_path / r.data["file"]
    assert saved.read_bytes() == PNG and saved.suffix == ".png" and r.data["kind"] == "image"
    assert "/" not in r.data["name"] and "描きました" in r.content


async def test_gemini_without_aspect_ratio_omits_image_config():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=gemini_ok())

    await gemini(handler).generate("x")
    assert "imageConfig" not in seen["body"]["generationConfig"]


@pytest.mark.parametrize("payload,match", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "拒否.*SAFETY"),
    (gemini_ok(parts=[{"text": "描けません"}]), "画像を返しませんでした.*描けません"),
    ({"candidates": [{"content": {"parts": []}, "finishReason": "IMAGE_SAFETY"}]}, "IMAGE_SAFETY"),
    (gemini_ok(parts=[{"inlineData": {"mimeType": "image/png", "data": "@@not-base64@@"}}]), "壊れて"),
])
async def test_gemini_no_image_cases(payload, match):
    with pytest.raises(ToolError, match=match):
        await gemini(lambda r: httpx.Response(200, json=payload)).generate("x")


@pytest.mark.parametrize("status,match", [(403, "APIキー"), (404, "gemini-img-x"), (429, "上限"), (500, "HTTP 500")])
async def test_gemini_http_errors(status, match):
    with pytest.raises(ToolError, match=match):
        await gemini(lambda r: httpx.Response(status, text="e")).generate("x")


async def test_gemini_unexpected_mime_rejected(tmp_path):
    payload = gemini_ok(parts=[{"inlineData": {"mimeType": "image/svg+xml", "data": _b64.b64encode(b"<svg/>").decode()}}])
    with pytest.raises(ToolError, match="形式"):
        await image_tool(gemini(lambda r: httpx.Response(200, json=payload)), tmp_path).execute({"prompt": "x"}, CTX)


def test_build_registry_registers_gemini(tmp_path):
    mem = MemoryService(MemoryStore(Database(":memory:")))
    reg = build_registry(load_settings(env={"BUDDY_WORKSPACE_DIR": str(tmp_path),
                                            "GEMINI_API_KEY": "g", "GEMINI_IMAGE_MODEL": "m"}), mem)
    t = reg.get("generate_image")
    assert t and t.external == "Google (Gemini)" and t.level == Level.IMPORTANT and t.node == "image"


# ---------- Gemini: 会話(OpenAI 互換窓口)・文書作成・調査 ----------
from buddy.llm.registry import build_providers
from buddy.tools.gemini_api import GeminiAPI
from buddy.tools.gemini_text import GeminiResearch, GeminiWriter
from buddy.tools.research import research_tool as _research_tool


def test_gemini_conversation_preset():
    from buddy.config import ConfigError
    p = build_providers(load_settings(env={"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "gk", "LLM_MODEL": "gem-fast"}))
    fast = p["fast"]
    assert fast.name == "gemini" and fast.sends_data_externally and fast.model == "gem-fast"
    assert fast._base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert fast._headers()["Authorization"] == "Bearer gk"
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        build_providers(load_settings(env={"LLM_PROVIDER": "gemini", "LLM_MODEL": "m"}))


def gapi(handler):
    return GeminiAPI("AIza-t", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_gemini_writer_wire_format_and_save(tmp_path):
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), body=json.loads(req.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "# 企画書\n本文"}]},
                                                         "finishReason": "STOP"}]})

    r = await document_tool(GeminiWriter(gapi(handler), "gem-text"), tmp_path).execute(
        {"title": "企画", "instructions": "まとめて", "materials": "材料X"}, CTX)
    assert seen["url"].endswith("/v1beta/models/gem-text:generateContent")
    # 公式 SDK と同じ形(systemInstruction は role:user + parts)
    assert seen["body"]["systemInstruction"]["role"] == "user"
    assert "成果物の作成担当" in seen["body"]["systemInstruction"]["parts"][0]["text"]
    assert "材料X" in seen["body"]["contents"][0]["parts"][0]["text"]
    assert (tmp_path / r.data["file"]).read_text(encoding="utf-8") == "# 企画書\n本文"
    assert r.brief.startswith("outputs/") and "冒頭: # 企画書 本文" in r.brief  # 司令塔には本文全体を返さない


async def test_gemini_writer_truncated_and_empty():
    w = GeminiWriter(gapi(lambda r: httpx.Response(200, json={"candidates": [
        {"content": {"parts": [{"text": "途中"}]}, "finishReason": "MAX_TOKENS"}]})), "m")
    assert (await w.write("p")).truncated
    empty = GeminiWriter(gapi(lambda r: httpx.Response(200, json={"candidates": [
        {"content": {"parts": []}, "finishReason": "SAFETY"}]})), "m")
    with pytest.raises(ToolError, match="SAFETY"):
        await empty.write("p")


async def test_gemini_research_grounding():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"candidates": [{
            "content": {"parts": [{"text": "結論です"}]}, "finishReason": "STOP",
            "groundingMetadata": {"webSearchQueries": ["q"], "groundingChunks": [
                {"web": {"uri": "https://a.example/x", "title": "a.example"}},
                {"web": {"uri": "https://a.example/x", "title": "dup"}},
                {"web": {"uri": "https://b.example/y", "title": "b.example"}}]}}]})

    tool = _research_tool(GeminiResearch(gapi(handler), "gem-text"))
    r = await tool.execute({"query": "最新の動向", "recency": "week"}, CTX)
    assert seen["body"]["tools"] == [{"googleSearch": {}}]  # 公式 SDK と同じ形
    assert "直近1週間" in seen["body"]["contents"][0]["parts"][0]["text"]
    assert [s["url"] for s in r.data["sources"]] == ["https://a.example/x", "https://b.example/y"]
    assert tool.external == "Google (Gemini)" and "[1] a.example" in r.content


async def test_gemini_list_models():
    def handler(req):
        assert req.url.path == "/v1beta/models" and req.headers["x-goog-api-key"] == "AIza-t"
        return httpx.Response(200, json={"models": [{"name": "models/g1", "supportedGenerationMethods": ["generateContent"]}]})
    assert (await gapi(handler).list_models())[0]["name"] == "models/g1"


def test_settings_reject_unknown_providers():
    from buddy.config import ConfigError
    for k in ("WRITER_PROVIDER", "RESEARCH_PROVIDER", "ORCHESTRATOR"):
        with pytest.raises(ConfigError, match=k):
            load_settings(env={k: "nope"})
