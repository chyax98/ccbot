---
active: false
iteration: 16
session_id: 
max_iterations: 50
completion_promise: null
started_at: "2026-03-07T21:04:09Z"
---
# ccbot 架构重构 - Ralph 循环指令

## 项目目标

  将 ccbot 从单体 FeishuBot 重构为 OpenClaw 风格的分层架构，保持
  ClaudeAgent SDK 原生体验，重点优化 Agent 调度和可靠性。

记住：一定使用 claude agentsdk，充分挖掘claude agentsdk-python v0.1.48 版本的潜力，参考 openclaw 的整体方案，我们设计我们自己的方案，更强大 更简洁（claudecode 原生能力就很强），一定端到端完成任务才能结束，确保项目观测性强，和飞书的交互体验强大无比，调度系统强大，不要使用 pgsql等重型的，需要数据库就是要 sqlite 这样的，claude code 支持 skills subagent memory 工具 mcp 等能力，不要重复造轮子.禁止有冗余代码，禁止随意打补丁，我们要实现优质无冗余，不向后兼容的优质高质量代码，每一轮都有经过充分测试。及时更新文档，记录执行轨迹

## 核心架构

  Feishu Channel          # 通道适配
    ↓
  Inbound Pipeline        # 入站处理
    ├── Dedup (内存+JSON) # OpenClaw 式去重
    ├── Debounce (300ms)  # 防抖合并
    └── Queue (per-chat)  # 串行队列
    ↓
  Agent Runtime           # Agent 运行时
    ├── AgentPool         # Client 复用管理
    └── AgentTeam         # Supervisor-Worker 调度
    ↓
  Outbound                # 出站发送

## 技术约束

1. **个人使用**：单机，1-5 并发聊天，不重
2. **Agent SDK**：ClaudeSDKClient，每 chat 一实例
3. **去重**：内存 LRU + 异步 JSON 文件（OpenClaw 式）
4. **队列**：asyncio.Queue，同 chat 串行
5. **Dispatch**：结构化 Pydantic，替代文本解析
6. 参考 openclaw:/Users/Apple/share/openclaw
