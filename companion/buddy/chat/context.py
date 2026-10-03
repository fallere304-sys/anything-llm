"""LLMに渡す文脈の組み立て(短期記憶 = 現在の会話履歴の切り詰め)。"""
from __future__ import annotations

from ..llm.base import ChatMessage


def build_context(
    system_prompt: str, history: list[dict], max_chars: int
) -> tuple[list[ChatMessage], int]:
    """システムプロンプト + 直近の履歴を、文字数予算内で新しい順に詰める。

    戻り値: (メッセージ列, 切り捨てた古いメッセージ数)。
    最新メッセージは予算超過でも必ず含める(直近のユーザー発言を落とさないため)。
    """
    budget = max_chars - len(system_prompt)
    kept: list[ChatMessage] = []
    used = 0
    for m in reversed(history):
        size = len(m["content"])
        if kept and used + size > budget:
            break
        kept.append(ChatMessage(m["role"], m["content"]))
        used += size
    kept.reverse()
    dropped = len(history) - len(kept)
    msgs = [ChatMessage("system", system_prompt)] if system_prompt else []
    return msgs + kept, dropped
