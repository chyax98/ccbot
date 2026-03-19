"""Message deduplication with memory cache + JSON persistence.

Reference: OpenClaw extensions/feishu/src/dedup.ts
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from loguru import logger


class DedupCache:
    """Memory LRU cache + async JSON persistence.

    Features:
    - In-memory OrderedDict for fast lookup
    - Async JSON file persistence (OpenClaw style)
    - TTL-based expiration
    - Namespace support (different files per channel/account)

    Example:
        cache = DedupCache(ttl_ms=86400000, max_size=1000)
        if cache.check("msg_123"):
            print("Already processed")
        await cache.persist(".ccbot/dedup")
    """

    DEFAULT_TTL_MS = 24 * 60 * 60 * 1000  # 24 hours
    DEFAULT_MAX_SIZE = 1000

    def __init__(
        self,
        ttl_ms: int = DEFAULT_TTL_MS,
        max_size: int = DEFAULT_MAX_SIZE,
    ) -> None:
        self._cache: OrderedDict[str, float] = OrderedDict()
        self._ttl_ms = ttl_ms
        self._max_size = max_size
        self._persist_task: asyncio.Task[None] | None = None
        self._dirty = False
        # 记录最后一次 schedule_persist 的参数，供 stop() 时使用
        self._persist_base_path: str | Path | None = None
        self._persist_namespace: str = "global"

    def check(self, key: str) -> bool:
        """Check if key exists in cache. If not, add it.

        Args:
            key: Message ID or unique identifier

        Returns:
            True if key already exists (duplicate), False if new
        """
        now = time.time() * 1000  # milliseconds

        # Clean expired entries if at max size
        if len(self._cache) >= self._max_size:
            self._cleanup_expired(now)

        if key in self._cache:
            timestamp = self._cache[key]
            if self._ttl_ms <= 0 or now - timestamp < self._ttl_ms:
                # Valid entry - duplicate
                return True
            # Expired - remove and treat as new
            del self._cache[key]

        # New entry
        self._cache[key] = now
        self._dirty = True

        # Maintain max size (remove oldest)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

        return False

    def peek(self, key: str) -> bool:
        """Check if key exists without adding it.

        Args:
            key: Message ID or unique identifier

        Returns:
            True if key exists and not expired
        """
        if key not in self._cache:
            return False

        timestamp = self._cache[key]
        now = time.time() * 1000

        if self._ttl_ms <= 0 or now - timestamp < self._ttl_ms:
            return True

        # Expired
        del self._cache[key]
        return False

    def _cleanup_expired(self, now: float | None = None) -> int:
        """Remove expired entries.

        Args:
            now: Current timestamp in milliseconds

        Returns:
            Number of entries removed
        """
        if self._ttl_ms <= 0:
            return 0

        if now is None:
            now = time.time() * 1000

        expired = [key for key, timestamp in self._cache.items() if now - timestamp >= self._ttl_ms]

        for key in expired:
            del self._cache[key]

        return len(expired)

    async def persist(self, base_path: str | Path, namespace: str = "global") -> None:
        """Persist cache to JSON file asynchronously.

        Args:
            base_path: Directory to store dedup files
            namespace: Namespace for separate dedup files
        """
        if not self._dirty:
            return

        base_path = Path(base_path).expanduser()
        base_path.mkdir(parents=True, exist_ok=True)

        file_path = base_path / f"{namespace}.json"

        try:
            # Clean expired before saving
            self._cleanup_expired()

            data: dict[str, Any] = {
                "version": 1,
                "ttl_ms": self._ttl_ms,
                "entries": dict(self._cache),
            }

            # Write to temp file then rename for atomicity
            temp_file = file_path.with_suffix(".tmp")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: temp_file.write_text(json.dumps(data), encoding="utf-8")
            )
            await loop.run_in_executor(None, temp_file.rename, file_path)

            self._dirty = False
            logger.debug("Dedup cache persisted: {} ({} entries)", file_path, len(self._cache))

        except Exception as e:
            logger.warning("Failed to persist dedup cache: {}", e)

    async def load(self, base_path: str | Path, namespace: str = "global") -> int:
        """Load cache from JSON file.

        Args:
            base_path: Directory containing dedup files
            namespace: Namespace for separate dedup files

        Returns:
            Number of entries loaded
        """
        base_path = Path(base_path).expanduser()
        file_path = base_path / f"{namespace}.json"

        if not file_path.exists():
            return 0

        try:
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, lambda: file_path.read_text(encoding="utf-8"))
            data = json.loads(text)

            # Validate version
            if data.get("version") != 1:
                logger.warning("Unknown dedup file version: {}", data.get("version"))
                return 0

            entries = data.get("entries", {})
            now = time.time() * 1000

            loaded = 0
            for key, timestamp in entries.items():
                # Skip expired entries
                if self._ttl_ms > 0 and now - timestamp >= self._ttl_ms:
                    continue
                self._cache[key] = timestamp
                loaded += 1

            # Maintain max size
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

            logger.info("Dedup cache loaded: {} entries from {}", loaded, file_path)
            return loaded

        except Exception as e:
            logger.warning("Failed to load dedup cache: {}", e)
            return 0

    def schedule_persist(
        self,
        base_path: str | Path,
        namespace: str = "global",
        interval_sec: float = 30.0,
    ) -> None:
        """Schedule periodic persistence.

        Args:
            base_path: Directory to store dedup files
            namespace: Namespace for separate dedup files
            interval_sec: Persistence interval in seconds
        """
        if self._persist_task is not None:
            return

        # 记录路径以便 stop() 时最终持久化
        self._persist_base_path = base_path
        self._persist_namespace = namespace

        async def _persist_loop() -> None:
            while True:
                try:
                    await asyncio.sleep(interval_sec)
                    await self.persist(base_path, namespace)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("Dedup persist error: {}", e)

        self._persist_task = asyncio.create_task(_persist_loop())

    async def stop(self) -> None:
        """Stop scheduled persistence and flush cache."""
        if self._persist_task is not None:
            self._persist_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._persist_task
            self._persist_task = None

        # 使用记录的路径进行最终持久化
        if self._dirty and self._persist_base_path is not None:
            await self.persist(self._persist_base_path, self._persist_namespace)

    def __len__(self) -> int:
        """Return number of entries in cache."""
        return len(self._cache)
