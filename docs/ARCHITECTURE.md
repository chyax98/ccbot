# ccbot 架构设计

## 概述

ccbot 是基于 Claude Agent SDK 的个人 AI 助手，采用 OpenClaw 风格的分层架构设计。

```
┌─────────────────────────────────────────────────────────────────┐
│                        架构总览                                   │
├─────────────────────────────────────────────────────────────────┤
│  Channel Layer    │  Feishu Channel  │  CLI Channel            │
├───────────────────┴──────────────────┴───────────────────────────┤
│                     Inbound Pipeline                              │
│  ┌──────────┐  ┌────────────┐  ┌──────────────────────────────┐ │
│  │  Dedup   │→ │  Debounce  │→ │         PerChatQueue         │ │
│  │ 去重缓存  │  │  防抖合并   │  │      每聊天串行队列          │ │
│  └──────────┘  └────────────┘  └──────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                     Agent Runtime                                 │
│  ┌──────────────┐  ┌──────────────────────────────────────────┐ │
│  │  AgentPool   │  │           AgentTeam (Supervisor)         │ │
│  │ Client池化管理│  │  ┌────────────────────────────────────┐  │ │
│  │ - 复用       │  │  │         Worker Pool                │  │ │
│  │ - 空闲释放   │  │  │  ┌─────┐ ┌─────┐ ┌─────┐          │  │ │
│  └──────────────┘  │  │  │Worker│ │Worker│ │Worker│          │  │ │
│                    │  │  └──┬──┘ └──┬──┘ └──┬──┘          │  │ │
│                    │  │     └───────┼───────┘              │  │ │
│                    │  │        comm (MCP)                  │  │ │
│                    │  └────────────────────────────────────┘  │ │
│                    └──────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                     Outbound Layer                                │
│              Feishu 卡片 / CLI 输出 / A2A 响应                    │
└─────────────────────────────────────────────────────────────────┘
```

## 核心组件

### 1. Inbound Pipeline

位于 `src/ccbot/core/`，处理所有入站消息的标准化流程。

#### DedupCache - 去重缓存

```python
class DedupCache:
    """内存 LRU + 异步 JSON 持久化"""

    # 特性
    - OrderedDict 实现 O(1) 查找
    - TTL 过期（默认 24 小时）
    - 自动容量管理（默认 1000 条）
    - 异步 JSON 文件持久化
```

**设计理由**：个人使用场景下，内存+文件足够，无需 Redis/PostgreSQL。

#### Debouncer - 防抖合并

```python
class Debouncer[T]:
    """300ms 延迟合并，控制命令立即处理"""

    # 特性
    - 按 key 分组（chat_id + thread + sender）
    - 控制命令绕过：/new, /stop, /help, /reset, /clear
    - 最大等待时间防止无限缓冲
```

**设计理由**：减少 API 调用和 token 消耗，改善用户体验。

#### PerChatQueue - 每聊天队列

```python
class PerChatQueue:
    """每 chat 独立队列，串行处理，异常隔离"""

    # 特性
    - 同 chat 串行（保证消息顺序）
    - 不同 chat 并行（提高吞吐量）
    - 异常隔离（单任务失败不影响队列）
    - 60s 超时自动清理空闲 worker
```

### 2. Agent Runtime

位于 `src/ccbot/runtime/` 和 `src/ccbot/models/`。

#### AgentPool - Client 生命周期管理

```python
class AgentPool:
    """ClaudeSDKClient 池化管理"""

    # 特性
    - 按 chat_id 复用 client
    - 8 小时空闲自动释放（28800s，继承 config.idle_timeout）
    - 优雅关闭时保存历史
```

**对比旧版**：旧版直接管理 sessions，现在由 `AgentPool` 统一管理。

#### AgentTeam - 多 Agent 调度

```python
class AgentTeam:
    """Supervisor + Worker 模式"""

    # 流程
    1. Supervisor 分析任务
    2. 如需并行，输出 <dispatch>[...]</dispatch>
    3. 解析 DispatchPayload（Pydantic 模型）
    4. 并行启动 Workers
    5. 收集 WorkerResult
    6. 喂回 Supervisor 综合回复
```

**设计理由**：充分利用 Claude Agent SDK 原生能力，无需外部进程调度。

#### Worker 通信层（comm）

位于 `src/ccbot/comm/`，为 Worker 提供进程内通信能力。

```python
# 核心组件
InMemoryBus        # 消息路由：DIRECT/BROADCAST/REPORT/CLARIFY
InMemoryContext    # 共享状态：Worker 间 key-value 读写
WorkerChannel      # 为每个 Worker 生成 MCP 服务器配置
```

通信通过 SDK 进程内 MCP 服务器实现，零网络开销。每个 Worker 自动获得 7 个 MCP 工具（`ccbot_send_message`、`ccbot_read_messages` 等），可以与其他 Worker 交换消息和共享状态。

详见 [通信模块文档](COMM.md)。

### 3. Channel 抽象

位于 `src/ccbot/channels/`。

```python
class Channel(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, target: str, content: str) -> None: ...
```

#### FeishuChannel

集成 Pipeline 的飞书通道：
- 使用 `DedupCache` 去重
- 使用 `Debouncer` 防抖
- 使用 `PerChatQueue` 队列

#### CLIChannel

本地交互通道：
- 交互式 REPL 模式
- 单消息模式（`-m "message"`）
- 支持进度回调显示

## 数据流

```
Feishu WebSocket
       ↓
┌──────────────────┐
│  FeishuChannel   │
│  _on_message     │
└────────┬─────────┘
         ↓
┌──────────────────┐
│  1. Dedup Check  │ ← 内存 LRU + JSON 文件
│  message_id 存在？ │
└────────┬─────────┘
         ↓ 否
┌──────────────────┐
│  2. Debounce     │ ← 300ms 延迟 / 控制命令立即
│  按 chat 分组缓冲 │
└────────┬─────────┘
         ↓ 触发 flush
┌──────────────────┐
│  3. PerChatQueue │ ← 每 chat 串行队列
│  enqueue(task)   │
└────────┬─────────┘
         ↓ 出队执行
┌──────────────────┐
│  4. AgentTeam    │
│  ask(chat_id, msg)│
└────────┬─────────┘
         ↓
   ┌─────┴─────┐
   ↓           ↓
Supervisor   Dispatch?
   ↓           是
 Reply      ┌─────────────┐
            │ 解析 Dispatch│
            │ 启动 Workers  │
            └──────┬──────┘
                   ↓
            ┌─────────────┐
            │ 综合结果     │
            │ 生成回复     │
            └─────────────┘
```

## 配置设计

```python
class Config:
    feishu: FeishuConfig      # 飞书认证、权限、交互配置
    agent: AgentConfig        # 模型、工具、心跳配置
    a2a: A2AConfig           # Agent-to-Agent 服务器配置
```

**优先级**：环境变量 > JSON 文件 > 默认值

## 技术选型理由

| 组件 | 选择 | 理由 |
|------|------|------|
| 去重存储 | 内存 LRU + JSON | 个人使用足够，无需 PostgreSQL |
| 队列 | asyncio.Queue | Python 原生，无需 Redis/RabbitMQ |
| 调度 | Claude Agent SDK | 原生支持多 Agent，无需自建 |
| 配置 | Pydantic Settings | 类型安全，环境变量自动映射 |
| 日志 | loguru | 结构化日志，自动轮转 |
| Worker 通信 | 进程内 MCP (SDK) | 零开销，后端可替换为 Redis |

## 扩展性

### 添加新 Channel

```python
class MyChannel(Channel):
    async def start(self) -> None:
        # 启动连接
        pass

    async def stop(self) -> None:
        # 关闭连接
        pass

    async def send(self, target: str, content: str) -> None:
        # 发送消息
        pass
```

### 自定义 Pipeline

```python
# 组合现有组件
dedup = DedupCache(ttl_ms=3600000)
debounce = Debouncer[MyEvent](
    delay_ms=500,
    key_extractor=lambda e: e.chat_id,
)
queue = PerChatQueue()
```

## 与 OpenClaw 的对比

| 特性 | ccbot | OpenClaw |
|------|-------|----------|
| 目标场景 | 个人使用 | 多用户/团队 |
| 部署 | 单机 | 分布式 |
| 存储 | SQLite/文件 | PostgreSQL |
| 队列 | asyncio.Queue | Redis/RabbitMQ |
| 进程模型 | Python asyncio | Node.js/多进程 |
| Agent 调度 | Claude SDK 原生 | 自建调度器 |

ccbot 是 OpenClaw 的"个人轻量版"，保留核心设计理念但大幅简化运维。
