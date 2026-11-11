---
type: spec
summary: Pre-commit runs the tests related to the changed files through a static import-graph mapper; the full credential-free lane stays the CI and pre-push gate.
status: approved
date: 2026-11-10
---

# Changed-File Test Selection for the Commit Gate

## Problem

`.pre-commit-config.yaml` gates every `apps/api/` commit on the whole
credential-free lane (`not postgres and not provider_live and not full_stack and
not jetstream`, ~1958 tests, ~13 minutes) and gates every commit at all on the
web typecheck and web unit tests (`always_run: true`). A one-line doc fix pays
for a full-lane run; a `runs/service.py` edit pays for the same 13 minutes it
would pay for a `sandbox_images.py` edit. The cost makes contributors want to
skip the gate, and a skipped gate is the failure mode that matters.

## Goal

A commit runs the tests that can observe what it changed, in seconds, while the
full credential-free lane keeps running in CI and becomes a pre-push command.
The selection is a *narrowing* of the same lane, never a new assurance level:
when the mapper cannot see a dependency, it runs the full lane.

## Non-goals

- Per-test-function granularity inside a selected module. Module granularity is
  the contract.
- Coverage-guided selection (`pytest-testmon`, `--last-failed` caches). A stale
  dependency cache is a silent-miss machine; a committed commit gate must be
  reproducible from the repository alone.
- Running `postgres`, `full_stack`, `provider_live` or `jetstream` lanes from a
  hook. Those lanes keep their explicit selectors and prerequisites.
- Replacing CI. `.github/workflows/python-ci.yml` and `frontend-ci.yml` remain
  the authority; the launcher has no shard plugin and changes no CI command.

## Interfaces

### `apps/api/scripts/testselect.py` — the mapper

Pure stdlib (module `ast`, `pathlib`), no new dependency, no cache.

```python
Plan = FullLane(reason) | Selected(files, reasons) | Skip(reason)

def map_files(changed: list[str]) -> Plan: ...
def build_graph(api_root: Path) -> ImportGraph: ...
```

`build_graph` parses every `apps/api/tests/**/*.py` and `apps/api/src/**/*.py`
with `ast`, records `test module → src module` edges from `import src.openship…`
and `from src.openship… import …`, plus `src module → src module` edges —
**including relative ones**, resolved against the importing file's own package,
because `src/openship` is written almost entirely as `from .task_graph import …`
and `from ...providers.errors import …`. A graph that read only absolute module
names mapped `runs/task_graph.py` (imported only relatively) to no test module,
which made every edit to it pay for the full lane. The mapper then closes each
changed src module transitively. Measured cost: 189 `src` files plus
223 `tests` files (5582 import statements) in **0.62s**, which is why there is
no cache and no cache-invalidation logic to get stale.

CLI: `uv run python scripts/testselect.py --explain <files…>` prints the plan
and one reason line per selected test module
(`tests/test_runs_service.py ← src/openship/runs/service.py`). The mapper is
importable so its rules are unit-testable without git.

### `apps/api/scripts/run_selected_tests.py` — the launcher the hook calls

```bash
cd apps/api && uv run python scripts/run_selected_tests.py [--explain]
```

`--explain` is the inspection mode (the owner's ruling): it prints the plan, the
reason for every selected module and the exact command, and **runs nothing**.
`--full-lane --explain` behaves the same - asking to see the gate's command never
starts the gate.

1. Reads the commit's file set from the index:
   `git diff --cached --name-only -r HEAD` (includes staged additions and
   deletions; untracked files are not in the commit and are not selected).
2. Calls `map_files`.
3. `Skip` → prints the reason, exits 0.
4. `FullLane` → runs the lane command verbatim:
   `uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'`.
5. `Selected` → runs
   `uv run pytest -q -m '<same expression>' <explicit test file arguments>`,
   printing the count and the command before pytest's own output.

The lane marker expression is defined once, in this launcher. The hook entry
only calls the launcher, so hook and launcher cannot drift.

### `apps/web/scripts/run_changed_tests` — the web side, same contract

`vitest run --changed` (vitest 2 compares the working tree and index against
`HEAD`), with the same fail-closed rule as the API side: if a changed
`apps/web/src/**` file yields zero related test files, run full `vitest run`.
Typecheck stays a separate hook and is no longer `always_run`.

### `.pre-commit-config.yaml`

- `api-tests`: entry becomes `bash -c "cd apps/api && uv run python scripts/run_selected_tests.py"`,
  same `files: ^apps/api/` trigger, `pass_filenames: false`.
- `frontend-tests` / `frontend-typecheck`: `always_run: true` becomes
  `files: ^(apps/web/|pnpm-lock\.yaml$)` so a docs-only commit does not pay for
  Chromium-adjacent tooling it cannot affect.
- New hook on `stages: [pre-push]`: `scripts/openship-verify`.

### `scripts/openship-verify` — the branch gate

Full credential-free lane, `pnpm typecheck`, `pnpm test:run`, and
`rayloc scan` over the whole tree. Documented in `docs/development.md` as the
command to run before pushing, and installed with
`pre-commit install --hook-type pre-commit --hook-type pre-push`.

## Selection rules

| Changed path | Outcome |
| --- | --- |
| `apps/api/src/**` | tests importing it, transitively through `src → src` |
| `apps/api/src/**` with no importer | **full lane** |
| `apps/api/tests/**.py` | that module, plus modules importing a changed test-support module |
| `apps/api/alembic/**` | **full lane** (no test imports a migration) |
| `apps/api/tests/**/conftest.py`, `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, `apps/api/scripts/**`, `scripts/**` | **full lane** |
| `apps/api/tests/fixtures/**`, `apps/api/tests/*.json` data | **full lane** (fixtures are reached by data, not by import) |
| `apps/web/src/**`, `apps/web/tests/**` | `vitest run --changed`; zero related tests for a `src/**` change → full `vitest run` |
| `docs/**`, `*.md`, `deploy/**`, `poc/**`, root config the lanes never read | ignored; a commit whose files are all ignored → `Skip` |

Two guards were considered and **declined** by the project owner, recorded so
the next reader knows the boundary is deliberate:

- *"large selection → full lane"*: no size denominator and no percentage rule.
  The launcher collects and runs what it finds. Selection overhead is ~1s of
  graph building; a selection can never exceed the full lane.
- *a separate "migration/config/compose/env-example → full lane" category*:
  folded into the single rule above — a code-domain change that maps to no test
  module runs the full lane.

## Failure and safety story

- **Fail closed everywhere.** A mapper exception, a failed `git diff`, a pytest
  usage error or a collection error exits non-zero and blocks the commit. The
  launcher never degrades to "no tests".
- **Empty selection is never a pass.** Only `Skip` (all files outside the code
  domains) exits 0 without running tests, and it prints its reason.
- **Deletions.** A deleted `src` module selects every test that imported it;
  they run and fail, which is the correct verdict. A deleted test module simply
  drops out of the graph.
- **Merge commits.** The index-vs-`HEAD` diff is the merge's own change set; a
  merge touching unmapped code hits the full-lane rule.
- **Rebase and `--no-verify`.** `git rebase` does not run `pre-commit` at all,
  and neither does `--no-verify`. Those paths are covered by
  `scripts/openship-verify` before pushing and by CI, not by the hook.
- **Auditability.** Every run prints its plan (`full-lane: …alembic/versions/…
  maps to no test module`), the selected module count, and the exact pytest
  invocation. `--explain` reproduces it after the fact, and runs nothing: the
  same decision, its per-file reasons, the command it would issue.
- **Named repository, inherited environment.** The launcher reads the index of the
  repository it is *named*, never the one the process inherited: it resolves
  `repo_root`'s own git directory with `git rev-parse --absolute-git-dir` under a
  scrubbed environment and pins `GIT_DIR`, `GIT_WORK_TREE` and `GIT_INDEX_FILE`
  for the diff. This is not theoretical. The commit hooks export `GIT_DIR`, and
  during autostash they point it at a git directory of their own; a linked
  worktree has a git directory distinct from the parent checkout. A test fixture
  that built repositories without scrubbing them committed into the repository
  running the tests and emptied its tracked tree, which had to be reset. A
  fixture that shells out to git scrubs the inherited `GIT_*` variables and
  neutralizes `core.hooksPath` and `init.templateDir`, so neither the real
  repository's hooks nor its git directory can reach the fixture.
- **Prerequisites.** The credential-free lane needs no PostgreSQL, provider key,
  sandbox endpoint or broker, exactly as today's `api-tests` hook needs none; the
  launcher adds no new environment requirement.

## Known blind spots (recorded, not hidden)

The graph sees `import`. It does not see: code reached through
`importlib`/entry points/CLI strings, `apps/api/src/openship/config.py` read
from the environment by fixtures, seed data, `docker-compose*.yml` and `.env*`
effects on behavior, or the `postgres`/`full_stack`/`live` lanes. Mitigations
are structural, not hopeful: the full-lane triggers cover conftest, packaging,
scripts, migrations and fixtures; unmapped code runs the full lane; CI runs the
full lane in shards; `scripts/openship-verify` runs it before push.

## Acceptance criteria

Unit tests for the mapper (`apps/api/tests/test_testselect.py`, no git, no
network, no DB):

- a changed `src/openship/runs/service.py` selects the modules importing it;
- a transitive `src → src` edge selects the importer's importers;
- a changed test-support module selects the tests importing it;
- `tests/conftest.py`, `pyproject.toml`, `uv.lock`, a fixture JSON, and
  `alembic/versions/x.py` each yield `FullLane` with a named reason;
- a `docs/**`-only set yields `Skip`; a mixed `docs/**` + `src/**` set selects;
- an unmapped `src/**` file yields `FullLane`, never `Selected([])`;
- a deleted `src` module selects its importers.

Launcher tests (git fixtures in a temp repo, `pytest -m 'not postgres …'`):

- `--explain` output contains the reason line for every selected module;
- a docs-only staged commit exits 0 without invoking pytest;
- a full-lane decision invokes the lane expression verbatim;
- a `Selected` decision passes explicit file arguments, never a bare lane run;
- a mapper failure exits non-zero.

Web: `apps/web/scripts/run_changed_tests` runs `vitest run --changed` for a
related change, and full `vitest run` when a `src/**` change relates to nothing.

Docs: `docs/development.md` records the launcher contract, the pre-push command,
the frontend hook trigger change, and the blind spots above.
`docs/validations.md` records the measured lane counts before and after, with
commands and timings, so the narrowing is evidence and not a claim.
