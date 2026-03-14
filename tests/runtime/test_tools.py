"""SDK in-process runtime tools 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ccbot.runtime.tools import SERVER_NAME, create_runtime_tools


@pytest.fixture
def scheduler() -> MagicMock:
    return MagicMock()


@pytest.fixture
def tools(scheduler: MagicMock) -> dict:
    return create_runtime_tools(scheduler)


def test_server_name(tools: dict) -> None:
    assert tools["name"] == SERVER_NAME


def test_server_has_type_sdk(tools: dict) -> None:
    assert tools["type"] == "sdk"


@pytest.mark.asyncio
async def test_schedule_list_empty(scheduler: MagicMock) -> None:
    scheduler.list_jobs.return_value = []
    tools = create_runtime_tools(scheduler)
    # 直接调用 MCP server 的 tool handler
    server_instance = tools["instance"]
    # 通过 server 的内部 tools 列表找到 schedule_list
    result = await _call_tool(server_instance, "schedule_list", {})
    assert "没有定时任务" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_schedule_list_with_jobs(scheduler: MagicMock) -> None:
    scheduler.list_jobs.return_value = [
        MagicMock(
            job_id="job-1",
            name="日报",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            enabled=True,
            next_run_at="2026-01-17 09:00:00",
        ),
    ]
    tools = create_runtime_tools(scheduler)
    result = await _call_tool(tools["instance"], "schedule_list", {})
    text = result["content"][0]["text"]
    assert "job-1" in text
    assert "日报" in text
    assert "0 9 * * *" in text


@pytest.mark.asyncio
async def test_schedule_create_success(scheduler: MagicMock) -> None:
    scheduler.create_job.return_value = MagicMock(
        job_id="job-new",
        name="周报",
        cron_expr="0 10 * * 1",
        timezone="Asia/Shanghai",
        next_run_at="2026-01-20 10:00:00",
    )
    tools = create_runtime_tools(scheduler)
    result = await _call_tool(
        tools["instance"],
        "schedule_create",
        {
            "name": "周报",
            "cron_expr": "0 10 * * 1",
            "prompt": "生成周报",
        },
    )
    text = result["content"][0]["text"]
    assert "已创建" in text
    assert "job-new" in text
    scheduler.create_job.assert_called_once()


@pytest.mark.asyncio
async def test_schedule_create_with_context(scheduler: MagicMock) -> None:
    scheduler.create_job.return_value = MagicMock(
        job_id="job-ctx",
        name="测试",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        next_run_at="2026-01-17 09:00:00",
    )
    context = {"sender_id": "user-1", "channel": "feishu", "notify_target": "chat-1"}
    tools = create_runtime_tools(scheduler, get_context=lambda: context)
    result = await _call_tool(
        tools["instance"],
        "schedule_create",
        {"name": "测试", "cron_expr": "0 9 * * *", "prompt": "测试任务"},
    )
    assert "is_error" not in result or not result.get("is_error")
    # 验证 context 被传递给 scheduler.create_job
    call_kwargs = scheduler.create_job.call_args
    assert call_kwargs.kwargs["created_by"] == "user-1"
    assert call_kwargs.kwargs["channel"] == "feishu"


@pytest.mark.asyncio
async def test_schedule_delete_success(scheduler: MagicMock) -> None:
    scheduler.get_job.return_value = MagicMock(job_id="job-1", name="日报")
    scheduler.delete_job.return_value = True
    tools = create_runtime_tools(scheduler)
    result = await _call_tool(tools["instance"], "schedule_delete", {"job_id": "job-1"})
    text = result["content"][0]["text"]
    assert "已删除" in text
    assert "日报" in text


@pytest.mark.asyncio
async def test_schedule_delete_not_found(scheduler: MagicMock) -> None:
    scheduler.get_job.return_value = None
    tools = create_runtime_tools(scheduler)
    result = await _call_tool(tools["instance"], "schedule_delete", {"job_id": "bad-id"})
    # SDK create_sdk_mcp_server 不传递 is_error 标志，通过文本判断错误
    assert "不存在" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_schedule_pause_success(scheduler: MagicMock) -> None:
    scheduler.pause_job.return_value = True
    tools = create_runtime_tools(scheduler)
    result = await _call_tool(tools["instance"], "schedule_pause", {"job_id": "job-1"})
    assert "已暂停" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_schedule_resume_success(scheduler: MagicMock) -> None:
    scheduler.resume_job.return_value = True
    tools = create_runtime_tools(scheduler)
    result = await _call_tool(tools["instance"], "schedule_resume", {"job_id": "job-1"})
    assert "已恢复" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_schedule_pause_not_found(scheduler: MagicMock) -> None:
    scheduler.pause_job.return_value = False
    tools = create_runtime_tools(scheduler)
    result = await _call_tool(tools["instance"], "schedule_pause", {"job_id": "bad"})
    assert "不存在" in result["content"][0]["text"]


# ── 辅助函数 ──


async def _call_tool(server_instance: object, tool_name: str, args: dict) -> dict:
    """通过 MCP Server 实例直接调用 tool handler。

    SDK MCP Server 的 tool handler 注册在 server.request_handlers 中，
    handler key 是 MCP 类型类（如 CallToolRequest）而非字符串。
    """
    from mcp.types import CallToolRequest, CallToolRequestParams

    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=tool_name, arguments=args),
    )
    handler = server_instance.request_handlers.get(CallToolRequest)
    if handler is None:
        raise RuntimeError("CallToolRequest handler 未注册")
    server_result = await handler(request)
    # ServerResult.root 是 CallToolResult
    tool_result = server_result.root
    return {
        "content": [{"type": c.type, "text": c.text} for c in tool_result.content],
        **({"is_error": True} if tool_result.isError else {}),
    }
