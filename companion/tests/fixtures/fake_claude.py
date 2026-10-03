"""テスト用の偽 Claude Code。受け取った引数・環境・標準入力を記録し、stream-json を出力する。"""
import json
import os
import sys
import time

record = os.environ.get("FAKE_RECORD")
prompt = sys.stdin.read()
args = sys.argv[1:]
cfg_arg = args[args.index("--mcp-config") + 1]
if not os.path.isfile(cfg_arg):  # 本物と同様、設定が読めなければ失敗する
    sys.stderr.write("Error: Invalid MCP configuration\n")
    sys.exit(1)
if record:
    with open(record, "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(),
                   "mcp_config": json.load(open(cfg_arg, encoding="utf-8")),
                   "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
                   "mcp_tool_timeout": os.environ.get("MCP_TOOL_TIMEOUT")}, f, ensure_ascii=False)
mode = os.environ.get("FAKE_MODE", "ok")
out = lambda e: (sys.stdout.write(json.dumps(e, ensure_ascii=False) + "\n"), sys.stdout.flush())
out({"type": "system", "subtype": "init", "model": "claude-fake",
     "apiKeySource": os.environ.get("FAKE_KEY_SOURCE", "none"), "mcp_servers": [{"name": "buddy", "status": "connected"}]})
if mode == "hang":
    time.sleep(60)
if mode == "crash":
    sys.stderr.write("boom: something failed\n")
    sys.exit(3)
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "計画します"},
     {"type": "tool_use", "name": "mcp__buddy__create_document", "input": {}}]}})
out({"type": "result", "subtype": "success", "is_error": mode == "error", "num_turns": 3,
     "result": "報告: outputs/x.md を作成しました。" if mode != "error" else "failed",
     "usage": {"input_tokens": 5, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 200, "output_tokens": 50}})
