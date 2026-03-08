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
from ccbot.memory import MemoryStore
from ccbot.observability import configure_langsmith_once
from ccbot.runtime.profiles import RuntimeRole, build_sdk_options
from ccbot.workspace import WorkspaceManager

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

_CLEANUP_CHECK_INTERVAL = 60


class AgentPool:
    """管理 ClaudeSDKClient 实例的池化组件。"""

    def __init__(
        self,
        config: AgentConfig,
        workspace: WorkspaceManager | None = None,
        extra_system_prompt: str = "",
        idle_timeout: int | None = None,
        output_format: dict[str, Any] | None = None,
        role: RuntimeRole = RuntimeRole.SUPERVISOR,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._config = config
        self._workspace = workspace
        self._extra_system_prompt = extra_system_prompt
        self._idle_timeout = idle_timeout if idle_timeout is not None else config.idle_timeout
        self._output_format = output_format
        self._role = role
        self._memory_store = memory_store

        self._clients: dict[str, ClaudeSDKClient] = {}
        self._last_used: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="agent-pool-cleanup",
        )
        logger.info("AgentPool 已启动，空闲超时: {}s", self._idle_timeout)

    async def stop(self) -> None:
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        await self._close_all()
        logger.info("AgentPool 已停止")

    async def acquire(self, chat_id: str) -> ClaudeSDKClient:
        if chat_id not in self._clients:
            client = await self._create_client(chat_id)
            self._clients[chat_id] = client
            logger.info("创建新 client: chat_id={}", chat_id)

        self._last_used[chat_id] = time.time()
        return self._clients[chat_id]

    async def release(self, chat_id: str) -> None:
        if chat_id in self._clients:
            self._last_used[chat_id] = time.time()

    async def close(self, chat_id: str) -> None:
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
        return {
            "active_clients": len(self._clients),
            "idle_timeout": self._idle_timeout,
        }

    async def _create_client(self, chat_id: str) -> ClaudeSDKClient:
        """创建新的 ClaudeSDKClient。"""
        configure_langsmith_once(self._config)

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        if self._config.system_prompt:
            base_prompt = self._config.system_prompt
        elif self._workspace:
            base_prompt = self._workspace.build_system_prompt()
        else:
            base_prompt = ""

        cwd = self._config.cwd or (str(self._workspace.path) if self._workspace else ".")

        memory_prompt = ""
        resume_session_id = ""
        if self._role == RuntimeRole.SUPERVISOR and self._memory_store is not None:
            memory_prompt = self._memory_store.build_memory_prompt(chat_id)
            if self._config.supervisor_resume_enabled:
                resume_session_id = self._memory_store.load(chat_id).runtime_session_id

        extra_prompt = "\n\n---\n\n".join(
            part for part in (memory_prompt, self._extra_system_prompt) if part
        )

        kwargs = build_sdk_options(
            self._config,
            role=self._role,
            cwd=cwd,
            base_prompt=base_prompt,
            extra_prompt=extra_prompt,
            model=self._config.model,
            max_turns=self._config.max_turns,
            allowed_tools=self._config.allowed_tools or None,
            output_format=self._output_format,
        )

        if resume_session_id:
            kwargs["resume"] = resume_session_id
            kwargs["continue_conversation"] = True

        options = ClaudeAgentOptions(**kwargs)
        client = ClaudeSDKClient(options)
        await client.connect()
        return client

    async def _cleanup_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(_CLEANUP_CHECK_INTERVAL)
                await self._cleanup_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("清理任务出错: {}", e)

    async def _cleanup_idle(self) -> None:
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
        clients = list(self._clients.items())
        self._clients.clear()
        self._last_used.clear()
        self._locks.clear()

        for chat_id, client in clients:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3.0)
                logger.debug("关闭 client: chat_id={}", chat_id)
            except Exception:
                logger.debug("关闭 client 跳过: chat_id={}", chat_id)
