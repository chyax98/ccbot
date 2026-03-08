"""AgentTeam: Supervisor-Worker 多 Agent 编排（全进程内 asyncio，无 bash 子进程）。

协议：
  1. Supervisor 接收用户任务，决定直接处理或输出 <dispatch>[...] 计划
  2. Python 解析计划，asyncio.gather 并行启动 CCBotAgent worker
  3. 每个 worker 的 on_progress 回调前缀 "[name] "，供上层聚合显示
  4. 全部完成后结果喂回 Supervisor 综合，返回最终回复

Phase 2 Update: 使用结构化 DispatchPayload 替代文本解析
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

from loguru import logger

from ccbot.agent import CCBotAgent
from ccbot.comm.bus import InMemoryBus
from ccbot.comm.channel import WorkerChannel
from ccbot.comm.context import InMemoryContext
from ccbot.config import AgentConfig
from ccbot.models import DispatchPayload, DispatchResult, WorkerResult, WorkerTask
from ccbot.models.comm import CommMessage
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
    "model": "sonnet",
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

Worker 通信：各 Worker 配备 MCP 通信工具（ccbot-comm），可互相发消息、
共享状态、向你汇报进度。Worker 间的通信记录会在结果中一并呈现。
"""

_WORKER_PROMPT = """\
You are a focused AI coding assistant.
Working directory: {cwd}
Complete the assigned task thoroughly and autonomously.
"""


class AgentTeam:
    """
    Supervisor（Opus）+ 动态 Worker 池，全部跑在同一 Python asyncio 进程内。

    - 无额外进程：worker 就是 CCBotAgent（ClaudeSDKClient 子进程）
    - 无 bash 开销：Python asyncio.gather 并行，Supervisor 全程感知
    - 实时进度：worker on_progress 前缀 "[name] "，由上层聚合为状态看板
    - 容错：单个 worker 失败不影响其他 worker，结果中标记 ❌
    - 结构化 Dispatch：使用 Pydantic 模型替代文本解析
    - 并发控制：max_workers 限制并行 worker 数量

    用法（等同 CCBotAgent.ask）：
        team = AgentTeam(config, workspace)
        reply = await team.ask(chat_id, prompt, on_progress=cb)
    """

    def __init__(self, config: AgentConfig, workspace: WorkspaceManager) -> None:
        self._config = config
        self._supervisor = CCBotAgent(config, workspace, extra_system_prompt=_SUPERVISOR_PROMPT)

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

        logger.info(
            "[{}] Supervisor 派发 {} 个 worker: {}",
            chat_id,
            len(dispatch.tasks),
            dispatch.worker_names,
        )
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
        """并行执行所有 worker 任务（受 max_workers 限制），返回结构化结果。"""
        session_id = f"{chat_id}:{uuid4().hex[:8]}"
        worker_names = [t.name for t in dispatch.tasks]
        semaphore = asyncio.Semaphore(self._config.max_workers)

        # 1. 创建通信基础设施
        bus = InMemoryBus()
        context = InMemoryContext()
        await bus.create_session(session_id, worker_names)
        await context.create_session(session_id)

        # 2. 注册上报回调 → on_progress
        async def _on_report(name: str, msg: CommMessage) -> None:
            text = f"[{name}] {msg.subject}: {msg.body[:200]}"
            logger.info("[{}] worker 汇报: {}", chat_id, text)
            if on_progress:
                await on_progress(text)

        bus.on_report(_on_report)

        try:

            async def run_one(task_def: WorkerTask) -> WorkerResult:
                """执行单个 worker 任务，带并发控制和完整生命周期管理。"""
                # 创建通信通道（SDK 进程内 MCP，无需 HTTP 服务器）
                peer_names = [n for n in worker_names if n != task_def.name]
                channel = WorkerChannel(bus, context, session_id, task_def.name, peer_names)

                cfg = AgentConfig(
                    model=task_def.model or self._config.model or "",
                    cwd=str(task_def.cwd),
                    system_prompt=_WORKER_PROMPT.format(cwd=task_def.cwd)
                    + channel.system_prompt_addition,
                    max_turns=task_def.max_turns,
                    mcp_servers={**self._config.mcp_servers, **channel.mcp_servers},
                    env=self._config.env,
                )
                worker = CCBotAgent(cfg)

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
                    await worker.start()
                    try:
                        result_text = await worker.ask(
                            f"{chat_id}:{task_def.name}",
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
                    except Exception as e:
                        logger.error("[{}] worker 失败 name={}: {}", chat_id, task_def.name, e)

                        if on_progress:
                            error_msg = str(e)[:80]
                            await on_progress(f"[{task_def.name}] 失败: {error_msg}")

                        return WorkerResult.from_exception(task_def.name, e)
                    finally:
                        await worker.stop()

            # 并行执行所有 worker（受 semaphore 限制并发数）
            worker_results = await asyncio.gather(
                *[run_one(task) for task in dispatch.tasks],
                return_exceptions=False,  # 异常在 run_one 内部捕获
            )

            # 3. 收集通信记录 + 状态快照
            comm_summary = await _build_comm_summary(bus, context, session_id)

            return DispatchResult(workers=list(worker_results), comm_summary=comm_summary)
        finally:
            await bus.close_session(session_id)
            await context.close_session(session_id)


async def _build_comm_summary(bus: InMemoryBus, context: InMemoryContext, session_id: str) -> str:
    """构建通信摘要文本。"""
    history = await bus.get_history(session_id)
    snapshot = await context.snapshot(session_id)

    if not history and not snapshot:
        return ""

    lines: list[str] = []
    if history:
        lines.append(f"通信记录（共 {len(history)} 条）：")
        for msg in history:
            direction = f"{msg.source}→{msg.target or 'all'}"
            lines.append(f"- [{msg.type.value}] {direction}: {msg.subject}")
            if msg.body:
                body_preview = msg.body[:100] + ("..." if len(msg.body) > 100 else "")
                lines.append(f"  {body_preview}")

    if snapshot:
        lines.append(f"\n共享状态快照：\n{snapshot}")

    return "\n".join(lines)
