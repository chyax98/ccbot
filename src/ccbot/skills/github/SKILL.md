---
name: github
description: "Interact with GitHub using the `gh` CLI. Use `gh issue`, `gh pr`, `gh run`, and `gh api` for issues, PRs, CI runs, and advanced queries."
metadata: {"nanobot":{"emoji":"🐙","requires":{"bins":["gh"]}}}
---

# GitHub Skill

Use the `gh` CLI via the `Bash` tool. Always specify `--repo owner/repo` when not in a git directory.

## Pull Requests

```bash
gh pr checks 55 --repo owner/repo
gh run list --repo owner/repo --limit 10
gh run view <run-id> --repo owner/repo --log-failed
```

## Issues

```bash
gh issue list --repo owner/repo --json number,title --jq '.[] | "\(.number): \(.title)"'
```

## API for Advanced Queries

```bash
gh api repos/owner/repo/pulls/55 --jq '.title, .state, .user.login'
```
