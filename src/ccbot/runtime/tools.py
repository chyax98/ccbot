"""SDK in-process MCP tools — 将运行时能力暴露给 Supervisor agent。

使用 Claude Agent SDK 的 @tool + create_sdk_mcp_server() 机制，
工具函数在 ccbot 主进程内执行，直接操作 SchedulerService 内存状态。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from loguru import logger

from ccbot.models.schedule import ScheduleSpec
from ccbot.scheduler import SchedulerService

# SDK MCP server 名称，工具名格式为 mcp__ccbot-runtime__<tool_name>
SERVER_NAME = "ccbot-runtime"

# 上下文提供者类型：返回当前会话的 channel/notify_target 等信息
ContextProvider = Callable[[], dict[str, str]]


def _noop_context() -> dict[str, str]:
    return {}


def create_runtime_tools(
    scheduler: SchedulerService,
    get_context: ContextProvider | None = None,
) -> dict[str, Any]:
    """创建 ccbot 运行时 SDK MCP server。

    通过闭包捕获 SchedulerService 引用，工具函数直接操作内存中的 scheduler。

    Args:
        scheduler: 定时任务调度服务实例
        get_context: 可选的上下文提供者，返回 channel/notify_target 等会话信息

    Returns:
        McpSdkServerConfig dict，可直接放入 ClaudeAgentOptions.mcp_servers
    """
    ctx = get_context or _noop_context

    # ── schedule_list ──

    @tool("schedule_list", "列出所有定时任务（含已暂停的）", {})
    async def schedule_list(args: dict[str, Any]) -> dict[str, Any]:
        jobs = scheduler.list_jobs()
        if not jobs:
            return _text("当前没有定时任务。")

        lines: list[str] = []
        for job in jobs:
            status = "enabled" if job.enabled else "paused"
            lines.append(
                f"- job_id={job.job_id}  name={job.name}  "
                f"cron={job.cron_expr}  tz={job.timezone}  "
                f"status={status}  next={job.next_run_at}"
            )
        return _text(f"共 {len(jobs)} 个定时任务：\n" + "\n".join(lines))

    # ── schedule_create ──

    @tool(
        "schedule_create",
        "创建一个新的定时任务。cron_expr 使用标准 5 段格式（如 '0 9 * * *'），timezone 使用 IANA 时区名。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "任务名称，简洁可读"},
                "cron_expr": {"type": "string", "description": "标准 5 段 cron 表达式"},
                "timezone": {"type": "string", "description": "IANA 时区名，默认 Asia/Shanghai"},
                "prompt": {"type": "string", "description": "到点后发给 Supervisor 的执行提示词"},
                "purpose": {"type": "string", "description": "创建该任务的目的说明"},
            },
            "required": ["name", "cron_expr", "prompt"],
        },
    )
    async def schedule_create(args: dict[str, Any]) -> dict[str, Any]:
        try:
            spec = ScheduleSpec(
                name=args["name"],
                cron_expr=args["cron_expr"],
                timezone=args.get("timezone", "Asia/Shanghai"),
                prompt=args["prompt"],
                purpose=args.get("purpose", ""),
            )
        except Exception as exc:
            return _error(f"参数校验失败：{exc}")

        context = ctx()
        try:
            job = scheduler.create_job(
                spec,
                created_by=context.get("sender_id", "agent"),
                channel=context.get("channel", ""),
                notify_target=context.get("notify_target", ""),
                conversation_id=context.get("conversation_id", ""),
            )
        except Exception as exc:
            logger.warning("创建定时任务失败: {}", exc)
            return _error(f"创建失败：{exc}")

        return _text(
            f"已创建定时任务：{job.name}\n"
            f"- job_id: {job.job_id}\n"
            f"- cron: {job.cron_expr}\n"
            f"- timezone: {job.timezone}\n"
            f"- next_run_at: {job.next_run_at}"
        )

    # ── schedule_delete ──

    @tool(
        "schedule_delete",
        "删除指定的定时任务",
        {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    )
    async def schedule_delete(args: dict[str, Any]) -> dict[str, Any]:
        job_id = args.get("job_id", "")
        if not job_id:
            return _error("缺少 job_id 参数")

        job = scheduler.get_job(job_id)
        if job is None:
            return _error(f"定时任务不存在：{job_id}")

        scheduler.delete_job(job_id)
        return _text(f"已删除定时任务：{job.name} ({job_id})")

    # ── schedule_pause ──

    @tool(
        "schedule_pause",
        "暂停指定的定时任务",
        {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    )
    async def schedule_pause(args: dict[str, Any]) -> dict[str, Any]:
        job_id = args.get("job_id", "")
        if not job_id:
            return _error("缺少 job_id 参数")

        if not scheduler.pause_job(job_id):
            return _error(f"定时任务不存在：{job_id}")

        return _text(f"已暂停定时任务：{job_id}")

    # ── schedule_resume ──

    @tool(
        "schedule_resume",
        "恢复已暂停的定时任务",
        {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    )
    async def schedule_resume(args: dict[str, Any]) -> dict[str, Any]:
        job_id = args.get("job_id", "")
        if not job_id:
            return _error("缺少 job_id 参数")

        if not scheduler.resume_job(job_id):
            return _error(f"定时任务不存在：{job_id}")

        return _text(f"已恢复定时任务：{job_id}")

    # ── 构建 MCP server ──

    tools: list[SdkMcpTool[Any]] = [
        schedule_list,
        schedule_create,
        schedule_delete,
        schedule_pause,
        schedule_resume,
    ]

    server = create_sdk_mcp_server(SERVER_NAME, version="1.0.0", tools=tools)
    logger.debug("已注册 {} 个运行时工具到 SDK MCP server '{}'", len(tools), SERVER_NAME)
    return server


def _text(text: str) -> dict[str, Any]:
    """构造 MCP 文本结果。"""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """构造 MCP 错误结果。"""
    return {"content": [{"type": "text", "text": message}], "is_error": True}
