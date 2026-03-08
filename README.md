# ccbot 🐈

超轻量级个人 AI 助手框架，基于 **Claude Agent SDK** + **飞书接入**。

## 特性

- 🚀 **Claude Agent SDK**：完整的工具能力（Read/Write/Bash/Glob/Grep/WebFetch/WebSearch）
- 🤖 **多 Agent 编排**：Supervisor-Worker 架构，结构化 dispatch，自动并行调度
- 💬 **飞书机器人**：WebSocket 长连接，支持所有消息类型和媒体
- 🧠 **记忆系统**：两层记忆（Claude runtime session resume + 本地长期/短期记忆）
- ⏰ **定时任务**：Cron 定时调度 + HEARTBEAT.md 周期性任务
- 🎯 **Skills 系统**：内置 + 自定义 skills，按需加载
- 📊 **可观测性**：LangSmith 原生 Claude Agent SDK 集成追踪

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

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  Channel Layer      │  Feishu Channel  │  CLI Channel      │
├─────────────────────┴──────────────────┴────────────────────┤
│                    Inbound Pipeline                          │
│  Dedup (内存+JSON) → Debounce (300ms) → PerChatQueue        │
├─────────────────────────────────────────────────────────────┤
│  Agent Runtime                                              │
│  AgentTeam (Supervisor → WorkerPool → Workers)              │
│  SchedulerService + HeartbeatService                        │
├─────────────────────────────────────────────────────────────┤
│  Runtime Layer                                              │
│  AgentPool / WorkerPool → ClaudeSDKClient (持久会话)        │
│  RuntimeRoleProfile (Supervisor/Worker/Reviewer)            │
├─────────────────────────────────────────────────────────────┤
│  Outbound Layer                                             │
│  ChannelResponder → 飞书卡片 / CLI 输出                     │
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
Supervisor 分析 → SupervisorResponse (structured output)
       ↓
  mode=respond → 直接回复
  mode=dispatch → 并行 Workers → 综合结果
  mode=schedule_create → 创建定时任务
```

详见 `docs/` 下的架构文档。

## 可观测性

`ccbot` 支持通过 LangSmith 官方 Claude Agent SDK 集成追踪 `ClaudeSDKClient` 的运行。

```bash
# 安装可观测性依赖
uv sync --group observability

# 启用 tracing
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=lsv2_xxx
export LANGSMITH_PROJECT=ccbot-dev
```

详见 `docs/LANGSMITH_INTEGRATION.md`。

## 多 Agent 调度

Supervisor 通过结构化输出（JSON Schema）自动判断任务处理方式：

- **respond**：简单任务，Supervisor 直接回复
- **dispatch**：复杂任务，拆分为多个 WorkerTask 并行执行
- **schedule_create**：定时任务，创建 Cron 调度

Dispatch 示例：

```json
{
  "mode": "dispatch",
  "user_message": "正在为你拆分前后端任务...",
  "tasks": [
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
}
```

Workers 并行执行，结果由 Supervisor 综合后返回用户。Workers 支持持久化复用（同名 Worker 保留上下文）。

## 定时调度

支持 Cron 表达式定时任务，持久化存储在 workspace 中：

```bash
# 在对话中告诉 Supervisor 创建定时任务
"每天早上 9 点检查一下项目里有没有 TODO"

# 管理命令
/schedule list              # 查看所有定时任务
/schedule run <job_id>      # 立即执行
/schedule pause <job_id>    # 暂停
/schedule resume <job_id>   # 恢复
/schedule delete <job_id>   # 删除
```

## Workspace 结构

```
~/.ccbot/workspace/
  .claude/
    CLAUDE.md           # 项目级指令（Claude Code 自动加载）
    settings.json       # 项目级工具权限
    skills/
      <name>/SKILL.md   # 自定义 skills（memory/scheduler/github 等）
  .ccbot/
    memory/
      long_term.md      # 长期记忆（注入 system_prompt）
      conversations/    # 每 chat 的短期记忆 + runtime session id
    schedules/
      jobs.json         # 定时任务持久化
  HEARTBEAT.md          # 心跳任务入口
  output/               # 生成文件输出目录（Channel 自动上传）
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `ccbot version` | 显示版本 |
| `ccbot chat` | 交互式对话（支持多 Agent 调度） |
| `ccbot chat -m "消息"` | 单次查询 |
| `ccbot run` | 启动机器人（Supervisor+Worker+Scheduler+Heartbeat） |
| `ccbot worker` | 单次 worker agent（供外部脚本调用） |

## 配置

完整配置示例：

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "allow_from": ["*"],
    "react_emoji": "THINKING",
    "require_mention": false
  },
  "agent": {
    "model": "claude-sonnet-4-6",
    "workspace": "~/.ccbot/workspace",
    "max_turns": 10,
    "idle_timeout": 28800,
    "max_workers": 4,
    "heartbeat_enabled": true,
    "heartbeat_interval": 1800,
    "scheduler_enabled": true,
    "scheduler_poll_interval_s": 30,
    "supervisor_resume_enabled": true,
    "short_term_memory_turns": 12,
    "mcp_servers": {},
    "env": {}
  }
}
```

配置加载优先级：`JSON 文件 > CCBOT_* 环境变量 > 默认值`

```bash
export CCBOT_FEISHU__APP_ID=cli_xxx
export CCBOT_FEISHU__APP_SECRET=xxx
export CCBOT_AGENT__MODEL=claude-opus-4-6
```

## 开发

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest

# 代码检查
uv run ruff check .
uv run mypy src/ccbot
```

## 许可

MIT License
