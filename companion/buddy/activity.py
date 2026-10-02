"""アクセス可視化のためのノード定義とアクティビティイベント。

UI はここで定義された「実在する処理」だけを光らせる。未実装の機能は available=False で
返し、UI 側で暗く表示する(やっていない処理をやっているように見せない)。
イベントの summary には会話本文を含めない(ログにも出るため)。
"""
from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Optional

from .llm.base import LLMProvider
from .tts.base import TTSProvider

if TYPE_CHECKING:
    from .tools.registry import ToolRegistry

def describe_nodes(
    providers: dict[str, LLMProvider], tts: Optional[TTSProvider], tools: Optional["ToolRegistry"] = None
) -> list[dict]:
    names = set(tools.names()) if tools is not None else set()

    def node(id_, short, label, desc, available, phase=None, external=False, note=None):
        return {
            "id": id_, "short": short, "label": label, "description": desc,
            "available": available, "planned_phase": phase, "external": external, "note": note,
        }

    nodes = [
        node("persona", "人格", "人格・行動規則", "システムプロンプト(prompts/system.md)", True),
        node("history", "履歴", "会話履歴", "現在の会話(短期記憶)", True),
        node("memory", "記憶", "長期記憶", "ユーザーが保存を許可した情報(重要事項を含む)", True),
        node("project", "PJ", "プロジェクト", "会話に紐づくプロジェクトの記憶", True),
    ]
    for profile, short, label in (("fast", "高速", "高速モデル"), ("strong", "深考", "高性能モデル")):
        p = providers.get(profile)
        nodes.append(node(
            f"llm:{profile}", short, label,
            f"会話・思考・タスク振り分け: {p.name} / {p.model}" if p else "未設定(.env の LLM_MODEL_STRONG)",
            p is not None, external=bool(p and p.sends_data_externally),
        ))
    nodes += [
        node("research", "調査", "Perplexity", "Web 調査(出典付き)。.env の PERPLEXITY_API_KEY / PERPLEXITY_MODEL",
             "research_web" in names, external=True, note=None if "research_web" in names else "未設定"),
        node("claude", "制作", "Claude", "成果物の作成(outputs/ に保存)。.env の ANTHROPIC_API_KEY / CLAUDE_MODEL",
             "create_deliverable" in names, external=True, note=None if "create_deliverable" in names else "未設定"),
        node("canva", "画像", "Canva", "画像・デザイン。接続方式を検討中(公開APIに画像生成が無いため)",
             False, note="未接続"),
        node("files", "File", "作業フォルダ", "作業フォルダの読み取り(.env の BUDDY_WORKSPACE_DIR)",
             "read_workspace_file" in names),
        node(
            "tts", "声", "音声出力",
            f"{tts.name} / {tts.voice}" if tts else "未設定(.env の TTS_PROVIDER)",
            tts is not None, external=bool(tts and tts.sends_data_externally),
        ),
    ]
    return nodes


def activity(
    target: str, phase: str, summary: str, aid: Optional[str] = None, **extra
) -> dict:
    """phase: start(開始) / end(終了) / pulse(瞬間的な参照)。"""
    return {
        "type": "activity", "id": aid or uuid.uuid4().hex[:12], "target": target,
        "phase": phase, "summary": summary, "ts": time.time(), **extra,
    }
