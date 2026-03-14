"""Tests for WorkerPool 持久化 Worker 池（直接管理 ClaudeSDKClient）。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccbot.config import AgentConfig
from ccbot.models import WorkerTask
from ccbot.runtime.worker_pool import WorkerPool, WorkerStatus


@pytest.fixture
def base_config() -> AgentConfig:
    return AgentConfig(model="sonnet", max_turns=10)


@pytest.fixture
def pool(base_config: AgentConfig, tmp_path: Path) -> WorkerPool:
    return WorkerPool(base_config, workspace_path=tmp_path)


def _make_task(name: str = "fe", cwd: str = ".", task: str = "build UI") -> WorkerTask:
    return WorkerTask(name=name, cwd=cwd, task=task)


def _mock_client() -> MagicMock:
    """创建 mock ClaudeSDKClient。"""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    return client


class TestWorkerPoolBasic:
    """基础 Worker 管理测试。"""

    @pytest.mark.asyncio
    async def test_get_or_create_new_worker(self, pool: WorkerPool) -> None:
        """首次请求应创建新 Worker。"""
        mock = _mock_client()
        with patch.object(pool, "_create_client", return_value=mock):
            await pool.get_or_create(_make_task())
        assert pool.has_worker("fe")

    @pytest.mark.asyncio
    async def test_get_or_create_reuses_existing(self, pool: WorkerPool) -> None:
        """相同 name 应复用已有 Worker。"""
        mock = _mock_client()
        with patch.object(pool, "_create_client", return_value=mock) as create:
            await pool.get_or_create(_make_task())
            await pool.get_or_create(_make_task(task="another"))
        assert create.await_count == 1

    @pytest.mark.asyncio
    async def test_send_task(self, pool: WorkerPool) -> None:
        """send 应调用 query_and_collect 并更新元数据。"""
        mock = _mock_client()
        with (
            patch.object(pool, "_create_client", return_value=mock),
            patch("ccbot.runtime.worker_pool.query_and_collect", return_value="done!") as qac,
        ):
            await pool.get_or_create(_make_task())
            result = await pool.send("fe", "build login page")

        assert result == "done!"
        qac.assert_awaited_once()
        info = pool._info["fe"]
        assert info.task_count == 1
        assert info.status == WorkerStatus.IDLE

    @pytest.mark.asyncio
    async def test_send_multiple_tasks(self, pool: WorkerPool) -> None:
        """多次 send 应累加 task_count。"""
        mock = _mock_client()
        with (
            patch.object(pool, "_create_client", return_value=mock),
            patch("ccbot.runtime.worker_pool.query_and_collect", return_value="ok"),
        ):
            await pool.get_or_create(_make_task())
            await pool.send("fe", "task 1")
            await pool.send("fe", "task 2")
            await pool.send("fe", "task 3")
        assert pool._info["fe"].task_count == 3

    @pytest.mark.asyncio
    async def test_send_nonexistent_worker_raises(self, pool: WorkerPool) -> None:
        """向不存在的 Worker 发送任务应抛出 KeyError。"""
        with pytest.raises(KeyError, match="不存在"):
            await pool.send("ghost", "task")

    @pytest.mark.asyncio
    async def test_kill_worker(self, pool: WorkerPool) -> None:
        """kill 应 disconnect 并移除 Worker。"""
        mock = _mock_client()
        with patch.object(pool, "_create_client", return_value=mock):
            await pool.get_or_create(_make_task())
            await pool.kill("fe")

        assert not pool.has_worker("fe")
        mock.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_kill_nonexistent_is_noop(self, pool: WorkerPool) -> None:
        await pool.kill("ghost")


class TestWorkerPoolStatus:
    """状态查询和格式化测试。"""

    @pytest.mark.asyncio
    async def test_list_workers(self, pool: WorkerPool) -> None:
        mock = _mock_client()
        with patch.object(pool, "_create_client", return_value=mock):
            await pool.get_or_create(_make_task("fe", ".", "t1"))
            await pool.get_or_create(_make_task("be", ".", "t2"))
        workers = pool.list_workers()
        assert len(workers) == 2
        assert {w.name for w in workers} == {"fe", "be"}

    def test_format_status_empty(self, pool: WorkerPool) -> None:
        assert pool.format_status() == ""

    @pytest.mark.asyncio
    async def test_format_status_with_workers(self, pool: WorkerPool) -> None:
        mock = _mock_client()
        with patch.object(pool, "_create_client", return_value=mock):
            await pool.get_or_create(_make_task())
        status = pool.format_status()
        assert "当前活跃 Workers" in status
        assert "fe" in status

    @pytest.mark.asyncio
    async def test_has_worker(self, pool: WorkerPool) -> None:
        assert not pool.has_worker("fe")
        mock = _mock_client()
        with patch.object(pool, "_create_client", return_value=mock):
            await pool.get_or_create(_make_task())
        assert pool.has_worker("fe")
        assert not pool.has_worker("be")


class TestWorkerPoolLifecycle:
    """生命周期管理测试。"""

    @pytest.mark.asyncio
    async def test_stop_closes_all_workers(self, pool: WorkerPool) -> None:
        m1, m2 = _mock_client(), _mock_client()
        with patch.object(pool, "_create_client", side_effect=[m1, m2]):
            await pool.get_or_create(_make_task("fe", ".", "t1"))
            await pool.get_or_create(_make_task("be", ".", "t2"))
        await pool.stop()
        m1.disconnect.assert_awaited_once()
        m2.disconnect.assert_awaited_once()
        assert len(pool._clients) == 0

    @pytest.mark.asyncio
    async def test_cleanup_idle_workers(self) -> None:
        config = AgentConfig(model="sonnet", worker_idle_timeout=1)
        pool = WorkerPool(config)
        mock = _mock_client()
        with patch.object(pool, "_create_client", return_value=mock):
            await pool.get_or_create(_make_task())
        pool._info["fe"].last_used = time.time() - 2
        await pool._cleanup_idle()
        assert not pool.has_worker("fe")

    @pytest.mark.asyncio
    async def test_running_worker_not_cleaned(self) -> None:
        config = AgentConfig(model="sonnet", worker_idle_timeout=1)
        pool = WorkerPool(config)
        mock = _mock_client()
        with patch.object(pool, "_create_client", return_value=mock):
            await pool.get_or_create(_make_task())
        pool._info["fe"].status = WorkerStatus.RUNNING
        pool._info["fe"].last_used = time.time() - 100
        await pool._cleanup_idle()
        assert pool.has_worker("fe")

    @pytest.mark.asyncio
    async def test_evicts_oldest_idle_worker_when_pool_limit_reached(self) -> None:
        config = AgentConfig(model="sonnet", max_pooled_workers=2)
        pool = WorkerPool(config)
        m1, m2, m3 = _mock_client(), _mock_client(), _mock_client()
        with patch.object(pool, "_create_client", side_effect=[m1, m2, m3]):
            await pool.get_or_create(_make_task("w1", ".", "t1"))
            await pool.get_or_create(_make_task("w2", ".", "t2"))
            pool._info["w1"].last_used = time.time() - 100
            pool._info["w2"].last_used = time.time() - 10
            await pool.get_or_create(_make_task("w3", ".", "t3"))

        assert not pool.has_worker("w1")
        assert pool.has_worker("w2")
        assert pool.has_worker("w3")
        m1.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_pool_limit_reached_without_idle_worker(self) -> None:
        config = AgentConfig(model="sonnet", max_pooled_workers=2)
        pool = WorkerPool(config)
        m1, m2, m3 = _mock_client(), _mock_client(), _mock_client()
        with patch.object(pool, "_create_client", side_effect=[m1, m2, m3]):
            await pool.get_or_create(_make_task("w1", ".", "t1"))
            await pool.get_or_create(_make_task("w2", ".", "t2"))
            pool._info["w1"].status = WorkerStatus.RUNNING
            pool._info["w2"].status = WorkerStatus.RUNNING
            with pytest.raises(RuntimeError, match="Worker 池已满"):
                await pool.get_or_create(_make_task("w3", ".", "t3"))

    @pytest.mark.asyncio
    async def test_send_error_resets_status(self, pool: WorkerPool) -> None:
        mock = _mock_client()
        with (
            patch.object(pool, "_create_client", return_value=mock),
            patch("ccbot.runtime.worker_pool.query_and_collect", side_effect=RuntimeError("oops")),
        ):
            await pool.get_or_create(_make_task())
            with pytest.raises(RuntimeError, match="oops"):
                await pool.send("fe", "broken")
        assert pool._info["fe"].status == WorkerStatus.IDLE
        assert pool._info["fe"].task_count == 0

    @pytest.mark.asyncio
    async def test_create_client_uses_claude_code_preset_and_project_settings(
        self, pool: WorkerPool
    ) -> None:
        """Worker 应保留 Claude Code 原生 prompt，并加载 cwd 下的项目级 settings。"""
        options_seen = {}

        class DummyOptions:
            def __init__(self, **kwargs):
                options_seen.update(kwargs)

        dummy_client = _mock_client()

        with (
            patch("ccbot.runtime.worker_pool._setup_worker_workspace"),
            patch("claude_agent_sdk.ClaudeAgentOptions", DummyOptions),
            patch("claude_agent_sdk.ClaudeSDKClient", return_value=dummy_client),
        ):
            await pool._create_client(_make_task())

        assert options_seen["system_prompt"]["type"] == "preset"
        assert options_seen["system_prompt"]["preset"] == "claude_code"
        assert "Working directory" in options_seen["system_prompt"]["append"]
        assert options_seen["setting_sources"] == ["user", "project"]
        assert options_seen["disallowed_tools"] == ["Agent", "SendMessage"]
        assert callable(options_seen["stderr"])
        assert options_seen["cwd"] == "."
        dummy_client.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_client_keeps_project_settings_when_env_is_injected(
        self, base_config: AgentConfig
    ) -> None:
        """Worker 注入 env 后仍同时加载 user + project 级 setting sources。"""
        base_config.env = {"FOO": "BAR"}
        pool = WorkerPool(base_config)
        options_seen = {}

        class DummyOptions:
            def __init__(self, **kwargs):
                options_seen.update(kwargs)

        dummy_client = _mock_client()

        with (
            patch("ccbot.runtime.worker_pool._setup_worker_workspace"),
            patch("claude_agent_sdk.ClaudeAgentOptions", DummyOptions),
            patch("claude_agent_sdk.ClaudeSDKClient", return_value=dummy_client),
        ):
            await pool._create_client(_make_task())

        assert options_seen["setting_sources"] == ["user", "project"]
        assert options_seen["disallowed_tools"] == ["Agent", "SendMessage"]
        assert callable(options_seen["stderr"])
        assert options_seen["settings"] == '{"env": {"FOO": "BAR"}}'

    @pytest.mark.asyncio
    async def test_stop_ignores_disconnect_base_exception(self, pool: WorkerPool) -> None:
        mock = _mock_client()
        mock.disconnect = AsyncMock(side_effect=asyncio.CancelledError())
        with patch.object(pool, "_create_client", return_value=mock):
            await pool.get_or_create(_make_task())
        await pool.stop()
        assert not pool.has_worker("fe")

    @pytest.mark.asyncio
    async def test_kill_disconnects_on_same_actor_task(self, pool: WorkerPool) -> None:
        task_ids: dict[str, int] = {}

        class DummyClient:
            async def interrupt(self) -> None:
                return None

            async def disconnect(self) -> None:
                task_ids["disconnect"] = id(asyncio.current_task())

        async def fake_create_client(task: WorkerTask, *, worker_key: str | None = None):
            _ = task
            _ = worker_key
            task_ids["create"] = id(asyncio.current_task())
            return DummyClient()

        with patch.object(pool, "_create_client", side_effect=fake_create_client):
            await pool.get_or_create(_make_task())
            await pool.kill("fe")

        assert task_ids["disconnect"] == task_ids["create"]

    @pytest.mark.asyncio
    async def test_same_worker_name_is_isolated_by_owner(self, pool: WorkerPool) -> None:
        m1, m2 = _mock_client(), _mock_client()
        with patch.object(pool, "_create_client", side_effect=[m1, m2]) as create:
            await pool.get_or_create(_make_task("reviewer", ".", "t1"), owner_id="chat-a")
            await pool.get_or_create(_make_task("reviewer", ".", "t2"), owner_id="chat-b")

        assert create.await_count == 2
        assert pool.has_worker("reviewer", owner_id="chat-a")
        assert pool.has_worker("reviewer", owner_id="chat-b")
        assert len(pool.list_workers(owner_id="chat-a")) == 1
        assert len(pool.list_workers(owner_id="chat-b")) == 1
        await pool.stop()

    @pytest.mark.asyncio
    async def test_resolve_cwd_dot_to_workspace(self, tmp_path: Path) -> None:
        """cwd='.' 应被解析为 workspace_path。"""
        config = AgentConfig(model="sonnet")
        ws = tmp_path / "my_ws"
        ws.mkdir()
        pool = WorkerPool(config, workspace_path=ws)
        assert pool._resolve_cwd(".") == str(ws)

    @pytest.mark.asyncio
    async def test_resolve_cwd_absolute_unchanged(self, pool: WorkerPool) -> None:
        """绝对路径 cwd 不应被修改。"""
        assert pool._resolve_cwd("/some/path") == "/some/path"

    @pytest.mark.asyncio
    async def test_resolve_cwd_dot_without_workspace(self) -> None:
        """无 workspace 时 '.' 保持不变。"""
        config = AgentConfig(model="sonnet")
        pool = WorkerPool(config, workspace_path=None)
        assert pool._resolve_cwd(".") == "."

    @pytest.mark.asyncio
    async def test_send_retries_once_after_process_exit(self, pool: WorkerPool) -> None:
        from claude_agent_sdk._errors import ProcessError

        broken = _mock_client()
        healthy = _mock_client()

        with (
            patch.object(pool, "_create_client", side_effect=[broken, healthy]),
            patch(
                "ccbot.runtime.worker_pool.query_and_collect",
                side_effect=[ProcessError("boom", exit_code=1), "ok"],
            ),
        ):
            await pool.get_or_create(_make_task())
            result = await pool.send("fe", "hello")

        assert result == "ok"
        broken.disconnect.assert_awaited_once()
        assert pool._info["fe"].task_count == 1
