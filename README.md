# ccbot 🐈

超轻量级个人 AI 助手框架，基于 **Claude Agent SDK** + **飞书接入**。

## 特性

- 🚀 **Claude Agent SDK**：完整的工具能力（Read/Write/Bash/Glob/Grep/WebFetch/WebSearch）
- 🤖 **多 Agent 编排**：Supervisor-Worker 架构，结构化 dispatch，自动并行调度
- 💬 **飞书机器人**：WebSocket 长连接接入，支持消息接收、进度反馈与结果回传
- 🧠 **记忆系统**：两层记忆（Claude runtime session resume + 本地长期/短期记忆）
- ⏰ **定时任务**：Cron 定时调度，SDK MCP tools 管理
- 🎯 **Skills 系统**：内置 + 自定义 skills，按需加载
- 📊 **可观测性**：LangSmith 原生 Claude Agent SDK 集成追踪

## 文档索引

建议按下面顺序阅读：

- `docs/README.md`：模块化文档地图
- `docs/PRODUCT_ARCHITECTURE.md`：产品定义、架构、边界
- `docs/CLAUDE_RUNTIME.md`：Claude SDK、runtime profile、memory
- `docs/CHANNELS_AND_OPERATIONS.md`：Channel、workspace、运行与回归
- `docs/OBSERVABILITY_AND_TROUBLESHOOTING.md`：LangSmith、日志、排障
- `docs/PROJECT_REVIEW.md`：当前架构评审与演进建议（补充阅读）

兼容旧引用的专题入口仍保留在 `docs/` 下，但已逐步收敛为跳转页。

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
```

详见 `docs/PRODUCT_ARCHITECTURE.md`。

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

详见 `docs/OBSERVABILITY_AND_TROUBLESHOOTING.md`。

## 多 Agent 调度

Supervisor 通过结构化输出（JSON Schema）自动判断任务处理方式：

- **respond**：简单任务，Supervisor 直接回复
- **dispatch**：复杂任务，拆分为多个 WorkerTask 并行执行

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
    output/
```

- `workspace/.claude/`：Supervisor 的项目级 Claude 配置
- `workspace/.ccbot/`：运行态数据（记忆、schedule 等）
- `task.cwd/.claude/`：Worker 所在项目自己的 Claude 配置；若不存在只补最小模板

## 开发

### 环境搭建

```bash
uv sync --group dev
```

### 本地 CI

项目使用 git hooks 在提交/推送前自动检查：

```bash
# 启用 git hooks（仅需执行一次）
make hooks

# 手动运行完整 CI
make ci
```

| 阶段 | 内容 | 阻断 |
|------|------|------|
| pre-commit | `ruff --fix` + `ruff format`（自动修复） | 否 |
| pre-push | lint + typecheck + test（完整检查） | 是 |

### 常用命令

```bash
make lint        # ruff check
make format      # ruff format + fix
make typecheck   # mypy
make test        # pytest
make ci          # lint + typecheck + test
```

## 当前边界

- 当前不依赖 A2A
- 当前不依赖远程 Worker
- 当前只为 Supervisor 增加额外记忆
- 当前不允许 Claude Code 原生 `Agent` / `SendMessage` 接管多 Agent 调度
