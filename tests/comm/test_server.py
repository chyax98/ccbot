"""SDK 进程内 MCP 通信服务器测试。"""

from __future__ import annotations

from typing import Any

from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

from ccbot.comm.bus import InMemoryBus
from ccbot.comm.context import InMemoryContext
from ccbot.comm.server import create_worker_mcp_server


async def _list_tools(server) -> list[str]:
    """获取 MCP 服务器注册的工具名列表。"""
    handler = server.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list", params=None))
    return [t.name for t in result.root.tools]


async def _call(server, name: str, args: dict[str, Any] | None = None) -> str:
    """调用 MCP 工具并返回文本结果。"""
    if args is None:
        args = {}
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=args),
        )
    )
    return " ".join(c.text for c in result.root.content if hasattr(c, "text"))


def test_create_worker_mcp_server_returns_sdk_config():
    """create_worker_mcp_server 返回 sdk 类型配置。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    config = create_worker_mcp_server(bus, ctx, "s1", "alice")

    assert config["type"] == "sdk"
    assert config["name"] == "ccbot-comm"
    assert "instance" in config


async def test_tools_registered():
    """MCP 服务器注册了全部 7 个工具。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice", "bob"])
    await ctx.create_session("s1")

    server = create_worker_mcp_server(bus, ctx, "s1", "alice")["instance"]
    tool_names = set(await _list_tools(server))
    expected = {
        "ccbot_send_message",
        "ccbot_read_messages",
        "ccbot_list_workers",
        "ccbot_set_shared",
        "ccbot_get_shared",
        "ccbot_list_shared",
        "ccbot_report_progress",
    }
    assert expected == tool_names

    await bus.close_session("s1")
    await ctx.close_session("s1")


async def test_send_and_read_messages():
    """通过 MCP 工具发送和读取消息。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice", "bob"])
    await ctx.create_session("s1")

    alice = create_worker_mcp_server(bus, ctx, "s1", "alice")["instance"]
    bob = create_worker_mcp_server(bus, ctx, "s1", "bob")["instance"]

    # Alice 给 Bob 发消息
    result = await _call(
        alice, "ccbot_send_message", {"to": "bob", "subject": "hello", "body": "hi bob"}
    )
    assert "消息已发送" in result

    # Bob 读消息
    result = await _call(bob, "ccbot_read_messages")
    assert "hello" in result
    assert "hi bob" in result

    # Bob 再读应该没有新消息
    result = await _call(bob, "ccbot_read_messages")
    assert "没有新消息" in result

    await bus.close_session("s1")
    await ctx.close_session("s1")


async def test_shared_context_via_tools():
    """通过 MCP 工具读写共享状态。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice", "bob"])
    await ctx.create_session("s1")

    alice = create_worker_mcp_server(bus, ctx, "s1", "alice")["instance"]
    bob = create_worker_mcp_server(bus, ctx, "s1", "bob")["instance"]

    # Alice 设置共享状态
    result = await _call(alice, "ccbot_set_shared", {"key": "result", "value": "done"})
    assert "已设置" in result

    # Bob 读取共享状态
    result = await _call(bob, "ccbot_get_shared", {"key": "result"})
    assert "done" in result

    # 列出共享键
    result = await _call(alice, "ccbot_list_shared")
    assert "result" in result

    await bus.close_session("s1")
    await ctx.close_session("s1")


async def test_list_workers():
    """ccbot_list_workers 返回所有 Worker。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice", "bob", "charlie"])
    await ctx.create_session("s1")

    server = create_worker_mcp_server(bus, ctx, "s1", "alice")["instance"]
    result = await _call(server, "ccbot_list_workers")
    assert "alice" in result
    assert "bob" in result
    assert "charlie" in result

    await bus.close_session("s1")
    await ctx.close_session("s1")


async def test_report_progress():
    """ccbot_report_progress 触发回调。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice"])
    await ctx.create_session("s1")

    reports: list[tuple[str, object]] = []

    async def on_report(name, msg):
        reports.append((name, msg))

    bus.on_report(on_report)

    server = create_worker_mcp_server(bus, ctx, "s1", "alice")["instance"]
    result = await _call(server, "ccbot_report_progress", {"status": "50%", "details": "halfway"})
    assert "已汇报" in result
    assert len(reports) == 1
    assert reports[0][0] == "alice"

    await bus.close_session("s1")
    await ctx.close_session("s1")


async def test_worker_identity_baked_in():
    """Worker 身份通过闭包捕获，无需工具参数。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice", "bob"])
    await ctx.create_session("s1")

    alice = create_worker_mcp_server(bus, ctx, "s1", "alice")["instance"]

    # Alice 发广播 — source 自动为 "alice"
    await _call(
        alice, "ccbot_send_message", {"to": "", "subject": "broadcast", "body": "hello all"}
    )

    # Bob 收到的消息 source 应该是 alice
    messages = await bus.receive("s1", "bob")
    assert len(messages) == 1
    assert messages[0].source == "alice"

    await bus.close_session("s1")
    await ctx.close_session("s1")
