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
from ccbot.runtime.profiles import RuntimeRole, build_sdk_options, join_prompt_parts
from ccbot.runtime.sdk_utils import build_stderr_capture
from ccbot.workspace import WorkspaceManager

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

_CLEANUP_CHECK_INTERVAL = 60
_CLAUDE_CODE_HOST_ENV_KEYS = frozenset(
    {
        "CLAUDECODE",
        # Host Claude Code sessions may export their own control-plane transport.
        # Nested SDK subprocesses must bootstrap a fresh transport instead of
        # inheriting the parent's live session wiring.
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
        "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
        "CLAUDE_CODE_REMOTE_SESSION_ID",
        "CLAUDE_CODE_REMOTE",
        "CLAUDE_CODE_TEAMMATE_COMMAND",
        "CLAUDE_CODE_PLAN_MODE_REQUIRED",
    }
)


def _sanitize_sdk_host_env() -> None:
    """Remove host-side env vars that can break nested Claude Code startup."""
    import os

    removed = []
    for key in _CLAUDE_CODE_HOST_ENV_KEYS:
        if key in os.environ:
            removed.append(key)
            del os.environ[key]

    if removed:
        logger.debug("移除宿主 Claude Code 环境变量: {}", ", ".join(sorted(removed)))


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
        sdk_mcp_servers: dict[str, Any] | None = None,
    ) -> None:
        self._config = config
        self._workspace = workspace
        self._extra_system_prompt = extra_system_prompt
        self._explicit_idle_timeout = idle_timeout  # None = 从 config 动态读取
        self._output_format = output_format
        self._role = role
        self._memory_store = memory_store
        self._sdk_mcp_servers = sdk_mcp_servers or {}

        self._clients: dict[str, ClaudeSDKClient] = {}
        self._last_used: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stderr_captures: dict[str, object] = {}
        self._running = False

    @property
    def _idle_timeout(self) -> int:
        if self._explicit_idle_timeout is not None:
            return self._explicit_idle_timeout
        return self._config.idle_timeout

    def set_sdk_mcp_servers(self, servers: dict[str, Any]) -> None:
        """延迟注入 SDK MCP servers（在 scheduler 初始化后调用）。"""
        self._sdk_mcp_servers.update(servers)

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
        self._stderr_captures.pop(chat_id, None)

        if client:
            await self._safe_disconnect(client, chat_id)

    async def _safe_disconnect(self, client: ClaudeSDKClient, chat_id: str) -> None:
        """安全断开 client，处理跨 task cancel scope 冲突。

        Claude SDK 内部使用 anyio cancel scope，当 disconnect() 在与 connect()
        不同的 asyncio.Task 中调用时会抛出 RuntimeError。
        此时释放引用，让 SDK 子进程的退出机制自行清理。
        """
        try:
            await client.disconnect()
            logger.info("关闭 client: chat_id={}", chat_id)
        except BaseException as e:
            if "cancel scope" in str(e).lower():
                logger.info("关闭 client: chat_id={} (跨 task cancel scope，已释放引用)", chat_id)
            else:
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

    def get_recent_stderr(self, chat_id: str, *, limit: int = 8) -> str:
        capture = self._stderr_captures.get(chat_id)
        if capture is None:
            return ""
        snapshot = getattr(capture, "snapshot", None)
        if callable(snapshot):
            result: str = snapshot(limit=limit)
            return result
        return ""

    async def _create_client(self, chat_id: str) -> ClaudeSDKClient:
        """创建新的 ClaudeSDKClient。"""
        _sanitize_sdk_host_env()
        configure_langsmith_once(self._config)

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        if self._config.system_prompt:
            base_prompt = self._config.system_prompt
        elif self._workspace:
            base_prompt = self._workspace.build_system_prompt()
        else:
            base_prompt = ""

        # cwd 解析：workspace 优先，无 workspace 时要求 config.cwd，禁止 "." 兜底
        if self._workspace is not None:
            cwd = str(self._workspace.path)
        elif self._config.cwd:
            cwd = self._config.cwd
        else:
            raise ValueError("AgentPool 缺少 workspace 或 config.cwd，无法确定工作目录")

        memory_prompt = ""
        resume_session_id = ""
        if self._role == RuntimeRole.SUPERVISOR and self._memory_store is not None:
            if self._config.supervisor_resume_enabled:
                resume_session_id = self._memory_store.load(chat_id).runtime_session_id
            if resume_session_id:
                # SDK resume 已恢复完整对话历史，只注入长期记忆，避免短期记忆重复
                memory_prompt = self._memory_store.build_long_term_prompt(chat_id)
            else:
                memory_prompt = self._memory_store.build_memory_prompt(chat_id)

        kwargs = build_sdk_options(
            self._config,
            role=self._role,
            cwd=cwd,
            base_prompt=base_prompt,
            context_prompt=memory_prompt,
            extra_prompt=self._extra_system_prompt,
            model=self._config.model,
            max_turns=self._config.max_turns,
            allowed_tools=self._config.allowed_tools or None,
            output_format=self._output_format,
        )

        # 合并进程内 SDK MCP server（如 runtime tools）
        if self._sdk_mcp_servers:
            mcp = kwargs.get("mcp_servers", {})
            mcp.update(self._sdk_mcp_servers)
            kwargs["mcp_servers"] = mcp

        if resume_session_id:
            kwargs["resume"] = resume_session_id
            kwargs["continue_conversation"] = True

        stderr_capture = build_stderr_capture(f"[sdk:{self._role.value}:{chat_id}]")
        kwargs["stderr"] = stderr_capture.callback
        options = ClaudeAgentOptions(**kwargs)
        client = ClaudeSDKClient(options)
        self._stderr_captures[chat_id] = stderr_capture
        try:
            await client.connect()
        except Exception:
            raise
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
        self._stderr_captures.clear()

        for chat_id, client in clients:
            await self._safe_disconnect(client, chat_id)
