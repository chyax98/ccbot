# Changelog

> 维护规则：每次提交在文件**顶部**追加条目，最新在最前。
> 格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### Fixed

- **Prompt 注入链路收敛** — 重新整理 `preset -> project CLAUDE -> runtime metadata/reference context -> role prompt -> extra prompt` 的顺序，去掉 runtime 对宿主机 `user` 级 Claude settings 的依赖，减少 authority 混乱并提升可复现性。(`runtime/profiles.py`, `runtime/pool.py`, `templates/.claude/CLAUDE.md`, `templates/prompts/*.md`)
- **记忆注入结构化与转义** — `MemoryStore` 现以 `reference-only` 结构化块注入长期/短期记忆，对内容做转义，并跳过默认 bootstrap 模板，避免 memory 与角色指令混写、降低 prompt injection 风险并减少无效 token。(`memory.py`)
- **Worker prompt 绑定真实 cwd** — `WorkerPool._create_client()` 现使用解析后的实际工作目录注入 Claude SDK 与 role prompt，避免 `cwd='.'` 时 prompt、项目 settings、实际执行目录三者不一致。(`runtime/worker_pool.py`)
- **CLI 单次 worker 链路去重** — `ccbot worker` 不再额外覆写一整段自定义 system prompt，改为在现有 worker role prompt 之上追加最小的单次执行说明。(`cli.py`)

### Changed

- **目录结构简化** — workspace 扁平化，移除 `workspace/.ccbot/` 嵌套层级。新结构：
  ```
  ~/.ccbot/
  ├── config.json
  ├── .claude/
  ├── memory/
  ├── schedules/
  ├── dedup/
  ├── tmp/
  └── output/
  ```
  - `AgentConfig.workspace` 字段已移除，workspace 路径由 config 文件所在目录推导
  - `WorkspaceManager.runtime_dir` 现在返回 workspace 根目录
  - `MemoryStore` 路径从 `workspace/.ccbot/memory` 改为 `workspace/memory`
  - `SchedulerService` 路径从 `workspace/.ccbot/schedules` 改为 `workspace/schedules`
  - 飞书 channel 的 `dedup_dir`/`tmp_dir` fallback 已移除,强制由 workspace 提供
  - `ccbot onboard` 命令创建扁平目录结构

### Added

- **日志系统增强** — 新增 `LoggingConfig` 配置模块和 `logging_setup.py`：
  - 支持 DEBUG/INFO/WARNING/ERROR 级别
  - 支持控制台和文件双输出
  - 支持 text 和 json 两种格式
  - 支持日志轮转（按大小)和保留(按天数)

### Fixed

- **调度链路：通知失败不再覆盖执行状态** — `_run_job` 中所有 `_on_notify` 调用改为 `_safe_notify`，通知发送失败（如飞书 API 不可用）只记日志，不影响已成功执行的任务状态。此前通知失败会让 `succeeded` 被覆盖为 `failed`。(`scheduler.py`)
- **调度链路：开始通知失败不再阻止任务执行** — 随上述修复一并解决，"⏰ 开始"通知失败后任务仍会正常执行。(`scheduler.py`)
- **调度链路：`_last_request_context` 竞态条件** — `AgentTeam` 中的共享变量 `_last_request_context` 改为 `contextvars.ContextVar`，每个 asyncio.Task 自动隔离。此前并发请求（如用户消息与 schedule 执行同时进行）可能导致 MCP 工具获取到错误的请求上下文。(`team.py`)
- **调度链路：`run_job_now` 不再阻塞调用方** — `scheduler.run_job_now()` 从 `await _run_job()` 改为 `_launch_job()`（fire-and-forget），`/schedule run` 控制命令立即返回"已触发"，不再等待任务执行完毕。(`scheduler.py`)

### Added

- **调度链路：任务执行超时控制** — `_run_job` 中 `_on_execute` 包裹 `asyncio.wait_for`，超时后标记 `failed` 并通知，防止挂起的任务永久占用 `_active_runs` 槽位。新增配置项 `scheduler_job_timeout_s`（默认 1800 秒 / 30 分钟）。(`scheduler.py`, `config.py`, `cli.py`)
- **代码级架构文档** — 新增 `docs/SYSTEM_INTERNALS.md`，从代码实现中提炼设计思想，覆盖请求主链路、Agent 生命周期、记忆系统、定时任务、错误恢复等，包含 12 个关键设计决策速查表。

### Changed

- 测试适配：`test_scheduler.py` 和 `test_scheduler_team.py` 中 `run_job_now` 相关测试改为等待后台 `_run_tasks` 完成后再断言，匹配异步触发行为。

---

## [0.3.0] — 2026-03-14

> 从 v0.3.0 开始追溯记录。此版本包含从项目创建（2026-03-08）至今的全部变更。

### Added — 核心架构

- **Claude Agent SDK 集成** — 以 `ClaudeSDKClient` 作为执行引擎，每个 Agent 对应一个 Claude Code 子进程，提供文件操作、代码执行、Git 等原生工具能力。(`2e8b62f`)
- **Supervisor-Worker 多 Agent 编排** — `AgentTeam` 实现 Supervisor 决策 → Worker 执行 → Supervisor 综合的协作模式。Supervisor 通过结构化输出（`SupervisorResponse`）选择 `respond` 或 `dispatch`。(`dbd5528`, `a2b307a`)
- **AgentPool** — Supervisor 的 `ClaudeSDKClient` 池化管理，按 `chat_id` 缓存复用，空闲超时自动释放。(`7cc2605`)
- **WorkerPool（持久化 Worker）** — Actor 模式管理 Worker 生命周期，Worker 创建后保持存活可接收多次任务。支持 idle 回收、异常自动重建、池容量控制（`max_pooled_workers`）、owner_id 隔离。(`448ac2e`)
- **结构化调度模型** — `DispatchPayload`、`WorkerTask`、`WorkerResult`、`DispatchResult` 等 Pydantic 模型，定义 Agent 间通信协议。(`0f48535`, `0494159`)
- **结构化输出 + 文本回退** — `SupervisorResponse` JSON Schema 结构化输出为主路径，`<dispatch>` XML 文本解析为防御性回退。(`0f48535`)

### Added — 定时任务

- **SchedulerService** — 轻量 cron 调度器，轮询式（默认 30s）检查到期任务，JSON 文件持久化，防重入（`_active_runs`），支持 pause/resume/delete/run_now。(`8bbfa40`, `94f4f59`)
- **SDK in-process MCP 工具** — `schedule_list/create/delete/pause/resume` 五个工具通过 `@tool` + `create_sdk_mcp_server()` 注入 Supervisor，替代此前的结构化输出方式。工具函数直接操作内存中的 `SchedulerService`，保证状态一致。(`7cc6398`, `1025a44`)
- **`/schedule` 控制命令** — 用户可直接通过 `/schedule list/run/pause/resume/delete` 管理定时任务。(`94f4f59`)
- **`ensure_job` 幂等创建接口** — 通过 `system_key` 实现幂等创建/更新，为系统级任务预留。(`8bbfa40`)

### Added — 记忆系统

- **三层记忆架构** — Layer 1: Claude runtime session resume；Layer 2: 本地短期记忆（最近 N 轮对话）；Layer 3: 长期记忆文件（`long_term.md`）。(`756664b`)
- **Resume 机制** — 持久化 `runtime_session_id`，重启后通过 SDK `resume` 参数恢复会话。resume 时只注入长期记忆，冷启动时注入完整记忆。(`756664b`, `01ba306`)
- **Stale session 自动恢复** — SDK 返回 `is_error` 时自动清除过期 session 并冷启动重试。(`e3ecf71`, `91611c6`)
- **KV Cache 友好优化** — 日期只保留天级，`<runtime_context>` 过滤后再存入记忆。(`9a13493`)

### Added — 通道层

- **Channel 抽象** — `Channel` ABC + `IncomingMessage` 标准化 + `ChannelCapability` 声明式能力枚举。(`337801a`)
- **飞书通道** — WebSocket 接入，消息解析（富文本→纯文本）、Markdown 渲染（→飞书 post）、消息分段、表情反应、进度通知、文件上传。(`337801a`, `3b16439`, `f45977c`)
- **飞书交互式确认** — `<<<CONFIRM: ...>>>` 协议，Supervisor 输出被拦截渲染为飞书交互卡片，用户点击后注入选择结果继续执行。(`b4a33d7`)
- **飞书异步派发** — Worker 结果通过 `on_worker_result` 回调逐步发送，用户不必等待全部完成。(`0cff56f`)
- **CLI 通道** — 轻量 REPL，本地开发调试和回归测试。(`7df8342`, `09e7a4b`)
- **消息处理基建** — `DedupCache`（去重 + JSON 持久化）、`Debouncer`（防抖合并）、`PerChatQueue`（per-chat 串行化）。(`49bc69e`, `d1b5598`, `335155a`)

### Added — 基础设施

- **HeartbeatService** — 定时读取 `HEARTBEAT.md` 中的巡检 prompt，调用 Supervisor 执行，结果通知到指定 chat_id。(`756664b`)
- **WorkspaceManager** — 管理 `~/.ccbot/workspace/` 目录结构、system prompt 构建、output/ 目录管理。(`756664b`)
- **LangSmith 可观测性** — 通过 Claude Agent SDK 原生 tracing，支持 project/tags/metadata 配置。(`252a8ee`)
- **FastAPI Web 控制台** — 嵌入 `ccbot run` 进程的监控控制台，显示 Agent 状态、Worker 列表、定时任务列表。(`562a71c`)
- **Typer CLI** — `ccbot run`（完整 runtime）、`ccbot chat`（交互/单次）、`ccbot worker`（单次执行）、`ccbot web`（独立控制台）。(`2e8b62f`)
- **配置系统** — Pydantic Settings，JSON 文件 > 环境变量 > 默认值，嵌套分隔符 `__`。(`b8caa77`, `8b6bb6e`)
- **Docker 支持** — Dockerfile + docker-compose.yml。(`d6af97f`)
- **CI/CD** — GitHub Actions，lint + test + build。(`d6af97f`, `606f4f4`)
- **本地 git hooks** — pre-commit (ruff check + format) + pre-push (pytest + mypy)。(`d9aa28d`)
- **12 个 Supervisor skills** — search, data, deploy, browser automation, summarize, github, sql 等。(`6da9cdc`, `4eadb1f`)

### Added — 运行时安全

- **禁用 Agent/SendMessage 工具** — 防止 Claude 原生 sub-agent 脱离 WorkerPool 管理。(`18a7fc9`, `187ee03`)
- **SDK stderr 捕获** — `StderrCapture` 收集 Claude Code 子进程 stderr 用于诊断。(`187ee03`, `abb4c0f`)
- **SDK 启动环境清理** — `_sanitize_sdk_host_env()` 移除可能破坏嵌套 Claude Code 启动的环境变量。(`abb4c0f`)
- **跨 task cancel scope 处理** — `_safe_disconnect()` 处理 anyio cancel scope 在跨 asyncio.Task 调用时的冲突。(`e3ecf71`)
- **Worker preload** — 后台派发前确保 Worker 创建完成，防止消息队列竞争。(`d43c1a5`)

### Changed

- **A2A/comm 模块删除** — 早期引入的 A2A 协议层和 comm 模块在架构重构中移除，多 Agent 调度回归到 runtime 内编排。(`448ac2e`)
- **nanobot → ccbot 全局重命名** — 项目名、包名、配置前缀全部统一为 ccbot。(`a64d9e4`, `82930a5`, `46c0e3c`)
- **Schedule 管理从结构化输出迁移到 SDK in-process tools** — Agent 不再通过结构化输出的 mode 字段管理 schedule，改为自然语言调用 MCP 工具。(`7cc6398`)
- **进度消息优化** — 双重节流（`progress_silent_s` + `progress_interval_s`），大幅减少飞书消息噪音。(`8bc91ea`)

### Fixed

- **config.env 注入** — 自定义环境变量正确覆盖系统 env，Worker 继承 env 配置。(`6a345d3`, `6e68fb4`)
- **runtime_context 隔离** — 从用户消息中分离 `<runtime_context>`，防止 Supervisor 模式误判。(`f99dee3`)
- **Stale session 清理** — 重试耗尽后清除持久化 session_id，避免下次请求永久失败。(`01ba306`)
- **Worker 会话异常自动重建** — SDK 错误时 disconnect 旧 client，创建新 client 重试一次。(`606f4f4`)
- **mypy 类型检查** — 修复类型注解错误以通过 pre-push hook。(`1c7b111`)

### Tests

- 初始测试套件清理，适配 Claude Agent SDK 架构。(`bb92876`)
- Stale supervisor session recovery 测试覆盖。(`91611c6`)
- 新增 46 个集成测试，覆盖 channel flow、heartbeat、lifecycle、scheduler-team、team-agent-memory、team-worker-dispatch 6 个关键组件边界。(`c826d98`)

---

[Unreleased]: https://github.com/user/ccbot/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/user/ccbot/releases/tag/v0.3.0
