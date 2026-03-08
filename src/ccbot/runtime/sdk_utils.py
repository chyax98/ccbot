"""ClaudeSDKClient 共享交互逻辑。

提取自 CCBotAgent.ask()，供 Supervisor 和 Worker 共用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskProgressMessage,
    TextBlock,
    ToolUseBlock,
)
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from claude_agent_sdk import ClaudeSDKClient


@dataclass
class AgentRunResult:
    text: str
    structured_output: Any = None
    runtime_session_id: str = ""


async def query_and_collect_result(
    client: ClaudeSDKClient,
    prompt: str,
    *,
    session_id: str = "default",
    on_progress: Callable[[str], Awaitable[None]] | None = None,
    log_prefix: str = "",
) -> AgentRunResult:
    """向 ClaudeSDKClient 发送 query，并收集文本与 structured_output。"""
    await client.query(prompt, session_id=session_id)

    parts: list[str] = []
    tool_count = 0
    structured_output: Any = None
    runtime_session_id = ""

    async for msg in client.receive_response():
        if isinstance(msg, TaskProgressMessage):
            tool = msg.last_tool_name or "tool"
            desc = (msg.description or "").strip()
            logger.info("{} 🔧 {} | {}", log_prefix, tool, desc[:120])
            tool_count += 1
            if on_progress:
                detail = f"🔧 {tool}"
                if desc:
                    detail = f"🔧 {tool}: {desc[:80]}"
                await on_progress(detail)

        elif isinstance(msg, ResultMessage):
            cost = f"${msg.total_cost_usd:.4f}" if msg.total_cost_usd else "n/a"
            duration = f"{msg.duration_ms / 1000:.1f}s"
            logger.info(
                "{} ✅ 完成 | {} 轮 | {} 工具 | {} | {}",
                log_prefix,
                msg.num_turns,
                tool_count,
                cost,
                duration,
            )
            structured_output = msg.structured_output
            runtime_session_id = msg.session_id or runtime_session_id
            if msg.is_error:
                logger.warning("{} stop_reason={}", log_prefix, msg.stop_reason)

        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text:
                    parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    _log_tool_use(block, log_prefix)

        elif isinstance(msg, SystemMessage):
            logger.debug("{} sys subtype={}", log_prefix, msg.subtype)

    text = "\n".join(parts) or "（无响应）"
    return AgentRunResult(
        text=text,
        structured_output=structured_output,
        runtime_session_id=runtime_session_id,
    )


async def query_and_collect(
    client: ClaudeSDKClient,
    prompt: str,
    *,
    session_id: str = "default",
    on_progress: Callable[[str], Awaitable[None]] | None = None,
    log_prefix: str = "",
) -> str:
    result = await query_and_collect_result(
        client,
        prompt,
        session_id=session_id,
        on_progress=on_progress,
        log_prefix=log_prefix,
    )
    return result.text




def build_stderr_logger(prefix: str):
    """Build a Claude SDK stderr callback that forwards CLI stderr into loguru."""

    def _callback(line: str) -> None:
        logger.warning("{} STDERR | {}", prefix, line)

    return _callback

def _log_tool_use(block: ToolUseBlock, prefix: str) -> None:
    """记录工具调用详情。"""
    tool_input = block.input
    if block.name == "Bash" and isinstance(tool_input, dict):
        cmd = tool_input.get("command", "")
        logger.info("{} $ {}", prefix, cmd[:200])
    elif block.name == "Write" and isinstance(tool_input, dict):
        logger.info("{} ✍️  Write {}", prefix, tool_input.get("file_path", ""))
    elif block.name == "Read" and isinstance(tool_input, dict):
        logger.info("{} 📖 Read {}", prefix, tool_input.get("file_path", ""))
    else:
        logger.info("{} ⚡ {} | {}", prefix, block.name, str(tool_input)[:200])
