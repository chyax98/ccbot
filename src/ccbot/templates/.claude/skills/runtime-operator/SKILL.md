---
name: runtime-operator
description: Operate and regression-test a local ccbot runtime through CLI, including worker control, memory commands, schedule commands, and observability checks.
metadata: {"ccbot":{"emoji":"🛠️"}}
---

# Runtime Operator

Use this skill when the goal is to run, rehearse, or validate the agent runtime locally.

## Preferred path

Start with CLI before Feishu or other bot channels.

```bash
uv run ccbot run --config ~/.ccbot/config.json --channel cli
```

## Minimum regression set

Check these in order:

1. `/help`
2. `/workers`
3. `/memory show`
4. `/schedule list`
5. a normal user message
6. a task likely to dispatch workers
7. a recurring schedule request, then `/schedule run <job_id>`

## What to watch

- startup log prints model / workspace / LangSmith state
- control commands should return immediately without hitting the model
- worker results should come back through the channel path
- scheduler jobs should persist under `workspace/.ccbot/schedules/`
- failures should expose `sdk stderr` and LangSmith traces

## If runtime fails

Collect:

- triggering message
- last 30-50 log lines
- any `[sdk:...] STDERR | ...`
- LangSmith trace link or last tool name
