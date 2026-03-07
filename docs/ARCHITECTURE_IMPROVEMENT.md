# Nanobot 飞书架构改进方案

> [归档说明]
> 本文档已转为历史参考。最终架构决策请以 [ARCHITECTURE_FINAL_PLANS.md](ARCHITECTURE_FINAL_PLANS.md) 为准。

基于 [OpenClaw](https://github.com/openclaw/openclaw) 飞书实现的深度分析，本文档提供详细的架构改进建议。

---

## 目录

1. [现状分析](#现状分析)
2. [核心问题](#核心问题)
3. [参考架构](#参考架构)
4. [详细改进方案](#详细改进方案)
5. [代码重构计划](#代码重构计划)
6. [实施路线图](#实施路线图)

---

## 现状分析

### 当前架构图

```
┌─────────────────────────────────────┐
│           FeishuBot                 │
│  ┌─────────────────────────────┐   │
│  │   WebSocket Client (lark)   │   │
│  │   - _on_message_sync        │   │
│  │   - 直接调度到事件循环       │   │
│  └─────────────────────────────┘   │
│              │                      │
│  ┌───────────▼──────────────┐      │
│  │   Memory Deduplication   │      │
│  │   - OrderedDict (1000)   │      │
│  │   - 仅内存，无持久化      │      │
│  └──────────────────────────┘      │
│              │                      │
│  ┌───────────▼──────────────┐      │
│  │   Simple Permission      │      │
│  │   - allow_from list      │      │
│  │   - 无配对流程            │      │
│  └──────────────────────────┘      │
│              │                      │
│  ┌───────────▼──────────────┐      │
│  │   Agent Callback         │      │
│  │   - on_message_cb        │      │
│  └──────────────────────────┘      │
└─────────────────────────────────────┘
```

### 代码统计

| 文件 | 行数 | 职责 |
|-----|------|------|
| `feishu.py` | 929 | 全部功能（过于臃肿） |
| 去重实现 | ~20 行 | 仅内存 LRU |
| 队列实现 | ❌ 缺失 | 无消息保序 |
| 会话管理 | ~5 行 | 简单 chat_id |

---

## 核心问题

### 🔴 问题 1: 消息可靠性不足

**当前实现:**
```python
# 仅内存去重，重启即丢失
self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
while len(self._processed_message_ids) > 1000:
    self._processed_message_ids.popitem(last=False)
```

**风险:**
- 服务重启后重复处理消息
- 无法应对 WebSocket 重连期间的消息
- 1000 条限制可能导致消息漏判（高并发场景）

**参考 (OpenClaw):**
```typescript
// 内存 + 持久化双保险
if (!tryRecordMessage(memoryDedupeKey)) return;
if (!(await tryRecordMessagePersistent(messageId, accountId))) return;
```

---

### 🔴 问题 2: 消息顺序无保障

**当前实现:**
```python
def _on_message_sync(self, data: Any) -> None:
    # 直接从 WebSocket 线程调度到事件循环
    asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)
```

**风险:**
- 同一聊天内的多条消息并发处理
- 消息处理顺序与接收顺序不一致
- 竞态条件可能导致状态混乱

**参考 (OpenClaw):**
```typescript
// 每 chat 独立队列
function createChatQueue() {
  const queues = new Map<string, Promise<void>>();
  return (chatId: string, task: () => Promise<void>) => {
    const prev = queues.get(chatId) ?? Promise.resolve();
    const next = prev.then(task, task);  // 串行执行
    queues.set(chatId, next);
    return next;
  };
}
```

---

### 🟡 问题 3: 会话隔离过于简单

**当前实现:**
```python
reply_to = chat_id if chat_type == "group" else sender_id
```

**缺失功能:**
- 群内按用户隔离会话（`group_sender`）
- 话题（Thread）级别隔离（`group_topic`）
- 话题+用户组合隔离（`group_topic_sender`）

---

### 🟡 问题 4: 缺少消息防抖

**场景:** 用户快速发送多条消息（如复制粘贴长文本）

**当前:** 每条消息独立处理，导致 Agent 多次响应

**参考 (OpenClaw):**
```typescript
const inboundDebouncer = createInboundDebouncer({
  debounceMs: 300,
  shouldDebounce: (event) => {
    return !hasControlCommand(text, cfg);  // 控制命令不防抖
  },
  onFlush: async (entries) => {
    // 合并多条消息为一条
    const combinedText = entries.map(e => resolveDebounceText(e)).join("\n");
  },
});
```

---

### 🟡 问题 5: 权限系统不完善

**当前:** 仅简单白名单

**缺失:**
- 配对（Pairing）流程
- 群组级别白名单
- 发送者级别白名单

---

### 🟢 问题 6: 代码组织

**当前:** 单文件 929 行，职责混杂

**建议:** 模块化拆分

```
nanobot/channels/
├── base.py          # 抽象基类
├── feishu/
│   ├── __init__.py
│   ├── bot.py       # 主入口（精简）
│   ├── client.py    # SDK 封装
│   ├── dedup.py     # 去重（内存+持久化）
│   ├── queue.py     # 消息队列
│   ├── session.py   # 会话管理
│   ├── policy.py    # 权限策略
│   ├── send.py      # 消息发送
│   ├── media.py     # 媒体处理
│   └── card.py      # 卡片构建
```

---

## 参考架构

### OpenClaw 飞书架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     Gateway (OpenClaw)                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │               Channel Plugin (feishu)                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │   │
│  │  │   Config     │  │  Multi-      │  │   Pairing    │  │   │
│  │  │   Schema     │  │  Account     │  │   System     │  │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  ┌───────────────────────────▼────────────────────────────┐    │
│  │                   Monitor Layer                         │    │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────┐   │    │
│  │  │ WebSocket  │  │  Webhook   │  │ Event Dispatcher│   │    │
│  │  │ Transport  │  │  Transport │  │                │   │    │
│  │  └────────────┘  └────────────┘  └────────────────┘   │    │
│  └───────────────────────────┬────────────────────────────┘    │
│                              │                                   │
│  ┌───────────────────────────▼────────────────────────────┐    │
│  │                   Processing Pipeline                   │    │
│  │  ┌──────────┐ → ┌──────────┐ → ┌──────────┐ → ┌─────┐ │    │
│  │  │  Dedup   │ → │ Debounce │ → │  Queue   │ → │ Bot │ │    │
│  │  │ (Memory+ │   │ (Merge   │   │ (Per-    │   │     │ │    │
│  │  │  Disk)   │   │  Burst)  │   │  Chat)   │   │     │ │    │
│  │  └──────────┘   └──────────┘   └──────────┘   └─────┘ │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 详细改进方案

### 1. 消息去重系统

#### 目标
- 内存缓存（热数据）+ SQLite（持久化）
- 自动过期清理
- 异步写入不阻塞主流程

#### 实现

```python
# nanobot/channels/feishu/dedup.py

from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Protocol
import time


class DeduplicationStore(Protocol):
    """去重存储接口"""
    
    async def is_processed(self, message_id: str) -> bool:
        ...
    
    async def mark_processed(self, message_id: str, ttl: int = 604800) -> None:
        """标记已处理，ttl 为过期时间（秒，默认7天）"""
        ...


class SQLiteDeduplicationStore:
    """SQLite 持久化去重存储"""
    
    def __init__(self, db_path: Path, cache_size: int = 10000):
        self.db_path = db_path
        self._cache: set[str] = set()  # 内存热缓存
        self._cache_size = cache_size
        self._initialized = False
    
    async def initialize(self) -> None:
        """初始化数据库表"""
        if self._initialized:
            return
            
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    id TEXT PRIMARY KEY,
                    created_at INTEGER DEFAULT (strftime('%s', 'now')),
                    expired_at INTEGER
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_expired 
                ON processed_messages(expired_at)
            """)
            await db.commit()
        
        self._initialized = True
    
    async def is_processed(self, message_id: str) -> bool:
        # 1. 检查内存缓存
        if message_id in self._cache:
            return True
        
        # 2. 查询数据库
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM processed_messages WHERE id = ? AND expired_at > ?",
                (message_id, int(time.time()))
            ) as cursor:
                exists = await cursor.fetchone() is not None
                if exists:
                    self._cache.add(message_id)
                return exists
    
    async def mark_processed(self, message_id: str, ttl: int = 604800) -> None:
        # 添加到内存缓存
        self._cache.add(message_id)
        
        # 缓存过大时清理
        if len(self._cache) > self._cache_size:
            self._cache = set(list(self._cache)[-self._cache_size//2:])
        
        # 异步写入数据库
        expired_at = int(time.time()) + ttl
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO processed_messages (id, expired_at)
                   VALUES (?, ?)""",
                (message_id, expired_at)
            )
            await db.commit()
    
    async def cleanup_expired(self) -> int:
        """清理过期记录，返回清理数量"""
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM processed_messages WHERE expired_at <= ?",
                (now,)
            )
            await db.commit()
            return cursor.rowcount


class MemoryDeduplicationStore:
    """纯内存去重（测试/低可靠性场景）"""
    
    def __init__(self, max_size: int = 10000):
        self._processed: set[str] = set()
        self._max_size = max_size
    
    async def is_processed(self, message_id: str) -> bool:
        return message_id in self._processed
    
    async def mark_processed(self, message_id: str, ttl: int = 604800) -> None:
        self._processed.add(message_id)
        if len(self._processed) > self._max_size:
            # 随机清理一半
            import random
            self._processed = set(random.sample(list(self._processed), self._max_size // 2))
```

---

### 2. 消息队列与保序

#### 目标
- 每聊天（chat_id）独立队列
- 串行处理，保证消息顺序
- 支持优雅关闭

#### 实现

```python
# nanobot/channels/feishu/queue.py

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Awaitable
from loguru import logger


@dataclass
class QueuedTask:
    """队列任务"""
    handler: Callable[[], Awaitable[None]]
    future: asyncio.Future


class PerChatMessageQueue:
    """
    每聊天独立的串行消息队列
    
    保证同一 chat 内的消息按接收顺序处理，
    不同 chat 之间并行处理。
    """
    
    def __init__(self, max_concurrent_chats: int = 1000):
        self._queues: dict[str, asyncio.Queue[QueuedTask]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._max_concurrent = max_concurrent_chats
        self._lock = asyncio.Lock()
    
    async def enqueue(
        self, 
        chat_id: str, 
        handler: Callable[[], Awaitable[None]]
    ) -> asyncio.Future:
        """
        将任务加入指定 chat 的队列
        
        Returns:
            Future: 可用于等待任务完成
        """
        async with self._lock:
            if chat_id not in self._queues:
                # 限制并发 chat 数量
                if len(self._queues) >= self._max_concurrent:
                    await self._cleanup_idle_queues()
                
                self._queues[chat_id] = asyncio.Queue()
                self._workers[chat_id] = asyncio.create_task(
                    self._worker_loop(chat_id),
                    name=f"feishu-queue-{chat_id[:20]}"
                )
        
        future = asyncio.get_event_loop().create_future()
        await self._queues[chat_id].put(QueuedTask(handler, future))
        return future
    
    async def _worker_loop(self, chat_id: str) -> None:
        """队列工作循环"""
        queue = self._queues[chat_id]
        
        try:
            while True:
                task = await queue.get()
                try:
                    await task.handler()
                    task.future.set_result(None)
                except Exception as e:
                    task.future.set_exception(e)
                    logger.error(f"处理消息失败 [chat={chat_id}]: {e}")
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            logger.debug(f"队列工作器取消 [chat={chat_id}]")
            raise
    
    async def _cleanup_idle_queues(self) -> None:
        """清理空闲队列"""
        # 简单策略：取消空队列
        empty_chats = [
            cid for cid, q in self._queues.items() 
            if q.empty() and cid in self._workers
        ]
        for cid in empty_chats[:10]:  # 每次最多清理 10 个
            self._workers[cid].cancel()
            try:
                await self._workers[cid]
            except asyncio.CancelledError:
                pass
            del self._queues[cid]
            del self._workers[cid]
    
    async def close(self, timeout: float = 30.0) -> None:
        """优雅关闭所有队列"""
        logger.info(f"正在关闭 {len(self._workers)} 个消息队列...")
        
        # 等待所有队列处理完成
        await asyncio.gather(
            *[q.join() for q in self._queues.values()],
            return_exceptions=True
        )
        
        # 取消所有工作器
        for task in self._workers.values():
            task.cancel()
        
        await asyncio.gather(
            *self._workers.values(),
            return_exceptions=True
        )
        
        self._queues.clear()
        self._workers.clear()
        logger.info("消息队列已关闭")


class PriorityMessageQueue(PerChatMessageQueue):
    """支持优先级的消息队列（控制命令优先）"""
    
    async def enqueue_with_priority(
        self,
        chat_id: str,
        handler: Callable[[], Awaitable[None]],
        priority: int = 0  # 数字越小优先级越高
    ) -> asyncio.Future:
        """高优先级任务可插队到队列头部"""
        # 实现略，可使用 PriorityQueue
        pass
```

---

### 3. 会话管理

#### 目标
- 支持多种会话隔离策略
- 配置化切换
- 稳定的话题 ID 生成

#### 实现

```python
# nanobot/channels/feishu/session.py

from __future__ import annotations

from enum import Enum, auto
from dataclasses import dataclass
from typing import Protocol


class SessionScope(str, Enum):
    """会话隔离范围"""
    GROUP = "group"                          # 整群共享会话
    GROUP_SENDER = "group_sender"            # 群内按用户隔离
    GROUP_TOPIC = "group_topic"              # 按话题隔离
    GROUP_TOPIC_SENDER = "group_topic_sender"  # 话题+用户组合


@dataclass(frozen=True)
class SessionKey:
    """会话标识（不可变，可作为 dict key）"""
    key: str
    parent_key: str | None = None  # 用于话题回复时知道父群组
    
    def __str__(self) -> str:
        return self.key
    
    def __hash__(self) -> int:
        return hash(self.key)


class SessionResolver(Protocol):
    """会话解析器接口"""
    
    def resolve(
        self,
        chat_id: str,
        sender_id: str,
        root_id: str | None,
        thread_id: str | None,
    ) -> SessionKey:
        """解析会话标识"""
        ...
    
    def is_reply_in_thread(self) -> bool:
        """是否应该在话题内回复"""
        ...


class FeishuSessionResolver:
    """飞书会话解析器"""
    
    def __init__(
        self,
        scope: SessionScope = SessionScope.GROUP,
        reply_in_thread: bool = False,
    ):
        self.scope = scope
        self._reply_in_thread = reply_in_thread
    
    def resolve(
        self,
        chat_id: str,
        sender_id: str,
        root_id: str | None,
        thread_id: str | None,
    ) -> SessionKey:
        """
        解析会话标识
        
        话题 ID 优先级: root_id > thread_id
        （第一条消息只有 message_id，回复后才有 root_id/thread_id）
        """
        topic_id = root_id or thread_id
        
        match self.scope:
            case SessionScope.GROUP:
                return SessionKey(key=chat_id)
            
            case SessionScope.GROUP_SENDER:
                return SessionKey(
                    key=f"{chat_id}:sender:{sender_id}"
                )
            
            case SessionScope.GROUP_TOPIC:
                if topic_id:
                    return SessionKey(
                        key=f"{chat_id}:topic:{topic_id}",
                        parent_key=chat_id
                    )
                return SessionKey(key=chat_id)
            
            case SessionScope.GROUP_TOPIC_SENDER:
                if topic_id:
                    return SessionKey(
                        key=f"{chat_id}:topic:{topic_id}:sender:{sender_id}",
                        parent_key=f"{chat_id}:sender:{sender_id}"
                    )
                return SessionKey(
                    key=f"{chat_id}:sender:{sender_id}"
                )
    
    def is_reply_in_thread(self) -> bool:
        return self._reply_in_thread


class SessionManager:
    """会话管理器"""
    
    def __init__(self, resolver: SessionResolver):
        self._resolver = resolver
        self._sessions: dict[SessionKey, dict] = {}
    
    def get_or_create(self, key: SessionKey) -> dict:
        """获取或创建会话上下文"""
        if key not in self._sessions:
            self._sessions[key] = {
                "created_at": __import__('time').time(),
                "message_count": 0,
            }
        self._sessions[key]["message_count"] += 1
        return self._sessions[key]
    
    def get_session_stats(self) -> dict:
        """获取会话统计"""
        return {
            "total_sessions": len(self._sessions),
            "sessions_by_scope": {
                str(k): v["message_count"] 
                for k, v in self._sessions.items()
            }
        }
```

---

### 4. 消息防抖

#### 目标
- 合并短时间内的连续消息
- 控制命令不防抖
- 保留 @mention 信息

#### 实现

```python
# nanobot/channels/feishu/debounce.py

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Awaitable, TypeVar
from collections import defaultdict
from loguru import logger

T = TypeVar("T")


@dataclass
class DebounceEntry:
    """防抖条目"""
    data: T
    timestamp: float = field(default_factory=lambda: asyncio.get_event_loop().time())


class MessageDebouncer:
    """
    消息防抖器
    
    将短时间内的连续消息合并为一条处理，
    适用于用户快速发送多条消息的场景。
    """
    
    def __init__(
        self,
        debounce_ms: float = 300,
        max_wait_ms: float = 1000,
        key_extractor: Callable[[T], str] | None = None,
        should_debounce: Callable[[T], bool] | None = None,
    ):
        """
        Args:
            debounce_ms: 防抖等待时间
            max_wait_ms: 最大等待时间（超过立即触发）
            key_extractor: 提取防抖键的函数
            should_debounce: 判断是否应防抖的函数
        """
        self._debounce_ms = debounce_ms / 1000
        self._max_wait_ms = max_wait_ms / 1000
        self._key_extractor = key_extractor or (lambda x: str(x))
        self._should_debounce = should_debounce or (lambda x: True)
        
        self._buffers: dict[str, list[DebounceEntry]] = defaultdict(list)
        self._timers: dict[str, asyncio.Task] = {}
        self._flush_handlers: list[Callable[[list[T]], Awaitable[None]]] = []
    
    def on_flush(self, handler: Callable[[list[T]], Awaitable[None]]) -> None:
        """注册刷新处理器"""
        self._flush_handlers.append(handler)
    
    async def enqueue(self, item: T) -> None:
        """添加条目到防抖队列"""
        # 不应防抖的直接触发
        if not self._should_debounce(item):
            await self._flush_immediately([item])
            return
        
        key = self._key_extractor(item)
        entry = DebounceEntry(data=item)
        
        self._buffers[key].append(entry)
        
        # 重置定时器
        if key in self._timers:
            self._timers[key].cancel()
        
        self._timers[key] = asyncio.create_task(
            self._debounce_timer(key)
        )
    
    async def _debounce_timer(self, key: str) -> None:
        """防抖定时器"""
        try:
            first_entry_time = self._buffers[key][0].timestamp
            
            # 等待 debounce_ms 或 max_wait_ms（以先到为准）
            elapsed = asyncio.get_event_loop().time() - first_entry_time
            wait_time = min(self._debounce_ms, self._max_wait_ms - elapsed)
            
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            
            await self._flush_key(key)
        except asyncio.CancelledError:
            pass
    
    async def _flush_key(self, key: str) -> None:
        """刷新指定键的缓冲"""
        if key not in self._buffers:
            return
        
        entries = self._buffers.pop(key, [])
        self._timers.pop(key, None)
        
        if entries:
            items = [e.data for e in entries]
            await self._flush_immediately(items)
    
    async def _flush_immediately(self, items: list[T]) -> None:
        """立即刷新"""
        for handler in self._flush_handlers:
            try:
                await handler(items)
            except Exception as e:
                logger.error(f"防抖处理器错误: {e}")
    
    async def close(self) -> None:
        """关闭防抖器，刷新所有缓冲"""
        # 取消所有定时器
        for task in self._timers.values():
            task.cancel()
        
        # 刷新所有缓冲
        await asyncio.gather(*[
            self._flush_key(key) for key in list(self._buffers.keys())
        ], return_exceptions=True)


# 飞书特定的防抖键提取
def extract_feishu_debounce_key(event: dict) -> str:
    """
    提取飞书消息的防抖键
    
    格式: feishu:{account_id}:{chat_id}:{thread_key}:{sender_id}
    """
    message = event.get("message", {})
    sender = event.get("sender", {})
    
    chat_id = message.get("chat_id", "unknown")
    sender_id = sender.get("sender_id", {}).get("open_id", "unknown")
    root_id = message.get("root_id")
    
    thread_key = f"thread:{root_id}" if root_id else "chat"
    
    return f"feishu:{chat_id}:{thread_key}:{sender_id}"


def should_debounce_feishu_message(event: dict) -> bool:
    """判断飞书消息是否应该防抖"""
    # 只防抖文本消息
    msg_type = event.get("message", {}).get("message_type")
    if msg_type != "text":
        return False
    
    # 控制命令不防抖
    content = event.get("message", {}).get("content", "")
    try:
        import json
        text = json.loads(content).get("text", "")
        if text.strip().startswith(("/", "!", "#")):
            return False
    except:
        pass
    
    return True
```

---

### 5. 权限系统

#### 实现

```python
# nanobot/channels/feishu/policy.py

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from typing import Callable
from loguru import logger


class DMPolicy(Enum):
    """私聊策略"""
    OPEN = "open"           # 允许所有人
    PAIRING = "pairing"     # 需要配对
    ALLOWLIST = "allowlist" # 仅白名单
    DISABLED = "disabled"   # 禁用


class GroupPolicy(Enum):
    """群聊策略"""
    OPEN = "open"           # 允许所有群
    ALLOWLIST = "allowlist" # 仅白名单群组
    DISABLED = "disabled"   # 禁用群聊


@dataclass
class PolicyConfig:
    """权限配置"""
    dm_policy: DMPolicy = DMPolicy.PAIRING
    group_policy: GroupPolicy = GroupPolicy.OPEN
    
    # 白名单
    allow_from: list[str] = None           # 用户白名单
    group_allow_from: list[str] = None     # 群组白名单
    
    # 群组特定配置
    require_mention: bool = True           # 群聊是否需要 @bot
    groups: dict[str, GroupConfig] = None  # 群组级别配置


@dataclass
class GroupConfig:
    """群组级别配置"""
    enabled: bool = True
    require_mention: bool | None = None    # 覆盖全局设置
    allow_from: list[str] | None = None    # 群组内发送者白名单


class PairingManager:
    """配对管理器"""
    
    def __init__(self, storage_path: str = "~/.nanobot/pairing.json"):
        self._storage_path = storage_path
        self._pending: dict[str, str] = {}    # code -> user_id
        self._approved: set[str] = set()      # 已批准的用户
        self._load()
    
    def _load(self) -> None:
        """从磁盘加载"""
        import json
        from pathlib import Path
        
        path = Path(self._storage_path).expanduser()
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                self._approved = set(data.get("approved", []))
    
    def _save(self) -> None:
        """保存到磁盘"""
        import json
        from pathlib import Path
        
        path = Path(self._storage_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w") as f:
            json.dump({"approved": list(self._approved)}, f)
    
    def generate_code(self, user_id: str) -> str:
        """生成配对码"""
        import secrets
        code = secrets.token_hex(4).upper()  # 8位大写
        self._pending[code] = user_id
        return code
    
    def approve(self, code: str) -> str | None:
        """批准配对码，返回 user_id"""
        user_id = self._pending.pop(code, None)
        if user_id:
            self._approved.add(user_id)
            self._save()
            return user_id
        return None
    
    def is_approved(self, user_id: str) -> bool:
        """检查用户是否已批准"""
        return user_id in self._approved or "*" in self._approved


class PolicyChecker:
    """权限检查器"""
    
    def __init__(self, config: PolicyConfig, pairing: PairingManager):
        self.config = config
        self.pairing = pairing
    
    def check_dm(self, user_id: str) -> tuple[bool, str | None]:
        """
        检查私聊权限
        
        Returns:
            (allowed, pairing_code)
            pairing_code 仅在需要配对时返回
        """
        policy = self.config.dm_policy
        
        if policy == DMPolicy.DISABLED:
            return False, None
        
        if policy == DMPolicy.OPEN:
            return True, None
        
        if policy == DMPolicy.ALLOWLIST:
            allowed = user_id in (self.config.allow_from or [])
            return allowed, None
        
        if policy == DMPolicy.PAIRING:
            if self.pairing.is_approved(user_id):
                return True, None
            code = self.pairing.generate_code(user_id)
            return False, code
        
        return False, None
    
    def check_group(
        self, 
        chat_id: str, 
        sender_id: str,
        is_mentioned: bool
    ) -> tuple[bool, str]:
        """
        检查群聊权限
        
        Returns:
            (allowed, reason)
        """
        policy = self.config.group_policy
        
        if policy == GroupPolicy.DISABLED:
            return False, "群聊已禁用"
        
        if policy == GroupPolicy.ALLOWLIST:
            if chat_id not in (self.config.group_allow_from or []):
                return False, "群组不在白名单"
        
        # 群组特定配置
        group_cfg = (self.config.groups or {}).get(chat_id)
        if group_cfg and not group_cfg.enabled:
            return False, "该群组已禁用"
        
        require_mention = group_cfg.require_mention if (
            group_cfg and group_cfg.require_mention is not None
        ) else self.config.require_mention
        
        if require_mention and not is_mentioned:
            return False, "需要 @bot"
        
        # 群组内发送者白名单
        group_allow = group_cfg.allow_from if group_cfg else None
        if group_allow and sender_id not in group_allow:
            return False, "发送者不在群组白名单"
        
        # 全局白名单
        if self.config.allow_from and "*" not in self.config.allow_from:
            if sender_id not in self.config.allow_from:
                return False, "发送者不在白名单"
        
        return True, ""
```

---

## 代码重构计划

### 目录结构

```
nanobot/
├── nanobot/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py              # CLI 入口（精简）
│   ├── agent.py            # NanobotAgent（保持不变）
│   ├── team.py             # AgentTeam（保持不变）
│   ├── config.py           # 配置（添加新字段）
│   ├── server.py           # A2A 服务器（保持不变）
│   ├── heartbeat.py        # 心跳服务（保持不变）
│   ├── workspace.py        # 工作空间（保持不变）
│   └── channels/           # 新增：消息通道
│       ├── __init__.py
│       ├── base.py         # 抽象基类
│       └── feishu/         # 飞书实现
│           ├── __init__.py
│           ├── bot.py      # 主入口（< 200 行）
│           ├── client.py   # SDK 客户端封装
│           ├── dedup.py    # 去重（内存+SQLite）
│           ├── queue.py    # 消息队列
│           ├── session.py  # 会话管理
│           ├── policy.py   # 权限策略
│           ├── debounce.py # 消息防抖
│           ├── send.py     # 消息发送
│           ├── media.py    # 媒体处理
│           ├── card.py     # 卡片构建
│           └── parser.py   # 消息解析
├── tests/
│   └── channels/
│       └── feishu/
│           ├── test_dedup.py
│           ├── test_queue.py
│           ├── test_session.py
│           └── test_policy.py
└── docs/
    └── ARCHITECTURE_IMPROVEMENT.md  # 本文档
```

### 重构后的 FeishuBot（精简版）

```python
# nanobot/channels/feishu/bot.py

from __future__ import annotations

import asyncio
from typing import Callable
from loguru import logger

from nanobot.channels.feishu.client import FeishuClient
from nanobot.channels.feishu.dedup import SQLiteDeduplicationStore
from nanobot.channels.feishu.queue import PerChatMessageQueue
from nanobot.channels.feishu.session import FeishuSessionResolver, SessionScope
from nanobot.channels.feishu.debounce import MessageDebouncer, extract_feishu_debounce_key
from nanobot.channels.feishu.policy import PolicyChecker, PolicyConfig, PairingManager
from nanobot.config import FeishuConfig


MessageHandler = Callable[[str, str, str, Callable], asyncio.Future[str]]


class FeishuBot:
    """
    改进后的飞书 Bot
    
    特性：
    - 持久化消息去重
    - 每聊天串行队列
    - 消息防抖
    - 可配置会话隔离
    - 完整权限系统
    """
    
    def __init__(
        self,
        config: FeishuConfig,
        handler: MessageHandler,
        data_dir: str = "~/.nanobot",
    ):
        self.config = config
        self.handler = handler
        self.data_dir = data_dir
        
        # 子组件（延迟初始化）
        self._client: FeishuClient | None = None
        self._dedup: SQLiteDeduplicationStore | None = None
        self._queue: PerChatMessageQueue | None = None
        self._debouncer: MessageDebouncer | None = None
        self._policy: PolicyChecker | None = None
        
        self._running = False
    
    async def start(self) -> None:
        """启动 Bot"""
        from pathlib import Path
        
        data_path = Path(self.data_dir).expanduser()
        data_path.mkdir(parents=True, exist_ok=True)
        
        # 初始化组件
        self._dedup = SQLiteDeduplicationStore(data_path / "dedup.db")
        await self._dedup.initialize()
        
        self._queue = PerChatMessageQueue()
        
        self._debouncer = MessageDebouncer(
            debounce_ms=300,
            key_extractor=extract_feishu_debounce_key,
        )
        self._debouncer.on_flush(self._on_debounced_messages)
        
        pairing = PairingManager(str(data_path / "pairing.json"))
        policy_config = PolicyConfig(
            dm_policy=self.config.dm_policy,
            group_policy=self.config.group_policy,
            allow_from=self.config.allow_from,
            require_mention=self.config.require_mention,
        )
        self._policy = PolicyChecker(policy_config, pairing)
        
        # 启动客户端
        self._client = FeishuClient(
            app_id=self.config.app_id,
            app_secret=self.config.app_secret,
            encrypt_key=self.config.encrypt_key,
            verification_token=self.config.verification_token,
            on_message=self._on_raw_message,
        )
        
        self._running = True
        await self._client.start()
    
    async def stop(self) -> None:
        """停止 Bot"""
        self._running = False
        
        if self._debouncer:
            await self._debouncer.close()
        if self._queue:
            await self._queue.close()
        if self._client:
            await self._client.stop()
        
        logger.info("FeishuBot 已停止")
    
    async def _on_raw_message(self, event: dict) -> None:
        """原始消息入口"""
        message = event.get("message", {})
        message_id = message.get("message_id")
        
        # 1. 去重检查
        if await self._dedup.is_processed(message_id):
            return
        await self._dedup.mark_processed(message_id)
        
        # 2. 进入防抖队列
        await self._debouncer.enqueue(event)
    
    async def _on_debounced_messages(self, events: list[dict]) -> None:
        """防抖后的消息"""
        for event in events:
            await self._process_single_message(event)
    
    async def _process_single_message(self, event: dict) -> None:
        """处理单条消息"""
        message = event.get("message", {})
        sender = event.get("sender", {})
        
        chat_id = message.get("chat_id")
        sender_id = sender.get("sender_id", {}).get("open_id", "unknown")
        chat_type = message.get("chat_type")
        
        # 权限检查
        if chat_type == "p2p":
            allowed, code = self._policy.check_dm(sender_id)
            if not allowed:
                if code:
                    await self._client.send_text(
                        sender_id, 
                        f"请使用配对码批准: {code}"
                    )
                return
        else:
            is_mentioned = self._is_mentioned(message)
            allowed, reason = self._policy.check_group(
                chat_id, sender_id, is_mentioned
            )
            if not allowed:
                logger.debug(f"群聊消息被拒绝: {reason}")
                return
        
        # 进入队列保序处理
        await self._queue.enqueue(chat_id, lambda: self._handle_message(event))
    
    async def _handle_message(self, event: dict) -> None:
        """实际处理消息（已保序）"""
        # ... 解析内容、调用 handler、发送回复 ...
        pass
    
    def _is_mentioned(self, message: dict) -> bool:
        """检查是否被 @"""
        mentions = message.get("mentions", [])
        for m in mentions:
            if m.get("id", {}).get("open_id") == self._client.bot_open_id:
                return True
            if m.get("id", {}).get("user_id") == "all":
                return True
        return False
```

---

## 实施路线图

### Phase 1: 基础设施（1-2 天）

1. **创建目录结构**
   ```bash
   mkdir -p nanobot/channels/feishu
   mkdir -p tests/channels/feishu
   mkdir -p docs
   ```

2. **实现去重模块** (`dedup.py`)
   - SQLiteDeduplicationStore
   - 单元测试

3. **实现队列模块** (`queue.py`)
   - PerChatMessageQueue
   - 单元测试

### Phase 2: 核心功能（2-3 天）

4. **实现会话模块** (`session.py`)
   - SessionResolver
   - 多种隔离策略

5. **实现防抖模块** (`debounce.py`)
   - MessageDebouncer
   - 飞书特定的提取函数

6. **实现权限模块** (`policy.py`)
   - PairingManager
   - PolicyChecker

### Phase 3: 整合（2 天）

7. **重构 FeishuClient** (`client.py`)
   - 封装 lark-oapi
   - 重连机制

8. **实现新的 FeishuBot** (`bot.py`)
   - 整合所有模块
   - 保持向后兼容的 API

9. **更新配置** (`config.py`)
   - 添加新配置项

### Phase 4: 测试与迁移（1-2 天）

10. **编写集成测试**
    - 消息流程测试
    - 并发测试

11. **逐步替换**
    - 保留旧的 `feishu.py` 作为兼容层
    - 新代码通过 feature flag 启用

12. **文档更新**
    - README 更新
    - 配置文档

---

## 配置示例

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "encrypt_key": "",
    "verification_token": "",
    
    "dm_policy": "pairing",
    "group_policy": "open",
    "require_mention": true,
    "allow_from": ["*"],
    
    "session": {
      "scope": "group_topic",
      "reply_in_thread": true
    },
    
    "debounce": {
      "enabled": true,
      "debounce_ms": 300,
      "max_wait_ms": 1000
    },
    
    "dedup": {
      "cache_size": 10000,
      "ttl_days": 7
    }
  }
}
```

---

## 参考资源

- [OpenClaw 飞书实现](https://github.com/openclaw/openclaw/tree/main/extensions/feishu)
- [lark-oapi Python SDK](https://github.com/larksuite/oapi-sdk-python)
- [飞书开放平台文档](https://open.feishu.cn/document/home/index)

---

**最后更新:** 2026-03-08
