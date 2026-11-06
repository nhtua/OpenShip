# Chat Python Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution, or superpowers:subagent-driven-development if the user explicitly chooses delegation. Track each checkbox and record verified evidence in the active plan. Never dispatch agents without user authorization.

**Goal:** Let chat select a secure URL download and general Python execution, then display a retained chart from a real OpenSandbox journey.

**Architecture:** Add two versioned capabilities to the existing authorized chat loop and gateway, retaining immutable artifacts across calls. Reuse provider execution and conversation-scoped sandbox handles; add additive chat continuation, PNG/JPEG validation, and authenticated preview without changing the old analysis contract.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy/PostgreSQL, NATS JetStream, httpx, OpenSandbox, pinned Matplotlib, Pillow, Vue/TypeScript, pytest and Playwright.

**Spec:** [Approved design](../specs/2026-11-05-phase6b-chat-python-execution-design.md); preserve the [original Phase 6B acceptance criteria](2026-10-28-phase6b-opensandbox-implementation.md#task-6-one-real-parser-demonstration-and-existing-regressions).

## Global constraints

- Public unauthenticated HTTPS CSV/JSON only for downloads; no credentials, proxy inheritance, internal destinations, unvalidated redirects, or DNS-rebinding exposure. Maximum 8 MiB transferred and decoded, 30 seconds total, three redirects.
- General Python: source ≤256 KiB; ≤8 inputs, 8 MiB each/16 MiB total; ≤8 outputs, 4 MiB each/8 MiB total; combined stdout/stderr ≤256 KiB; parameters ≤16 KiB. Allow no files or outputs.
- Provider request: 1 CPU, 512 MiB, 30-second command timeout, 1,800-second sandbox lifetime. Reuse only compatible conversation-scoped handles, fresh directories, no automatic command retries, renewal or deletion.
- Operator selects the pinned Python/Matplotlib image and dependency identity. No model-selected images, host execution, runtime installs, or credentials in sandbox. Preserve existing analysis contracts and A/B resume.
- PNG/JPEG: fully decode with a maintained decoder, match declared media type, ≤4096 pixels per dimension and ≤16 million pixels; no BMP/SVG/HTML/archive or arbitrary binary previews.
- No database transaction over provider/network waits. Retain exact authorized refs, enforce existing policy, claims, quotas, attempt budgets and idempotency. No silent refetch on continuation.
- Use isolated test database, broker namespace/credentials and artifact storage. Do not reset existing volumes or disrupt running services. Record actual evidence and user acceptance separately.
- Commit task-scoped changes only using the repository's `git-workflow` skill and scanned message files; preserve existing uncommitted changes.

## Review focus

1. DNS changes between validation and connection or a redirect to private IP: Task 1 tests the connected peer and blocks each hop.
2. A model fabricates an artifact ID from another run or conversation: Task 2 rejects it and accepts only pinned earlier same-run outputs or explicitly selected artifacts.
3. Crash after source capture or command dispatch: Tasks 3 and 5 assert reuse of captured refs and no automatic repeat of uncertain commands.
4. Tiny compressed PNG/JPEG with excessive decoded dimensions or a truncated tail: Task 4 rejects both before publication.
5. User switches conversations or a preview unmounts while a blob request is pending: Task 6 revokes URLs and prevents stale/cross-conversation rendering.

## File and responsibility map

- `apps/api/src/openship/tools/download/{contracts,transport,tool}.py`: download contract, SSRF-safe transport, staged publication.
- `apps/api/src/openship/sandboxes/{python_tool,outputs,contracts,client}.py`: generic Python contract, source capture/execution, bounded outputs and provider reuse.
- `apps/api/src/openship/execution/{gateway,admission,publication}.py`, `capabilities/{catalog,definitions}.py`: versioned capability registration, admitted artifacts, fenced publication.
- `apps/api/src/openship/runs/{agent_graph,resume,routes,schemas,service}.py`: same-run reference handoff and explicit continuation through existing run/outbox infrastructure.
- `deploy/sandbox/`, API dependency locks and settings: pinned plotting image and decoder/runtime identity.
- `apps/web/src/components/chat/`, `services/api.ts`, `types/index.ts`: operation status, authenticated PNG/JPEG preview and continuation controls.
- `apps/api/tests/{tools,sandboxes,integration,full_stack}/`, `apps/web/tests/`: security, contract, durability and real browser evidence.

## Task 1: Bounded HTTPS download capability

**Verified implementation — 2026-11-05:** The transport rejects a
private-IP redirect, limits an identity-encoded response to 8 MiB, interrupts
stalled reads on cancellation/deadline, and validates basic UTF-8 CSV/JSON.
Focused run: `cd apps/api && uv run pytest -q tests/tools/test_download.py` →
Tests exercise rejection of mixed private/public DNS, connection by vetted IP,
gateway publication and failure cleanup. The downloader now streams to a
private staging file, computes its digest while receiving, and validates the
completed content before fenced publication. A focused lane covering downloads,
artifact storage, catalog, availability and sandbox regressions passed 94 tests
(two existing warnings); the latest focused rerun passed 98 tests. The broader
network-free run passed 1,520 tests (131 deselected, 97 warnings); two staging
tests added subsequently passed separately. A direct pinned-transport
HTTPS fetch of `https://raw.githubusercontent.com/vega/vega-datasets/master/data/seattle-weather.csv`
returned 48,219 bytes; this is not an application/gateway journey or security
certification. Real-service application acceptance remains in Task 7.

**Files:** Create `apps/api/src/openship/tools/download/{__init__,contracts,transport,tool}.py` and `apps/api/tests/tools/test_download.py`; modify `capabilities/{catalog,definitions}.py`, `execution/publication.py` and relevant policy configuration.

**Interfaces:** `DownloadArguments(url: str)` → `DownloadResult(artifact: ArtifactRef, name: str, format: Literal['csv','json'], sample: str)`; `async download_file(arguments, invocation, control) -> DownloadObservation`; register `download_file` as model-selectable and publish its artifact through the existing gateway.

- [x] Cover private redirects/DNS, byte limits, deadline/redirect bounds, accepted/rejected formats, safe errors, TLS/proxy settings, exact publication and failed cleanup in `tests/tools/test_download*.py` and staging tests. Compressed responses are refused rather than decompressed.
- [x] Run the initial download and gateway tests red for missing capability, stream staging and cancellation before implementing each.
- [x] Implement pinned DNS connections, verified TLS, per-hop redirect validation, bounded direct staging and fenced publication without a transaction over the download.
- [x] Committed as `b6cf1b5`; task-done focused run: 98 passed, 2 warnings.

## Task 2: Authorized same-run artifact handoff

**In progress — 2026-11-05:** Added an admission helper to accept selected refs
or exact digest-matching committed outputs of the same run. Gateway-level tests
exercise same-run download handoff and reject an unselected artifact before
provider execution. Unreviewed same-run outputs cannot be used as source files;
explicitly selected sources remain allowed. Additional cross-run, replay and
real-service checks remain open.

**Files:** Modify `runs/agent_graph.py`, `execution/{gateway,admission}.py`, relevant contracts; test `apps/api/tests/integration/test_chat_artifact_handoff.py` and `tests/test_agent_graph.py`.

**Interfaces:** `resolve_chat_inputs(db, *, claim: Claim, requested_ids: tuple[UUID, ...], selected: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]` returns exact ready refs either selected at submission or published by earlier successfully committed operations in the same run; no other model-supplied ID becomes authority.

- [ ] Write `test_download_ref_is_pinned_for_next_call`, `test_other_run_or_conversation_ref_denied`, `test_deleted_changed_or_forged_ref_denied`, and `test_replay_preserves_digest_and_operation_identity`. Assert fetched bytes match committed reference and forged ID never reaches the provider.
- [ ] Run `cd apps/api && uv run pytest -q tests/integration/test_chat_artifact_handoff.py`; observe red.
- [ ] Implement lookup and pinning under the existing live claim and ownership checks; update chat gateway calls to use exactly those authorized refs, not the submission-only tuple or all conversation files.
- [ ] Rerun the handoff and agent graph tests; record output and commit the scoped changes.

## Task 3: General Python tool and source capture

**In progress — 2026-11-05:** `PythonArguments`/`PythonResult` contracts accept
stdout-only tasks, enforce exactly one bounded source, and reject caller-selected
images/unsafe aliases. The registered capability captures operation-scoped `.py`
source and executes via the gateway/client. HTTP-boundary-stub tests cover
stdout-only execution and replay, selected inputs/declared output, and same-run
download handoff; a lost command reply is not redispatched. The affected focused
lane passed 226 tests, and Ruff passed. These use a stubbed provider HTTP boundary;
real-provider execution, cancellation, full regressions and review remain open;
do not mark Task 3 complete.

**Files:** Create `sandboxes/python_tool.py`, `tests/sandboxes/test_python_tool.py`; modify `sandboxes/{client,contracts,tool}.py`, `capabilities/{catalog,definitions}.py`, `execution/publication.py` and `runs/analysis_graph.py` only where shared source capture is extracted.

**Interfaces:** `PythonArguments(source: str | None, source_id: UUID | None, input_ids: dict[str, UUID], parameters: dict, output_media: dict[str, str])` → `PythonResult(source: ArtifactRef, outputs: dict[str, ArtifactRef], exit_code: int, stdout: str, stderr: str)`; `async python_execute(arguments, invocation, control) -> PythonObservation`. Exactly one source form; empty inputs/outputs are valid.

- [ ] Write `test_stdout_only_without_inputs_or_outputs`, `test_source_capture_before_provider_and_exact_input_bytes`, `test_replay_reuses_captured_source_and_committed_result`, `test_lost_command_is_not_retried`, and `test_model_cannot_choose_runtime`. Include cancellation/late publication and changed-input fresh-directory reuse.
- [ ] Run `cd apps/api && uv run pytest -q tests/sandboxes/test_python_tool.py`; observe red.
- [ ] Implement a new versioned `python_execute` capability; reuse existing client, pointer, claim and staged artifact services. Capture source under a stable operation identity before dispatch; retain bounded diagnostics and explicit exit status. Keep `sandbox_analysis` behavior and persisted versions unchanged.
- [ ] Run the new tests plus `uv run pytest -q tests/sandboxes/ tests/integration/test_analysis_results.py`; record output and commit.

## Task 4: Plotting runtime and verified PNG/JPEG outputs

**Files:** Modify `deploy/sandbox/Dockerfile.python`, `deploy/sandbox/images.json`, `sandboxes/{contracts,outputs,python_tool}.py`, API dependency manifest/lock, and `docs/operations/opensandbox.md`; test `tests/sandboxes/test_png_outputs.py` and packaging tests.

**Interfaces:** `validate_image(data: bytes, media_type: str) -> None` fully decodes one PNG/JPEG, requires its decoded format to match `image/png` or `image/jpeg`, and raises `OutputValidationFailed` on invalid image, dimensions >4096 or pixels >16,000,000; `PythonArguments.output_media` admits those image types along with text/JSON.

- [x] Write tests for valid PNG and JPEG (`.jpg`/`.jpeg`), malformed/truncated images of each type, media/format mismatch, `test_4097_dimension_or_16000001_pixels_rejected`, `test_decompression_bomb_rejected`, and `test_runtime_identity_changes_with_plotting_dependencies`. Verify image byte/aggregate limits still hold and BMP/SVG are rejected.
- [x] Run `cd apps/api && uv run pytest -q tests/sandboxes/test_png_outputs.py`; observe red.
- [x] Add a maintained pinned decoder to API dependencies, full bounded image verification, and an operator-built digest-pinned Matplotlib/headless runtime with pinned transitive dependencies. Do not overwrite an existing operator image or change the standard-library runtime identity.
- [x] Run PNG/JPEG and packaging tests, `uv run ruff check .`, and verify the recorded runtime's headless chart generation in an isolated sandbox; record image identity and output before committing.

**Done — 2026-11-05:** `validate_image` lives in `sandboxes/outputs.py` with Pillow
pinned in the API manifest (`pillow>=11.3.0,<12.0.0`, lock resolved 11.3.0);
declared geometry is checked before decode so a bomb never allocates pixels.
Focused file went RED (missing module) then GREEN 16/16; combined sandbox/packaging
lane passed 197 with Ruff clean. Plotting layer built as a separate digest-pinned
Dockerfile (`Dockerfile.python-plotting`, full transitive closure pinned); the
stdlib workload identity is untouched. Isolated headless verification produced
byte-identical PNG/JPEG accepted by the API-side validator.

## Task 5: Explicit durable chat continuation

**Files:** Modify `runs/{resume,routes,schemas,service,agent_graph}.py`, existing queue/publication seams, and frontend API/types; create `tests/integration/test_chat_python_resume.py`.

**Interfaces:** Add authenticated `POST /api/runs/{run_id}/resume-chat-python` with `{mode: 'last_step' | 'from_beginning', client_request_id: UUID}`; return the new or idempotently existing `Run`. Last-step consumes recorded transcript, committed tool results and exact source/input refs; from-beginning uses captured initial refs in a new root and never silently refetches.

- [ ] Write `test_last_step_reuses_download_and_source`, `test_duplicate_resume_is_one_run`, `test_restart_does_not_redispatch_uncertain_command`, `test_from_beginning_uses_original_captured_bytes`, and `test_missing_input_is_actionable`. Verify policy/fence/ownership rejection and no speculative sandbox replacement.
- [ ] Run `cd apps/api && uv run pytest -q tests/integration/test_chat_python_resume.py`; observe red.
- [ ] Implement additive continuation through existing run submission/outbox and transcript/operation records, with persisted operation identity and no hidden URL refetch or source regeneration. Keep analysis A/B behavior unchanged.
- [ ] Run the new tests and `uv run pytest -q tests/sandboxes/test_analysis_resume.py tests/integration/test_analysis_resume.py`; record output and commit.

## Task 6: Chat operation presentation

**Files:** Create `apps/web/src/components/chat/PythonOperation.vue` and `apps/web/tests/PythonOperation.test.ts`; modify `ToolOperationCard.vue`, chat run/continuation components, `services/api.ts` and `types/index.ts`.

**Interfaces:** `PythonOperation` receives a typed operation with authorized artifact summaries and emits download/continuation actions; fetch PNG/JPEG bytes using authenticated `artifactsApi.download`, display an object URL and revoke it on change/unmount.

- [ ] Write tests `shows_progress_and_failures`, `previews_only_owned_png_or_jpeg_from_operation`, `revokes_replaced_and_unmounted_urls`, `ignores_late_response_after_conversation_change`, `downloads_retained_chart`, and `resume_disables_duplicate_submission`. Verify no model-provided external URL is rendered.
- [ ] Run `cd apps/web && pnpm exec vitest run tests/PythonOperation.test.ts`; observe red.
- [ ] Implement accessible progress, preview, download and explicit continuation using existing UI/store conventions. Display stdout for ordinary no-file Python runs; do not label them analysis.
- [ ] Run the new test, existing `RunResume.test.ts`, `pnpm typecheck`, `pnpm test:run`, and `pnpm build`; record output and commit.

## Task 7: Real Task 6 journeys and acceptance

**Files:** Create `apps/api/tests/full_stack/test_sandbox_python_chat.py`, bounded CSV/JSON/parser fixtures, and `apps/web/tests/full-stack/sandbox-python.spec.ts`; update `docs/operations/opensandbox.md` and the original Phase 6B plan.

**Interfaces:** Existing pytest and Playwright runners exercise real API, worker, gateway, PostgreSQL, authenticated JetStream, artifact store and OpenSandbox. A deterministic model fixture may emit tool calls but must never stub the sandbox transport; record any real-model demonstration separately.

- [ ] Verify safe disposable service configuration before executing: isolated database, broker namespace/credentials, artifact root and identity, and authorized provider. If unavailable, record the exact blocker and stop; never count skips as passes or modify the running deployment's volumes.
- [ ] Write and run the real-service test red before final integration wiring: URL → download → model-written Python → PNG → persisted artifact → authenticated browser preview/download; assert exact input digest, observed sandbox ID and fresh request directory. Explicitly enabled lanes fail on missing config, not skip.
- [ ] Extend the live journey for changed-input sandbox reuse, provider-confirmed expiry/absence replacement, general no-input stdout execution, unavailable provider, accepted/rejected returned source, explicit A/B analysis resume, chat continuation/idempotency, and retained artifacts after restart/provider disappearance. Use a disposable short-lived fixture only for expiry; production stays 1,800 seconds.
- [ ] Run the real API and browser tests, affected PostgreSQL/JetStream lanes, `uv run ruff check .`, `uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'`, `pnpm typecheck`, `pnpm test:run`, and `pnpm build`; record exact commands, counts, image identities, candidate revision, limitations and expected artifact refs.
- [ ] Reconcile every original Task 6 criterion with new chart evidence, recording partial results without checking unmet items. Commit verified documentation/evidence with a scanned message file.
- [ ] Present the runnable demonstration for user accept/revise/reject. Record acceptance separately; do not claim Task 6 accepted before the user's decision.
