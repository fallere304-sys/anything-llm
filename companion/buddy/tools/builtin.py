"""設定から Tool Registry と権限ポリシーを組み立てる。キーや実行環境が無い連携は登録しない。

担当(B案の既定): 作成・調査・画像 = Gemini、司令塔 = Claude Code(サブスク)。
"""
from __future__ import annotations

import logging
import shutil
from typing import Optional

from ..config import Settings
from ..memory.service import MemoryService
from .documents import document_tool
from .gemini_api import GeminiAPI
from .gemini_text import GeminiResearch, GeminiWriter
from .image import GeminiImageClient, image_tool
from .memory_tools import propose_memory_tool
from .permissions import PermissionPolicy
from .registry import Level, ToolRegistry
from .research import PerplexityClient, research_tool
from .workspace import list_files_tool, read_file_tool

log = logging.getLogger("buddy.tools")


def build_registry(settings: Settings, memory: MemoryService) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(propose_memory_tool(memory))
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    reg.register(list_files_tool(settings.workspace_dir))
    reg.register(read_file_tool(settings.workspace_dir))
    gemini: Optional[GeminiAPI] = (
        GeminiAPI(settings.gemini_api_key, settings.gemini_base_url) if settings.gemini_api_key else None)

    # 文書作成
    if settings.writer_provider == "gemini" and gemini and settings.gemini_text_model:
        reg.register(document_tool(GeminiWriter(gemini, settings.gemini_text_model), settings.workspace_dir))
    elif settings.writer_provider == "claude_api" and settings.anthropic_api_key and settings.claude_model:
        from .deliverable import ClaudeWriter  # API 課金の経路(任意)
        reg.register(document_tool(ClaudeWriter(
            settings.anthropic_api_key, settings.claude_model, effort=settings.claude_effort,
            max_tokens=settings.claude_max_tokens, fallback=settings.claude_refusal_fallback,
            base_url=settings.claude_base_url), settings.workspace_dir))
    else:
        log.info("文書作成(%s)は未設定のため無効", settings.writer_provider)

    # 調査
    if settings.research_provider == "gemini" and gemini and settings.gemini_text_model:
        reg.register(research_tool(GeminiResearch(gemini, settings.gemini_text_model)))
    elif settings.research_provider == "perplexity" and settings.perplexity_api_key and settings.perplexity_model:
        reg.register(research_tool(PerplexityClient(
            settings.perplexity_api_key, settings.perplexity_model, settings.perplexity_base_url)))
    else:
        log.info("調査(%s)は未設定のため無効", settings.research_provider)

    # 画像
    if gemini and settings.gemini_image_model:
        reg.register(image_tool(GeminiImageClient("", settings.gemini_image_model, api=gemini), settings.workspace_dir))
    else:
        log.info("画像生成は未設定のため無効")
    return reg


def claude_code_executable(settings: Settings) -> Optional[str]:
    """Claude Code CLI の実行ファイルの絶対パス(見つからなければ None)。"""
    if settings.orchestrator != "claude_code":
        return None
    return shutil.which(settings.claude_code_path)


def build_policy(settings: Settings) -> PermissionPolicy:
    return PermissionPolicy(Level(settings.tool_auto_max_level), frozenset(settings.tool_auto_approve))
