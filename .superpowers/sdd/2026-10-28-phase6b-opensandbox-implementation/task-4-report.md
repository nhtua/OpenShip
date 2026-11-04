# Phase 6B Task 4 — returned-source expectation review

## Status and scope

Implemented and verified. Code commit: `8e3cac4` (`feat: review returned sandbox source against declared expectations`), with all applicable commit hooks passing.
Base: `cf3bcbe`. No subagents dispatched. Existing parent-owned changes to
`progress.md` are excluded from this task.

### Verified acceptance criteria

- [x] Async `review_source_output(*, output: bytes, spec: OutputSpec, model_context, control: InvocationControl) -> ReviewDecision`.
- [x] Accept/reject against the declared trusted expectation; returned instructions remain in a separate untrusted message.
- [x] Missing model/accounting, malformed/unavailable/partial judgments, unexpected source, invalid text, binary/archive content, oversized source and oversized complete requests fail closed.
- [x] No truncated source receives approval. The exact original bytes determine the locally computed SHA-256.
- [x] Existing pinned provider runner enforces provider admission, attempts, token/cost limits, deadline, stop signal and current-profile authorization. Real-runner tests cover completion/partial output and token-budget refusal.
- [x] Source acceptance/rejection decisions and exact digests persist in existing operation results and graph `tool_result`; rejected results have no accepted output refs.
- [x] A rejected/unavailable review replays its terminal operation result without another sandbox command or model review.
- [x] All declared outputs publish together only after review; publication validates the review alias set and exact bytes.
- [x] Result consumption checks accepted source review against the saved artifact digest. Source remains inert; text/JSON/source previews escape HTML text without changing ordinary JSON quotes.
- [x] Existing project/conversation sandbox identity, per-run files/results, command-uncertainty handling, gateway admission, artifact staging and fenced publication remain in place.
- [x] Generated execution source receives the declared output expectations as well as output media types.

## Implementation notes

`AnalysisArguments.output_expectations` is an optional alias-to-trusted-expectation
map. Its keys must name declared text outputs, and each expectation must be
nonblank and at most 16 KiB. Absence preserves ordinary data-output behavior.
The gateway carries these declarations in the existing operation arguments.

Review admits at most 256 KiB of complete UTF-8 source and the entire serialized
request must fit the pinned model context-byte budget. It reserves at most 512
output tokens, further restricted by the pinned model and execution limits.
No new provider, approval service, queue, database schema or execution authority
is introduced. The model returns only a strict boolean/reason object; the digest
is computed by OpenShip, not trusted from the model.

The tool reads the submission-pinned `Run.model_configuration`, uses the same
provider runner as source generation, and records provider attempts through the
existing fenced `DatabaseAttemptRecorder`. Review uses the ongoing invocation
control rather than a new deadline or uncancelled task. Cancellation/lease loss
propagate; ordinary review unavailability becomes a persisted negative decision.

Rejected decisions use the existing fenced failure transaction and release all
output reservations. Successful decisions travel through existing artifact
staging/publication. Both kinds replay from `ToolOperation.result_json`. Task 5
must continue to use the existing logical operation identity: an output-use
failure is not authorization to create a new sandbox execution.

## TDD evidence

Commands below ran from `apps/api` unless explicitly stated otherwise. Output
blocks are exact relevant excerpts; complete stdout/stderr is retained in the
named `/tmp/opencode/` logs.

### RED 1 — missing review interface

```sh
uv run pytest tests/sandboxes/test_output_review.py -q > /tmp/opencode/task4-red.log 2>&1
```

Exit 1. Each behavioral case failed because review did not exist:

```text
E       AttributeError: module 'src.openship.sandboxes.outputs' has no attribute 'review_source_output'
13 failed, 1 warning in 3.86s
```

### RED 2 — source handoff and durable decisions absent

```sh
uv run pytest tests/sandboxes/test_tool.py -q -k 'pinned_review or source_review_is' > /tmp/opencode/task4-integration-red.log 2>&1
```

Exit 1; existing source fail-closed dispatch and missing review runner prevented
the requested behavior:

```text
E           src.openship.execution.publication.ToolPublicationError: The maintained tool returned an invalid result.
FAILED tests/sandboxes/test_tool.py::test_execute_analysis_refuses_source_without_pinned_review_model
FAILED tests/sandboxes/test_tool.py::test_source_review_is_persisted_and_replayed_without_execution[True]
FAILED tests/sandboxes/test_tool.py::test_source_review_is_persisted_and_replayed_without_execution[False]
3 failed, 22 deselected, 1 warning in 4.48s
```

### GREEN 1 — review and gateway integration

```sh
uv run pytest tests/sandboxes/test_output_review.py tests/sandboxes/test_tool.py -q > /tmp/opencode/task4-green-initial.log 2>&1
```

Exit 0 (initial integration green before the consumer tests were added):

```text
38 passed, 1 warning in 16.28s
```

### RED 3 — consumer escaping and publication digest binding

```sh
uv run pytest tests/sandboxes/test_tool.py -q -k 'result_step_escapes or publication_validation' > /tmp/opencode/task4-consumer-red.log 2>&1
```

```text
E       AssertionError: assert '<script>' not in 'raise Runti...(1)</script>'
E           Failed: DID NOT RAISE any of (ValueError, OutputValidationFailed)
FAILED tests/sandboxes/test_tool.py::test_analysis_result_step_escapes_text_and_keeps_source_inert
FAILED tests/sandboxes/test_tool.py::test_publication_validation_rejects_missing_or_mismatched_source_review
2 failed, 26 deselected, 1 warning in 4.46s
```

### GREEN 2 — consumer and publication invariants

```sh
uv run pytest tests/sandboxes/test_output_review.py tests/sandboxes/test_tool.py -q > /tmp/opencode/task4-green.log 2>&1
```

```text
41 passed, 1 warning in 17.22s
```

### RED 4 — generated execution source must know output expectations

```sh
uv run pytest tests/sandboxes/test_tool.py -q -k generated_source_is_captured > /tmp/opencode/task4-generation-red.log 2>&1
```

```text
E       KeyError: 'output_expectations'
FAILED tests/sandboxes/test_tool.py::test_generated_source_is_captured_and_exact_ref_is_handed_to_execution
1 failed, 29 deselected, 1 warning in 4.11s
```

GREEN is included in final task/consumer verification below.

## Broader verification and investigation

The first whole-sandbox run exposed a test-fixture connection leak: `Lane`
sessions were never explicitly closed, and the additional behavioral cases
crossed the shared 15-connection pool ceiling. Reproduced with:

```sh
uv run pytest tests/sandboxes/ -q > /tmp/opencode/task4-sandboxes-final.log 2>&1
uv run pytest tests/sandboxes/ -x -vv > /tmp/opencode/task4-sandboxes-debug.log 2>&1
```

First command timed out at 120 seconds. The diagnostic rerun exited 1:

```text
FAILED tests/sandboxes/test_tool.py::test_undeclared_oversized_or_invalid_output_is_rejected[invalid-json-invalid_tool_output] - sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00 (Background on this error at: https://sqlalche.me/e/21/3o7r)
ERROR tests/sandboxes/test_tool.py::test_undeclared_oversized_or_invalid_output_is_rejected[invalid-json-invalid_tool_output] - sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00 (Background on this error at: https://sqlalche.me/e/21/3o7r)
========= 1 failed, 123 passed, 1 warning, 1 error in 98.45s (0:01:38) =========
```

Fixed only the local test utility: register created `Lane` sessions and close
them after every test. No production database/pool settings changed.

The unfiltered API suite was also run (before the fixture cleanup and the
ordinary-JSON quote compatibility correction):

```sh
uv run pytest tests/ -q > /tmp/opencode/task4-api-full.log 2>&1
```

```text
2 failed, 1427 passed, 77 skipped, 97 warnings, 55 errors in 546.50s (0:09:06)
```

Two failures were investigated and corrected:

- `tests/integration/test_analysis_results.py::test_fixed_journey_consumes_saved_refs` — HTML text escaping initially also escaped JSON quote characters. Use `html.escape(..., quote=False)` to preserve the existing JSON preview contract while escaping active HTML delimiters.
- `tests/sandboxes/test_tool.py::test_retained_reports_survive_the_sandbox_provider_disappearing` — the `Lane` session leak described above; its teardown and `tests/test_agent_tool_loop.py::test_chat_v2_runs_two_sequential_tool_calls_and_publishes_only_final_text` also errored from that same exhausted pool.

The remaining 53 setup errors were external-service prerequisites, not task
failures: every collected case in `tests/integration/test_messaging_relay.py`
(38 cases) requires `TEST_POSTGRES_URL`, and every collected case in
`tests/integration/test_messaging_topology.py` (15 cases) requires
`TEST_NATS_AUTH_FILE` and `TEST_NATS_SERVER_CONFIG`. The complete individual
node IDs and prerequisite diagnostics are in `task4-api-full.log` (short summary
lines 1120–1178). No live broker/provider or PostgreSQL qualification is claimed.

### Final verified task and consumer tests

```sh
uv run pytest tests/sandboxes/ -q > /tmp/opencode/task4-sandboxes-green.log 2>&1
```

```text
129 passed, 1 warning in 42.52s
```

```sh
uv run pytest tests/integration/test_analysis_results.py tests/sandboxes/test_output_review.py tests/sandboxes/test_tool.py -q > /tmp/opencode/task4-consumers-final.log 2>&1
```

```text
51 passed, 2 warnings in 22.85s
```

Existing SQLAlchemy/Pydantic deprecation warnings remain; no new runtime warning
was introduced by review.

Latest task/consumer rerun, after all code/test edits:

```sh
uv run pytest tests/sandboxes/test_output_review.py tests/sandboxes/test_tool.py tests/integration/test_analysis_results.py -q > /tmp/opencode/task4-final.log 2>&1
```

```text
51 passed, 2 warnings in 23.24s
```

Final complete network-free API lane, after all code/test edits:

```sh
uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream' > /tmp/opencode/task4-api-unit-final.log 2>&1
```

```text
1434 passed, 131 deselected, 97 warnings in 455.41s (0:07:35)
```

`uv run ruff check .` passed after correcting import ordering and annotating the
test-only session registry as `ClassVar`; `git diff --check` was clean.

## Files changed

- `apps/api/src/openship/sandboxes/outputs.py`: complete bounded inert-text review and binary/control-byte text rejection.
- `apps/api/src/openship/sandboxes/tool.py`: trusted expectations, source reconstruction, pinned review context, digest-bound observations/results/evidence.
- `apps/api/src/openship/capabilities/definitions.py`: register the observation/declaration validator.
- `apps/api/src/openship/execution/gateway.py`: persist negative review results before staging any accepted artifact.
- `apps/api/src/openship/execution/publication.py`: fenced failure-result persistence/replay and successful review-result persistence.
- `apps/api/src/openship/runs/analysis_graph.py`: pass expectations to generation; verify saved source decisions and escape result previews.
- `apps/api/tests/sandboxes/test_output_review.py`: focused review behavior and real provider-runner budget/completion tests.
- `apps/api/tests/sandboxes/test_tool.py`: real gateway/storage/replay/consumer tests and deterministic fixture session cleanup.
- This task report.

## Self-review and concerns

- Reviewed source and consumers against the brief. No source compilation,
  evaluation, import, shell execution or host execution path was added.
- Review digests are locally computed and checked before staging and against
  verified stored bytes during result consumption. Failed source sets publish
  no partial outputs. Gateway operation IDs, command-uncertainty reservation,
  scope checks and publication fences remain authoritative.
- Review failures and committed results replay without another sandbox/model
  call. Task 5 must preserve these operation identities and use existing state.
- This is a probabilistic expectation judgment, **not security scanning or a
  safety certificate**. Prompt separation is tested; semantic resistance of a
  particular hosted model is not certified by synthetic transport tests.
- Ordinary text remains untrusted data, including source-looking text declared
  as data. No unreliable source-code heuristic was added; data never grants
  execution authority. Explicit source declarations require review.
- Existing model-runner budgets apply per model turn, as in source generation.
  The chat-only `RunBudget` service is not repurposed for analysis/tool runs.
  No cumulative-budget redesign or new approval subsystem is included.
- Parent-owned `progress.md` edits are not staged or committed. No unrelated
  implementation or repository cleanup is included.

## Hooks and commit

The standalone `pre-commit run` command was unavailable on `PATH` (exit 127);
used the repository's API environment instead:

```sh
uv run --project apps/api pre-commit run > /tmp/opencode/task4-precommit-uv.log 2>&1
```

That initial staged-snapshot run exited 1 on the previously described JSON-quote
regression (the final unstaged correction had not yet entered that snapshot):

```text
FAILED tests/integration/test_analysis_results.py::test_fixed_journey_consumes_saved_refs
1 failed, 1433 passed, 131 deselected, 97 warnings in 460.33s (0:07:40)
```

Prototype tests, API lint, Vue typecheck, frontend tests and staged secret scan
passed in that run. The final correction was staged and independently verified
by the 1434-test API run above before committing.

Reviewed the task-specific UTF-8 message file, then ran the required separate
scan immediately before the commit:

```sh
rayloc scan '/tmp/openship-phase6b-task4-commit-message.txt'
```

```text
rayloc — CLEAN
0 finding(s); 0 retained; 1 of 1 file(s) completed; 9 line(s); 484 B read
```

```sh
git commit --file '/tmp/openship-phase6b-task4-commit-message.txt' > /tmp/opencode/task4-commit.log 2>&1
```

Exit 0. Final commit-time hook output:

```text
Ruff Lint............................................(no files to check)Skipped
Run Tests................................................................Passed
API Ruff Lint............................................................Passed
API Unit Tests...........................................................Passed
Vue Typecheck............................................................Passed
Frontend Unit Tests......................................................Passed
rayloc staged secret scan................................................Passed
[task/phase6b-sandbox-client 8e3cac4] feat: review returned sandbox source against declared expectations
 8 files changed, 523 insertions(+), 24 deletions(-)
 create mode 100644 apps/api/tests/sandboxes/test_output_review.py
```

This report is committed separately as task-verification documentation. Its
documentation-only commit runs applicable hooks and uses its own separately
scanned `/tmp` message file. The report commit ID is supplied in the final
handoff rather than attempting to embed its own hash in itself.

## Task 4 review fix round — findings against `809e651`

All three findings were verified, reproduced before their respective production
fixes, and corrected in one fix round. Fix commit: `f3f7814` (`fix: close
returned-source review supervision and persistence gaps`). No history was
rewritten and no subagents were dispatched.

The earlier tests mocked `run_model_turn` in the gateway integration path and
therefore missed the combined-control supervision defect. The new integration
helper configures a real eligible operator profile and uses the real gateway,
provider adapter/runner, current-profile authorization, attempt recorder,
artifact store, admission and fenced publication. Only remote sandbox/model
transports are synthetic; `run_model_turn` is not replaced in these regressions.

### Finding 1 — critical: combined gateway control lacked `is_stopped()`

Added a real-gateway source-review regression that reaches real provider-runner
supervision and asserts successful provider-attempt persistence and idempotent
replay. Added all four parent-stop/slot-loss combinations, checking both the
boolean supervision contract and `raise_if_stopped()` behavior.

RED, from `apps/api`:

```sh
uv run pytest tests/sandboxes/test_tool.py -q -k 'real_runner_supervision or combined_control_preserves' > /tmp/opencode/task4-fix1-red.log 2>&1
```

Exit 1; exact relevant output:

```text
E       AssertionError: ToolFailure(code='provider_unavailable', message='The search service is temporarily unavailable.')
E       assert 'failed' == 'succeeded'
E       AttributeError: '_CombinedControl' object has no attribute 'is_stopped'
5 failed, 35 deselected, 1 warning in 5.16s
```

Fix: `_CombinedControl.is_stopped()` now reports slot loss OR the parent's stop
signal. Existing deadline delegation and parent-first `raise_if_stopped()`
semantics are unchanged.

GREEN:

```sh
uv run pytest tests/sandboxes/test_tool.py -q -k 'real_runner_supervision or combined_control_preserves' > /tmp/opencode/task4-fix1-green.log 2>&1
```

```text
5 passed, 35 deselected, 1 warning in 5.23s
```

### Finding 2 — important: invalid source lost digest-bound refusal

Added real-gateway persistence/replay tests for invalid UTF-8, an ELF-shaped
binary and a ZIP-shaped archive. They assert a locally computed exact SHA-256,
negative persisted review, no accepted refs or ready operation-output artifacts,
and replay with one sandbox execution and zero remote model requests.

RED:

```sh
uv run pytest tests/sandboxes/test_tool.py -q -k invalid_source_refusal > /tmp/opencode/task4-fix2-red.log 2>&1
```

```text
E       AssertionError: assert None is not None
3 failed, 37 deselected, 1 warning in 4.51s
```

The failure was the generic failed result with `result=None`, rather than a
persisted `ReviewDecision`. Fix: `validate_outputs` still enforces declared-set
and transfer-byte bounds for every output, but leaves source content untouched
for `review_source_output`. Ordinary text/JSON data validation is unchanged.

GREEN:

```sh
uv run pytest tests/sandboxes/test_tool.py -q -k invalid_source_refusal > /tmp/opencode/task4-fix2-green.log 2>&1
```

```text
3 passed, 37 deselected, 1 warning in 4.54s
```

### Finding 3 — important: aggregate UTF-8 review evidence exceeded 16 KiB

Added eight-output gateway tests with maximum-length, heavily JSON-escaped
aliases and multibyte reasons. The original 1024-character emoji reasons
reproduced both lost success evidence and rolled-back failure publication.

RED:

```sh
uv run pytest tests/sandboxes/test_tool.py -q -k multibyte_review_evidence > /tmp/opencode/task4-fix3-red.log 2>&1
```

```text
E       AssertionError: assert None is not None
E       AssertionError: ToolFailure(code='invalid_tool_output', message='The maintained tool returned an invalid result.')
E       assert 'interrupted' == 'failed'
2 failed, 38 deselected, 1 warning in 4.27s
```

Expanded cases also cover JSON escape expansion (`U+0001` reasons), valid
near-limit multibyte reasons, both accepted/rejected model answers, and direct
contract enforcement. The expanded RED command was:

```sh
uv run pytest tests/sandboxes/test_tool.py tests/sandboxes/test_output_review.py -q -k 'multibyte_review_evidence or serialized_reason_bytes' > /tmp/opencode/task4-fix3-expanded-red.log 2>&1
```

It emitted `FF` and exceeded the 120-second command timeout before providing a
complete summary. This incomplete run is not counted as passing evidence; the
completed two-case RED above established the intended publication failures.

Fix: `ReviewDecision.reason` must be nonempty and at most 1024 bytes **after
canonical JSON string serialization**, counting both UTF-8 and escaping. A
model judgment is converted to this bounded contract inside the review's
validation/error boundary. Over-budget reasons become a short negative decision
with the original source digest; they are not accepted, truncated or allowed to
reach oversized publication. The trusted model instructions state this bound.
With eight outputs and bounded aliases/refs this leaves room under the existing
16-KiB result limit, which remains enforced without being raised or bypassed.

GREEN (all eight expanded cases):

```sh
uv run pytest tests/sandboxes/test_tool.py tests/sandboxes/test_output_review.py -q -k 'multibyte_review_evidence or serialized_reason_bytes' > /tmp/opencode/task4-fix3-green.log 2>&1
```

```text
8 passed, 54 deselected, 1 warning in 6.88s
```

The tests assert complete serialized result size, serialized per-reason size,
persisted operation status/result and exact source digests. Valid near-limit
reasons preserve acceptance/rejection; excessive reasons persist refusals.
All replay paths keep one sandbox command and exactly eight initial model
requests, with no additional requests on replay.

### Final fix-round verification and commits

Latest complete task suite (all sandbox tests and analysis-result consumers),
after the last test assertion was added:

```sh
uv run pytest tests/sandboxes/ tests/integration/test_analysis_results.py -q > /tmp/opencode/task4-fix-task-final.log 2>&1
```

```text
150 passed, 2 warnings in 49.24s
```

`uv run ruff check .` returned `All checks passed!` and
`git diff --cached --check` exited 0.

The fix message was reviewed and scanned immediately before committing:

```sh
rayloc scan '/tmp/openship-phase6b-task4-review-fix-message.txt'
```

```text
rayloc — CLEAN
0 finding(s); 0 retained; 1 of 1 file(s) completed; 9 line(s); 511 B read
```

```sh
git commit --file '/tmp/openship-phase6b-task4-review-fix-message.txt' > /tmp/opencode/task4-fix-commit.log 2>&1
```

Exit 0; commit-time hooks exercised the complete network-free API unit lane as
configured in `.pre-commit-config.yaml`, not just the focused task tests:

```text
Ruff Lint............................................(no files to check)Skipped
Run Tests................................................................Passed
API Ruff Lint............................................................Passed
API Unit Tests...........................................................Passed
Vue Typecheck............................................................Passed
Frontend Unit Tests......................................................Passed
rayloc staged secret scan................................................Passed
[task/phase6b-sandbox-client f3f7814] fix: close returned-source review supervision and persistence gaps
 5 files changed, 164 insertions(+), 5 deletions(-)
```

Fix files:

- `apps/api/src/openship/execution/gateway.py`
- `apps/api/src/openship/sandboxes/contracts.py`
- `apps/api/src/openship/sandboxes/outputs.py`
- `apps/api/tests/sandboxes/test_output_review.py`
- `apps/api/tests/sandboxes/test_tool.py`

This appended verification section is a separate documentation commit using its
own scanned message and applicable hooks; its hash is in the final handoff.

### Combined Task 4 self-review and remaining concerns

Reviewed the entire Task 4 production diff from `cf3bcbe`, including gateway,
publication, capability registration, tool, contracts, output review and graph
consumption—not just the follow-up patch. All three reported findings are
addressed with no severity downgrade.

- Source collection remains bounded; invalid source is never approved or
  imported/executed, and its exact bytes now reach local refusal logic.
- Negative reviews persist through the existing fenced failure transaction;
  accepted results retain the exact reviewed artifact digest. Aggregate reason
  bounds are enforced before the publication transaction, including JSON escape
  expansion, for success and failure paths.
- Real-runner integration covers the previously missing supervision boundary,
  real current-profile checks and durable provider-attempt accounting. Parent
  stop and slot-loss signals still stop the combined control.
- No sandbox pointer/session identity, per-run output directory, gateway logical
  call ID, artifact ownership check, fence, model pin or provider budget was
  changed. Task 5 must replay the existing operation/state, never create another
  sandbox execution to recover output-review/use failures.
- Existing parent-owned `progress.md` remains unstaged and untouched. No Task
  1–3 implementation or unrelated user work was reverted.
- No unresolved issue among these findings. Existing external PostgreSQL/NATS
  qualification limitations and the expectation-only (not security-certified)
  nature of model judgment still apply. Existing deprecation warnings remain.
