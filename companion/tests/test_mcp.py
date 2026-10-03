"""司令塔用 MCP エンドポイントのテスト。"""
import threading

from fastapi.testclient import TestClient

from buddy.api.app import create_app
from buddy.llm.mock import MockProvider
from buddy.storage.db import Database
from buddy.tools.registry import Level, ToolContext, ToolRegistry

from .conftest import make_settings
from .test_agent import make_tool


def setup(tmp_path, tools):
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    app = create_app(make_settings(tmp_path), MockProvider(), Database(":memory:"), registry=reg)
    return app


def rpc(c, run, method, params=None, rid=1, token=None):
    body = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
    if rid is not None:
        body["id"] = rid
    return c.post(f"/mcp/{run.id}", json=body, headers={"Authorization": f"Bearer {token or run.token}"})


def new_run(app, events):
    cid = app.state.service.db.create_conversation("t")["id"]
    return app.state.runs.create(ToolContext(conversation_id=cid, emit=events.append, origin="orchestrator"))


def test_auth_and_protocol(tmp_path):
    app = setup(tmp_path, [make_tool("look", Level.READ)])
    with TestClient(app) as c:
        run = new_run(app, [])
        other = new_run(app, [])
        assert c.post(f"/mcp/{run.id}", json={}).status_code == 401
        assert rpc(c, run, "ping", token="wrong").status_code == 401
        assert rpc(c, run, "ping", token=other.token).status_code == 401  # 別実行のトークンは不可
        assert c.post("/mcp/nope", json={}, headers={"Authorization": "Bearer x"}).status_code == 401
        init = rpc(c, run, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}).json()
        assert init["result"]["protocolVersion"] == "2025-06-18"
        assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
        old = rpc(c, run, "initialize", {"protocolVersion": "1999-01-01"}).json()
        assert old["result"]["protocolVersion"] == "2025-06-18"
        assert rpc(c, run, "notifications/initialized", rid=None).status_code == 202
        assert rpc(c, run, "nope").json()["error"]["code"] == -32601
        assert c.get(f"/mcp/{run.id}").status_code == 405
        bad = c.post(f"/mcp/{run.id}", content=b"{", headers={"Authorization": f"Bearer {run.token}", "Content-Type": "application/json"})
        assert bad.status_code == 400


def test_tools_list_hides_delegate_and_includes_purpose(tmp_path):
    hidden = make_tool("delegate_task", Level.IMPORTANT)
    app = setup(tmp_path, [make_tool("look", Level.READ), hidden])
    with TestClient(app) as c:
        run = new_run(app, [])
        tools = rpc(c, run, "tools/list").json()["result"]["tools"]
        assert [t["name"] for t in tools] == ["look"]
        assert "purpose" in tools[0]["inputSchema"]["required"]
        r = rpc(c, run, "tools/call", {"name": "delegate_task", "arguments": {"q": "x"}}).json()
        assert r["error"]["code"] == -32602


def test_tools_call_runs_through_policy_and_relays_events(tmp_path):
    seen, events = [], []
    app = setup(tmp_path, [make_tool("look", Level.READ, seen=seen)])
    with TestClient(app) as c:
        run = new_run(app, events)
        r = rpc(c, run, "tools/call", {"name": "look", "arguments": {"q": "abc", "purpose": "確認"}}).json()
        assert r["result"] == {"content": [{"type": "text", "text": "look:OK"}], "isError": False}
        assert seen == [{"q": "abc"}]
        kinds = [e["type"] for e in events]
        assert "activity" in kinds and "tool_result" in kinds and "status" in kinds
        assert all(e["via"] == "orchestrator" for e in events)
        assert run.records[0]["tool"] == "look" and run.records[0]["ok"]
        # 引数エラーは isError で返す
        r = rpc(c, run, "tools/call", {"name": "look", "arguments": {}}).json()
        assert r["result"]["isError"] and "必須" in r["result"]["content"][0]["text"]
        log = c.get("/api/worklog").json()
        assert any(l["action"] == "tool_call" and l["target"] == "look" for l in log)


def test_tools_call_waits_for_human_approval(tmp_path):
    seen, events = [], []
    app = setup(tmp_path, [make_tool("send", Level.IMPORTANT, external="Google (Gemini)", seen=seen)])
    with TestClient(app) as c:
        run = new_run(app, events)
        out = {}
        th = threading.Thread(target=lambda: out.update(r=rpc(c, run, "tools/call", {"name": "send", "arguments": {"q": "x", "purpose": "作成"}}).json()))
        th.start()
        for _ in range(200):
            pend = c.get("/api/approvals").json()
            if pend:
                break
            threading.Event().wait(0.02)
        assert pend and pend[0]["tool"] == "send" and pend[0]["purpose"] == "作成"
        assert any(e["type"] == "approval_request" for e in events)  # 承認カードが親へ中継される
        c.post(f"/api/approvals/{pend[0]['id']}", json={"approve": False})
        th.join(10)
        assert seen == [] and out["r"]["result"]["isError"]
        assert "承認しませんでした" in out["r"]["result"]["content"][0]["text"]


def test_call_limit(tmp_path):
    app = setup(tmp_path, [make_tool("look", Level.READ)])
    app.state.runs.MAX_CALLS = 2
    with TestClient(app) as c:
        run = new_run(app, [])
        for _ in range(2):
            assert not rpc(c, run, "tools/call", {"name": "look", "arguments": {"q": "a"}}).json()["result"]["isError"]
        r = rpc(c, run, "tools/call", {"name": "look", "arguments": {"q": "a"}}).json()
        assert r["result"]["isError"] and "上限" in r["result"]["content"][0]["text"]


def test_brief_is_returned_instead_of_full_content(tmp_path):
    from buddy.tools.registry import Tool, ToolResult

    async def run_(args, ctx):
        return ToolResult(content="本文" * 5000, summary="s", brief="outputs/x.md に保存")
    tool = Tool(name="mk", description="d", input_schema={"type": "object", "properties": {}}, level=Level.READ,
                execute=run_, node="create")
    app = setup(tmp_path, [tool])
    with TestClient(app) as c:
        run = new_run(app, [])
        r = rpc(c, run, "tools/call", {"name": "mk", "arguments": {}}).json()
        assert r["result"]["content"][0]["text"] == "outputs/x.md に保存"
