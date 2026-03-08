"""Tests for CLIChannel."""

from unittest.mock import AsyncMock

import pytest

from ccbot.channels.cli import CLIChannel


class TestCLIChannel:
    """Test cases for CLIChannel."""

    @pytest.mark.asyncio
    async def test_single_message_mode(self, capsys):
        """Single message mode should process and exit."""
        channel = CLIChannel(single_message="Hello bot")

        mock_handler = AsyncMock(return_value="Hello back!")
        channel.on_message(mock_handler)

        await channel.start()

        captured = capsys.readouterr()
        assert "You: Hello bot" in captured.out
        assert "ccbot: Hello back!" in captured.out

    @pytest.mark.asyncio
    async def test_single_message_mode_prints_worker_results(self, capsys):
        """Single message mode should surface streamed worker results."""
        channel = CLIChannel(single_message="run workers")

        async def handler(message, progress_cb, result_sender):
            await progress_cb("分析任务中")
            assert result_sender is not None
            await result_sender("worker-a", "done")
            return "all done"

        channel.on_message_context(handler)

        await channel.start()

        captured = capsys.readouterr()
        assert "[分析任务中]" in captured.out
        assert "[worker-a] done" in captured.out
        assert "ccbot: all done" in captured.out

    @pytest.mark.asyncio
    async def test_send_message(self, capsys):
        """Send should print to console."""
        channel = CLIChannel()
        await channel.send("cli", "Test message")

        captured = capsys.readouterr()
        assert "ccbot: Test message" in captured.out

    def test_channel_initialization(self):
        """CLIChannel should initialize with correct defaults."""
        channel = CLIChannel()
        assert channel._single_message is None
        assert channel._prompt == "You: "
        assert channel._bot_name == "🐈 ccbot"

    def test_channel_custom_params(self):
        """CLIChannel should accept custom parameters."""
        channel = CLIChannel(single_message="test", prompt=">>> ", bot_name="TestBot")
        assert channel._single_message == "test"
        assert channel._prompt == ">>> "
        assert channel._bot_name == "TestBot"
