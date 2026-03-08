"""CommServer：基于 Claude Agent SDK 的进程内 MCP 通信服务器。

每个 Worker 获得一个独立的 McpSdkServerConfig，worker 身份通过闭包捕获，
工具参数中无需传入 worker_name。所有 Worker 共享同一个 bus 和 context 实例。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ccbot.comm.bus import InMemoryBus
    from ccbot.comm.context import InMemoryContext


def _make_worker_tools(
    bus: InMemoryBus,
    context: InMemoryContext,
    session_id: str,
    worker_name: str,
) -> list[Any]:
    """创建 Worker 的 SdkMcpTool 列表（可供测试直接调用 handler）。"""
    from claude_agent_sdk import tool

    from ccbot.models.comm import CommMessage, MessageType

    @tool(
        "ccbot_send_message",
        "发送消息给其他 Worker 或 Supervisor",
        {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": '目标（worker名=点对点, ""=广播, "supervisor"=上报）',
                },
                "subject": {"type": "string", "description": "消息主题"},
                "body": {"type": "string", "description": "消息内容"},
            },
            "required": ["to", "subject", "body"],
        },
    )
    async def ccbot_send_message(args: dict[str, Any]) -> dict[str, Any]:
        to = args["to"]
        msg_type = MessageType.DIRECT
        if to == "supervisor":
            msg_type = MessageType.REPORT
        elif to == "":
            msg_type = MessageType.BROADCAST

        msg = CommMessage(
            type=msg_type,
            source=worker_name,
            target=to,
            session_id=session_id,
            subject=args["subject"],
            body=args["body"],
        )
        await bus.send(msg)
        return _text(f"消息已发送 (id={msg.id}, to={to or 'all'})")

    @tool(
        "ccbot_read_messages",
        "读取收到的消息",
        {
            "type": "object",
            "properties": {
                "since_timestamp": {
                    "type": "number",
                    "description": "只返回此时间戳之后的消息（0=全部）",
                    "default": 0,
                },
            },
            "required": [],
        },
    )
    async def ccbot_read_messages(args: dict[str, Any]) -> dict[str, Any]:
        since = args.get("since_timestamp", 0.0)
        messages = await bus.receive(session_id, worker_name, since=since)
        if not messages:
            return _text("没有新消息")

        lines = []
        for m in messages:
            lines.append(
                f"[{m.source}→{m.target or 'all'}] {m.subject}: {m.body} (ts={m.timestamp:.2f})"
            )
        return _text("\n".join(lines))

    @tool(
        "ccbot_list_workers",
        "查看当前协作的 Worker 列表",
        {"type": "object", "properties": {}, "required": []},
    )
    async def ccbot_list_workers(args: dict[str, Any]) -> dict[str, Any]:
        names = bus.get_worker_names(session_id)
        return _text(", ".join(names) if names else "无可用 Worker")

    @tool(
        "ccbot_set_shared",
        "设置共享状态（所有 Worker 可读）",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "状态键名"},
                "value": {"type": "string", "description": "状态值"},
            },
            "required": ["key", "value"],
        },
    )
    async def ccbot_set_shared(args: dict[str, Any]) -> dict[str, Any]:
        await context.set(session_id, args["key"], args["value"], author=worker_name)
        return _text(f"已设置共享状态: {args['key']}")

    @tool(
        "ccbot_get_shared",
        "读取共享状态",
        {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "状态键名"},
            },
            "required": ["key"],
        },
    )
    async def ccbot_get_shared(args: dict[str, Any]) -> dict[str, Any]:
        value = await context.get(session_id, args["key"])
        if value is None:
            return _text(f"键 '{args['key']}' 不存在")
        return _text(value)

    @tool(
        "ccbot_list_shared",
        "列出所有共享状态的键名",
        {"type": "object", "properties": {}, "required": []},
    )
    async def ccbot_list_shared(args: dict[str, Any]) -> dict[str, Any]:
        keys = await context.list_keys(session_id)
        return _text(", ".join(keys) if keys else "无共享状态")

    @tool(
        "ccbot_report_progress",
        "向 Supervisor 汇报进度",
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": '进度状态（如 "50% 完成"）'},
                "details": {"type": "string", "description": "详细说明", "default": ""},
            },
            "required": ["status"],
        },
    )
    async def ccbot_report_progress(args: dict[str, Any]) -> dict[str, Any]:
        msg = CommMessage(
            type=MessageType.REPORT,
            source=worker_name,
            target="supervisor",
            session_id=session_id,
            subject=args["status"],
            body=args.get("details", ""),
        )
        await bus.send(msg)
        return _text("进度已汇报")

    return [
        ccbot_send_message,
        ccbot_read_messages,
        ccbot_list_workers,
        ccbot_set_shared,
        ccbot_get_shared,
        ccbot_list_shared,
        ccbot_report_progress,
    ]


def create_worker_mcp_server(
    bus: InMemoryBus,
    context: InMemoryContext,
    session_id: str,
    worker_name: str,
) -> dict[str, Any]:
    """为指定 Worker 创建进程内 MCP 服务器配置。

    使用 Claude Agent SDK 的 sdk 类型 MCP 服务器，worker 身份通过闭包捕获。

    Returns:
        McpSdkServerConfig（可直接放入 mcp_servers dict）
    """
    from claude_agent_sdk import create_sdk_mcp_server

    tools = _make_worker_tools(bus, context, session_id, worker_name)
    return create_sdk_mcp_server(name="ccbot-comm", tools=tools)


def _text(s: str) -> dict[str, Any]:
    """构造 SDK MCP 工具返回格式。"""
    return {"content": [{"type": "text", "text": s}]}
