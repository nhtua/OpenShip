# Phase 6B OpenSandbox Client Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` for native execution, or `superpowers:subagent-driven-development` if the user chooses delegation. Follow `git-workflow` for verified commits. Steps use unchecked checkboxes; carried setup and old test results do not complete implementation tasks.

**Goal:** Generate a small analysis program, send its selected inputs to OpenSandbox, validate and retain its outputs, and support explicit workflow resume.

**Architecture:** Extend the existing gateway/run/artifact path with a narrow provider client. OpenSandbox is the Sandbox Manager service in `docker-compose.yml`, using the rootful Docker socket and gVisor by default; reuse the existing Ansible bootstrap for host dependencies. A workflow-scoped provider handle permits compatible reuse during the requested 1,800-second lifetime; OpenSandbox alone expires, cleans and deletes sandboxes. Existing graph state stores accepted outputs, not sandbox snapshots or replayable process state.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy/Alembic, LangGraph, existing PostgreSQL/JetStream/artifact storage, httpx or the upstream Python SDK, Vue/TypeScript, standalone OpenSandbox on rootful Docker/gVisor.

**Spec:** [Approved scope](../specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md).

**Reset:** 2026-11-03. Start from `main` (`5fe6e03` at reset), not the old `0a1fc6b` application implementation. [Scope review and carry-forward decisions](../../reviews/2026-11-03-phase6b-scope-reset.md). Tasks 1 and 1A are complete; Tasks 2–6 remain open. The client alone is not an integrated application feature.

**Plan revision — 2026-11-03:** Split the remaining Task 1 execution deliverable into **Task 1A**, including provider startup and real-provider verification before downstream integration. At the user's request, execution order is **1A → 1 → 2 → 3 → 4 → 5 → 6**; the original Task 1 goal is complete only when **both Task 1 and Task 1A** pass. Task 6 still owns the full application/parser/resume demonstration.

## Global constraints

- Request 1,800-second sandbox lifetime, 30-second command timeout, 1 CPU and 512 MiB. Provider owns enforcement; no TTL renewal or per-step destruction.
- Workflow-scoped reusable handle only: root run ID, provider identity, sandbox ID, requirement key and expiration. No installation-wide physical reservation, cleanup state machine or orphan reconciliation.
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
| `apps/api/src/openship/sandboxes/models.py`, `sessions.py` | One workflow-scoped reusable-handle row and on-demand selection/persistence. |
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

### Task 2: Workflow-scoped sandbox reuse

**Files:** Create `apps/api/src/openship/sandboxes/{models,sessions}.py` and the next unused Alembic revision for `WorkflowSandbox`; modify `apps/api/src/openship/database/models.py`; test `apps/api/tests/sandboxes/test_sessions.py` and `apps/api/tests/integration/test_workflow_sandbox_postgres.py`.

**Interfaces:** Consume Task 1's create/inspect/contracts after Task 1A verifies the complete client/provider boundary. Produce async `get_or_create_sandbox(session_factory, client: OpenSandboxClient, *, claim: Claim, root_run_id: UUID, requirements: RuntimeRequirements) -> SandboxHandle`. Persist one reusable pointer per authorized root run, scoped through its project/conversation. Provider waits have no open DB transaction; recheck the existing claim before saving a returned ID. Serialize concurrent creates through the existing durable run claim and operation identity; once a create may have been sent, an uncertain owner/acknowledgement blocks automatic recreation rather than assuming an expired claim proves absence. Do not introduce a provider-reconciliation ledger.

- [ ] Write tests: compatible requests reuse one ID; changed source/data/parameters do not change the requirement key; changed image/profile/resources do. Expired/404 handles create on demand; unreachable/ambiguous handles do not. Different roots/projects never share a sandbox, and duplicate delivery cannot race a second create under the existing run claim.
- [ ] Run the unit module red, then implement the single handle row and on-demand selection. A replaced or lost-claim sandbox is left to its original provider TTL. Add no deletion/capacity/orphan tables or maintenance loop.
- [ ] Add a real PostgreSQL upgrade/scoping test and independent-session claim/selection test. Check fresh `main` data upgrades; do not reuse or downgrade the abandoned branch's 021–025 database/volumes.
- [ ] Run `uv run pytest -q tests/sandboxes/test_sessions.py` and `uv run pytest -q -m postgres tests/integration/test_workflow_sandbox_postgres.py` with a disposable configured database. Missing infrastructure is reported, not counted as passing.
- [ ] Commit the verified reuse task with a scanned message file.

### Task 3: Wire preparation, execution and retained data outputs end to end

**Files:** Create `apps/api/src/openship/sandboxes/{tool,outputs}.py` and `apps/api/src/openship/runs/analysis_graph.py`; extend existing `artifacts/service.py`, `capabilities/{contracts,catalog,definitions}.py`, `execution/{gateway,publication,availability}.py`, `runs/{service,dispatch,worker}.py` only at their integration seams. Test `apps/api/tests/sandboxes/test_tool.py` and `apps/api/tests/integration/test_analysis_results.py`.

**Interfaces:** Consume Tasks 1, 1A and 2. Produce `prepare_execution(db, *, claim: Claim, request: ExecutionRequest, store) -> PreparedFiles`, `validate_outputs(request: ExecutionRequest, result: ExecutionResult) -> mapping[str, bytes]`, and async `execute_analysis(context, request: ExecutionRequest, control: InvocationControl) -> PublishedToolResult`. The gateway owns its usual operation/attempt, input pins, active slot and publication transaction. The fixed graph prepares source/inputs, executes and uses accepted outputs.

- [ ] Write tests using real artifact records/store: selected current bytes and generated JSON/log files reach a new operation-specific directory, earlier sandbox files cannot replace them, wrong-scope/deleted refs fail, and saved reports survive provider disappearance. Assert nonzero/missing completion, invalid/oversized/undeclared files and cancellation/lost fence cannot publish success. Assert a committed result is returned after duplicate delivery without another provider command.
- [ ] Run the tool tests red. Implement source/input capture through existing artifact services (minimal generated source/report kinds if required), call Task 1A's transfer/execution/collection client, and add text/JSON data validation. Do not duplicate provider I/O. Keep generated source inert. Publish all required output refs/result/events through existing fenced helpers; never wait for sandbox deletion.
- [ ] Register the exact capability and supported graph/configuration version through the actual production dispatch table, availability and worker path. Preserve old decoders and policy semantics; expose execution only through explicit authorized task/run commands, not an invented bypass endpoint or broad model permission. Source generation uses the existing pinned model boundary and captures its bytes before dispatch.
- [ ] Run the focused tool/database tests green, including an API→real worker→gateway smoke fixture with provider transport stubbed only at its HTTP boundary. Verify the production handler executes rather than remaining a fail-closed placeholder. A full real provider demonstration remains Task 6.
- [ ] Commit the working data-output path with a scanned message file.

### Task 4: Judge expected source outputs before workflow use

**Files:** Extend `apps/api/src/openship/sandboxes/outputs.py`, `tool.py` and the analysis graph's result step; test `apps/api/tests/sandboxes/test_output_review.py`.

**Interfaces:** Produce async `review_source_output(*, output: bytes, spec: OutputSpec, model_context, control: InvocationControl) -> ReviewDecision`, using the existing pinned provider runner and budgets. Task 3 calls it before publishing a source output as accepted. Persist its decision/digest in the existing result/state, not another approval service.

- [ ] Write tests for expected code accepted/rejected against a trusted expectation, unexpected source, a response trying to instruct the judge, unavailable/malformed review and content exceeding the review budget. Verify no partial/truncated text receives approval and returned code never runs/imports in OpenShip. Existing text/JSON previews remain escaped and binary/archive outputs fail.
- [ ] Run the module red; implement bounded expectation-only review with trusted instructions separate from returned text. Whole content must fit review limits; otherwise fail visibly. A review failure does not repeat sandbox execution or grant authority.
- [ ] Rerun output-review and tool tests green. Check the exact reviewed SHA-256 equals the stored accepted source; a passing decision is not labeled a security scan or safety certificate.
- [ ] Commit with a scanned message file.

### Task 5: Explicit A/B resume and minimal run UI

**Files:** Create `apps/api/src/openship/runs/resume.py`, extend `analysis_graph.py`, `runs/{routes,schemas,service}.py` and only needed additive persistence; create `apps/web/src/components/chat/RunResume.vue`, extend existing run card, `services/api.ts` and `types/index.ts`. Test `apps/api/tests/sandboxes/test_analysis_resume.py`, `apps/api/tests/integration/test_analysis_resume.py`, and `apps/web/tests/RunResume.test.ts`.

**Interfaces:** Produce `ResumeAnalysisRequest(mode: last_step|from_beginning, client_request_id: UUID)` and `resume_analysis(db, *, principal_id: UUID, run_id: UUID, command: ResumeAnalysisRequest) -> Run`, exposed as authenticated `POST /api/runs/{run_id}/resume-analysis`. Consume `AnalysisState` and Task 2's root-scoped reuse; use existing run submission/outbox rather than a second scheduler.

- [ ] Write tests: A resumes `use_outputs` with accepted report refs and **zero** provider calls; A resuming `execute` uses prior step refs and compatible live handle or on-demand replacement after expiry. B starts a new root from initial refs, reruns preparation/execution and leaves old results unchanged. Missing/pruned inputs fail without earlier-step recomputation. Double-click/replayed resume returns one new run; restart after dispatch marks interruption instead of resending code; lost acknowledgement after commit reuses the result.
- [ ] Run those backend tests red; implement the fixed journey's checkpoint/resume command. Keep initial refs/output refs in ordinary state. An uncertain still-running command yields an actionable blocked/interrupted state until provider availability is definite; never use a snapshot or hidden input-byte replay store.
- [ ] Write/run frontend tests red, then add only the two named resume choices and retained result/error links in existing run UI. Bind to the exact run/request identity; disable duplicate submissions and keep unrelated chat usable. No source editor or bundle inspector.
- [ ] Run backend/database cases, `pnpm typecheck` and `pnpm exec vitest run tests/RunResume.test.ts` green. Verify restart alone starts no sandbox and cancellation rejects late output without deleting the provider sandbox.
- [ ] Commit with a scanned message file.

### Task 6: One real parser demonstration and existing regressions

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
