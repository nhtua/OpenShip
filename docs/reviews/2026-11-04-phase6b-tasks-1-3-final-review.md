---
type: review
summary: Tasks 1–3 need scope, uncertainty, output and preparation corrections before Task 4.
date: 2026-11-04
status: changes-required-before-task-4
---

# Phase 6B Tasks 1, 1A, 2 and 3 — final review

## Assessment

**Not ready to advance to Task 4.** This supersedes the earlier inline
"ready to merge" assessment. The provider transport and ordinary fenced
publication are useful foundations, but the current tests miss important
failure paths and encode the old per-root-run reuse rule.

Review performed inline, without subagents. Implementation reviewed at
`7722a9d`, against the approved Phase 6B plan/spec and the user's subsequent
scope clarification. No application code was changed for this review.

## Scope correction

- A workflow can have many runs; distinct run IDs do not imply distinct
  workflows or require distinct provider sandboxes. Reuse is compatible,
  unexpired execution-environment reuse, not reuse of a run's working files.
- No saved-workflow implementation or conversation-to-workflow conversion
  exists yet. Do not add a workflow entity, compiler, cron scheduler or
  conversion feature to solve this Phase 6B correction.
- For the current conversation-backed slice, preserve the ongoing conversation
  context across submitted run/attempt records. A new run/resume identity must
  not by itself force a new sandbox. Keep project/conversation boundaries.
  A future explicit workflow identity must preserve separation between
  different workflows; conversation identity is not a universal workflow ID.
- Give each execution a fresh directory containing its complete selected
  inputs. Its working set and produced-artifact identities remain independent
  of earlier runs. Do not turn sandbox reuse into an implicit shared workspace
  or add another artifact store.
- The earlier ledger statement that pinning an artifact proves workflow
  membership was not established and is withdrawn. Neither artifact provenance
  nor a root run ID is, by itself, an implemented workflow identity.
- Existing spec lines 100–110 and 165–169, plan Task 2's one-row-per-root rule,
  and tests must be reconciled with this clarification before implementation.
  In particular, distinguish restarting the journey with initial refs from
  requiring a new container. Do not silently change the ordinary Run lifecycle.

## Strengths to preserve

- Fixed Python argv; explicit completion/zero exit; bounded diagnostics and
  output downloads; fresh request directories and current-input transfers.
- Provider-owned 1,800-second lifetime, with no application delete, renewal,
  orphan controller or deletion-gated reservation.
- Request intent commits before provider waits; no provider wait holds a
  database transaction; claim checks protect acknowledgement/publication.
- Existing capability admission, operation/attempt identities, slots,
  immutable artifact service and result/event publication are reused.
- Accepted outputs are retained independently of provider availability.
- Source text remains inert; nothing reviewed here imports/executes returned
  programs in OpenShip. The source-review bypass below is an acceptance-gate
  defect, not evidence of host code execution.

## Important findings — fix before Task 4

### R1. Reuse lookup and persistence still mean one sandbox per run

**Locations:** `sandboxes/sessions.py:79–106,186–208`,
`sandboxes/models.py:56–69`, relative to `apps/api/src/openship/`.

`_pointer` filters by `claim.run_id`, the guard refuses another root, and the
unique constraint is per root. A review probe with two live runs in the same
project/conversation creates two sandboxes. The API/worker test explicitly
expects this old behavior (`tests/integration/test_analysis_results.py:377–379`).

Correct selection/persistence at the actual reuse scope, without implementing
future workflows. Address the previously discussed hazards together:

1. **Full acknowledgement identity:** two runs can have the same numeric fence.
   Bind an intent/ack to its run plus operation/attempt or generation identity,
   and recheck the live claim. Do not just remove the fence comparison.
2. **One authoritative pointer:** do not pick an arbitrary/newest `active` row
   while skipping a newer unresolved `requested` intent. Define deterministic
   handling of existing per-run rows and keep one current pointer per scope.
3. **Serialize selection across runs:** the current unique key does not prevent
   two runs in the same context creating independently. Commit shared-scope
   intent under the existing lock order before releasing the transaction for
   provider I/O. A late/stale acknowledgement must not overwrite its successor.

Leaving an accidentally created extra sandbox to provider TTL remains correct;
that does not make deliberate duplicate dispatch/create races acceptable.
Fresh directories prevent path collisions, not acknowledgement races or
uncertain-process interference. Use a new Alembic revision if needed; preserve
026/027 history and do not reuse abandoned 021–025 identifiers.

### R2. An ambiguous create blocks forever

**Locations:** `sandboxes/sessions.py:112–114,186–208`,
`sandboxes/models.py:60–64`.

A three-hour-old `requested` row still raises `SandboxCreationUncertain`.
Requested rows cannot record an expiration, and replacement does not refresh
`updated_at`. A larger reuse scope makes this block every later request in it.

Persist an uncertainty bound for each create intent and permit recovery only
on a new authorized execution after the original lifetime is conservatively
over. Account for bounded create latency and late acknowledgements. It is not
a two-line `created_at + 1800` fix, especially when reusing a row. Claim expiry
is never proof of provider absence. No background reconciliation is needed.

### R3. Lost command replies do not block sandbox reuse

**Locations:** `sandboxes/client.py:169–207`,
`sandboxes/tool.py:250–259`, `sandboxes/sessions.py:117–152`.

After a command ReadTimeout, the pointer remains `active`. The next explicit
operation inspects container state `Running` and dispatches another command
into the same handle. The probe observed two commands. Container Running is
not evidence that the earlier command stopped.

Associate dispatch uncertainty with the chosen handle through ordinary
operation/attempt state. After lost replies, cancellation or worker loss,
reuse must remain blocked until command completion is definite or the original
sandbox lifetime ends. Do not replay the old command, create a speculative
replacement, or invent a lifecycle controller.

### R4. Expiration is parsed but not enforced when accepting a handle

**Locations:** `sandboxes/client.py:52–59,72–78`,
`sandboxes/sessions.py:117–152`.

An inspect response with `Running` and an expiration one hour in the past
returns an acceptable handle. Neither layer checks it against the current
time. Reject an expired create acknowledgement and do not execute on an
expired inspected handle. Preserve definitive expiry/absence versus provider
unavailability, without TTL renewal.

### R5. Declared source outputs lose their review requirement

**Locations:** `sandboxes/tool.py:185–198,263–281`,
`sandboxes/outputs.py:23–50`.

`execute_analysis` serializes only output alias/media type, discarding
`OutputSpec.kind` and `expectation`. `_execution_request` reconstructs every
output as `kind="data"`; `validate_outputs` also does not check kind. A declared
`source` output was published successfully without a review.

Before Task 4, reject unsupported source declarations at the wrapper/validation
boundary instead of silently downgrading them. Preserve the complete trusted
declaration when Task 4 enables source review. Ordinary code-looking data text
is not the issue; erasing an explicit source-review requirement is.

### R6. Failed execution is recorded as a completed journey phase

**Location:** `runs/analysis_graph.py:239–258`.

`completed_phases` gains `execute` before checking result status. After missing
completion, `resume_phase` returns `use_outputs`, not `execute`. Append completion
only after accepted successful publication; retain the failed/interrupted step
and earlier accepted refs. Fix now, not after building resume atop bad state.

### R7. Planned source generation/capture and pinned model handoff are missing

**Locations:** `runs/analysis_graph.py:185–216`,
`runs/service.py:1245–1266`, `runs/worker.py:412–431`.

Preparation currently resolves an already-uploaded `source_id` and selected
refs. It neither generates source nor captures generated source/inputs through
the artifact service. The tool submission stores null model configuration and
selection; the worker context supplies no pinned generation/judge context.
Integration tests upload a constant parser fixture before submission.

The prepared-program execution slice works, but Task 3's checked statement
that source generation retains the pinned model boundary is not implemented.
Complete that planned path and its model handoff before Task 4 depends on it,
or obtain explicit approval for a prepared-program-only scope revision. Do not
silently defer it or claim the fixture demonstrates production generation.

### R8. Provider deployment identity is hard-coded

**Location:** `sandboxes/tool.py:121–130`.

Changing the configured endpoint leaves `provider_id="openship-phase6b"` and
the requirements key unchanged. Saved IDs are thereby interpreted against a
different provider deployment. Derive or explicitly configure a stable,
non-secret deployment identity and include it in compatibility checks. A
deployment change must not adopt a previous deployment's handle.

### R9. Partial multi-output staging leaks already-installed content files

**Locations:** `execution/gateway.py:487–507,1049–1064`.

Each output is installed at its final artifact key before the whole set is
staged. If staging output two fails, cleanup removes only staging files, not
output one's already-installed unpublished content. The probe found that file
still present with a non-ready artifact after the failure.

Use digest/size-checked cleanup of known uncommitted files as well as staging
files, reusing the existing helpers. Preserve bytes when commit outcome is
uncertain and never remove an already-ready result. This is application
artifact cleanup, not provider lifecycle management.

### R10. Per-invocation HTTP clients are never closed

**Locations:** `sandboxes/tool.py:133–148,250–260`.

Every invocation constructs an `httpx.AsyncClient`; no success/failure path
closes it. The review probe confirmed it remains open after success. Establish
transport ownership and close on success, failure and cancellation. Closing
HTTP connections must not delete the remote sandbox.

### R11. PostgreSQL regression fixture expects the obsolete head

**Location:** `apps/api/tests/integration/test_workflow_sandbox_postgres.py:113`.

The fixture upgrades to `head` then asserts `phase6b026 (head)`. Actual head is
`phase6b027`, so the fixture will fail before its tests when a database is
provided. Update its migration expectations, cover the widened artifact kind,
and add independent-session shared-scope contention/equal-fence/late-ack cases.
SQLite sequential tests do not establish PostgreSQL concurrency safety.

## Documentation and acceptance corrections

- Reconcile superseded root-run reuse wording in the spec, Task 2 interfaces,
  Task 3 acceptance evidence, Task 5 integration notes and local SDD ledger.
  Do not describe an unimplemented workflow system as an existing one.
- Restore the missing Task 4 heading, and correct stale plan/operations status
  statements that Tasks 2–3/client integration are wholly unimplemented.
- Record original green tests as historical evidence, not proof that these
  newly identified requirements have passed. Keep completed transport work
  distinct from outstanding Task 2/3 corrections.
- Previous minor concerns about empty text, `_utc()` compatibility and key-file
  reads do not justify blocking Task 4. Artifact-name validation and UUID-based
  storage paths already prevent the alleged alias path traversal; no separate
  compound-key hardening project is required here.

## Verification performed for this review

From `apps/api`:

| Check | Observed result |
| --- | --- |
| `uv run pytest -q tests/sandboxes/ tests/integration/test_analysis_results.py` | 91 passed, 2 warnings |
| `uv run ruff check .` | Passed |
| `uv run alembic heads` | `phase6b027 (head)` |
| `uv run pytest -q -m postgres tests/integration/test_workflow_sandbox_postgres.py` | 4 skipped: no `TEST_POSTGRES_URL`; not passing DB evidence |
| Review-only out-of-tree behavioral probes | 9 failed on the intended assertions, reproducing R1–R6 and R8–R10 |

The probes are `/tmp/opencode/phase6b-final-review-probes.py`, runnable with:

```sh
uv run python -m pytest -q -p tests.conftest \
  /tmp/opencode/phase6b-final-review-probes.py --tb=short
```

They test the unchanged implementation; these expected failures are findings,
not fixed regression tests. R7 and R11 are verified code/fixture gaps, not
claims of a live model/database test. No new real-provider, full-stack or
frontend verification was performed in this review.

## Recommended correction order and exit gate

1. Align scope documentation and tests; fix shared pointer identity,
   serialization and conservative uncertainty recovery (R1–R4).
2. Close Task 3 contract/state/preparation gaps (R5–R7).
3. Fix deployment identity, partial-file cleanup and transport ownership
   (R8–R10), and repair the PostgreSQL lane (R11).
4. Promote the review probes into permanent regression tests; verify original
   focused tests, independent-session PostgreSQL cases and ordinary regressions.
   Recheck real-client compatible reuse/expiry when changing selection behavior.
5. Record verified evidence under the plan's correction gate, then start Task 4.

No source-review implementation, resume UI, saved-workflow feature or provider
lifecycle controller is authorized or needed merely to close this review.
