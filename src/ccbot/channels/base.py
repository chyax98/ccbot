"""通道基类定义。"""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

from loguru import logger

ResultSender = Callable[[str, str], Awaitable[None]]
ProgressSender = Callable[[str], Awaitable[None]]

LegacyMessageHandler = Callable[[str, str, str, ProgressSender], Awaitable[str]]
MessageHandler = Callable[[str, str, str, ProgressSender, ResultSender | None], Awaitable[str]]


class ChannelCapability(StrEnum):
    """平台无关的消息能力。"""

    PROGRESS_UPDATES = "progress_updates"
    WORKER_RESULTS = "worker_results"
    THREAD_REPLIES = "thread_replies"
    FILE_OUTPUTS = "file_outputs"
    INTERACTIVE_CONFIRM = "interactive_confirm"
    RICH_TEXT = "rich_text"


@dataclass(slots=True, frozen=True)
class IncomingMessage:
    """标准化后的入站消息。"""

    text: str
    channel: str
    conversation_id: str
    reply_target: str
    sender_id: str
    message_id: str = ""
    thread_id: str = ""
    mentions_bot: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


ContextMessageHandler = Callable[
    [IncomingMessage, ProgressSender, ResultSender | None], Awaitable[str]
]


class Channel(ABC):
    """消息通道抽象基类。"""

    def __init__(self) -> None:
        self._on_message_handler: MessageHandler | LegacyMessageHandler | None = None
        self._on_message_context_handler: ContextMessageHandler | None = None
        self._handler_accepts_result_sender = True
        self._running = False

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """通道名。"""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[ChannelCapability]:
        """通道能力集合。"""
        ...

    @abstractmethod
    async def start(self) -> None:
        """启动通道。"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止通道。"""
        ...

    @abstractmethod
    async def send(self, target: str, content: str, **kwargs: Any) -> None:
        """发送消息到指定目标。"""
        ...

    async def wait_closed(self) -> None:
        """阻塞直到通道关闭。子类可用 asyncio.Event 覆盖。"""
        while self._running:
            await asyncio.sleep(1)

    def build_responder(self, message: IncomingMessage) -> ChannelResponder:
        """为入站消息创建出站 responder。"""
        return ChannelResponder(self, message)

    def on_message(self, handler: MessageHandler | LegacyMessageHandler) -> None:
        """注册兼容旧接口的消息回调。"""
        self._on_message_context_handler = None
        self._on_message_handler = handler
        self._handler_accepts_result_sender = _accepts_result_sender(handler)

    def on_message_context(self, handler: ContextMessageHandler) -> None:
        """注册标准化上下文消息回调。"""
        self._on_message_handler = None
        self._on_message_context_handler = handler

    async def _handle_message(
        self,
        content: str,
        reply_to: str,
        sender_id: str,
        progress_cb: ProgressSender,
        result_sender: ResultSender | None = None,
        *,
        message_id: str = "",
        thread_id: str = "",
        mentions_bot: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """内部消息处理包装。"""
        if self._on_message_context_handler is not None:
            message = IncomingMessage(
                text=content,
                channel=self.channel_name,
                conversation_id=reply_to,
                reply_target=reply_to,
                sender_id=sender_id,
                message_id=message_id,
                thread_id=thread_id,
                mentions_bot=mentions_bot,
                metadata=metadata or {},
            )
            try:
                return await self._on_message_context_handler(message, progress_cb, result_sender)
            except Exception as e:
                logger.exception("消息处理失败: {}", e)
                return f"处理消息时出错: {e}"

        if self._on_message_handler is None:
            logger.warning("收到消息但无处理器: chat_id={}", reply_to)
            return "服务暂时不可用"

        try:
            if self._handler_accepts_result_sender:
                message_handler = cast(MessageHandler, self._on_message_handler)
                return await message_handler(
                    content, reply_to, sender_id, progress_cb, result_sender
                )
            legacy_handler = cast(LegacyMessageHandler, self._on_message_handler)
            return await legacy_handler(content, reply_to, sender_id, progress_cb)
        except Exception as e:
            logger.exception("消息处理失败: {}", e)
            return f"处理消息时出错: {e}"


@dataclass(slots=True)
class ChannelResponder:
    """平台无关的出站消息封装。"""

    channel: Channel
    message: IncomingMessage

    @property
    def target(self) -> str:
        return self.message.reply_target

    async def reply(self, content: str, **kwargs: Any) -> None:
        await self.channel.send(self.target, content, **kwargs)

    async def progress(self, content: str) -> None:
        await self.reply(content)

    async def worker_result(self, worker_name: str, result: str) -> None:
        prefix = "✅" if not result.startswith("❌") else ""
        await self.reply(f"**{prefix} [{worker_name}]**\n\n{result}")

    async def error(self, content: str) -> None:
        await self.reply(content)

    async def upload_outputs_since(self, since: float) -> None:
        _ = since
        return None


def _accepts_result_sender(handler: MessageHandler | LegacyMessageHandler) -> bool:
    """判断 handler 是否支持第 5 个 result_sender 参数。"""
    try:
        params = inspect.signature(handler).parameters.values()
    except (TypeError, ValueError):
        return True

    positional = [
        param
        for param in params
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(param.kind == param.VAR_POSITIONAL for param in params)
    return has_varargs or len(positional) >= 5
