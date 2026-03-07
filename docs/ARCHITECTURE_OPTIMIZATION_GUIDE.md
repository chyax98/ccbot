# ccbot 架构优化详细方案

> [文档定位]
> 本文档提供实施细节；最终架构决策请以 [ARCHITECTURE_FINAL_PLANS.md](ARCHITECTURE_FINAL_PLANS.md) 为准。

面向目标：
- 把当前 `FeishuBot -> AgentTeam` 的直连模式，升级为“控制面 + 通道适配层 + 消息总线”的可持续架构。
- 在不破坏现有使用方式的前提下，分阶段补齐可靠性、会话隔离、安全与可观测性。

适用版本：
- 当前基线：`ccbot`（2026-03-08 仓库状态）
- 参考对象：`openclaw` 的 Gateway + Feishu 插件实现

---

## 1. 现状与主要差距

### 1.1 当前架构（简化）

```text
Feishu WS Event
  -> FeishuBot._on_message
    -> on_message_cb(...)
      -> AgentTeam.ask(chat_id, text)
        -> Supervisor
        -> optional worker dispatch
      -> FeishuBot.send(...)
```

关键位置：
- 飞书入口与处理：[src/ccbot/feishu.py](/Users/Apple/share/ccbot/src/ccbot/feishu.py:832)
- Agent 编排：[src/ccbot/team.py](/Users/Apple/share/ccbot/src/ccbot/team.py:80)
- A2A Server：[src/ccbot/server.py](/Users/Apple/share/ccbot/src/ccbot/server.py:62)

### 1.2 与目标架构的差距

1. 可靠性不足
- 入站去重仅内存 `OrderedDict(1000)`，重启丢失：[feishu.py](/Users/Apple/share/ccbot/src/ccbot/feishu.py:256)
- 无持久出站队列、无失败重放机制

2. 入站顺序与背压不足
- WebSocket 线程直接 `run_coroutine_threadsafe`，同 chat 无串行保证：[feishu.py](/Users/Apple/share/ccbot/src/ccbot/feishu.py:835)
- 无防抖合并（burst message 会放大模型调用成本）

3. 会话与策略层薄弱
- 群会话维度单一（缺少 topic/sender 粒度）
- `group_policy` 等配置字段存在，但未形成完整 runtime 策略闭环：[config.py](/Users/Apple/share/ccbot/src/ccbot/config.py:57)

4. 控制面能力弱
- A2A 仅基础 RPC/SSE，无鉴权、幂等与请求级 dedupe：[server.py](/Users/Apple/share/ccbot/src/ccbot/server.py:62)

5. 工程一致性问题
- `ccbot`/`nanobot` 命名、默认路径、版本号不一致：
  - README 使用 `~/.ccbot`：[README.md](/Users/Apple/share/ccbot/README.md:36)
  - 配置默认 `~/.nanobot`：[config.py](/Users/Apple/share/ccbot/src/ccbot/config.py:11)
  - 版本双源：[`__init__.py`](/Users/Apple/share/ccbot/src/ccbot/__init__.py:6) vs [`pyproject.toml`](/Users/Apple/share/ccbot/pyproject.toml:3)

---

## 2. 目标架构与设计原则

### 2.1 目标架构（分层）

```text
                    +------------------------------+
                    |         Control Plane        |
                    |  (API / idempotency / auth)  |
                    +--------------+---------------+
                                   |
             +---------------------v---------------------+
             |             Channel Adapter               |
             |    FeishuTransport + EventDispatcher      |
             +---------------------+---------------------+
                                   |
              Inbound Bus          |          Outbound Bus
       +---------------------------+-----------------------------+
       |  Dedup (mem+sqlite) -> Debounce -> Per-chat Queue      |
       |  PolicyEngine -> SessionResolver -> AgentRuntime        |
       |  Outbox WAL Queue -> Deliver -> Retry/Backoff -> DLQ    |
       +---------------------------+-----------------------------+
                                   |
                         +---------v---------+
                         |  AgentTeam/Core   |
                         | Supervisor+Worker |
                         +-------------------+
```

### 2.2 设计原则

1. 先可靠，再复杂
- 先做“重复消息不处理、消息顺序正确、失败可恢复”，再做高级路由与扩展功能。

2. 明确边界
- 通道适配层只做“协议解析与发送”。
- 策略、会话、调度、队列在核心层统一处理。

3. 所有副作用可重试
- 需要落盘状态必须原子写入，业务请求具备幂等键。

4. 兼容迁移
- 新能力默认关闭或兼容旧行为，通过配置开关渐进启用。

---

## 3. 模块拆分方案

建议新增目录：

```text
src/ccbot/
  control/
    api.py                    # A2A + internal control methods
    idempotency.py            # request-level dedupe/inflight
    auth.py                   # token / signature / allow scopes
  bus/
    inbound.py                # pipeline orchestration
    outbound.py               # send orchestration
    queue.py                  # per-chat serial queue
    debounce.py               # burst merge
    dedup.py                  # memory + sqlite dedupe
    outbox.py                 # WAL outbox + retry + DLQ
  session/
    key.py                    # session key format/parse
    resolver.py               # group/topic/sender routing
    store.py                  # session metadata persistence
  policy/
    dm.py                     # DM policy (open/pairing/allowlist)
    group.py                  # group policy + sender allowlist
    pairing.py                # pairing request lifecycle
  channels/
    feishu/
      transport.py            # ws/webhook transport
      parser.py               # inbound normalize
      sender.py               # outbound send/edit/media
      media.py                # upload/download abstraction
      card.py                 # card render/split
      adapter.py              # channel adapter entry
  runtime/
    orchestrator.py           # wrap AgentTeam ask + progress
    metrics.py                # counters/latency/error
```

现有 `src/ccbot/feishu.py` 逐步拆分为上述模块，最终仅保留 façade（兼容旧调用）。

---

## 4. 核心优化项（详细）

## 4.1 入站可靠性：持久去重 + 串行队列 + 防抖

### A. 去重（Dedup）

目标：
- 保证“至少一次投递”环境下的幂等处理。
- 进程重启后仍能识别最近已处理消息。

实现：
- 内存 LRU + SQLite 双层。
- 键：`{account_id}:{message_id}`。
- TTL 默认 24h，可配置。

SQLite 表建议：

```sql
CREATE TABLE IF NOT EXISTS inbound_dedup (
  dedup_key TEXT PRIMARY KEY,
  seen_at INTEGER NOT NULL,
  expire_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbound_dedup_expire ON inbound_dedup(expire_at);
```

验收：
- 同一 message_id 在重启前后都不会二次进入 Agent。

### B. Per-chat 串行队列

目标：
- 同一 chat 严格保序，不同 chat 并行。

实现：
- `Map[chat_key, PromiseChain]` 语义在 Python 中用 `asyncio.Task` 链实现。
- `chat_key` 取 `chat_id`，必要时拼 `thread_id`。

验收：
- 并发注入同 chat 10 条消息，Agent 处理顺序与入站顺序一致。

### C. 防抖（Debounce）

目标：
- 合并短时间 burst 输入，降低 token/调用量。

实现建议：
- 仅文本消息防抖，默认 300ms。
- 控制命令（`/reset`、`/new`、`/help`）不防抖。

验收：
- 300ms 内 5 条普通文本合并为 1 次调度。

---

## 4.2 会话路由：支持 group/topic/sender 维度

引入 `group_session_scope` 配置：
- `group`: 群共享会话
- `group_sender`: 群内按用户会话
- `group_topic`: 群话题会话
- `group_topic_sender`: 群话题+用户会话

推荐 Session Key 规范：
- `agent:{agent_id}:direct:{sender_id}`
- `agent:{agent_id}:feishu:group:{chat_id}`
- `agent:{agent_id}:feishu:group:{chat_id}:topic:{root_id}`
- `agent:{agent_id}:feishu:group:{chat_id}:topic:{root_id}:sender:{sender_id}`

验收：
- 同群不同话题上下文不串。
- 同话题不同用户在 `group_topic_sender` 下不串。

---

## 4.3 策略与权限：DM/群聊策略闭环

统一策略引擎输入：
- `chat_type`, `chat_id`, `sender_id`, `mentions`, `command`, `account_id`

策略模型：
1. DM
- `open`：全部放行
- `pairing`：未授权用户发 pairing code
- `allowlist`：仅 allow_from

2. Group
- `open` / `allowlist` / `disabled`
- 可选 `group_allow_from`
- 可选 `groups.{chat_id}.allow_from`（群内发送者白名单）
- 可选 `require_mention`

配对状态存储（SQLite）：

```sql
CREATE TABLE IF NOT EXISTS pairing_requests (
  channel TEXT NOT NULL,
  sender_id TEXT NOT NULL,
  code TEXT NOT NULL,
  status TEXT NOT NULL, -- pending/approved/expired
  created_at INTEGER NOT NULL,
  expire_at INTEGER NOT NULL,
  PRIMARY KEY(channel, sender_id)
);
```

验收：
- DM 未授权触发 pairing，不进入 Agent。
- 群 sender allowlist 生效，非授权成员被拒绝。

---

## 4.4 出站总线：WAL Outbox + Retry + DLQ

目标：
- 避免“模型已生成回复但发送失败导致消息丢失”。

机制：
1. 发送前写 Outbox（WAL）
2. 发送成功后 ACK 删除
3. 失败计数 + 指数退避重试
4. 超过阈值进入 DLQ
5. 进程启动执行恢复扫描

目录建议：
- `~/.ccbot/state/outbox/*.json`
- `~/.ccbot/state/outbox/failed/*.json`

记录结构（JSON）：
- `id`, `channel`, `target`, `payloads`, `retry_count`, `last_error`, `next_retry_at`

验收：
- kill 进程后重启，未送达消息自动恢复继续投递。

---

## 4.5 控制面增强：A2A 鉴权 + 幂等 + inflight 合并

现状：`/rpc` 接口可用但无 auth/幂等控制。

增强项：
1. 鉴权
- `Authorization: Bearer <token>`（最小方案）
- 后续可升级签名挑战机制

2. 幂等键
- `message/send`、`message/stream` 要求 `idempotencyKey`
- 同 key 返回缓存结果

3. inflight 合并
- 相同 key 并发请求共享同一执行 Future

4. 统一错误码
- `INVALID_REQUEST`、`UNAUTHORIZED`、`UNAVAILABLE`、`TIMEOUT`

验收：
- 同 key 重试不重复触发 Agent。
- 无 token 请求返回 401/403。

---

## 4.6 工程一致性与配置治理

### A. 命名与路径统一

统一为 `ccbot`：
- 默认配置路径：`~/.ccbot/config.json`
- 默认 workspace：`~/.ccbot/workspace`
- 环境变量前缀：`CCBOT_`

### B. 配置 Schema 版本化

增加：
- `config_version`
- 启动时迁移器（v1 -> v2）

示例新增配置：

```json
{
  "config_version": 2,
  "feishu": {
    "connection_mode": "websocket",
    "dm_policy": "pairing",
    "group_policy": "allowlist",
    "group_session_scope": "group_topic",
    "inbound_debounce_ms": 300
  },
  "bus": {
    "dedup_ttl_sec": 86400,
    "outbox": {
      "enabled": true,
      "max_retries": 5
    }
  },
  "a2a": {
    "enabled": true,
    "auth_token": "change-me",
    "require_idempotency_key": true
  }
}
```

---

## 5. 分阶段实施路线图

## Phase 0: 基线与保护（1-2 天）

目标：
- 建立可回归与可观测基础，不改行为。

任务：
1. 新增结构化日志字段：`message_id/chat_id/session_key/run_id`
2. 新增指标计数器（内存版）：入站数、去重命中、发送失败数
3. 补充链路冒烟测试脚本

退出标准：
- 关键路径日志可追踪一次消息全生命周期。

## Phase 1: 入站可靠性（3-5 天）

目标：
- 去重持久化 + 串行队列 + 防抖上线。

任务：
1. 实现 `bus/dedup.py`（mem+sqlite）
2. 实现 `bus/queue.py`（per-chat）
3. 实现 `bus/debounce.py`
4. `FeishuBot` 接入新 pipeline（保留旧逻辑开关）
5. 新增单测：重启去重、顺序保证、防抖合并

退出标准：
- 相同消息不重复调度，顺序测试稳定通过。

## Phase 2: 出站可靠性（3-4 天）

目标：
- Outbox WAL + 恢复重试。

任务：
1. 实现 `bus/outbox.py`
2. 发送链路改造为“先入箱后发送”
3. 启动恢复任务
4. 失败分类（临时错误 vs 永久错误）

退出标准：
- 模拟发送失败后，重启可恢复。

## Phase 3: 会话与策略（4-6 天）

目标：
- 策略引擎与 session scope 完整化。

任务：
1. 实现 `session/resolver.py` + `session/key.py`
2. 实现 `policy/*` + pairing store
3. 引入 `group_session_scope` 配置
4. 补齐群聊 topic/sender 场景测试

退出标准：
- 4 种 session scope 行为符合预期。

## Phase 4: 控制面升级（3-5 天）

目标：
- A2A 鉴权、幂等、inflight 合并。

任务：
1. `control/auth.py`、`control/idempotency.py`
2. `server.py` 接入认证中间件
3. `message/send`/`stream` 增加幂等键校验

退出标准：
- 重试不重复执行，未授权请求被拒绝。

## Phase 5: 收口与迁移（2-3 天）

目标：
- 命名统一、配置迁移、文档更新。

任务：
1. `nanobot` 兼容别名保留一版
2. 默认路径/env 前缀统一为 `ccbot`
3. 更新 README/CONFIG/A2A 文档

退出标准：
- 用户可无痛迁移（含迁移提示与自动修复脚本）。

---

## 6. 测试策略（必须覆盖）

1. 单元测试
- dedup TTL、queue 顺序、debounce 合并、policy 判定、session key 解析。

2. 集成测试
- 飞书消息入站到回复的端到端（mock SDK）。
- Outbox 恢复（故障注入 + 重启）。

3. 回归测试
- 当前已有能力不退化：`post` 解析、表格拆卡、worker dispatch。

现有可复用测试：
- post 解析：[tests/test_feishu_post_content.py](/Users/Apple/share/ccbot/tests/test_feishu_post_content.py:1)
- 表格拆分：[tests/test_feishu_table_split.py](/Users/Apple/share/ccbot/tests/test_feishu_table_split.py:1)
- team 调度：[tests/test_team.py](/Users/Apple/share/ccbot/tests/test_team.py:1)

---

## 7. 运行指标与验收门槛

建议 SLO：
1. 正确性
- 重复处理率 < 0.1%
- 同 chat 乱序率 = 0

2. 可靠性
- 发送失败后自动恢复成功率 > 99%
- Outbox 积压恢复时间（100 条）< 5 分钟

3. 性能
- 入站到开始调度 P95 < 500ms（无模型耗时）
- 防抖后模型调用次数下降 20% 以上（群聊 burst 场景）

4. 可维护性
- 飞书主入口文件控制在 250 行以内（其余模块化）
- 关键模块单测覆盖率 >= 80%

---

## 8. 风险与回滚策略

主要风险：
1. 新 pipeline 引入行为变化（用户体感差异）
2. Outbox 恢复导致重复发送
3. 配置迁移误伤旧部署

控制策略：
1. Feature Flag
- `bus.enable_v2_pipeline`
- `bus.enable_outbox`
- `feishu.enable_session_scope_v2`

2. 双写/灰度
- 初期只启用 dedup/queue，不启用 outbox
- 小流量验证后全量

3. 回滚
- 关闭 feature flag，退回旧链路
- Outbox 保留但不消费，避免数据丢失

---

## 9. 90 天执行计划（建议）

1. 第 1-2 周
- 完成 Phase 0-1（入站可靠性）

2. 第 3-4 周
- 完成 Phase 2（出站 WAL）

3. 第 5-7 周
- 完成 Phase 3（会话与策略）

4. 第 8-9 周
- 完成 Phase 4（控制面）

5. 第 10-12 周
- 完成 Phase 5（迁移收口 + 文档 + 观测优化）

---

## 10. 本文档对应的落地优先级

立刻开始（P0）：
1. 入站持久去重
2. per-chat 串行队列
3. 出站 WAL 队列

随后（P1）：
1. session scope
2. pairing + group sender allowlist
3. A2A 幂等与认证

最后（P2）：
1. 命名与路径统一迁移
2. 可观测性深度优化（指标面板、报警）
