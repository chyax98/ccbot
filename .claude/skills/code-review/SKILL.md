---
name: code-review
description: Structured code review — security, correctness, performance, maintainability. Use when asked to review code, a PR, or a diff.
metadata: {"ccbot":{"emoji":"🔬"}}
---

# Code Review Skill

## Review Workflow

1. **Understand scope** — what changed, why, what's the expected behavior
2. **Read the diff** — use `git diff` or `gh pr diff`
3. **Run through the checklist** — cover all dimensions
4. **Report with severity** — 🔴 Critical / 🟡 Warning / 🔵 Info

## Get the Code to Review

```bash
# Review staged changes
git diff --cached

# Review against main
git diff main..HEAD

# Review a GitHub PR
gh pr diff 123 --repo owner/repo

# Review a specific file
git show HEAD:src/auth.py
```

## Review Checklist

### 🔴 Security (Critical — must fix)

- [ ] No secrets, tokens, or passwords hardcoded
- [ ] SQL queries use parameterized inputs (no string concatenation)
- [ ] User input is validated and sanitized before use
- [ ] File paths sanitized (no path traversal: `../../etc/passwd`)
- [ ] Authentication/authorization checks present and correct
- [ ] No `eval()`, `exec()`, `subprocess(shell=True)` with user input
- [ ] Dependencies up to date (no known CVEs)
- [ ] Sensitive data not logged

### 🔴 Correctness (Critical — must fix)

- [ ] Logic handles edge cases (empty input, null, zero, overflow)
- [ ] Error paths are handled (exceptions caught and handled properly)
- [ ] Async/concurrent code is race-condition-free
- [ ] Database transactions are atomic where needed
- [ ] Return values are used (not silently ignored)
- [ ] Off-by-one errors in loops/ranges

### 🟡 Performance (Warning — should fix)

- [ ] No N+1 queries (use batch/join instead)
- [ ] No unnecessary repeated computation in loops
- [ ] Appropriate data structures (list vs set, dict lookup)
- [ ] Large data processed in streams/chunks, not fully loaded
- [ ] Indexes exist for queried columns
- [ ] Caching used appropriately

### 🟡 Maintainability (Warning — should consider)

- [ ] Functions are focused (single responsibility)
- [ ] Function/variable names are descriptive
- [ ] Complex logic has explanatory comments
- [ ] Magic numbers have named constants
- [ ] Code duplication extracted into shared functions
- [ ] Tests cover the new behavior

### 🔵 Style (Info — good to have)

- [ ] Consistent with codebase conventions
- [ ] No dead code or unused imports
- [ ] Commit message follows convention

## Output Format

```markdown
## Code Review: [PR/commit title]

**Summary**: [1-2 sentences on what this does]

### 🔴 Critical

**[File:Line]** — [Issue description]
```code snippet```
**Fix**: [Concrete suggestion]

### 🟡 Warnings

- **[File:Line]** — [Issue + suggestion]

### 🔵 Info

- [Minor observations, style notes]

### ✅ Approved / 🚫 Changes Requested
```

## Quick Security Scan

```bash
# Detect potential secrets in diff
git diff main..HEAD | grep -iE "(password|secret|token|api_key|private_key)\s*=\s*['\"][^'\"]{8,}"

# Check for shell injection patterns
git diff main..HEAD | grep -E "(subprocess|os\.system|eval|exec)\s*\("

# Find hardcoded IPs or internal URLs
git diff main..HEAD | grep -E "\b(192\.168|10\.\d+|172\.1[6-9]|localhost:)\b"
```
