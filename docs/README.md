# ccbot Docs

> 更新时间：2026-03-09
> 目标：将 `docs/` 从碎片专题收敛为按模块组织的主文档集合，同时保留旧文件入口，避免断链接。

## 1. 当前模块文档

建议按下面顺序阅读：

1. `docs/PRODUCT_ARCHITECTURE.md`
   - 产品定义
   - 当前主线架构
   - 阶段边界
   - 为什么现在不做 A2A

2. `docs/CLAUDE_RUNTIME.md`
   - Claude Agent SDK 能力
   - `ClaudeSDKClient` 的真实作用
   - runtime profile / prompt / memory

3. `docs/CHANNELS_AND_OPERATIONS.md`
   - Channel 抽象
   - workspace / worker cwd
   - CLI 预演与回归
   - 运行和值班手册

4. `docs/OBSERVABILITY_AND_TROUBLESHOOTING.md`
   - LangSmith
   - 日志与 trace
   - 常见故障与排障顺序

## 2. 旧文档处理策略

当前只保留少量兼容入口页：

- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_OPERATIONS.md`
- `docs/TROUBLESHOOTING.md`

作用：

- 避免最常见的旧链接失效
- 给仍在使用旧入口的人一个平滑跳转层

其余历史专题内容已被合并进 4 个模块主文档，不再在 `docs/` 下继续平行维护。

## 3. 当前稳定结论

- `docs/` 的主阅读入口已切到模块文档
- 主线架构仍是 `Channel -> AgentTeam -> Supervisor -> WorkerPool -> Worker`
- 当前不让 Claude Code 原生 `Agent` / `SendMessage` 接管多 Agent 控制面
- 当前只为 Supervisor 提供额外记忆

## 4. 文档维护约定

- 模块文档是主事实来源
- 旧专题文档只保留跳转和少量上下文，不再继续膨胀
- 新增内容优先写入模块文档，再决定是否保留专题补充
