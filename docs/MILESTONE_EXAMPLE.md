# Milestone 模式效果示例

## 场景：用户要求同时开发前端和后端登录功能

### 飞书消息流（milestone 模式）

```
用户：
同时开发前端和后端的登录功能

---

Bot：
📋 分析任务中...

---

Bot：
📋 派发任务: frontend, backend

---

Bot：
[frontend] 🔧 Read
[backend] 🔧 Read

⏳ 处理中，请稍候...
（这条消息会不断更新，显示各 worker 的最新工具调用）

---

Bot：
✅ frontend 完成

---

Bot：
✅ backend 完成

---

Bot：
🎯 综合结果中...

---

Bot：
（最终结果）

已完成前后端登录功能开发：

**前端（frontend）**
- 创建了 LoginForm.tsx 组件
- 实现了表单验证
- 添加了错误提示

**后端（backend）**
- 实现了 /api/login 接口
- 添加了 JWT 认证
- 完成了单元测试

所有功能已就绪，可以开始测试。
```

---

## 消息统计

- **关键节点消息**：6 条（可追溯）
  1. 📋 分析任务中...
  2. 📋 派发任务: frontend, backend
  3. ✅ frontend 完成
  4. ✅ backend 完成
  5. 🎯 综合结果中...
  6. （最终结果）

- **工具调用消息**：1 条（不断更新）
  - 显示各 worker 的实时进度

---

## 对比其他模式

### edit 模式（简洁）
只有 2 条消息：
1. 🤔 正在处理中，请稍候...（不断更新）
2. （最终结果）

**缺点**：无法追溯中间过程

### verbose 模式（详细）
可能有 30+ 条消息：
1. 📋 分析任务中...
2. 🔧 Read
3. 🔧 Grep
4. 📋 派发任务: frontend, backend
5. [frontend] 🔧 Read
6. [frontend] 🔧 Write
7. [frontend] 🔧 Bash
8. [backend] 🔧 Read
9. [backend] 🔧 Write
10. ...（每个工具调用都是一条消息）

**缺点**：消息太多，刷屏

---

## Milestone 模式的优势

1. **可追溯**：可以看到任务的关键进度节点
2. **不刷屏**：工具调用只更新一条消息
3. **清晰**：关键节点用 emoji 标记，一目了然
4. **适中**：5-10 条消息，既不太少也不太多

---

## Worker 失败的情况

如果某个 worker 失败：

```
Bot：
📋 派发任务: frontend, backend, database

---

Bot：
✅ frontend 完成

---

Bot：
❌ backend 失败: Connection refused to database

---

Bot：
✅ database 完成

---

Bot：
🎯 综合结果中...

---

Bot：
（最终结果）

部分任务完成，但 backend 遇到问题：

**frontend** ✅
- 登录页面已完成

**backend** ❌
- 失败原因：无法连接数据库
- 建议：检查数据库配置

**database** ✅
- 数据库表结构已创建

请先解决 backend 的数据库连接问题。
```

---

## 配置

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "progress_mode": "milestone"
  }
}
```

或通过环境变量：

```bash
export NANOBOT_FEISHU__PROGRESS_MODE=milestone
```

---

## 实现细节

### 关键节点识别

只有以下 emoji 开头的消息会在 milestone 模式下发送新消息：

- `📋` - 任务分析/派发
- `✅` - 成功完成
- `🎯` - 综合结果
- `❌` - 失败/错误

其他消息（如工具调用 `🔧 Read`）会编辑同一条消息。

### 代码位置

- `nanobot/config.py` - 配置项
- `nanobot/feishu.py` - 消息发送逻辑
- `nanobot/team.py` - 关键节点消息生成
