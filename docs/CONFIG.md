# 配置文件指南

## 配置文件位置

ccbot 支持多种配置方式，按优先级从高到低：

### 1. JSON 配置文件（最高优先级）

```bash
ccbot run --config /path/to/config.json
ccbot serve --config /path/to/config.json
```

### 2. 环境变量

```bash
export CCBOT_FEISHU__APP_ID=cli_xxx
export CCBOT_FEISHU__APP_SECRET=xxx
export CCBOT_AGENT__MODEL=claude-opus-4-6
```

### 3. 默认值

配置加载优先级：`JSON 文件 > CCBOT_* 环境变量 > 默认值`

默认配置文件位置：

```
~/.ccbot/config.json
```

如果不指定 `--config`，ccbot 会自动读取这个文件。

---

## 推荐的配置组织

### 方案 A：单一配置文件（推荐）

适合单机部署，所有配置放在一个文件：

```
~/.ccbot/
  config.json          # 主配置
  workspace/           # Workspace 目录
    .claude/
      CLAUDE.md
      settings.json
      skills/
    memory/
```

**config.json**：
```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx"
  },
  "agent": {
    "model": "claude-sonnet-4-6"
  }
}
```

### 方案 B：多配置文件（多机部署）

适合 Supervisor + Workers 分布式部署：

```
~/.ccbot/
  supervisor.json      # Supervisor 配置
  worker1.json         # Worker 1 配置
  worker2.json         # Worker 2 配置
  workspace/
    supervisor/        # Supervisor workspace
    worker1/           # Worker 1 workspace
    worker2/           # Worker 2 workspace
```

**启动命令**：
```bash
# Supervisor（机器 A）
ccbot run --config ~/.ccbot/supervisor.json

# Worker 1（机器 B）
ccbot serve --config ~/.ccbot/worker1.json

# Worker 2（机器 C）
ccbot serve --config ~/.ccbot/worker2.json
```

### 方案 C：环境变量（容器部署）

适合 Docker/K8s 部署：

```bash
# docker-compose.yml
services:
  supervisor:
    image: ccbot:latest
    environment:
      CCBOT_FEISHU__APP_ID: cli_xxx
      CCBOT_FEISHU__APP_SECRET: xxx
      CCBOT_AGENT__MODEL: claude-opus-4-6

  worker1:
    image: ccbot:latest
    environment:
      CCBOT_A2A__ENABLED: true
      CCBOT_A2A__PORT: 8765
      CCBOT_AGENT__MODEL: claude-sonnet-4-6
```

---

## 配置示例

### 最小配置（日常使用）

`examples/config.minimal.json`：
```json
{
  "feishu": {
    "app_id": "cli_your_app_id",
    "app_secret": "your_app_secret"
  },
  "agent": {
    "model": "claude-sonnet-4-6"
  }
}
```

### 完整配置（所有选项）

`examples/config.full.json`：
```json
{
  "feishu": {
    "app_id": "cli_your_app_id",
    "app_secret": "your_app_secret",
    "encrypt_key": "",
    "verification_token": "",
    "allow_from": ["*"],
    "dm_policy": "open",
    "group_policy": "open",
    "require_mention": false,
    "react_emoji": "THUMBSUP",
    "progress_mode": "milestone"
  },
  "agent": {
    "model": "claude-sonnet-4-6",
    "workspace": "~/.ccbot/workspace",
    "max_turns": 10,
    "idle_timeout": 28800,
    "max_workers": 4,
    "allowed_tools": [],
    "mcp_servers": {},
    "env": {},
    "heartbeat_enabled": true,
    "heartbeat_interval": 1800,
    "heartbeat_notify_chat_id": ""
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

### Supervisor 配置

`examples/supervisor_config.json`：
```json
{
  "feishu": {
    "app_id": "cli_your_app_id",
    "app_secret": "your_app_secret",
    "progress_mode": "milestone"
  },
  "agent": {
    "model": "claude-opus-4-6",
    "workspace": "~/.ccbot/supervisor",
    "max_turns": 10
  }
}
```

### Worker 配置

`examples/worker_config.json`：
```json
{
  "agent": {
    "model": "claude-sonnet-4-6",
    "workspace": "~/.ccbot/worker",
    "max_turns": 30
  },
  "a2a": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8765,
    "name": "worker-1",
    "description": "General purpose worker agent"
  }
}
```

---

## 配置项说明

### feishu（飞书机器人）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `app_id` | string | "" | 飞书应用 ID（必填） |
| `app_secret` | string | "" | 飞书应用密钥（必填） |
| `encrypt_key` | string | "" | 消息加密密钥（可选） |
| `verification_token` | string | "" | 验证令牌（可选） |
| `allow_from` | list | ["*"] | 白名单用户 ID |
| `dm_policy` | string | "open" | 私聊策略 |
| `group_policy` | string | "open" | 群聊策略 |
| `require_mention` | bool | false | 群聊是否需要 @ |
| `react_emoji` | string | "THUMBSUP" | 反应表情 |
| `progress_mode` | string | "milestone" | 进度模式 |

### agent（Agent 配置）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | string | "" | 模型名（空=SDK 默认） |
| `workspace` | string | ~/.ccbot/workspace | Workspace 路径 |
| `max_turns` | int | 10 | 最大轮数 |
| `idle_timeout` | int | 28800 | 空闲超时秒数（默认 8 小时） |
| `max_workers` | int | 4 | 最大并行 worker 数（1-16） |
| `allowed_tools` | list | [] | 允许的工具（空=全部） |
| `mcp_servers` | dict | {} | MCP 服务器配置 |
| `env` | dict | {} | 注入 claude 子进程的额外环境变量 |
| `heartbeat_enabled` | bool | true | 是否启用心跳 |
| `heartbeat_interval` | int | 1800 | 心跳间隔（秒） |
| `heartbeat_notify_chat_id` | string | "" | 心跳通知目标 |
| `system_prompt` | string | "" | 自定义 system prompt |
| `cwd` | string | "" | 工作目录覆盖 |

### a2a（A2A 协议）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | false | 是否启用 A2A 服务器 |
| `host` | string | "0.0.0.0" | 监听地址 |
| `port` | int | 8765 | 监听端口 |
| `name` | string | "ccbot" | Agent 名称 |
| `description` | string | "..." | Agent 描述 |

---

## 常见问题

### Q: 配置文件放在哪里？

**A**: 推荐放在 `~/.ccbot/config.json`，这样不需要每次指定 `--config`。

### Q: 如何管理多个配置？

**A**:
- 单机：使用默认位置 `~/.ccbot/config.json`
- 多机：每台机器用不同的配置文件，通过 `--config` 指定
- 容器：使用环境变量

### Q: 环境变量如何覆盖配置文件？

**A**: JSON 文件优先级高于环境变量。例如：
```bash
# config.json 中 model = "claude-sonnet-4-6"
# 环境变量设置（仅在 JSON 未指定该字段时生效）
export CCBOT_AGENT__MODEL=claude-opus-4-6
```

### Q: 如何验证配置是否正确？

**A**:
```bash
# 查看版本（会加载配置）
ccbot version

# 查看日志
ccbot run --verbose
```

---

## 最佳实践

1. **敏感信息**：使用环境变量存储 `app_id` 和 `app_secret`
2. **版本控制**：配置文件可以提交到 Git，但要排除敏感信息
3. **备份**：定期备份 `~/.ccbot/` 目录
4. **多环境**：开发/生产使用不同的配置文件
