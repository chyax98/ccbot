# Claude Runtime

> 更新时间：2026-03-09
> 作用：统一说明 Claude Agent SDK、`ClaudeSDKClient`、runtime profile、prompt 分层、memory 的设计与落地方式。

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

当前建议长期维持三层：

### 第 1 层：Claude Code preset

作用：

- 提供 Claude Code 默认工作方式
- 保留原生工具说明和行为习惯

### 第 2 层：项目级 `.claude/CLAUDE.md`

作用：

- 承载项目规则
- 承载 channel / 输出 / 调度边界等长期约束
- 承载 skills 的工作约定

### 第 3 层：role prompt

作用：

- 定义 Supervisor / Worker / Reviewer 的职责边界
- 定义当前 runtime 的产品化规则

## 4. 当前角色模型

### 4.1 Supervisor

职责：

- 理解任务
- 决定 `respond / dispatch / schedule_create`
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

## 7. 还没充分用到、但值得保留视角的 SDK 能力

- 更细粒度的权限收缩
- 更丰富的 MCP / custom tools
- Reviewer 等角色化 profile 扩展
- 更正式的 runtime control / rewind / checkpointing 策略

但当前顺序仍应是：

- 先把现有主链路用对、用稳
- 再扩充高阶能力

## 8. 对产品设计的直接启发

- Session 是一等公民，不是每轮都无状态重启
- Role 必须进入运行时，而不是只写在 prompt 文本里
- Dispatch 优先结构化，而不是依赖长久的文本协议
- 产品能力应尽量工具化，而不是长期堆 prompt

## 9. 推荐阅读顺序

- 产品与架构总览：`docs/PRODUCT_ARCHITECTURE.md`
- 运行与渠道：`docs/CHANNELS_AND_OPERATIONS.md`
- 可观测性与排障：`docs/OBSERVABILITY_AND_TROUBLESHOOTING.md`
