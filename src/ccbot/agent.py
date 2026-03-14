"""CCBotAgent: per-chat-id ClaudeSDKClient sessions with workspace integration.

使用 AgentPool 作为底层 client 管理器，消除重复的生命周期管理代码。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger

from ccbot.config import AgentConfig
from ccbot.memory import MemoryStore
from ccbot.runtime import AgentPool
from ccbot.runtime.profiles import RuntimeRole
from ccbot.runtime.sdk_utils import (
    AgentRunResult,
    format_sdk_error,
    is_retryable_sdk_error,
    query_and_collect_result,
)
from ccbot.workspace import WorkspaceManager

_HELP_TEXT = """\
🐈 ccbot commands:
/new  — Start a new conversation (clears local memory + runtime session)
/stop — Cancel the active task
/workers — Show active workers
/worker stop <name> — Interrupt a worker
/worker kill <name> — Destroy a worker
/memory show — Show persisted memory snapshot
/memory clear — Clear local memory for current conversation
/help — Show this help"""


class CCBotAgent:
    """Multi-turn agent backed by Claude Agent SDK."""

    def __init__(
        self,
        config: AgentConfig,
        workspace: WorkspaceManager | None = None,
        extra_system_prompt: str = "",
        idle_timeout: int | None = None,
        output_format: dict[str, object] | None = None,
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
        self._pool = AgentPool(
            config=config,
            workspace=workspace,
            extra_system_prompt=extra_system_prompt,
            idle_timeout=self._idle_timeout,
            output_format=output_format,
            role=role,
            memory_store=memory_store,
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_chat_id: str | None = None

    async def start(self) -> None:
        await self._pool.start()

    async def stop(self) -> None:
        await self._pool.stop()

    def _get_lock(self, chat_id: str) -> asyncio.Lock:
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    async def _close_session(self, chat_id: str) -> None:
        await self._pool.close(chat_id)
        self._locks.pop(chat_id, None)

    async def reset_conversation(self, chat_id: str) -> None:
        """Reset local runtime session and persisted supervisor memory."""
        await self._close_session(chat_id)
        if self._memory_store is not None and self._role == RuntimeRole.SUPERVISOR:
            self._memory_store.clear_conversation(chat_id)

    async def interrupt(self, chat_id: str) -> bool:
        """Interrupt the active runtime session for the given conversation."""
        return await self._pool.interrupt(chat_id)

    async def ask(
        self,
        chat_id: str,
        prompt: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        result = await self.ask_run(chat_id, prompt, on_progress=on_progress)
        return result.text

    async def ask_run(
        self,
        chat_id: str,
        prompt: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunResult:
        self.last_chat_id = chat_id
        cmd = prompt.strip().lower()

        if cmd == "/help":
            return AgentRunResult(text=_HELP_TEXT)

        if cmd == "/new":
            await self.reset_conversation(chat_id)
            return AgentRunResult(text="New session started. 🐈")

        if cmd == "/stop":
            await self.interrupt(chat_id)
            return AgentRunResult(text="⏹ Stopped.")

        logger.info("[{}] ← {}", chat_id, prompt[:120])

        async with self._get_lock(chat_id):
            for attempt in range(2):
                try:
                    client = await self._pool.acquire(chat_id)
                    result = await query_and_collect_result(
                        client,
                        prompt,
                        session_id=chat_id,
                        on_progress=on_progress,
                        log_prefix=f"[{chat_id}]",
                    )

                    # SDK 返回 is_error（如 resume 会话与当前模型不兼容），
                    # 清除 stale session 后冷启动重试一次
                    if result.is_error and attempt == 0:
                        logger.warning(
                            "[{}] SDK 返回 is_error，清除 session 后冷启动重试", chat_id
                        )
                        await self._close_session(chat_id)
                        if self._memory_store is not None and self._role == RuntimeRole.SUPERVISOR:
                            self._memory_store.set_runtime_session(chat_id, "")
                        continue

                    logger.info("[{}] → {} chars", chat_id, len(result.text))
                    if self._memory_store is not None and self._role == RuntimeRole.SUPERVISOR:
                        if result.runtime_session_id:
                            self._memory_store.set_runtime_session(
                                chat_id, result.runtime_session_id
                            )
                        self._memory_store.remember_turn(chat_id, prompt, result.text)
                    await self._pool.release(chat_id)
                    return result

                except Exception as e:
                    recent_stderr = self._pool.get_recent_stderr(chat_id)
                    if recent_stderr:
                        logger.error(
                            "[{}] Agent 出错: {}\nRecent stderr:\n{}", chat_id, e, recent_stderr
                        )
                    else:
                        logger.error("[{}] Agent 出错: {}", chat_id, e)

                    should_retry = attempt == 0 and is_retryable_sdk_error(e)
                    await self._close_session(chat_id)

                    if should_retry:
                        logger.warning(
                            "[{}] 检测到 Claude 会话异常退出，正在重建后重试一次", chat_id
                        )
                        continue

                    # 所有重试耗尽后清除持久化 session_id，避免 stale resume 导致下次
                    # 请求永久失败（Anthropic 服务端 session 过期时会走到这里）
                    if self._memory_store is not None and self._role == RuntimeRole.SUPERVISOR:
                        self._memory_store.set_runtime_session(chat_id, "")
                        logger.info("[{}] 已清除 stale runtime_session_id，下次将冷启动", chat_id)

                    return AgentRunResult(text=format_sdk_error(e, recent_stderr))

            return AgentRunResult(text="抱歉，处理消息时出现未知错误。")
