# ccbot 架构最终方案（整合版）

本文件整合以下历史文档的核心结论，并收敛为可执行的最终方案：
- `ARCHITECTURE_COMPARISON.md`
- `ARCHITECTURE_IMPROVEMENT.md`
- `ARCHITECTURE_OPTIMIZATION_GUIDE.md`
- `QUICK_REFERENCE.md`
- `IMPLEMENTATION_CHECKLIST.md`

---

## 1. 目标与边界

目标：
1. 飞书消息链路稳定可靠（不重、不乱、不丢）。
2. Agent 调度可扩展（先单机，后可跨机）。
3. 文档与实施路径清晰、可落地、可回滚。

非目标（当前阶段）：
1. 一次性做成全量分布式控制平面。
2. 引入高复杂度多租户权限体系。

---

## 2. 三个最终方案

## 方案 A：单机可靠性优先（推荐立即实施）

定位：
- 把当前系统做成“稳定可用的单机生产形态”。

架构要点：
1. 入站总线
- `Dedup(mem+sqlite) -> Debounce -> Per-chat Queue -> Policy -> SessionResolver -> Agent`

2. 出站总线
- `Outbox(WAL) -> Deliver -> Retry/Backoff -> DLQ -> Recovery on startup`

3. 策略与会话
- DM：`open/pairing/allowlist`
- Group：`open/allowlist/disabled + sender allowlist + require_mention`
- Session Scope：`group/group_sender/group_topic/group_topic_sender`

4. 控制面
- 保持现有 A2A，先补最小鉴权和幂等键（不做复杂协议升级）。

优点：
- 交付快，风险低，收益最大。
- 直接解决当前核心问题（重复、乱序、丢消息）。

代价：
- 跨机器协作能力有限。

适用场景：
- 个人/小团队单机部署，飞书为主入口。

---

## 方案 B：本地工具调度优先，远程能力后挂（推荐中期）

定位：
- 保持“内部工具调度”为主，把远程执行抽象为可插拔 transport。

架构要点：
1. 统一调度接口（例如 `dispatch_task`）
- `transport=local|remote`
- 默认 local（同进程/同机）
- remote 作为扩展后端（可对接 A2A）

2. 调度层与消息总线解耦
- 飞书链路稳定后，再引入远程 worker。

3. 远程能力最小集
- token auth
- idempotency key
- inflight 合并

优点：
- 兼顾长期扩展，避免过早做重控制面。
- 不影响当前单机性能与稳定性。

代价：
- 需要额外定义抽象层与适配器。

适用场景：
- 近期单机为主，但 1-2 个季度内需要跨机器 worker。

---

## 方案 C：完整 A2A 控制平面优先（不推荐当前立即实施）

定位：
- 提前投入完整分布式控制能力。

架构要点：
1. 控制面协议升级
- connect/auth/challenge
- scope/role
- 完整幂等与事件流

2. 统一远程会话管理
- 跨节点 session/state 一致性策略

3. 运维与可观测配套
- distributed tracing
- 多节点健康与故障转移

优点：
- 终态能力最强。

代价：
- 开发、测试、运维复杂度显著上升。
- 在当前阶段容易“过度建设”。

适用场景：
- 明确有高频跨机器协作和多节点生产要求。

---

## 3. 方案对比与结论

| 维度 | 方案 A | 方案 B | 方案 C |
|---|---|---|---|
| 落地速度 | 快 | 中 | 慢 |
| 风险 | 低 | 中 | 高 |
| 近期收益 | 高 | 高 | 中 |
| 长期扩展 | 中 | 高 | 高 |
| 复杂度 | 低 | 中 | 高 |

结论：
1. 立刻采用 **方案 A** 作为当前主线。
2. 在 A 稳定后演进到 **方案 B**（推荐路线）。
3. 仅当出现明确跨机刚需时再进入 **方案 C** 的大规模投入。

---

## 4. 最终推荐实施路线（A -> B）

## Phase 1（P0）：消息可靠性底座
1. 持久去重（sqlite）
2. per-chat 串行队列
3. 文本防抖（控制命令豁免）

验收：
- 重复处理率 < 0.1%
- 同 chat 乱序率 = 0

## Phase 2（P0）：出站 WAL
1. outbox 持久化
2. retry/backoff
3. DLQ + 启动恢复

验收：
- 重启后未送达消息可恢复
- 恢复成功率 > 99%

## Phase 3（P1）：策略与会话
1. pairing store
2. group sender allowlist
3. session scope 四模式

验收：
- 群内 topic/sender 不串上下文

## Phase 4（P1）：控制面最小增强
1. A2A token auth
2. idempotency key
3. inflight 合并

验收：
- 重试不重复执行
- 未授权调用被拒绝

## Phase 5（P2）：B 方案抽象层
1. 统一 `dispatch_task` 接口
2. `transport=local|remote`
3. remote 适配 A2A 后端

验收：
- 不改上层业务代码即可切换 local/remote

---

## 5. 文档收敛规则（从现在开始）

1. 本文档是“最终方案源文档”。
2. 历史文档保留，但仅做背景参考，不再继续扩写。
3. 新的架构决策必须更新到本文档“方案对比/推荐路线”章节。

---

## 6. 历史文档映射

| 历史文档 | 当前用途 |
|---|---|
| `ARCHITECTURE_COMPARISON.md` | 背景对比参考 |
| `ARCHITECTURE_IMPROVEMENT.md` | 细节草案参考 |
| `ARCHITECTURE_OPTIMIZATION_GUIDE.md` | 详细实施条目参考 |
| `QUICK_REFERENCE.md` | 临时速查参考 |
| `IMPLEMENTATION_CHECKLIST.md` | 执行 checklist 参考 |

