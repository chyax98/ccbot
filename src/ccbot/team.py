"""AgentTeam: Supervisor-Worker 多 Agent 编排（持久化 Worker 架构）。"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.memory import MemoryStore
from ccbot.models import (
    DispatchPayload,
    DispatchResult,
    ScheduleSpec,
    SupervisorResponse,
    WorkerResult,
    WorkerTask,
)
from ccbot.runtime.profiles import RuntimeRole
from ccbot.runtime.worker_pool import WorkerPool
from ccbot.scheduler import SchedulerService
from ccbot.workspace import WorkspaceManager


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
        self._worker_pool = WorkerPool(config)
        self._scheduler: SchedulerService | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()

    def set_scheduler(self, scheduler: SchedulerService) -> None:
        self._scheduler = scheduler

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

    def _track_background_task(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.add(task)

        def _cleanup(done_task: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done_task)
            with contextlib.suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc is not None:
                    logger.error("后台 worker 任务异常退出: {}", exc)

        task.add_done_callback(_cleanup)

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

        worker_status = self._worker_pool.format_status()
        schedule_status = self._scheduler.format_status() if self._scheduler is not None else ""
        enhanced_prompt = prompt
        extra_sections = [section for section in (worker_status, schedule_status) if section]
        if extra_sections:
            enhanced_prompt = f"{prompt}\n\n---\n" + "\n\n---\n".join(extra_sections)

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
        elif structured_response.mode == "schedule_create":
            if structured_response.schedule is None:
                return structured_response.user_message or "无法创建定时任务：缺少任务定义。"
            return self._create_schedule(
                chat_id,
                structured_response.schedule,
                structured_response.user_message,
                request_context=request_context,
            )
        else:
            dispatch = structured_response.dispatch_payload
            user_message = structured_response.user_message
            if dispatch is None:
                return structured_response.user_message or supervisor_reply

        if on_progress:
            await on_progress(f"📋 派发任务: {dispatch.worker_names}")

        if on_worker_result is not None:
            pre_text = user_message or _extract_pre_dispatch_text(supervisor_reply)
            task = asyncio.create_task(
                self._run_workers_async(chat_id, dispatch, on_progress, on_worker_result),
                name=f"worker-dispatch:{chat_id}",
            )
            self._track_background_task(task)
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

    def _create_schedule(
        self,
        chat_id: str,
        schedule: ScheduleSpec,
        user_message: str,
        *,
        request_context: dict[str, Any] | None,
    ) -> str:
        if self._scheduler is None:
            return "当前运行模式未启用 Scheduler，无法创建定时任务。"

        channel = str((request_context or {}).get("channel", ""))
        notify_target = str((request_context or {}).get("notify_target", chat_id))
        created_by = str((request_context or {}).get("sender_id", ""))
        conversation_id = str((request_context or {}).get("conversation_id", chat_id))

        try:
            job = self._scheduler.create_job(
                schedule,
                created_by=created_by,
                channel=channel,
                notify_target=notify_target,
                conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.warning("创建定时任务失败 chat_id={}: {}", chat_id, exc)
            return f"无法创建定时任务：{exc}"
        summary = (
            f"已创建定时任务：{job.name}\n"
            f"- job_id: {job.job_id}\n"
            f"- cron: {job.cron_expr}\n"
            f"- timezone: {job.timezone}\n"
            f"- next_run_at: {job.next_run_at}\n"
            f"- notify_target: {job.notify_target or chat_id}"
        )
        if user_message:
            return f"{user_message}\n\n{summary}"
        return summary

    async def _run_workers(
        self,
        chat_id: str,
        dispatch: DispatchPayload,
        on_progress: Callable[[str], Awaitable[None]] | None,
    ) -> DispatchResult:
        semaphore = asyncio.Semaphore(self._config.max_workers)

        async def run_one(task_def: WorkerTask) -> WorkerResult:
            await self._worker_pool.get_or_create(task_def)

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
                    result_text = await self._worker_pool.send(
                        task_def.name,
                        task_def.task,
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

            success_count = sum(1 for wr in result.workers if wr.success)
            total = len(result.workers)
            summary = f"全部 {total} 个任务完成（{success_count} 成功"
            if success_count < total:
                summary += f"，{total - success_count} 失败"
            summary += "）"
            try:
                await on_worker_result("📊", summary)
            except Exception as exc:
                logger.error("[{}] 回调汇总失败: {}", chat_id, exc)
        except Exception as exc:
            logger.exception("[{}] 异步 dispatch 执行失败: {}", chat_id, exc)
            with contextlib.suppress(Exception):
                await on_worker_result("❌ 系统错误", f"异步派发执行失败: {exc}")

    async def _handle_control_command(self, chat_id: str, prompt: str) -> str | None:
        command = prompt.strip()
        lowered = command.lower()

        if lowered == "/workers":
            status = self._worker_pool.format_status()
            return status or "当前没有活跃 Worker。"

        if lowered.startswith("/worker kill "):
            name = command.split(maxsplit=2)[2].strip()
            if not name:
                return "用法: /worker kill <name>"
            if not self._worker_pool.has_worker(name):
                return f"Worker '{name}' 不存在。"
            await self._worker_pool.kill(name)
            return f"已销毁 Worker: {name}"

        if lowered.startswith("/worker stop "):
            name = command.split(maxsplit=2)[2].strip()
            if not name:
                return "用法: /worker stop <name>"
            if not self._worker_pool.has_worker(name):
                return f"Worker '{name}' 不存在。"
            interrupted = await self._worker_pool.interrupt(name)
            if interrupted:
                return f"已中断 Worker: {name}"
            return f"Worker '{name}' 当前无法中断。"

        if lowered.startswith("/memory show"):
            memory_prompt = self._memory_store.build_memory_prompt(chat_id).strip()
            return memory_prompt or "当前没有持久化记忆。"

        if lowered.startswith("/memory clear"):
            self._memory_store.clear_conversation(chat_id)
            await self._supervisor._close_session(chat_id)
            return "已清空当前会话的本地记忆与 runtime session。"

        if lowered == "/schedule list":
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            status = self._scheduler.format_status()
            return status or "当前没有定时任务。"

        if lowered.startswith("/schedule delete "):
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            job_id = command.split(maxsplit=2)[2].strip()
            deleted = self._scheduler.delete_job(job_id)
            return f"已删除定时任务: {job_id}" if deleted else f"定时任务不存在: {job_id}"

        if lowered.startswith("/schedule pause "):
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            job_id = command.split(maxsplit=2)[2].strip()
            paused = self._scheduler.pause_job(job_id)
            return f"已暂停定时任务: {job_id}" if paused else f"定时任务不存在: {job_id}"

        if lowered.startswith("/schedule resume "):
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            job_id = command.split(maxsplit=2)[2].strip()
            resumed = self._scheduler.resume_job(job_id)
            return f"已恢复定时任务: {job_id}" if resumed else f"定时任务不存在: {job_id}"

        if lowered.startswith("/schedule run "):
            if self._scheduler is None:
                return "当前未启用 Scheduler。"
            job_id = command.split(maxsplit=2)[2].strip()
            ran = await self._scheduler.run_job_now(job_id)
            return f"已触发定时任务: {job_id}" if ran else f"定时任务不存在: {job_id}"

        return None


def _extract_pre_dispatch_text(text: str) -> str:
    match = re.search(r"<dispatch>", text, re.IGNORECASE)
    return text[: match.start()].strip() if match else text.strip()
