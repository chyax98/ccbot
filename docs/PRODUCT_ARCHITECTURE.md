# Product & Architecture

> 更新时间：2026-03-14
> 作用：统一说明 `ccbot` 的产品目标、当前架构、阶段边界，以及为什么现在这样设计。

## 1. 产品定义

`ccbot` 不是一个简单聊天壳，而是一个面向个人工作流的 Agent runtime：

- 以 bot channel 为入口
- 以 Claude Agent SDK 为执行内核
- 以真实工作目录为执行现场
- 以 Supervisor 为主脑、WorkerPool 为执行层
- 具备记忆、调度、可观测性和结果回传能力

当前最重要的产品目标不是“协议互联”或“远程集群”，而是把以下三件事做扎实：

- 消息能稳定收、稳定执行、稳定回
- Supervisor 能清楚决策何时直答、何时 dispatch，并通过工具管理 schedule
- Worker 能在真实项目目录里可靠执行，并把结果回到用户链路

## 2. 当前主线架构

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
- `RuntimeRoleProfile`
- `LangSmith observability`

## 3. 模块职责

### 3.1 Channel

入口层负责：

- 接收消息
- 归一化为 `IncomingMessage`
- 处理平台差异
- 提供 `progress`、`worker_result`、文件回传等能力

当前主通道：

- `CLIChannel`：本地预演、回归、开发调试
- `FeishuChannel`：真实 bot 场景

### 3.2 AgentTeam

编排层负责：

- 接收用户输入
- 调用 Supervisor 做结构化决策（`respond / dispatch`）
- 注入 SDK in-process tools（schedule 管理等）
- 派发 Worker
- 汇总 Worker 结果
- 处理 `/help`、`/new`、`/stop`、`/workers`、`/memory`、`/schedule` 等控制命令

### 3.3 Supervisor

Supervisor 是当前产品主脑，负责：

- 理解用户意图
- 判断是否需要拆任务
- 维护长期/短期记忆
- 在 schedule 到点时继续做总控
- 对用户最终结果负责

### 3.4 WorkerPool / Worker

WorkerPool 负责：

- 按名称创建与复用 Worker
- 让 Worker 直接在 `task.cwd` 中执行
- 维护空闲回收
- 支持 `interrupt / kill`

当前设计原则：

- Worker 不做独立长期记忆
- Worker 不运行在虚构二级 workspace
- Worker 结果先回 runtime，再由 Supervisor 或 channel 汇总回用户

### 3.5 Scheduler

Scheduler 负责：

- 持久化 cron job
- 到点触发 `schedule:<job_id>` 对应的 Supervisor 会话
- 把执行结果通知回原始 channel 目标

当前策略：

- 默认创建 Supervisor job
- 到点后由 Supervisor 再决定是否 dispatch Worker
- Schedule 管理通过 SDK in-process tools 暴露给 Supervisor（`mcp__ccbot-runtime__schedule_*`），而非结构化输出
- `/schedule *` 控制命令作为用户直接操控旁路保留

### 3.6 Memory

当前只给 Supervisor 额外记忆：

- `workspace/.ccbot/memory/long_term.md`
- `workspace/.ccbot/memory/conversations/<chat>.json`

能力包括：

- 本地长期记忆
- 本地短期记忆
- Claude runtime session `resume`

## 4. 当前阶段的产品边界

### 4.1 当前优先做扎实的能力

- 连续会话
- Worker 复用
- 结构化 dispatch
- schedule 创建与执行
- 结果回传与可观测性
- CLI 可预演、Feishu 可上线

### 4.2 当前不优先的方向

- A2A / 远程 Agent 网络
- 远程 Worker 集群
- 让 Claude Code 原生 sub-agent 接管控制面
- 过度复杂的多平台抽象

## 5. 为什么现在不做 A2A

当前阶段的主要问题不是协议互联，而是单机 runtime 的稳定性和产品闭环：

- Supervisor / Worker 的控制边界要先稳定
- 消息链路、schedule、memory、observability 要先跑顺
- 过早引入 A2A 会让调度、状态、权限、排障复杂度成倍增加

所以当前结论很明确：

- 先把单机主线打磨到可预演、可回归、可值班
- 再考虑协议层互联

## 6. 当前架构判断

### 已经合理的部分

- `Supervisor -> WorkerPool -> Worker` 的主线清晰
- Worker 直接在真实项目目录运行，符合产品直觉
- 调度权仍留在 runtime，不交给 Claude 原生 sub-agent
- Supervisor 有记忆，Worker 不背长期状态，符合职责分离

### 仍需持续观察的部分

- 调度策略是否足够稳定
- 更多 channel 接入后，抽象边界是否仍然成立
- schedule 与一次性提醒的语义区分是否需要进一步产品化

## 7. 推荐阅读顺序

- Claude SDK / runtime 细节：`docs/CLAUDE_RUNTIME.md`
- 渠道、运行、回归：`docs/CHANNELS_AND_OPERATIONS.md`
- LangSmith 与排障：`docs/OBSERVABILITY_AND_TROUBLESHOOTING.md`
