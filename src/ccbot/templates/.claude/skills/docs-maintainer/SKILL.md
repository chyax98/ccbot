---
name: docs-maintainer
description: Update repository documentation so README, architecture docs, runtime docs, and troubleshooting guides stay aligned with the actual implementation.
metadata: {"ccbot":{"emoji":"📝"}}
---

# Docs Maintainer

Use this skill when code has changed and the docs must be brought back in sync.

## Update order

1. `README.md` for top-level entry and quickstart
2. architecture / runtime / troubleshooting docs
3. specialized docs only where behavior actually changed

## Rules

- Document the current behavior, not an aspirational future state.
- Prefer one clear source of truth per topic.
- Keep README concise; move detail to docs.
- When a directory is gitignored, call that out before saying docs are "done".

## ccbot-specific checks

- current runtime boundary forbids native `Agent` / `SendMessage`
- Supervisor has extra memory; Worker does not
- scheduler is for recurring jobs, not arbitrary one-shot timers
- LangSmith traces `ClaudeSDKClient`, not top-level `query()`

## Validation

After doc edits, at least run:

```bash
uv run ruff check .
uv run pytest
```
