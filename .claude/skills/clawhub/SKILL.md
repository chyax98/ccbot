---
name: clawhub
description: Search and install agent skills from ClawHub, the public skill registry.
homepage: https://clawhub.ai
metadata: {"ccbot":{"emoji":"🦞"}}
---

# ClawHub

Public skill registry for AI agents. Search by natural language (vector search).

## When to use

Use this skill when the user asks any of:
- "find a skill for …"
- "search for skills"
- "install a skill"
- "what skills are available?"
- "update my skills"

## Search

```bash
npx --yes clawhub@latest search "web scraping" --limit 5
```

## Install

```bash
# WORKSPACE 从 system prompt 中的 "Workspace: ..." 获取
npx --yes clawhub@latest install <slug> --workdir $WORKSPACE/.claude
```

Replace `<slug>` with the skill name from search results. Skills are placed into `$WORKSPACE/.claude/skills/`, loaded natively by Claude Code. Always include `--workdir`.

## Update

```bash
npx --yes clawhub@latest update --all --workdir $WORKSPACE/.claude
```

## List installed

```bash
npx --yes clawhub@latest list --workdir $WORKSPACE/.claude
```

## Notes

- Requires Node.js (`npx` comes with it).
- No API key needed for search and install.
- Login (`npx --yes clawhub@latest login`) is only required for publishing.
- `--workdir $WORKSPACE/.claude` is critical — skills go into `.claude/skills/` under your workspace.
- After install, remind the user to start a new session to load the skill.
