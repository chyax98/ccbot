# nanobot 开发方向

本文档总结 nanobot 的核心开发方向和技术架构。

---

## 🎯 三大核心方向

### 1. 多 Agent 编排 🤖

**目标**：实现 Supervisor-Worker 架构，支持任务自动分解和并行执行

**核心技术**：
- In-process asyncio 编排（无 bash 子进程）
- `<dispatch>` XML 协议
- 动态 Worker 池
- 实时进度聚合

**实现文件**：
- `nanobot/team.py` - AgentTeam 编排器
- `nanobot/agent.py` - NanobotAgent 基础

**文档**：
- [README.md](../README.md#多-agent-调度)

**状态**：✅ 已完成

---

### 2. A2A 协议（跨机器通信）🔗

**目标**：支持多个 nanobot 实例跨机器通信，构建分布式 Agent 网络

**核心技术**：
- HTTP + JSON-RPC 2.0
- Agent Card (/.well-known/agent.json)
- SSE 流式进度
- contextId 持久会话

**实现文件**：
- `nanobot/server.py` - A2A HTTP 服务器
- `nanobot/config.py` - A2AConfig

**文档**：
- [A2A.md](A2A.md)

**状态**：✅ 已完成

**未来扩展**：
- [ ] 认证和授权（API Key）
- [ ] 服务发现（自动注册 Workers）
- [ ] 负载均衡（多 Worker 轮询）
- [ ] 故障转移（Worker 失败重试）

---

### 3. 进度反馈系统 📊

**目标**：提供灵活的进度反馈机制，平衡信息量和用户体验

**核心技术**：
- 三种模式：edit / milestone / verbose
- 关键节点识别（emoji 标记）
- 多 Worker 状态看板
- 飞书卡片动态更新

**实现文件**：
- `nanobot/feishu.py` - 进度消息处理
- `nanobot/team.py` - 关键节点消息

**文档**：
- [PROGRESS_MODE.md](PROGRESS_MODE.md)
- [MILESTONE_EXAMPLE.md](MILESTONE_EXAMPLE.md)

**状态**：✅ 已完成

**未来扩展**：
- [ ] 进度百分比估算
- [ ] 预计剩余时间
- [ ] 可视化进度条

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户（飞书）                          │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              FeishuBot (WebSocket)                       │
│  - 消息接收/发送                                         │
│  - 进度反馈（edit/milestone/verbose）                   │
│  - 卡片格式化                                            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              AgentTeam (编排器)                          │
│  - Supervisor 分析任务                                   │
│  - 解析 <dispatch> 计划                                 │
│  - asyncio.gather 并行执行 Workers                      │
│  - 综合结果                                              │
└────────┬────────────────────────────────┬───────────────┘
         │                                │
         ▼                                ▼
┌──────────────────┐            ┌──────────────────┐
│  Supervisor      │            │  Workers         │
│  (Opus 4.6)      │            │  (Sonnet 4.6)    │
│  - 任务分析      │            │  - 专项执行      │
│  - 决策派发      │            │  - 并行运行      │
│  - 结果综合      │            │  - 进度上报      │
└──────────────────┘            └──────────────────┘
         │                                │
         └────────────┬───────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│         Claude Agent SDK (ClaudeSDKClient)               │
│  - Read/Write/Edit/Bash/Glob/Grep                       │
│  - WebFetch/WebSearch                                    │
│  - MCP 服务器                                            │
└─────────────────────────────────────────────────────────┘
```

---

## 🔄 跨机器通信架构

```
┌──────────────────┐
│  Supervisor      │
│  (机器 A)        │
│  - 飞书接入      │
│  - 任务分析      │
└────────┬─────────┘
         │ HTTP (A2A)
         ├─────────────────────┐
         │                     │
         ▼                     ▼
┌──────────────────┐  ┌──────────────────┐
│  Worker 1        │  │  Worker 2        │
│  (机器 B)        │  │  (机器 C)        │
│  - 前端专家      │  │  - 后端专家      │
│  - A2A 服务器    │  │  - A2A 服务器    │
│  - port 8765     │  │  - port 8765     │
└──────────────────┘  └──────────────────┘
```

**通信协议**：
```json
POST http://worker:8765/rpc
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "contextId": "task-123",
    "message": "实现登录功能"
  },
  "id": 1
}
```

---

## 📦 核心模块

### 1. 配置管理 (`nanobot/config.py`)
- AgentConfig - Agent 配置
- FeishuConfig - 飞书配置
- A2AConfig - A2A 配置
- 环境变量 > JSON 文件 > 默认值

### 2. Agent 核心 (`nanobot/agent.py`)
- NanobotAgent - per-chat-id ClaudeSDKClient
- Workspace 集成
- 多轮对话
- 进度回调

### 3. 多 Agent 编排 (`nanobot/team.py`)
- AgentTeam - Supervisor + Workers
- `<dispatch>` 协议解析
- asyncio 并行执行
- 关键节点消息

### 4. 飞书接入 (`nanobot/feishu.py`)
- WebSocket 长连接
- 消息类型处理（text/post/image/audio/file/media）
- 卡片格式化
- 进度反馈（三种模式）

### 5. A2A 服务器 (`nanobot/server.py`)
- FastAPI HTTP 服务器
- JSON-RPC 2.0 处理
- Agent Card 端点
- SSE 流式

### 6. Workspace (`nanobot/workspace.py`)
- MEMORY.md 长期记忆
- Skills 管理
- System prompt 构建

### 7. CLI (`nanobot/cli.py`)
- `nanobot chat` - 交互式对话
- `nanobot run` - 启动飞书机器人
- `nanobot serve` - 启动 A2A 服务器
- `nanobot worker` - 单次 worker

---

## 🎨 设计原则

### 1. 简洁优先
- 最小化依赖
- 避免过度抽象
- 代码即文档

### 2. 灵活配置
- 环境变量优先
- 合理的默认值
- 渐进式配置

### 3. 可观测性
- 详细的日志
- 实时进度反馈
- 清晰的错误信息

### 4. 可扩展性
- 插件化 Skills
- MCP 服务器支持
- A2A 协议标准化

---

## 🚀 未来方向

### 短期（1-2 个月）
- [ ] A2A 认证和授权
- [ ] 服务发现和注册
- [ ] 更多内置 Skills
- [ ] 性能优化和监控

### 中期（3-6 个月）
- [ ] Web UI 管理界面
- [ ] 多租户支持
- [ ] 任务队列和调度
- [ ] 分布式追踪

### 长期（6-12 个月）
- [ ] Agent 市场（共享 Skills）
- [ ] 可视化编排器
- [ ] 自动化测试框架
- [ ] 企业级部署方案

---

## 📊 技术栈

| 层级 | 技术 |
|------|------|
| AI 模型 | Claude 4.6 (Opus/Sonnet) |
| Agent SDK | Claude Agent SDK |
| 异步框架 | asyncio |
| Web 框架 | FastAPI |
| 配置管理 | Pydantic Settings |
| 消息平台 | 飞书 (Lark) |
| 协议 | JSON-RPC 2.0, SSE |
| 测试 | pytest, pytest-asyncio |
| 包管理 | uv |

---

## 🤝 贡献方向

### 1. 核心功能
- 改进多 Agent 调度算法
- 优化 A2A 协议性能
- 增强进度反馈体验

### 2. 生态系统
- 开发新的 Skills
- 集成更多 MCP 服务器
- 支持更多消息平台

### 3. 文档和示例
- 编写使用教程
- 提供最佳实践
- 分享部署经验

### 4. 测试和质量
- 增加测试覆盖率
- 性能基准测试
- 安全审计

---

## 📚 相关资源

- [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk)
- [Google A2A Protocol](https://github.com/google/a2a)
- [飞书开放平台](https://open.feishu.cn/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [MCP Protocol](https://modelcontextprotocol.io/)
