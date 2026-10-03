"""作業フォルダのファイル読み取りツール。作業フォルダ外へのアクセスは拒否する。"""
from __future__ import annotations

from pathlib import Path

from .registry import Level, Tool, ToolContext, ToolError, ToolResult

MAX_READ_BYTES = 200_000
MAX_RETURN_CHARS = 30_000
MAX_LIST = 200


def safe_path(root: Path, rel: str) -> Path:
    """root 配下に解決できるパスのみ許可(.. やシンボリックリンクでの脱出を防ぐ)。"""
    root = root.resolve()
    target = (root / (rel or ".")).resolve()
    if target != root and root not in target.parents:
        raise ToolError("作業フォルダの外にはアクセスできません。")
    return target


def list_files_tool(root: Path) -> Tool:
    async def run(args: dict, ctx: ToolContext) -> ToolResult:
        base = safe_path(root, args.get("path", ""))
        if not base.is_dir():
            raise ToolError("フォルダが見つかりません。")
        entries = []
        for p in sorted(base.rglob("*") if args.get("recursive") else base.iterdir()):
            if len(entries) >= MAX_LIST:
                break
            rel = p.relative_to(root.resolve()).as_posix()
            entries.append(f"{rel}/" if p.is_dir() else f"{rel} ({p.stat().st_size} bytes)")
        listing = "\n".join(entries) or "(空)"
        more = f"\n…ほか多数(先頭 {MAX_LIST} 件のみ表示)" if len(entries) >= MAX_LIST else ""
        return ToolResult(content=listing + more, summary=f"{len(entries)}件を一覧")

    return Tool(
        name="list_workspace_files",
        description="作業フォルダ内のファイル・フォルダを一覧する(読み取りのみ)。",
        input_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "作業フォルダからの相対パス(省略時はルート)"},
            "recursive": {"type": "boolean", "description": "サブフォルダも含めるか"},
        }},
        level=Level.READ, execute=run, node="files",
        preview=lambda a: a.get("path") or "(ルート)",
    )


def read_file_tool(root: Path) -> Tool:
    async def run(args: dict, ctx: ToolContext) -> ToolResult:
        path = safe_path(root, args["path"])
        if not path.is_file():
            raise ToolError("ファイルが見つかりません。")
        if path.stat().st_size > MAX_READ_BYTES:
            raise ToolError(f"ファイルが大きすぎます(上限 {MAX_READ_BYTES // 1000}KB)。")
        raw = path.read_bytes()
        if b"\0" in raw[:4096]:
            raise ToolError("テキストファイルではないため読めません。")
        text = raw.decode("utf-8", errors="replace")
        cut = len(text) > MAX_RETURN_CHARS
        body = text[:MAX_RETURN_CHARS] + ("\n…(以下省略)" if cut else "")
        return ToolResult(content=body, summary=f"{args['path']} を読み込み({len(text)}文字)")

    return Tool(
        name="read_workspace_file",
        description="作業フォルダ内のテキストファイルを読む(読み取りのみ・200KBまで)。",
        input_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "作業フォルダからの相対パス"},
        }, "required": ["path"]},
        level=Level.READ, execute=run, node="files",
        preview=lambda a: str(a.get("path", "")),
    )
