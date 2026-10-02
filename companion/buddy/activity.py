"""アクセス可視化のためのノード定義とアクティビティイベント。

UI はここで定義された「実在する処理」だけを光らせる。未実装の機能は available=False で
返し、UI 側で暗く表示する(やっていない処理をやっているように見せない)。
イベントの summary には会話本文を含めない(ログにも出るため)。
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from .llm.base import LLMProvider
from .tts.base import TTSProvider

# (id, 球体内の短い表示, 名称, 説明, 予定フェーズ) — 予定フェーズが None なら実装済み
_PLANNED = [
    ("memory", "記憶", "長期記憶", "ユーザーが保存を許可した情報", 3),
    ("project", "PJ", "プロジェクト", "プロジェクト単位の記憶", 3),
    ("web", "Web", "Web検索", "外部情報の調査", 4),
    ("files", "File", "ファイル", "PC上のファイル参照・作成", 4),
    ("tools", "Tool", "ツール", "コマンド・Python・Git 等の実行", 4),
]


def describe_nodes(
    providers: dict[str, LLMProvider], tts: Optional[TTSProvider]
) -> list[dict]:
    def node(id_, short, label, desc, available, phase=None, external=False):
        return {
            "id": id_, "short": short, "label": label, "description": desc,
            "available": available, "planned_phase": phase, "external": external,
        }

    nodes = [
        node("persona", "人格", "人格・行動規則", "システムプロンプト(prompts/system.md)", True),
        node("history", "履歴", "会話履歴", "現在の会話(短期記憶)", True),
    ]
    for profile, short, label in (("fast", "高速", "高速モデル"), ("strong", "深考", "高性能モデル")):
        p = providers.get(profile)
        nodes.append(node(
            f"llm:{profile}", short, label,
            f"{p.name} / {p.model}" if p else "未設定(.env の LLM_MODEL_STRONG)",
            p is not None, external=bool(p and p.sends_data_externally),
        ))
    nodes.append(node(
        "tts", "声", "音声出力",
        f"{tts.name} / {tts.voice}" if tts else "未設定(.env の TTS_PROVIDER)",
        tts is not None, external=bool(tts and tts.sends_data_externally),
    ))
    nodes += [node(i, s, l, d, False, phase) for i, s, l, d, phase in _PLANNED]
    return nodes


def activity(
    target: str, phase: str, summary: str, aid: Optional[str] = None, **extra
) -> dict:
    """phase: start(開始) / end(終了) / pulse(瞬間的な参照)。"""
    return {
        "type": "activity", "id": aid or uuid.uuid4().hex[:12], "target": target,
        "phase": phase, "summary": summary, "ts": time.time(), **extra,
    }
