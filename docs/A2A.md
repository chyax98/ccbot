# A2A 协议支持

ccbot 现已支持 **A2A (Agent-to-Agent)** 协议，允许多个 ccbot 实例或其他 AI Agent 跨机器通信。

## 什么是 A2A？

A2A 是 Google 提出的 Agent-to-Agent 通信协议，基于：
- **HTTP + JSON-RPC 2.0**：标准化的 RPC 调用
- **Agent Card**：描述 agent 能力的元数据
- **contextId**：支持多轮对话的会话标识
- **SSE Streaming**：实时进度更新

## 快速开始

### 1. 配置

**重要**：在多 Agent 架构中，只有 **Supervisor** 连接飞书，**Workers** 只提供 HTTP 服务。

#### Supervisor 配置（连接飞书）

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx"
  },
  "agent": {
    "workspace": "~/.ccbot/supervisor",
    "model": "claude-opus-4-6"
  }
}
```

#### Worker 配置（只提供 A2A 服务）

```json
{
  "a2a": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8765,
    "name": "my-worker",
    "description": "Specialized worker agent"
  },
  "agent": {
    "workspace": "~/.ccbot/worker",
    "model": "claude-sonnet-4-6"
  }
}
```

**注意**：Worker 配置中**不要填写 `feishu` 配置**，避免与 Supervisor 冲突。

### 2. 启动

#### 启动 Worker（只提供 HTTP 服务）

```bash
uv run ccbot serve --config worker_config.json
```

Worker 将在 `http://0.0.0.0:8765` 启动，**不连接飞书**。

#### 启动 Supervisor（连接飞书 + 调度 Workers）

```bash
uv run ccbot run --config supervisor_config.json
```

Supervisor 连接飞书，接收用户消息，通过 HTTP 调用 Workers。

### 3. 测试

#### Agent Card

```bash
curl http://localhost:8765/.well-known/agent.json
```

响应：
```json
{
  "name": "my-ccbot",
  "description": "My personal AI assistant",
  "version": "1.0.0",
  "capabilities": ["message/send", "message/stream"],
  "endpoints": {
    "message/send": "http://0.0.0.0:8765/rpc",
    "message/stream": "http://0.0.0.0:8765/rpc"
  }
}
```

#### 同步消息（message/send）

```bash
curl -X POST http://localhost:8765/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "contextId": "my-session-1",
      "message": "你好，请介绍一下你自己"
    },
    "id": 1
  }'
```

响应：
```json
{
  "jsonrpc": "2.0",
  "result": {
    "contextId": "my-session-1",
    "message": "你好！我是 ccbot..."
  },
  "id": 1
}
```

#### 流式消息（message/stream）

```bash
curl -X POST http://localhost:8765/rpc \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/stream",
    "params": {
      "contextId": "my-session-2",
      "message": "用 Python 写一个快速排序"
    },
    "id": 2
  }'
```

响应（SSE 流）：
```
event: progress
data: {"message": "🔧 Read"}

event: progress
data: {"message": "🔧 Write"}

event: result
data: {"jsonrpc": "2.0", "result": {"contextId": "my-session-2", "message": "..."}, "id": 2}
```

### 4. Python 客户端示例

```python
import httpx
import json

async def call_agent(message: str, context_id: str = "default"):
    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "contextId": context_id,
            "message": message,
        },
        "id": 1,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post("http://localhost:8765/rpc", json=payload)
        result = resp.json()
        return result["result"]["message"]

# 使用
reply = await call_agent("你好")
print(reply)
```

## 多轮对话

使用相同的 `contextId` 可以保持会话上下文：

```python
# 第一轮
await call_agent("我叫张三", context_id="user-123")

# 第二轮（记住上下文）
reply = await call_agent("我叫什么名字？", context_id="user-123")
# 回复: "你叫张三"
```

## Supervisor 调用 Worker

在 Supervisor 的 system prompt 中配置已知的 worker 端点：

```markdown
## Known Workers

- **frontend-agent**: http://192.168.1.10:8765/rpc
  - 专注前端开发（React, Vue, TypeScript）

- **backend-agent**: http://192.168.1.11:8765/rpc
  - 专注后端开发（Python, FastAPI, 数据库）

当任务需要专项处理时，使用 WebFetch 工具调用对应 worker：

\`\`\`python
import json
payload = {
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
        "contextId": "task-123",
        "message": "实现用户登录 API"
    },
    "id": 1
}
# 使用 Bash 工具: curl -X POST http://192.168.1.11:8765/rpc -d '...'
\`\`\`
```

## 测试脚本

运行完整测试：

```bash
# 启动服务器（终端 1）
uv run ccbot serve --config examples/a2a_config.json

# 运行测试（终端 2）
uv run python examples/test_a2a.py
```

## 架构优势

1. **跨机器通信**：不同机器上的 ccbot 可以互相调用
2. **持久会话**：`contextId` 映射到 `chat_id`，支持多轮对话
3. **实时进度**：SSE streaming 提供工具调用进度
4. **标准协议**：基于 JSON-RPC 2.0，易于集成
5. **多 Agent 编排**：Supervisor 可以通过 HTTP 调度远程 Worker

## 配置选项

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `a2a.enabled` | bool | false | 是否启用 A2A 服务器 |
| `a2a.host` | str | "0.0.0.0" | 监听地址 |
| `a2a.port` | int | 8765 | 监听端口 |
| `a2a.name` | str | "ccbot" | Agent 名称 |
| `a2a.description` | str | "..." | Agent 描述 |

## 安全建议

1. **内网部署**：建议在内网环境使用，避免暴露到公网
2. **反向代理**：如需公网访问，使用 Nginx + HTTPS
3. **认证**：可在 FastAPI 中添加 API Key 认证中间件
4. **防火墙**：限制访问来源 IP

## 参考

- [Google A2A Protocol](https://github.com/google/a2a)
- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- [Server-Sent Events (SSE)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
