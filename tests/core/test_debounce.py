"""Tests for Debouncer."""

import asyncio

import pytest

from ccbot.core.debounce import Debouncer


class TestDebouncer:
    """Test cases for Debouncer."""

    @pytest.mark.asyncio
    async def test_single_item_debounce(self):
        """Single item should be flushed after delay."""
        flushed = []

        async def handler(items):
            flushed.append(items)

        # Use same key for all items
        debouncer = Debouncer[str](delay_ms=50, key_extractor=lambda x: "same_key")
        debouncer.on_flush(handler)

        await debouncer.enqueue("Hello")
        assert len(flushed) == 0

        await asyncio.sleep(0.1)
        assert len(flushed) == 1
        assert flushed[0] == ["Hello"]

    @pytest.mark.asyncio
    async def test_merge_multiple_items(self):
        """Multiple items within delay should be merged."""
        flushed = []

        async def handler(items):
            flushed.append(items)

        # Use same key for all items
        debouncer = Debouncer[str](delay_ms=200, key_extractor=lambda x: "same_key")
        debouncer.on_flush(handler)

        # Send 3 messages rapidly
        await debouncer.enqueue("A")
        await debouncer.enqueue("B")
        await debouncer.enqueue("C")

        # Wait for debounce delay
        await asyncio.sleep(0.3)

        assert len(flushed) == 1
        assert flushed[0] == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_control_command_bypass(self):
        """Control commands should bypass debounce."""
        flushed = []

        async def handler(items):
            flushed.append(items)

        debouncer = Debouncer[str](delay_ms=100, key_extractor=lambda x: "same_key")
        debouncer.on_flush(handler)

        await debouncer.enqueue("/new")
        assert len(flushed) == 1
        assert flushed[0] == ["/new"]
