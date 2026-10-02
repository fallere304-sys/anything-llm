from fastapi.testclient import TestClient

from buddy.api.app import create_app
from buddy.llm.base import LLMError, LLMProvider
from buddy.llm.mock import MockProvider
from buddy.storage.db import Database

from .conftest import make_settings, parse_sse


def chat(client, cid, text, **kw):
    r = client.post(f"/api/conversations/{cid}/messages", json={"content": text}, **kw)
    return r, (parse_sse(r.text) if r.status_code == 200 else None)


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "AI相棒" in r.text


def test_full_chat_flow_and_persistence(client):
    cid = client.post("/api/conversations", json={}).json()["id"]
    r, ev = chat(client, cid, "こんにちは")
    types = [e["type"] for e in ev]
    assert types[0] == "status" and ev[0]["state"] == "thinking"
    assert "delta" in types and types[-1] == "done"
    text = "".join(e["text"] for e in ev if e["type"] == "delta")
    assert "こんにちは" in text and ev[-1]["message"]["content"] == text
    msgs = client.get(f"/api/conversations/{cid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    # 初回発言から自動タイトル
    assert client.get("/api/conversations").json()[0]["title"] == "こんにちは"
    # 2ターン目は履歴を認識(mockはユーザー発言件数を返す)
    _, ev2 = chat(client, cid, "二回目")
    assert "ユーザー発言: 2件" in ev2[-1]["message"]["content"]
    assert client.get("/api/status").json()["state"] == "idle"


def test_validation_and_404(client):
    cid = client.post("/api/conversations", json={}).json()["id"]
    assert client.post(f"/api/conversations/{cid}/messages", json={"content": ""}).status_code == 422
    assert client.post(f"/api/conversations/{cid}/messages", json={"content": "   "}).status_code == 422
    assert client.post(f"/api/conversations/{cid}/messages", json={"content": "x" * 9000}).status_code == 422
    assert client.post("/api/conversations/nope/messages", json={"content": "a"}).status_code == 404
    assert client.get("/api/conversations/nope/messages").status_code == 404
    assert client.delete("/api/conversations/nope").status_code == 404


def test_delete(client):
    cid = client.post("/api/conversations", json={}).json()["id"]
    assert client.delete(f"/api/conversations/{cid}").status_code == 204
    assert client.get("/api/conversations").json() == []


class FailingProvider(LLMProvider):
    name, model = "fail", "f"

    async def stream(self, messages, **kw):
        raise LLMError("LLM に接続できません")
        yield  # pragma: no cover


def test_llm_error_is_surfaced_not_swallowed(tmp_path):
    app = create_app(make_settings(tmp_path), FailingProvider(), Database(":memory:"))
    with TestClient(app) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        _, ev = chat(c, cid, "hi")
        assert ev[-1] == {"type": "error", "message": "LLM に接続できません"}
        st = c.get("/api/status").json()
        assert st["state"] == "error" and "接続できません" in st["last_error"]
        # 失敗した応答は保存されない(ユーザー発言のみ)
        assert [m["role"] for m in c.get(f"/api/conversations/{cid}/messages").json()] == ["user"]


def test_auth_enforced_when_token_set(tmp_path):
    s = make_settings(tmp_path, host="0.0.0.0", access_token="s3cret-token")
    app = create_app(s, MockProvider(), Database(":memory:"))
    with TestClient(app) as c:
        assert c.get("/").status_code == 200  # 静的UIはデータを含まない
        assert c.get("/api/conversations").status_code == 401
        assert c.get("/api/conversations", headers={"X-Buddy-Token": "wrong"}).status_code == 401
        assert c.get("/api/conversations", headers={"X-Buddy-Token": "s3cret-token"}).status_code == 200
        assert c.post("/api/conversations", json={}).status_code == 401


def test_context_truncation_reported(tmp_path):
    s = make_settings(tmp_path, max_context_chars=600)
    app = create_app(s, MockProvider(), Database(":memory:"))
    with TestClient(app) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        for i in range(4):
            _, ev = chat(c, cid, f"{i}" * 300)
        assert ev[0]["dropped_history"] > 0


def test_profiles_select_model_and_reject_unknown(tmp_path):
    from buddy.llm.mock import MockProvider as M

    app = create_app(make_settings(tmp_path), {"fast": M(model="f1"), "strong": M(model="s1")}, Database(":memory:"))
    with TestClient(app) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        _, ev = chat(c, cid, "a")
        assert ev[0]["profile"] == "fast" and ev[-1]["message"]["model"] == "f1"
        r = c.post(f"/api/conversations/{cid}/messages", json={"content": "b", "profile": "strong"})
        ev2 = parse_sse(r.text)
        assert ev2[0]["model"] == "s1" and ev2[-1]["message"]["model"] == "s1"
        assert c.post(f"/api/conversations/{cid}/messages", json={"content": "c", "profile": "x"}).status_code == 400
        st = c.get("/api/status").json()
        assert set(st["profiles"]) == {"fast", "strong"} and st["profiles"]["strong"]["model"] == "s1"
