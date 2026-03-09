# ccbot Docs

> 更新时间：2026-03-09
> 目标：把 `ccbot` 当前产品形态、运行时设计、运维方式和排障方法整理成一套可直接交接、可直接值班、可直接继续开发的文档集合。

## 1. 推荐阅读路径

### 1.1 产品 / 架构视角

1. `README.md`
2. `docs/PRODUCT_REQUIREMENTS_MODEL.md`
3. `docs/ARCHITECTURE.md`
4. `docs/ARCHITECTURE_REVIEW.md`

适合回答：

- 这个产品当前到底做什么
- 为什么现在走 `Supervisor -> WorkerPool -> Worker`
- 为什么当前阶段不做 A2A

### 1.2 Claude SDK / Prompt 视角

1. `docs/CLAUDE_AGENT_SDK_CAPABILITY_MAP.md`
2. `docs/CLAUDE_RUNTIME_PROFILES.md`
3. `docs/MEMORY_ARCHITECTURE.md`
4. `docs/LANGSMITH_INTEGRATION.md`

适合回答：

- Claude Agent SDK 的能力到底怎么用
- `ClaudeSDKClient` 在链路里的真实作用是什么
- role prompt、`.claude/CLAUDE.md`、LangSmith、memory 怎么协同

### 1.3 运行 / 值班视角

1. `docs/RUNTIME_OPERATIONS.md`
2. `docs/TROUBLESHOOTING.md`
3. `docs/CHANNEL_ARCHITECTURE.md`

适合回答：

- 服务怎么启动、怎么预演、怎么回归
- 飞书消息出问题时先查哪
- 定时任务、Worker、LangSmith 怎么排障

## 2. 文档地图

### 总览类

- `README.md`
  - 仓库首页总览、快速启动、主能力摘要
- `docs/ARCHITECTURE.md`
  - 当前实现的整体架构说明
- `docs/ARCHITECTURE_REVIEW.md`
  - 架构演进判断、为什么当前阶段不做 A2A
- `docs/PRODUCT_REQUIREMENTS_MODEL.md`
  - 产品目标、阶段边界、设计优先级

### Claude Runtime 专题

- `docs/CLAUDE_AGENT_SDK_CAPABILITY_MAP.md`
  - Claude Agent SDK 全能力盘点与项目映射
- `docs/CLAUDE_RUNTIME_PROFILES.md`
  - `Supervisor / Worker / Reviewer` 的 runtime 配置与 prompt 分层
- `docs/MEMORY_ARCHITECTURE.md`
  - Supervisor 记忆模型、`resume`、本地长期/短期记忆
- `docs/LANGSMITH_INTEGRATION.md`
  - LangSmith tracing 接入方式、限制与观察点

### Channel / Runtime 专题

- `docs/CHANNEL_ARCHITECTURE.md`
  - Channel 抽象、Feishu responder、入站 pipeline
- `docs/RUNTIME_OPERATIONS.md`
  - 运行目录、配置、启动、回归、预演
- `docs/TROUBLESHOOTING.md`
  - 错误模式、日志定位、链路排障

## 3. 当前稳定结论

- 当前产品主线是：`Channel -> AgentTeam -> Supervisor -> WorkerPool -> Worker`
- 当前推荐路径是：先把单机 runtime、Feishu 通道、Scheduler、Memory、Observability 做扎实
- 当前多 Agent 编排必须由 `ccbot runtime` 统一托管，不再让 Claude Code 原生 `Agent` / `SendMessage` 接管控制面
- 当前只给 Supervisor 维护额外记忆；Worker 只保留运行时上下文，不做独立长期记忆
- 当前定时任务采用 **Supervisor job** 模型：到点后先触发 Supervisor，再由 Supervisor 决定是否 dispatch Worker

## 4. 文档维护约定

- `README.md` 只保留高密度总览，不承载全部细节
- `docs/ARCHITECTURE.md` 负责说明“现在系统到底怎么工作”
- Claude SDK / Prompt / Memory / LangSmith 的细节优先写进专题文档
- 文档默认基于当前代码实现，不讨论已经放弃的主路径
- 涉及外部官方能力的结论，必须注明核对时间
