"""AgentTeam: Supervisor-Worker 多 Agent 编排（持久化 Worker 架构）。

协议：
  1. Supervisor 接收用户任务，决定直接处理或输出 <dispatch>[...] 计划
  2. Python 解析计划，WorkerPool 按 name 复用或创建 Worker
  3. 每个 worker 的 on_progress 回调前缀 "[name] "，供上层聚合显示
  4. 全部完成后结果喂回 Supervisor 综合，返回最终回复

持久化 Worker：Worker 创建后保持存活，可通过相同 name 追加任务。
Supervisor 每次处理消息前注入当前 Worker 状态。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

from loguru import logger

from ccbot.agent import CCBotAgent
from ccbot.config import AgentConfig
from ccbot.models import DispatchPayload, DispatchResult, WorkerResult, WorkerTask
from ccbot.runtime.worker_pool import WorkerPool
from ccbot.workspace import WorkspaceManager

# Supervisor 额外注入的多 Agent 调度说明
_SUPERVISOR_PROMPT = """\
## 你的角色：项目总控

你是整个任务的负责人，负责把控全局。你可以：
- 自己搜索、调研、读文件、分析问题——这是理解任务的基础
- 直接处理简单问答，无需派发

**当任务适合并行或需要专项执行时**（如同时开发多个模块、前后端分离、多步骤独立子任务），
输出 dispatch 计划交给 Worker 执行：

<dispatch>
[
  {
    "name": "worker 唯一名称（如 frontend / backend / reviewer）",
    "cwd": "/绝对路径/工作目录",
    "task": "详细任务描述",
    "model": "sonnet",
    "max_turns": 30
  }
]
</dispatch>

规则：
- name 在本次 dispatch 中唯一，用于日志和进度显示
- cwd 必须是绝对路径；同一 repo 内各 worker 操作不重叠的文件/目录
- model / max_turns 可省略（默认继承配置）
- dispatch 块之外可以写给用户看的说明，不要在 dispatch 块内加注释
- 收到 worker 结果后，综合成清晰的汇报返回给用户

## Worker 管理

Worker 在 dispatch 后保持存活，可以接收后续任务：
- 如需向已有 Worker 追加任务，使用**相同的 name**
- 新 name 会创建新 Worker
- 活跃 Worker 列表会在每次对话时提供给你

典型用法：
1. 首次派发：创建 Workers 并分配初始任务
2. 用户说"frontend 再看看性能"：用相同 name="frontend" 追加任务
3. 用户问"现在谁在跑"：直接查看活跃 Worker 列表回答
"""


class AgentTeam:
    """
    Supervisor（Opus）+ 持久化 Worker 池，全部跑在同一 Python asyncio 进程内。

    - 持久化 Worker：Worker 创建后保持存活，可接收多次任务
    - Worker 复用：相同 name 的 dispatch 路由到已有 Worker，保留完整上下文
    - 实时进度：worker on_progress 前缀 "[name] "，由上层聚合为状态看板
    - 容错：单个 worker 失败不影响其他 worker，结果中标记失败
    - 并发控制：max_workers 限制并行 worker 数量
    - 状态注入：每次 Supervisor 处理消息前注入当前 Worker 状态

    用法（等同 CCBotAgent.ask）：
        team = AgentTeam(config, workspace)
        await team.start()
        reply = await team.ask(chat_id, prompt, on_progress=cb)
        await team.stop()
    """

    def __init__(self, config: AgentConfig, workspace: WorkspaceManager) -> None:
        self._config = config
        self._supervisor = CCBotAgent(config, workspace, extra_system_prompt=_SUPERVISOR_PROMPT)
        self._worker_pool = WorkerPool(config)

    async def start(self) -> None:
        """启动 AgentTeam，启动 Supervisor 和 WorkerPool。"""
        await self._supervisor.start()
        await self._worker_pool.start()

    async def stop(self) -> None:
        """停止 AgentTeam，关闭 WorkerPool 和 Supervisor。"""
        await self._worker_pool.stop()
        await self._supervisor.stop()

    @property
    def last_chat_id(self) -> str | None:
        return self._supervisor.last_chat_id

    @property
    def worker_pool(self) -> WorkerPool:
        """暴露 WorkerPool 供外部查询状态。"""
        return self._worker_pool

    async def ask(
        self,
        chat_id: str,
        prompt: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_worker_result: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> str:
        """处理消息：Supervisor 分析 → 可选 dispatch → 综合回复。

        Args:
            chat_id: 会话 ID
            prompt: 用户消息
            on_progress: 进度回调
            on_worker_result: Worker 结果回调。提供时启用异步派发模式：
                后台执行 Workers，每完成一个立即回调，ask() 立即返回派发摘要。
                不提供时使用同步模式：等待全部完成 + Supervisor 综合。
        """
        # Step 1: 注入 Worker 状态到 prompt
        worker_status = self._worker_pool.format_status()
        enhanced_prompt = prompt
        if worker_status:
            enhanced_prompt = f"{prompt}\n\n---\n{worker_status}"

        # Step 2: Supervisor 分析任务
        if on_progress:
            await on_progress("📋 分析任务中...")

        supervisor_reply = await self._supervisor.ask(
            chat_id, enhanced_prompt, on_progress=on_progress
        )

        # Step 3: 解析 dispatch 计划
        dispatch = DispatchPayload.from_text(supervisor_reply)
        if dispatch is None:
            return supervisor_reply  # Supervisor 直接处理了，无需派发

        logger.info(
            "[{}] Supervisor 派发 {} 个 worker: {}",
            chat_id,
            len(dispatch.tasks),
            dispatch.worker_names,
        )
        if on_progress:
            await on_progress(f"📋 派发任务: {dispatch.worker_names}")

        if on_worker_result is not None:
            # 异步模式：后台启动 Workers，立即返回派发摘要
            pre_text = _extract_pre_dispatch_text(supervisor_reply)
            asyncio.create_task(
                self._run_workers_async(chat_id, dispatch, on_progress, on_worker_result)
            )
            return (
                f"{pre_text}\n\n"
                f"📋 已派发 {len(dispatch.tasks)} 个任务: {dispatch.worker_names}"
            ).strip()
        else:
            # 同步模式：等待全部完成 + Supervisor 综合
            result = await self._run_workers(chat_id, dispatch, on_progress)
            synthesis = result.to_synthesis_prompt()
            logger.info("[{}] 所有 worker 完成，请求 Supervisor 综合", chat_id)
            if on_progress:
                await on_progress("🎯 综合结果中...")
            return await self._supervisor.ask(chat_id, synthesis, on_progress=on_progress)

    async def _run_workers(
        self,
        chat_id: str,
        dispatch: DispatchPayload,
        on_progress: Callable[[str], Awaitable[None]] | None,
    ) -> DispatchResult:
        """并行执行所有 worker 任务（受 max_workers 限制），返回结构化结果。"""
        semaphore = asyncio.Semaphore(self._config.max_workers)

        async def run_one(task_def: WorkerTask) -> WorkerResult:
            """执行单个 worker 任务，带并发控制。"""
            # get_or_create: 复用已有 Worker 或创建新的
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
                        task_def.name, task_def.task, on_progress=worker_progress
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
                except Exception as e:
                    logger.error("[{}] worker 失败 name={}: {}", chat_id, task_def.name, e)

                    if on_progress:
                        error_msg = str(e)[:80]
                        await on_progress(f"[{task_def.name}] 失败: {error_msg}")

                    return WorkerResult.from_exception(task_def.name, e)
            # Worker 保持存活，不销毁

        # 并行执行所有 worker（受 semaphore 限制并发数）
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
        """异步模式：后台并行执行 Workers，每完成一个立即回调。"""
        # 让出控制权，确保调用方先完成派发摘要的发送
        await asyncio.sleep(0)

        try:
            result = await self._run_workers(chat_id, dispatch, on_progress)

            # 逐个回调每个 worker 的结果
            for wr in result.workers:
                try:
                    await on_worker_result(wr.name, wr.result)
                except Exception as e:
                    logger.error("[{}] 回调 worker 结果失败 name={}: {}", chat_id, wr.name, e)

            # 发送汇总
            success_count = sum(1 for wr in result.workers if wr.success)
            total = len(result.workers)
            summary = f"全部 {total} 个任务完成（{success_count} 成功"
            if success_count < total:
                summary += f"，{total - success_count} 失败"
            summary += "）"
            try:
                await on_worker_result("📊", summary)
            except Exception as e:
                logger.error("[{}] 回调汇总失败: {}", chat_id, e)

        except Exception as e:
            logger.exception("[{}] 异步 dispatch 执行失败: {}", chat_id, e)
            try:
                await on_worker_result("❌ 系统错误", f"异步派发执行失败: {e}")
            except Exception:
                pass


def _extract_pre_dispatch_text(text: str) -> str:
    """提取 <dispatch> 之前的文本。"""
    match = re.search(r"<dispatch>", text, re.IGNORECASE)
    return text[: match.start()].strip() if match else text.strip()
