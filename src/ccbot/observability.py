"""LangSmith observability integration for ClaudeSDKClient runtimes."""

from __future__ import annotations

import importlib
import os
import threading
from typing import TYPE_CHECKING, Any

from loguru import logger

from ccbot import __version__

if TYPE_CHECKING:
    from ccbot.config import AgentConfig

_LANGSMITH_LOCK = threading.Lock()
_LANGSMITH_CONFIGURED = False
_LANGSMITH_ATTEMPTED = False


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _should_enable(config: AgentConfig) -> bool:
    if config.langsmith_enabled:
        return True
    return _is_truthy(os.getenv("LANGSMITH_TRACING")) or _is_truthy(
        os.getenv("LANGSMITH_TRACING_V2")
    )


def _apply_langsmith_env(config: AgentConfig) -> None:
    if config.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = config.langsmith_api_key
    if config.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = config.langsmith_endpoint
    if config.langsmith_project:
        os.environ["LANGSMITH_PROJECT"] = config.langsmith_project
    if config.langsmith_enabled:
        os.environ.setdefault("LANGSMITH_TRACING", "true")


def _build_metadata(config: AgentConfig) -> dict[str, Any]:
    metadata = {
        "service": "ccbot",
        "service_version": __version__,
        "runtime": "claude-agent-sdk",
        "configured_model": config.model or "default",
        "max_turns": config.max_turns,
        "max_workers": config.max_workers,
        "scheduler_enabled": config.scheduler_enabled,
        "heartbeat_enabled": config.heartbeat_enabled,
    }
    metadata.update(config.langsmith_metadata)
    return metadata


def _build_tags(config: AgentConfig) -> list[str]:
    tags = ["ccbot", *config.langsmith_tags]
    # 去重但保留顺序
    return list(dict.fromkeys(tag for tag in tags if tag))


def get_langsmith_status(config: AgentConfig) -> dict[str, Any]:
    """Return the current LangSmith tracing state for logging/UI."""
    project = config.langsmith_project or os.getenv("LANGSMITH_PROJECT", "")
    endpoint = config.langsmith_endpoint or os.getenv("LANGSMITH_ENDPOINT", "")
    api_key = config.langsmith_api_key or os.getenv("LANGSMITH_API_KEY", "")
    return {
        "enabled": _should_enable(config),
        "project": project,
        "name": config.langsmith_name or "ccbot",
        "endpoint_set": bool(endpoint),
        "api_key_set": bool(api_key),
        "tags": _build_tags(config),
        "metadata_keys": sorted(_build_metadata(config).keys()),
    }


def configure_langsmith_once(config: AgentConfig) -> bool:
    """Enable LangSmith tracing for all ClaudeSDKClient instances in this process.

    LangSmith's official Claude Agent SDK integration instruments `ClaudeSDKClient`.
    It does not instrument the top-level `claude_agent_sdk.query()` function, which is
    acceptable for ccbot because the runtime is built around persistent client sessions.
    """
    global _LANGSMITH_ATTEMPTED, _LANGSMITH_CONFIGURED

    if _LANGSMITH_CONFIGURED:
        return True
    if not _should_enable(config):
        return False

    with _LANGSMITH_LOCK:
        if _LANGSMITH_ATTEMPTED:
            return _LANGSMITH_CONFIGURED

        _LANGSMITH_ATTEMPTED = True
        _apply_langsmith_env(config)

        try:
            module = importlib.import_module("langsmith.integrations.claude_agent_sdk")
        except ImportError:
            logger.warning(
                "已启用 LangSmith tracing，但未安装 langsmith。请安装: pip install 'langsmith[claude-agent-sdk]'"
            )
            return False

        configure = getattr(module, "configure_claude_agent_sdk", None)
        if configure is None:
            logger.warning("当前安装的 langsmith 缺少 configure_claude_agent_sdk()。")
            return False

        ok = bool(
            configure(
                name=config.langsmith_name or None,
                project_name=config.langsmith_project or None,
                metadata=_build_metadata(config),
                tags=_build_tags(config),
            )
        )
        if not ok:
            logger.warning("LangSmith Claude Agent SDK tracing 初始化失败。")
            return False

        _LANGSMITH_CONFIGURED = True
        logger.info(
            "LangSmith tracing 已启用（ClaudeSDKClient 已接入；顶层 claude_agent_sdk.query() 不会被追踪）"
        )
        return True
