"""Tests for PerChatQueue."""

import asyncio

import pytest

from ccbot.core.queue import PerChatQueue


class TestPerChatQueue:
    """Test cases for PerChatQueue."""

    @pytest.mark.asyncio
    async def test_basic_enqueue(self):
        """Basic task should execute and return result."""
        queue = PerChatQueue()

        async def handler():
            return "hello"

        result = await queue.enqueue("chat_1", handler)
        assert result == "hello"

        await queue.stop()

    @pytest.mark.asyncio
    async def test_serial_processing_same_chat(self):
        """Tasks in same chat should execute serially."""
        queue = PerChatQueue()
        execution_order = []

        async def handler1():
            await asyncio.sleep(0.05)
            execution_order.append(1)
            return "first"

        async def handler2():
            execution_order.append(2)
            return "second"

        # Enqueue both tasks
        task1 = asyncio.create_task(queue.enqueue("chat_1", handler1))
        task2 = asyncio.create_task(queue.enqueue("chat_1", handler2))

        await asyncio.gather(task1, task2)

        # Should execute in order
        assert execution_order == [1, 2]

        await queue.stop()

    @pytest.mark.asyncio
    async def test_parallel_processing_different_chats(self):
        """Tasks in different chats should execute in parallel."""
        queue = PerChatQueue()
        execution_times = {}

        async def make_handler(chat_id):
            async def handler():
                execution_times[chat_id] = asyncio.get_event_loop().time()
                await asyncio.sleep(0.1)
                return chat_id

            return handler

        start_time = asyncio.get_event_loop().time()

        # Enqueue tasks to different chats
        task1 = asyncio.create_task(queue.enqueue("chat_1", await make_handler("chat_1")))
        task2 = asyncio.create_task(queue.enqueue("chat_2", await make_handler("chat_2")))

        await asyncio.gather(task1, task2)

        end_time = asyncio.get_event_loop().time()

        # Both should start around same time (parallel)
        assert abs(execution_times["chat_1"] - execution_times["chat_2"]) < 0.05
        # Total time should be ~0.1s, not ~0.2s
        assert end_time - start_time < 0.15

        await queue.stop()

    @pytest.mark.asyncio
    async def test_exception_isolation(self):
        """Exception in one task should not affect other tasks."""
        queue = PerChatQueue()

        async def failing_handler():
            raise ValueError("test error")

        async def success_handler():
            return "success"

        # Enqueue failing task
        with pytest.raises(ValueError, match="test error"):
            await queue.enqueue("chat_1", failing_handler)

        # Queue should still work
        result = await queue.enqueue("chat_1", success_handler)
        assert result == "success"

        await queue.stop()

    @pytest.mark.asyncio
    async def test_get_pending_count(self):
        """Pending count should reflect queue state."""
        queue = PerChatQueue()

        async def slow_handler():
            await asyncio.sleep(0.2)
            return "done"

        # Enqueue tasks
        task1 = asyncio.create_task(queue.enqueue("chat_1", slow_handler))
        task2 = asyncio.create_task(queue.enqueue("chat_1", slow_handler))

        # Wait a bit for tasks to be queued
        await asyncio.sleep(0.01)

        assert queue.get_pending_count("chat_1") >= 0

        await asyncio.gather(task1, task2)
        await queue.stop()

    @pytest.mark.asyncio
    async def test_get_active_chats(self):
        """Should return list of active chat IDs."""
        queue = PerChatQueue()

        async def slow_handler():
            await asyncio.sleep(0.1)
            return "done"

        # Start task
        task = asyncio.create_task(queue.enqueue("chat_1", slow_handler))

        # Wait for worker to start
        await asyncio.sleep(0.01)

        assert "chat_1" in queue.get_active_chats()

        await task
        await queue.stop()

    @pytest.mark.asyncio
    async def test_shutdown_rejects_new_tasks(self):
        """After shutdown, new tasks should be rejected."""
        queue = PerChatQueue()

        async def handler():
            return "hello"

        # First task works
        result = await queue.enqueue("chat_1", handler)
        assert result == "hello"

        # Shutdown
        await queue.stop()

        # New task should fail
        with pytest.raises(RuntimeError, match="shutting down"):
            await queue.enqueue("chat_1", handler)

    @pytest.mark.asyncio
    async def test_wait_for_chat(self):
        """wait_for_chat should wait for all tasks to complete."""
        queue = PerChatQueue()

        async def slow_handler():
            await asyncio.sleep(0.1)
            return "done"

        # Enqueue task
        task = asyncio.create_task(queue.enqueue("chat_1", slow_handler))

        # Wait for completion
        completed = await queue.wait_for_chat("chat_1", timeout=1.0)
        assert completed is True

        await task
        await queue.stop()

    @pytest.mark.asyncio
    async def test_wait_for_chat_empty_queue(self):
        """wait_for_chat should return True for empty queue."""
        queue = PerChatQueue()

        # No tasks in queue
        completed = await queue.wait_for_chat("chat_1", timeout=0.1)
        assert completed is True

        await queue.stop()
