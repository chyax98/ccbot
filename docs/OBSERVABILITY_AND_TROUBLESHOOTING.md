# Observability & Troubleshooting

> 更新时间：2026-03-09
> 作用：统一说明 LangSmith 接入、运行时观察点、常见故障、排障顺序与值班建议。

## 1. LangSmith 接入现状

当前 `ccbot` 使用 LangSmith 官方 Claude Agent SDK 集成。

追踪范围：

- `ClaudeSDKClient` queries
- tool calls
- MCP interactions

限制：

- 顶层 `claude_agent_sdk.query()` 不在追踪范围

这不是 `ccbot` 的缺陷，而是官方集成边界。

## 2. 安装与启用

### 最简安装

```bash
pip install 'langsmith[claude-agent-sdk]'
```

或使用项目工作流：

```bash
uv sync --group observability
```

### 启用方式

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=lsv2_xxx
export LANGSMITH_PROJECT=ccbot-dev
```

或在配置文件中显式开启。

## 3. 当前建议看的观察面

### 本地服务日志

重点模块：

- `ccbot.channels.feishu.adapter`
- `ccbot.channels.feishu.renderer`
- `ccbot.agent`
- `ccbot.runtime.pool`
- `ccbot.runtime.worker_pool`
- `ccbot.scheduler`
- `ccbot.observability`

### SDK stderr

出现 Claude Code CLI 内部失败时，优先看：

```text
[sdk:supervisor:<chat_id>] STDERR | ...
[sdk:worker:<name>] STDERR | ...
```

### LangSmith trace

重点看：

- 最后一个 tool call
- structured output
- stop reason
- 哪一轮开始异常
- `configured_model` / `entrypoint` / `workspace`

## 4. 常见问题

### 4.1 `exit code 1`

症状：

- 外层 `uv run` 还活着
- 内部 Claude Code 子进程已失败

优先检查：

1. SDK stderr
2. LangSmith 最后一个 tool
3. 是否误触发原生 `Agent` / `SendMessage`

### 4.2 Feishu 发消息后长时间无响应

优先检查：

1. 是否只有 progress，没有最终 reply
2. `msg_process_timeout_s` 是否合适
3. trace 是否仍在运行
4. 是否卡在确认交互
5. 是否线程回复失败后降级发送

### 4.3 LangSmith 云端看不到链路

检查：

1. 是否安装 `langsmith[claude-agent-sdk]`
2. tracing 是否启用
3. API key / project 是否正确
4. 当前入口是否真的用的是 `ClaudeSDKClient`

### 4.4 创建 schedule 后服务异常

检查：

1. `schedule_create` 是否合法
2. `cron_expr` 是否是合法 5 段 cron
3. `timezone` 是否是合法 IANA 时区
4. `jobs.json` 是否损坏
5. 用户需求是否其实是一次性提醒而非周期任务

### 4.5 重启后记忆或 Worker 状态不符合预期

说明：

- Worker 不做独立长期记忆
- Supervisor 才有额外记忆与 `resume`

检查：

- `workspace/.ccbot/memory/long_term.md`
- `workspace/.ccbot/memory/conversations/<chat>.json`
- 当前 chat id 是否变化

### 4.6 `/workers` 看不到执行体

先确认：

- 当前 runtime 已禁用 Claude 原生 sub-agent 控制面
- `/workers` 只展示 `WorkerPool` 托管的 Worker

## 5. 推荐排障顺序

1. 先看服务日志
2. 再看 SDK stderr
3. 再看 LangSmith trace
4. 最后看 workspace 实际文件状态

## 6. 值班时最有价值的信息

建议保留：

- 原始用户消息
- `chat_id` / worker name
- 出错前最后 30~50 行日志
- 全部 `[sdk:...] STDERR | ...`
- LangSmith trace 链接或最后一个 tool 名

## 7. 当前运行边界

- LangSmith 的官方 tracing 已足够覆盖当前主链路
- 当前优先级是让排障路径可重复、可定位、可回放
- 不要把“增加更多监控系统”误当成“解决运行时不稳定”
