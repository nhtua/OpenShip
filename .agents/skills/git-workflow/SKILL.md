---
name: git-workflow
description: skill for any Git-related work on this repository
---

# Git Workflow (OpenShip)

Invoke this skill for any Git-related work on this repository.

## Core Rules

- Work in an **isolated worktree**, never directly on `main`.
- One worktree per feature/fix; reuse it for related follow-ups.
- Always read commit messages, PR descriptions (creation and updates), and comments from text files in `/tmp/`. Scan each file with `rayloc scan` before using it. Follow **File-Based Messages** below, even for short messages.

## Worktree Lifecycle

1. **Check existing worktrees:**

   ```bash
   git worktree list
   ```

   Reuse a relevant worktree if it already exists.

2. **Create a worktree** from the latest `main`:

   ```bash
   git fetch origin
   git worktree add -b <branch-name> .worktrees/<task-slug> origin/main
   cd .worktrees/<task-slug>
   ```

3. **Install dependencies and pre-commit hooks** in the new worktree:

   ```bash
   # Frontend dependencies
   cd apps/web
   pnpm install
   cd ../..

   # Backend dependencies
   cd apps/api
   uv sync
   cd ../..

   # Install pre-commit hooks
   pre-commit install
   ```

4. **Sync before starting a sub-task** (rebase, not merge):

   ```bash
   git fetch origin
   git rebase origin/main
   ```

   Resolve any uncommitted or unstaged work first.

5. **Run tests before committing:**

   ```bash
   # Frontend typecheck, tests, and build
   cd apps/web
   pnpm typecheck
   pnpm test:run
   pnpm build
   cd ../..

   # Backend tests
   cd apps/api
   uv run pytest tests/ -q
   cd ../..
   ```

## Branch Naming

- `feature/<issue-id>-<short-description>` — e.g., `feature/42-add-metrics`
- `fix/<issue-id>-<short-description>` — e.g., `fix/38-memory-leak`
- `task/<short-description>` — when no issue ID exists

## Commit & PR Strategy

- **Validate before committing.** Always run relevant tests and typechecks before committing. Never commit code you haven't verified works.
- **Commit atomic, working changes.** Each commit should be a complete, tested unit of work that leaves the codebase in a working state.
- One feature/fix per branch. Group related changes; do not open PRs after every small edit.
- Open a PR only when the feature/fix is complete and ready for review.

## File-Based Messages

Keep message content out of shell command arguments. This preserves Markdown,
quotes, backticks, dollar signs, and newlines without shell interpolation.

1. Use the existing `/tmp/` directory directly, with task-specific filenames
   such as `/tmp/openship-<task>-commit-message.txt`. Do not create a temporary
   directory. Choose an unused filename rather than overwriting another task's file.
2. Write the exact UTF-8 text with a file-writing/editing tool. If a shell is
   needed, use a **single-quoted heredoc delimiter**, chosen so it does not
   occur as a line in the message:

   ```bash
   cat > '/tmp/openship-<task>-commit-message.txt' <<'COMMIT_MESSAGE_EOF'
   docs: require file-based Git messages

   Preserve literal `code`, $variables, and $(expressions) as text.
   COMMIT_MESSAGE_EOF
   ```

   Prepare separate files for the commit message, PR description, and comment.
   Use real newlines, not literal `\n` sequences.
3. Review the file's wording, then use Rayloc to check for secrets and credentials.
   **Immediately before every Git or GitHub command that consumes a message
   file**, scan that exact file in a separate tool call:

   ```bash
   rayloc scan '/tmp/openship-<task>-commit-message.txt'
   ```

   Proceed only when the scan succeeds with no findings. If Rayloc is missing,
   the scan fails, or it reports secrets or credentials, stop; do not run the
   Git/GitHub command or bypass the scan. Remove any sensitive content and
   rescan. Scan again after every edit and before every retry, including when
   reusing the same file for another operation. A staged secret scan does not
   replace this check because message files live outside the repository.
4. After the clean scan, run the appropriate short command as a separate tool call:

   | Operation | Required command pattern |
   | --- | --- |
   | Commit | `git commit --file '/tmp/openship-<task>-commit-message.txt'` |
   | Create PR | `gh pr create --base main --head '<branch-name>' --title 'Short literal title' --body-file '/tmp/openship-<task>-pr-description.md'` |
   | Update PR description | `gh pr edit <pr-number> --body-file '/tmp/openship-<task>-pr-description.md'` |
   | Add PR comment | `gh pr comment <pr-number> --body-file '/tmp/openship-<task>-comment.md'` |

   Use `--body-file` for issue comments and comment edits as well. For a PR
   description update, put the complete replacement description in the file.
   GitHub CLI has no `--title-file`; keep `--title` a short, safely quoted literal.

Never use inline `git commit -m/--message` or `gh --body/-b`, even for one line.
Do not embed message content in `echo`, `printf`, unquoted heredocs, `bash -c`,
`eval`, or chained commands. Do not read files back into command arguments with
`$(cat ...)` or backticks. JSON serialization is not shell escaping. Pass only
the quoted file path to `--file` or `--body-file`.

Keep the files for a retry if the operation fails; remove only the temporary
files you created after success.

## Completing a Task

1. Push the branch:

   ```bash
   git push -u origin <branch-name>
   ```

2. Clean up the worktree (the branch is preserved on remote):

   ```bash
   cd <repo-root>
   git worktree remove .worktrees/<task-slug>
   ```

## Privacy

Do not expose local development details in code or PRs:

- Avoid hardcoding local paths (e.g., `/home/username/...`, `C:\Users\...`).
- Do not reference local environment variables in code, comments, or PR descriptions.
- Use generic placeholders like `<repo-root>` or `<local-path>`.
