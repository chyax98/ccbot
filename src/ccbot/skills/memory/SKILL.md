---
name: memory
description: Two-layer memory system with grep-based recall.
always: true
---

# Memory

## Structure

- `memory/MEMORY.md` — Long-term facts (preferences, project context, relationships). Always loaded into your context.
- `memory/HISTORY.md` — Append-only event log. NOT loaded into context. Search it with grep. Each entry starts with [YYYY-MM-DD HH:MM].

## Search Past Events

```bash
grep -i "keyword" memory/HISTORY.md
```

Use the `Bash` tool to run grep. Combine patterns: `grep -iE "meeting|deadline" memory/HISTORY.md`

## When to Update MEMORY.md

Write important facts immediately using the `Edit` or `Write` tool:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

## Append to HISTORY.md

After significant conversations, append a summary entry:

```
[2026-01-15 14:30] USER: Asked about project X. ASSISTANT: Explained architecture, recommended approach Y.
```

Use `Bash` to append: `echo "[$(date '+%Y-%m-%d %H:%M')] ..." >> memory/HISTORY.md`
