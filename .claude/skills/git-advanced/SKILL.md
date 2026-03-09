---
name: git-advanced
description: Advanced git operations — bisect, worktree, stash, cherry-pick, rebase, submodules, and more. Use for complex git workflows beyond basic add/commit/push.
metadata: {"ccbot":{"emoji":"🌿","requires":{"bins":["git"]}}}
---

# Git Advanced Skill

Use `git` via the `Bash` tool. Use `gh` CLI for GitHub-specific actions (see `github` skill).

## Worktrees (parallel branches without switching)

```bash
# Create worktree for a feature branch
git worktree add ../feature-login feature/login
cd ../feature-login  # independent working copy

# List all worktrees
git worktree list

# Remove when done
git worktree remove ../feature-login
```

## Stash

```bash
git stash push -m "WIP: auth refactor"      # save with message
git stash list                               # list stashes
git stash pop                                # apply + remove
git stash apply stash@{2}                   # apply specific, keep it
git stash drop stash@{0}                    # remove specific
git stash show -p stash@{0}                 # inspect diff
```

## Interactive Rebase

```bash
git rebase -i HEAD~5           # edit last 5 commits
# In editor: pick / squash / fixup / reword / drop
git rebase -i main             # rebase onto main
git rebase --abort             # cancel
git rebase --continue          # after resolving conflict
```

## Cherry-Pick

```bash
git cherry-pick abc1234                     # single commit
git cherry-pick abc1234..def5678            # range (exclusive start)
git cherry-pick -n abc1234                  # apply without committing
git cherry-pick --abort                     # cancel
```

## Bisect (find bug-introducing commit)

```bash
git bisect start
git bisect bad                              # current commit is broken
git bisect good v1.2.0                     # this tag was good

# git checks out a midpoint — test it, then:
git bisect good   # or: git bisect bad

# After finding the culprit:
git bisect reset
```

## Submodules

```bash
git submodule add https://github.com/owner/lib.git libs/lib
git submodule update --init --recursive    # init after clone
git submodule update --remote              # pull latest upstream
git submodule foreach git pull             # update all
```

## Find & Inspect

```bash
# Search commit messages
git log --oneline --grep="fix: auth"

# Search code changes across all commits
git log -S "function authenticate" --oneline

# Who changed this line? (blame)
git blame -L 42,55 src/auth.py

# What changed in a commit?
git show abc1234 --stat
git show abc1234 -- src/auth.py

# Diff between branches
git diff main..feature/login -- src/
```

## Undo & Fix

```bash
# Undo last commit, keep changes staged
git reset --soft HEAD~1

# Undo last commit, unstage changes
git reset HEAD~1

# Discard specific file changes
git checkout -- src/config.py

# Recover deleted branch (find commit hash first)
git reflog | grep "branch-name"
git checkout -b branch-name abc1234

# Revert a commit (safe for shared branches)
git revert abc1234
```

## Cleanup

```bash
# Delete merged local branches
git branch --merged main | grep -v "^\* " | xargs git branch -d

# Prune remote-tracking branches
git remote prune origin

# Clean untracked files (dry run first!)
git clean -nd    # show what would be removed
git clean -fd    # actually remove
```

## Log Formatting

```bash
git log --oneline --graph --decorate --all   # visual branch graph
git log --since="2 weeks ago" --author="Alice"
git log --format="%h %ad %s" --date=short
```

## Tips

- Always `git stash` before switching branches with dirty state.
- Use worktrees instead of multiple clones for parallel work.
- Prefer `git revert` over `git reset` for shared/pushed commits.
- `git reflog` is your safety net — commits are recoverable for ~30 days.
