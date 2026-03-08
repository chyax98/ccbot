# ccbot 技术文档

> 版本: 0.3.0 | 基于 Claude Agent SDK 的轻量级个人 AI 助手

## 目录

1. [架构概览](#架构概览)
2. [核心模块](#核心模块)
3. [配置系统](#配置系统)
4. [API 参考](#api-参考)
5. [多 Agent 编排](#多-agent-编排)
6. [消息通道](#消息通道)
7. [入站 Pipeline](#入站-pipeline)
8. [A2A 协议](#a2a-协议)
9. [Docker 部署](#docker-部署)

---

## 架构概览

ccbot 采用分层架构设计，参考 OpenClaw 模式：

```
┌─────────────────────────────────────────────────────────────┐
│                    消息通道层 (Channel)                      │
│              FeishuChannel / CLI / A2A Server               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   入站 Pipeline (Inbound)                    │
│         Dedup (去重) → Debounce (防抖) → Queue (队列)        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   编排层 (Orchestration)                     │
│         AgentTeam (Supervisor-Worker 模式) / CCBotAgent      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   运行时层 (Runtime)                         │
│              AgentPool (ClaudeSDKClient 管理)               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   外部服务 (External)                        │
│              Claude API / MCP Servers / Tools               │
└─────────────────────────────────────────────────────────────┘
```

---

## 核心模块

### 1. CCBotAgent (`ccbot.agent`)

单会话 Agent 封装，每个 chat_id 对应一个独立的 ClaudeSDKClient。

```python
class CCBotAgent:
    """Multi-turn agent backed by Claude Agent SDK.

    Args:
        config: Agent 配置
        workspace: 工作空间管理器（可选，worker 模式可不传）
        extra_system_prompt: 额外的 system prompt
        idle_timeout: 空闲超时秒数

    Example:
        agent = CCBotAgent(AgentConfig(), workspace)
        await agent.start()
        reply = await agent.ask("chat_123", "Hello!")
        await agent.stop()
    """
```

**核心方法：**

| 方法 | 签名 | 说明 |
|------|------|------|
| `start` | `async () -> None` | 启动 agent，初始化 AgentPool |
| `stop` | `async () -> None` | 停止 agent，关闭所有 client |
| `ask` | `async (chat_id, prompt, on_progress) -> str` | 处理消息，返回回复 |

**Slash Commands：**

- `/help` - 显示帮助信息
- `/new` - 开启新会话（关闭当前 session）
- `/stop` - 中断当前正在执行的任务

---

### 2. AgentPool (`ccbot.runtime.pool`)

ClaudeSDKClient 生命周期管理器。

```python
class AgentPool:
    """管理 ClaudeSDKClient 实例的池化组件。

    - 按 chat_id 缓存和复用 client
    - 空闲超时自动释放（默认继承 config.idle_timeout）
    - 优雅关闭时保存历史记录

    Args:
        config: Agent 配置
        workspace: 工作空间管理器
        extra_system_prompt: 额外的 system prompt
        idle_timeout: 空闲超时秒数（None=继承 config，0=永不超时）
    """
```

**核心方法：**

| 方法 | 签名 | 说明 |
|------|------|------|
| `start` | `async () -> None` | 启动池，开始空闲清理任务 |
| `stop` | `async () -> None` | 停止池，关闭所有 client |
| `acquire` | `async (chat_id) -> ClaudeSDKClient` | 获取/创建指定 chat_id 的 client |
| `release` | `async (chat_id) -> None` | 释放 client（更新最后使用时间） |
| `close` | `async (chat_id) -> None` | 主动关闭指定 chat_id 的 client |
| `interrupt` | `async (chat_id) -> bool` | 中断指定 chat_id 正在执行的查询 |
| `get_stats` | `() -> dict` | 获取池统计信息 |

**空闲清理机制：**

- 每 60 秒检查一次所有 client 的空闲时间
- 空闲超过 `idle_timeout`（默认 28800s = 8 小时）的 client 自动关闭
- 关闭时自动调用 `client.disconnect()`

---

### 3. WorkspaceManager (`ccbot.workspace`)

工作空间管理，负责构建 system prompt。

```python
class WorkspaceManager:
    """Manages the ccbot workspace directory.

    Workspace layout:
        memory/MEMORY.md    — long-term facts (always in system_prompt)
        memory/HISTORY.md   — append-only grep-searchable log
        HEARTBEAT.md        — periodic tasks
        SOUL.md / AGENTS.md / USER.md / TOOLS.md — personality & instructions
        skills/<name>/SKILL.md — custom user skills (override builtins)
    """
```

**核心方法：**

| 方法 | 签名 | 说明 |
|------|------|------|
| `build_system_prompt` | `() -> str` | 构建完整 system prompt |
| `read_memory` | `() -> str` | 读取 MEMORY.md 内容 |

**System Prompt 构建顺序：**

1. Identity（运行时信息、workspace 路径、guidelines）
2. Bootstrap 文件（SOUL.md, AGENTS.md, USER.md, TOOLS.md, IDENTITY.md）
3. Long-term memory（MEMORY.md）
4. Always-on skills（always=true 的 skill）
5. Skills summary（所有可用 skill 的 XML 摘要）

**Skill Metadata 格式：**

```yaml
---
metadata: {"ccbot": {"emoji": "🌤️", "requires": {"bins": ["curl"]}}}
---
```

兼容 nanobot（`"nanobot"` key）和 OpenClaw（`"openclaw"` key）格式。查找优先级：`ccbot` > `nanobot` > `openclaw`。

---

## 配置系统

### AgentConfig (`ccbot.config`)

```python
class AgentConfig(BaseModel):
    # 基础配置
    model: str = ""                           # 模型名，空=SDK 默认
    workspace: str = _DEFAULT_WORKSPACE       # 工作空间路径
    max_turns: int = 10                       # 最大对话轮数

    # SDK 配置
    allowed_tools: list[str] = []             # 允许的工具列表
    mcp_servers: dict[str, dict[str, Any]] = {}  # MCP 服务器配置

    # Heartbeat 配置
    heartbeat_enabled: bool = True            # 是否启用心跳
    heartbeat_interval: int = 1800            # 心跳间隔（秒）
    heartbeat_notify_chat_id: str = ""        # 心跳通知目标

    # Worker 模式配置
    system_prompt: str = ""                   # 直接指定 system prompt
    cwd: str = ""                             # 工作目录覆盖

    # Session 配置
    idle_timeout: int = 28800                 # 空闲超时（默认 8 小时）

    # 多 Agent 编排
    max_workers: int = Field(default=4, ge=1, le=16)  # 最大并行 worker 数
```

### 配置加载优先级

```
环境变量 > JSON 配置文件 > 默认值
```

环境变量前缀：`CCBOT_`，嵌套分隔符：`__`

示例：
```bash
export CCBOT_AGENT__MODEL="claude-opus-4-6"
export CCBOT_AGENT__IDLE_TIMEOUT="3600"
export CCBOT_FEISHU__APP_ID="cli_xxx"
```

### Claude API 环境变量

以下环境变量由 SDK 子进程自动继承，用于配置 Claude API 连接：

```bash
# 原生 Anthropic API
ANTHROPIC_API_KEY=sk-ant-xxx

# 兼容 API（如 Kimi）
ANTHROPIC_AUTH_TOKEN=sk-xxx
ANTHROPIC_BASE_URL=https://api.kimi.com/coding/
ANTHROPIC_MODEL=kimi-for-coding
ANTHROPIC_DEFAULT_SONNET_MODEL=kimi-for-coding
ANTHROPIC_DEFAULT_OPUS_MODEL=kimi-for-coding
ANTHROPIC_DEFAULT_HAIKU_MODEL=kimi-for-coding
```

---

## 多 Agent 编排

### AgentTeam (`ccbot.team`)

Supervisor-Worker 多 Agent 编排系统。

```python
class AgentTeam:
    """Supervisor（Opus）+ 动态 Worker 池，全部跑在同一 Python asyncio 进程内。

    - 无额外进程：worker 就是 CCBotAgent（ClaudeSDKClient 子进程）
    - 无 bash 开销：Python asyncio.gather 并行
    - 实时进度：worker on_progress 前缀 "[name] "
    - 容错：单个 worker 失败不影响其他 worker
    - 并发控制：max_workers 限制并行 worker 数量（默认 4，范围 1-16）
    """
```

**工作流程：**

```
用户消息
    │
    ▼
┌─────────────────┐
│  Supervisor分析  │ ← 决定直接处理 or 派发任务
└─────────────────┘
    │
    ├── 直接处理 → 返回结果
    │
    └── 输出 dispatch 计划
            │
            ▼
    ┌─────────────────┐
    │  解析DispatchPayload │
    │  (结构化Pydantic模型) │
    └─────────────────┘
            │
            ▼
    ┌─────────────────┐
    │ asyncio.gather  │ ← 并行启动 worker（受 Semaphore 限制）
    │ 并行执行worker   │
    └─────────────────┘
            │
            ▼
    ┌─────────────────┐
    │  收集Worker结果  │
    │  → WorkerResult  │
    └─────────────────┘
            │
            ▼
    ┌─────────────────┐
    │  Supervisor综合  │ ← 生成最终回复
    └─────────────────┘
            │
            ▼
        返回用户
```

**Dispatch 格式（Supervisor 输出）：**

```xml
<dispatch>
[
  {
    "name": "frontend",
    "cwd": "/abs/path/to/project",
    "task": "实现登录页面",
    "model": "claude-sonnet-4-6",
    "max_turns": 30
  },
  {
    "name": "backend",
    "cwd": "/abs/path/to/project",
    "task": "实现登录API",
    "model": "claude-sonnet-4-6"
  }
]
</dispatch>
```

### Dispatch 模型 (`ccbot.models.dispatch`)

```python
class WorkerTask(BaseModel):
    """Worker 任务定义。"""
    name: str           # Worker 唯一名称
    task: str           # 详细任务描述
    cwd: str = "."      # 工作目录（绝对路径）
    model: str = ""     # 模型名称
    max_turns: int = 30 # 最大对话轮数

class DispatchPayload(BaseModel):
    """调度负载，包含多个 Worker 任务。"""
    tasks: list[WorkerTask]

    @classmethod
    def from_text(cls, text: str) -> Self | None:
        """从文本解析 <dispatch>...</dispatch> 块。"""

class WorkerResult(BaseModel):
    """Worker 执行结果。"""
    name: str
    success: bool
    result: str = ""
    error: str = ""

class DispatchResult(BaseModel):
    """完整调度结果。"""
    workers: list[WorkerResult]

    def to_synthesis_prompt(self) -> str:
        """生成供 Supervisor 综合的提示词。"""
```

---

## 消息通道

### Channel 基类 (`ccbot.channels.base`)

```python
class Channel(ABC):
    """消息通道抽象基类。"""

    @abstractmethod
    async def start(self) -> None:
        """启动通道。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止通道。"""

    @abstractmethod
    async def send(self, target: str, content: str, **kwargs) -> None:
        """发送消息到指定目标。"""

    def on_message(self, handler) -> None:
        """注册消息处理回调。"""
```

### FeishuChannel (`ccbot.channels.feishu`)

飞书通道，集成完整 Inbound Pipeline。

**特性：**

- WebSocket 实时消息接收
- 自动重连机制
- 权限检查（allow_from 白名单）
- 消息反应（WINK 表情表示已收到）
- 进度反馈（批量聚合工具调用）

**进度反馈机制：**

```python
# 批量聚合工具调用，每3条发送一次
progress_buffer: list[str] = []

async def progress_cb(msg: str) -> None:
    if msg.startswith("🔧"):  # 只收集工具调用消息
        progress_buffer.append(f"{total_tools}. {tool_name}")

        # 每3条发送一次，或者超过8秒有积压时也发送
        if len(progress_buffer) >= 3 or now - last_send_time > 8:
            await send(reply_to, f"⏳ 执行中 ({total_tools} 工具):\n{batch}")
```

---

## 入站 Pipeline

Pipeline 流程：`Dedup → Debounce → Queue`

### 1. Dedup (`ccbot.core.dedup`)

基于 message_id 的去重缓存。

```python
class DedupCache:
    """Memory LRU cache + async JSON persistence.

    Features:
    - In-memory OrderedDict for fast lookup
    - Async JSON file persistence
    - TTL-based expiration (default 24h)
    - Namespace support
    """
```

**核心方法：**

| 方法 | 说明 |
|------|------|
| `check(key)` | 检查 key 是否存在，不存在则添加 |
| `peek(key)` | 检查 key 是否存在（只读）|
| `persist(path, namespace)` | 持久化到 JSON 文件 |
| `load(path, namespace)` | 从 JSON 文件加载 |
| `schedule_persist(...)` | 定时持久化 |
| `stop()` | 停止并刷新缓存 |

### 2. Debounce (`ccbot.core.debounce`)

消息防抖合并。

```python
class Debouncer(Generic[T]):
    """Debounces rapid-fire messages by key.

    Features:
    - Keyed debounce (e.g., per chat)
    - Configurable delay (default 300ms)
    - Max wait time to prevent indefinite buffering
    - Control command bypass (/new, /stop, /help, etc.)
    - Flush callback for merged messages
    """
```

**配置参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `delay_ms` | 300 | 防抖延迟 |
| `max_wait_ms` | 1000 | 最大等待时间 |
| `key_extractor` | str(item) | 从 item 提取 key 的函数 |
| `is_control_command` | 内置检测 | 检测是否为控制命令 |

**控制命令（不防抖，立即处理）：**

- `/new`, `/stop`, `/help`, `/reset`, `/clear`

### 3. PerChatQueue (`ccbot.core.queue`)

每 chat 串行队列。

```python
class PerChatQueue:
    """Per-chat queue ensuring serial processing within each chat.

    Features:
    - Independent queue per chat_id
    - Parallel processing across different chats
    - Exception isolation
    - Graceful shutdown
    """
```

**核心方法：**

| 方法 | 签名 | 说明 |
|------|------|------|
| `enqueue` | `async (chat_id, handler) -> T` | 入队任务，返回结果 |
| `stop` | `async () -> None` | 停止所有 worker |
| `get_pending_count` | `(chat_id?) -> int` | 获取待处理任务数 |
| `get_active_chats` | `() -> list[str]` | 获取活跃 chat 列表 |
| `wait_for_chat` | `async (chat_id, timeout) -> bool` | 等待 chat 完成 |

---

## A2A 协议

### A2AServer (`ccbot.server`)

基于 Google A2A 协议的 HTTP 服务器。

```python
class A2AServer:
    """A2A 协议 HTTP 服务器。

    将 ccbot 的 AgentTeam 暴露为 A2A 兼容的 HTTP 端点。

    核心映射：
    - A2A contextId → ccbot chat_id（多轮对话）
    - A2A message/send → team.ask()（同步）
    - A2A message/stream → team.ask() + SSE（流式）
    """
```

**端点：**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/.well-known/agent.json` | GET | Agent Card |
| `/rpc` | POST | JSON-RPC 2.0 端点 |

**JSON-RPC 方法：**

```json
// message/send (同步)
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "contextId": "session-123",
    "message": "Hello!"
  },
  "id": 1
}

// message/stream (SSE 流式)
{
  "jsonrpc": "2.0",
  "method": "message/stream",
  "params": {
    "contextId": "session-123",
    "message": "Hello!"
  },
  "id": 2
}
```

---

## Heartbeat 服务

### HeartbeatService (`ccbot.heartbeat`)

周期性任务执行服务。

```python
class HeartbeatService:
    """Periodically reads HEARTBEAT.md and triggers the agent.

    Args:
        heartbeat_file: HEARTBEAT.md 文件路径
        on_execute: 执行回调 (prompt) -> reply
        on_notify: 通知回调 (reply) -> None
        interval_s: 检查间隔（默认 1800s = 30分钟）
    """
```

**HEARTBEAT.md 格式：**

```markdown
# Heartbeat Tasks

## Active Tasks

- [ ] 检查服务器状态
- [ ] 清理临时文件

## Completed

- [x] 备份数据库
```

---

## API 参考

### 完整类图

```
┌──────────────────────────────────────────────────────────────┐
│                          CCBotAgent                          │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                      AgentPool                       │   │
│  │  - _clients: dict[str, ClaudeSDKClient]              │   │
│  │  - _last_used: dict[str, float]                      │   │
│  │  - acquire(chat_id) -> ClaudeSDKClient               │   │
│  │  - release(chat_id)                                  │   │
│  │  - close(chat_id)                                    │   │
│  │  - interrupt(chat_id) -> bool                        │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
                              ▲
                              │
┌──────────────────────────────────────────────────────────────┐
│                         AgentTeam                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  _supervisor: CCBotAgent (extra_system_prompt)       │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  _run_workers() -> DispatchResult                    │   │
│  │  - 创建临时 CCBotAgent 作为 worker                    │   │
│  │  - asyncio.gather 并行执行（Semaphore 限制并发）       │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### 配置模型

```python
class Config(BaseSettings):
    feishu: FeishuConfig
    agent: AgentConfig
    a2a: A2AConfig
```

### 异常处理

所有核心组件都遵循以下异常处理模式：

1. **AgentPool**: 关闭/中断 client 时捕获所有异常，记录 warning 日志
2. **CCBotAgent.ask**: 异常时关闭 session，返回错误信息给用户
3. **AgentTeam._run_workers**: 单个 worker 异常返回 WorkerResult(success=False)，worker.stop() 在 finally 中保证调用
4. **FeishuChannel**: 消息处理异常记录日志，返回错误消息

---

## 设计思路

### 1. Session 持久化策略

```python
# 默认 8 小时空闲超时
idle_timeout: int = 28800  # 8 * 3600
```

- ClaudeSDKClient 的 session 保存在内存中，disconnect 后丢失
- 设置较长的 idle_timeout 可保持 session 活跃，避免 memory 丢失
- 0 表示永不自动关闭（不推荐，可能占用资源）
- AgentPool 的 idle_timeout 统一从 config.idle_timeout 继承

### 2. 并发控制

```python
# CCBotAgent: 每个 chat_id 一个 Lock
self._locks: dict[str, asyncio.Lock] = {}

# PerChatQueue: 每个 chat_id 一个 Queue + Worker
self._queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
self._workers: dict[str, asyncio.Task] = {}

# AgentTeam: Semaphore 限制并行 worker 数
semaphore = asyncio.Semaphore(self._config.max_workers)
```

同一 chat_id 的请求被串行化，不同 chat_id 之间并行处理。

### 3. 进度反馈机制

```python
# 层级传递
FeishuChannel._process_event.on_progress
    ↓
AgentTeam.ask.on_progress
    ↓
CCBotAgent.ask.on_progress
    ↓
client.receive_response() 中的 TaskProgressMessage
```

Worker 进度带有 `[name]` 前缀，便于聚合显示。

### 4. 多 Agent 调度策略

1. **Supervisor** 使用 `claude-opus-4-6` 模型进行任务分析
2. 复杂任务输出结构化 dispatch 计划
3. **Worker** 使用独立配置（可指定不同模型）
4. 所有 Worker 并行执行，通过 `asyncio.gather` 收集结果（受 `max_workers` Semaphore 限制）
5. **Supervisor** 综合所有 Worker 结果生成最终回复
6. Worker 生命周期由 `start()/stop()` 管理，stop 在 finally 中保证调用

---

## Docker 部署

### 快速开始

```bash
# 复制环境变量模板
cp .env.example .env
vi .env  # 填入 API Key 和飞书配置

# 构建并启动
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

### 镜像架构

```
python:3.11-slim
  + uv（Python 包管理）
  + git（供 Claude Agent 使用）
  + ccbot 源码 + 依赖
  + claude-agent-sdk（内置平台对应的 claude 二进制）
```

不需要 Node.js。`claude-agent-sdk` 的 pip wheel 按平台分发对应的原生 `claude` 二进制（约 192MB）。

### 环境变量

所有环境变量通过 `.env` 文件集中管理，`docker-compose.yml` 通过 `env_file` 加载。

SDK 子进程自动继承容器环境变量，`ANTHROPIC_*` 和 `CLAUDE_*` 直接写 `.env` 即可。

### 数据持久化

| Volume | 挂载点 | 用途 |
|--------|--------|------|
| `ccbot-workspace` | `/home/ccbot/.ccbot/workspace` | 记忆、技能、模板 |
| `ccbot-data` | `/home/ccbot/.ccbot/data` | 去重缓存等运行时数据 |

---

## CLI 命令

```bash
# 版本信息
ccbot version

# 交互式对话
ccbot chat [--message "Hello"] [--workspace /path]

# Worker 模式（单次任务）
ccbot worker "实现登录功能" --cwd /project --output result.md --model claude-opus-4-6

# 启动飞书机器人
ccbot run [--config ~/.ccbot/config.json]

# 启动 A2A 服务器
ccbot serve [--config ~/.ccbot/config.json]
```

---

*文档生成时间: 2026-03-08*
*ccbot version: 0.3.0*
