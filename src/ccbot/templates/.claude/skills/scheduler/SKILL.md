---
name: scheduler
description: Create and manage recurring scheduled jobs in ccbot.
metadata: {"ccbot":{"emoji":"⏰"}}
---

# Scheduler Skill

Use this skill when the user asks to create or manage recurring tasks such as:

- 每天/每周/每月定时检查
- 定时汇总、巡检、日报
- 固定时间启动一次自动执行流程

## What to produce

When the user wants to **create** a scheduled task, prefer the structured supervisor mode `schedule_create` and provide:

- `name`: short readable job name
- `cron_expr`: standard 5-field cron
- `timezone`: IANA timezone like `Asia/Shanghai`
- `prompt`: the exact prompt that should be sent to the Supervisor when the job runs
- `purpose`: why this job exists

## Cron examples

- 每天早上 9 点 → `0 9 * * *`
- 每周一早上 10 点 → `0 10 * * 1`
- 每月 1 号 08:30 → `30 8 1 * *`

## Principles

- 默认用 Supervisor job，而不是直接固定 Worker job
- `prompt` 要写成可独立执行的完整任务说明
- 如果用户没给时区，优先使用当前用户/工作区常用时区
- 如果任务含高风险动作，应先让用户确认再创建
