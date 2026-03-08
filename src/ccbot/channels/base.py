"""通道基类定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger


class Channel(ABC):
    """消息通道抽象基类。

    所有通道适配器必须实现此接口。
    """

    def __init__(self) -> None:
        self._on_message_handler: (
            Callable[[str, str, str, Callable[[str], Awaitable[None]]], Awaitable[str]] | None
        ) = None
        self._running = False

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
        """发送消息到指定目标。

        Args:
            target: 目标标识（chat_id 或 open_id）
            content: 消息内容
            **kwargs: 额外参数
        """
        ...

    def on_message(
        self,
        handler: Callable[[str, str, str, Callable[[str], Awaitable[None]]], Awaitable[str]],
    ) -> None:
        """注册消息处理回调。

        Args:
            handler: 回调函数，签名 (content, reply_to, sender_id, progress_cb) -> reply
        """
        self._on_message_handler = handler

    async def _handle_message(
        self,
        content: str,
        reply_to: str,
        sender_id: str,
        progress_cb: Callable[[str], Awaitable[None]],
    ) -> str:
        """内部消息处理包装。"""
        if self._on_message_handler is None:
            logger.warning("收到消息但无处理器: chat_id={}", reply_to)
            return "服务暂时不可用"
        try:
            return await self._on_message_handler(content, reply_to, sender_id, progress_cb)
        except Exception as e:
            logger.exception("消息处理失败: {}", e)
            return f"处理消息时出错: {e}"
