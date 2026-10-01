# Git Workflow (OpenShip)

Invoke this skill for any Git-related work on this repository.

## Core Rules

- Work in an **isolated worktree**, never directly on `main`.
- One worktree per feature/fix; reuse it for related follow-ups.

## Worktree Lifecycle

1. **Check existing worktrees:**
   ```bash
   git worktree list
   ```
   Reuse a relevant worktree if it already exists.

2. **Create a worktree** from the latest `main`:
   ```bash
   git fetch origin
   git worktree add -b <branch-name> /tmp/opencode/<task-slug> origin/main
   cd /tmp/opencode/<task-slug>
   ```

3. **Sync before starting a sub-task** (rebase, not merge):
   ```bash
   git fetch origin
   git rebase origin/main
   ```
   Resolve any uncommitted or unstaged work first.

## Branch Naming

- `feature/<issue-id>-<short-description>` — e.g., `feature/42-add-metrics`
- `fix/<issue-id>-<short-description>` — e.g., `fix/38-memory-leak`
- `task/<short-description>` — when no issue ID exists

## Commit & PR Strategy

- Commit often with clear messages after each logical change.
- One feature/fix per branch. Group related changes; do not open PRs after every small edit.
- Open a PR only when the feature/fix is complete and ready for review.

## Completing a Task

1. Push the branch:
   ```bash
   git push -u origin <branch-name>
   ```
2. Clean up the worktree (the branch is preserved on remote):
   ```bash
   cd <repo-root>
   git worktree remove /tmp/opencode/<task-slug>
   ```

## Privacy

Do not expose local development details in code or PRs:

- Avoid hardcoding local paths (e.g., `/home/username/...`, `C:\Users\...`).
- Do not reference local environment variables in code, comments, or PR descriptions.
- Use generic placeholders like `<repo-root>` or `<local-path>`.
