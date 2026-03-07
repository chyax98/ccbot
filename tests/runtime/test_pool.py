"""Tests for AgentPool."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.runtime.pool import AgentPool


class TestAgentPool:
    """Test cases for AgentPool."""

    @pytest.fixture
    def mock_config(self):
        """Create mock AgentConfig."""
        config = MagicMock()
        config.model = "claude-sonnet-4-6"
        config.max_turns = 30
        config.system_prompt = ""
        config.cwd = ""
        config.allowed_tools = []
        config.mcp_servers = {}
        return config

    @pytest.fixture
    def mock_workspace(self):
        """Create mock WorkspaceManager."""
        ws = MagicMock()
        ws.path = "/tmp/test"
        ws.build_system_prompt.return_value = "System prompt"
        return ws

    @pytest.mark.asyncio
    async def test_acquire_creates_new_client(self, mock_config, mock_workspace):
        """Acquire should create new client for unknown chat_id."""
        pool = AgentPool(mock_config, mock_workspace, idle_timeout=60)

        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch.object(pool, "_create_client", return_value=mock_client):
            client = await pool.acquire("chat_123")

        assert client is mock_client

        await pool.stop()

    @pytest.mark.asyncio
    async def test_acquire_returns_existing_client(self, mock_config, mock_workspace):
        """Acquire should return existing client for known chat_id."""
        pool = AgentPool(mock_config, mock_workspace, idle_timeout=60)

        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch.object(pool, "_create_client", return_value=mock_client) as mock_create:
            # First acquire creates client
            client1 = await pool.acquire("chat_123")
            # Second acquire returns same client
            client2 = await pool.acquire("chat_123")

        assert client1 is client2
        assert mock_create.call_count == 1  # Only created once

        await pool.stop()

    @pytest.mark.asyncio
    async def test_close_removes_client(self, mock_config, mock_workspace):
        """Close should remove and disconnect client."""
        pool = AgentPool(mock_config, mock_workspace, idle_timeout=60)

        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch.object(pool, "_create_client", return_value=mock_client):
            await pool.acquire("chat_123")
            await pool.close("chat_123")

        mock_client.disconnect.assert_called_once()
        assert "chat_123" not in pool._clients

        await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_closes_all_clients(self, mock_config, mock_workspace):
        """Stop should close all clients."""
        pool = AgentPool(mock_config, mock_workspace, idle_timeout=60)

        mock_client1 = AsyncMock()
        mock_client1.disconnect = AsyncMock()
        mock_client2 = AsyncMock()
        mock_client2.disconnect = AsyncMock()

        with patch.object(pool, "_create_client", side_effect=[mock_client1, mock_client2]):
            await pool.acquire("chat_1")
            await pool.acquire("chat_2")

            await pool.stop()

        mock_client1.disconnect.assert_called_once()
        mock_client2.disconnect.assert_called_once()
        assert len(pool._clients) == 0

    @pytest.mark.asyncio
    async def test_cleanup_idle_clients(self, mock_config, mock_workspace):
        """Idle clients should be cleaned up after timeout."""
        pool = AgentPool(mock_config, mock_workspace, idle_timeout=0.1)

        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch.object(pool, "_create_client", return_value=mock_client):
            await pool.acquire("chat_123")
            await pool.release("chat_123")

            # Wait for idle timeout
            await asyncio.sleep(0.2)

            # Manually trigger cleanup
            await pool._cleanup_idle()

        mock_client.disconnect.assert_called_once()
        await pool.stop()

    def test_get_stats(self, mock_config, mock_workspace):
        """Get stats should return pool statistics."""
        pool = AgentPool(mock_config, mock_workspace, idle_timeout=300)
        stats = pool.get_stats()

        assert stats["active_clients"] == 0
        assert stats["idle_timeout"] == 300
