# System Internals — 代码级设计思想全景

> 更新时间：2026-03-14
> 定位：从代码实现中提炼设计思想，覆盖模块职责、数据流、关键设计决策和实现细节。
> 与其他文档的关系：`PRODUCT_ARCHITECTURE.md` 说"为什么这样做"，本文档说"代码里具体怎么做的"。

---

## 1. 一句话总结

ccbot 用最小代价把 Claude Code 子进程编排成多 Agent 协作系统——不重新造 AI runtime，而是在 Claude Agent SDK 之上构建编排层、通道层和持久化层。

---

## 2. 模块全景与代码结构

```
src/ccbot/
├── cli.py                # 命令行入口（typer）
├── config.py             # 配置系统（pydantic-settings）
├── agent.py              # CCBotAgent: 单 Agent 多轮对话封装
├── team.py               # AgentTeam: Supervisor-Worker 编排中枢
├── scheduler.py          # SchedulerService: 轻量 cron 调度器
├── memory.py             # MemoryStore: 三层记忆系统
├── heartbeat.py          # HeartbeatService: 定时巡检
├── workspace.py          # WorkspaceManager: 工作空间管理
├── observability.py      # LangSmith 可观测性集成
│
├── models/               # 结构化数据契约（Agent 间通信协议）
│   ├── supervisor.py     #   SupervisorResponse: respond/dispatch 二择
│   ├── dispatch.py       #   WorkerTask + DispatchPayload + WorkerResult
│   └── schedule.py       #   ScheduleSpec + ScheduledJob + ScheduleControl
│
├── runtime/              # SDK 运行时管理
│   ├── pool.py           #   AgentPool: Supervisor 的 ClaudeSDKClient 池化
│   ├── worker_pool.py    #   WorkerPool: Worker actor 生命周期
│   ├── profiles.py       #   角色 prompt/权限配置
│   ├── sdk_utils.py      #   SDK 交互工具（query、stderr、错误处理）
│   └── tools.py          #   SDK in-process MCP 工具（schedule CRUD）
│
├── channels/             # 消息通道抽象
│   ├── base.py           #   Channel ABC + IncomingMessage
│   ├── cli.py            #   CLI 通道
│   └── feishu/           #   飞书通道
│       ├── adapter.py    #     事件分发（dedup→debounce→queue）
│       ├── parser.py     #     飞书消息 → 纯文本
│       ├── renderer.py   #     Markdown → 飞书 post
│       ├── responder.py  #     飞书 API 封装
│       └── file_service.py #   文件上传服务
│
├── core/                 # 消息处理基建
│   ├── dedup.py          #   消息去重（LRU + JSON 持久化）
│   ├── debounce.py       #   消息防抖（按 key 分组合并）
│   └── queue.py          #   PerChatQueue（per-chat 串行化）
│
└── webui/                # Web 控制台
    └── app.py            #   FastAPI 嵌入式监控
```

---

## 3. 请求主链路

### 3.1 消息入站

```
Feishu WebSocket 事件
    │
    ▼ FeishuAdapter._handle_event()
DedupCache.check(message_id)      ← 去重：已处理则丢弃
    │ 新消息
    ▼
Debouncer.enqueue(event)          ← 防抖：300ms 内连发消息合并
    │ flush
    ▼
PerChatQueue.enqueue(chat_id, handler)  ← 串行化：同一聊天串行处理
    │
    ▼
on_message_context(IncomingMessage, send_progress, send_worker_result)
```

**思想**：IM 机器人场景的三个共性问题——重复投递、连发抖动、并发打架——在通道层统一解决，不污染业务逻辑。

### 3.2 AgentTeam 处理

```python
# team.py: AgentTeam.ask()

# 1. 控制命令短路
control_reply = await self._handle_control_command(chat_id, prompt)
if control_reply is not None:
    return control_reply  # /help /new /stop /workers /schedule /memory

# 2. 注入运行时上下文
context_parts = [f"Current date: {current_date}"]
if worker_status:
    context_parts.append(worker_status)     # 活跃 Worker 列表
if schedule_status:
    context_parts.append(schedule_status)    # 定时任务列表

enhanced_prompt = f"<runtime_context>\n{context}\n</runtime_context>\n\n{prompt}"

# 3. Supervisor 决策
supervisor_result = await self._supervisor.ask_run(chat_id, enhanced_prompt)
structured_response = SupervisorResponse.from_structured_output(...)

# 4. 路由
if mode == "respond":
    return user_message                     # 直接回复
elif mode == "dispatch":
    dispatch = structured_response.dispatch_payload
    # → Worker 派发流程
```

**思想**：
- 控制命令不走模型主链路，直接返回，保证响应速度和确定性
- `<runtime_context>` 每次注入当前状态，让 Supervisor 感知系统全景
- 日期只到天级，减少 KV cache 失效

### 3.3 Supervisor 结构化决策

```python
# models/supervisor.py
class SupervisorResponse(BaseModel):
    mode: Literal["respond", "dispatch"]
    user_message: str
    tasks: list[WorkerTask]
```

通过 Claude Agent SDK 的 `output_format` 要求模型返回 JSON Schema 结构化输出。

**容错链**：
1. 优先解析 `structured_output`（SDK 原生结构化输出）
2. 解析失败 → 回退到文本中提取 `<dispatch>...</dispatch>` XML 标签
3. 两者都失败 → 将 Supervisor 原始文本作为直接回复返回

**思想**：结构化输出是主路径，文本解析是防御性回退。决策层的鲁棒性比严格性更重要。

### 3.4 Worker 派发

**同步模式（CLI）**：

```
dispatch → 并发执行 Worker → 等待全部完成 → Supervisor 综合 → 返回
```

**异步模式（飞书）**：

```
dispatch
    → preload_workers()          ← 确保 Worker 创建完成
    → 立即返回 "已派发 N 个任务"
    → 后台 asyncio.Task:
        → Worker 执行（semaphore 并发控制）
        → on_worker_result() 逐个回调
        → Supervisor 综合
        → on_worker_result("综合", final)
```

**思想**：
- 飞书用户不应等待 Worker 执行（可能数分钟），先返回确认，结果异步推送
- `preload_workers()` 防止竞争：后台 task 启动前 Worker 必须就绪，否则消息队列可能认为处理已完成、放行下一条消息，导致并发创建
- `_track_background_task()` 追踪异步派发的 asyncio.Task，/stop 可中断

---

## 4. Agent 生命周期管理

### 4.1 AgentPool（Supervisor 池）

```
acquire(chat_id) → 创建或复用 ClaudeSDKClient
release(chat_id) → 更新 last_used 时间戳
close(chat_id)   → disconnect + 清理资源

定时清理 loop → 每 60s 检查 → idle > timeout → close
```

**创建 client 的 prompt 构建**：
```
ClaudeAgentOptions.system_prompt = {
    type: "preset",
    preset: "claude_code",      ← 第 1 层：Claude Code 默认行为
    append: join(
        workspace_prompt,        ← 第 2 层：SYSTEM.md 项目级
        role_prompt,             ← 第 3 层：Supervisor/Worker 角色 prompt
        memory_prompt,           ← 第 4 层：短期+长期记忆
    )
}
```

**resume 机制**：
```
有 runtime_session_id → resume=session_id, continue_conversation=True
                       只注入长期记忆（SDK 已恢复对话历史）

无 runtime_session_id → 冷启动
                       注入完整记忆（短期+长期）
```

**思想**：
- 每个 chat_id 对应一个 Claude Code 子进程（~200-500 MB），idle 后必须回收
- resume 时不注入短期记忆，避免与 SDK 恢复的历史重复
- `_safe_disconnect()` 处理 anyio cancel scope 冲突：SDK 内部 cancel scope 在跨 asyncio.Task 调用时会抛 RuntimeError

### 4.2 WorkerPool（Worker 池）

**Actor 模式**：

```python
# 每个 Worker = 一个独立的 asyncio.Task + 命令队列
async def _worker_actor(task_def, info, queue, ready):
    client = await _create_client(task_def)
    ready.set_result(None)  # 通知创建完成

    while True:
        command = await queue.get()
        if isinstance(command, _ExecuteCommand):
            result = await query_and_collect(client, command.task)
            command.future.set_result(result)
        elif isinstance(command, _ShutdownCommand):
            await client.disconnect()
            return
```

**关键特性**：

| 特性 | 实现 |
|------|------|
| 持久化复用 | 同名 Worker 复用已有 actor，不重建 |
| 异常自动重建 | SDK 错误时 disconnect 旧 client，创建新 client 重试一次 |
| 池容量控制 | `max_pooled_workers` 上限，满时回收最久未用的空闲 Worker |
| owner 隔离 | key = `{owner_id}::{name}`，不同用户的同名 Worker 互不干扰 |
| idle 回收 | 每 120s 检查，idle > `worker_idle_timeout` 则 kill |
| 优雅关闭 | ShutdownCommand 通知 actor 退出，pending future 收到 RuntimeError |
| workspace 配置 | 自动在 Worker cwd 复制 `.claude/settings.json` 和 `CLAUDE.md` 模板 |

**思想**：
- Actor 模式天然解耦创建和执行：Worker 存活期间可接收多次任务
- asyncio.Queue 保证命令串行化，无需额外锁
- `_evict_if_needed()` 实现 LRU-idle 回收策略，防止无限制膨胀

---

## 5. 记忆系统

### 5.1 三层架构

```
┌──────────────────────────────────────────────────────────┐
│ Layer 1: Claude Runtime Session                           │
│   Anthropic 服务端保留完整对话历史                          │
│   通过 resume(session_id) 恢复                            │
│   最强但不可控：服务端可能清除过期 session                   │
├──────────────────────────────────────────────────────────┤
│ Layer 2: Short-term Memory                                │
│   本地 JSON：conversations/{chat_id}.json                 │
│   最近 N 轮对话（默认 12 轮）                              │
│   SDK session 不可用时的回退                               │
│   过滤 <runtime_context> 避免历史噪音                      │
├──────────────────────────────────────────────────────────┤
│ Layer 3: Long-term Memory                                 │
│   文件系统：memory/long_term.md                           │
│   由 Supervisor 通过 Read/Write 工具自主维护               │
│   跨 session 持久化的知识和用户偏好                        │
└──────────────────────────────────────────────────────────┘
```

### 5.2 注入策略

| 场景 | 注入内容 | 原因 |
|------|---------|------|
| resume 成功 | 仅长期记忆 | SDK 已恢复对话历史，短期记忆会重复 |
| 冷启动 | 短期 + 长期记忆 | 需要完整上下文 |
| stale session | 清除 session_id，下次冷启动 | 避免永久失败 |

### 5.3 KV Cache 友好

- `_memory_turn_date()` 只保留日期，不带秒级时间戳
- `_strip_runtime_context()` 过滤 `<runtime_context>` 块再存入记忆
- 日期注入放在 `<runtime_context>` 内，每天只变一次

**思想**：记忆系统的核心矛盾是"上下文保留"与"KV cache 命中率"。稳定的 prompt 前缀命中 cache，变化的部分集中在尾部。

---

## 6. 定时任务系统

### 6.1 调度机制

```python
# scheduler.py
async def _tick(self):
    now = datetime.now(UTC)
    for job in self.list_jobs():
        if not job.enabled or job.job_id in self._active_runs:
            continue
        if datetime.fromisoformat(job.next_run_at) <= now:
            self._launch_job(job)
```

- 轮询式（默认 30s），非事件驱动——简单可靠，精度足够
- `_active_runs` 防重入：同一任务不会并发执行
- JSON 文件持久化：重启后恢复任务列表

### 6.2 执行链路

```
到期 → team.ask(f"schedule:{job_id}", job.prompt)
     → Supervisor 独立会话处理（与用户会话隔离）
     → on_notify(job, result) → channel.send(notify_target, ...)
```

### 6.3 两种管理路径

| 路径 | 入口 | 适用场景 |
|------|------|---------|
| SDK MCP 工具 | Supervisor 自然语言调用 `schedule_create` 等 | 对话中创建/管理 |
| 控制命令 | 用户直接 `/schedule list/run/pause/resume/delete` | 直接操控 |

**思想**：
- runtime 操作必须走工具/API，不让 Agent 直接编辑 `jobs.json`
- MCP 工具 + 控制命令双通道：Agent 用工具、用户用命令，互不干扰

---

## 7. 错误处理与恢复策略

### 7.1 SDK 错误分类

```python
# sdk_utils.py
def is_retryable_sdk_error(error):
    # ProcessError: Claude Code 子进程异常退出
    # CLIConnectionError: 连接断开
    # "terminated process" / "processtransport is not ready": 进程状态异常
    → True: 可通过重建 client 恢复
```

### 7.2 重试策略

**Supervisor（agent.py）**：
```
尝试 1: query → 成功 → 返回
              → is_error → 清除 session → 重试
              → 异常 (retryable) → close session → 重试
尝试 2: 冷启动 query → 成功 → 返回
                      → 失败 → 清除 session_id → 返回错误信息
```

**Worker（worker_pool.py）**：
```
尝试 1: query → 成功 → 返回
              → 异常 (retryable) → disconnect → 重建 client → 重试
尝试 2: query → 成功 → 返回
              → 失败 → 抛出异常
```

**思想**：
- 最多重试一次，避免无限循环
- 重试前清除 stale session，确保冷启动
- Supervisor 额外处理 `is_error`（SDK 级别的错误标志，如 resume 与模型不兼容）

### 7.3 跨 Task Cancel Scope

```python
# pool.py
async def _safe_disconnect(self, client, chat_id):
    try:
        await client.disconnect()
    except BaseException as e:
        if "cancel scope" in str(e).lower():
            # anyio cancel scope 在跨 asyncio.Task 调用时冲突
            # 释放引用，让 SDK 子进程退出机制自行清理
            pass
```

**思想**：SDK 内部使用 anyio cancel scope，当 disconnect() 在与 connect() 不同的 asyncio.Task 中调用时会失败。这是已知的框架限制，通过释放引用安全降级。

---

## 8. 飞书通道实现细节

### 8.1 消息处理管线

```
WebSocket 事件
    → 提取 event_type (im.message.receive_v1 / interactive)
    → parser.extract_text(): 飞书富文本 → 纯文本
    → DedupCache.check(message_id): 去重
    → Debouncer.enqueue(): 防抖合并
    → PerChatQueue.enqueue(): per-chat 串行化
    → handler(IncomingMessage, progress, result)
```

### 8.2 确认交互协议

```
Supervisor 输出包含 <<<CONFIRM: 问题 | 选项1 | 选项2>>>
    → adapter 拦截，解析为确认请求
    → responder 发送飞书交互卡片（action buttons）
    → 用户点击按钮
    → WebSocket 收到 interactive 事件
    → 注入 [用户选择: 选项文本] 继续执行
    → 超时（confirm_timeout_s）自动取消
```

### 8.3 进度消息节流

```python
# 双重控制：
progress_silent_s = 30   # 收到消息后 30s 内不发进度
progress_interval_s = 60 # 两次进度消息最小间隔 60s
```

### 8.4 长消息分段

```python
# renderer.py
msg_split_max_len = 3000  # 超过则拆分为多条消息
# Markdown → 飞书 post 格式：代码块、链接、加粗等结构化渲染
```

---

## 9. 配置系统

### 9.1 加载优先级

```
JSON 文件 (~/.ccbot/config.json) > 环境变量 (CCBOT_*) > 默认值
```

### 9.2 关键配置项

| 配置 | 默认值 | 影响 |
|------|--------|------|
| `idle_timeout` | 8 小时 | Supervisor 会话空闲回收 |
| `worker_idle_timeout` | 1 小时 | Worker 空闲回收 |
| `max_workers` | 4 | 并发 Worker 数 |
| `max_pooled_workers` | 8 | Worker 池上限 |
| `short_term_memory_turns` | 12 | 短期记忆保留轮数 |
| `scheduler_poll_interval_s` | 30 | 定时任务检查间隔 |
| `heartbeat_interval` | 1800 | 心跳巡检间隔（30 分钟） |
| `progress_silent_s` | 30 | 进度消息静默期 |
| `confirm_timeout_s` | 300 | 确认按钮超时 |
| `msg_process_timeout_s` | 600 | 消息处理超时 |

---

## 10. 启动链路

```python
# cli.py: ccbot run

config = load_config()
workspace = WorkspaceManager(config.agent.workspace)
channel = FeishuChannel(config.feishu)
team = AgentTeam(config.agent, workspace)

# 接线：channel → team
channel.on_message_context(lambda msg, progress, result:
    team.ask(msg.conversation_id, msg.text, ...))

# 接线：scheduler → team
scheduler = SchedulerService(workspace, schedule_execute, schedule_notify)
team.set_scheduler(scheduler)  # 注入 SDK MCP tools

# 启动顺序
await team.start()          # AgentPool + WorkerPool
await scheduler.start()     # 定时任务轮询
await heartbeat.start()     # 心跳巡检
web_server = create_task()  # 嵌入式 Web 控制台
await channel.start()       # 开始接收消息
await channel.wait_closed() # 阻塞直到关闭

# 关闭顺序（与启动相反）
web_server.cancel()
heartbeat.stop()
scheduler.stop()
channel.stop()
team.stop()
```

**思想**：严格的启动/关闭顺序确保依赖关系正确。team 最先启动最后关闭，因为 channel 和 scheduler 都依赖它。

---

## 11. 测试覆盖

```
tests/
├── channels/            # 通道层测试
│   ├── test_base.py     #   Channel ABC 行为
│   ├── test_cli.py      #   CLI 通道
│   └── test_feishu_responder.py  # 飞书 API 封装
├── core/                # 基建测试
│   ├── test_debounce.py #   防抖逻辑
│   ├── test_dedup.py    #   去重逻辑
│   ├── test_dedup_persist.py  # 去重持久化
│   └── test_queue.py    #   PerChatQueue
├── models/              # 模型测试
│   ├── test_dispatch.py #   WorkerTask + DispatchPayload 解析
│   ├── test_schedule.py #   ScheduleSpec 校验
│   └── test_supervisor.py  # SupervisorResponse 解析
├── runtime/             # 运行时测试
│   ├── test_pool.py     #   AgentPool
│   ├── test_pool_interrupt.py  # 中断行为
│   ├── test_pool_memory.py     # 记忆注入
│   ├── test_worker_pool.py     # WorkerPool
│   ├── test_tools.py    #   SDK MCP 工具
│   └── test_observability.py   # LangSmith 配置
├── test_agent.py        # CCBotAgent 多轮对话
├── test_config.py       # 配置加载
├── test_heartbeat_service.py   # 心跳服务
├── test_memory.py       # 记忆系统
├── test_scheduler.py    # 定时任务
├── test_team.py         # AgentTeam 编排
├── test_team_lifecycle.py      # 生命周期管理
├── test_webui.py        # Web 控制台
└── test_workspace.py    # 工作空间
```

---

## 12. 关键设计决策速查

| # | 决策 | 原因 | 代码位置 |
|---|------|------|---------|
| 1 | Claude Code 子进程 = Agent 执行引擎 | 免费获得文件/Git/代码执行能力，不重造 | runtime/pool.py |
| 2 | 禁用 Agent/SendMessage 工具 | 防止 Claude 原生 sub-agent 脱离 WorkerPool 管理 | runtime/profiles.py:46 |
| 3 | 结构化输出 + 文本回退 | 主路径可靠，回退路径兜底 | team.py + models/supervisor.py |
| 4 | Actor 模式管理 Worker | 解耦创建/执行，天然串行化 | runtime/worker_pool.py:375 |
| 5 | in-process MCP 而非 HTTP MCP | 直接操作内存状态，无序列化开销 | runtime/tools.py |
| 6 | resume 时只注入长期记忆 | 避免与 SDK 恢复的历史重复 | runtime/pool.py:188-192 |
| 7 | 日期只保留天级 | 减少 KV cache 失效 | memory.py + team.py |
| 8 | preload_workers 再后台派发 | 防止消息队列竞争 | team.py |
| 9 | 飞书 dedup→debounce→queue 三层 | 解决 IM 场景重复/抖动/并发 | channels/feishu/adapter.py |
| 10 | runtime 操作走工具不走文件编辑 | 保证内存状态一致 | runtime/tools.py |

---

*本文档与代码同步维护，作为整体链路 review 的基础材料。*
