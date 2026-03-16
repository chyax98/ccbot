"""AgentTeam: Supervisor-Worker 多 Agent 编排（持久化 Worker 架构）。"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from loguru import logger

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.memory import MemoryStore
from ccbot.models import (
    DispatchPayload,
    DispatchResult,
    SupervisorResponse,
    WorkerResult,
    WorkerTask,
)
from ccbot.runtime.profiles import RuntimeRole
from ccbot.runtime.tools import create_runtime_tools
from ccbot.runtime.worker_pool import WorkerPool
from ccbot.scheduler import SchedulerService
from ccbot.workspace import WorkspaceManager

# per-task 请求上下文，避免并发请求间的竞态条件
_current_request_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_current_request_context", default=None
)

_TEAM_HELP_TEXT = """\
🐈 ccbot commands:
/new — 新建 Supervisor 会话（清空本地记忆与 runtime session）
/stop — 中断当前 Supervisor 任务
/workers — 查看活跃 Workers
/worker stop <name> — 中断指定 Worker
/worker kill <name> — 销毁指定 Worker
/memory show — 查看当前持久化记忆快照
/memory clear — 清空当前会话的本地记忆与 runtime session
/schedule list — 查看定时任务
/schedule run <id> — 立即执行定时任务
/schedule pause <id> — 暂停定时任务
/schedule resume <id> — 恢复定时任务
/schedule delete <id> — 删除定时任务
/help — 显示帮助"""


class AgentTeam:
    """Supervisor + 持久化 WorkerPool + 可选 Scheduler。"""

    def __init__(self, config: AgentConfig, workspace: WorkspaceManager) -> None:
        self._config = config
        self._workspace = workspace
        self._memory_store = MemoryStore(
            workspace.path,
            max_short_term_turns=config.short_term_memory_turns,
        )
        self._supervisor = CCBotAgent(
            config,
            workspace,
            output_format=SupervisorResponse.output_format(),
            role=RuntimeRole.SUPERVISOR,
            memory_store=self._memory_store,
        )
        self._worker_pool = WorkerPool(config, workspace_path=workspace.path)
        self._scheduler: SchedulerService | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._background_tasks_by_chat: dict[str, set[asyncio.Task[None]]] = {}
        self._background_task_workers: dict[asyncio.Task[None], frozenset[str]] = {}

    def set_scheduler(self, scheduler: SchedulerService) -> None:
        self._scheduler = scheduler
        # 创建 runtime tools 并注入 Supervisor 的 SDK MCP servers
        # 使用 ContextVar 读取当前 task 的请求上下文，避免并发竞态
        sdk_server = create_runtime_tools(
            scheduler,
            get_context=lambda: _current_request_context.get() or {},
        )
        self._supervisor.set_sdk_mcp_servers({sdk_server["name"]: sdk_server})

    async def start(self) -> None:
        await self._supervisor.start()
        await self._worker_pool.start()

    async def stop(self) -> None:
        if self._background_tasks:
            for task in self._background_tasks:
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        await self._worker_pool.stop()
        await self._supervisor.stop()

    @property
    def last_chat_id(self) -> str | None:
        return self._supervisor.last_chat_id

    @property
    def worker_pool(self) -> WorkerPool:
        return self._worker_pool

    def _track_background_task(
        self,
        chat_id: str,
        worker_names: list[str],
        task: asyncio.Task[None],
    ) -> None:
        self._background_tasks.add(task)
        self._background_tasks_by_chat.setdefault(chat_id, set()).add(task)
        self._background_task_workers[task] = frozenset(worker_names)

        def _cleanup(done_task: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done_task)
            chat_tasks = self._background_tasks_by_chat.get(chat_id)
            if chat_tasks is not None:
                chat_tasks.discard(done_task)
                if not chat_tasks:
                    self._background_tasks_by_chat.pop(chat_id, None)
            self._background_task_workers.pop(done_task, None)
            with contextlib.suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc is not None:
                    logger.error("后台 worker 任务异常退出: {}", exc)

        task.add_done_callback(_cleanup)

    async def _cancel_active_dispatch(self, chat_id: str) -> tuple[int, int]:
        tasks = list(self._background_tasks_by_chat.get(chat_id, set()))
        worker_names: set[str] = set()
        for task in tasks:
            worker_names.update(self._background_task_workers.get(task, frozenset()))

        for task in tasks:
            task.cancel()

        interrupted_workers = 0
        for name in worker_names:
            interrupted = await self._worker_pool.interrupt(name, owner_id=chat_id)
            if interrupted:
                interrupted_workers += 1

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return len(tasks), interrupted_workers

    async def ask(
        self,
        chat_id: str,
        prompt: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_worker_result: Callable[[str, str], Awaitable[None]] | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> str:
        control_reply = await self._handle_control_command(chat_id, prompt)
        if control_reply is not None:
            return control_reply

        # 设置当前 task 的请求上下文（ContextVar 隔离并发请求）
        _current_request_context.set(request_context or {})

        # 当前日期始终注入：保留基础时间语义，同时降低分钟级抖动对 KV cache 的影响
        current_date = datetime.now().strftime("%Y-%m-%d (%A)")
        context_parts = [f"Current date: {current_date}"]

        worker_status = self._worker_pool.format_status(owner_id=chat_id)
        if worker_status:
            context_parts.append(worker_status)

        context_block = "<runtime_context>\n" + "\n\n".join(context_parts) + "\n</runtime_context>"
        enhanced_prompt = f"{context_block}\n\n{prompt}"

        if on_progress:
            await on_progress("📋 分析任务中...")

        supervisor_result = await self._supervisor.ask_run(
            chat_id,
            enhanced_prompt,
            on_progress=on_progress,
        )
        supervisor_reply = supervisor_result.text

        structured_payload = supervisor_result.structured_output
        structured_response = SupervisorResponse.from_structured_output(structured_payload)
        if structured_payload is not None and structured_response is None:
            logger.warning("Supervisor 返回了无效的结构化结果: {}", structured_payload)
            dispatch = DispatchPayload.from_text(supervisor_reply)
            if dispatch is not None:
                user_message = _extract_pre_dispatch_text(supervisor_reply)
            elif supervisor_reply:
                return supervisor_reply
            else:
                return "Supervisor 返回了无效的结构化结果，请重试。"
        elif structured_response is None:
            dispatch = DispatchPayload.from_text(supervisor_reply)
            if dispatch is None:
                return supervisor_reply
            user_message = _extract_pre_dispatch_text(supervisor_reply)
        elif structured_response.mode == "respond":
            return structured_response.user_message or supervisor_reply
        else:
            dispatch = structured_response.dispatch_payload
            user_message = structured_response.user_message
            if dispatch is None:
                return structured_response.user_message or supervisor_reply

        if on_progress:
            await on_progress(f"📋 派发任务: {dispatch.worker_names}")

        if on_worker_result is not None:
            pre_text = user_message or _extract_pre_dispatch_text(supervisor_reply)

            # 预加载 Worker：确保 Worker 创建完成后再返回，防止并发竞争
            try:
                await self._worker_pool.preload_workers(dispatch.tasks, owner_id=chat_id)
            except Exception as exc:
                logger.error("预加载 Worker 失败: {}", exc)
                return f"无法启动 Worker: {exc}"

            task = asyncio.create_task(
                self._run_workers_async(chat_id, prompt, dispatch, on_progress, on_worker_result),
                name=f"worker-dispatch:{chat_id}",
            )
            self._track_background_task(chat_id, [task.name for task in dispatch.tasks], task)
            return pre_text or f"已派发 {len(dispatch.tasks)} 个任务，结果将陆续返回。"

        result = await self._run_workers(chat_id, dispatch, on_progress)

        if on_progress:
            await on_progress("🎯 综合结果中...")

        synthesis = result.to_synthesis_prompt(prompt)
        synthesis_result = await self._supervisor.ask_run(
            chat_id,
            synthesis,
            on_progress=on_progress,
        )
        synthesis_response = SupervisorResponse.from_structured_output(
            synthesis_result.structured_output
        )
        if synthesis_response is not None and synthesis_response.mode == "respond":
            return synthesis_response.user_message or synthesis_result.text
        return synthesis_result.text

    async def _run_workers(
        self,
        chat_id: str,
        dispatch: DispatchPayload,
        on_progress: Callable[[str], Awaitable[None]] | None,
    ) -> DispatchResult:
        semaphore = asyncio.Semaphore(self._config.max_workers)

        async def run_one(task_def: WorkerTask) -> WorkerResult:
            async def worker_progress(msg: str) -> None:
                tagged = f"[{task_def.name}] {msg}"
                logger.info("[{}] {}", chat_id, tagged)
                if on_progress:
                    await on_progress(tagged)

            logger.info(
                "[{}] 启动 worker name={} cwd={} model={}",
                chat_id,
                task_def.name,
                task_def.cwd,
                task_def.model or "default",
            )

            async with semaphore:
                try:
                    await self._worker_pool.get_or_create(task_def, owner_id=chat_id)
                    result_text = await self._worker_pool.send(
                        task_def.name,
                        task_def.task,
                        owner_id=chat_id,
                        on_progress=worker_progress,
                    )
                    logger.info(
                        "[{}] worker 完成 name={} ({} chars)",
                        chat_id,
                        task_def.name,
                        len(result_text),
                    )
                    if on_progress:
                        await on_progress(f"[{task_def.name}] 完成")
                    return WorkerResult.from_result(task_def.name, result_text)
                except Exception as exc:
                    logger.error("[{}] worker 失败 name={}: {}", chat_id, task_def.name, exc)
                    if on_progress:
                        await on_progress(f"[{task_def.name}] 失败: {str(exc)[:80]}")
                    return WorkerResult.from_exception(task_def.name, exc)

        worker_results = await asyncio.gather(
            *[run_one(task) for task in dispatch.tasks],
            return_exceptions=False,
        )
        return DispatchResult(workers=list(worker_results))

    async def _run_workers_async(
        self,
        chat_id: str,
        prompt: str,
        dispatch: DispatchPayload,
        on_progress: Callable[[str], Awaitable[None]] | None,
        on_worker_result: Callable[[str, str], Awaitable[None]],
    ) -> None:
        await asyncio.sleep(0)

        try:
            result = await self._run_workers(chat_id, dispatch, on_progress)
            for wr in result.workers:
                try:
                    await on_worker_result(wr.name, wr.result)
                except Exception as exc:
                    logger.error("[{}] 回调 worker 结果失败 name={}: {}", chat_id, wr.name, exc)

            if on_progress:
                await on_progress("🎯 综合结果中...")

            synthesis = result.to_synthesis_prompt(prompt)
            synthesis_result = await self._supervisor.ask_run(
                chat_id,
                synthesis,
                on_progress=on_progress,
            )
            synthesis_response = SupervisorResponse.from_structured_output(
                synthesis_result.structured_output
            )
            final_reply = synthesis_result.text
            if synthesis_response is not None and synthesis_response.mode == "respond":
                final_reply = synthesis_response.user_message or synthesis_result.text

            if final_reply:
                try:
                    await on_worker_result("🤖 综合", final_reply)
                except Exception as exc:
                    logger.error("[{}] 回调综合结果失败: {}", chat_id, exc)
        except Exception as exc:
            logger.exception("[{}] 异步 dispatch 执行失败: {}", chat_id, exc)
            with contextlib.suppress(Exception):
                await on_worker_result("❌ 系统错误", f"异步派发执行失败: {exc}")

    async def _handle_control_command(self, chat_id: str, prompt: str) -> str | None:
        command = prompt.strip()
        lowered = command.lower()

        if lowered == "/help":
            return _TEAM_HELP_TEXT

        if lowered == "/new":
            _, interrupted_workers = await self._cancel_active_dispatch(chat_id)
            await self._supervisor.reset_conversation(chat_id)
            if interrupted_workers:
                return f"已开始新的 Supervisor 会话，并停止 {interrupted_workers} 个 Worker。"
            return "已开始新的 Supervisor 会话。"

        if lowered == "/stop":
            supervisor_interrupted = await self._supervisor.interrupt(chat_id)
            canceled_dispatches, interrupted_workers = await self._cancel_active_dispatch(chat_id)
            if supervisor_interrupted or canceled_dispatches or interrupted_workers:
                parts: list[str] = []
                if supervisor_interrupted:
                    parts.append("Supervisor")
                if canceled_dispatches:
                    parts.append(f"{canceled_dispatches} 个后台派发")
                if interrupted_workers:
                    parts.append(f"{interrupted_workers} 个 Worker")
                return f"已中断当前任务（{'，'.join(parts)}）。"
            return "当前没有可中断的任务。"

        if lowered == "/workers":
            status = self._worker_pool.format_status(owner_id=chat_id)
            return status or "当前没有活跃 Worker。"

        if lowered.startswith("/worker kill "):
            name = _extract_command_argument(command, "/worker kill ")
            if name is None:
                return "用法: /worker kill <name>"
            if not self._worker_pool.has_worker(name, owner_id=chat_id):
                return f"Worker '{name}' 不存在。"
            await self._worker_pool.kill(name, owner_id=chat_id)
            return f"已销毁 Worker: {name}"

        if lowered.startswith("/worker stop "):
            name = _extract_command_argument(command, "/worker stop ")
            if name is None:
                return "用法: /worker stop <name>"
            if not self._worker_pool.has_worker(name, owner_id=chat_id):
                return f"Worker '{name}' 不存在。"
            interrupted = await self._worker_pool.interrupt(name, owner_id=chat_id)
            if interrupted:
                return f"已中断 Worker: {name}"
            return f"Worker '{name}' 当前无法中断。"

        if lowered.startswith("/memory show"):
            memory_prompt = self._memory_store.build_memory_prompt(chat_id).strip()
            return memory_prompt or "当前没有持久化记忆。"

        if lowered.startswith("/memory clear"):
            _, interrupted_workers = await self._cancel_active_dispatch(chat_id)
            await self._supervisor.reset_conversation(chat_id)
            if interrupted_workers:
                return f"已清空当前会话的本地记忆与 runtime session，并停止 {interrupted_workers} 个 Worker。"
            return "已清空当前会话的本地记忆与 runtime session。"

        if lowered == "/schedule list":
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            return self._scheduler.format_jobs() or "当前没有定时任务。"

        if lowered.startswith("/schedule delete "):
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            job_id = _extract_command_argument(command, "/schedule delete ")
            if job_id is None:
                return "用法: /schedule delete <id>"
            deleted = self._scheduler.delete_job(job_id)
            return f"已删除定时任务: {job_id}" if deleted else f"定时任务不存在: {job_id}"

        if lowered.startswith("/schedule pause "):
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            job_id = _extract_command_argument(command, "/schedule pause ")
            if job_id is None:
                return "用法: /schedule pause <id>"
            paused = self._scheduler.pause_job(job_id)
            return f"已暂停定时任务: {job_id}" if paused else f"定时任务不存在: {job_id}"

        if lowered.startswith("/schedule resume "):
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            job_id = _extract_command_argument(command, "/schedule resume ")
            if job_id is None:
                return "用法: /schedule resume <id>"
            resumed = self._scheduler.resume_job(job_id)
            return f"已恢复定时任务: {job_id}" if resumed else f"定时任务不存在: {job_id}"

        if lowered.startswith("/schedule run "):
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            job_id = _extract_command_argument(command, "/schedule run ")
            if job_id is None:
                return "用法: /schedule run <id>"
            result = await self._scheduler.run_job_now(job_id)
            if result == "started":
                return f"已触发定时任务: {job_id}"
            if result == "already_running":
                return f"定时任务正在执行中: {job_id}"
            return f"定时任务不存在: {job_id}"

        return None


def _extract_pre_dispatch_text(text: str) -> str:
    match = re.search(r"<dispatch>", text, re.IGNORECASE)
    return text[: match.start()].strip() if match else text.strip()


def _extract_command_argument(command: str, prefix: str) -> str | None:
    if not command.lower().startswith(prefix.lower()):
        return None
    value = command[len(prefix) :].strip()
    return value or None
