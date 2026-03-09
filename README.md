# ccbot 🐈

超轻量级个人 AI 助手框架，基于 **Claude Agent SDK** + **飞书接入**。

## 特性

- 🚀 **Claude Agent SDK**：完整的工具能力（Read/Write/Bash/Glob/Grep/WebFetch/WebSearch）
- 🤖 **多 Agent 编排**：Supervisor-Worker 架构，结构化 dispatch，自动并行调度
- 💬 **飞书机器人**：WebSocket 长连接接入，支持消息接收、进度反馈与结果回传
- 🧠 **记忆系统**：两层记忆（Claude runtime session resume + 本地长期/短期记忆）
- ⏰ **定时任务**：Cron 定时调度 + HEARTBEAT.md 周期性任务
- 🎯 **Skills 系统**：内置 + 自定义 skills，按需加载
- 📊 **可观测性**：LangSmith 原生 Claude Agent SDK 集成追踪

## 文档索引

建议按下面顺序阅读：

- `docs/README.md`：完整文档地图与阅读路径
- `docs/ARCHITECTURE.md`：当前推荐架构、运行边界、核心链路
- `docs/RUNTIME_OPERATIONS.md`：运行目录、配置、启动、回归、预演
- `docs/TROUBLESHOOTING.md`：常见错误与定位方法

专题文档：

- `docs/CLAUDE_AGENT_SDK_CAPABILITY_MAP.md`：Claude Agent SDK 能力盘点
- `docs/CLAUDE_RUNTIME_PROFILES.md`：Supervisor / Worker / Reviewer 的 runtime profile
- `docs/LANGSMITH_INTEGRATION.md`：LangSmith tracing 接入
- `docs/MEMORY_ARCHITECTURE.md`：Supervisor 记忆体系
- `docs/CHANNEL_ARCHITECTURE.md`：Feishu / Channel 抽象
- `docs/ARCHITECTURE_REVIEW.md`：为什么当前阶段不做 A2A
- `docs/PRODUCT_REQUIREMENTS_MODEL.md`：产品需求模型与阶段边界

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

```text
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

```text
Feishu WebSocket / CLI
       ↓
Channel
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
  mode=dispatch → 并行 Workers → Supervisor 综合结果
  mode=schedule_create → 创建定时任务
```

详见 `docs/ARCHITECTURE.md`。

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
- **schedule_create**：周期性定时任务，创建 Cron 调度

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

```text
~/.ccbot/
  config.json
  workspace/
    .claude/
      CLAUDE.md
      settings.json
      skills/
    .ccbot/
      memory/
      schedules/
    HEARTBEAT.md
    output/
```

- `workspace/.claude/`：Supervisor 的项目级 Claude 配置
- `workspace/.ccbot/`：运行态数据（记忆、schedule 等）
- `task.cwd/.claude/`：Worker 所在项目自己的 Claude 配置；若不存在只补最小模板

## 当前边界

- 当前不依赖 A2A
- 当前不依赖远程 Worker
- 当前只为 Supervisor 增加额外记忆
- 当前不允许 Claude Code 原生 `Agent` / `SendMessage` 接管多 Agent 调度
