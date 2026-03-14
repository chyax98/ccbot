"""集成测试：HeartbeatService + AgentTeam 交互。

验证：
- HEARTBEAT.md 有活跃任务时触发 execute callback
- execute callback 返回值传递给 notify callback
- 无活跃任务时不触发
- heartbeat 文件不存在时安全跳过
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ccbot.heartbeat import HeartbeatService, _has_active_tasks


class TestHeartbeatDetection:
    """HeartbeatService 活跃任务检测（纯逻辑，无 mock）。"""

    def test_detects_active_tasks(self) -> None:
        content = """# HEARTBEAT
## Active Tasks
- 检查服务器状态
- 清理日志
## Completed
- 部署完成
"""
        assert _has_active_tasks(content) is True

    def test_empty_active_section(self) -> None:
        content = """# HEARTBEAT
## Active Tasks
## Completed
- 已完成任务
"""
        assert _has_active_tasks(content) is False

    def test_no_active_section(self) -> None:
        content = """# HEARTBEAT
这只是一个普通文件。
"""
        assert _has_active_tasks(content) is False

    def test_comments_ignored(self) -> None:
        content = """# HEARTBEAT
## Active Tasks
<!-- 注释不算活跃任务 -->
## Completed
"""
        assert _has_active_tasks(content) is False


class TestHeartbeatServiceIntegration:
    """HeartbeatService 端到端集成。"""

    @pytest.mark.asyncio
    async def test_tick_triggers_execute_and_notify(self, tmp_path: Path) -> None:
        """有活跃任务时 _tick 应触发 execute + notify 回调。"""
        heartbeat_file = tmp_path / "HEARTBEAT.md"
        heartbeat_file.write_text(
            "# HEARTBEAT\n## Active Tasks\n- 检查磁盘空间\n",
            encoding="utf-8",
        )

        execute_calls: list[str] = []
        notify_calls: list[str] = []

        async def on_execute(prompt: str) -> str:
            execute_calls.append(prompt)
            return "磁盘空间充足"

        async def on_notify(content: str) -> None:
            notify_calls.append(content)

        service = HeartbeatService(
            heartbeat_file=heartbeat_file,
            on_execute=on_execute,
            on_notify=on_notify,
            interval_s=3600,
        )

        await service._tick()

        assert len(execute_calls) == 1
        assert "检查磁盘空间" in execute_calls[0]
        assert len(notify_calls) == 1
        assert "磁盘空间充足" in notify_calls[0]

    @pytest.mark.asyncio
    async def test_tick_skips_when_no_active_tasks(self, tmp_path: Path) -> None:
        """无活跃任务时 _tick 不应触发回调。"""
        heartbeat_file = tmp_path / "HEARTBEAT.md"
        heartbeat_file.write_text(
            "# HEARTBEAT\n## Active Tasks\n## Completed\n- done\n",
            encoding="utf-8",
        )

        on_execute = AsyncMock(return_value="should not run")
        on_notify = AsyncMock()

        service = HeartbeatService(
            heartbeat_file=heartbeat_file,
            on_execute=on_execute,
            on_notify=on_notify,
        )

        await service._tick()

        on_execute.assert_not_awaited()
        on_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tick_skips_when_file_missing(self, tmp_path: Path) -> None:
        """heartbeat 文件不存在时 _tick 应安全跳过。"""
        heartbeat_file = tmp_path / "HEARTBEAT.md"
        # 不创建文件

        on_execute = AsyncMock(return_value="should not run")
        on_notify = AsyncMock()

        service = HeartbeatService(
            heartbeat_file=heartbeat_file,
            on_execute=on_execute,
            on_notify=on_notify,
        )

        await service._tick()

        on_execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_error_does_not_crash_service(self, tmp_path: Path) -> None:
        """execute callback 异常不应导致服务崩溃。"""
        heartbeat_file = tmp_path / "HEARTBEAT.md"
        heartbeat_file.write_text(
            "# HEARTBEAT\n## Active Tasks\n- 执行任务\n",
            encoding="utf-8",
        )

        async def on_execute(prompt: str) -> str:
            raise RuntimeError("执行失败")

        service = HeartbeatService(
            heartbeat_file=heartbeat_file,
            on_execute=on_execute,
            on_notify=AsyncMock(),
        )

        # 不应抛出异常
        await service._tick()

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, tmp_path: Path) -> None:
        """HeartbeatService 应能正常启动和停止。"""
        heartbeat_file = tmp_path / "HEARTBEAT.md"

        service = HeartbeatService(
            heartbeat_file=heartbeat_file,
            on_execute=AsyncMock(return_value="ok"),
            on_notify=AsyncMock(),
            interval_s=1,
        )

        await service.start()
        assert service._running is True
        assert service._task is not None

        service.stop()
        assert service._running is False
        # 等待 task 被 cancel
        await asyncio.sleep(0.1)
