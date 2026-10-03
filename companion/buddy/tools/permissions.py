"""権限ポリシー。起動時の設定からのみ作られ、実行中に変更する手段を持たない
(AI が自分の権限を引き上げられないようにするため。ポリシーを変えるツールも API も存在しない)。

判定:
- Lv4 は常に「確認」。設定でも自動許可にできない。
- 送信先(external)を持つツールは、自動許可リストに明示されない限り「確認」。
- それ以外は、auto_max_level 以下なら「自動許可」、超えたら「確認」。
"""
from __future__ import annotations

from dataclasses import dataclass

from .registry import Level, Tool


@dataclass(frozen=True)
class PermissionPolicy:
    auto_max_level: Level = Level.READ
    auto_tools: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.auto_max_level >= Level.IRREVERSIBLE:
            raise ValueError("Lv4 を自動許可にはできません。")

    def requires_approval(self, tool: Tool) -> bool:
        if tool.level >= Level.IRREVERSIBLE:
            return True
        if tool.name in self.auto_tools:
            return False
        if tool.external:
            return True
        return tool.level > self.auto_max_level

    def reason(self, tool: Tool) -> str:
        if tool.level >= Level.IRREVERSIBLE:
            return "不可逆な外部操作のため、必ず承認が必要です。"
        if tool.external and tool.name not in self.auto_tools:
            return f"データを外部サービス({tool.external})へ送信するため、承認が必要です。"
        if tool.level > self.auto_max_level:
            return f"権限レベル {int(tool.level)} は自動許可の上限({int(self.auto_max_level)})を超えるため、承認が必要です。"
        return ""
