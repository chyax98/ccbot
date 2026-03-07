# ccbot 文档总览

本目录已完成架构文档收敛，采用“1 个最终方案文档 + 若干实施/专题文档 + 历史归档”的结构。

## 最终方案（唯一主入口）

- [ARCHITECTURE_FINAL_PLANS.md](ARCHITECTURE_FINAL_PLANS.md)
  - 架构最终决策（A/B/C）
  - 推荐路线（A -> B）
  - 分阶段落地（Phase 1-5）
  - 历史文档映射与治理规则

> 说明：涉及“做哪个方案、先后顺序、是否引入 A2A 控制面”的决策，统一以本文件为准。

## 实施与专题文档（现行）

- [ARCHITECTURE_OPTIMIZATION_GUIDE.md](ARCHITECTURE_OPTIMIZATION_GUIDE.md)
  - 详细实施设计（模块拆分、数据模型、SLO、回滚）
- [CLAUDE_SDK_ADAPTATION_ARCHITECTURE.md](CLAUDE_SDK_ADAPTATION_ARCHITECTURE.md)
  - 基于 Claude Agent SDK 的适配架构主方案
- [OPENCLAW_ADAPTATION_SCENARIOS.md](OPENCLAW_ADAPTATION_SCENARIOS.md)
  - 基于 Claude Agent SDK 的 OpenClaw 方案迁移与场景化落地
- [A2A.md](A2A.md)
  - A2A 协议说明与接入方式
- [CONFIG.md](CONFIG.md)
  - 配置项、部署方式、运行参数
- [DEVELOPMENT.md](DEVELOPMENT.md)
  - 开发方向与模块说明
- [PROGRESS_MODE.md](PROGRESS_MODE.md)
  - 飞书进度反馈模式
- [MILESTONE_EXAMPLE.md](MILESTONE_EXAMPLE.md)
  - 里程碑反馈示例
- [MILESTONE_QUICK_REF.md](MILESTONE_QUICK_REF.md)
  - 里程碑速查

## 历史参考（归档，不再作为主决策依据）

- [ARCHITECTURE_COMPARISON.md](ARCHITECTURE_COMPARISON.md)
- [ARCHITECTURE_IMPROVEMENT.md](ARCHITECTURE_IMPROVEMENT.md)
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)

> 以上文档保留用于背景、设计演进和历史讨论；若与最终方案冲突，以 `ARCHITECTURE_FINAL_PLANS.md` 为准。

## 推荐阅读路径

1. 先读 [ARCHITECTURE_FINAL_PLANS.md](ARCHITECTURE_FINAL_PLANS.md)
2. 再读 [ARCHITECTURE_OPTIMIZATION_GUIDE.md](ARCHITECTURE_OPTIMIZATION_GUIDE.md)
3. 按需查阅 [A2A.md](A2A.md) 与 [CONFIG.md](CONFIG.md)
4. 仅在需要背景时阅读历史归档文档

## 命名与维护约束

- 文档索引层统一使用 `ccbot` 命名。
- 新的架构决策必须回写到 `ARCHITECTURE_FINAL_PLANS.md`。
- 历史归档文档只做必要勘误，不新增“新决策章节”。
