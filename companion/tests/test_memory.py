import pytest
from fastapi.testclient import TestClient

from buddy.api.app import create_app
from buddy.llm.base import LLMProvider
from buddy.memory.retrieval import bigrams, relevance
from buddy.memory.service import MemoryService, MemoryValidationError
from buddy.memory.store import MemoryStore, ProjectInUse
from buddy.storage.db import Database, NotFound

from .conftest import make_settings, parse_sse


@pytest.fixture
def mem():
    return MemoryService(MemoryStore(Database(":memory:")), max_chars=3000)


# ---------- 関連度 ----------
def test_bigram_relevance_japanese():
    assert bigrams("AI 相棒!") == {"ai", "i相", "相棒"}
    assert relevance("好きな飲み物は?", "ユーザーの好きな飲み物はコーヒー") > relevance("好きな飲み物は?", "締切は金曜")
    assert relevance("", "x") == 0.0


# ---------- ルール ----------
def test_user_save_is_active_and_logged(mem):
    m = mem.save("名前は太郎", important=True)
    assert m["status"] == "active" and m["source"] == "user" and m["important"] is True
    assert m["confidence"] == 1.0 and m["created_at"] and m["updated_at"]
    assert mem.store.list_log()[0]["action"] == "memory_save"


@pytest.mark.parametrize("kw,msg", [
    ({"content": "  "}, "空"),
    ({"content": "x" * 2001}, "2000"),
    ({"content": "a", "kind": "project"}, "プロジェクトの指定"),
    ({"content": "a", "kind": "project", "project_id": "nope"}, "存在しません"),
    ({"content": "a", "project_id": "x"}, "長期記憶にプロジェクト"),
    ({"content": "a", "confidence": 1.5}, "信頼度"),
    ({"content": "a", "kind": "weird"}, "不明"),
    ({"content": "a", "source": "ai_proposal"}, "情報源"),
])
def test_validation(mem, kw, msg):
    content = kw.pop("content")
    with pytest.raises(MemoryValidationError, match=msg):
        mem.save(content, **kw)


def test_ai_proposal_is_pending_and_excluded_until_approved(mem):
    p = mem.propose("ユーザーは猫が好き", confidence=0.6, conversation_id="c1")
    assert p["status"] == "pending" and p["source"] == "ai_proposal"
    assert mem.retrieve("猫").items == []  # 承認前は文脈に入らない
    mem.approve(p["id"])
    assert [m["id"] for m in mem.retrieve("猫").items] == [p["id"]]
    with pytest.raises(MemoryValidationError):
        mem.approve(p["id"])  # 二重承認不可
    actions = [l["action"] for l in mem.store.list_log()]
    assert actions[:2] == ["memory_approve", "memory_propose"]
    assert mem.store.list_log()[1]["actor"] == "ai"


def test_reject_deletes(mem):
    p = mem.propose("x")
    mem.reject(p["id"])
    with pytest.raises(NotFound):
        mem.store.get_memory(p["id"])


def test_update_and_delete(mem):
    m = mem.save("古い内容")
    u = mem.update(m["id"], content="新しい内容", important=True, confidence=0.5)
    assert (u["content"], u["important"], u["confidence"]) == ("新しい内容", True, 0.5)
    with pytest.raises(MemoryValidationError):
        mem.update(m["id"], confidence=2)
    mem.delete(m["id"])
    with pytest.raises(NotFound):
        mem.delete(m["id"])


# ---------- 取り込み ----------
def test_retrieval_order_scope_and_budget(mem):
    pj = mem.store.create_project("家計")
    other = mem.store.create_project("仕事")
    rel = mem.save("好きな飲み物はコーヒー")
    unrel = mem.save("締切は金曜")
    imp = mem.save("持病がある", important=True)
    pmem = mem.save("予算は月5万円", kind="project", project_id=pj["id"])
    mem.save("別PJの情報", kind="project", project_id=other["id"])
    r = mem.retrieve("飲み物を教えて", pj["id"])
    ids = [m["id"] for m in r.items]
    assert ids == [imp["id"], pmem["id"], rel["id"], unrel["id"]]  # 重要→PJ→関連度→その他
    assert r.project_name == "家計" and r.count("project") == 1 and r.important == 1
    # 予算: 小さくすると後ろから落ちる
    small = MemoryService(mem.store, max_chars=100)
    r2 = small.retrieve("飲み物を教えて", pj["id"])
    assert r2.items[0]["id"] == imp["id"] and r2.dropped >= 1
    # プロジェクト外では PJ 記憶は出ない
    assert all(m["kind"] == "long_term" for m in mem.retrieve("x").items)


def test_format_for_prompt(mem):
    m = mem.save("改行\nを含む", important=True)
    text = mem.format_for_prompt([m])
    assert f"#{m['id'][:8]} [重要] (長期記憶 / 信頼度1.0" in text and "改行 を含む" in text
    assert "ユーザーに確認" in text


def test_project_delete_guard(mem):
    pj = mem.store.create_project("p")
    m = mem.save("a", kind="project", project_id=pj["id"])
    with pytest.raises(ProjectInUse):
        mem.store.delete_project(pj["id"])
    mem.delete(m["id"])
    mem.store.delete_project(pj["id"])


# ---------- チャットとの統合 ----------
class CaptureProvider(LLMProvider):
    name, model = "cap", "cap-1"

    def __init__(self):
        self.seen = []

    async def stream(self, messages, **kw):
        self.seen.append(messages)
        yield "ok"


def test_chat_injects_memories_and_logs(tmp_path):
    cap = CaptureProvider()
    app = create_app(make_settings(tmp_path), cap, Database(":memory:"))
    with TestClient(app) as c:
        pj = c.post("/api/projects", json={"name": "AI相棒開発"}).json()
        c.post("/api/memories", json={"content": "ユーザーは猫を飼っている"})
        c.post("/api/memories", json={"content": "UIは緑基調", "kind": "project", "project_id": pj["id"]})
        pend = c.post("/api/memories", json={"content": "x"}).json()
        c.app.state.memory.propose("承認待ちの内容")  # AI提案(未承認)
        cid = c.post("/api/conversations", json={}).json()["id"]
        assert c.patch(f"/api/conversations/{cid}", json={"project_id": pj["id"]}).json()["project_id"] == pj["id"]
        ev = parse_sse(c.post(f"/api/conversations/{cid}/messages", json={"content": "猫の話"}).text)
        system = cap.seen[0][0].content
        assert "ユーザーは猫を飼っている" in system and "UIは緑基調" in system
        assert "承認待ちの内容" not in system
        acts = {e["target"]: e["summary"] for e in ev if e["type"] == "activity"}
        assert acts["memory"] == "Recalled 2 memories"
        assert acts["project"] == 'Project "AI相棒開発": recalled 1'
        log = c.get("/api/worklog").json()
        assert log[0]["action"] == "llm_call" and log[0]["actor"] == "ai" and log[0]["conversation_id"] == cid
        # 紐付け解除
        assert c.patch(f"/api/conversations/{cid}", json={"project_id": None}).json()["project_id"] is None
        assert c.patch(f"/api/conversations/{cid}", json={"project_id": "nope"}).status_code == 404


# ---------- API ----------
def test_memory_api(client):
    r = client.post("/api/memories", json={"content": "a", "kind": "project"})
    assert r.status_code == 422
    m = client.post("/api/memories", json={"content": "好物は寿司", "important": True}).json()
    assert client.get("/api/memories?important=true").json()[0]["id"] == m["id"]
    assert client.patch(f"/api/memories/{m['id']}", json={"content": "好物はラーメン"}).json()["content"] == "好物はラーメン"
    assert client.patch(f"/api/memories/{m['id']}", json={"confidence": 3}).status_code == 422
    assert client.delete(f"/api/memories/{m['id']}").status_code == 204
    assert client.delete(f"/api/memories/{m['id']}").status_code == 404
    p = client.app.state.memory.propose("提案")
    assert [x["id"] for x in client.get("/api/memories?status=pending").json()] == [p["id"]]
    assert client.post(f"/api/memories/{p['id']}/approve").json()["status"] == "active"
    assert client.post(f"/api/memories/{p['id']}/approve").status_code == 422


def test_remember_from_message(client):
    cid = client.post("/api/conversations", json={}).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"content": "私の誕生日は5月1日"})
    mid = client.get(f"/api/conversations/{cid}/messages").json()[0]["id"]
    m = client.post("/api/memories", json={"content": "誕生日は5月1日", "source_message_id": mid}).json()
    assert m["source"] == "conversation" and m["source_ref"] == f"message:{mid}"
    assert client.post("/api/memories", json={"content": "x", "source_message_id": 99999}).status_code == 404


def test_project_api(client):
    p = client.post("/api/projects", json={"name": "家計"}).json()
    assert client.post("/api/projects", json={"name": "家計"}).status_code == 409
    assert client.post("/api/projects", json={"name": "   "}).status_code == 422
    client.post("/api/memories", json={"content": "a", "kind": "project", "project_id": p["id"]})
    assert client.get("/api/projects").json()[0]["memory_count"] == 1
    assert client.delete(f"/api/projects/{p['id']}").status_code == 409
    assert client.patch(f"/api/projects/{p['id']}", json={"name": "家計簿"}).json()["name"] == "家計簿"


def test_memory_endpoints_require_auth(tmp_path):
    from buddy.llm.mock import MockProvider
    app = create_app(make_settings(tmp_path, host="0.0.0.0", access_token="tok-abcdef"), MockProvider(), Database(":memory:"))
    with TestClient(app) as c:
        for method, path in (("get", "/api/memories"), ("post", "/api/memories"), ("get", "/api/projects"),
                             ("get", "/api/worklog"), ("patch", "/api/conversations/x")):
            assert getattr(c, method)(path, **({"json": {}} if method != "get" else {})).status_code == 401, path


def test_settings_memory_budget_validation():
    from buddy.config import ConfigError, load_settings
    with pytest.raises(ConfigError):
        load_settings(env={"BUDDY_MAX_CONTEXT_CHARS": "1000", "BUDDY_MAX_MEMORY_CHARS": "1000"})
