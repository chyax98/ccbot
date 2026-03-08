"""Tests for AgentPool.interrupt() 方法。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.config import AgentConfig
from ccbot.runtime.pool import AgentPool


@pytest.fixture
def pool() -> AgentPool:
    config = MagicMock(spec=AgentConfig)
    config.model = ""
    config.max_turns = 10
    config.system_prompt = ""
    config.cwd = ""
    config.allowed_tools = []
    config.mcp_servers = {}
    config.idle_timeout = 1800
    return AgentPool(config, idle_timeout=60)


class TestInterrupt:
    """AgentPool.interrupt 的测试用例。"""

    @pytest.mark.asyncio
    async def test_interrupt_existing_client(self, pool: AgentPool) -> None:
        """interrupt 应当调用 client.interrupt()。"""
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.interrupt = AsyncMock()

        with patch.object(pool, "_create_client", return_value=mock_client):
            await pool.acquire("chat_1")

        result = await pool.interrupt("chat_1")

        assert result is True
        mock_client.interrupt.assert_awaited_once()
        await pool.stop()

    @pytest.mark.asyncio
    async def test_interrupt_nonexistent_client(self, pool: AgentPool) -> None:
        """不存在的 chat_id 应返回 False，不抛异常。"""
        result = await pool.interrupt("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_interrupt_exception_returns_false(self, pool: AgentPool) -> None:
        """interrupt 异常时返回 False，不抛出。"""
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.interrupt = AsyncMock(side_effect=RuntimeError("connection lost"))

        with patch.object(pool, "_create_client", return_value=mock_client):
            await pool.acquire("chat_1")

        result = await pool.interrupt("chat_1")

        assert result is False
        await pool.stop()

    @pytest.mark.asyncio
    async def test_interrupt_does_not_close_client(self, pool: AgentPool) -> None:
        """interrupt 不应关闭 client（只是中断查询）。"""
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.interrupt = AsyncMock()

        with patch.object(pool, "_create_client", return_value=mock_client):
            await pool.acquire("chat_1")

        await pool.interrupt("chat_1")

        # client 仍在池中
        assert "chat_1" in pool._clients
        mock_client.disconnect.assert_not_awaited()
        await pool.stop()


class TestIdleTimeoutFromConfig:
    """验证 idle_timeout 从 config 正确继承。"""

    def test_uses_config_idle_timeout_when_not_specified(self) -> None:
        """不传 idle_timeout 时使用 config.idle_timeout。"""
        config = MagicMock(spec=AgentConfig)
        config.idle_timeout = 7200
        pool = AgentPool(config)
        assert pool._idle_timeout == 7200

    def test_explicit_idle_timeout_overrides_config(self) -> None:
        """显式传入 idle_timeout 优先于 config。"""
        config = MagicMock(spec=AgentConfig)
        config.idle_timeout = 7200
        pool = AgentPool(config, idle_timeout=300)
        assert pool._idle_timeout == 300
