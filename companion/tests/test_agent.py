"""エージェントループ・権限・承認のテスト(スクリプト化した偽 LLM で検証)。"""
import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from buddy.agent.approvals import ApprovalBroker
from buddy.api.app import create_app
from buddy.llm.base import LLMProvider, ToolCallRequest
from buddy.storage.db import Database
from buddy.tools.permissions import PermissionPolicy
from buddy.tools.registry import Level, Tool, ToolError, ToolRegistry, ToolResult

from .conftest import make_settings, parse_sse


class ScriptedLLM(LLMProvider):
    """呼ばれるたびに script の次の手を返す。tools と messages を記録する。"""
    name, model, supports_tools = "scripted", "s-1", True

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def stream(self, messages, tools=None, **kw):
        self.calls.append({"messages": messages, "tools": tools})
        step = self.script.pop(0) if self.script else {"text": "おわり"}
        if step.get("text"):
            yield step["text"]
        for i, (name, args) in enumerate(step.get("tools", [])):
            yield ToolCallRequest(id=f"c{len(self.calls)}_{i}", name=name,
                                  arguments=args if isinstance(args, str) else json.dumps(args, ensure_ascii=False))


def make_tool(name, level, *, external=None, result="OK", fail=None, sleep=0, seen=None):
    async def run(args, ctx):
        if seen is not None:
            seen.append(args)
        if sleep:
            await asyncio.sleep(sleep)
        if fail:
            raise fail
        return ToolResult(content=f"{name}:{result}", summary=f"{name} 完了", data={"x": 1})
    return Tool(name=name, description=f"{name} tool",
                input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
                level=level, execute=run, node="files", external=external, preview=lambda a: a.get("q", ""))


def build(tmp_path, llm, tools, **settings_kw):
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    app = create_app(make_settings(tmp_path, **settings_kw), llm, Database(":memory:"), registry=reg)
    return app


def run_chat(client, text="やって"):
    cid = client.post("/api/conversations", json={}).json()["id"]
    r = client.post(f"/api/conversations/{cid}/messages", json={"content": text})
    return cid, parse_sse(r.text)


# ---------- 権限ポリシー(単体) ----------
def test_policy_rules():
    p = PermissionPolicy()  # 既定: Lv1 まで自動
    t = lambda lvl, ext=None, name="t": make_tool(name, lvl, external=ext)
    assert not p.requires_approval(t(Level.READ))
    assert p.requires_approval(t(Level.REVERSIBLE))
    assert p.requires_approval(t(Level.READ, ext="X"))  # 外部送信は低レベルでも確認
    assert p.requires_approval(t(Level.IRREVERSIBLE))
    auto = PermissionPolicy(Level.IMPORTANT, frozenset({"ext_ok", "danger"}))
    assert not auto.requires_approval(t(Level.IMPORTANT, ext="X", name="ext_ok"))
    assert auto.requires_approval(t(Level.IRREVERSIBLE, name="danger"))  # Lv4 は許可リストでも確認
    with pytest.raises(ValueError):
        PermissionPolicy(Level.IRREVERSIBLE)


def test_policy_is_immutable():
    p = PermissionPolicy()
    with pytest.raises(Exception):
        p.auto_max_level = Level.IMPORTANT  # 実行中に権限を上げられない


def test_settings_reject_lv4_auto():
    from buddy.config import ConfigError, load_settings
    with pytest.raises(ConfigError):
        load_settings(env={"TOOL_AUTO_APPROVE_MAX_LEVEL": "4"})


# ---------- ループ ----------
def test_auto_tool_runs_and_result_goes_back_to_llm(tmp_path):
    seen = []
    llm = ScriptedLLM([{"text": "調べます", "tools": [("look", {"q": "abc", "purpose": "確認のため"})]},
                       {"text": "結果はOKでした"}])
    with TestClient(build(tmp_path, llm, [make_tool("look", Level.READ, seen=seen)])) as c:
        cid, ev = run_chat(c)
        assert seen == [{"q": "abc"}]  # purpose は取り除かれて実行される
        tr = [e for e in ev if e["type"] == "tool_result"]
        assert tr[0]["ok"] and tr[0]["summary"] == "look 完了"
        assert not [e for e in ev if e["type"] == "approval_request"]
        # 2回目の LLM 呼び出しには assistant(tool_calls) と tool 結果が入っている
        second = llm.calls[1]["messages"]
        assert second[-2].role == "assistant" and second[-2].tool_calls[0].name == "look"
        assert second[-1].role == "tool" and second[-1].content == "look:OK"
        # スキーマに purpose が必須で追加されている
        fn = llm.calls[0]["tools"][0]["function"]
        assert "purpose" in fn["parameters"]["required"]
        done = ev[-1]["message"]
        assert done["content"] == "調べます\n\n結果はOKでした"
        assert json.loads(done["tools_json"])[0]["tool"] == "look"
        # 次のターンでは作業記録が履歴に添えられる
        c.post(f"/api/conversations/{cid}/messages", json={"content": "次"})
        hist = [m.content for m in llm.calls[-1]["messages"] if m.role == "assistant"]
        assert any("[この返答で実行した作業]" in h and "look" in h for h in hist)
        log = c.get("/api/worklog").json()
        assert any(l["action"] == "tool_call" and l["target"] == "look" for l in log)


def _approval_flow(tmp_path, approve):
    seen = []
    llm = ScriptedLLM([{"tools": [("send", {"q": "外部へ", "purpose": "調査のため"})]}, {"text": "了解"}])
    app = build(tmp_path, llm, [make_tool("send", Level.IMPORTANT, external="ExtSvc", seen=seen)])
    with TestClient(app) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        out = {}

        def post():
            out["r"] = c.post(f"/api/conversations/{cid}/messages", json={"content": "x"})

        th = threading.Thread(target=post)
        th.start()
        for _ in range(200):
            pend = c.get("/api/approvals").json()
            if pend:
                break
            threading.Event().wait(0.02)
        assert pend and pend[0]["tool"] == "send" and pend[0]["external"] == "ExtSvc"
        assert pend[0]["purpose"] == "調査のため" and "ExtSvc" in pend[0]["reason"]
        assert c.get("/api/status").json()["state"] == "waiting"
        assert c.post(f"/api/approvals/{pend[0]['id']}", json={"approve": approve}).status_code == 200
        th.join(10)
        ev = parse_sse(out["r"].text)
        assert c.post(f"/api/approvals/{pend[0]['id']}", json={"approve": True}).status_code == 404
        return seen, ev, llm, c.get("/api/worklog").json()


def test_external_tool_waits_for_approval_and_runs(tmp_path):
    seen, ev, llm, log = _approval_flow(tmp_path, True)
    assert seen == [{"q": "外部へ"}]
    types = [e["type"] for e in ev]
    assert types.index("approval_request") < types.index("approval_resolved") < types.index("tool_result")
    assert next(e for e in ev if e["type"] == "approval_resolved")["outcome"] == "approved"
    assert any(l["action"] == "approval_approved" and l["actor"] == "user" for l in log)


def test_denied_tool_is_not_executed(tmp_path):
    seen, ev, llm, log = _approval_flow(tmp_path, False)
    assert seen == []  # 実行されない
    assert "承認しませんでした" in llm.calls[1]["messages"][-1].content
    assert next(e for e in ev if e["type"] == "tool_result")["ok"] is False


def test_approval_timeout(tmp_path):
    llm = ScriptedLLM([{"tools": [("send", {"q": "a"})]}, {"text": "了解"}])
    app = build(tmp_path, llm, [make_tool("send", Level.REVERSIBLE)])
    app.state.service.limits.approval_timeout = 0.2
    with TestClient(app) as c:
        _, ev = run_chat(c)
        assert next(e for e in ev if e["type"] == "approval_resolved")["outcome"] == "timeout"
        assert "時間切れ" in llm.calls[1]["messages"][-1].content


@pytest.mark.parametrize("call,expect", [
    (("nope", {"q": "a"}), "存在しません"),
    (("look", "{bad json"), "JSON"),
    (("look", {"purpose": "x"}), "必須の引数"),
    (("look", {"q": 5}), "型が不正"),
])
def test_bad_tool_calls_are_reported_to_llm(tmp_path, call, expect):
    llm = ScriptedLLM([{"tools": [call]}, {"text": "直します"}])
    with TestClient(build(tmp_path, llm, [make_tool("look", Level.READ)])) as c:
        _, ev = run_chat(c)
        assert expect in llm.calls[1]["messages"][-1].content
        assert ev[-1]["type"] == "done"


def test_tool_error_timeout_and_crash(tmp_path):
    llm = ScriptedLLM([{"tools": [("e1", {"q": "a"}), ("e2", {"q": "a"}), ("e3", {"q": "a"})]}, {"text": "報告"}])
    tools = [make_tool("e1", Level.READ, fail=ToolError("APIキーが無効")),
             make_tool("e2", Level.READ, sleep=1),
             make_tool("e3", Level.READ, fail=RuntimeError("bug"))]
    app = build(tmp_path, llm, tools)
    app.state.service.limits.tool_timeout = 0.1
    with TestClient(app) as c:
        _, ev = run_chat(c)
        results = [m.content for m in llm.calls[1]["messages"] if m.role == "tool"]
        assert "APIキーが無効" in results[0] and "中止" in results[1] and "内部エラー(RuntimeError)" in results[2]
        assert [e["ok"] for e in ev if e["type"] == "tool_result"] == [False, False, False]


def test_max_steps_stops_loop(tmp_path):
    llm = ScriptedLLM([{"text": f"手{i}", "tools": [("look", {"q": "a"})]} for i in range(10)])
    with TestClient(build(tmp_path, llm, [make_tool("look", Level.READ)], agent_max_steps=3)) as c:
        _, ev = run_chat(c)
        assert len(llm.calls) == 3
        assert "最大ステップ数 3" in ev[-1]["message"]["content"]


def test_no_tools_sent_when_provider_lacks_support(client):
    # MockProvider は supports_tools=False。従来どおり動く。
    _, ev = run_chat(client)
    assert ev[-1]["type"] == "done"


def test_broker_unknown_and_cancel():
    async def go():
        b = ApprovalBroker()
        assert b.resolve("missing", True) is False
        ap = b.create(conversation_id="c", tool="t", level=3, level_label="", purpose="", preview="",
                      external=None, reason="")
        task = asyncio.create_task(b.wait(ap, 5))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert b.list() == []  # 切断時に取り消される
    asyncio.run(go())


def test_approval_endpoints_require_auth(tmp_path):
    from buddy.llm.mock import MockProvider
    app = create_app(make_settings(tmp_path, host="0.0.0.0", access_token="tok-abcdef"), MockProvider(), Database(":memory:"))
    with TestClient(app) as c:
        assert c.get("/api/approvals").status_code == 401
        assert c.post("/api/approvals/x", json={"approve": True}).status_code == 401
        assert c.get("/api/outputs/a.md").status_code == 401
