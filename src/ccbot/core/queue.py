"""Per-chat queue for serial message processing.

Each chat has its own queue, ensuring messages within a chat are processed
serially while different chats are processed in parallel.
Reference: OpenClaw extensions/feishu/src/queue.ts
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, TypeVar

from loguru import logger

T = TypeVar("T")


class PerChatQueue:
    """Per-chat queue ensuring serial processing within each chat.

    Features:
    - Independent queue per chat_id
    - Parallel processing across different chats
    - Exception isolation - one task failure doesn't block queue
    - Graceful shutdown with pending task completion

    Example:
        queue = PerChatQueue()

        async def handle_message():
            # Process message
            return "result"

        result = await queue.enqueue("chat_123", handle_message)
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[tuple[Callable[[], Awaitable[T]], asyncio.Future[T]]]] = defaultdict(asyncio.Queue)
        self._workers: dict[str, asyncio.Task] = {}
        self._shutdown = False

    async def enqueue(self, chat_id: str, handler: Callable[[], Awaitable[T]]) -> T:
        """Enqueue a task for specific chat.

        Args:
            chat_id: Unique chat identifier
            handler: Async function to execute

        Returns:
            Task result

        Raises:
            RuntimeError: If queue is shutting down
        """
        if self._shutdown:
            raise RuntimeError("Queue is shutting down")

        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()

        # Ensure worker exists for this chat
        if chat_id not in self._workers or self._workers[chat_id].done():
            self._workers[chat_id] = asyncio.create_task(
                self._worker_loop(chat_id),
                name=f"queue-worker-{chat_id}",
            )

        await self._queues[chat_id].put((handler, future))
        return await future

    async def _worker_loop(self, chat_id: str) -> None:
        """Worker loop processing tasks for a specific chat."""
        logger.debug("Queue worker started for chat: {}", chat_id)
        queue = self._queues[chat_id]

        try:
            while True:
                try:
                    # Wait for task with timeout to allow periodic cleanup
                    handler, future = await asyncio.wait_for(queue.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    # No tasks for 60s, exit worker (will restart on next enqueue)
                    break

                try:
                    result = await handler()
                    if not future.done():
                        future.set_result(result)
                except Exception as e:
                    logger.exception("Task failed in chat {}: {}", chat_id, e)
                    if not future.done():
                        future.set_exception(e)
                finally:
                    queue.task_done()

        except asyncio.CancelledError:
            logger.debug("Queue worker cancelled for chat: {}", chat_id)
            raise
        finally:
            logger.debug("Queue worker stopped for chat: {}", chat_id)
            # Clean up if this worker is still registered
            if self._workers.get(chat_id) is asyncio.current_task():
                self._workers.pop(chat_id, None)

    async def stop(self) -> None:
        """Stop all workers and cleanup."""
        self._shutdown = True

        # Cancel all workers
        for chat_id, worker in list(self._workers.items()):
            worker.cancel()

        # Wait for workers to finish
        if self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)

        self._workers.clear()
        self._queues.clear()

        logger.info("PerChatQueue stopped")

    def get_pending_count(self, chat_id: str | None = None) -> int:
        """Get number of pending tasks.

        Args:
            chat_id: Specific chat to check, or None for all

        Returns:
            Number of pending tasks
        """
        if chat_id is not None:
            return self._queues.get(chat_id, asyncio.Queue()).qsize()

        return sum(q.qsize() for q in self._queues.values())

    def get_active_chats(self) -> list[str]:
        """Get list of chat IDs with active workers.

        Returns:
            List of chat IDs
        """
        return [
            chat_id
            for chat_id, worker in self._workers.items()
            if not worker.done()
        ]

    async def wait_for_chat(self, chat_id: str, timeout: float | None = None) -> bool:
        """Wait for all tasks in a chat to complete.

        Args:
            chat_id: Chat ID to wait for
            timeout: Maximum wait time in seconds

        Returns:
            True if all tasks completed, False if timeout
        """
        queue = self._queues.get(chat_id)
        if not queue:
            return True

        try:
            await asyncio.wait_for(queue.join(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
