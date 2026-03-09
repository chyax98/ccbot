---
name: memory
description: Two-layer memory system — read and update long-term facts, recall past events from conversation history.
always: true
---

# Memory

ccbot 维护两层记忆，均位于 workspace 的 `.ccbot/memory/` 目录下。

## Structure

```
.ccbot/memory/
  long_term.md          ← 长期事实（偏好、项目背景、持续约束）
  conversations/
    <chat_id>.json      ← 短期对话快照（由 runtime 自动管理，无需手动编辑）
```

## Read Long-Term Memory

长期记忆已在每次会话启动时注入到你的上下文中（标注为 `# ccbot Memory Context`）。
若需查看原始文件：

```bash
cat .ccbot/memory/long_term.md
```

## Update Long-Term Memory

当用户表达偏好、项目约束、持续背景信息时，立即更新：

```bash
# 读取后用 Edit 工具追加或修改
```

写入原则：
- 只保留**长期有效**的信息（偏好、约束、背景）
- 不要写入一次性任务细节
- 过时信息及时修正或删除

## Search Past Conversations

短期记忆以 JSON 存储，通过 grep 快速搜索：

```bash
grep -ri "关键词" .ccbot/memory/conversations/
```

查看特定会话：

```bash
cat .ccbot/memory/conversations/<chat_id>.json | python3 -m json.tool
```

## Memory Hierarchy

| 层级 | 位置 | 管理方 | 用途 |
|------|------|-------|------|
| 长期记忆 | `.ccbot/memory/long_term.md` | Supervisor（你）主动维护 | 稳定偏好、项目背景 |
| 短期记忆 | `.ccbot/memory/conversations/` | runtime 自动写入 | 对话历史快照，冷启动恢复用 |
| Session 记忆 | Claude SDK session | SDK 管理 | 当前会话完整 in-context 历史 |
