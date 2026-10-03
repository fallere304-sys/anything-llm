from fastapi.testclient import TestClient

from buddy.activity import describe_nodes
from buddy.api.app import create_app
from buddy.llm.mock import MockProvider
from buddy.storage.db import Database
from buddy.tts.mock import MockTTSProvider

from .conftest import make_settings, parse_sse
from .test_api import FailingProvider


def acts(events):
    return [e for e in events if e["type"] == "activity"]


def test_success_flow_emits_real_accesses(client):
    cid = client.post("/api/conversations", json={}).json()["id"]
    ev = parse_sse(client.post(f"/api/conversations/{cid}/messages", json={"content": "秘密の内容"}).text)
    a = acts(ev)
    assert [(x["target"], x["phase"]) for x in a] == [
        ("persona", "pulse"), ("memory", "pulse"), ("history", "pulse"),
        ("llm:fast", "start"), ("llm:fast", "end")]
    # 可視化パネルの処理ログは英語
    assert a[1]["summary"] == "Memory search: no match"
    assert a[2]["summary"] == "Context: 1 message"
    assert a[3]["id"] == a[4]["id"] and a[4]["ok"] is True and a[4]["summary"].startswith("Response ")
    assert all("秘密の内容" not in x["summary"] for x in a)  # 本文は要約に含めない
    # activity は delta より前に開始、done より前に終了
    types = [e["type"] for e in ev]
    assert types.index("activity") < types.index("delta") and types[-1] == "done"


def test_history_count_and_drop_note(tmp_path):
    app = create_app(make_settings(tmp_path, max_context_chars=600), MockProvider(), Database(":memory:"))
    with TestClient(app) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        for i in range(4):
            ev = parse_sse(c.post(f"/api/conversations/{cid}/messages", json={"content": f"{i}" * 300}).text)
        hist = next(x for x in acts(ev) if x["target"] == "history")
        assert "older trimmed" in hist["summary"]


def test_failure_ends_activity_not_ok(tmp_path):
    app = create_app(make_settings(tmp_path), FailingProvider(), Database(":memory:"))
    with TestClient(app) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        a = acts(parse_sse(c.post(f"/api/conversations/{cid}/messages", json={"content": "x"}).text))
        assert a[-1]["target"] == "llm:fast" and a[-1]["phase"] == "end" and a[-1]["ok"] is False


def test_nodes_reflect_real_availability():
    nodes = {n["id"]: n for n in describe_nodes({"fast": MockProvider()}, None)}
    assert nodes["persona"]["available"] and nodes["llm:fast"]["available"]
    assert not nodes["llm:strong"]["available"] and not nodes["tts"]["available"]
    assert nodes["memory"]["available"] and nodes["project"]["available"]
    # ツール未登録なら外部連携・作業フォルダは利用不可
    for nid in ("research", "claude", "image", "files"):
        assert nodes[nid]["available"] is False, nid
    # 可視化の表記は英語のみ(日本語を含まない)
    import re
    for n in nodes.values():
        assert not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", n["short"] + n["label"] + n["description"]), n
    with_tts = {n["id"]: n for n in describe_nodes({"fast": MockProvider()}, MockTTSProvider())}
    assert with_tts["tts"]["available"]


def test_status_and_static_assets(client):
    st = client.get("/api/status").json()
    assert {n["id"] for n in st["nodes"]} >= {"persona", "history", "llm:fast", "memory"}
    for f in ("app.js", "viz.js", "memory.js", "agent.js", "style.css"):
        assert client.get(f"/static/{f}").status_code == 200, f
