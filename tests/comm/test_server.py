"""SDK 进程内 MCP 通信服务器测试。"""

from __future__ import annotations

from typing import Any

from ccbot.comm.bus import InMemoryBus
from ccbot.comm.context import InMemoryContext
from ccbot.comm.server import _make_worker_tools, create_worker_mcp_server


def _tool_map(bus: InMemoryBus, ctx: InMemoryContext, session_id: str, name: str) -> dict:
    """创建 {tool_name: SdkMcpTool} 映射，直接调用 handler 无需 MCP 协议。"""
    tools = _make_worker_tools(bus, ctx, session_id, name)
    return {t.name: t for t in tools}


async def _call(tools: dict, name: str, args: dict[str, Any] | None = None) -> str:
    """调用指定工具，返回文本内容。"""
    if args is None:
        args = {}
    result = await tools[name].handler(args)
    return " ".join(c["text"] for c in result["content"] if c.get("type") == "text")


def test_create_worker_mcp_server_returns_sdk_config():
    """create_worker_mcp_server 返回 sdk 类型配置。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    config = create_worker_mcp_server(bus, ctx, "s1", "alice")

    assert config["type"] == "sdk"
    assert config["name"] == "ccbot-comm"
    assert "instance" in config


async def test_tools_registered():
    """_make_worker_tools 返回全部 7 个工具。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice", "bob"])
    await ctx.create_session("s1")

    tools = _tool_map(bus, ctx, "s1", "alice")
    expected = {
        "ccbot_send_message",
        "ccbot_read_messages",
        "ccbot_list_workers",
        "ccbot_set_shared",
        "ccbot_get_shared",
        "ccbot_list_shared",
        "ccbot_report_progress",
    }
    assert expected == set(tools.keys())

    await bus.close_session("s1")
    await ctx.close_session("s1")


async def test_send_and_read_messages():
    """通过工具发送和读取消息。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice", "bob"])
    await ctx.create_session("s1")

    alice = _tool_map(bus, ctx, "s1", "alice")
    bob = _tool_map(bus, ctx, "s1", "bob")

    # Alice 给 Bob 发消息
    result = await _call(alice, "ccbot_send_message", {"to": "bob", "subject": "hello", "body": "hi bob"})
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
    """通过工具读写共享状态。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()
    await bus.create_session("s1", ["alice", "bob"])
    await ctx.create_session("s1")

    alice = _tool_map(bus, ctx, "s1", "alice")
    bob = _tool_map(bus, ctx, "s1", "bob")

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

    tools = _tool_map(bus, ctx, "s1", "alice")
    result = await _call(tools, "ccbot_list_workers")
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

    tools = _tool_map(bus, ctx, "s1", "alice")
    result = await _call(tools, "ccbot_report_progress", {"status": "50%", "details": "halfway"})
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

    alice = _tool_map(bus, ctx, "s1", "alice")

    # Alice 发广播 — source 自动为 "alice"
    await _call(alice, "ccbot_send_message", {"to": "", "subject": "broadcast", "body": "hello all"})

    # Bob 收到的消息 source 应该是 alice
    messages = await bus.receive("s1", "bob")
    assert len(messages) == 1
    assert messages[0].source == "alice"

    await bus.close_session("s1")
    await ctx.close_session("s1")
