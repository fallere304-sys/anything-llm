"""設定から Tool Registry と権限ポリシーを組み立てる。キーが無い外部連携は登録しない。"""
from __future__ import annotations

import logging

from ..config import Settings
from ..memory.service import MemoryService
from .deliverable import ClaudeWriter, deliverable_tool
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
    if settings.perplexity_api_key and settings.perplexity_model:
        reg.register(research_tool(PerplexityClient(
            settings.perplexity_api_key, settings.perplexity_model, settings.perplexity_base_url)))
    else:
        log.info("Perplexity 未設定のため research_web は無効")
    if settings.anthropic_api_key and settings.claude_model:
        reg.register(deliverable_tool(ClaudeWriter(
            settings.anthropic_api_key, settings.claude_model, effort=settings.claude_effort,
            max_tokens=settings.claude_max_tokens, fallback=settings.claude_refusal_fallback,
            base_url=settings.claude_base_url), settings.workspace_dir))
    else:
        log.info("Claude 未設定のため create_deliverable は無効")
    if settings.gemini_api_key and settings.gemini_image_model:
        reg.register(image_tool(GeminiImageClient(
            settings.gemini_api_key, settings.gemini_image_model, settings.gemini_base_url), settings.workspace_dir))
    else:
        log.info("Gemini 未設定のため generate_image は無効")
    return reg


def build_policy(settings: Settings) -> PermissionPolicy:
    return PermissionPolicy(Level(settings.tool_auto_max_level), frozenset(settings.tool_auto_approve))
