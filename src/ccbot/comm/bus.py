"""MessageBus：Worker 间消息路由。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Awaitable, Callable

from ccbot.models.comm import CommMessage, MessageType


class MessageBusBackend(ABC):
    """消息总线抽象后端，Phase 2 可替换为 RedisBus。"""

    @abstractmethod
    async def send(self, msg: CommMessage) -> None: ...

    @abstractmethod
    async def receive(
        self, session_id: str, worker: str, since: float = 0.0
    ) -> list[CommMessage]: ...

    @abstractmethod
    async def get_history(self, session_id: str) -> list[CommMessage]: ...

    @abstractmethod
    async def create_session(self, session_id: str, worker_names: list[str]) -> None: ...

    @abstractmethod
    async def close_session(self, session_id: str) -> None: ...


class InMemoryBus(MessageBusBackend):
    """进程内 MessageBus 实现：asyncio dict + deque。"""

    def __init__(self) -> None:
        # session_id → worker_name → 消息队列
        self._inboxes: dict[str, dict[str, deque[CommMessage]]] = {}
        # session_id → 全部消息历史
        self._history: dict[str, list[CommMessage]] = {}
        # session_id → worker 名称列表
        self._workers: dict[str, list[str]] = {}
        # 上报回调
        self._on_report: Callable[[str, CommMessage], Awaitable[None]] | None = None
        self._lock = asyncio.Lock()

    def on_report(self, callback: Callable[[str, CommMessage], Awaitable[None]]) -> None:
        """注册上报/澄清回调。callback(worker_name, message)。"""
        self._on_report = callback

    async def create_session(self, session_id: str, worker_names: list[str]) -> None:
        async with self._lock:
            self._inboxes[session_id] = {name: deque() for name in worker_names}
            self._history[session_id] = []
            self._workers[session_id] = list(worker_names)

    async def close_session(self, session_id: str) -> None:
        async with self._lock:
            self._inboxes.pop(session_id, None)
            self._history.pop(session_id, None)
            self._workers.pop(session_id, None)

    async def send(self, msg: CommMessage) -> None:
        """发送消息，根据路由规则分发。"""
        report_callback = None

        async with self._lock:
            session = self._inboxes.get(msg.session_id)
            if session is None:
                return

            # 记录历史
            history = self._history.get(msg.session_id)
            if history is not None:
                history.append(msg)

            # 路由规则
            if msg.target == "supervisor" or msg.type in (MessageType.REPORT, MessageType.CLARIFY):
                # 上报给 Supervisor（回调在锁外执行，避免阻塞其他 send/receive）
                if self._on_report:
                    report_callback = (msg.source, msg)
            elif msg.target == "" or msg.type == MessageType.BROADCAST:
                # 广播给所有 Worker（除发送者）
                for name, inbox in session.items():
                    if name != msg.source:
                        inbox.append(msg)
            else:
                # 点对点
                inbox = session.get(msg.target)
                if inbox is not None:
                    inbox.append(msg)

        # 在锁外执行回调，避免慢 I/O 阻塞消息总线
        if report_callback and self._on_report:
            await self._on_report(*report_callback)

    async def receive(self, session_id: str, worker: str, since: float = 0.0) -> list[CommMessage]:
        """读取指定 Worker 的消息（自 since 时间戳后）。

        先过滤再移除：不匹配 since 的消息保留在 inbox 中，避免消息丢失。
        """
        async with self._lock:
            session = self._inboxes.get(session_id)
            if session is None:
                return []

            inbox = session.get(worker)
            if inbox is None:
                return []

            if since > 0:
                # 分离：匹配的取出，不匹配的保留
                matched = [m for m in inbox if m.timestamp > since]
                remaining = deque(m for m in inbox if m.timestamp <= since)
                inbox.clear()
                inbox.extend(remaining)
                return matched

            # since == 0：取出全部
            messages = list(inbox)
            inbox.clear()
            return messages

    async def get_history(self, session_id: str) -> list[CommMessage]:
        async with self._lock:
            return list(self._history.get(session_id, []))

    async def get_worker_names(self, session_id: str) -> list[str]:
        """获取 session 中的 Worker 名称列表。"""
        async with self._lock:
            return list(self._workers.get(session_id, []))
