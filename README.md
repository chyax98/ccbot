# ccbot 🐈

超轻量级个人 AI 助手框架，基于 **Claude Agent SDK** + **飞书接入** + **A2A 协议**。

## 特性

- 🚀 **Claude Agent SDK**：完整的工具能力（Read/Write/Bash/Glob/Grep/WebFetch/WebSearch）
- 🤖 **多 Agent 编排**：Supervisor-Worker 架构，自动并行调度
- 💬 **飞书机器人**：WebSocket 长连接，支持所有消息类型和媒体
- 🔗 **A2A 协议**：Agent-to-Agent 跨机器通信，支持多轮对话
- 🧠 **记忆系统**：MEMORY.md 长期记忆 + HISTORY.md 历史日志
- ⏰ **定时任务**：HEARTBEAT.md 自动执行周期性任务
- 🎯 **Skills 系统**：内置 + 自定义 skills，按需加载

## 快速开始

### 安装

```bash
cd ccbot
uv sync
```

### 命令行对话

```bash
# 交互模式
uv run ccbot chat

# 单次查询
uv run ccbot chat -m "你好"
```

### 飞书机器人

1. 创建配置文件 `~/.ccbot/config.json`：

```json
{
  "feishu": {
    "app_id": "your_app_id",
    "app_secret": "your_app_secret"
  },
  "agent": {
    "model": "claude-sonnet-4-6",
    "workspace": "~/.ccbot/workspace"
  }
}
```

2. 启动机器人：

```bash
uv run ccbot run
```

### A2A 服务器（Agent-to-Agent 通信）

启动 HTTP 服务器，支持跨机器 Agent 通信：

```bash
# 配置 a2a.enabled = true
uv run ccbot serve --config config.json
```

详见 [A2A 协议文档](docs/A2A.md)。

## 架构

ccbot v2 采用 OpenClaw 风格分层架构：

```
┌─────────────────────────────────────────────────────────────┐
│  Channel Layer      │  Feishu Channel  │  CLI Channel      │
├─────────────────────┴──────────────────┴────────────────────┤
│                    Inbound Pipeline                          │
│  Dedup (内存+JSON) → Debounce (300ms) → PerChatQueue        │
├─────────────────────────────────────────────────────────────┤
│                    Agent Runtime                             │
│  AgentPool (Client 生命周期) + AgentTeam (Supervisor-Worker) │
├─────────────────────────────────────────────────────────────┤
│                    Outbound Layer                            │
│              飞书卡片 / CLI 输出 / A2A 响应                  │
└─────────────────────────────────────────────────────────────┘
```

### 核心流程

```
Feishu WebSocket
       ↓
FeishuChannel (集成 Pipeline)
       ↓
  1. Dedup: message_id 去重
  2. Debounce: 300ms 防抖合并
  3. Queue: 每 chat 串行队列
       ↓
AgentTeam.ask(chat_id, text)
       ↓
Supervisor 分析 → <dispatch>?
       ↓
并行 Workers (结构化 Pydantic 调度)
       ↓
综合结果 → 返回回复
```

详见 [架构文档](docs/ARCHITECTURE.md) 和 [迁移指南](docs/MIGRATION.md)。

## 多 Agent 调度

Supervisor 自动识别适合并行的任务，输出 `<dispatch>` 计划：

```xml
<dispatch>
[
  {
    "name": "frontend",
    "cwd": "/path/to/frontend",
    "task": "实现登录页面",
    "model": "claude-sonnet-4-6"
  },
  {
    "name": "backend",
    "cwd": "/path/to/backend",
    "task": "实现登录 API"
  }
]
</dispatch>
```

Workers 并行执行，结果自动综合后返回。

## Workspace 结构

```
~/.ccbot/workspace/
  memory/
    MEMORY.md       # 长期记忆（始终载入 system_prompt）
    HISTORY.md      # 历史日志（可 grep 查询）
  SOUL.md           # Agent 个性
  AGENTS.md         # 多 Agent 协作指令
  USER.md           # 用户偏好
  TOOLS.md          # 工具使用指南
  HEARTBEAT.md      # 定时任务
  skills/
    <name>/SKILL.md # 自定义 skills
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `ccbot version` | 显示版本 |
| `ccbot chat` | 交互式对话 |
| `ccbot chat -m "消息"` | 单次查询 |
| `ccbot run` | 启动飞书机器人 |
| `ccbot serve` | 启动 A2A HTTP 服务器 |
| `ccbot worker` | 单次 worker（供外部调用） |

## 配置

完整配置示例：

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "allow_from": ["*"],
    "react_emoji": "THUMBSUP",
    "require_mention": false
  },
  "agent": {
    "model": "claude-sonnet-4-6",
    "max_turns": 10,
    "workspace": "~/.ccbot/workspace",
    "heartbeat_enabled": true,
    "heartbeat_interval": 1800,
    "mcp_servers": {}
  },
  "a2a": {
    "enabled": false,
    "host": "0.0.0.0",
    "port": 8765,
    "name": "ccbot",
    "description": "Claude Agent SDK powered assistant"
  }
}
```

环境变量优先级更高：

```bash
export ccbot_FEISHU__APP_ID=cli_xxx
export ccbot_FEISHU__APP_SECRET=xxx
export ccbot_AGENT__MODEL=claude-opus-4-6
```

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest

# 代码检查
uv run ruff check .
uv run mypy ccbot
```

## 许可

MIT License
