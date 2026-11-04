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
