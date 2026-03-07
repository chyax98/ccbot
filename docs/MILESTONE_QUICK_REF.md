# Milestone 模式快速参考

## 配置

```json
{
  "feishu": {
    "progress_mode": "milestone"  // 默认值
  }
}
```

## 关键节点标记

| Emoji | 含义 | 示例 |
|-------|------|------|
| 📋 | 任务分析/派发 | `📋 分析任务中...` |
| ✅ | 成功完成 | `✅ frontend 完成` |
| 🎯 | 综合结果 | `🎯 综合结果中...` |
| ❌ | 失败/错误 | `❌ backend 失败: ...` |

## 消息流程

```
📋 分析任务中...
    ↓
📋 派发任务: worker1, worker2
    ↓
[工具调用进度] (编辑同一条消息)
    ↓
✅ worker1 完成
    ↓
✅ worker2 完成
    ↓
🎯 综合结果中...
    ↓
（最终结果）
```

## 优势

- ✅ 可追溯关键进度
- ✅ 不会刷屏
- ✅ 消息数量适中（5-10 条）
- ✅ 清晰的视觉标记

## 其他模式

### edit（简洁）
```json
{"progress_mode": "edit"}
```
只有 1 条消息，不断更新

### verbose（详细）
```json
{"progress_mode": "verbose"}
```
每步都发送新消息（20-50 条）

## 实现位置

- 配置：`nanobot/config.py`
- 逻辑：`nanobot/feishu.py`
- 节点：`nanobot/team.py`
