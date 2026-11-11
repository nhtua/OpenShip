---
type: spec
summary: The changed-file launcher runs the full lane as parallel pytest shards using the existing CI shard plugin; narrowed selections stay single-process.
status: approved
date: 2026-11-10
---

# Parallel Shards for the Launcher's Full Lane

## Problem

`scripts/run_selected_tests.py` narrows a commit to the modules its staged
files can reach, and runs the whole credential-free lane when the change is
lane-wide (migrations, `scripts/**`, `.pre-commit-config.yaml`, …). The lane is
one `pytest` process: 2001 tests, 853 s measured on this branch
(`docs/validations.md`). CI does not pay that cost — `.github/workflows/python-ci.yml`
and `typescript-ci.yml` split the suite into shards (`shard_index: 0..3`) and
run them in parallel, using `apps/api/scripts/pytest_shard.py`, a deterministic
partition plugin that keeps each module in one shard and greedy-balances by test
count. Locally, every `pre-push` and every `scripts/openship-verify` pays the
single-process 14 minutes.

## Decisions

1. **Reuse the plugin, do not write a new one.** `scripts/pytest_shard.py` is
   already the CI contract and already has its own tests
   (`tests/test_pytest_shard.py`). The launcher drives it with N processes,
   exactly as the workflow does: `-p scripts.pytest_shard` with
   `OPENSHIP_TEST_SHARD` / `OPENSHIP_TEST_SHARDS` in the environment.
2. **Auto-shard the full lane only** (ruling 2026-11-10). A narrowed selection
   of a few dozen modules gains nothing from four process starts, so
   auto-shard applies when the plan is `FullLane` and the shard count is
   `min(8, cpu_count)`. `--shards N` forces any number on any plan;
   `--shards 1` is today's behaviour. A forced N is capped at the number of
   modules in a narrowed plan — a shard beyond that collects nothing.
3. **The lane definition stays one string.** `LANE_MARKER_EXPRESSION` remains
   the only place the marker expression is written; each shard command is
   `PYTEST_PREFIX (+ files) -p scripts.pytest_shard`. Sharding changes how the
   lane is *run*, never what it *contains*.
4. **Aggregation is explicit.** Shards run concurrently, each with its output
   captured. The launcher prints one line per shard (`shard 1/4 exit 0: 503
   passed, 42 deselected in 214.02s`), then the summed lane
   (`2000 passed in 8 shards, 152.6s`). A shard that failed has
   its full output printed so tracebacks survive.
5. **Exit codes are read with their meaning.** `0` = passed, `5` = collected
   nothing (a shard above the module count; not a failure), anything else
   (1 failures, 2/3/4 interrupt/internal/usage) fails the lane. The launcher
   exits non-zero if any shard is not in `{0, 5}`.
6. **`--explain` announces the shards and runs nothing**, consistent with the
   ruling that `--explain` does the `--dry-run` jobs and explains.
7. **The single-process seam is untouched.** The launcher's `runner` seam (used
   by every narrowed plan and by all existing pins) keeps its exact signature
   and streams output live. Shards go through a separate `shard_runner` seam
   that captures output, because a report needs each shard's summary.
8. **Safety of running the lane in parallel.** The credential-free lane binds
   no ports and no broker (postgres/provider_live/full_stack/jetstream are
   deselected by the marker expression), `tmp_path` is per-process unique, and
   the plugin keeps module-scoped fixtures inside one process. `.pytest_cache`
   is last-write-wins and harmless.
9. **A lane inside a shard never fans out.** Every shard runs this suite, so an
   unguarded auto-shard recurses: a real full lane inside a real full lane, each
   level running every test again. Measured 2026-11-10: a 35-deep chain of
   launcher and pytest processes before it was killed. The rule is now
   structural: with `OPENSHIP_TEST_SHARD` in the environment the launcher runs
   one process (auto), and an explicit `--shards N>1` is refused with the reason
   printed. Independently, a caller that injects a `runner` gets that executor
   for shards too (`_shard_via`), so a fake executor can never reach a real lane.

10. **A sharded lane exposed a live dependency.** `test_health_endpoint_exists`
    asserted `/ready` returns 200, but the app reads the repository `.env`, so
    that assertion described the developer's running stack (database, worker
    lease, sandbox profiles), not the code: four shards pinged it concurrently
    and one saw 503 with no code change between them. The lane now asserts the
    endpoint's contract (`200` or `503`, with `status`); "it becomes ready" is
    an assertion for the stack that can be ready. The summed report also stopped
    adding per-shard deselections: the plugin deselects the items it assigned to
    the other shards, so each shard's deselected count is most of the suite.

## Non-goals

- No `pytest-xdist`: it changes fixture and collection semantics for the whole
  suite, while the existing plugin is already the CI partition contract.
- No change to CI: the workflows already shard across jobs.
- No sharding of the web launcher this round (its gate is file-filter based and
  its suite runs 4 shards in CI already).
- No sharding of narrowed selections by default.

## Plan

- [x] Task 1: launcher shard tests (RED). `tests/test_run_selected_tests.py`
      gains: full lane auto-splits into `min(8, cpu)` shard commands naming the
      plugin; narrowed stays single process; `--shards N` forces and is capped
      by module count; any failing shard fails the lane; exit 5 is not a
      failure; per-shard and summed report lines; failing shard prints its
      output; `--full-lane --explain` announces shards and runs nothing;
      `--shards 0` is refused; one real two-shard run partitions a module file.
      Verified GREEN with the launcher suite: 28 passed, 1 deselected
      (`uv run pytest -q tests/test_run_selected_tests.py tests/test_testselect.py tests/test_precommit_wiring.py`, 111.34 s).
- [x] Task 2: implement `--shards`, shard commands, concurrent scheduling,
      aggregation, and the report in `scripts/run_selected_tests.py`.
      Also implemented after the recursion incident: the nested-shard guard and
      the `_shard_via` seam (an injected runner serves shards too). Launcher
      suite: 31 passed, including one real two-shard run over two module files.
- [x] Task 3a: measure the lane both ways on this tree.
      Single process `851.34 s` (853 s in the earlier gate run). Same tree,
      same machine, minutes apart:
      `249.1 s` with 4 shards (3.4x), `152.6 s` with 8 (5.6x). Both shard runs
      reported the same `2036 passed` plus the known docs failure
      (`test_packaging` asks for the three `AGENTIC_*` bindings the in-progress
      `.env.example` does not name yet). The default is 8 because 8 was
      measurably faster, capped by `cpu_count` so a small machine keeps its
      cores.
- [ ] Task 3b: record the feature commit's own hook run (the launcher
      sharding its own full-lane gate) and the `docs/validations.md` entry.
- [ ] Task 4: document in `docs/development.md` (gate section) and `.env`
      notes: auto-shard on full lane, `--shards N`, narrowed stays single
      process.
