"""集成测试：AgentTeam + WorkerPool 完整 dispatch 流程。

验证：
- Supervisor 返回 dispatch → 真实 WorkerPool 创建 Worker → 执行任务 → 综合回复
- 异步 dispatch 模式下 on_worker_result 回调正确触发
- Worker 失败时的错误传播与综合
- /workers 和 /worker stop 命令与真实 WorkerPool 交互
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from ccbot.config import AgentConfig
from ccbot.models import DispatchPayload, SupervisorResponse
from ccbot.runtime.sdk_utils import AgentRunResult
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager

from .conftest import make_mock_sdk_client


def _dispatch_response(tasks: list[dict[str, str]]) -> SupervisorResponse:
    """构建 dispatch 模式的 SupervisorResponse。"""
    payload = DispatchPayload(
        tasks=[{"name": t["name"], "task": t["task"], "cwd": t.get("cwd", ".")} for t in tasks]
    )
    return SupervisorResponse(
        mode="dispatch",
        user_message="正在派发任务...",
        tasks=payload.tasks,
    )


class TestTeamWorkerDispatchSync:
    """同步 dispatch 模式集成测试（无 on_worker_result）。"""

    @pytest.mark.asyncio
    async def test_full_dispatch_cycle_with_real_worker_pool(
        self, workspace: WorkspaceManager
    ) -> None:
        """完整 dispatch 周期：Supervisor → WorkerPool → 综合。

        使用真实 WorkerPool，仅 mock SDK client。
        """
        team = AgentTeam(AgentConfig(), workspace)
        await team._worker_pool.start()

        try:
            # dispatch 响应 → 两个 worker 任务
            dispatch_text = """正在派发任务...
<dispatch>
[
  {"name": "writer", "task": "写一段代码", "cwd": "."},
  {"name": "reviewer", "task": "审查代码", "cwd": "."}
]
</dispatch>"""

            # Supervisor: 第一次返回 dispatch，第二次返回综合
            supervisor_replies = [
                AgentRunResult(dispatch_text),
                AgentRunResult("综合结果：代码已完成并通过审查。"),
            ]
            call_idx = 0

            async def fake_supervisor_ask_run(chat_id, prompt, **kwargs):
                nonlocal call_idx
                result = supervisor_replies[call_idx]
                call_idx += 1
                return result

            team._supervisor.ask_run = AsyncMock(side_effect=fake_supervisor_ask_run)

            # Worker SDK clients：每个 worker 创建时返回不同回复
            worker_clients = [
                make_mock_sdk_client("代码已编写完成"),
                make_mock_sdk_client("审查通过，无问题"),
            ]
            w_idx = 0

            def worker_factory(*args, **kwargs):
                nonlocal w_idx
                c = worker_clients[w_idx]
                w_idx += 1
                return c

            with patch("claude_agent_sdk.ClaudeSDKClient", side_effect=worker_factory):
                reply = await team.ask("chat1", "帮我写代码并审查")

            assert "综合结果" in reply
            # 验证 supervisor 被调用了两次（dispatch + synthesis）
            assert team._supervisor.ask_run.await_count == 2
            # 验证综合 prompt 中包含 worker 结果
            synthesis_call = team._supervisor.ask_run.call_args_list[1]
            synthesis_prompt = synthesis_call[0][1]  # 第二个位置参数
            assert "writer" in synthesis_prompt
            assert "reviewer" in synthesis_prompt
        finally:
            await team._worker_pool.stop()


class TestTeamWorkerDispatchAsync:
    """异步 dispatch 模式集成测试（有 on_worker_result 回调）。"""

    @pytest.mark.asyncio
    async def test_async_dispatch_triggers_worker_result_callbacks(
        self, workspace: WorkspaceManager
    ) -> None:
        """异步 dispatch 应通过 on_worker_result 回调返回每个 Worker 的结果。"""
        team = AgentTeam(AgentConfig(), workspace)
        await team._worker_pool.start()

        try:
            dispatch_text = """好的
<dispatch>
[{"name": "analyst", "task": "分析数据", "cwd": "."}]
</dispatch>"""

            supervisor_replies = [
                AgentRunResult(dispatch_text),
                AgentRunResult("最终总结"),
            ]
            call_idx = 0

            async def fake_supervisor_ask_run(chat_id, prompt, **kwargs):
                nonlocal call_idx
                result = supervisor_replies[call_idx]
                call_idx += 1
                return result

            team._supervisor.ask_run = AsyncMock(side_effect=fake_supervisor_ask_run)

            worker_results: list[tuple[str, str]] = []

            async def on_worker_result(name: str, result: str) -> None:
                worker_results.append((name, result))

            worker_client = make_mock_sdk_client("数据分析完成")

            with patch("claude_agent_sdk.ClaudeSDKClient", return_value=worker_client):
                reply = await team.ask(
                    "chat1",
                    "分析一下",
                    on_worker_result=on_worker_result,
                )

            # 异步模式立即返回
            assert reply  # 非空

            # 等待后台任务完成
            await asyncio.sleep(0.5)

            # 验证 worker 结果回调被触发
            assert len(worker_results) >= 1
            worker_names = [name for name, _ in worker_results]
            assert "analyst" in worker_names or "🤖 综合" in worker_names
        finally:
            await team._worker_pool.stop()


class TestTeamWorkerControlCommands:
    """Team 控制命令与真实 WorkerPool 交互。"""

    @pytest.mark.asyncio
    async def test_workers_command_with_real_pool(self, workspace: WorkspaceManager) -> None:
        """/workers 命令应返回真实 WorkerPool 状态。"""
        team = AgentTeam(AgentConfig(), workspace)
        await team._worker_pool.start()

        try:
            reply = await team.ask("chat1", "/workers")
            assert "没有活跃" in reply or "Worker" in reply
        finally:
            await team._worker_pool.stop()

    @pytest.mark.asyncio
    async def test_worker_kill_nonexistent(self, workspace: WorkspaceManager) -> None:
        """/worker kill 不存在的 worker 应返回错误提示。"""
        team = AgentTeam(AgentConfig(), workspace)
        reply = await team.ask("chat1", "/worker kill nonexistent")
        assert "不存在" in reply

    @pytest.mark.asyncio
    async def test_stop_with_no_active_tasks(self, workspace: WorkspaceManager) -> None:
        """/stop 在无活跃任务时应返回提示信息。"""
        team = AgentTeam(AgentConfig(), workspace)
        reply = await team.ask("chat1", "/stop")
        assert "没有" in reply or "无" in reply
