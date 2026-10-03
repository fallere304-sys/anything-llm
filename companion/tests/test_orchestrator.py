"""司令塔(Claude Code)起動部のテスト。偽の claude スクリプトで検証する。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from buddy.agent.orchestrator import (ORCHESTRATOR_SYSTEM, ClaudeCodeOrchestrator, OrchestratorConfig,
                                      build_command, build_env, delegate_tool)
from buddy.agent.runs import RunRegistry
from buddy.tools.registry import Level, ToolContext, ToolError

FAKE = Path(__file__).parent / "fixtures" / "fake_claude.py"


def make(tmp_path, monkeypatch, mode="ok", key_source="none", timeout=30.0):
    rec = tmp_path / "rec.json"
    monkeypatch.setenv("FAKE_RECORD", str(rec))
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("FAKE_KEY_SOURCE", key_source)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-stripped")

    async def spawn(exe, *args, **kw):  # 実行ファイルの代わりに偽スクリプトを Python で起動
        return await asyncio.create_subprocess_exec(sys.executable, str(FAKE), *args, **kw)

    monkeypatch.chdir(tmp_path)  # 相対パスの作業ディレクトリでも動くこと(回帰テスト)
    cfg = OrchestratorConfig(executable="claude", server_url="http://127.0.0.1:9", workdir=Path("cc"),
                             timeout=timeout, mcp_tool_timeout=120)
    runs = RunRegistry()
    return ClaudeCodeOrchestrator(cfg, runs, spawn=spawn), runs, rec


def test_command_is_minimal_and_env_strips_keys(tmp_path):
    cfg = OrchestratorConfig(executable="claude", server_url="http://x", workdir=tmp_path, model="m1")
    cmd = build_command(cfg, tmp_path / "c.json")
    for flag in ("-p", "--verbose", "--restricted", "--strict-mcp-config"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""  # 組み込みツールなし
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--system-prompt") + 1] == ORCHESTRATOR_SYSTEM
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__buddy"
    assert cmd[cmd.index("--model") + 1] == "m1"
    assert "--bare" not in cmd  # サブスクのログインを使うため
    env = build_env(cfg, {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "PATH": "/bin"})
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env and env["PATH"] == "/bin"
    assert env["MCP_TOOL_TIMEOUT"] == str(int(cfg.mcp_tool_timeout * 1000))


async def test_successful_run(tmp_path, monkeypatch):
    orch, runs, rec = make(tmp_path, monkeypatch)
    events = []
    ctx = ToolContext(conversation_id="c1", emit=events.append)
    tool = delegate_tool(orch)
    assert tool.level == Level.IMPORTANT and tool.external == "Anthropic (Claude Code)"
    assert tool.timeout > orch.cfg.timeout
    r = await tool.execute({"goal": "報告書を作る", "context": "前提A", "deliverable": "md 1枚"}, ctx)
    got = json.loads(rec.read_text(encoding="utf-8"))
    assert "# 目的\n報告書を作る" in got["prompt"] and "前提A" in got["prompt"]  # 指示書は標準入力
    assert not got["has_api_key"]  # API キーは外して起動
    assert got["mcp_tool_timeout"] == "120000"
    assert Path(got["cwd"]).resolve() == (tmp_path / "cc").resolve()
    cfg_file = Path(got["argv"][got["argv"].index("--mcp-config") + 1])
    assert cfg_file.is_absolute() and not cfg_file.exists()  # 絶対パスで渡し、実行後に削除(使い捨てトークン入り)
    server = got["mcp_config"]["mcpServers"]["buddy"]
    assert server["type"] == "http" and server["url"].startswith("http://127.0.0.1:9/mcp/")
    assert server["headers"]["Authorization"].startswith("Bearer ")
    assert len(runs) == 0  # 実行単位も閉じている
    assert "outputs/x.md" in r.content and r.data["turns"] == 3 and r.data["context_tokens"] == 1205
    summaries = [e["summary"] for e in events if e["type"] == "activity"]
    assert summaries[0] == "Session started (claude-fake)" and "Dispatch -> create_document" in summaries


async def test_api_key_billing_is_refused(tmp_path, monkeypatch):
    orch, runs, _ = make(tmp_path, monkeypatch, key_source="ANTHROPIC_API_KEY")
    with pytest.raises(ToolError, match="API キー"):
        await orch.run({"goal": "x"}, ToolContext(conversation_id="c"))
    assert len(runs) == 0


@pytest.mark.parametrize("mode,match", [("crash", "boom"), ("error", "エラーで終了")])
async def test_failures(tmp_path, monkeypatch, mode, match):
    orch, _, _ = make(tmp_path, monkeypatch, mode=mode)
    with pytest.raises(ToolError, match=match):
        await orch.run({"goal": "x"}, ToolContext(conversation_id="c"))


async def test_timeout_kills_process(tmp_path, monkeypatch):
    orch, runs, _ = make(tmp_path, monkeypatch, mode="hang", timeout=1.0)
    with pytest.raises(ToolError, match="中止"):
        await orch.run({"goal": "x"}, ToolContext(conversation_id="c"))
    assert len(runs) == 0


async def test_missing_executable(tmp_path):
    cfg = OrchestratorConfig(executable=str(tmp_path / "no-such-claude"), server_url="http://x", workdir=tmp_path / "cc")
    with pytest.raises(ToolError, match="見つかりません"):
        await ClaudeCodeOrchestrator(cfg, RunRegistry()).run({"goal": "x"}, ToolContext(conversation_id="c"))
