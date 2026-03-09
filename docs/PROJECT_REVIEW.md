# Project Review

> 更新时间：2026-03-09
> 作用：汇总当前 `ccbot` 的架构审查结论，作为补充材料；主事实来源仍以模块文档为准。

## 1. 结论摘要

当前 `ccbot` 已具备“个人自用型 Agent runtime”的基本完成度，主线设计是清晰且可持续演进的：

- 分层清楚：`Channel -> AgentTeam -> Supervisor -> WorkerPool -> Worker`
- 控制面明确：多 Agent 编排由产品 runtime 自己控制，不外包给 Claude 原生 sub-agent 机制
- 运行形态明确：以真实 workspace 与运行目录为现场，而不是纯 prompt 演示
- 可观测性具备：LangSmith + 结构化日志可以支撑日常定位

当前更适合继续打磨单机主线，而不是过早引入 A2A、远程 Worker 或更重的分布式协议。

## 2. 主要优势

### 2.1 产品方向是对的

`ccbot` 不是聊天壳，而是“能接渠道、能调度、能在真实目录执行任务、能回传结果”的个人 Agent runtime。这个产品方向和当前实现基本一致。

### 2.2 架构边界比较干净

各层职责相对稳定：

- Channel 负责接入、归一化、消息回传
- AgentTeam 负责决策与控制面编排
- Supervisor 负责理解需求、选择 `respond / dispatch / schedule_create`
- WorkerPool 负责 Worker 生命周期与复用
- ClaudeSDKClient 负责实际执行 Claude Code runtime

### 2.3 当前 runtime 选型合理

以 `ClaudeSDKClient` 作为执行核心是正确的，因为它提供：

- 持续会话
- response event stream
- interrupt 能力
- 工具调用现场
- 与 LangSmith 的官方 tracing 对接点

### 2.4 渠道链路有稳定性基础

`Dedup -> Debounce -> PerChatQueue` 这个消息入站链路，对个人 bot 场景是合理的，能够减少重复投递、短时间抖动和同 chat 并发打架。

## 3. 当前主要风险

### 3.1 Worker 生命周期仍需持续盯住

虽然当前已经补上池化上限、空闲回收和中断控制，但 Worker 生命周期依然是系统复杂度最高的地方，需要继续围绕以下几点守住边界：

- 总量上限是否合理
- 繁忙时是否出现池满阻塞
- `/stop`、`/new`、`/memory clear` 是否能一致中断后台任务
- 长时间运行后是否出现僵尸会话

### 3.2 Supervisor 结构化决策需要保守降级

结构化输出比文本协议好很多，但产品上仍应坚持“决策失败时优先降级为安全回复”，不要让用户直接承受 schema 失败。

### 3.3 文档与代码容易再次漂移

这个项目近期演进很快，最容易出问题的不是单点 bug，而是“代码改了，文档还停在旧结论”。所以文档必须继续保持按模块收敛，而不是回到大量平行专题文档。

## 4. 对个人自用的判断

如果以“个人自用、持续值班、以 CLI + Feishu 为主入口”为目标，当前项目已经接近可用，但前提是保持以下原则：

- 先把单机主线打磨到稳定
- 先保证 Supervisor + Worker 的控制面可靠
- 先保证 `stop / reset / schedule / memory` 行为一致
- 不急着扩展 A2A、远程集群、复杂跨节点协议

换句话说，当前不是“功能不够多”，而是“需要继续把已有主线打磨得更稳”。

## 5. 建议的近期优先级

### P0

- 保持 Supervisor 控制面稳定
- 守住 WorkerPool 上限、回收和中断语义
- 确保 Feishu / CLI 两条主链路都能稳定收、稳定执行、稳定回
- 保持 LangSmith trace 与本地日志信息完整

### P1

- 增补更贴近真实使用的 CLI / runtime 集成测试
- 继续完善 skills 与系统提示词维护方式
- 增加对定时任务创建、执行、回传的回归覆盖

### P2

- 再评估是否需要 A2A、远程 Worker、DAG 编排等增强能力
- 在真实使用压力下复盘 memory / scheduler / ops 模型是否要升级

## 6. 最终判断

从“标准项目”的角度看，`ccbot` 当前应该采用下面的文档策略：

- 4 个主模块文档作为事实来源
- 3 个兼容入口页负责承接旧链接
- 审查 / 复盘 / 决策类文档合并为少量补充材料
- 不再把专题文档散落在仓库根目录

从“个人自用”的角度看，项目值得继续投入，重点不是扩面，而是继续打磨稳定性、控制面和运维可见性。
