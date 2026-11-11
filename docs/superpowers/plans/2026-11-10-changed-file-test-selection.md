# Changed-File Test Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this session's plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow `git-workflow` for isolated work and verified, file-based commit messages.

**Goal:** Make the commit gate run the tests that can observe the commit's changes, in seconds, while the full credential-free lane remains the CI and pre-push gate.

**Architecture:** One pure mapper builds a static `ast` import graph over `apps/api/tests` + `apps/api/src` and returns a plan (`full-lane` / `selected` / `skip`); one launcher turns the git index into that plan and runs pytest with explicit file arguments; the web side mirrors the same contract with vitest. The hook entries call the launchers; the full lane moves to a `pre-push` script and CI.

**Tech Stack:** Python 3.11+ stdlib (`ast`, `pathlib`, `subprocess`, `argparse`), pytest, git, vitest 2 (`--changed`, `--reporter=json`), Node ESM, YAML hooks in `.pre-commit-config.yaml`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-10-changed-file-test-selection-design.md`

## Global Constraints

- The lane marker expression is exactly `not postgres and not provider_live and not full_stack and not jetstream`, defined once in `apps/api/scripts/run_selected_tests.py` as `LANE_MARKER_EXPRESSION`.
- Selection is module-granular. No per-test-function selection, no coverage DB, no cache, no `--last-failed`.
- The mapper uses stdlib only and builds its graph per run (measured 0.62s for 189 `src` + 223 `tests` files); nothing is persisted.
- Fail closed: mapper exception, `git` failure, pytest usage/collection error → non-zero exit, commit blocked. Never "no tests".
- A `Selected` plan with zero files is impossible: empty selection in a code domain is a `full-lane` plan.
- The launcher never runs `postgres`, `full_stack`, `provider_live` or `jetstream` lanes, and needs no DB, provider key, sandbox endpoint, broker, Playwright browser, source repo or live provider.
- `.github/workflows/python-ci.yml` and `.github/workflows/frontend-ci.yml` are not modified: CI remains the full-lane authority.
- No product code changes: `apps/api/src/**`, `apps/web/src/**`, `apps/api/pyproject.toml` (other than nothing), and the existing lane commands are untouched.
- `docs/validations.md` records measured timings and counts; a run that did not happen is recorded as unrun, never as passing.

## Review Focus

- **Renames and deletions in the index.** `git diff --cached --name-only -r HEAD` reports the new path for a rename and the old path for a deletion. Expected: a renamed test module is selected by its new path; a deleted `src` module selects the tests that imported it *before* the commit, and a deleted test module simply drops out. Pinned in Task 3 Step 1.
- **Non-ASCII and quoted paths.** `core.quotePath=true` makes git print `"apps/api/tests/test_\342\200\246.py"`. Expected: the launcher uses `-z` parsing so quoted-escape paths never reach the mapper as literal quotes. Pinned in Task 3 Step 1.
- **Dynamic-import-only consumers.** A src module reached only through `importlib`, a console entry point, or a CLI string has no import edge. Expected: `full-lane`, not an empty selection. Pinned in Task 2.
- **Very large selections.** A refactor touching many `src` modules may select most of the suite. Expected: the launcher runs exactly those modules with no size cap (the owner's ruling), and prints the count so the cost is visible. Pinned in Task 3.
- **Bypassed hooks.** `git rebase` and `git commit --no-verify` never run the mapper. Expected: the documented pre-push script is the gate those paths fall back on, and the docs say so in the same words. Pinned in Task 5 Step 1 (the wiring test requires the `pre-push` hook to exist) and Task 6 Step 1 (the docs name the bypass paths).
- **Inherited git environment inside the hook.** The hook process exports `GIT_DIR` (and, during its autostash, a git directory of its own), and a linked worktree has a git directory distinct from the parent checkout. Expected: the launcher reads the index of the repository it is *named*, never the one the process inherited, and a test fixture that builds a repository scrubs `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, the object-directory vars, `GIT_NAMESPACE`, and `core.hooksPath`/`init.templateDir` so its commits and the real repository's hooks stay out of each other. Pinned in Task 3 Steps 1–3 (`test_the_launcher_reads_the_index_of_the_repository_it_names`, `test_the_fixture_commits_land_in_their_own_repository`). This is not hypothetical: a non-scrubbed fixture committed into the repository running it, and the branch had to be reset.
- **RED tests never sit in the lane.** `tests/test_precommit_wiring.py` is red until the hook config is rewired, and the `api-tests` hook runs the whole lane, so the wiring test is committed in the *same* commit as the rewiring (Task 5 Step 3), not before it.
- **`--explain` is inspection, not execution.** The owner's ruling: "`--explain` should do the `--dry-run` jobs, just explain and don't run." The launcher prints the plan kind, the reason for every selected module and the exact command, and executes nothing; `--full-lane --explain` also runs nothing; the web launcher explains the plan and the whole-suite command without running the suite. Pinned in Task 3 (`test_explain_announces_the_plan_and_runs_nothing`, `test_explain_overrides_the_full_lane_flag`, `test_explain_on_a_skip_plan_runs_nothing`, `test_the_lane_runs_when_explain_is_absent`) and Task 4 ("explains the plan and runs nothing", "explains a whole-suite decision and still runs nothing").
- **Relative imports are edges, and the mapper resolves them.** `apps/api/src/openship` is written almost entirely in relative imports (`from .task_graph import …`, `from ...providers.errors import …`). A first implementation read only absolute module names, so `runs/task_graph.py` — imported only by `from .task_graph import` in `runs/dispatch.py` — mapped to no test module and every edit to it paid for the full lane. `build_graph` now resolves `ast.ImportFrom.level` against the importing file's own package, and `test_a_module_imported_only_by_a_relative_import_is_not_a_full_lane` pins the real-tree case (25 modules selected). Cost of the original behaviour: never a missed test (an empty selection is the full lane), but a narrowed gate that quietly degraded to the whole lane on most `src` edits. Pinned in Task 1.
- **Nothing edits a staged file while the lane runs.** The `api-tests` hook is the whole lane, ~14 minutes. Editing a file that is in the commit during that window — formatting it, applying ruff fixes, finishing a half-written version — makes the hook report `- files were modified by this hook` and abort the commit *after* the lane passed (observed: 2001 passed, then aborted). Land a launcher change fully, re-stage, then commit and do not touch tracked files until the commit object exists.

---

### Task 1: Import graph and transitive selection

**Files:**
- Create: `apps/api/scripts/testselect.py`
- Test: `apps/api/tests/test_testselect.py`

**Interfaces:**
- Consumes: nothing (pure stdlib).
- Produces: `APPS_API: Path`, `REPO_ROOT: Path`, `build_graph(api_root: Path) -> dict[str, set[str]]` (dotted module name → dotted names that import it), `tests_for_src(graph: dict[str, set[str]], module: str, api_root: Path) -> set[str]` (transitive closure through `src → src`, returned as repo-root-relative paths), and the plan types `FullLane(reason: str)`, `Selected(files: tuple[str, ...], reasons: tuple[tuple[str, str], ...])`, `Skip(reason: str)` with a `kind: str` attribute on each (`"full-lane" | "selected" | "skip"`); `Selected.files` are repo-root-relative.

- [x] **Step 1: Write the failing tests.** In `apps/api/tests/test_testselect.py`, build a fixture tree rooted at `tmp_path/"apps/api"` (so plan paths come out repo-root-relative) containing `src/openship/runs/service.py`, `src/openship/runs/tasks/plan.py` (imports `runs.service`), `tests/test_runs_service.py` (imports `src.openship.runs.service`), `tests/test_plan.py` (imports `src.openship.runs.tasks.plan`), and `tests/live/test_live.py` (imports it only through a string: `importlib.import_module("src.openship.runs.service")`). Assert `build_graph(api_root)` maps `"src.openship.runs.service"` to its two direct importers plus `"src.openship.runs.tasks.plan"`; assert `tests_for_src(graph, "src.openship.runs.service", api_root) == {"apps/api/tests/test_runs_service.py", "apps/api/tests/test_plan.py"}`; assert the `importlib` importer is absent from the closure; assert a fixture file that raises `SyntaxError` is skipped, not raised.

- [x] **Step 2: Run the tests to verify they fail.**
Run: `cd apps/api && uv run pytest -q tests/test_testselect.py`
Expected: FAIL — no mapper module yet. The tests load it with a module-level helper `load_mapper()` that uses `importlib.util.spec_from_file_location("testselect", APPS_API / "scripts/testselect.py")`, because `apps/api/scripts/` is not a package and the repo's precedent for script-directory modules is path-based loading (`scripts/sandbox_images.py` loaded by tests).

- [x] **Step 3: Implement the graph and closure in `apps/api/scripts/testselect.py`.** Exact signatures:

```python
def build_graph(api_root: Path) -> dict[str, set[str]]: ...
def tests_for_src(graph: dict[str, set[str]], module: str) -> set[str]: ...
```

Walk `api_root/"tests"` and `api_root/"src"` for `*.py`, parse each with `ast.parse`, and record edges from `ImportFrom(module=...)` / `Import(names=[...])` where the imported dotted name starts with `src.`; each edge is `(imported_module_dotted) -> {importing_module_dotted}`. `tests_for_src` walks importers transitively, collecting nodes under `tests/` and following `src.` nodes; guard against cycles with a visited set. Module dotted names come from the file path (`src/openship/runs/service.py` → `src.openship.runs.service`; `tests/test_x.py` → `tests.test_x`). A file that raises `SyntaxError` is skipped. Define `FullLane`, `Selected`, `Skip` as frozen dataclasses with a `kind` field now, so Task 2 only adds rules.

- [x] **Step 4: Run the tests to verify they pass.**
Run: `cd apps/api && uv run pytest -q tests/test_testselect.py && uv run ruff check tests/test_testselect.py scripts/testselect.py`
Expected: all pass, ruff clean.

- [x] **Step 5: Measure and record the real-graph cost** (the spec's no-cache justification).
Run: `cd apps/api && python3 -c "import time,sys; sys.path.insert(0,'scripts'); import testselect as t; s=time.time(); g=t.build_graph(t.APPS_API); print(len(g),'nodes', round(time.time()-s,3),'s')"`
Expected: a node count over 300 and an elapsed time under 2s. Record both numbers for Task 6.

- [x] **Step 6: Commit** (`test:` subject for the mapper, per `git-workflow` with a `--file` message after a clean `rayloc scan`).

### Task 2: Path rules — full-lane triggers, unmapped code, docs skip

**Files:**
- Modify: `apps/api/scripts/testselect.py` (add `map_files`, rule tables, `--explain` CLI)
- Test: `apps/api/tests/test_testselect.py`

**Interfaces:**
- Consumes: `build_graph`, `tests_for_src`, the plan dataclasses from Task 1.
- Produces: `map_files(changed: Iterable[str], *, api_root: Path = APPS_API, graph: dict[str, set[str]] | None = None) -> Plan`, `FULL_LANE_TRIGGERS: tuple[str, ...]` (prefix globs), `IGNORED_PREFIXES: tuple[str, ...]`, `CODE_PREFIXES: tuple[str, ...]`, and `main(argv: list[str]) -> int` printing `plan.kind`, its reason, and one `reason` line per selected file.

- [x] **Step 1: Write the failing tests.** Add cases over the fixture tree, each asserting `plan.kind` and that the reason names the triggering path:
  - `apps/api/src/openship/runs/service.py` → `Selected` whose files include `apps/api/tests/test_runs_service.py` and `apps/api/tests/test_plan.py` (repo-root-relative in the plan, per the Task 1 note).
  - `apps/api/tests/test_plan.py` → `Selected` containing itself.
  - a changed test-support module (`apps/api/tests/support.py` imported by `tests/test_plan.py`) → `Selected` containing `apps/api/tests/test_plan.py`.
  - `apps/api/tests/conftest.py`, `apps/api/pyproject.toml`, `apps/api/uv.lock`, `.pre-commit-config.yaml`, `apps/api/scripts/x.py`, `scripts/y.py`, `apps/api/alembic/versions/z.py`, `apps/api/tests/fixtures/a.json` → `FullLane`, each with its own reason text.
  - `docs/development.md`, `deploy/x.yaml`, `README.md`, `poc/agent/main.py` → `Skip`.
  - docs + `src/openship/runs/service.py` in one set → `Selected` (docs ignored, code selected).
  - an unmapped code file (`apps/api/src/openship/newthing.py`, imported by no test) → `FullLane` with a reason naming the file, **never** `Selected` with zero files.
  - the `importlib`-only consumer fixture from Task 1 → `FullLane`.

- [x] **Step 2: Run to verify they fail.**
Run: `cd apps/api && uv run pytest -q tests/test_testselect.py`
Expected: FAIL with `AttributeError: module 'testselect' has no attribute 'map_files'`.

- [x] **Step 3: Implement `map_files` and the CLI.** Order of decisions, each returning immediately: (1) any changed path matching `FULL_LANE_TRIGGERS` → `FullLane(reason naming that path)`; (2) drop every path matching `IGNORED_PREFIXES`; if nothing remains → `Skip("no file the test lanes observe")`; (3) for each remaining path in `CODE_PREFIXES` (`apps/api/src/`, `apps/api/tests/`), collect its selection (src → `tests_for_src`, test module → itself, test-support module → its importers); (4) any code path whose selection is empty → `FullLane(f"{path} maps to no test module")`; (5) union the selections into `Selected(files, reasons)`. `main` prints the plan, and with `--explain` prints every `(file, reason)` pair; exits 0 on any plan (the launcher, not the mapper, decides).

- [x] **Step 4: Run to verify they pass.**
Run: `cd apps/api && uv run pytest -q tests/test_testselect.py && uv run ruff check scripts/testselect.py tests/test_testselect.py`
Expected: all pass, ruff clean.

- [x] **Step 5: Exercise the real graph** on paths that exist in this repo, with `--explain`:
Run: `cd apps/api && uv run python scripts/testselect.py --explain apps/api/src/openship/runs/service.py`
Expected: `selected`, with a reason line per selected test module naming `src.openship.runs.service`. Record the selected count for Task 6.

- [x] **Step 6: Commit.**

### Task 3: Launcher — index to plan to command

**Files:**
- Create: `apps/api/scripts/run_selected_tests.py`
- Test: `apps/api/tests/test_run_selected_tests.py`

**Interfaces:**
- Consumes: `map_files`, `Selected`, `FullLane`, `Skip` from Tasks 1–2 (loaded by path, as in Task 1's `load_mapper()`).
- Produces: `LANE_MARKER_EXPRESSION: str`, `changed_files_from_index(repo_root: Path) -> list[str]`, `build_command(plan: Plan, *, apps_api_prefix: str = "apps/api/") -> list[str]`, and `run(argv: list[str], *, repo_root: Path, changed: list[str] | None = None, runner: Callable[[list[str]], int] = subprocess_run_exit_code) -> int` returning pytest's exit code; `Skip` returns 0 without invoking `runner`.

- [x] **Step 1: Write the failing tests.** With an injected `runner` that records commands and returns 0, and `changed=` supplied directly (no git):
  - `Selected` plan → recorded command is `["uv","run","pytest","-q","-m",LANE_MARKER_EXPRESSION,"tests/test_runs_service.py","tests/test_plan.py"]`, i.e. the `apps/api/` prefix stripped, files sorted, marker expression present verbatim.
  - `FullLane` plan → recorded command is the same prefix with **no** file arguments.
  - `Skip` plan → `runner` never called, return code 0, stdout contains the reason.
  - a 120-file `Selected` plan → the launcher issues one command (no chunking, no size cap) and prints `selected 120 test modules`.
  - a mapper that raises → `run` returns non-zero and prints `mapper failed`.
  Then, in a `tmp_path` git repo (initialize, commit a base tree, stage edits with `git add`):
  - `changed_files_from_index(repo_root)` returns exactly the staged paths, repo-root-relative, sorted: for one staged addition and one staged modification, the two paths; a staged deletion appears only as its old path and a staged rename only as its new path, which is all `-z --name-only` reports;
  - a path the filesystem spells `apps/api/tests/test_ünïcode.py` comes back exactly as spelled, because `-z` output is NUL-terminated and never path-quoted;
  - with no commits yet (`HEAD` unknown), the `git diff` call raises and `run` prints `git failed: …` and returns non-zero.

- [x] **Step 2: Run to verify they fail.**
Run: `cd apps/api && uv run pytest -q tests/test_run_selected_tests.py`
Expected: FAIL — the launcher module does not exist.

- [x] **Step 3: Implement the launcher.** `changed_files_from_index` runs `git diff --cached --name-only -z -r HEAD` with `cwd=repo_root` and splits on `NUL`. `build_command` is the pure command-shape function above. `run` prints `plan.kind` plus reasons (with `--explain` printing the full reason table), builds the command, prints it, and returns `runner(command)`. On `Skip` it prints `skip: <reason>` and returns 0. Wrap mapper and git failures in a printed `mapper failed: …` / `git failed: …` line and return 2. **Owner ruling recorded during implementation:** `--explain` is the inspection mode — it does the `--dry-run` job, just explained. It prints the plan kind, the reason for every selected module, and the exact command, and **executes nothing**; `--full-lane --explain` likewise runs nothing, so asking to see the gate's command can never start the gate. The commit hook and the pre-push gate invoke the launcher without the flag, so the gate itself is unaffected.

- [x] **Step 4: Run to verify they pass.**
Run: `cd apps/api && uv run pytest -q tests/test_run_selected_tests.py tests/test_testselect.py && uv run ruff check scripts/run_selected_tests.py tests/test_run_selected_tests.py`
Expected: all pass, ruff clean.

- [x] **Step 5: Prove it on a real staged narrowing** — stage one real `src` file edit and run the launcher for real:
Run: stage a one-line whitespace-neutral edit to `apps/api/src/openship/runs/tasks/projections.py`, then `cd apps/api && uv run python scripts/run_selected_tests.py --explain` to audit the plan (it runs nothing), and re-run the same command **without** `--explain` to measure the narrowed run; record the selected count and wall time, then `git reset` that file.
Expected: exits 0 after running only the mapped modules, in well under the ~13-minute full-lane time; recorded numbers go to Task 6. Unstage before committing.

- [x] **Step 6: Commit.**

### Task 4: Web launcher with the same contract

**Files:**
- Create: `apps/web/scripts/run-changed-tests.mjs`
- Test: `apps/web/tests/RunChangedTests.test.ts`

**Interfaces:**
- Consumes: `vitest list --changed --filesOnly --json=<tmp json>` for the run-free suite count (a JSON array of suite file entries; collection never executes a test), and `vitest run [--changed]` for the run itself; git via `git diff --name-only -z HEAD` for the web-domain check.
- Produces: exported `planFor(changedWebFiles: string[], relatedSuites: number): "skip" | "changed" | "full"`, `webFilesUnder(nulSeparatedPaths: string): string[]`, and `main(argv: string[], deps?: { changed, list, run }): Promise<number>` — the dependency seam the tests inject, so the launcher's run behaviour (including "`--explain` runs nothing") is pinned without touching git or vitest; `WEB_SRC_PREFIX = "apps/web/src/"`.

- [x] **Step 1: Write the failing vitest cases** in `apps/web/tests/RunChangedTests.test.ts` against the exported pure function:
  - `planFor(["apps/web/src/stores/taskProgress.ts"], 3) === "changed"`;
  - `planFor(["apps/web/src/new/widget.ts"], 0) === "full"` (unmapped web code is never "nothing to check");
  - `planFor(["apps/web/tests/TaskProgressStore.test.ts"], 1) === "changed"`;
  - `planFor(["apps/web/README.md"], 0) === "skip"`;
  - `planFor([], 0) === "skip"`.

- [x] **Step 2: Run to verify they fail.**
Run: `cd apps/web && pnpm test:run tests/RunChangedTests.test.ts`
Expected: FAIL — cannot resolve `../scripts/run-changed-tests.mjs`.

- [x] **Step 3: Implement `planFor`, `webFilesUnder` and `main`.** `main` reads changed files under `apps/web/` from `git diff --name-only -z HEAD` (index + working tree, matching vitest's own base), counts the suites `vitest list --changed --filesOnly --json=<tmp>` reaches — collection only, no run — decides with `planFor`, and runs `vitest run --changed`, or `vitest run` when the decision is `full`. With `--explain` it prints the plan, the changed web files, the suite count and the exact command, and **runs nothing** (the same inspection-mode ruling as the API launcher; a run-free `vitest list` is what makes an honest explanation possible). A git failure returns 2, a collection failure 1, and a suite failure propagates vitest's exit code. It prints the decision and the command.

- [x] **Step 4: Run to verify they pass.**
Run: `cd apps/web && pnpm test:run tests/RunChangedTests.test.ts && pnpm typecheck`
Expected: all pass, typecheck clean.

- [x] **Step 5: Prove the real narrowing** on a staged `apps/web/src/**` edit, recording the suite count and time (both go to Task 6).

- [x] **Step 6: Commit.**

### Task 5: Hook rewiring and the pre-push gate

**Files:**
- Modify: `.pre-commit-config.yaml` (`api-tests` entry, `frontend-typecheck` / `frontend-tests` triggers, new `pre-push` hook)
- Create: `scripts/openship-verify`
- Test: `apps/api/tests/test_precommit_wiring.py`

**Interfaces:**
- Consumes: `apps/api/scripts/run_selected_tests.py`, `apps/web/scripts/run-changed-tests.mjs`, the existing lane command.
- Produces: hook ids `api-tests` (launcher entry), `frontend-tests`, `frontend-typecheck`, `openship-verify` with `stages: [pre-push]`; executable `scripts/openship-verify` printing one line per gate.

- [x] **Step 1: Write the failing wiring test** `apps/api/tests/test_precommit_wiring.py`, parsing `.pre-commit-config.yaml` with `import yaml` (a runtime dependency of `apps/api`, `pyyaml>=6.0.3,<7`), locating hooks by their `id`:
  - the hook with `id: api-tests` has an `entry` containing `scripts/run_selected_tests.py` and `pass_filenames: false`;
  - `frontend-typecheck` and `frontend-tests` have a `files` pattern starting `^(apps/web/` and no `always_run` key;
  - a hook exists with `stages: [pre-push]` whose `entry` contains `scripts/openship-verify`;
  - the string `uv run pytest -q -m 'not postgres` appears in the config exactly once (in the pre-push lane script reference, not duplicated as a commit hook) and `LANE_MARKER_EXPRESSION` is not redefined outside `apps/api/scripts/run_selected_tests.py` (asserted by grepping `apps/api/scripts/` and `scripts/`).

- [x] **Step 2: Run to verify it fails.**
Run: `cd apps/api && uv run pytest -q tests/test_precommit_wiring.py`
Expected: FAIL on the first assertion (the `api-tests` entry still runs the full lane).

- [x] **Step 3: Rewrite the hook entries.** `api-tests` → `bash -c "cd apps/api && uv run python scripts/run_selected_tests.py"`. Give `frontend-typecheck` and `frontend-tests` `files: ^(apps/web/|pnpm-lock\.yaml$)` and drop `always_run: true`. Add `apps/web/scripts/run-changed-tests.mjs` as the `frontend-tests` entry. Add the `pre-push` hook `bash scripts/openship-verify` with `pass_filenames: false`, `always_run: true`, `stages: [pre-push]`. Keep `api-ruff`, `ruff-lint`, `pytest` (poc) and `rayloc-staged` untouched.

- [x] **Step 4: Write `scripts/openship-verify`** (`set -euo pipefail`, executable): full credential-free lane, `pnpm typecheck`, `pnpm test:run`, `rayloc scan .`, each preceded by one printed gate line, failing on the first non-zero exit.

- [x] **Step 5: Run the wiring test and the hook end-to-end.**
Run: `cd apps/api && uv run pytest -q tests/test_precommit_wiring.py && cd ../.. && pre-commit run --all-files 2>&1 | tail -12`
Expected: wiring test passes; `pre-commit run --all-files` reports every hook `Passed` with `api-tests` having run a selection (its output names the selected module count) — record what it printed.

- [x] **Step 6: Prove the pre-push gate is installed and green.**
Run: `pre-commit install --hook-type pre-commit --hook-type pre-push && bash scripts/openship-verify`
Expected: installs cleanly, then the script completes with the full credential-free lane's pass count and the web suite's pass count. Record both counts.

- [x] **Step 7: Commit.**

### Task 6: Documentation and measured evidence

Status (2026-11-10): Tasks 1-5 are landed in `06cc90a feat: run the tests a commit can affect` — one commit, per the owner's ruling that four per-task commits would each pay the ~14-minute lane this plan replaces. Its own hook run is the lane evidence (`2001 passed, 169 deselected` in the API hook, `Vue Typecheck` and `Frontend Unit Tests` passed, secret scan passed, commit `EXIT:0`). Tasks 1-5 steps are checked against that evidence; Steps 1-5 here stay open until the docs and the measured numbers below land.

**Files:**
- Modify: `docs/development.md` (test-selection contract, pre-push command, bypass paths, blind spots)
- Modify: `docs/validations.md` (measured counts and timings, commands, what is unrun)

**Interfaces:**
- Consumes: the launcher outputs from Tasks 3–5 and the graph node count from Task 1 Step 5.
- Produces: nothing consumed by code; the contract text contributors and reviewers read.

- [x] **Step 1: Write the `docs/development.md` section.** One section, named `Changed-file test selection`, stating: the hook runs `scripts/run_selected_tests.py` and `vitest run --changed`; the full credential-free lane runs in CI and in `scripts/openship-verify`; `git rebase` and `git commit --no-verify` bypass the commit hooks, so `scripts/openship-verify` before pushing is the gate for them; merge commits run the same hooks against the merge's own index, and a merge that touches unmapped code hits the full-lane rule; the selection rules table verbatim from the spec; the blind spots verbatim from the spec (dynamic imports, `config.py` via environment, seed data, compose/`.env` effects, the DB/live/full-stack lanes); and the "unmapped code ⇒ full lane" rule called out as the fail-closed guarantee.

- [x] **Step 2: Write the `docs/validations.md` entry** with: graph node count and build time; the selected module count and wall time for the Task 3 Step 5 and Task 4 Step 5 real narrowings; the `pre-commit run --all-files` hook summary; the `scripts/openship-verify` full-lane and web pass counts with their durations; and an explicit line that no `postgres`, `full_stack`, `provider_live` or `jetstream` lane ran, with the command that runs them.

- [x] **Step 3: Verify the docs render and link.**
Run: `grep -n "Changed-file test selection" docs/development.md && grep -n "openship-verify" docs/development.md docs/validations.md`
Expected: the section heading exists once, and both files name the pre-push command.

- [x] **Step 4: Run the whole credential-free suite once more** so the plan's own tests are inside the lane CI runs.
Run: `cd apps/api && uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'`
Expected: all pass; record the pass count and duration, and the delta versus the previous recorded lane count (the new mapper/launcher/wiring tests add to it).

- [ ] **Step 5: Commit** (message records the measured numbers, and states explicitly that CI is unchanged and no live lane ran).
