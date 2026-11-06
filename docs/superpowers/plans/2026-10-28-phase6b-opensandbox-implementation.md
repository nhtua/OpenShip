# Phase 6B OpenSandbox Client Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` for native execution, or `superpowers:subagent-driven-development` if the user chooses delegation. Follow `git-workflow` for verified commits. Steps use unchecked checkboxes; carried setup and old test results do not complete implementation tasks.

**Goal:** Generate a small analysis program, send its selected inputs to OpenSandbox, validate and retain its outputs, and support explicit workflow resume.

**Architecture:** Extend the existing gateway/run/artifact path with a narrow provider client. OpenSandbox is the Sandbox Manager service in `docker-compose.yml`, using the rootful Docker socket and gVisor by default; reuse the existing Ansible bootstrap for host dependencies. The current conversation-backed slice reuses one compatible provider handle during its requested 1,800-second lifetime; each run gets a fresh execution directory and independent artifact records. A future explicit workflow identity must separate workflows; OpenSandbox alone expires, cleans and deletes sandboxes. Existing graph state stores accepted outputs, not sandbox snapshots or replayable process state.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy/Alembic, LangGraph, existing PostgreSQL/JetStream/artifact storage, httpx or the upstream Python SDK, Vue/TypeScript, standalone OpenSandbox on rootful Docker/gVisor.

**Spec:** [Approved scope](../specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md).

**Reset:** 2026-11-03. Start from `main` (`5fe6e03` at reset), not the old `0a1fc6b` application implementation. [Scope review and carry-forward decisions](../../reviews/2026-11-03-phase6b-scope-reset.md). Tasks 1, 1A, 2 and 3 have committed implementation/evidence; the final review identified required corrections in the pre–Task 4 gate below. Tasks 4–6 remain open. The client alone is not an integrated application feature.

**Plan revision — 2026-11-03:** Split the remaining Task 1 execution deliverable into **Task 1A**, including provider startup and real-provider verification before downstream integration. At the user's request, execution order is **1A → 1 → 2 → 3 → 4 → 5 → 6**; the original Task 1 goal is complete only when **both Task 1 and Task 1A** pass. Task 6 still owns the full application/parser/resume demonstration.

## Global constraints

- Request 1,800-second sandbox lifetime, 30-second command timeout, 1 CPU and 512 MiB. Provider owns enforcement; no TTL renewal or per-step destruction.
- One reusable pointer per project/conversation for this slice: run/intent owner, provider identity, sandbox ID, requirement key and expiration. Do not treat conversation identity as a future universal workflow ID. No installation-wide physical reservation, cleanup state machine or orphan reconciliation.
- Reuse compatible live sandboxes; source/input/parameter changes use a fresh request directory, not a new container. Replace on changed runtime requirements or definitive expiry/absence, only during an authorized execution request.
- Unknown provider/command outcomes stop the attempt, not trigger POST retries or speculative replacement. Existing run/operation fencing and request idempotency remain authoritative.
- Python 3.11 standard-library profile only. Fixed argv; no application/cloud credentials or host execution. Socket access belongs only to OpenSandbox infrastructure.
- OpenSandbox is the manager, not a new OpenShip lifecycle service. Add its manager/gateway services to `docker-compose.yml` under the opt-in `sandbox` profile, reusing the carried overlay/configuration. Default to `CONTAINER_ENGINE=docker`, `DOCKER_HOST=unix:///var/run/docker.sock` and manager-only `/var/run/docker.sock:/var/run/docker.sock`; select `runsc` for workloads. Docker and Podman are both installed on the development machine, but Podman/rootless alternatives are not default targets or Task 1A qualification prerequisites.
- Reuse `scripts/bootstrap/playbooks/arch-docker.yml` (or the existing dual-engine playbook) for gVisor/engine dependencies; do not duplicate provisioning or reapply privileged host changes without need/operator authorization. Provider-only testing may start OpenSandbox from this worktree, without starting the OpenShip application stack or sharing another worktree's volumes.
- Source 256 KiB; 8 inputs, 8 MiB each/16 MiB total; 8 outputs, 4 MiB each/8 MiB total; stdout/stderr 256 KiB combined; parameter JSON 16 KiB. Reuse existing quotas and artifact access rules.
- Accept declared text/JSON; expected returned code is inert text and requires an LLM expectation decision before use. No security scanning, binary execution, archives or active previews.
- Resume A uses prior accepted outputs and reruns only the interrupted step; B starts a new root run from original initial refs. Restart/redelivery alone never creates or executes a sandbox.
- No mutable-file editor/history system, provider forks, full saved-workflow engine, dedicated qualification platform, production matrix or load-study prerequisite.

## Review focus

1. A reused sandbox still contains old files: a new request must transfer all current selected inputs into a distinct directory, never read residual inputs.
2. A saved ID may expire or the provider may be unreachable: absence permits on-demand creation; timeout/uncertainty does not imply absence or authorize replay.
3. Returned code may claim it is safe or match only a truncated excerpt: judge complete bounded text against the trusted expectation, and keep it inert.
4. A worker can stop after output commit but before acknowledgement: reuse the committed result and never repeat execution merely to recover the acknowledgement.
5. A user may double-click resume or delete an earlier input: return one new run per request identity, or an actionable missing-input error without hidden upstream reruns.

## File responsibilities and shared interfaces

Paths are relative to the repository root; these are planned targets, not claims that code exists. Keep the large worker/gateway as delegation points, not new orchestration frameworks.

| Files | Owns |
| --- | --- |
| `apps/api/src/openship/sandboxes/contracts.py` | Small immutable request/result and runtime-requirement models using existing `ArtifactRef` and canonical digest helpers. |
| `apps/api/src/openship/sandboxes/client.py` | Documented provider API calls, fixed execution, bounded transfers/completion. No delete/list-orphans/renew controller. |
| `docker-compose.yml`, `docker-compose.sandbox*.yml`, `scripts/openship-stack`, `deploy/sandbox/` | Existing OpenSandbox manager/gateway setup integrated into base Compose, Docker/gVisor defaults and provider-only startup; preserve optional alternatives without duplicate service definitions. |
| `apps/api/tests/live/test_opensandbox.py`, `docs/operations/opensandbox.md` | Opt-in real-provider client/lifecycle smoke test and reproducible worktree-local commands/evidence; no new qualification framework. |
| `apps/api/src/openship/sandboxes/models.py`, `sessions.py` | One project/conversation-scoped reusable-handle row and on-demand selection/persistence. |
| `apps/api/src/openship/sandboxes/tool.py`, `outputs.py` | Authorized input preparation, gateway integration, output validation/judging and existing artifact/result publication. |
| `apps/api/src/openship/runs/analysis_graph.py`, `resume.py` | Fixed analysis journey/checkpoints and explicit A/B commands, without a generic workflow compiler. |
| Existing capability/configuration/run files | Add only necessary new contracts/versions/dispatch wiring; old versions keep their meaning. |
| Existing web API/types/run card and `apps/web/src/components/chat/RunResume.vue` | Submit task/resume commands; show retained result links and safe failures. No editor/inspector prerequisite. |

Define these shared types before consumers:

- `RuntimeRequirements`: configured provider identity, profile ID, image/dependency identity, CPU and memory; `key` is their canonical digest. User data cannot select arbitrary images. Source/data/parameters are excluded from this key.
- `InputFile(alias, artifact: ArtifactRef)` and `OutputSpec(alias, media_type, kind: data|source, expectation: str|None)`. Aliases are single safe filename components. Source outputs require the trusted task expectation.
- `ExecutionRequest(root_run_id, operation_id, requirements, source: ArtifactRef, inputs, parameters, outputs)`; `PreparedFiles` holds verified bytes only in worker memory/staging, never checkpoints.
- `SandboxHandle(provider_id, sandbox_id, requirements_key, expires_at)`; `ExecutionResult(exit_code, stdout, stderr, outputs: mapping[str, bytes])`; `ReviewDecision(accepted: bool, reason: str, output_sha256: str)`.
- `AnalysisState`: initial refs, prepared source/input refs, current step (`prepare|execute|use_outputs`), accepted output refs, review decisions and original root run ID. Store ordinary JSON/ref data through existing checkpoints/configuration, not provider secrets or a second input-byte store.

Each task follows red → minimal implementation → green → reviewed commit. Use a task-specific `/tmp/` message file, scan it with Rayloc immediately before `git commit --file`, and do not bypass hooks. No new PR or rollout is implied.

### Task 1: Provider lifecycle client and bounded request foundation

**Files:** Create `apps/api/src/openship/sandboxes/{__init__,contracts,client}.py`; test `apps/api/tests/sandboxes/test_client.py`; modify `apps/api/src/openship/config.py` and dependency locks only if choosing the upstream SDK.

**Interfaces:** Produce async `OpenSandboxClient.create(requirements: RuntimeRequirements) -> SandboxHandle` and `inspect(handle: SandboxHandle) -> SandboxHandle|None`, plus the shared contracts above using existing `ArtifactRef`. Expose safe transport errors; `None` means definitive absence, not timeout. Task 1A adds `execute` and verifies the complete client against OpenSandbox.

**Complete — 2026-11-03:** Commit `761bd0a` (Task 1A) plus the Task 1 lifecycle commit add the frozen contracts and authenticated create/inspect adapter. New Task 1 tests first exposed acceptance of missing/non-running create status (4 cases red), then passed after the fix; duplicate aliases, oversized source/inputs/parameters, invalid output declarations, unsafe sandbox ids/unparsable expiry, malformed inspection bodies, running inspection with provider expiry, definitive absence versus unavailability and redacted provider failures are covered. Focused run passed **61 tests**; Ruff passed; the live-provider journey re-passed (1 test, 94.40s); the network-free backend regression re-passed **1359 tests** (124 deselected). No automatic create retry, no delete/renewal path, lost replies interrupt without retry. Execution, transfer, cancellation, output bounds and real-provider verification are verified under Task 1A below.

- [x] Contract/lifecycle tests prove create requests `1800`/`1`/`512Mi` with the authenticated key; invalid/duplicate aliases, oversized source/inputs/parameters, invalid output declarations, malformed lifecycle responses and unsafe sandbox identity fail; running inspection, definitive absence and unavailability are separate cases; lost replies interrupt with exactly one call and no delete/renewal path.
- [x] `uv run pytest -q tests/sandboxes/test_client.py` observed the four new create-status cases red, then the full focused suite green.
- [x] Frozen contracts plus authenticated create/inspect adapter complete; provider bodies and the API key never enter `SandboxUnavailable`; documented release-compatible lifecycle APIs are used, with no cleanup or orphan-listing path.
- [x] Focused rerun passed 61 tests and `uv run ruff check src/openship/sandboxes tests/sandboxes/test_client.py` passed; single-call create/command behavior and the no-context-cleanup review confirmed by the lost-reply tests.
- [x] Foundation committed with the scanned message-file workflow and passing hooks; the original execution goal closes only after Task 1A passed above.

### Task 1A: Complete sandbox execution and verify the real OpenSandbox provider

**Files:** Extend `apps/api/src/openship/sandboxes/{contracts,client}.py`, `apps/api/src/openship/config.py`, `apps/api/tests/sandboxes/test_client.py` and `apps/api/tests/test_opensandbox_packaging.py`; create `apps/api/tests/live/test_opensandbox.py`. Integrate existing `docker-compose.sandbox.yml` services into `docker-compose.yml`; update `docker-compose.sandbox.rootless.yml`, `scripts/openship-stack`, `.env.example`, `deploy/sandbox/{config.example.toml,images.json,Dockerfile.python,nginx.conf}` and `docs/operations/opensandbox.md` only as required. Preserve unrelated carry-forward/user changes. Reuse `scripts/bootstrap/`; do not add another installer.

**Interfaces:** Consume Task 1's contracts/create/inspect and existing `InvocationControl` from `providers/control.py`. Produce async `OpenSandboxClient.execute(handle: SandboxHandle, request: ExecutionRequest, files: PreparedFiles, control: InvocationControl) -> ExecutionResult`. Use documented file/command APIs through the configured authenticated gateway. Transfer, execution, bounded output collection and explicit completion belong here; authorization/materialization of stored artifact refs and publication remain Task 3. Expose safe interruption/completion/limit errors without credentials or raw provider bodies.

**Acceptance gate:** The original Task 1 goal requires file transfer, command execution, bounded diagnostics/output collection, cancellation and a passing real-provider client journey. Create/inspect tests, Compose rendering or a healthy manager alone cannot satisfy this gate. Task 1A is not the later API/worker/parser/resume acceptance test in Task 6.

**Complete — 2026-11-03:** Commit `761bd0a` adds the manager/gateway, client and deployment setup; all commit hooks passed. The combined task-done check passed **66 tests** including the real provider. It covered create/inspect, changed-input reuse in distinct directories, declared output collection, nonzero exit, the provider's 30-second timeout, link rejection, diagnostic/output overflow, cancellation and separate 60-second expiry. Ruff passed; the network-free backend run passed 1346 with 124 deselected. Frontend typecheck, 344 tests and build passed. Task 1 and downstream tasks remain open. Running project: `openship-phase6b-client`; protected settings are temporary under `/tmp/opencode/` and removed on reboot. Do not disturb other projects.

- [x] Verify the opt-in Compose manager/gateway, Docker default, `runsc`, authenticated private networks and manager-only rootful socket; retain the loopback-only host-test override and optional Podman alternative. Packaging tests were observed failing before the Compose change and then passed; placeholder rendering succeeded.
- [x] Test and implement verified source/input/parameter transfer into distinct request directories and fixed Python argv with a provider-enforced 30-second timeout. Reject mismatched refs, unsafe aliases and oversized inputs before transfer.
- [x] Test and implement explicit zero completion, bounded diagnostics, interruption/no-retry behavior, and cancellation/deadline handling during upload, command and download. Cancellation does not promise process termination or sandbox deletion.
- [x] Test and implement declared regular output collection, rejecting missing/undeclared files, directories, links, unsafe paths, partial downloads, and **4 MiB each/8 MiB total** overflow based on actual bytes. Task 3 still owns text/JSON semantic validation and durable publication.
- [x] Run focused client/packaging tests and Ruff; verify no application create/command retries, engine access, delete or renewal paths.
- [x] Start only the worktree-local OpenSandbox manager/gateway on rootful Docker and run the opt-in real-provider test for create/inspect, input changes, distinct-directory reuse, output, failures, cancellation and separate 60-second expiration. Production creates request 1,800 seconds. The combined task-done test passed 66 tests.
- [x] Record reproducible commands, image identities, results and limitations in `docs/operations/opensandbox.md`; commit the scoped changes as `761bd0a` using a scanned message file and passing hooks. This is not isolation certification or the Task 6 application journey.

### Task 2: Conversation-scoped sandbox reuse

**Files:** Create `apps/api/src/openship/sandboxes/{models,sessions}.py` and the next unused Alembic revision for `WorkflowSandbox`; modify `apps/api/src/openship/database/models.py`; test `apps/api/tests/sandboxes/test_sessions.py` and `apps/api/tests/integration/test_workflow_sandbox_postgres.py`.

**Interfaces:** Consume Task 1's create/inspect/contracts after Task 1A verifies the complete client/provider boundary. Produce async `get_or_create_sandbox(session_factory, client: OpenSandboxClient, *, claim: Claim, root_run_id: UUID, requirements: RuntimeRequirements) -> SandboxHandle`. Persist one reusable pointer per project/conversation in this slice, with unique intent identity for each owning run. Provider waits have no open DB transaction; recheck the matching intent and live claim before saving a returned ID. Serialize cross-run selection at the conversation lock. An unresolved create blocks speculation until its conservative lifetime bound, and a late acknowledgement cannot replace a successor. Do not introduce a provider-reconciliation ledger.

**Historical implementation — 2026-11-03, corrected before Task 4:** the first pointer used one row per `root_run_id` in `phase6b026`; final review superseded that scope. Revision `phase6b028` migrates to one project/conversation pointer and records request time/intent plus command uncertainty. Earlier 11-unit/4-PostgreSQL results below are historical evidence for the original behavior, not acceptance of shared reuse.

**Initial implementation evidence (historical):** `sandboxes/{models,sessions}.py` added the first pointer and on-demand selection: request intent is committed before any provider round-trip, inspect/create waits run with no open DB transaction, and a returned sandbox ID is saved only while the same durable claim is still current. A committed-but-unacknowledged create raises `SandboxCreationUncertain` and blocks automatic recreation (checked before claim freshness, so an expired claim never proves absence). Alembic revision `phase6b026` (down_revision `phase6a020`) avoids the abandoned branch's 021–025 identifiers; its downgrade refuses while pointers exist. Unit lane `tests/sandboxes/test_sessions.py` passed **11 tests** (red first: claim-lock and commit-semantics failures were root-caused to dangling FK seeds and session-context close semantics, fixed at the source). PostgreSQL lane `tests/integration/test_workflow_sandbox_postgres.py` passed **4 tests** against a disposable PostgreSQL 16 service (`postgres:16`, the docs expect 15): fresh main-data alembic upgrade to head `phase6b026`, independent-session reuse of one handle, table-level duplicate-pointer rejection, and claim-stolen-during-wait blocking acknowledgement and recreation. Ruff passed; the network-free backend rerun and live-provider journey are tracked by the regression below.

- [x] Historical tests: equal requirements reused within one root; runtime changes replaced the pointer; provider unavailability blocked create. These tests were revised for conversation scope and conservative uncertainty as part of the pre–Task 4 correction gate.
- [x] Unit module ran red first (10 failures: the claim-lock helper could not resolve the seeded FK chain, then session-context close discarded pending mutations), then green after fixing the seed's explicit ids and adding explicit commits at each mutation point — 11 passed; the pointer row leaves replaced/lost sandboxes to the provider TTL with no deletion/capacity/orphan machinery.
- [x] Historical Alembic `phase6b026` installed the initial per-run table; migration `phase6b028` replaces its uniqueness with conversation scope and adds bounded-intent state. PostgreSQL verification for the current schema remains part of the correction gate.
- [x] `uv run pytest -q tests/sandboxes` passed 72 (11 new + 61 Task 1); `uv run pytest -q -m postgres tests/integration/test_workflow_sandbox_postgres.py` passed 4 with a disposable `postgres:16` service via `TEST_POSTGRES_URL` on loopback :5433; missing infrastructure skips with a report rather than passing.
- [x] Commit the verified reuse task with the scanned message file (`4478d0e`; hooks passed: API Ruff, API unit tests, Run Tests, frontend checks, staged secret scan).

### Task 3: Wire preparation, execution and retained data outputs end to end

**Files:** Create `apps/api/src/openship/sandboxes/{tool,outputs}.py` and `apps/api/src/openship/runs/analysis_graph.py`; extend existing `artifacts/service.py`, `capabilities/{contracts,catalog,definitions}.py`, `execution/{gateway,publication,availability}.py`, `runs/{service,dispatch,worker}.py` only at their integration seams. Test `apps/api/tests/sandboxes/test_tool.py` and `apps/api/tests/integration/test_analysis_results.py`.

**Interfaces:** Consume Tasks 1, 1A and 2. Produce `prepare_execution(db, *, claim: Claim, request: ExecutionRequest, store) -> PreparedFiles`, `validate_outputs(request: ExecutionRequest, result: ExecutionResult) -> mapping[str, bytes]`, and async `execute_analysis(context, request: ExecutionRequest, control: InvocationControl) -> PublishedToolResult`. The gateway owns its usual operation/attempt, input pins, active slot and publication transaction. The fixed graph prepares source/inputs, executes and uses accepted outputs.

- [x] Tests written and green using real artifact records/store: selected current bytes plus generated JSON/log outputs reach a new operation-specific directory, earlier sandbox files cannot replace them (two root runs, distinct `cwd`, distinct produced bytes), wrong-scope/deleted refs fail, and saved reports survive provider disappearance. Nonzero/missing completion, invalid/oversized/undeclared files and cancellation/lost fence cannot publish success; a committed result replays without another provider command (`apps/api/tests/sandboxes/test_tool.py` 14 cases).
- [x] Implemented materialization of existing source/input artifacts, Task 1A's transfer/execution/collection client and text/JSON data validation (`sandboxes/outputs.py`, `sandboxes/tool.py`). Output refs, results and events publish through the existing fenced staging/publication helpers, and nothing waits for sandbox deletion. Generated-source capture and pinned-model handoff were added in the pre–Task 4 correction work below.
- [x] Registered the exact capability (`sandbox_analysis`, credential-free v1 manifest) and the supported graph/configuration version through the production dispatch table (`_builtin_definition`, `list_capability_manifests`), availability (`sandbox_runtime` readiness) and worker path; execution remains only through the authorized run/tool-command routes, with the fixed journey resolved from the run's pinned execution configuration.
- [x] Pre–Task 4 correction adds explicit `source_prompt` generation using the submission-pinned model, bounded provider attempts, immutable artifact capture and exact-reference handoff. Verification and PostgreSQL evidence remain in the correction gate.
- [x] Focused tests green: `uv run pytest -q tests/sandboxes/ tests/integration/test_analysis_results.py` → 91 passed (61 Task 1 + 11 Task 2 + 14 Task 3 + 5 integration), `uv run ruff check .` passed, and the API→real worker→gateway fixture runs with provider transport stubbed only at its HTTP boundary. The production handler executes rather than remaining a fail-closed placeholder; the full real-provider demonstration remains Task 6.
- [x] Committed the working data-output path with a scanned message file (`7722a9d`; hooks passed: API Ruff, API unit tests, Run Tests, frontend checks, staged secret scan).

### Pre–Task 4 correction gate — final Tasks 1–3 review

**Status:** correction gate satisfied; Task 4 completed after one review/fix
round. Earlier checked items record their original verification, not acceptance
of the subsequently clarified reuse semantics. See
[the final review](../../reviews/2026-11-04-phase6b-tasks-1-3-final-review.md)
for file/line evidence, reproductions and the superseded earlier assessment.

- [x] Reconcile the spec/plan/operations/SDD scope wording: compatible runs in one project/conversation may reuse the environment, but directories/artifacts remain per execution. No workflow engine or conversation-conversion feature was added; conversation is not a universal workflow identity (R1).
- [x] Implement deterministic shared-pointer selection, conversation-lock serialization, UUID intent identity, latest-intent acknowledgement and migration cleanup of prior duplicate pointers (R1).
- [x] Bound stale-create recovery, block same-sandbox reuse after uncertain command/cancellation until provider expiration, and reject expired create/inspect handles without treating provider unavailability as absence (R2–R4).
- [x] Preserve fail-closed source declarations, keep failed execute incomplete, and implement pinned-model generated-source capture and exact-ref handoff (R5–R7).
- [x] Bind provider compatibility to endpoint/deployment identity, remove installed unpublished files after partial output staging, and close invocation-owned but not caller-owned HTTP clients (R8–R10).
- [x] PostgreSQL fixture at `phase6b028`: 7 passed, including independent-session contention, late acknowledgement/equal fence, and artifact constraint checks. Focused lanes: 124 passed; network-free regression: 1,411 passed, 131 deselected. Missing infrastructure was not counted as a pass (R11).
- [x] Reconcile spec/plan/operations/SDD wording and record final verification. Corrections committed in `098ec7c`, `a92eb72`, and `cf3bcbe` before Task 4; do not edit applied 026/027 history or reuse abandoned 021–025 revision IDs.

**Review evidence (historical):** original focused lane 91 passed; head was
`phase6b027`; PostgreSQL was skipped without `TEST_POSTGRES_URL`; nine probes
reproduced the review findings. Correction work below is separate and complete.

**Correction verification:** `uv run pytest -q tests/sandboxes/ tests/integration/test_analysis_results.py tests/test_tool_run_commands.py` → 124 passed; disposable PostgreSQL 16 lane at head `phase6b028` → 7 passed; `uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'` → 1,411 passed, 131 deselected; Ruff passed; `uv run alembic heads` → `phase6b028 (head)`; `git diff --check` passed. 97 existing/deprecation warnings were reported in the network-free lane.

Correction execution order (TDD, each test must be observed failing first):

1. **Shared reuse state:** permanent unit/PG tests for same-scope runs, distinct
   scopes, shared-pointer uniqueness/selection, same numeric fence across runs,
   intent/ack ownership, unresolved-intent ordering and two-run create races.
   Then add schema/migration updates and adjust API integration reuse evidence.
2. **Provider uncertainty:** tests for stale requested create recovery only after
   conservative expiry, lost command reply blocking later dispatch, fresh
   sandbox expiry versus an expired handle returned as Running, and transport
   unavailable not being treated as absence.
3. **Task 3 contract/state:** source declarations remain fail-closed with kind /
   expectation preserved; failed execute stays the first incomplete phase;
   partial staging removes all known uncommitted keys but preserves ready or
   uncertain-commit content.
4. **Preparation/model handoff:** trace existing pinned-model runner and budget;
   add `source_prompt` as the explicit generation request (mutually exclusive
   with an existing `source_id`); pin the default model snapshot at analysis
   submission; run a model-only bounded turn using that snapshot and normal
   attempt/budget accounting; capture bounded source as an immutable artifact;
   persist its exact ref/digest in journey state and pass that ref to execution.
   Test exact generated bytes/ref handoff, provider/model outage, size/finish
   rejection, redelivery reusing captured source, and no sandbox execution
   before an authorized task submission. No generic workflow/compiler.
5. **Deployment/transport:** test provider endpoint/deployment identity affects
   compatibility; ensure invocation-owned `AsyncClient` closes on success,
   failure and cancellation, while injected clients remain caller-owned.
6. **PostgreSQL and docs:** expect `phase6b028` head and `analysis_output`
   constraint; run PostgreSQL independent-session contention / late ack / equal
   fence cases; reconcile current spec/plan/operations/ledger wording and then
   focused plus network-free regressions. Do not check this gate until all
   required criteria are verified.

### Task 4: Returned-source expectation review

**Files:** Extend `apps/api/src/openship/sandboxes/outputs.py`, `tool.py` and the analysis graph's result step; test `apps/api/tests/sandboxes/test_output_review.py`.

**Interfaces:** Produce async `review_source_output(*, output: bytes, spec: OutputSpec, model_context, control: InvocationControl) -> ReviewDecision`, using the existing pinned provider runner and budgets. Task 3 calls it before publishing a source output as accepted. Persist its decision/digest in the existing result/state, not another approval service.

- [x] Write tests for expected code accepted/rejected against a trusted expectation, unexpected source, a response trying to instruct the judge, unavailable/malformed review and content exceeding the review budget. Verify no partial/truncated text receives approval and returned code never runs/imports in OpenShip. Existing text/JSON previews remain escaped and binary/archive outputs fail.
- [x] Run the module red; implement bounded expectation-only review with trusted instructions separate from returned text. Whole content must fit review limits; otherwise fail visibly. A review failure does not repeat sandbox execution or grant authority.
- [x] Rerun output-review and tool tests green. Check the exact reviewed SHA-256 equals the stored accepted source; a passing decision is not labeled a security scan or safety certificate.
- [x] Commit with scanned message files; hooks passed (`8e3cac4`, `809e651`, `f3f7814`, `bd08cdc`). Task/consumer tests: 150 passed; network-free API lane: 1,434 passed, 131 deselected. Independent review raised one Critical and two Important cross-task issues; one TDD fix round addressed all three, and scoped re-review passed.

### Task 5: Explicit A/B resume and minimal run UI

**Files:** Create `apps/api/src/openship/runs/resume.py`, extend `analysis_graph.py`, `runs/{routes,schemas,service}.py` and only needed additive persistence; create `apps/web/src/components/chat/RunResume.vue`, extend existing run card, `services/api.ts` and `types/index.ts`. Test `apps/api/tests/sandboxes/test_analysis_resume.py`, `apps/api/tests/integration/test_analysis_resume.py`, and `apps/web/tests/RunResume.test.ts`.

**Interfaces:** Produce `ResumeAnalysisRequest(mode: last_step|from_beginning, client_request_id: UUID)` and `resume_analysis(db, *, principal_id: UUID, run_id: UUID, command: ResumeAnalysisRequest) -> Run`, exposed as authenticated `POST /api/runs/{run_id}/resume-analysis`. Consume `AnalysisState` and Task 2's project/conversation-scoped pointer; keep identity scope separate from per-run state and use existing run submission/outbox rather than a second scheduler.

- [x] Write tests: A resumes `use_outputs` with accepted report refs and **zero** provider calls; A resuming `execute` uses prior step refs and compatible live handle or on-demand replacement after expiry. B starts a new root from initial refs, reruns preparation/execution and leaves old results unchanged. Missing/pruned inputs fail without earlier-step recomputation. Double-click/replayed resume returns one new run; restart after dispatch marks interruption instead of resending code; lost acknowledgement after commit reuses the result.
- [x] Run those backend tests red; implement the fixed journey's checkpoint/resume command. Keep initial refs/output refs in ordinary state. An uncertain still-running command yields an actionable blocked/interrupted state until provider availability is definite; never use a snapshot or hidden input-byte replay store.
- [x] Write/run frontend tests red, then add only the two named resume choices and retained result/error links in existing run UI. Bind to the exact run/request identity; disable duplicate submissions and keep unrelated chat usable. No source editor or bundle inspector.
- [x] Run backend/database cases, `pnpm typecheck` and `pnpm exec vitest run tests/RunResume.test.ts` green. Verify restart alone starts no sandbox and cancellation rejects late output without deleting the provider sandbox. Additional verification: 1,471 API tests and 350 frontend tests passed; typecheck/build passed. Two Important review findings were fixed TDD (`8d3447d`) and scoped re-review passed; full evidence in the SDD Task 5 report.
- [x] Commit with scanned message files (`11188e8`, `9a75119`, `8d3447d`, `763cdd0`); hooks passed.

### Task 6: One real parser demonstration and existing regressions

**Preparation update — 2026-11-05:** The user has started the dev application,
PostgreSQL, NATS, and OpenSandbox services and authorized their use for Task 6.
The earlier provider-reuse authorization blocker is superseded; disposable
database, isolated broker/artifact configuration, and service compatibility
still need verification. The user now requests natural-language URL download
→ generated Python → general sandbox execution → inline chart, explicitly not
an analysis-specific executor. The proposed
[scope extension](../specs/2026-11-05-phase6b-chat-python-execution-design.md)
awaits written review and a follow-up implementation plan. No new journey has
been run and no Task 6 acceptance item is complete. Preserve the original
criteria below and map them into that plan alongside the new scenario.

**Historical prerequisite stop — 2026-11-04 (NOT COMPLETE):** The safe read-only prerequisite
check found no authorized disposable PostgreSQL/JetStream targets, application
stack configuration, or explicit permission/configuration for this worktree to
reuse the existing provider deployment. No real journey test or application
code was added; therefore no Task 6 application/demo criteria are checked. See
`.superpowers/sdd/2026-10-28-phase6b-opensandbox-implementation/task-6-report.md`
for sanitized inspection evidence, ordinary regression results, and the exact
operator inputs needed to resume. The full parser journey and user acceptance
remain pending; the provider stack was not modified.

**Files:** Create `apps/api/tests/full_stack/test_sandbox_analysis.py` and small log/parser/returned-code fixtures; extend `apps/web/tests/full-stack/` for the parser/resume journey and `docs/operations/opensandbox.md`. Register opt-in real-service marker/exclusions consistently if needed; do not add a custom qualification launcher/CI platform.

**Interfaces:** Exercise the real registered API/worker/gateway, PostgreSQL, JetStream, artifact store and configured OpenSandbox endpoint, reusing Task 1A's verified Docker/provider setup. Task 1A's direct-client smoke is a prerequisite, not a substitute for this full application journey. Generation/judge may use clearly labeled deterministic provider fixtures; real-model evidence is separate. Unit transport stubs cannot satisfy this task.

- [ ] Write the real-service journey: upload logs, generate/capture parser, execute, validate and use `summary.json`/`report.txt`; make a second compatible request with changed logs and prove ID reuse and current inputs. Test real expiry/absence replacement using a disposable provider fixture or a previously expired ID; production requests remain 1,800 seconds.
- [ ] Run it red before final wiring; complete only integration gaps required by the approved spec. Demonstrate accepted/rejected returned source, explicit A/B resume, retained downloads after restart/provider disappearance and a visible unavailable-provider failure. No provider hardening or cleanup controller may be introduced to pass the test.
- [ ] Run existing ordinary backend/frontend checks plus affected PostgreSQL/JetStream/browser tests. Record exact commands, candidate revision, expected output refs and limitations in the operator guide. Real tests require an explicitly configured service and must execute rather than silently skip; no dedicated protected runner is a completion prerequisite.
- [ ] Present the runnable demonstration for user accept/revise/reject. Keep implementation, test results and user acceptance distinct; do not inherit old progress.
- [ ] Commit verified evidence/documentation with a scanned message file.

## Verification and scope stop

Existing network-free checks (from `apps/api` and `apps/web` respectively):

```bash
uv run ruff check .
uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'
pnpm typecheck
pnpm test:run
pnpm build
```

Task-specific additions above are **planned**, not implemented by this document;
Task 1 records only historical partial checks. Task 1A must record the real-provider
client run before its acceptance gate can pass. Task 6 must separately document
its exact configured application-journey invocation and marker selection while
preserving existing service-independent CI. Use current repository PostgreSQL,
JetStream and full-stack runners rather than a new qualification platform.

- [ ] All seven deliverables (Tasks 1, 1A and 2–6) verified against the approved spec; the original Task 1 goal includes Task 1A's execution and real-provider gate.
- [ ] OpenSandbox manager/gateway available through base Compose with rootful Docker/gVisor defaults, manager-only socket access and recorded worktree-local live-client evidence.
- [ ] One real parser/reuse/resume demonstration and affected existing regressions recorded.
- [ ] No application lifecycle controller, per-step delete or physical deletion gate introduced.
- [ ] User acceptance recorded separately from passing tests.

**Stop if scope expands:** AWS credentials/CLI, another runtime, mutable-file
management, provider repairs, stronger isolation certification or production
qualification require a separate proposal. Do not turn them into blockers here.
