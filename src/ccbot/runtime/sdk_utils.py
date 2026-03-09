"""ClaudeSDKClient 共享交互逻辑。

提取自 CCBotAgent.ask()，供 Supervisor 和 Worker 共用。
"""

from __future__ import annotations

from collections import deque
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
class StderrCapture:
    """Capture recent Claude Code stderr lines for diagnostics."""

    prefix: str
    max_lines: int = 40

    def __post_init__(self) -> None:
        self._lines: deque[str] = deque(maxlen=self.max_lines)

    def callback(self, line: str) -> None:
        self._lines.append(line)
        logger.warning("{} STDERR | {}", self.prefix, line)

    def snapshot(self, limit: int = 8) -> str:
        if not self._lines:
            return ""
        lines = list(self._lines)[-limit:]
        return "\n".join(lines)


def build_stderr_capture(prefix: str) -> StderrCapture:
    """Build stderr capture for a Claude SDK client."""
    return StderrCapture(prefix=prefix)


def format_sdk_error(error: Exception, recent_stderr: str = "") -> str:
    """Format SDK/runtime errors into a user-facing diagnostic string."""
    try:
        import claude_agent_sdk._errors as sdk_errors
    except Exception:
        sdk_errors = None

    process_error_cls = getattr(sdk_errors, "ProcessError", None) if sdk_errors else None
    if process_error_cls is not None and isinstance(error, process_error_cls):
        exit_code = getattr(error, "exit_code", None)
        if recent_stderr:
            return (
                "抱歉，处理消息时出现错误：Claude Code 子进程异常退出"
                f"（exit code: {exit_code}）。\n最近 stderr：\n{recent_stderr}"
            )
        return (
            "抱歉，处理消息时出现错误：Claude Code 子进程异常退出"
            f"（exit code: {exit_code}）。请查看服务日志中的 `[sdk:...] STDERR` 输出。"
        )

    if recent_stderr:
        return f"抱歉，处理消息时出现错误: {error}\n最近 stderr：\n{recent_stderr}"
    return f"抱歉，处理消息时出现错误: {error}"


def is_retryable_sdk_error(error: Exception) -> bool:
    """Whether the SDK error is likely recoverable by recreating the client once."""
    try:
        import claude_agent_sdk._errors as sdk_errors
    except Exception:
        sdk_errors = None

    if sdk_errors is None:
        return False

    process_error_cls = getattr(sdk_errors, "ProcessError", None)
    cli_connection_error_cls = getattr(sdk_errors, "CLIConnectionError", None)

    retryable_types = tuple(
        err_cls for err_cls in (process_error_cls, cli_connection_error_cls) if err_cls is not None
    )
    if retryable_types and isinstance(error, retryable_types):
        return True

    message = str(error).lower()
    return "terminated process" in message or "processtransport is not ready" in message


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
