# ccbot 基于 Claude Agent SDK 的适配架构方案

> [文档定位]
> 本文档是 ccbot 的 Claude Agent SDK 适配设计主文档，聚焦“可上线、可演进”的工程方案。
> OpenClaw 仅作为能力参考，最终设计以 ccbot 的运行模式为中心。

---

## 1. 目标与原则

目标：
1. 飞书消息处理稳定（不重、不乱、不丢）。
2. Agent 运行可控（权限、成本、并发、可中断）。
3. 架构支持从单机平滑演进到跨机 worker。

原则：
1. 先稳定消息总线，再扩展控制平面。
2. 先用 SDK 已有能力（session_id / permission / stop_task），避免自造协议。
3. 高风险能力必须可配置、可观测、可回滚。

---

## 2. Claude Agent SDK 运行模型（设计约束）

已确认约束（基于当前项目依赖版本）：
1. `ClaudeSDKClient` 是状态化长连接客户端，生命周期是 `connect -> query/receive -> disconnect`。
2. `query(prompt, session_id)` 支持同 client 多会话路由；`receive_response()` 到 `ResultMessage` 结束。
3. 支持运行时控制：`interrupt()`、`stop_task(task_id)`、`set_permission_mode()`、`set_model()`。
4. `ClaudeAgentOptions` 可治理字段包括：
   - `permission_mode`
   - `allowed_tools` / `disallowed_tools`
   - `can_use_tool`
   - `sandbox`
   - `max_budget_usd`
   - `include_partial_messages`
   - `thinking` / `effort`

对架构的硬性影响：
1. 会话键必须稳定且可计算（不能只靠 chat_id）。
2. 权限要“按会话分层”，不能全局固定 `bypassPermissions`。
3. 中断要有两级机制：快速 interrupt + 精确 stop_task。

---

## 3. 目标架构（单机主线）

```text
Feishu Inbound
  -> Inbound Bus
     (Persistent Dedup -> Debounce -> Per-Session Queue -> Policy Gate)
  -> Session Resolver
  -> Agent Runtime (Claude Agent SDK)
  -> Outbound Bus
     (Outbox WAL -> Deliver -> Retry/Backoff -> Failed Queue)
  -> Feishu Outbound
```

模块职责：
1. Inbound Bus：保证消息入口可靠、可控。
2. Session Resolver：将飞书上下文映射为稳定 session key。
3. Agent Runtime：封装 SDK client 池、权限 profile、中断机制。
4. Outbound Bus：保证发送可恢复。

---

## 4. Session Key 设计（核心）

推荐支持四种 scope：
1. `group`：`feishu:{chat_id}`
2. `group_sender`：`feishu:{chat_id}:sender:{sender_id}`
3. `group_topic`：`feishu:{chat_id}:topic:{root_or_thread_id}`
4. `group_topic_sender`：`feishu:{chat_id}:topic:{root_or_thread_id}:sender:{sender_id}`

DM：
1. `feishu:dm:{sender_id}`

设计要求：
1. 同一语义上下文必须映射到同一 key。
2. topic 首条和后续跟帖 key 一致（优先 `root_id`）。
3. key 生成逻辑统一在 `SessionResolver`，禁止分散拼接。

---

## 5. SDK 权限分层（Profile）

定义三档 profile：

1. `trusted_dm`
- 场景：已配对私聊、可信用户。
- 建议：
  - `permission_mode=bypassPermissions`（可选）
  - 工具集合较宽
  - 仍保留关键工具 deny 列表

2. `shared_group`
- 场景：群聊、多人协作。
- 建议：
  - `permission_mode=default|plan`
  - 收紧 `allowed_tools`
  - `can_use_tool` 基于 sender/group 二次决策

3. `remote_worker`
- 场景：远程 worker 或低信任执行。
- 建议：
  - `permission_mode=default`
  - 最小工具集合
  - `sandbox` 强制开启

策略映射关系：
1. `dm_policy/group_policy/allowlist/require_mention` 决定 profile。
2. profile 决定 SDK options，不在业务代码里分散判断。

---

## 6. 消息总线设计

## 6.1 入站总线

顺序：
1. Persistent Dedup（内存+落盘）
2. Debounce（仅文本且非控制命令）
3. Per-Session Queue（同 session 串行）
4. Policy Gate（DM/Group/Pairing/Mention）

关键点：
1. dedup 必须跨重启生效。
2. 防抖合并后，被吞消息也需记 dedup。
3. queue 键使用 session key，不是 chat_id。

## 6.2 出站总线

顺序：
1. Outbox WAL 入盘
2. 发送执行
3. 失败重试（指数退避）
4. 超限进 failed queue
5. 启动恢复扫描

关键点：
1. “先入盘后发送”，防止进程崩溃丢回复。
2. 恢复按 `enqueuedAt` 顺序，避免乱序放大。

---

## 7. 中断与任务控制

两级停止机制：
1. 快速停止：`interrupt()`（立即打断当前流）
2. 精确停止：记录 `TaskStarted` 的 `task_id`，调用 `stop_task(task_id)`

建议行为：
1. `/stop` 先调用 `interrupt()`。
2. 若存在活跃 task_id，再调用 `stop_task` 确认收敛。

---

## 8. A2A 与调度边界

结论：
1. 当前阶段不做完整 A2A 控制平面。
2. 将 A2A 定位为 `remote transport`，而不是主控制总线。

落地方式：
1. 统一调度接口：`dispatch_task(transport=local|remote)`。
2. 默认 local，remote 通过现有 A2A 端点。
3. A2A 增补最小能力：
   - token auth
   - `idempotencyKey`
   - inflight 合并

何时升级为完整控制平面：
1. 跨机常驻 worker 明显增加；
2. 多角色权限与多客户端接入成为刚需；
3. 需要统一协议治理与强审计。

---

## 9. 配置模型建议（新增项）

`agent`：
1. `permission_mode`
2. `disallowed_tools`
3. `max_budget_usd`
4. `include_partial_messages`
5. `thinking`
6. `effort`
7. `sandbox`

`feishu`：
1. `group_session_scope`
2. `group_sender_allow_from`
3. `inbound_debounce_ms`

`runtime`：
1. `outbox_enabled`
2. `state_dir`
3. `retry_policy`

`a2a`：
1. `token`
2. `idempotency_ttl_ms`

---

## 10. 分阶段实施

## Phase 1（P0）
1. 持久 dedup
2. session queue
3. debounce
4. 策略闭环

验收：
1. 重复处理率 < 0.1%
2. 同 session 乱序率 = 0

## Phase 2（P1）
1. session scope 四模式
2. SDK profile 分层
3. `can_use_tool` 接入

验收：
1. 群聊串上下文显著下降
2. 高风险工具在群会话可拦截

## Phase 3（P1）
1. Outbox WAL
2. retry/backoff
3. failed queue + 启动恢复

验收：
1. 重启后回复恢复成功率 > 99%

## Phase 4（P1-P2）
1. A2A 最小增强（token + idempotency + inflight）
2. `dispatch_task(local|remote)` 抽象

验收：
1. remote 重试不重复执行
2. local/remote 可无业务改动切换

---

## 11. 测试与可观测

测试分层：
1. 单元测试：dedup/queue/debounce/policy/session resolver
2. 集成测试：飞书入站到 SDK 回包全链路
3. 故障测试：重启恢复、发送失败重试、A2A 幂等

指标建议：
1. inbound_dedup_hit_rate
2. inbound_queue_lag_ms
3. outbound_retry_count
4. outbound_failed_count
5. sdk_interrupt_count / sdk_stop_task_count
6. policy_deny_count（按原因分组）

---

## 12. 风险与回滚

主要风险：
1. 权限收紧导致历史流程不可用。
2. 防抖误合并导致语义损失。
3. Outbox 恢复时重复发送。

回滚策略：
1. 所有新能力开关化（feature flags）。
2. 先灰度到指定 chat/group。
3. 保留旧路径一版，按配置快速切回。

---

## 13. 最终建议

1. 先文档收敛，再按 Phase 逐步实现，不并行大改。
2. 以 Claude Agent SDK 能力做“约束内优化”，不要先造复杂中台。
3. A2A 只做 transport 增强，控制平面升级延后到明确业务触发。

