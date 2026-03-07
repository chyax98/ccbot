"""AgentTeam: Supervisor-Worker 多 Agent 编排（全进程内 asyncio，无 bash 子进程）。

协议：
  1. Supervisor 接收用户任务，决定直接处理或输出 <dispatch>[...] 计划
  2. Python 解析计划，asyncio.gather 并行启动 NanobotAgent worker
  3. 每个 worker 的 on_progress 回调前缀 "[name] "，供上层聚合显示
  4. 全部完成后结果喂回 Supervisor 综合，返回最终回复

Phase 2 Update: 使用结构化 DispatchPayload 替代文本解析
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger

from ccbot.agent import NanobotAgent
from ccbot.config import AgentConfig
from ccbot.models import DispatchPayload, DispatchResult, WorkerResult
from ccbot.workspace import WorkspaceManager

# Supervisor 额外注入的多 Agent 调度说明
_SUPERVISOR_PROMPT = """\
## Multi-Agent Dispatch

当任务适合并行或专项执行时，**立即输出 dispatch 计划，不要自己动手**：

<dispatch>
[
  {
    "name": "worker 唯一名称（如 frontend / backend / reviewer）",
    "cwd": "/绝对路径/工作目录",
    "task": "详细任务描述",
    "model": "claude-sonnet-4-6",
    "max_turns": 30
  }
]
</dispatch>

规则：
- name 在本次 dispatch 中唯一，用于日志和进度显示
- cwd 必须是绝对路径；同一 repo 内各 worker 操作不重叠的文件/目录
- model / max_turns 可省略（默认继承 Supervisor 配置）
- dispatch 块之外可以写给用户看的说明，但不要在 dispatch 块内加注释
- 收到 worker 结果后，综合成清晰的汇报返回给用户
"""

_WORKER_PROMPT = """\
You are a focused AI coding assistant.
Working directory: {cwd}
Complete the assigned task thoroughly and autonomously.
"""


class AgentTeam:
    """
    Supervisor（Opus）+ 动态 Worker 池，全部跑在同一 Python asyncio 进程内。

    - 无额外进程：worker 就是 NanobotAgent（ClaudeSDKClient 子进程）
    - 无 bash 开销：Python asyncio.gather 并行，Supervisor 全程感知
    - 实时进度：worker on_progress 前缀 "[name] "，由上层聚合为状态看板
    - 容错：单个 worker 失败不影响其他 worker，结果中标记 ❌
    - 结构化 Dispatch：使用 Pydantic 模型替代文本解析

    用法（等同 NanobotAgent.ask）：
        team = AgentTeam(config, workspace)
        reply = await team.ask(chat_id, prompt, on_progress=cb)
    """

    def __init__(self, config: AgentConfig, workspace: WorkspaceManager) -> None:
        self._config = config
        self._supervisor = NanobotAgent(config, workspace, extra_system_prompt=_SUPERVISOR_PROMPT)

    async def start(self) -> None:
        """启动 AgentTeam，启动 Supervisor 的 AgentPool。"""
        await self._supervisor.start()

    async def stop(self) -> None:
        """停止 AgentTeam，关闭所有 client。"""
        await self._supervisor.stop()

    @property
    def last_chat_id(self) -> str | None:
        return self._supervisor.last_chat_id

    async def ask(
        self,
        chat_id: str,
        prompt: str,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """处理消息：Supervisor 分析 → 可选 dispatch → 综合回复。"""
        # Step 1: Supervisor 分析任务
        if on_progress:
            await on_progress("📋 分析任务中...")

        supervisor_reply = await self._supervisor.ask(chat_id, prompt, on_progress=on_progress)

        # Step 2: 解析 dispatch 计划（使用结构化模型）
        dispatch = DispatchPayload.from_text(supervisor_reply)
        if dispatch is None:
            return supervisor_reply  # Supervisor 直接处理了，无需派发

        logger.info("[{}] Supervisor 派发 {} 个 worker: {}", chat_id, len(dispatch.tasks), dispatch.worker_names)
        if on_progress:
            await on_progress(f"📋 派发任务: {dispatch.worker_names}")

        # Step 3: 并行执行所有 worker
        result = await self._run_workers(chat_id, dispatch, on_progress)

        # Step 4: 喂回 Supervisor 综合
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
        """并行执行所有 worker 任务，返回结构化结果。"""

        async def run_one(task_def) -> WorkerResult:
            """执行单个 worker 任务。"""
            cfg = AgentConfig(
                model=task_def.model or self._config.model or "",
                cwd=str(task_def.cwd),
                system_prompt=_WORKER_PROMPT.format(cwd=task_def.cwd),
                max_turns=task_def.max_turns,
            )
            worker = NanobotAgent(cfg)

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

            try:
                result_text = await worker.ask(
                    f"{chat_id}:{task_def.name}", task_def.task, on_progress=worker_progress
                )
                logger.info("[{}] worker 完成 name={} ({} chars)", chat_id, task_def.name, len(result_text))

                # 发送 worker 完成的 milestone 消息
                if on_progress:
                    await on_progress(f"✅ {task_def.name} 完成")

                return WorkerResult.from_result(task_def.name, result_text)
            except Exception as e:
                logger.error("[{}] worker 失败 name={}: {}", chat_id, task_def.name, e)

                # 发送 worker 失败的 milestone 消息
                if on_progress:
                    error_msg = str(e)[:80]
                    await on_progress(f"❌ {task_def.name} 失败: {error_msg}")

                return WorkerResult.from_exception(task_def.name, e)

        # 并行执行所有 worker
        worker_results = await asyncio.gather(
            *[run_one(task) for task in dispatch.tasks],
            return_exceptions=False,  # 我们在 run_one 内部捕获异常
        )

        return DispatchResult(workers=worker_results)
