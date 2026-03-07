# OpenClaw 可复用方案汇总（按实际场景）

> [文档定位]
> 本文档回答“OpenClaw 哪些思路可迁移到 ccbot，以及在真实使用场景如何落地”。
> 最终架构决策仍以 [ARCHITECTURE_FINAL_PLANS.md](ARCHITECTURE_FINAL_PLANS.md) 为准。

---

## 1. 结论先行

对 ccbot 最有价值、且可快速落地的不是“完整 Gateway 控制平面”，而是 OpenClaw 的四类工程化能力：

1. 入站可靠性链路：持久去重 + 同 chat 串行 + 防抖合并。
2. 访问控制闭环：`dmPolicy/groupPolicy/allowlist/requireMention/pairing` 的组合策略。
3. 会话隔离策略：群聊按 group/sender/topic 维度隔离上下文。
4. 出站可靠性：Outbox(WAL) + retry/backoff + failed queue。

你们当前更适合路线：`ccbot 先做 A（可靠性底座）-> 再做 B（本地调度 + remote transport）`，不建议直接投入 C（完整 A2A 控制平面）。

---

## 2. Claude Agent SDK 适配前提（关键）

为了“可落地”，方案必须基于 Claude Agent SDK 的运行边界，而不是抽象概念。

已确认的 SDK 事实（来自当前虚拟环境 `claude_agent_sdk` 实现）：

1. `ClaudeSDKClient` 是长连接、状态化客户端，`connect()` 到 `disconnect()` 期间内部持有持续任务组，不能跨异步运行时上下文复用。
2. `query(prompt, session_id="default")` 支持多会话路由；`receive_response()` 会在 `ResultMessage` 后结束。
3. 支持运行时控制：
  - `interrupt()` / `stop_task(task_id)`
  - `set_permission_mode()`
  - `set_model()`
  - `reconnect_mcp_server()` / `toggle_mcp_server()`
4. `ClaudeAgentOptions` 支持关键治理字段：
  - `permission_mode`
  - `allowed_tools` / `disallowed_tools`
  - `can_use_tool`（细粒度授权回调）
  - `hooks`
  - `sandbox`
  - `max_budget_usd`
  - `include_partial_messages`
  - `thinking` / `effort`

对 ccbot 的直接影响：

1. 继续保留“每个会话 key 一个 `ClaudeSDKClient` + 每会话锁”的模型（当前实现方向是对的）。
2. 会话 key 不应只看 chat_id，而应由“策略层”决定（group/sender/topic）。
3. 不能继续把 `permission_mode` 固定为 `bypassPermissions` 覆盖全部场景；必须按会话信任级别分档。
4. 应引入 `can_use_tool` 把飞书策略（群/发送者/命令类型）真正落到 SDK 工具执行层。

---

## 3. OpenClaw 可复用能力（按优先级）

## P0：必须先做（1-2 周）

1. 持久去重（重启不丢）
- OpenClaw 参考：
  - `extensions/feishu/src/dedup.ts`（内存 + 持久化 + warmup）
  - `src/plugin-sdk/persistent-dedupe.ts`（文件锁、TTL、并发保护、磁盘失败回退内存）
- ccbot 现状：
  - `src/ccbot/feishu.py` 仅内存 `OrderedDict(1000)`，重启后丢失去重状态。
- 建议：
  - 先用 sqlite 或 json+filelock 实现持久去重；
  - 启动时 warmup 近期键；
  - 增加 in-flight dedupe 防并发重复处理。

2. 同 chat 串行队列 + 跨 chat 并发
- OpenClaw 参考：
  - `extensions/feishu/src/monitor.account.ts` 的 `createChatQueue`（每 chat 串行）。
- ccbot 现状：
  - `src/ccbot/feishu.py` 在 WS 线程直接 `run_coroutine_threadsafe`，同 chat 无严格保序。
- 建议：
  - 引入 per-chat queue；
  - 队列键至少包含 `chat_id`，群 topic 场景再扩展。

3. 入站文本防抖（命令豁免）
- OpenClaw 参考：
  - `src/auto-reply/inbound-debounce.ts`
  - `extensions/feishu/src/monitor.account.ts`（按 chat/sender/thread 合并多条文本，控制命令不防抖）
- ccbot 现状：
  - 无防抖，burst 输入会放大模型调用。
- 建议：
  - 默认 600-1200ms；
  - 仅 text 且非控制命令参与；
  - 合并后要把被吞消息写入 dedupe（避免后续重复）。

4. 策略闭环（DM/Group/Pairing）
- OpenClaw 参考：
  - `extensions/feishu/src/policy.ts`
  - `extensions/feishu/src/bot.ts`（dmPolicy、groupPolicy、group sender allowlist、requireMention）
  - `src/pairing/pairing-store.ts`（配对码、TTL、account scope）
- ccbot 现状：
  - `src/ccbot/config.py` 里有 `dm_policy/group_policy/require_mention` 字段，但 runtime 只做了部分逻辑。
- 建议：
  - 明确决策顺序：group enabled -> group policy -> sender allowlist -> require mention -> dm pairing；
  - pairing store 独立落盘，允许 CLI 审批。
  - 同时把策略结果映射到 SDK：
    - trusted DM: `permission_mode=bypassPermissions`（可选）
    - group/共享会话: `permission_mode=default|plan` + `allowed_tools/disallowed_tools`
    - 高风险会话: 通过 `can_use_tool` 二次拒绝关键工具。

## P1：高收益中期（2-4 周）

1. 群聊会话隔离（topic/sender）
- OpenClaw 参考：
  - `extensions/feishu/src/bot.ts` 的 `groupSessionScope`（`group/group_sender/group_topic/group_topic_sender`）。
- ccbot 现状：
  - 目前主要按 chat 维度，群内多人/多话题容易串上下文。
- 建议：
  - 先支持 `group` 和 `group_sender`；
  - 再支持 `group_topic`（root_id/thread_id）。

2. 出站 Outbox（WAL + retry + failed）
- OpenClaw 参考：
  - `src/infra/outbound/delivery-queue.ts`（持久队列、回放恢复、指数退避、failed 目录）。
- ccbot 现状：
  - `src/ccbot/feishu.py` 发送失败即丢，无重放。
- 建议：
  - 所有 send 先入 outbox；
  - worker 消费发送，失败写 retryCount/lastError；
  - 重启恢复未完成项。

3. Webhook 安全防护（如果用 webhook）
- OpenClaw 参考：
  - `extensions/feishu/src/monitor.transport.ts` + `monitor.state.ts`（请求体大小限制、超时、速率限制、异常计数）。
- ccbot 现状：
  - 当前主路径是 WS，若后续支持 webhook，需补齐基础防护。

4. SDK 流式与中断能力接入
- Claude SDK 参考：
  - `include_partial_messages` + `StreamEvent` 可用于更平滑进度展示。
  - `stop_task(task_id)` 比单纯 `interrupt()` 可控性更强（拿到 task id 后精准停止）。
- ccbot 现状：
  - 当前只消费 `TaskProgressMessage`，未消费 partial stream；
  - `/stop` 仅调用 `interrupt()`。
- 建议：
  - 先保留 `interrupt()`；
  - 中期在收到 `TaskStartedMessage` 后记录 task_id，支持精确 stop；
  - 视飞书体验再决定是否启用 partial stream。

## P2：按需引入（有明确跨机需求再做）

1. API 幂等与 inflight 合并
- OpenClaw 参考：
  - `src/gateway/server-methods/send.ts`（idempotencyKey + inflight map + dedupe cache）。
- 对 ccbot 的价值：
  - A2A/send 重试不重复执行；
  - 相同请求并发只做一次真实处理。

2. 完整控制平面协议（challenge/role/scope）
- OpenClaw 参考：
  - `docs/gateway/protocol.md`（connect challenge、role/scopes、协议版本协商）。
- 建议：
  - 仅在跨机节点、外部客户端、权限分级明确出现时再投入。

---

## 4. 面向真实场景的落地方案（含 SDK 配置建议）

## 场景 A：个人助手（1 人 DM 为主，偶尔群聊）

目标：
- 稳定、低成本、不重复回复。

推荐：
1. P0 全量（持久 dedupe + 串行队列 + 防抖 + 策略闭环）。
2. 群聊先 `require_mention=true`，`group_policy=open` 或小范围 allowlist。
3. A2A 仅保留基础能力，不做控制平面升级。
4. SDK 建议：
  - DM 可用 `bypassPermissions`（默认工具面较宽）；
  - 群聊会话改为 `default` 并收紧 `allowed_tools`。

## 场景 B：小团队群聊协作（多人同群）

目标：
- 不串上下文、可控触发、减少噪音。

推荐：
1. 在场景 A 基础上增加 P1 的会话隔离（至少 `group_sender`）。
2. 群策略设为 `allowlist + require_mention=true`。
3. 为不同群设置独立策略（敏感群 disabled，项目群 allowlist）。
4. SDK 建议：
  - `permission_mode=plan|default`
  - 通过 `can_use_tool` 拒绝高风险工具（如 shell/network 写操作）在群会话执行。

## 场景 C：客服/运营（多账号、多群、高频消息）

目标：
- 可恢复、可追踪、重启不丢消息。

推荐：
1. 必做 P1 出站 Outbox（WAL + retry + failed）。
2. 引入账号维度隔离（pairing/allowlist 按 accountId 存储）。
3. 加基础可观测：重复率、发送失败率、恢复成功率。
4. SDK 建议：
  - 增加 `max_budget_usd` 防止 burst 造成成本失控；
  - 低信任群启用更严格 `disallowed_tools`。

## 场景 D：研发协同（需要跨机 worker）

目标：
- 保持当前本地调度体验，同时逐步接入远程执行。

推荐：
1. 先实现 `dispatch_task(transport=local|remote)` 抽象；
2. 默认 local，remote 通过现有 A2A 承载；
3. 给 remote 增加最小能力：token auth、idempotency key、inflight 合并；
4. 暂不做完整 Gateway 协议栈。
5. SDK 建议：
  - remote worker 使用更低权限 profile；
  - supervisor 本地会话可保持较高工具权限，但通过策略限制 remote。

---

## 5. 对 ccbot 的具体改造映射

1. `src/ccbot/feishu.py`
- 拆分为：`dedup.py / queue.py / debounce.py / policy.py / handler.py / send_outbox.py`
- `FeishuBot` 仅保留装配与生命周期。

2. `src/ccbot/config.py`
- 补齐可运行字段：
  - `group_sender_allow_from`
  - `group_session_scope`
  - `inbound_debounce_ms`
  - `outbox_enabled/state_dir/retry_policy`
  - `a2a.token`、`a2a.idempotency_ttl_ms`
  - `agent.permission_mode`
  - `agent.disallowed_tools`
  - `agent.max_budget_usd`
  - `agent.include_partial_messages`
  - `agent.thinking` / `agent.effort`
  - `agent.sandbox`

3. `src/ccbot/server.py`
- 在 `message/send` 增加 `idempotencyKey`；
- 增加最小 token 校验和 inflight 合并。

4. `src/ccbot/team.py`
- 保持本地调度主路径；
- 追加 remote transport 适配，不改 supervisor prompt 协议。

---

## 6. A2A 取舍（结合你们当前阶段）

结论：
1. 现在不做“完整 A2A 控制平面”。
2. 先把 A2A 当 remote transport（工具层）来用。
3. 把复杂度放在消息可靠性与策略闭环，而不是先堆协议层。

判定进入“完整 A2A 控制平面”的触发条件：
1. 长期运行的跨机 worker 数量稳定 > 3。
2. 需要多角色权限（operator/node）和统一会话治理。
3. 需要对外开放多客户端接入，不只是内部节点互调。

---

## 7. 验收指标（建议）

1. 重复处理率：`< 0.1%`
2. 同 chat 乱序率：`0`
3. 出站恢复成功率（重启后）：`> 99%`
4. 非授权触发拦截率：`100%`
5. 群 topic/sender 串上下文投诉：显著下降（可用 issue 数量追踪）
