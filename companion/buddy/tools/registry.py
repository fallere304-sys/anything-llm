"""Tool Registry。AI が使えるのはここに登録されたツールだけ。

Tool = name / description / input schema / permission level / execution function。
AI に渡すスキーマには必ず `purpose`(使う理由。承認画面に表示)を追加する。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable, Optional


class Level(IntEnum):
    INFO = 0  # 情報取得のみ(副作用なし・端末内)
    READ = 1  # 読み取り操作
    REVERSIBLE = 2  # 可逆的な変更(新規ファイル作成など)
    IMPORTANT = 3  # 重要な変更 / 外部サービスへの送信
    IRREVERSIBLE = 4  # 外部への不可逆操作(公開・送金・契約・削除など)。常に人間の明示承認


LEVEL_LABEL = {
    Level.INFO: "Lv0 情報取得", Level.READ: "Lv1 読み取り", Level.REVERSIBLE: "Lv2 可逆な変更",
    Level.IMPORTANT: "Lv3 重要/外部送信", Level.IRREVERSIBLE: "Lv4 不可逆な外部操作",
}


class ToolError(Exception):
    """ツールの実行失敗。メッセージは AI とユーザーに見せてよい内容にする。"""


@dataclass
class ToolContext:
    conversation_id: str
    project_id: Optional[str] = None
    # 実行中のイベント(承認依頼・可視化)を画面へ中継する口。司令塔のサブツール実行で使う
    emit: Callable[[dict], None] = field(default=lambda ev: None, repr=False)
    origin: str = "conversation"  # conversation | orchestrator(誰がツールを呼んだか)


@dataclass
class ToolResult:
    content: str  # AI に返す内容
    summary: str  # 画面・作業履歴に出す短い要約(本文を含めない)
    data: dict = field(default_factory=dict)  # UI 用の付加情報(出典 / 作成ファイル等)
    brief: Optional[str] = None  # 司令塔(Claude Code)へ返す短い結果。省略時は content を短く切って使う


Executor = Callable[[dict, ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    level: Level
    execute: Executor
    node: str  # 可視化ノード ID
    external: Optional[str] = None  # 送信先サービス名(端末外へ出る場合)
    preview: Callable[[dict], str] = lambda args: ""  # 承認画面に出す引数の要約
    timeout: Optional[float] = None  # 実行タイムアウト(秒)。None なら全体設定


PURPOSE = {"type": "string", "description": "このツールを使う理由を日本語で1文(ユーザーの承認画面に表示される)"}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        if tool.input_schema.get("type") != "object":
            raise ValueError(f"{tool.name}: input_schema must be an object schema")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_schemas(self) -> list[dict]:
        out = []
        for t in sorted(self._tools.values(), key=lambda t: t.name):
            params = json.loads(json.dumps(t.input_schema))  # 深いコピー
            params.setdefault("properties", {})["purpose"] = PURPOSE
            params["required"] = sorted(set(params.get("required", [])) | {"purpose"})
            out.append({"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": params}})
        return out

    @staticmethod
    def validate(tool: Tool, args: Any) -> dict:
        """最小限の検証: object であること・必須項目・型(string/integer/number/boolean)・enum。"""
        if not isinstance(args, dict):
            raise ToolError("引数は JSON オブジェクトである必要があります。")
        schema = tool.input_schema
        for key in schema.get("required", []):
            if key not in args:
                raise ToolError(f"必須の引数がありません: {key}")
        types = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
        for key, spec in schema.get("properties", {}).items():
            if key not in args:
                continue
            want = types.get(spec.get("type"))
            val = args[key]
            if want and (not isinstance(val, want) or (spec.get("type") != "boolean" and isinstance(val, bool))):
                raise ToolError(f"引数 {key} の型が不正です({spec.get('type')} が必要)。")
            if "enum" in spec and val not in spec["enum"]:
                raise ToolError(f"引数 {key} は {spec['enum']} のいずれかです。")
            if isinstance(val, str) and "maxLength" in spec and len(val) > spec["maxLength"]:
                raise ToolError(f"引数 {key} が長すぎます(最大 {spec['maxLength']} 文字)。")
        return args
