# Troubleshooting

> 更新时间：2026-03-09
> 目标：出现异常时能快速判断是 Channel、Runtime、Claude SDK、还是配置问题。

## 1. 先看哪三类日志

### 1.1 服务日志

关键模块：

- `ccbot.channels.feishu.adapter`
- `ccbot.channels.feishu.renderer`
- `ccbot.agent`
- `ccbot.runtime.pool`
- `ccbot.runtime.worker_pool`
- `ccbot.scheduler`
- `ccbot.observability`

### 1.2 SDK stderr

当前已经接入 Claude SDK 子进程 stderr。

如果 Claude Code CLI 自己失败，日志会出现类似：

```text
[sdk:supervisor:<chat_id>] STDERR | ...
[sdk:worker:<name>] STDERR | ...
```

这通常比外层的 `exit code 1` 更关键。

### 1.3 LangSmith trace

如果服务日志信息不够，去 LangSmith 看：

- 最后一个 tool call
- structured output
- stop reason
- 哪一轮开始异常

## 2. 常见问题

### 2.1 “抱歉，处理消息时出现错误: Command failed with exit code 1”

含义：

- 外层 `uv run` 可能还活着
- 但内部某个 Claude Code 子进程已经失败退出

优先检查：

1. 是否出现 SDK stderr 日志
2. LangSmith 中失败前最后一个 tool 是什么
3. 是否误触发了当前 runtime 不支持的原生控制路径

当前已知重点：

- Claude Code 原生 `Agent` / `SendMessage` 不能接管当前 `ccbot` 多 Agent 控制面
- 如果 trace 里最后一步是尝试走原生 sub-agent，优先看 role prompt / settings / disallowed tools 是否被意外绕开

### 2.2 Feishu 发消息后长时间无响应

优先检查：

1. 是否只有 progress，没有最终 reply
2. `msg_process_timeout_s` 是否太短或太长
3. Claude trace 是否仍在运行
4. 是否在等待确认卡片点击结果
5. 飞书侧回复是否因线程消息不可用而被降级发送

相关模块：

- `ccbot.channels.feishu.adapter`
- `ccbot.channels.feishu.renderer`

### 2.3 云端看不到 LangSmith 链路

检查：

1. 是否安装了 `langsmith[claude-agent-sdk]`
2. `langsmith_enabled` 是否打开，或环境变量是否启用 tracing
3. `LANGSMITH_API_KEY` / `langsmith_api_key` 是否存在
4. 项目是否看的是正确的 `project`

注意：

- 只有 `ClaudeSDKClient` 会被追踪
- 顶层 `claude_agent_sdk.query()` 不在当前追踪范围

### 2.4 创建定时任务后服务异常

检查：

1. Supervisor 是否生成了合法 `schedule_create`
2. `cron_expr` 是否是合法 5 段 cron
3. `timezone` 是否是合法 IANA 时区
4. `jobs.json` 是否损坏
5. 用户需求是不是其实是“一次性提醒 / 倒计时”，却被误生成为 cron

当前 runtime 已具备：

- 非法 schedule 参数校验
- 坏 `jobs.json` 容错
- 停机时 running job 回落为 `idle`

### 2.5 重启后 Worker / 记忆状态不对

说明：

- Worker 不做独立长期记忆
- Supervisor 才有额外记忆与 `resume`

检查：

- `workspace/.ccbot/memory/long_term.md`
- `workspace/.ccbot/memory/conversations/<chat>.json`
- 当前 chat id 是否发生变化

### 2.6 `/workers` 看不到实际创建的执行体

如果你期待看到 Claude Code 原生 sub-agent，请先确认：

- 当前 runtime 已禁用原生 `Agent` / `SendMessage`
- `/workers` 只展示 `WorkerPool` 托管的 Worker

这不是缺陷，而是当前产品边界。

## 3. 推荐排障顺序

1. 先看服务日志
2. 再看 SDK stderr
3. 再看 LangSmith trace
4. 最后检查 workspace 实际文件状态

## 4. 排障时最有价值的信息

如果要继续定位问题，建议保留并反馈：

- 触发问题的原始用户消息
- 对应 `chat_id` / worker name
- 出错前最后 30~50 行服务日志
- 所有 `[sdk:...] STDERR | ...` 日志
- LangSmith 对应 trace 链接或最后一个 tool 名
