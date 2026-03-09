---
name: repo-review
description: Review an existing repository for architecture issues, runtime risks, testing gaps, and code quality regressions. Use when asked to review, audit, harden, or check whether the current implementation is stable.
metadata: {"ccbot":{"emoji":"🧪"}}
---

# Repo Review

Use this skill when the task is to review an existing codebase rather than build a new feature from scratch.

## Review order

1. Read the current entrypoints and runtime boundaries.
2. Inspect the main orchestration path before edge modules.
3. Check whether docs, prompts, and runtime behavior still match.
4. Run the smallest useful validation first, then broader tests.
5. Report root-cause issues, not just symptoms.

## Focus points for ccbot-style agent runtimes

- `Channel -> AgentTeam -> Supervisor -> WorkerPool -> Worker`
- prompt / preset / `.claude/settings.json` alignment
- control commands and runtime observability
- scheduler / memory / workspace persistence
- channel progress, final reply, and worker-result semantics

## Good review output

Always summarize:

- what is already solid
- what is misleading or partially wired
- what can break at runtime
- what should be fixed now vs later

## Validation

Prefer:

```bash
uv run ruff check .
uv run pytest
```

If testing a live runtime path, start with CLI before bot channels.
