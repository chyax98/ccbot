"""Message debounce for burst message merging.

Merges rapid-fire messages to reduce agent calls and token usage.
Control commands are never debounced.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from loguru import logger

T = TypeVar("T")


@dataclass
class DebounceEntry(Generic[T]):
    """Single entry in debounce buffer."""

    item: T
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)


class Debouncer(Generic[T]):
    """Debounces rapid-fire messages by key.

    Features:
    - Keyed debounce (e.g., per chat)
    - Configurable delay (default 300ms)
    - Max wait time to prevent indefinite buffering
    - Control command bypass
    - Flush callback for merged messages

    Example:
        debouncer = Debouncer[str](delay_ms=300, max_wait_ms=1000)
        debouncer.on_flush(lambda items: process("\\n".join(items)))

        # These 3 messages sent within 300ms will be merged
        await debouncer.enqueue("chat_1", "Hello")
        await debouncer.enqueue("chat_1", "How are you?")
        await debouncer.enqueue("chat_1", "I have a question")
        # -> process("Hello\\nHow are you?\\nI have a question")
    """

    # Control commands that should never be debounced
    CONTROL_COMMANDS = frozenset({"/new", "/stop", "/help", "/reset", "/clear"})

    def __init__(
        self,
        delay_ms: float = 300,
        max_wait_ms: float = 1000,
        key_extractor: Callable[[T], str] | None = None,
        is_control_command: Callable[[T], bool] | None = None,
    ) -> None:
        """Initialize debouncer.

        Args:
            delay_ms: Debounce delay in milliseconds
            max_wait_ms: Maximum wait time before forcing flush
            key_extractor: Function to extract key from item (default str(item))
            is_control_command: Function to check if item is control command
        """
        self._delay_ms = delay_ms
        self._max_wait_ms = max_wait_ms
        self._key_extractor = key_extractor or (lambda x: str(x))
        self._is_control = is_control_command or self._default_is_control

        self._buffers: dict[str, list[DebounceEntry[T]]] = defaultdict(list)
        self._timers: dict[str, asyncio.Task] = {}
        self._flush_handler: Callable[[list[T]], Awaitable[None]] | None = None

    @staticmethod
    def _default_is_control(item: object) -> bool:
        """Default control command detection."""
        text = str(item).strip().lower()
        return any(text.startswith(cmd) for cmd in Debouncer.CONTROL_COMMANDS)

    def on_flush(
        self, handler: Callable[[list[T]], Awaitable[None]]
    ) -> Callable[[list[T]], Awaitable[None]]:
        """Register flush handler.

        Args:
            handler: Async function receiving list of merged items

        Returns:
            The handler (for decorator use)
        """
        self._flush_handler = handler
        return handler

    async def enqueue(self, item: T) -> None:
        """Enqueue item for debouncing.

        Control commands are flushed immediately without debouncing.

        Args:
            item: Message or event to debounce
        """
        # Control commands bypass debounce
        if self._is_control(item):
            await self._flush_immediately([item])
            return

        key = self._key_extractor(item)
        entry = DebounceEntry(item=item)

        # Reset timer for this key
        if key in self._timers:
            self._timers[key].cancel()

        self._buffers[key].append(entry)

        # Start new timer
        self._timers[key] = asyncio.create_task(
            self._debounce_timer(key),
            name=f"debounce-{key}",
        )

    async def _debounce_timer(self, key: str) -> None:
        """Timer that triggers flush after delay."""
        try:
            # Calculate wait time (respecting max_wait)
            if self._buffers[key]:
                first_ts = self._buffers[key][0].timestamp_ms
                elapsed = time.time() * 1000 - first_ts
                wait_ms = min(self._delay_ms, self._max_wait_ms - elapsed)
            else:
                wait_ms = self._delay_ms

            if wait_ms > 0:
                await asyncio.sleep(wait_ms / 1000)

            await self._flush_key(key)
        except asyncio.CancelledError:
            pass

    async def _flush_key(self, key: str) -> None:
        """Flush buffer for specific key."""
        if key not in self._buffers:
            return

        entries = self._buffers.pop(key, [])
        self._timers.pop(key, None)

        if entries:
            items = [e.item for e in entries]
            await self._flush_immediately(items)

    async def _flush_immediately(self, items: list[T]) -> None:
        """Flush items immediately."""
        if self._flush_handler is None:
            logger.warning("Debouncer has no flush handler, dropping {} items", len(items))
            return

        try:
            await self._flush_handler(items)
        except Exception:
            logger.exception("Debounce flush handler failed")

    async def flush_all(self) -> None:
        """Flush all pending buffers."""
        # Cancel all timers
        for task in self._timers.values():
            task.cancel()

        # Flush all keys
        await asyncio.gather(
            *[self._flush_key(key) for key in list(self._buffers.keys())],
            return_exceptions=True,
        )

    async def stop(self) -> None:
        """Stop debouncer and flush all pending items."""
        await self.flush_all()
        self._buffers.clear()
        self._timers.clear()

    def get_pending_count(self, key: str | None = None) -> int:
        """Get number of pending items.

        Args:
            key: Specific key to check, or None for all

        Returns:
            Number of pending items
        """
        if key is not None:
            return len(self._buffers.get(key, []))
        return sum(len(entries) for entries in self._buffers.values())


# Feishu-specific helpers


def extract_feishu_debounce_key(event: dict) -> str:
    """Extract debounce key from Feishu event.

    Format: feishu:{account_id}:{chat_id}:{thread_key}:{sender_id}
    """
    message = event.get("message", {})
    sender = event.get("sender", {})

    chat_id = message.get("chat_id", "unknown")
    sender_id = sender.get("sender_id", {}).get("open_id", "unknown")
    root_id = message.get("root_id")

    # Thread key differentiates topics within same chat
    thread_key = f"thread:{root_id}" if root_id else "main"

    return f"feishu:{chat_id}:{thread_key}:{sender_id}"


def is_feishu_control_command(event: dict) -> bool:
    """Check if Feishu event is a control command."""
    try:
        content = event.get("message", {}).get("content", "")
        import json

        text = json.loads(content).get("text", "")
        return text.strip().lower() in Debouncer.CONTROL_COMMANDS
    except Exception:
        return False
