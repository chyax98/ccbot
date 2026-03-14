"""集成测试共享 fixtures：真实组件 + mock SDK 边界。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ccbot.config import AgentConfig
from ccbot.memory import MemoryStore
from ccbot.workspace import WorkspaceManager


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceManager:
    """创建临时 workspace。"""
    return WorkspaceManager(tmp_path / "workspace")


@pytest.fixture
def memory_store(workspace: WorkspaceManager) -> MemoryStore:
    """创建真实 MemoryStore。"""
    return MemoryStore(workspace.path)


@pytest.fixture
def agent_config() -> AgentConfig:
    """基础 AgentConfig。"""
    return AgentConfig(idle_timeout=3600, max_workers=3)


def make_mock_sdk_client(
    text_reply: str = "ok",
    structured_output: Any = None,
    session_id: str = "test-session-001",
) -> MagicMock:
    """构建 mock ClaudeSDKClient，返回预定义响应。

    仅 mock 外部 SDK 边界，内部组件使用真实对象。
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage, TaskUsage, TextBlock

    client = MagicMock()
    client.connect = AsyncMock()
    client.query = AsyncMock()
    client.disconnect = AsyncMock()
    client.interrupt = AsyncMock()

    usage = TaskUsage(
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )

    async def _receive():
        yield AssistantMessage(
            content=[TextBlock(text=text_reply)],
            model="claude-sonnet-4-6",
        )
        yield ResultMessage(
            subtype="result",
            is_error=False,
            duration_ms=100,
            duration_api_ms=80,
            num_turns=1,
            total_cost_usd=0.001,
            usage=usage,
            session_id=session_id,
            structured_output=structured_output,
        )

    client.receive_response = _receive
    return client


def make_error_sdk_client(error: Exception) -> MagicMock:
    """构建抛出异常的 mock ClaudeSDKClient。"""
    client = MagicMock()
    client.connect = AsyncMock()
    client.query = AsyncMock(side_effect=error)
    client.disconnect = AsyncMock()
    client.interrupt = AsyncMock()
    return client
