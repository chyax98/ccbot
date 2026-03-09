# ccbot Architecture

> 更新时间：2026-03-09
> 当前结论：`ccbot` 的稳定主线是单机 `Supervisor -> WorkerPool -> Worker`，由 Feishu / CLI 等 Channel 驱动。

## 1. 一句话定义

`ccbot` 不是单纯的聊天壳，而是一个：

- 以 Claude Agent SDK 为执行内核
- 以 bot channel 为入口
- 以真实工作目录为执行现场
- 以 Supervisor 为主脑、WorkerPool 为执行层
- 具备记忆、定时任务和可观测性

的 Agent runtime。

## 2. 当前推荐架构

```text
CLI / Feishu
    ↓
Channel
    ↓
IncomingMessage / ChannelResponder
    ↓
AgentTeam
    ├─ Supervisor (CCBotAgent -> AgentPool -> ClaudeSDKClient)
    ├─ WorkerPool (ClaudeSDKClient)
    ├─ SchedulerService
    └─ MemoryStore
```

共享基础设施：

- `WorkspaceManager`
- `HeartbeatService`
- `LangSmith observability`
- `RuntimeRoleProfile`

## 3. 入口层

### 3.1 CLI

`src/ccbot/cli.py`

提供：

- `ccbot chat`
- `ccbot run`
- `ccbot worker`
- `ccbot version`

其中：

- `chat` 适合本地交互和快速调试
- `run` 适合启动 Feishu / CLI channel 的完整 runtime
- `worker` 是单次 worker 执行入口，主要供外部脚本或调试使用

### 3.2 Channel

当前已实现：

- `CLIChannel`
- `FeishuChannel`

Channel 负责：

- 接收消息
- 规范化为 `IncomingMessage`
- 构造 `ChannelResponder`
- 处理平台差异（线程回复、文件上传、卡片确认、进度更新）

当前产品重点不在“支持多少平台”，而在“把一个平台链路打稳”。
Feishu 已是主验证通道，CLI 是本地预演通道。

## 4. 编排层

### 4.1 AgentTeam

`src/ccbot/team.py`

这是当前产品主脑外层编排器。职责：

- 接收用户输入
- 调用 Supervisor 做结构化决策
- 处理 `respond / dispatch / schedule_create`
- 派发 Worker
- 汇总 Worker 结果
- 执行 `/help`、`/new`、`/stop`、`/workers`、`/worker stop`、`/worker kill`、`/memory`、`/schedule` 等控制命令

### 4.2 Supervisor

Supervisor 当前由：

- `CCBotAgent`
- `AgentPool`
- `ClaudeSDKClient`

组成。

它负责：

- 理解用户任务
- 判断直接回复、拆分执行、还是创建定时任务
- 综合 Worker 结果并返回给用户
- 维护长期/短期记忆上下文
- 在 schedule 到点时继续充当总控，而不是把定时任务直接绑定给某个 Worker

### 4.3 WorkerPool / Worker

`src/ccbot/runtime/worker_pool.py`

WorkerPool 负责：

- 按 `name` 创建和复用 Worker
- 让 Worker 在各自 `cwd` 中运行
- 管理 Worker 的空闲回收
- 支持 `interrupt / kill`

当前设计重点：

- Worker **不维护独立长期记忆**
- Worker 直接把 `task.cwd` 当作运行目录
- 如果该目录缺少 `.claude/`，只补最小模板，不覆盖已有用户配置
- Worker 的结果默认先回给 runtime；异步 dispatch 时，用户会先看到 worker 结果，再看到 Supervisor 的最终综合结论

## 5. Claude Runtime 配置边界

`src/ccbot/runtime/profiles.py`

当前已经把角色运行时配置统一收拢到 `RuntimeRoleProfile`：

- `system_prompt` 使用 `claude_code` preset，并通过 `append` 追加 role prompt
- `setting_sources=["project"]`，确保项目级 `.claude/CLAUDE.md` 与 `settings.json` 生效
- `Supervisor / Worker / Reviewer` 的行为差异由 profile + prompt 文件管理
- 当前显式禁止 `Agent` / `SendMessage`

这意味着角色差异不再依赖“散落的 prompt 字符串”，而是依赖一套统一、可审计的 runtime profile。

## 6. Scheduler 与 Heartbeat

### 6.1 Scheduler

`src/ccbot/scheduler.py`

负责：

- 持久化 cron job 到 `workspace/.ccbot/schedules/jobs.json`
- 轮询到点任务
- 以 `schedule:<job_id>` 的稳定 chat id 触发 Supervisor
- 把结果通知回原 channel 目标

当前策略：

- 默认创建 **Supervisor job**
- 到点后由 Supervisor 再决定是否派发 Worker
- 定时任务的创建入口是自然语言请求，经 Supervisor 输出 `schedule_create`

这符合当前产品目标：

- 让“理解任务”和“执行任务”仍由同一总控负责
- 避免把 schedule 直接绑定到某个孤立 Worker 上，导致失控

### 6.2 Heartbeat

`src/ccbot/heartbeat.py`

负责按周期检查 `HEARTBEAT.md` 中的 Active Tasks。

适合：

- 系统巡检
- 固定周期的维护型任务
- 少量、稳定、长期存在的值班检查项

不适合替代通用 Scheduler。

## 7. 记忆层

`src/ccbot/memory.py`

当前只给 Supervisor 维护额外记忆：

- `workspace/.ccbot/memory/long_term.md`
- `workspace/.ccbot/memory/conversations/<chat>.json`

能力包括：

- 持久化 Claude runtime session id
- 本地短期 turn 记录
- 长期偏好/背景知识注入
- 启动后基于 `resume` 尝试恢复会话连续性

设计判断：

- Supervisor 需要持续扮演“主脑”，所以值得保留连续记忆
- Worker 是执行体，不应该默认背负长期状态

## 8. 可观测性

`src/ccbot/observability.py`

当前使用 LangSmith 官方 Claude Agent SDK 集成。

追踪范围：

- `ClaudeSDKClient` queries
- tool calls
- MCP interactions

当前不会追踪：

- 顶层 `claude_agent_sdk.query()`

但这对 `ccbot` 影响不大，因为当前主链路都使用 `ClaudeSDKClient`。

## 9. Runtime Boundary

当前必须坚持一个核心边界：

- 多 Agent 编排由 `ccbot` runtime 负责
- 不允许 Claude Code 原生 `Agent` / `SendMessage` 接管控制面

原因：

- 否则会出现两套并行控制平面
- 原生 sub-agent 不受 `WorkerPool` 管理
- `/workers` 看不到，`/worker stop` 控不到
- 一旦原生工具失败，Claude CLI 子进程可能直接 `exit 1`

因此当前运行时已显式禁用这些原生控制工具。

## 10. 为什么现在不做 A2A

见：`docs/ARCHITECTURE_REVIEW.md`

简述：

- 当前瓶颈不是协议互联
- 而是 runtime 稳定性、调度正确性、消息链路可控性
- 过早引入 A2A 只会放大复杂度
