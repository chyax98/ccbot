"""集成测试：Channel → AgentTeam 消息流。

验证：
- CLIChannel 消息经过 Channel 基类正确路由到 handler
- IncomingMessage 构建与传递
- on_progress / on_worker_result 回调链路
- Channel 错误处理（handler 异常时的兜底回复）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ccbot.channels.base import ChannelResponder, IncomingMessage
from ccbot.channels.cli import CLIChannel
from ccbot.config import AgentConfig
from ccbot.runtime.sdk_utils import AgentRunResult
from ccbot.team import AgentTeam
from ccbot.workspace import WorkspaceManager


class TestChannelToTeamFlow:
    """Channel 基类消息路由集成测试。"""

    @pytest.mark.asyncio
    async def test_cli_channel_routes_to_context_handler(self) -> None:
        """CLIChannel 消息应正确路由到 on_message_context handler。"""
        received_messages: list[IncomingMessage] = []

        async def handler(msg: IncomingMessage, progress_cb, result_sender):
            received_messages.append(msg)
            return "handled"

        channel = CLIChannel(single_message="测试消息")
        channel.on_message_context(handler)

        # 直接调用内部 _handle_message 验证路由
        reply = await channel._handle_message(
            "测试消息",
            "cli",
            "user",
            AsyncMock(),
            AsyncMock(),
        )

        assert reply == "handled"
        assert len(received_messages) == 1
        assert received_messages[0].text == "测试消息"
        assert received_messages[0].channel == "cli"
        assert received_messages[0].conversation_id == "cli"

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error_message(self) -> None:
        """Handler 异常时 Channel 应返回兜底错误消息。"""

        async def broken_handler(msg, progress_cb, result_sender):
            raise ValueError("handler crashed")

        channel = CLIChannel()
        channel.on_message_context(broken_handler)

        reply = await channel._handle_message(
            "trigger error",
            "cli",
            "user",
            AsyncMock(),
        )

        assert "出错" in reply or "error" in reply.lower()

    @pytest.mark.asyncio
    async def test_progress_callback_forwarded(self) -> None:
        """on_progress 回调应正确传递到 handler。"""
        progress_received: list[str] = []

        async def handler(msg, progress_cb, result_sender):
            await progress_cb("处理中...")
            return "done"

        async def track_progress(msg: str) -> None:
            progress_received.append(msg)

        channel = CLIChannel()
        channel.on_message_context(handler)

        await channel._handle_message(
            "test",
            "cli",
            "user",
            track_progress,
        )

        assert "处理中..." in progress_received

    @pytest.mark.asyncio
    async def test_channel_responder_integration(self) -> None:
        """ChannelResponder 应正确封装 Channel + IncomingMessage。"""
        sent_messages: list[tuple[str, str]] = []

        channel = CLIChannel()

        async def capture_send(target: str, content: str, **kwargs):
            sent_messages.append((target, content))

        channel.send = capture_send

        msg = IncomingMessage(
            text="hello",
            channel="cli",
            conversation_id="cli",
            reply_target="cli",
            sender_id="user",
        )

        responder = channel.build_responder(msg)
        assert isinstance(responder, ChannelResponder)
        assert responder.target == "cli"

        await responder.reply("回复内容")
        assert ("cli", "回复内容") in sent_messages

        await responder.worker_result("analyzer", "分析完成")
        assert len(sent_messages) == 2
        assert "analyzer" in sent_messages[1][1]


class TestEndToEndChannelTeam:
    """Channel → Team 端到端集成（mock SDK 层）。"""

    @pytest.mark.asyncio
    async def test_message_flows_through_channel_to_team(self, workspace: WorkspaceManager) -> None:
        """消息从 Channel 经 handler 到达 AgentTeam.ask 的完整流程。"""
        team = AgentTeam(AgentConfig(), workspace)

        # mock supervisor 以避免 SDK 调用
        team._supervisor.ask_run = AsyncMock(
            return_value=AgentRunResult("Team 回复"),
        )
        team._worker_pool = MagicMock()
        team._worker_pool.format_status = MagicMock(return_value="")

        # 注册 handler：模拟 cli.py 中的 on_message 注册
        team_reply_holder: list[str] = []

        async def message_handler(msg: IncomingMessage, progress_cb, result_sender):
            reply = await team.ask(
                msg.conversation_id,
                msg.text,
                on_progress=progress_cb,
            )
            team_reply_holder.append(reply)
            return reply

        channel = CLIChannel()
        channel.on_message_context(message_handler)

        # 通过 Channel 内部方法发送消息
        reply = await channel._handle_message(
            "你好 ccbot",
            "cli-chat-1",
            "user",
            AsyncMock(),
        )

        assert reply == "Team 回复"
        assert len(team_reply_holder) == 1
        # 验证 supervisor 被调用，且 prompt 中包含原始消息
        call_args = team._supervisor.ask_run.call_args
        assert "你好 ccbot" in call_args[0][1]  # enhanced_prompt
