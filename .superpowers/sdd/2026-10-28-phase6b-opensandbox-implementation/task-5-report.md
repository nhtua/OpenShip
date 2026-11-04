# Phase 6B Task 5 — explicit analysis resume

Status: implemented and verified. Implementation commit: `11188e8`.

## Scope and implementation plan

Implemented the supplied Task 5 brief in the existing worktree, based on
`db0f936`. No subagents, generic workflow engine, second scheduler, snapshot,
source editor, bundle inspector, hidden input-byte replay store, or provider
sandbox deletion were introduced. Parent-owned `progress.md` is unchanged and
excluded from staging.

The task-local implementation sequence was:

1. Write backend command/routing and real API/worker/storage resume tests; run RED.
2. Persist ordinary fixed-analysis phase state under the existing live claim;
   admit explicit A/B commands through the existing run job and outbox.
3. Recover committed operations without re-admission, preserve digest-bound
   accepted/refused results, and refuse missing inputs or uncertain execution.
4. Write frontend tests RED; integrate only A/B and retained result/error links.
5. Run task tests, applicable regressions, whole suites, typecheck/build/lint;
   self-review, then commit using scanned message files and normal hooks.

## Verified acceptance criteria

- [x] `ResumeAnalysisRequest(mode: last_step|from_beginning, client_request_id: UUID)` rejects unknown modes, non-UUID identities, and extra fields.
- [x] `resume_analysis(db, *, principal_id: UUID, run_id: UUID, command: ResumeAnalysisRequest) -> Run`, exposed through authenticated `POST /api/runs/{run_id}/resume-analysis` (202).
- [x] Ownership is checked before admission/replay. The conversation lock serializes admission; request digest binds both source run and mode. Replays return the same new run, and reuse with another choice conflicts.
- [x] Existing active-scope, capacity, single-active-run, run job, transactional outbox, event, ownership and fencing patterns remain authoritative.
- [x] A uses saved initial/generated/output refs, preserves the original journey root, and enters the first incomplete fixed phase. Fully completed phases—including output consumption—are skipped.
- [x] A can resume execution from prepared refs using a compatible shared live handle or an on-demand replacement after provider-confirmed expiry.
- [x] A recovers a committed sandbox operation after a lost phase acknowledgment, using its existing operation ID/result/reviews, with no new sandbox command or review.
- [x] Accepted and rejected source review results retain their exact digest-bound result JSON and operation identity. Refusals never authorize another execution.
- [x] B clears generated/output phase state, starts a new root from original input refs, reruns preparation/execution, and preserves earlier artifacts/results.
- [x] Missing/pruned or changed ref metadata fails visibly. Missing physical input bytes fail before source generation; no earlier-step recomputation is used to hide missing inputs.
- [x] Uncertain/interrupted operations cannot be redispatched by A. A fresh execution (including B) is blocked while the shared pointer says an earlier command may still run. Once eligible, existing provider inspection/replacement remains authoritative.
- [x] Existing expired-dispatched-tool reconciliation regression verifies interruption rather than automatic requeue. The fixed graph still uses the ordinary worker/recovery path.
- [x] Cancellation rejects late output and leaves the provider sandbox pointer intact; unpublished reservations are not usable output artifacts.
- [x] UI contains only “A: Resume from last step” and “B: Start from beginning,” plus retained result/error links. Active resumed runs retain those links without offering another resume.
- [x] UI disables double submission, retains the same request on ambiguous acknowledgment loss, ignores acknowledgment for another run, and releases the choice only after a definite actionable refusal. It does not acquire the chat store's global submission lock.

## Durable-state and identity decisions

Migration `phase6b029` adds nullable `Run.analysis_state_json`. It stores ordinary
refs, completed phases, generated source ref, the published tool result,
consumed summary, and retained-result/error identities. Runtime authority,
credentials, input bytes and sandbox process state are not stored there.
Original refs are recorded before generation, then phase boundaries commit
through `require_live_claim(..., lock=True)`. The ordinary worker hydrates this
state before invoking the existing fixed graph.

The project/conversation-scoped `WorkflowSandbox` pointer is not copied or
repurposed. Each newly authorized execution retains its own operation and
per-run outputs. A completed operation is projected directly: resume does not
call the gateway with a different run identity merely to consume its result.
Output refs come from the committed result's exact refs, not fresh artifact-row
metadata. This prevents resume from silently repinning altered output digests.

The service does not commit independently; the route commits the new run, job,
outbox and event together, and rolls back failures. Exact accepted-request
replays precede fresh active-scope/capacity checks. Public run and snapshot
projections expose only retained artifact IDs, predecessor run ID and normalized
error code—not checkpoint internals or source/input bytes.

## Backend RED evidence

Commands ran from `apps/api`. Complete local logs are under `/tmp/opencode/`;
the snippets below omit synthetic authentication tokens and machine paths.

### Initial command, routing and persistence RED

```sh
uv run pytest tests/sandboxes/test_analysis_resume.py tests/integration/test_analysis_resume.py -q > /tmp/opencode/task5-backend-red.log 2>&1
```

Exit 1: `7 failed`. Intended failures included:

```text
AssertionError: explicit analysis resume command is missing
AssertionError: {"detail":"Not Found"}
AttributeError: 'Run' object has no attribute 'analysis_state_json'
```

One initial unit fixture incorrectly supplied a Python string to strict
`ArtifactRef.id`; this was corrected to a UUID before production changes.
The corrected unit-only RED was:

```sh
uv run pytest tests/sandboxes/test_analysis_resume.py -q > /tmp/opencode/task5-unit-red.log 2>&1
```

Exit 1: `2 failed, 1 warning in 1.09s`. The command
schema remained missing, and the output-resume case incorrectly entered
`prepare`, raising `ProviderError` for execution settings it should not read.

### Missing physical input before generation

```sh
uv run pytest tests/integration/test_analysis_resume.py -q -k 'missing_input_bytes or generated_source' > /tmp/opencode/task5-input-red.log 2>&1
```

```text
assert generation_calls == []
E assert [True] == []
1 failed, 2 passed, 10 deselected, 2 warnings in 5.41s
```

Fix: verify the selected immutable files through the artifact store before
source generation, without reconstructing any missing file.

### Retained predecessor links

```sh
uv run pytest tests/integration/test_analysis_resume.py -q -k 'b_starts or lost_phase_ack' > /tmp/opencode/task5-retained-red.log 2>&1
```

Exit 1: `2 failed`; B's response returned empty `output_ids`, and lost-ack
recovery lacked `prior_run_id`. Added only retained identity/error metadata to
ordinary state and public run/snapshot summaries.

### Uncertain earlier command

```sh
uv run pytest tests/integration/test_analysis_resume.py -q -k 'b_is_blocked' > /tmp/opencode/task5-uncertain-red.log 2>&1
```

```text
E assert 202 == 409
1 failed, 16 deselected, 2 warnings in 4.36s
```

Fix: fresh execution admission refuses a still-live uncertainty interval before
preparation/model work; existing provider/session checks still apply afterward.

### Completed-phase routing and exact output digests

```sh
uv run pytest tests/sandboxes/test_analysis_resume.py -q > /tmp/opencode/task5-completed-red.log 2>&1
uv run pytest tests/integration/test_analysis_resume.py -q -k 'does_not_repin' > /tmp/opencode/task5-digest-red.log 2>&1
```

Both exited 1. The first called the invocation factory despite all phases being
completed (`completed phases must be skipped`). The second returned 202 instead
of 409 after an output's artifact-row digest changed. Fixes: graph entry can end
without reconsumption, and recovered refs are taken from the exact committed
operation result before checking the current rows.

The queued B root identity was also checked before worker execution:

```sh
uv run pytest tests/integration/test_analysis_resume.py -q -k 'b_starts' > /tmp/opencode/task5-root-red.log 2>&1
```

Exit 1: the queued journey still had an empty root ID instead of the new run's
ID. B now allocates and records its new root atomically at admission, rather
than waiting for preparation to fill it in.

## Frontend RED evidence

Commands ran from `apps/web`.

```sh
pnpm exec vitest run tests/RunResume.test.ts > /tmp/opencode/task5-ui-red.log 2>&1
```

Exit 1, initial bootstrap RED (not a behavioral assertion):

```text
Error: Failed to resolve import "@/components/chat/RunResume.vue"
Test Files  1 failed (1)
Tests  no tests
```

Subsequent behavioral RED tests ran against the component:

```sh
pnpm exec vitest run tests/RunResume.test.ts > /tmp/opencode/task5-ui-refusal-red.log 2>&1
```

```text
RunResume > shows a definite missing-input refusal and lets the user explicitly choose B
expected 'Resume was not acknowledged. Please r…' to contain 'missing or pruned'
Tests  1 failed | 4 passed (5)
```

The initial implementation treated a definite domain refusal like ambiguous
network loss and unnecessarily locked the other choice. Fixed known-domain
409 handling while keeping ambiguous requests pinned to the original ID/mode.

```sh
pnpm exec vitest run tests/RunResume.test.ts > /tmp/opencode/task5-ui-active-red.log 2>&1
```

Exit 1: `1 failed | 5 passed`; the active resumed card did not render its retained
result link. Fixed link visibility independently of resume-button eligibility.

## GREEN and additional verification

Final task/regression command, after the digest and completed-phase fixes:

```sh
uv run pytest tests/sandboxes/ tests/integration/test_analysis_results.py tests/integration/test_analysis_resume.py tests/test_tool_worker.py -q > /tmp/opencode/task5-task-green-final.log 2>&1
```

```text
188 passed, 2 warnings in 66.38s (0:01:06)
```

This includes the supplied backend/database cases (real SQLite migrations,
transactions, artifact store, gateway and worker), all sandbox tests, Task 3/4
consumers, and the existing worker cancellation/recovery regressions. In
particular, `test_expired_dispatched_tool_is_interrupted_and_never_requeued`
verifies restart reconciliation does not silently dispatch a second effect.

The exact requested two-file backend command passed again after the final
queued-root regression and fix:

```sh
uv run pytest tests/sandboxes/test_analysis_resume.py tests/integration/test_analysis_resume.py -q > /tmp/opencode/task5-backend-brief-final.log 2>&1
```

```text
21 passed, 2 warnings in 15.58s
```

```sh
pnpm exec vitest run tests/RunResume.test.ts > /tmp/opencode/task5-ui-green-final.log 2>&1
```

```text
Test Files  1 passed (1)
Tests  6 passed (6)
```

```sh
pnpm typecheck > /tmp/opencode/task5-typecheck-green-final.log 2>&1
pnpm build > /tmp/opencode/task5-build-green-final.log 2>&1
```

Both exited 0. `uv run ruff check .` and `git diff --check` also passed.
The final `pnpm test:run` rerun (`task5-web-full-final.log`) passed all 35 files
and 350 tests, including the added active-link case.

The complete final network-free API command also passed independently:

```sh
uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream' > /tmp/opencode/task5-api-network-free-final.log 2>&1
```

```text
1471 passed, 131 deselected, 97 warnings in 474.10s (0:07:54)
```

### Whole API suite and blocked prerequisites

The required whole-project test attempt was not narrowed to hide external lanes:

```sh
uv run pytest tests/ -q > /tmp/opencode/task5-api-full.log 2>&1
```

```text
1466 passed, 77 skipped, 97 warnings, 53 errors in 472.44s (0:07:52)
```

No test-body failures. All 53 errors are setup prerequisites:

- Every collected case in `tests/integration/test_messaging_relay.py` (38 cases):
  `TEST_POSTGRES_URL is required for the JetStream relay lane`.
- Every collected case in `tests/integration/test_messaging_topology.py` (15 cases):
  `TEST_NATS_AUTH_FILE` and `TEST_NATS_SERVER_CONFIG` from a disposable broker
  launcher are required.

The individual node IDs are retained in `task5-api-full.log`, lines 732–784.
This run began before the last additional regressions; commit hooks verify the
final network-free API lane. No live PostgreSQL, broker or OpenSandbox/model
qualification is claimed. Those external fixtures are the only blocked
verification prerequisites, not prerequisites to implementing Task 5.

### Test-fixture investigations (not production defects)

Intermediate verification failures were investigated rather than suppressed:

- `task5-backend-green-initial.log`: 5 passed, 2 failed because the test assumed
  FastAPI's raw `detail` wrapper, while the application's error handler returns
  top-level `error`. Corrected the test to the established API contract.
- `task5-backend-green.log`: 10 passed, 1 failed because the provider stand-in
  still answered `Running` after only the local expiry timestamp changed.
  Existing session resolution correctly trusts remote inspection. The fixture
  now answers definitive 404 for an expired provider sandbox.
- Cancellation test development (`task5-backend-final2.log`,
  `task5-task-final.log`, `task5-task-verified.log`) initially assumed the stale
  worker would publish a terminal result, that no reservation rows would remain,
  and that unavailable content returns 404. Existing behavior is correct: cancel
  already published the terminal result/revoked the fence, reservation metadata
  may remain non-ready, and the artifact API returns 503 `artifact_unavailable`.
  Final assertions verify those semantics and the untouched sandbox pointer.

## Files changed

- `apps/api/alembic/versions/029_phase6b_analysis_resume.py`
- `apps/api/src/openship/runs/resume.py`
- `apps/api/src/openship/runs/{analysis_graph,models,routes,schemas,service,worker}.py`
- `apps/api/src/openship/events/{schemas,service}.py` (additive snapshot projection)
- `apps/api/tests/sandboxes/test_analysis_resume.py`
- `apps/api/tests/integration/test_analysis_resume.py`
- `apps/web/src/components/chat/RunResume.vue`
- `apps/web/src/components/chat/RunCard.vue`
- `apps/web/src/services/api.ts`
- `apps/web/src/stores/chat.ts` (carry additive analysis projection)
- `apps/web/src/types/index.ts`
- `apps/web/src/views/ChatView.vue` (refresh the accepted run using existing snapshot/event flow)
- `apps/web/tests/RunResume.test.ts`
- This report. Parent-owned `progress.md` is not part of the task changes.

## Self-review and concerns

- Reviewed the full Task 5 diff for identity, transaction boundaries, locks,
  cancellation, missing refs, output integrity and scope containment. The API
  never calls a sandbox/model; worker execution remains under the existing
  capability admission, current policy, deadlines and fences.
- Accepted and refused results replay without changing their operation IDs or
  digest-bound result JSON. The new output-metadata regression catches accidental
  repinning. Source remains inert; no host execution path was added.
- A cancelled or terminally refused operation is not authorization to retry its
  effect. A new root is explicit B authorization, still subject to uncertainty
  and ordinary gateway admission. No automatic retry loop was added.
- Existing runs lacking ordinary saved phase refs fail visibly rather than
  reconstructing state from sandbox residual files. The schema is additive;
  historical state is not guessed or backfilled.
- Sandbox/model transports are synthetic in these tests. Review acceptance and
  rejection replay tests replace only the review boundary; Task 4's existing
  real-runner supervision and accounting regressions are included in the full
  sandbox run. Task 6 remains responsible for live provider qualification.
- Existing SQLAlchemy/Pydantic and Vite CJS deprecation warnings remain. Whole
  frontend tests intentionally log some synthetic network-failure paths.
- Commit-hook evidence and commit IDs are recorded below after the normal
  scanned-file commit completes; no hook bypass is authorized or used.

## Initial commit-hook check

The implementation message file was reviewed and scanned immediately before the
first commit attempt:

```sh
rayloc scan '/tmp/openship-phase6b-task5-commit-message.txt'
git commit --file '/tmp/openship-phase6b-task5-commit-message.txt' > /tmp/opencode/task5-commit.log 2>&1
```

Message scan: CLEAN, zero findings. Commit exited 1: prototype tests, API lint,
complete network-free API tests, Vue typecheck and frontend tests all passed,
but the staged secret hook flagged the synthetic registration password literal
in the ownership test (`Concrete password assignment`, test line 422). This was
a fixture value, not a real credential. Replaced it with a freshly generated
UUID-based test password rather than suppressing or bypassing the scanner.
The changed fixture is reverified and the complete normal hooks rerun on retry.

## Final commit and hook evidence

The targeted ownership fixture rerun passed (`task5-password-fixture-green.log`):

```text
1 passed, 17 deselected, 2 warnings in 4.34s
```

Scanning the updated test file directly returned CLEAN with zero findings.
After staging the corrected test and report, the exact implementation message
file was scanned again in a separate call immediately before retrying:

```sh
rayloc scan '/tmp/openship-phase6b-task5-commit-message.txt'
git commit --file '/tmp/openship-phase6b-task5-commit-message.txt' > /tmp/opencode/task5-commit-retry.log 2>&1
```

Message scan: CLEAN, zero findings (9 lines, 503 bytes). Commit exited 0:

```text
Ruff Lint............................................(no files to check)Skipped
Run Tests................................................................Passed
API Ruff Lint............................................................Passed
API Unit Tests...........................................................Passed
Vue Typecheck............................................................Passed
Frontend Unit Tests......................................................Passed
rayloc staged secret scan................................................Passed
[task/phase6b-sandbox-client 11188e8] feat: add explicit fixed-analysis resume and minimal run controls
20 files changed, 1597 insertions(+), 7 deletions(-)
```

The API hook reran the entire final network-free API lane, including the
generated-password fixture correction. No hooks were skipped or suppressed.
After the implementation commit, `git status --short` contained only the
pre-existing parent-owned `progress.md` modification.

This final evidence update is a separate report-only commit, with its own
reviewed/scanned message file and applicable normal hooks. Its commit ID is
provided in the handoff rather than embedding its own hash in this report.

## Task 5 deep-review fix round — I1 and I2

Base: `9a75119`. Both Important findings were verified against the current code
and reproduced before their respective production fixes. This round changes
only `apps/api/src/openship/runs/resume.py`, its existing integration test file,
and this appended report. No minor findings, Task 6 work, history rewrite,
subagent dispatch, or parent-owned `progress.md` edits are included.

### I1 — recheck idempotency at the serialized admission boundary

The fast request lookup previously had no matching lookup after
`_lock_active_conversation_scope`. The regression forces a competing transaction
to commit after the fast lookup but before the scope-lock hand-off completes.
The competitor uses a separate real database session and the real resume
service/outbox/event path. Only scheduling at the lock boundary is controlled;
the run, job, event and outbox writes and the outer authenticated route are real.

SQLite does not implement PostgreSQL `SELECT FOR UPDATE`. This is a deterministic
serialization-window regression, not a claim of live PostgreSQL threaded-lock
qualification. It deliberately does not rely on thread timing or sleeps.

RED, from `apps/api`:

```sh
uv run pytest tests/integration/test_analysis_resume.py -q -k 'serialized_admission' > /tmp/opencode/task5-fix-i1-red.log 2>&1
```

```text
test_resume_rechecks_request_after_serialized_admission[last_step]
E AssertionError: {"error":{"code":"run_already_active","message":"Another run is already active on this conversation."}}
E assert 409 == 202
test_resume_rechecks_request_after_serialized_admission[from_beginning]
E AssertionError: assert 'run_already_active' == 'client_request_id_conflict'
2 failed, 18 deselected, 2 warnings in 4.89s
```

Fix: the fast and locked lookups now use the same `_resume_replay` comparison,
binding request identity to the existing conversation and source-run/mode
digest. The second lookup happens immediately after the scope lock, before
active-run checks or new writes. An identical contender returns the committed
winner; a different mode retains the normal `client_request_id_conflict` domain
error. Existing ownership checks and conversation → project → run lock ordering
are unchanged.

GREEN:

```sh
uv run pytest tests/integration/test_analysis_resume.py -q -k 'serialized_admission or request_identity' > /tmp/opencode/task5-fix-i1-green.log 2>&1
```

```text
3 passed, 17 deselected, 2 warnings in 5.47s
```

The tests assert exactly one durable run for the request UUID, one run job, one
`tool.submitted` event, one command outbox entry, and one event-fanout outbox
entry. There are exactly two outbox rows in total, as required by the existing
command-plus-event messaging design—not a duplicate admission. No additional
sandbox command is sent.

### I2 — malformed committed projection fails visibly and closed

The new table-driven API regression covers malformed/empty artifact-ID JSON,
JSON null/scalar ID lists, malformed result JSON, missing `outputs`, null/list/
string `outputs`, and malformed output refs. Result cases exercise both
succeeded and failed terminal operations; failed review-result projection
previously let validation errors escape. The real HTTP client has server
exception rethrow disabled so the regression explicitly observes HTTP 500 versus
the required domain refusal.

Initial RED (`task5-fix-i2-red.log`) also exposed a test assumption: terminal run
jobs remain recorded rather than being deleted. The test was corrected before
production changes to compare before/after job counts, as it already did for
runs, events and outbox rows. Corrected RED:

```sh
uv run pytest tests/integration/test_analysis_resume.py -q -k 'corrupt_committed_projection' > /tmp/opencode/task5-fix-i2-red-corrected.log 2>&1
```

```text
E AssertionError: Internal Server Error
E assert 500 == 409
11 failed, 8 passed, 20 deselected, 2 warnings in 15.39s
```

All 11 failures were actual HTTP 500 responses: empty/null/boolean/integer
artifact-ID JSON and all seven corrupt failed-operation result shapes. The
eight already-refused succeeded-result/malformed-JSON cases remain covered.

Fix: `_committed_analysis_result` bounds parsing, projection and exact-ref
extraction within one domain-error boundary. It requires a JSON string list
for artifact IDs and an output map for successful results, keeps the existing
artifact-set comparison, and converts `KeyError`, `TypeError` and `ValueError`
(including JSON/Pydantic validation errors) to HTTP 409
`analysis_inputs_unavailable`. It still takes output digests from the committed
result, never repins current artifact-row metadata, never modifies that result,
and never dispatches execution or source review to recover it.

GREEN, including the existing accepted/rejected Task 4 digest-bound replay and
changed-output-metadata regressions:

```sh
uv run pytest tests/integration/test_analysis_resume.py -q -k 'corrupt_committed_projection or digest_bound_review or does_not_repin' > /tmp/opencode/task5-fix-i2-green.log 2>&1
```

```text
22 passed, 17 deselected, 2 warnings in 17.25s
```

Every corrupt case asserts `analysis_inputs_unavailable`, no admitted request
run, unchanged run/job/event/outbox counts, unchanged stored corrupt operation
data, one original operation, and no additional sandbox creation or command.
The preserved accepted/rejected replay tests also assert no repeated review.

### Combined fix-round verification and self-review

```sh
uv run pytest tests/sandboxes/ tests/integration/test_analysis_results.py tests/integration/test_analysis_resume.py tests/test_tool_worker.py -q > /tmp/opencode/task5-fix-regressions-green.log 2>&1
```

```text
209 passed, 2 warnings in 83.55s (0:01:23)
```

`uv run ruff check .` and `git diff --check` passed. The complete unfiltered API
suite and normal commit hooks are also run for this fix round; their final
results are appended below after completion.

Self-review checked that the request recheck precedes all fresh admission
side effects, mismatch semantics match the fast path, malformed terminal data
does not mutate state or create a recovery effect, and valid accepted/rejected
results retain their operation identity and exact digest refs. No auth, fencing,
worker recovery, cancellation, provider review, UI, or scheduler code changed.
The only verification limitation remains live PostgreSQL/NATS/provider
qualification; the deterministic database regression is explicitly not labeled
as a live PostgreSQL concurrency test.
