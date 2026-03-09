# Runtime Operations

> 更新时间：2026-03-09
> 面向：本地运行、回归测试、预演、线上值班

## 1. 运行目录

默认主 workspace：

```text
~/.ccbot/workspace
```

关键目录：

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

说明：

- `~/.ccbot/config.json`：主配置
- `workspace/.claude/`：Supervisor 的项目级 Claude 配置
- `workspace/.ccbot/`：运行态数据
- `output/`：产物输出目录

## 2. Worker 运行目录

Worker 不使用主 workspace 作为执行现场。

规则：

- Worker 直接使用 `task.cwd`
- 如果 `task.cwd/.claude/` 已存在，优先尊重用户自己的本地配置
- 如果不存在，框架只补最小模板

这意味着：

- Worker 更像“在用户真实项目目录里工作”
- 不是“在框架虚构的二级 workspace 里工作”
- 框架要做的是调度和可观测，而不是替用户发明新的 repo 结构

## 3. 配置重点

主配置文件：`~/.ccbot/config.json`

建议重点关注：

### 3.1 agent

- `model`
- `workspace`
- `max_turns`
- `max_workers`
- `scheduler_enabled`
- `scheduler_poll_interval_s`
- `heartbeat_enabled`
- `heartbeat_interval`
- `supervisor_resume_enabled`
- `short_term_memory_turns`
- `langsmith_*`

### 3.2 feishu

- `app_id`
- `app_secret`
- `allow_from`
- `require_mention`
- `progress_silent_s`
- `progress_interval_s`
- `confirm_timeout_s`
- `msg_process_timeout_s`
- `ws_reconnect_delay_s`
- `ws_reconnect_max_delay_s`

## 4. 常用启动命令

### 4.1 CLI 对话

```bash
uv run ccbot chat
uv run ccbot chat -m "你好"
```

### 4.2 完整 runtime

```bash
uv run ccbot run --config ~/.ccbot/config.json --channel feishu
```

### 4.3 本地预演

```bash
uv run ccbot run --config ~/.ccbot/config.json --channel cli
```

这条路径非常适合回归：

- 先跑 `/help`、`/new`、`/stop` 等控制命令
- 创建 schedule
- 立即执行 `/schedule run <id>`
- 看 Worker 结果回传，以及最终综合回复是否返回
- 看 runtime 是否能正常退出

## 5. LangSmith 启用

最小方式：

```bash
uv sync --group observability
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=lsv2_xxx
export LANGSMITH_PROJECT=ccbot-dev
```

运行时你会在日志中看到类似提示：

```text
LangSmith tracing 已启用（ClaudeSDKClient 已接入；顶层 claude_agent_sdk.query() 不会被追踪）
```

注意：

- 这是官方能力限制，不是 `ccbot` 自己漏追踪
- `ccbot` 主链路本来就基于 `ClaudeSDKClient`
- 调试时建议同时看本地日志和云端 trace

## 6. 上线前检查清单

### 6.1 代码

```bash
uv run ruff check .
uv run pytest
```

### 6.2 配置

确认：

- `~/.ccbot/config.json` 存在且可读
- Feishu `app_id` / `app_secret` 正确
- LangSmith 项目和 API key 正确
- workspace 路径存在且可写

### 6.3 运行目录

确认：

- `~/.ccbot/workspace/.claude/CLAUDE.md`
- `~/.ccbot/workspace/.claude/settings.json`
- `~/.ccbot/workspace/.ccbot/memory/`
- `~/.ccbot/workspace/.ccbot/schedules/`

## 7. 推荐回归顺序

### 7.1 基础链路

1. 发一条普通消息
2. 确认有回复
3. 确认 LangSmith 有 trace

### 7.2 Worker 链路

1. 发一个明显会 dispatch 的任务
2. 查看 `/workers`
3. 必要时测试 `/worker stop <name>` / `/worker kill <name>`
4. 确认先收到 worker 结果，再收到最终综合回复

### 7.3 Scheduler 链路

1. 用自然语言创建周期性任务
2. `/schedule list`
3. `/schedule run <job_id>`
4. 确认到点执行 / 手动执行都正常

### 7.4 退出链路

1. 正常停止服务
2. 再次启动
3. 确认没有残留坏状态
4. 确认 Supervisor 仍能恢复会话连续性

## 8. 推荐预演剧本

### 8.1 飞书主链路

1. 发普通消息，确认回复正常
2. 发一个需要多步分析但不必 dispatch 的问题
3. 发一个明显需要拆分的任务，确认 Worker 结果逐步返回
4. 创建一个每天执行的 schedule，随后 `/schedule run <job_id>` 做手动验证

### 8.2 CLI 回归链路

1. `uv run ccbot run --channel cli`
2. 输入 `/workers`、`/schedule list`
3. 发一个创建 schedule 的自然语言请求
4. 发一个 dispatch 任务，确认结果综合正常

## 9. 运行时观察点

当前建议重点看：

- 启动日志中的 `Model`、`Workspace`、`LangSmith`
- Supervisor / Worker 的 `sdk stderr`
- `configured_model`、`entrypoint`、`channel`、`workspace`
- tool call 序列
- 失败前最后一个 tool 是什么

## 10. 当前运行边界

- 当前不依赖 A2A
- 当前不依赖远程 Worker
- 当前不允许 Claude Code 原生 `Agent` / `SendMessage` 接管调度
- 当前只为 Supervisor 增加额外记忆
- 当前 scheduler 更适合周期性任务，不适合把“一次性倒计时”强行落成 cron
