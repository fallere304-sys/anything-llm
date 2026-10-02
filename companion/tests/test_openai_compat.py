import json

import httpx
import pytest

from buddy.llm.base import ChatMessage, LLMError
from buddy.llm.openai_compat import OpenAICompatProvider


def sse(*chunks, done=True):
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


def provider(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider("http://x/v1", kw.pop("model", "m1"), client=client, **kw)


MSGS = [ChatMessage("user", "hi")]


async def test_streams_and_sends_payload_and_auth():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, text=sse("こん", "にちは"))

    p = provider(handler, api_key="k123")
    assert await p.complete(MSGS) == "こんにちは"
    assert seen["body"]["model"] == "m1" and seen["body"]["stream"] is True
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert seen["auth"] == "Bearer k123"


async def test_http_error_becomes_llmerror():
    p = provider(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(LLMError, match="HTTP 500"):
        await p.complete(MSGS)


async def test_connect_error_message():
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="接続できません"):
        await provider(handler).complete(MSGS)


async def test_malformed_chunk():
    p = provider(lambda r: httpx.Response(200, text="data: {not json}\n\n"))
    with pytest.raises(LLMError, match="解釈できません"):
        await p.complete(MSGS)


def test_model_required():
    with pytest.raises(LLMError):
        OpenAICompatProvider("http://x/v1", "")


def test_external_flag():
    assert not OpenAICompatProvider("http://localhost:11434/v1", "m").sends_data_externally
    assert OpenAICompatProvider("https://api.openai.com/v1", "m").sends_data_externally


async def test_health():
    assert (await provider(lambda r: httpx.Response(200, json={"data": []})).health())[0] is True
    assert (await provider(lambda r: httpx.Response(401)).health())[0] is False
