"""文書作成の書き手: Claude(Anthropic 公式 Python SDK)。WRITER_PROVIDER=claude_api で使う(API課金)。"""
from __future__ import annotations

from typing import Any, Optional

import anthropic

from .documents import WRITER_SYSTEM, Draft
from .registry import ToolError

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeWriter:
    external = "Anthropic (Claude API)"
    label = "Claude"

    def __init__(self, api_key: str, model: str, *, effort: str = "high", max_tokens: int = 32000,
                 fallback: str = "default", base_url: str = "https://api.anthropic.com",
                 timeout: float = 600.0, client: Optional[Any] = None) -> None:
        if not api_key or not model:
            raise ValueError("ANTHROPIC_API_KEY と CLAUDE_MODEL が必要です。")
        self.model, self.effort, self.max_tokens, self.fallback = model, effort, max_tokens, fallback
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, timeout=timeout)

    async def write(self, prompt: str) -> Draft:
        kwargs: dict = {
            "model": self.model, "max_tokens": self.max_tokens, "system": WRITER_SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        try:
            if self.fallback == "default":
                # 安全上の辞退(refusal)時にサーバー側で別モデルへ自動フォールバック
                stream_cm = self._client.beta.messages.stream(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                stream_cm = self._client.messages.stream(**kwargs)
            async with stream_cm as stream:
                msg = await stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise ToolError("Claude の APIキーが無効です。") from exc
        except anthropic.RateLimitError as exc:
            raise ToolError("Claude の利用上限に達しました。時間をおいて再試行してください。") from exc
        except anthropic.BadRequestError as exc:
            raise ToolError(f"Claude へのリクエストが不正です(モデル名・設定を確認): {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise ToolError(f"Claude がエラーを返しました (HTTP {exc.status_code})。") from exc
        except anthropic.APITimeoutError as exc:
            raise ToolError("Claude の応答がタイムアウトしました。") from exc
        except anthropic.APIConnectionError as exc:
            raise ToolError("Claude に接続できません。ネットワークを確認してください。") from exc
        if msg.stop_reason == "refusal":
            raise ToolError("Claude がこの依頼を辞退しました(安全上の理由)。依頼内容を見直してください。")
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise ToolError("Claude が空の成果物を返しました。")
        return Draft(text=text, model=getattr(msg, "model", self.model), truncated=msg.stop_reason == "max_tokens")
