"""SharedContext：Worker 间共享状态存储。"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod

from ccbot.models.comm import SharedEntry


class SharedContextBackend(ABC):
    """共享上下文抽象后端，Phase 2 可替换为 RedisContext。"""

    @abstractmethod
    async def set(self, session_id: str, key: str, value: str, author: str = "") -> None: ...

    @abstractmethod
    async def get(self, session_id: str, key: str) -> str | None: ...

    @abstractmethod
    async def list_keys(self, session_id: str) -> list[str]: ...

    @abstractmethod
    async def snapshot(self, session_id: str) -> str: ...

    @abstractmethod
    async def create_session(self, session_id: str) -> None: ...

    @abstractmethod
    async def close_session(self, session_id: str) -> None: ...


class InMemoryContext(SharedContextBackend):
    """进程内共享上下文实现：dict[session_id, dict[key, SharedEntry]]。"""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, SharedEntry]] = {}
        self._lock = asyncio.Lock()

    async def create_session(self, session_id: str) -> None:
        async with self._lock:
            self._store[session_id] = {}

    async def close_session(self, session_id: str) -> None:
        async with self._lock:
            self._store.pop(session_id, None)

    async def set(self, session_id: str, key: str, value: str, author: str = "") -> None:
        async with self._lock:
            store = self._store.get(session_id)
            if store is None:
                return

            existing = store.get(key)
            version = (existing.version + 1) if existing else 1
            store[key] = SharedEntry(
                key=key,
                value=value,
                author=author,
                updated_at=time.time(),
                version=version,
            )

    async def get(self, session_id: str, key: str) -> str | None:
        async with self._lock:
            store = self._store.get(session_id)
            if store is None:
                return None
            entry = store.get(key)
            return entry.value if entry else None

    async def list_keys(self, session_id: str) -> list[str]:
        async with self._lock:
            store = self._store.get(session_id)
            if store is None:
                return []
            return list(store.keys())

    async def snapshot(self, session_id: str) -> str:
        """生成当前 session 所有共享状态的文本快照。"""
        async with self._lock:
            store = self._store.get(session_id)
            if not store:
                return ""

            data = {
                key: {"value": entry.value, "author": entry.author, "version": entry.version}
                for key, entry in store.items()
            }
            return json.dumps(data, ensure_ascii=False, indent=2)
