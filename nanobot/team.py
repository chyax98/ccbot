"""AgentTeam: Supervisor-Worker 多 Agent 编排（全进程内 asyncio，无 bash 子进程）。

协议：
  1. Supervisor 接收用户任务，决定直接处理或输出 <dispatch>[...] 计划
  2. Python 解析计划，asyncio.gather 并行启动 NanobotAgent worker
  3. 每个 worker 的 on_progress 回调前缀 "[name] "，供上层聚合显示
  4. 全部完成后结果喂回 Supervisor 综合，返回最终回复
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable

from loguru import logger

from nanobot.agent import NanobotAgent
from nanobot.config import AgentConfig
from nanobot.workspace import WorkspaceManager

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

    用法（等同 NanobotAgent.ask）：
        team = AgentTeam(config, workspace)
        reply = await team.ask(chat_id, prompt, on_progress=cb)
    """

    _DISPATCH_RE = re.compile(r"<dispatch>\s*([\s\S]*?)\s*</dispatch>", re.IGNORECASE)

    def __init__(self, config: AgentConfig, workspace: WorkspaceManager) -> None:
        self._config = config
        self._supervisor = NanobotAgent(
            config, workspace, extra_system_prompt=_SUPERVISOR_PROMPT
        )

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
        supervisor_reply = await self._supervisor.ask(
            chat_id, prompt, on_progress=on_progress
        )

        # Step 2: 是否有 dispatch 计划
        match = self._DISPATCH_RE.search(supervisor_reply)
        if not match:
            return supervisor_reply  # Supervisor 直接处理了，无需派发

        try:
            tasks: list[dict] = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.warning("dispatch JSON 解析失败: {} | raw: {}", e, match.group(1)[:300])
            return supervisor_reply

        if not isinstance(tasks, list) or not tasks:
            return supervisor_reply

        names = ", ".join(t.get("name", "?") for t in tasks)
        logger.info("[{}] Supervisor 派发 {} 个 worker: {}", chat_id, len(tasks), names)
        if on_progress:
            await on_progress(f"📋 派发 {len(tasks)} 个子任务: {names}")

        # Step 3: 并行执行所有 worker
        results = await self._run_workers(chat_id, tasks, on_progress)

        # Step 4: 喂回 Supervisor 综合
        synthesis = self._build_synthesis_prompt(tasks, results)
        logger.info("[{}] 所有 worker 完成，请求 Supervisor 综合", chat_id)
        return await self._supervisor.ask(chat_id, synthesis, on_progress=on_progress)

    async def _run_workers(
        self,
        chat_id: str,
        tasks: list[dict],
        on_progress: Callable[[str], Awaitable[None]] | None,
    ) -> list[str | BaseException]:
        async def run_one(task: dict) -> str:
            name = task.get("name", "worker")
            cwd = task.get("cwd", ".")
            model = task.get("model", "") or self._config.model or ""
            max_turns = int(task.get("max_turns", 30))

            cfg = AgentConfig(
                model=model,
                cwd=str(cwd),
                system_prompt=_WORKER_PROMPT.format(cwd=cwd),
                max_turns=max_turns,
            )
            worker = NanobotAgent(cfg)

            async def worker_progress(msg: str) -> None:
                tagged = f"[{name}] {msg}"
                logger.info("[{}] {}", chat_id, tagged)
                if on_progress:
                    await on_progress(tagged)

            logger.info("[{}] 启动 worker name={} cwd={} model={}", chat_id, name, cwd, model or "default")
            result = await worker.ask(f"{chat_id}:{name}", task["task"], on_progress=worker_progress)
            logger.info("[{}] worker 完成 name={} ({} chars)", chat_id, name, len(result))
            return result

        return list(
            await asyncio.gather(*[run_one(t) for t in tasks], return_exceptions=True)
        )

    @staticmethod
    def _build_synthesis_prompt(
        tasks: list[dict], results: list[str | BaseException]
    ) -> str:
        lines = ["以下是各 worker 的执行结果，请综合后向用户汇报：\n"]
        for task, result in zip(tasks, results):
            name = task.get("name", "worker")
            if isinstance(result, BaseException):
                lines.append(f"### [{name}] ❌ 执行失败\n错误: {result}\n")
            else:
                lines.append(f"### [{name}]\n{result}\n")
        return "\n".join(lines)
