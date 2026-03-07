# 进度反馈模式配置

nanobot 支持三种进度反馈模式，通过 `feishu.progress_mode` 配置。

## 模式对比

| 模式 | 说明 | 消息数量 | 适用场景 |
|------|------|----------|----------|
| `edit` | 编辑同一条消息（默认） | 1 条 | 简洁模式，不刷屏 |
| `milestone` | 关键节点发送新消息 | 5-10 条 | 平衡模式，可追溯关键进度 |
| `verbose` | 每步都发送新消息 | 20-50 条 | 调试模式，完整历史 |

## 配置示例

### 模式 1：edit（默认，简洁）

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "progress_mode": "edit"
  }
}
```

**效果**：
- 只有 1 条"正在处理中"的消息
- 不断更新显示最新进度
- 最后替换为最终结果

**消息示例**：
```
🤔 正在处理中，请稍候...

`[frontend]` 🔧 Write
`[backend]` 🔧 Bash

⏳ 处理中，请稍候...
```
（这条消息会不断更新，最后替换为最终结果）

---

### 模式 2：milestone（推荐，平衡）

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "progress_mode": "milestone"
  }
}
```

**效果**：
- 关键节点发送新消息（可追溯）
- 工具调用编辑同一条消息（不刷屏）

**消息示例**：
```
📋 Supervisor 正在分析任务...

📋 派发 2 个子任务: frontend, backend

✅ [frontend] 完成

✅ [backend] 完成

🎯 Supervisor 正在综合结果...

（最终结果）
```

**关键节点标记**：
- `📋` - 开始分析/派发任务
- `✅` - Worker 完成
- `🎯` - 综合结果
- `❌` - 错误

---

### 模式 3：verbose（调试，详细）

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "progress_mode": "verbose"
  }
}
```

**效果**：
- 每个进度都发送新消息
- 完整的执行历史
- 消息较多，可能刷屏

**消息示例**：
```
📋 Supervisor 正在分析任务...

🔧 Read

🔧 Grep

📋 派发 2 个子任务: frontend, backend

[frontend] 🔧 Read

[frontend] 🔧 Write

[backend] 🔧 Bash

[backend] 🔧 Read

✅ [frontend] 完成

✅ [backend] 完成

🎯 Supervisor 正在综合结果...

🔧 Read

（最终结果）
```

---

## 选择建议

### 日常使用 → `milestone`
- 可以看到关键进度历史
- 不会因为频繁工具调用刷屏
- 消息数量适中（5-10 条）

### 演示/汇报 → `edit`
- 消息列表干净
- 只显示最终结果
- 适合给领导看

### 调试/开发 → `verbose`
- 完整的执行历史
- 方便排查问题
- 可以看到每个工具调用

---

## 实现原理

### 关键节点识别

通过正则表达式识别关键节点消息：

```python
_MILESTONE_RE = re.compile(r"^(📋|✅|🎯|❌)")
```

只有以这些 emoji 开头的消息才会在 `milestone` 模式下发送新消息。

### 代码位置

- 配置：`nanobot/config.py` - `FeishuConfig.progress_mode`
- 实现：`nanobot/feishu.py` - `_send_progress` 函数
- 关键节点：`nanobot/team.py` - `AgentTeam.ask` 方法

---

## 环境变量配置

也可以通过环境变量设置：

```bash
export NANOBOT_FEISHU__PROGRESS_MODE=milestone
uv run nanobot run
```

---

## 注意事项

1. **消息频率限制**：飞书 API 有频率限制，`verbose` 模式可能触发限流
2. **用户体验**：`milestone` 模式是最佳平衡点
3. **调试时**：使用 `verbose` 模式可以看到完整执行流程
