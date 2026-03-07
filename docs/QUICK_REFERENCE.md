# Nanobot 飞书架构快速参考

> [归档说明]
> 本文档已转为历史参考。最终架构决策请以 [ARCHITECTURE_FINAL_PLANS.md](ARCHITECTURE_FINAL_PLANS.md) 为准。

本文档提供架构改进的简明要点和代码片段。

---

## 核心改进点速查

| 问题 | 现状 | 改进方案 | 参考文件 |
|-----|------|---------|---------|
| 消息去重 | 内存 LRU (1000) | SQLite + 内存缓存 | `dedup.py` |
| 消息保序 | 无队列，并发处理 | 每 chat 独立 asyncio.Queue | `queue.py` |
| 会话隔离 | 简单 chat_id | 4 种策略可选 | `session.py` |
| 消息防抖 | 无 | Debouncer 合并连续消息 | `debounce.py` |
| 权限控制 | 简单白名单 | Pairing + 多级白名单 | `policy.py` |

---

## 关键代码片段

### 1. 持久化去重（SQLite）

```python
import aiosqlite

class SQLiteDeduplicationStore:
    def __init__(self, db_path: Path):
        self._cache: set[str] = set()
        self.db_path = db_path
    
    async def is_processed(self, message_id: str) -> bool:
        if message_id in self._cache:
            return True
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM processed_messages WHERE id = ?",
                (message_id,)
            ) as cur:
                exists = await cur.fetchone() is not None
                if exists:
                    self._cache.add(message_id)
                return exists
    
    async def mark_processed(self, message_id: str, ttl: int = 604800):
        self._cache.add(message_id)
        expired_at = int(time.time()) + ttl
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO processed_messages (id, expired_at) VALUES (?, ?)",
                (message_id, expired_at)
            )
            await db.commit()
```

### 2. 每聊天串行队列

```python
class PerChatMessageQueue:
    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
    
    async def enqueue(self, chat_id: str, handler: Callable):
        if chat_id not in self._queues:
            self._queues[chat_id] = asyncio.Queue()
            self._workers[chat_id] = asyncio.create_task(
                self._worker_loop(chat_id)
            )
        
        future = asyncio.get_event_loop().create_future()
        await self._queues[chat_id].put((handler, future))
        return future
    
    async def _worker_loop(self, chat_id: str):
        """串行处理该 chat 的所有消息"""
        queue = self._queues[chat_id]
        while True:
            handler, future = await queue.get()
            try:
                result = await handler()
                future.set_result(result)
            except Exception as e:
                future.set_exception(e)
            finally:
                queue.task_done()
```

### 3. 会话隔离策略

```python
from enum import Enum

class SessionScope(str, Enum):
    GROUP = "group"                          # 整群共享
    GROUP_SENDER = "group_sender"            # 群内按用户
    GROUP_TOPIC = "group_topic"              # 按话题
    GROUP_TOPIC_SENDER = "group_topic_sender"  # 话题+用户

def resolve_session(
    scope: SessionScope,
    chat_id: str,
    sender_id: str,
    root_id: str | None,
    thread_id: str | None,
) -> str:
    topic_id = root_id or thread_id
    
    match scope:
        case SessionScope.GROUP:
            return chat_id
        case SessionScope.GROUP_SENDER:
            return f"{chat_id}:sender:{sender_id}"
        case SessionScope.GROUP_TOPIC:
            return f"{chat_id}:topic:{topic_id}" if topic_id else chat_id
        case SessionScope.GROUP_TOPIC_SENDER:
            if topic_id:
                return f"{chat_id}:topic:{topic_id}:sender:{sender_id}"
            return f"{chat_id}:sender:{sender_id}"
```

### 4. 消息防抖

```python
class MessageDebouncer:
    def __init__(self, debounce_ms: float = 300):
        self._debounce_ms = debounce_ms / 1000
        self._buffers: dict[str, list] = defaultdict(list)
        self._timers: dict[str, asyncio.Task] = {}
        self._handlers: list[Callable] = []
    
    def on_flush(self, handler: Callable):
        self._handlers.append(handler)
    
    async def enqueue(self, item: T, key: str):
        self._buffers[key].append(item)
        
        if key in self._timers:
            self._timers[key].cancel()
        
        self._timers[key] = asyncio.create_task(self._flush_timer(key))
    
    async def _flush_timer(self, key: str):
        await asyncio.sleep(self._debounce_ms)
        items = self._buffers.pop(key, [])
        self._timers.pop(key, None)
        
        for handler in self._handlers:
            await handler(items)
```

### 5. 配对系统

```python
import secrets
import json

class PairingManager:
    def __init__(self, storage_path: str):
        self._storage_path = storage_path
        self._pending: dict[str, str] = {}  # code -> user_id
        self._approved: set[str] = set()
        self._load()
    
    def generate_code(self, user_id: str) -> str:
        code = secrets.token_hex(4).upper()
        self._pending[code] = user_id
        return code
    
    def approve(self, code: str) -> str | None:
        user_id = self._pending.pop(code, None)
        if user_id:
            self._approved.add(user_id)
            self._save()
            return user_id
        return None
    
    def is_approved(self, user_id: str) -> bool:
        return user_id in self._approved or "*" in self._approved
```

---

## 配置速查

```python
# config.py 新增字段

class FeishuConfig(BaseModel):
    # 原有字段
    app_id: str = ""
    app_secret: str = ""
    
    # 新增：权限策略
    dm_policy: str = "pairing"  # open | pairing | allowlist | disabled
    group_policy: str = "open"  # open | allowlist | disabled
    require_mention: bool = True
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    
    # 新增：会话隔离
    session_scope: str = "group"  # group | group_sender | group_topic | group_topic_sender
    reply_in_thread: bool = False
    
    # 新增：防抖
    debounce_enabled: bool = True
    debounce_ms: int = 300
    
    # 新增：去重
    dedup_cache_size: int = 10000
    dedup_ttl_days: int = 7
```

---

## 常见问题

### Q: 为什么需要持久化去重？

A: 服务重启后内存数据丢失，会导致消息重复处理。SQLite 去重可保证：
- 重启后仍能识别已处理消息
- 支持多实例部署（共享数据库）
- 可设置过期时间自动清理

### Q: 消息队列会不会导致性能下降？

A: 不会。设计特点：
- 不同 chat 之间完全并行
- 同一 chat 内串行（保证顺序）
- 异步处理不阻塞 WebSocket 接收

### Q: 会话隔离策略如何选择？

| 场景 | 推荐策略 |
|-----|---------|
| 小型团队，简单问答 | `group` |
| 多人协作，各自独立 | `group_sender` |
| 话题讨论，上下文重要 | `group_topic` |
| 复杂协作，严格隔离 | `group_topic_sender` |

### Q: 如何测试重连逻辑？

```python
# 模拟断网测试
async def test_reconnect():
    bot = FeishuBot(config, handler)
    
    # 启动
    task = asyncio.create_task(bot.start())
    await asyncio.sleep(5)
    
    # 模拟断开（强制关闭连接）
    await bot._client._ws.close()
    
    # 观察是否自动重连
    await asyncio.sleep(10)
    assert bot._client.is_connected
```

---

## 性能优化建议

1. **SQLite WAL 模式**
   ```python
   await db.execute("PRAGMA journal_mode=WAL")
   await db.execute("PRAGMA synchronous=NORMAL")
   ```

2. **连接池**
   ```python
   # 使用 aiosqlite 连接池或自己实现
   self._pool = asyncio.Queue(maxsize=5)
   for _ in range(5):
       conn = await aiosqlite.connect(self.db_path)
       await self._pool.put(conn)
   ```

3. **批量写入**
   ```python
   # 去重标记批量提交
   async def batch_mark_processed(self, message_ids: list[str]):
       async with aiosqlite.connect(self.db_path) as db:
           await db.executemany(
               "INSERT OR IGNORE INTO processed_messages (id, expired_at) VALUES (?, ?)",
               [(mid, int(time.time()) + ttl) for mid in message_ids]
           )
           await db.commit()
   ```

---

## 迁移检查清单

- [ ] 安装 aiosqlite: `uv pip install aiosqlite`
- [ ] 创建新目录结构
- [ ] 复制 dedup.py, queue.py, session.py
- [ ] 更新 config.py 添加新字段
- [ ] 重构 feishu.py 或创建新 bot.py
- [ ] 编写单元测试
- [ ] 在测试环境验证
- [ ] 逐步在生产环境启用
