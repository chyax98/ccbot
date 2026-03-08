"""AgentPool: ClaudeSDKClient 生命周期管理。

特性：
- 按 chat_id 缓存和复用 client
- 空闲超时自动释放
- 优雅关闭时保存历史记录
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from ccbot.config import AgentConfig
from ccbot.workspace import WorkspaceManager

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

# 清理循环检查间隔（秒）
_CLEANUP_CHECK_INTERVAL = 60


class AgentPool:
    """管理 ClaudeSDKClient 实例的池化组件。

    每个 chat_id 对应一个 client，空闲超过 timeout 后自动关闭以释放资源。

    Example:
        pool = AgentPool(config, workspace, idle_timeout=1800)

        # 获取或创建 client
        client = await pool.acquire("chat_123")
        try:
            await client.query("hello")
        finally:
            await pool.release("chat_123")

        # 停止时清理所有 client
        await pool.stop()
    """

    def __init__(
        self,
        config: AgentConfig,
        workspace: WorkspaceManager | None = None,
        extra_system_prompt: str = "",
        idle_timeout: int | None = None,
    ) -> None:
        """初始化 AgentPool。

        Args:
            config: Agent 配置
            workspace: 工作空间管理器
            extra_system_prompt: 额外的 system prompt（如 Supervisor/Worker 提示）
            idle_timeout: 空闲超时秒数，None 时使用 config.idle_timeout
        """
        self._config = config
        self._workspace = workspace
        self._extra_system_prompt = extra_system_prompt
        self._idle_timeout = idle_timeout if idle_timeout is not None else config.idle_timeout

        self._clients: dict[str, ClaudeSDKClient] = {}
        self._last_used: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """启动池，开始空闲清理任务。"""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="agent-pool-cleanup",
        )
        logger.info("AgentPool 已启动，空闲超时: {}s", self._idle_timeout)

    async def stop(self) -> None:
        """停止池，关闭所有 client。"""
        self._running = False

        # 停止清理任务
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        # 关闭所有 client
        await self._close_all()
        logger.info("AgentPool 已停止")

    async def acquire(self, chat_id: str) -> ClaudeSDKClient:
        """获取指定 chat_id 的 client，不存在则创建。

        Args:
            chat_id: 会话唯一标识

        Returns:
            ClaudeSDKClient 实例
        """
        if chat_id not in self._clients:
            client = await self._create_client(chat_id)
            self._clients[chat_id] = client
            logger.info("创建新 client: chat_id={}", chat_id)

        self._last_used[chat_id] = time.time()
        return self._clients[chat_id]

    async def release(self, chat_id: str) -> None:
        """释放 client（更新最后使用时间）。

        Args:
            chat_id: 会话唯一标识
        """
        if chat_id in self._clients:
            self._last_used[chat_id] = time.time()

    async def close(self, chat_id: str) -> None:
        """主动关闭指定 chat_id 的 client。

        Args:
            chat_id: 会话唯一标识
        """
        client = self._clients.pop(chat_id, None)
        self._last_used.pop(chat_id, None)
        self._locks.pop(chat_id, None)

        if client:
            try:
                await client.disconnect()
                logger.info("关闭 client: chat_id={}", chat_id)
            except Exception as e:
                logger.warning("关闭 client 出错: chat_id={} error={}", chat_id, e)

    async def interrupt(self, chat_id: str) -> bool:
        """中断指定 chat_id 正在执行的查询。

        Args:
            chat_id: 会话唯一标识

        Returns:
            True 如果成功中断，False 如果 client 不存在
        """
        client = self._clients.get(chat_id)
        if not client:
            return False
        try:
            await client.interrupt()
            return True
        except Exception as e:
            logger.warning("中断 client 出错: chat_id={} error={}", chat_id, e)
            return False

    def get_stats(self) -> dict[str, int | float]:
        """获取池统计信息。

        Returns:
            {
                "active_clients": 活跃 client 数,
                "idle_timeout": 空闲超时秒数,
            }
        """
        return {
            "active_clients": len(self._clients),
            "idle_timeout": self._idle_timeout,
        }

    async def _create_client(self, chat_id: str) -> ClaudeSDKClient:
        """创建新的 ClaudeSDKClient。"""
        import os

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        # 构建选项
        if self._config.system_prompt:
            system_prompt = self._config.system_prompt
        elif self._workspace:
            system_prompt = self._workspace.build_system_prompt()
        else:
            system_prompt = ""

        # 添加额外的 system prompt
        if self._extra_system_prompt:
            system_prompt = f"{system_prompt}\n\n---\n\n{self._extra_system_prompt}".strip()

        cwd = self._config.cwd or (str(self._workspace.path) if self._workspace else ".")

        kwargs: dict[str, Any] = {
            "system_prompt": system_prompt,
            "cwd": cwd,
            "permission_mode": "bypassPermissions",
        }
        if self._config.model:
            kwargs["model"] = self._config.model
        if self._config.max_turns:
            kwargs["max_turns"] = self._config.max_turns
        if self._config.mcp_servers:
            kwargs["mcp_servers"] = self._config.mcp_servers
        # config.env 优先于系统环境变量：系统 env 作为 base，config 覆盖
        kwargs["env"] = {**os.environ, **self._config.env}

        options = ClaudeAgentOptions(**kwargs)
        client = ClaudeSDKClient(options)
        await client.connect()
        return client

    async def _cleanup_loop(self) -> None:
        """后台任务：定期清理空闲 client。"""
        while self._running:
            try:
                await asyncio.sleep(_CLEANUP_CHECK_INTERVAL)
                await self._cleanup_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("清理任务出错: {}", e)

    async def _cleanup_idle(self) -> None:
        """关闭超过空闲超时的 client。"""
        if self._idle_timeout <= 0:
            return

        now = time.time()
        idle_clients = [
            chat_id
            for chat_id, last_used in self._last_used.items()
            if now - last_used > self._idle_timeout
        ]

        for chat_id in idle_clients:
            logger.info("清理空闲 client: chat_id={} (idle > {}s)", chat_id, self._idle_timeout)
            await self.close(chat_id)

    async def _close_all(self) -> None:
        """关闭所有 client。"""
        clients = list(self._clients.items())
        self._clients.clear()
        self._last_used.clear()
        self._locks.clear()

        for chat_id, client in clients:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
                logger.debug("关闭 client: chat_id={}", chat_id)
            except Exception:
                # Shutdown 期间 cancel scope 跨 task 是预期的，子进程会随主进程退出
                logger.debug("关闭 client 跳过: chat_id={}", chat_id)
