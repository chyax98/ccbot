"""Feishu-specific outbound responder."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ccbot.channels.base import ChannelResponder, IncomingMessage
from ccbot.channels.feishu.file_service import upload_and_send_outputs
from ccbot.channels.feishu.renderer import send_file_message

if TYPE_CHECKING:
    from ccbot.channels.feishu.adapter import FeishuChannel


class FeishuResponder(ChannelResponder):
    """封装飞书 thread / reply / 文件回传语义。"""

    channel: FeishuChannel
    message: IncomingMessage

    @property
    def reply_message_id(self) -> str | None:
        return self.message.thread_id or self.message.message_id or None

    async def reply(self, content: str, **kwargs: Any) -> None:
        await self.channel.send(
            self.target,
            content,
            reply_to_message_id=self.reply_message_id,
            **kwargs,
        )

    async def progress(self, content: str) -> None:
        await self.channel.send(
            self.target,
            content,
            msg_type="progress",
            reply_to_message_id=self.reply_message_id,
            reply_in_thread=True,
        )

    async def worker_result(self, worker_name: str, result: str) -> None:
        prefix = "✅" if not result.startswith("❌") else ""
        await self.channel.send(
            self.target,
            f"**{prefix} [{worker_name}]**\n\n{result}",
            reply_to_message_id=self.reply_message_id,
            reply_in_thread=True,
        )

    async def error(self, content: str) -> None:
        await self.channel.send(
            self.target,
            content,
            msg_type="error",
            reply_to_message_id=self.reply_message_id,
        )

    async def upload_outputs_since(self, since: float) -> None:
        await upload_and_send_outputs(
            self.channel.client,
            self.channel.output_dir,
            self.target,
            self.reply_message_id,
            since,
            send_file_message,
        )
