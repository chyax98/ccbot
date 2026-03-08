"""共享 pytest fixtures。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ccbot.config import AgentConfig
from ccbot.workspace import WorkspaceManager


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> WorkspaceManager:
    """创建临时工作空间。"""
    return WorkspaceManager(tmp_path / "workspace")


@pytest.fixture
def agent_config() -> AgentConfig:
    """默认 Agent 配置。"""
    return AgentConfig()


@pytest.fixture
def mock_claude_client() -> MagicMock:
    """创建 mock ClaudeSDKClient。"""
    client = MagicMock()
    client.connect = AsyncMock()
    client.query = AsyncMock()
    client.disconnect = AsyncMock()
    client.interrupt = AsyncMock()
    return client
