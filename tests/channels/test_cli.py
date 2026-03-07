"""Tests for CLIChannel."""

import pytest
from unittest.mock import AsyncMock

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
        channel = CLIChannel(
            single_message="test",
            prompt=">>> ",
            bot_name="TestBot"
        )
        assert channel._single_message == "test"
        assert channel._prompt == ">>> "
        assert channel._bot_name == "TestBot"
