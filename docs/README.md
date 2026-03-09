# ccbot Docs

> 更新时间：2026-03-09
> 目标：将 `docs/` 收敛为标准项目文档目录，按“产品 / Runtime / 运行 / 排障”四个主模块组织，评审类内容作为补充资料归档。

## 核心文档

建议按下面顺序阅读：

1. `docs/PRODUCT_ARCHITECTURE.md`
   - 产品目标
   - 当前主线架构
   - 能力边界与阶段判断
   - 为什么当前不做 A2A / 远程 Worker

2. `docs/CLAUDE_RUNTIME.md`
   - Claude Agent SDK 能力边界
   - `ClaudeSDKClient` 的实际作用
   - prompt / preset / settings / memory 的落地方式

3. `docs/CHANNELS_AND_OPERATIONS.md`
   - Channel 抽象
   - workspace 与 worker cwd
   - CLI / Feishu 运行方式
   - 值班、回归、日常运维

4. `docs/OBSERVABILITY_AND_TROUBLESHOOTING.md`
   - LangSmith 接入与限制
   - 关键日志与 trace 观察点
   - 常见故障与排障顺序

## 补充文档

5. `docs/PROJECT_REVIEW.md`
   - 当前架构审查结论
   - 核心优势、风险与建议
   - 适合作为阶段复盘或演进讨论输入

## 兼容入口

以下页面仅作为旧链接兼容入口，不再作为主事实来源维护：

- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_OPERATIONS.md`
- `docs/TROUBLESHOOTING.md`

## 当前事实边界

文档必须反映当前代码，而不是未来设想。当前稳定边界如下：

- 主线架构是 `Channel -> AgentTeam -> Supervisor -> WorkerPool -> Worker`
- 当前不让 Claude Code 原生 `Agent` / `SendMessage` 接管多 Agent 控制面
- 当前只为 Supervisor 提供额外记忆；Worker 不维护独立长期记忆
- Scheduler 面向周期任务，不是任意一次性后台任务系统
- LangSmith 当前追踪 `ClaudeSDKClient`，不追踪顶层 `claude_agent_sdk.query()`

## 维护约定

- 一个主题只保留一个主事实来源
- `README.md` 只保留入口和快速上手，不承载过多细节
- 新增专题优先判断是否应并入 4 个主模块，而不是继续新增平行文档
- 评审报告、复盘记录等补充材料统一放入 `docs/` 内，不再散落到仓库根目录
