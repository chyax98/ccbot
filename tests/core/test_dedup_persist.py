"""Tests for DedupCache 持久化路径跟踪和 stop() 行为。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.core.dedup import DedupCache


class TestDedupPersistPathTracking:
    """验证 DedupCache.stop() 使用 schedule_persist 的路径。"""

    @pytest.mark.asyncio
    async def test_stop_persists_to_scheduled_path(self) -> None:
        """stop() 应使用 schedule_persist 时记录的路径进行最终持久化。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DedupCache()
            cache.check("msg_1")

            # 启动定时持久化
            cache.schedule_persist(tmpdir, "test_ns", interval_sec=9999)

            # 验证路径被记录
            assert cache._persist_base_path == tmpdir
            assert cache._persist_namespace == "test_ns"

            # stop 应该触发最终持久化
            await cache.stop()

            # 验证文件已写入
            persist_file = Path(tmpdir) / "test_ns.json"
            assert persist_file.exists()

    @pytest.mark.asyncio
    async def test_stop_without_schedule_does_not_persist(self) -> None:
        """未调用 schedule_persist 时，stop 不应尝试持久化。"""
        cache = DedupCache()
        cache.check("msg_1")
        assert cache._dirty is True

        # stop 不应崩溃，即使没有 schedule_persist
        await cache.stop()

        # persist_base_path 为 None，所以不会持久化
        assert cache._persist_base_path is None

    @pytest.mark.asyncio
    async def test_stop_not_dirty_skips_persist(self) -> None:
        """没有新数据时 stop 不应触发持久化。"""
        cache = DedupCache()

        with patch.object(cache, "persist", new_callable=AsyncMock) as mock_persist:
            cache.schedule_persist("/tmp", "ns")
            await cache.stop()

        mock_persist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_schedule_persist_idempotent(self) -> None:
        """多次调用 schedule_persist 不应创建多个后台任务。"""
        cache = DedupCache()

        cache.schedule_persist("/tmp/a", "ns1", interval_sec=9999)
        first_task = cache._persist_task
        assert first_task is not None

        # 再次调用应该被忽略
        cache.schedule_persist("/tmp/b", "ns2", interval_sec=9999)
        assert cache._persist_task is first_task

        # 路径保持第一次的值
        assert cache._persist_base_path == "/tmp/a"
        assert cache._persist_namespace == "ns1"

        await cache.stop()

    @pytest.mark.asyncio
    async def test_len_reflects_entries(self) -> None:
        """__len__ 应返回缓存条目数。"""
        cache = DedupCache()
        assert len(cache) == 0

        cache.check("a")
        cache.check("b")
        assert len(cache) == 2

        cache.check("a")  # 重复不增加
        assert len(cache) == 2
