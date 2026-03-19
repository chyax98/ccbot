# Claude Runtime

> 更新时间：2026-03-20
> 作用：统一说明 Claude Agent SDK、`ClaudeSDKClient`、runtime profile、prompt 分层、memory、SDK in-process tools 的设计与落地方式。

## 1. `ClaudeSDKClient` 在链路里的真实作用

`ClaudeSDKClient` 不是普通聊天接口，而是当前 runtime 的执行引擎：

- 持续会话
- query / response event stream
- 中断能力
- 结构化输出承载
- Claude Code 工具使用现场
- 与 LangSmith 官方 tracing 的直接接点

在 `ccbot` 里，它应该承担：

- Supervisor 执行
- Worker 执行
- 会话连续性
- 工具调用与结构化输出

它不应该承担：

- 多 Agent 调度控制面
- Worker 生命周期管理
- 跨 channel 消息编排
- 产品语义解释

这些应该由 `AgentTeam`、`WorkerPool`、`SchedulerService` 等 runtime 组件负责。

## 2. 当前已采用的 Claude SDK 策略

### 2.1 使用 `claude_code` preset

当前 system prompt 统一采用：

- `system_prompt={"type": "preset", "preset": "claude_code", ...}`

这意味着：

- 保留 Claude Code 原生默认能力
- 不自己重造一整套工具说明
- 只在 append 层叠加产品规则和角色约束

### 2.2 显式加载项目级设置

当前统一使用：

- `setting_sources=["project"]`

保证以下内容生效：

- `.claude/CLAUDE.md`
- `.claude/settings.json`
- `.claude/skills/`

这样做的原因是：

- 保持 runtime 行为可复现
- 避免宿主机 `~/.claude/*` 把个人偏好或隐藏规则注入 bot runtime
- 让 prompt/source of truth 尽量留在仓库内可审查的文件里

### 2.3 角色配置集中管理

当前 `Supervisor / Worker / Reviewer` 的 runtime profile 集中在：

- `src/ccbot/runtime/profiles.py`

集中管理：

- preset
- permission mode
- setting sources
- disallowed tools
- role prompt 模板

## 3. Prompt 分层策略

当前建议长期维持五层：

### 第 1 层：Claude Code preset

作用：

- 提供 Claude Code 默认工作方式
- 保留原生工具说明和行为习惯

### 第 2 层：项目级 `.claude/CLAUDE.md`

作用：

- 承载跨角色共享的项目规则
- 承载 channel / 输出 / 调度边界等长期约束
- 承载 skills 的工作约定

### 第 3 层：runtime metadata / reference context

作用：

- 注入 workspace 路径、当前日期等动态但低频变化的信息
- 注入 memory 等参考上下文
- 这些内容是参考数据，不应与 role 指令混成一层语义

实现约束：

- 尽量使用结构化标签
- 尽量保持低频变化，友好 KV cache
- 记忆内容按 `reference-only` 处理，不作为新的最高优先级指令

### 第 4 层：role prompt

作用：

- 定义 Supervisor / Worker / Reviewer 的职责边界
- 定义当前 runtime 的产品化规则

### 第 5 层：extra prompt

作用：

- 只承载极少量调用方追加的高优先级补充说明
- 不应该承载大段共享规则或 memory 数据

## 4. 当前角色模型

### 4.1 Supervisor

职责：

- 理解任务
- 决定 `respond / dispatch`（结构化输出）
- 通过 `mcp__ccbot-runtime__schedule_*` 工具管理定时任务
- 综合 Worker 结果
- 维护记忆
- 对用户负责

### 4.2 Worker

职责：

- 在指定 `cwd` 内执行局部任务
- 聚焦 Supervisor 分配的范围
- 输出给 runtime / Supervisor 的结果，而不是直接面向用户

### 4.3 Reviewer

当前预留但未深用。

适合作为：

- 审查角色
- 风险分析角色
- 偏 `plan` / 建议模式的只读角色

## 5. 当前 runtime boundary

当前必须坚持：

- 不让 Claude Code 原生 `Agent` / `SendMessage` 接管多 Agent 调度
- 不让原生 sub-agent 脱离 `WorkerPool` 生命周期管理

原因：

- 否则会出现两套控制面
- `/workers` 看不到原生 sub-agent
- `/worker stop` / `/worker kill` 无法接管
- 失败时容易直接退化成 Claude CLI 子进程 `exit 1`

## 6. Memory 设计

当前记忆体系分两层：

### 6.1 Claude runtime session resume

- 持久化 `runtime_session_id`
- 下次 Supervisor 重建时尝试 `resume`

### 6.2 本地文件记忆

- 长期记忆：`long_term.md`
- 短期记忆：`conversations/<chat>.json`

设计原则：

- 不完全依赖 Claude 官方召回
- 让产品拥有可检查、可编辑、可纠正的本地记忆面
- memory 注入采用结构化 `reference-only` 上下文，而不是与角色指令混写
- 默认 bootstrap 模板不注入 prompt，避免空白项目也携带无效 boilerplate

## 7. SDK In-Process MCP Tools

### 7.1 机制

Claude Agent SDK 原生支持进程内 MCP server：通过 `@tool` 装饰器定义工具函数，用 `create_sdk_mcp_server()` 注册到 SDK。工具函数在 ccbot 主进程内执行，直接访问内存中的 runtime 组件（如 `SchedulerService`），不经过文件 I/O 或 IPC。

SDK 处理流程：

1. `create_sdk_mcp_server()` 返回 `McpSdkServerConfig(type="sdk", instance=<Server>)`
2. `ClaudeSDKClient` 启动时保留 `instance` 在进程内，CLI 子进程只收到 `{"type": "sdk", "name": "..."}`
3. Claude 调用工具时，SDK 通过 `control_request(subtype="mcp_message")` 路由回 Python 进程
4. 工具函数直接执行，结果即时返回模型上下文

### 7.2 当前已部署的工具

定时任务管理（`src/ccbot/runtime/tools.py`）：

| 工具名 | 参数 | 行为 |
|--------|------|------|
| `mcp__ccbot-runtime__schedule_list` | 无 | 列出所有定时任务（含 paused） |
| `mcp__ccbot-runtime__schedule_create` | name, cron_expr, timezone, prompt, purpose | 创建定时任务 |
| `mcp__ccbot-runtime__schedule_delete` | job_id | 删除定时任务 |
| `mcp__ccbot-runtime__schedule_pause` | job_id | 暂停定时任务 |
| `mcp__ccbot-runtime__schedule_resume` | job_id | 恢复定时任务 |

### 7.3 注入链路

```text
SchedulerService
    ↓ create_runtime_tools(scheduler, get_context)
McpSdkServerConfig
    ↓ team.set_scheduler() → supervisor.set_sdk_mcp_servers()
AgentPool._sdk_mcp_servers
    ↓ _create_client() → merge into kwargs["mcp_servers"]
ClaudeAgentOptions
```

### 7.4 为什么用 in-process tools 替代 structured output

此前 schedule 操作通过 `SupervisorResponse` 的结构化输出（`mode="schedule_create"` / `mode="schedule_manage"`）实现。实际运行中发现 agent 倾向直接 `Edit` jobs.json 文件，导致 `SchedulerService` 内存状态不同步。

工具化解决了这个问题：

- agent 像使用 Read/Write/Bash 一样自然地调用 `schedule_*` 工具
- 工具函数直接操作 `SchedulerService` 内存，保证状态一致
- 不再依赖"最终回复时才触发"的结构化输出，agent 可以在执行过程中随时管理 schedule

### 7.5 扩展方向

- Worker 管理工具化（`worker_list` 等）
- 更多 runtime 能力暴露为工具（memory 操作等）

## 8. 对产品设计的直接启发

- Session 是一等公民，不是每轮都无状态重启
- Role 必须进入运行时，而不是只写在 prompt 文本里
- Dispatch 优先结构化，而不是依赖长久的文本协议
- 产品能力应尽量工具化，而不是长期堆 prompt
- **Runtime 操作必须走工具/API，而不是让 agent 编辑配置文件**

## 9. 推荐阅读顺序

- 产品与架构总览：`docs/PRODUCT_ARCHITECTURE.md`
- 运行与渠道：`docs/CHANNELS_AND_OPERATIONS.md`
- 可观测性与排障：`docs/OBSERVABILITY_AND_TROUBLESHOOTING.md`
