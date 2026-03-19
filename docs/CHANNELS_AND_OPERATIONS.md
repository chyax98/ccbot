# Channels & Operations

> 更新时间：2026-03-09
> 作用：统一说明 channel 抽象、运行目录、启动方式、CLI 预演、回归剧本与值班视角的运行手册。

## 1. Channel 抽象

Channel 层负责：

- 接收消息
- 归一化输入
- 封装 responder
- 处理平台差异
- 把 worker 结果、进度、文件回传给用户

当前已实现：

- `CLIChannel`
- `FeishuChannel`

## 2. 多平台抽象原则

### 平台无关能力

- `progress_updates`
- `worker_results`
- `thread_replies`
- `file_outputs`
- `interactive_confirm`
- `rich_text`

### 平台特有能力

- Feishu：线程消息、卡片确认、文件上传
- CLI：本地交互、快速回归、无外部依赖
- Telegram / QQ / WeChat：未来可扩，但现在不应先做复杂抽象

## 3. 当前运行目录

默认主 workspace：

```text
~/.ccbot/workspace
```

关键目录：

```text
~/.ccbot/
  config.json
  workspace/
    .claude/
      CLAUDE.md
      settings.json
      skills/
    .ccbot/
      memory/
      schedules/
    output/
```

说明：

- `workspace/.claude/`：Supervisor 的项目级 Claude 配置
- `workspace/.ccbot/`：运行态数据
- `output/`：发给用户的输出目录

## 4. Worker 运行目录

Worker 不复用主 workspace 作为执行现场。

规则：

- 直接使用 `task.cwd`
- 若 `task.cwd/.claude/` 已存在，则优先尊重用户本地配置
- 若不存在，则框架只补最小模板

这符合当前产品目标：

- Worker 像在用户真实项目里工作
- 框架不额外发明一套虚构 repo 结构

## 5. 常用启动方式

### CLI 对话

```bash
uv run ccbot chat
uv run ccbot chat -m "你好"
```

### 完整 runtime

```bash
uv run ccbot run --config ~/.ccbot/config.json --channel feishu
```

### 本地预演

```bash
uv run ccbot run --config ~/.ccbot/config.json --channel cli
```

CLI 预演是当前最推荐的回归入口。

## 6. CLI 预演最小剧本

建议按以下顺序：

1. `/help`
2. `/workers`
3. `/memory show`
4. `/schedule list`
5. 普通用户消息
6. 明显会 dispatch 的任务
7. 创建周期性 schedule，并执行 `/schedule run <job_id>`

重点确认：

- 控制命令立即返回，不走模型主链路
- dispatch 时能收到 worker 结果
- 异步 dispatch 完成后能收到最终综合回复
- scheduler 能创建、列出、立即执行

## 7. 推荐回归顺序

### 7.1 基础链路

- 发普通消息
- 确认有回复
- 确认日志和 trace 正常

### 7.2 Worker 链路

- 发一个明显会 dispatch 的任务
- 查看 `/workers`
- 试 `/worker stop <name>` / `/worker kill <name>`
- 确认先收到 worker 结果，再收到最终综合回复

### 7.3 Scheduler 链路

- 用自然语言创建周期性任务
- `/schedule list`
- `/schedule run <job_id>`
- 确认结果通知回原始 target

### 7.4 退出链路

- 正常停止服务
- 再次启动
- 确认没有坏状态残留
- 确认 Supervisor 会话仍可续接

## 8. 配置重点

### agent

- `model`
- `workspace`
- `max_turns`
- `max_workers`
- `scheduler_enabled`
- `scheduler_poll_interval_s`
- `supervisor_resume_enabled`
- `short_term_memory_turns`
- `langsmith_*`

### feishu

- `app_id`
- `app_secret`
- `allow_from`
- `require_mention`
- `progress_silent_s`
- `progress_interval_s`
- `confirm_timeout_s`
- `msg_process_timeout_s`
- `ws_reconnect_delay_s`
- `ws_reconnect_max_delay_s`

## 9. 定时任务（Scheduler）

所有定时执行需求统一由 `SchedulerService` 管理：

- 用户通过自然语言创建的周期性任务
- 需要持久化、列出、编辑、暂停、恢复、立即执行的任务
- Supervisor 通过 SDK MCP tools（`schedule_list/create/edit/delete/pause/resume`）操作

### 不适合强行落成 schedule 的请求

- 一次性倒计时提醒
- 语义不清的“开定时器”但没有明确周期表达

## 10. 后续 channel 扩展建议

当前优先级应是：

1. 先把 Feishu 和 CLI 打磨稳
2. 再抽 Telegram
3. 最后再看 QQ / WeChat

不要为了抽象而抽象。
