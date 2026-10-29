# Phase 6B OpenSandbox Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to execute this plan task by task. Follow repository `git-workflow` instructions for commits and PRs. Checkboxes record verified deliverables; implementation and deployment qualification are separate gates.

**Goal:** Upload deployment logs, generate and inspect a captured Python parser, explicitly run that exact version against selected inputs, and retain its summary/report after sandbox destruction and application restart.

**Architecture:** OpenShip retains the existing run, admission, policy, fencing, operation ledger, artifact store and JetStream/SSE delivery. Standalone OpenSandbox Server owns on-demand sandbox lifecycle through its Docker backend with gVisor `runsc`; the trusted worker uses authenticated lifecycle and execd proxy APIs. Only manager infrastructure receives the engine socket; generated code receives no application credentials.

**Tech stack:** Python 3.11+, SQLAlchemy/Alembic, PostgreSQL, NATS JetStream, LangGraph, httpx, Vue/TypeScript, OpenSandbox, gVisor and Compose with rootless Podman or Docker.

**Spec:** [Phase 6B design](../specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md).

**Updated:** 2026-10-28, following the implementation audit of `4194af1` (`feat: add Phase 6B OpenSandbox foundation`). Continue in the existing `task/phase6b-opensandbox` worktree/branch. This plan replaces the original five broad slices with the remaining implementation and qualification tasks.

## Current progress and evidence

| Existing deliverable | Status and remaining boundary |
| --- | --- |
| `scripts/check_opensandbox_config.py` | Static checks implemented; no engine inspection, effective isolation proof or deployment enablement. |
| `docker-compose.sandbox.yml`, `deploy/sandbox/` and `scripts/openship-stack --sandbox` | Optional dev packaging implemented. Compatible server/execd/Python digests, protected supervision, storage/network enforcement, worker settings and production/native qualification remain. |
| Migration `phase6b021`, `artifacts/logical_files.py` and `file_routes.py` | Captures from existing text artifacts, bounded UTF-8 replacements, expected-base edits, listing/history, tombstoning and pruning implemented. Concurrent PostgreSQL behavior, execution pins, publication conflicts and direct create/write capability remain. |
| `sandboxes/client.py` and `executor.py` | Bounded source upload/readback, command streams, single-dispatch coordination and known-ID deletion implemented with mocked protocol tests. No production gateway caller, durable dispatch record, readiness inspection, input transfer, remote output capture or orphan reconciler. |
| `sandboxes/collector.py` | Local regular-file/link/size validation implemented. It is not integrated with remote execd collection or artifact publication. |
| `LogicalFiles.vue` | Current-source inspection and editing implemented. Run is statically disabled; history/pruning controls, bundle inspection and execution/results remain. |

- [x] Baseline foundation committed and pushed as `4194af1`.
- [x] Audit reran the six focused API modules: **39 passed**; `LogicalFiles.test.ts`: **2 passed**.
- [ ] Real OpenSandbox/runsc qualification, Phase 6B PostgreSQL concurrency and the full parser browser journey.
- [ ] PR checks and acceptance evidence. At audit time this branch has no open PR and no GitHub workflow runs; existing CI starts on PRs to `main` or pushes to `main`.

Prior full-suite results are historical evidence only. Re-run affected checks for each implementation change and the complete matrix for the final candidate. Installing gVisor and passing mock tests do not qualify the runtime.

### Rootless Podman configuration follow-up (2026-10-29)

- [x] Trusted CLI fixture succeeds with `podman run --runtime=runsc --runtime-flag=ignore-cgroups`.
- [x] Test the Docker-compatible API rather than inferring runtime selection from the CLI. On Podman 6.1.1, `HostConfig.Runtime = runsc` on the ordinary socket silently creates a `runc` container; native `OCIRuntime` inspection proves the mismatch.
- [x] Add a dedicated rootless API service/socket with service-scoped `--runtime=runsc`, preserving the normal `runc` default. A disposable Docker API fixture through the separate service executes and native inspection reports `runsc`; deletion is verified.
- [x] Add the rootless Compose overlay, automatic selection for Podman plus `--sandbox`, the runtime-specific flags example and operator setup. Packaging/preflight checks: **12 passed**.
- [x] Verify the installed socket-activated service and actual `runsc` execution/removal. Render with podman-compose 1.6.0 and confirm exactly one socket mount. Upstream OpenSandbox 1.2.0 passes configuration loading, startup, health, authenticated listing and unauthenticated rejection against the dedicated rootless API.
- [ ] Pin the upstream image tuple and exercise complete OpenSandbox-created sandbox lifecycle, transfers, command execution and collection. The startup fixture does not pull or execute the placeholder execd image.
- [ ] The [execd `v1.1.0` API schema](https://github.com/opensandbox-group/OpenSandbox/blob/docker/execd/v1.1.0/specs/execd-api.yaml) lacks the native `argv` command field required by this plan; the [current main schema](https://github.com/opensandbox-group/OpenSandbox/blob/main/specs/execd-api.yaml) includes it. Select and verify a compatible released image or build a pinned upstream revision before pinning the tuple; do not substitute a shell command. A lifecycle `Running` state and execd `/ping` show availability, not workload isolation or full readiness qualification.
- [ ] Qualify effective CPU/memory/task enforcement with `ignore-cgroups`, network denial, storage and deadlines. Startup success does not satisfy Task 2 or enable execution.

Ruling: rootless Podman is the requested development target, using a separate API service profile because per-container Docker API runtime selection is ignored on the installed version. Keep the same application/manager boundary and all isolation gates. The dedicated service shares the user's container storage; it is not a separate engine authorization boundary.

Follow-up: `scripts/bootstrap/bootstrap.sh arch-podman` now manages the
host dependencies, drop-in and dedicated socket. The standard `podman.socket`
is unnecessary for this setup and remains unmanaged; verify the ordinary
`runc` default through the local CLI. See the
[Ansible bootstrap guide](../../../scripts/bootstrap/README.md).

## Global constraints

- First workload profile: **CPython 3.11, standard library only**; record the exact patch and an explicit empty third-party dependency lock. No runtime installs/builds, arbitrary images, shell entrypoints, cloud credentials or external workload network access.
- Execution starts only through explicit **Run this version** using `POST /api/runs/{conversation_id}/tools`; the model may write source but cannot execute it.
- Resolve immutable source/input refs and recompute the bundle server-side. Preserve historical manifest/config/graph meanings and all existing ownership, archive, policy, idempotency and fence checks.
- One sandbox attempt per installation, also subject to existing tool/run capacity. No new scheduler or unbounded queue; uncertain create/command POSTs are never retried.
- Fixed Python argv: `/usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin /usr/local/bin/python3 -I -B /code/main.py`; non-root workload, inaccessible control tokens and protected execd/supervision.
- Ceilings: **1 CPU, 512 MiB**, **30 s** program deadline, **120 s** absolute sandbox lifetime; **2 s** graceful stop plus **3 s** forced-stop allowance. Deadlines must remain effective during lifecycle-server failure.
- At most **32 workload tasks**, **128 FDs**; source **256 KiB**; **8** selected inputs, **8 MiB each / 16 MiB total**; `/tmp` **64 MiB**, `/outputs` **16 MiB / 128 entries**. Bound every other writable root, engine log and manager staging/metadata storage.
- At most **8** declared outputs, **4 MiB each / 8 MiB total**, plus diagnostic stdout/stderr **256 KiB combined**. Reserve byte/count quota before physical dispatch; terminate on excess and retain only bounded diagnostics on failure.
- Read-only root/source/inputs. Deny workload DNS, IPv4/IPv6, host, metadata, control-plane and peer traffic while permitting manager-initiated execd traffic/replies. An internal Docker bridge is insufficient; do not combine gVisor with the incompatible egress sidecar.
- Stop workload descendants, capture outputs through the qualified remote interface, atomically publish, then destroy and verify removal. Pending cleanup blocks new sandbox work without disabling ordinary chat/history.
- Target development is Linux x86_64 with rootless Podman/runsc through a dedicated API service; retain Docker/runsc packaging. Qualify each engine profile independently, including native worker connectivity and dev/prod Compose. Kubernetes, other platforms/runtimes/dependencies and automatic execution remain outside this delivery.
- Keep execution disabled until runtime, recovery, applicable Phase 6A qualification and user acceptance prerequisites are satisfied. Missing native controls require a demonstrated protected profile/guard or recorded upstream addition; weakening a spec gate requires a spec review.

## Review focus

1. Ambiguous create/command replies and duplicate NATS delivery must never execute a second program (Tasks 7, 9).
2. A source edit, policy revoke, archive, cancellation or fence loss between inspection and publication must not execute substituted bytes or publish late results (Tasks 5, 6, 8).
3. An execd file download is not automatically proof of a safe regular file; links, descendants writing during capture and size/inode floods must be rejected (Tasks 2, 8, 12).
4. An edit rejected for stale base or quota must not leave unbounded orphan captures or miscount quota; retries must return the original successful capture (Tasks 3, 5).
5. Manager downtime, restored metadata and unknown/multiple orphan matches must keep physical lifetime bounded and admission closed until cleanup is verified (Tasks 2, 9, 13).

## File responsibilities and shared interfaces

Deployment, script, test and UI paths are relative to the repository root. Python module paths such as `sandboxes/`, `artifacts/`, `workspace/`, `execution/`, `capabilities/` and `runs/` are relative to `apps/api/src/openship/`. New filenames are planned implementation targets, not claims that the modules already exist. Keep feature logic in these focused modules; the existing large gateway/worker should delegate to them.

| Boundary | Files and responsibility |
| --- | --- |
| Qualified deployment | `deploy/sandbox/profile.json` (non-secret pinned compatibility/limits manifest), `deploy/sandbox/Dockerfile.python`, protected supervisor/host enforcement under `deploy/sandbox/`, Compose overlay and operator guide. |
| Host check | Extend `scripts/check_opensandbox_config.py`; create `scripts/check_opensandbox_runtime.py --profile <profile.json> --report <report.json>` for host-side engine/image/limit probes. Engine access stays outside API/worker. |
| Application contracts | New `sandboxes/contracts.py` defines `SandboxProfile`, `ExecutionBundle`, `SandboxExecutionResult`, `CapturedOutput`; `capabilities/contracts.py` adds explicit additive versions and decoders. |
| Durable sandbox details | New `sandboxes/models.py`, `admission.py`, `publication.py`, `recovery.py`, `readiness.py`, `routes.py`; details attach to existing `ToolOperation`/`ToolAttempt`, never duplicate their logical status. The route provides authenticated bundle inspection without execution. |
| Execution transport | Extend `sandboxes/client.py`, `executor.py`, `collector.py`; remote validation, fixed commands, transfer and lifecycle only, without engine access. |
| Source capture | Extend `artifacts/logical_files.py`, `file_routes.py`, `service.py`; new `workspace/write.py` exposes the same capture service through the maintained tool gateway. |
| UI | Extend `LogicalFiles.vue`, `services/api.ts`, `types/index.ts`; new `SandboxExecution.vue` owns bundle inspection/submission and uses existing run/result cards. |
| Qualification | New `apps/api/tests/sandboxes/`, `apps/api/scripts/qualify_sandbox.py`, PostgreSQL modules and parser full-stack fixtures/browser spec; existing workflows retain all their gates. |

Shared contracts to implement before consumers:

- `SandboxProfile`: versioned immutable server/execd/workload image pins, API compatibility, Python/dependency-lock identity, effective limits and enforcement identity; canonical digest excludes secrets and host-specific credentials.
- `ExecutionBundle`: schema version, capability ref, profile snapshot/digest, source `ArtifactRef`, optional file/expected-head refs, aliased input refs, bounded JSON parameters, declared outputs/media/mutability/expected bases and effective limits. Keep its content digest separate from authority/request identity.
- `CapturedOutput`: declared alias/media, verified size/SHA-256 and trusted staging identity. `SandboxExecutionResult`: validated exit disposition, bounded diagnostics and captured outputs; no arbitrary host path or upstream token in public projections.
- `build_execution_bundle(db, *, principal_id, conversation_id, arguments, profile) -> ExecutionBundle`: resolves authorized immutable refs and current heads; does not commit. Task 6 calls it during run submission and operation admission/revalidation.
- `execute_python(client, *, attempt_id, bundle, materializer, recorder, control) -> SandboxExecutionResult`: replace the source-only signature after Tasks 4, 6–7. `materializer` supplies verified authorized byte streams and stages bounded outputs; `recorder` commits create intent, bound sandbox ID and command intent under the live claim. Neither object enters graph checkpoints.
- `publish_sandbox_result(db, *, claim, operation_id, attempt_id, result) -> PublishedToolResult`: one caller-owned fenced transaction for all outputs, provenance, optional heads, outcome and outbox events.
- `reconcile_sandbox_attempts(session_factory, client, *, installation_id) -> ReconciliationReport`: bounded startup/periodic recovery returning active/pending/removed/unknown counts; it cannot create sandboxes or resend commands. Define `ReconciliationReport` in `sandboxes/contracts.py`.

Define concrete frozen models/protocols for the interface arguments in their owning task. Use existing `ArtifactRef`, `StagedArtifact`, `Claim`, `InvocationControl`, `PublishedToolResult` and canonical digest helpers rather than parallel substitutes.

## Execution order and checkpoints

`1 → 2` establishes the runtime contract. Tasks `1 + 3 → 4 → 5` complete versioned contracts and source writing. Tasks `2 + 4 + 5 → 6 → 7 → 8 → 9` deliver the backend parser path. Then `10 → 11 → 12 → 13` complete UI, CI, qualification and operation. Contract work and file qualification can proceed while host setup is being resolved, but a failed isolation check never authorizes execution.

For every task: write the named behavioral tests, run them and observe the intended failure, implement the smallest complete change, rerun the focused checks, review the diff, then commit the verified task through the repository's scanned `/tmp` message-file workflow. Do not bypass hooks or mark unrun/skipped real-service checks complete. Every task includes the global constraints and relevant historical regression cases.

### Task 1: Pin and exercise the upstream compatibility tuple

**Depends on:** Existing foundation. **Deliverable:** Reproducible authenticated create/ready/transfer/command/collect/delete compatibility fixture, with execution unavailable to users.

**Files:** `deploy/sandbox/profile.json`, `deploy/sandbox/Dockerfile.python`, `deploy/sandbox/config.example.toml`, `apps/api/src/openship/sandboxes/client.py`, `apps/api/tests/test_opensandbox_client.py`, new `apps/api/tests/sandboxes/test_protocol.py`, `apps/api/pyproject.toml`, ordinary unit CI/hook selections, `docs/operations/opensandbox.md`.

- [ ] Inspect official release documentation/API schemas and pin server, execd, Python base/workload digest, Python patch, runsc/engine/kernel tuple and bootstrap/proxy behavior. Retain the existing httpx adapter if adequate; pin SDK/lockfile only if introduced. Record sources/revisions and required upstream additions.
- [ ] Test `test_create_waits_for_ready_before_transfer`, `test_proxy_requires_authentication`, `test_unexpected_endpoint_is_refused`, `test_fixed_argv_uses_nonroot_clean_environment` and `test_command_requires_explicit_zero_exit_completion` against fixtures and the selected real server. Response loss/redirects must not trigger POST retries.
- [ ] Add `inspect`, bounded `wait_ready`, scoped `list`, endpoint resolution and removal inspection to `OpenSandboxClient`. Keep exact upstream wire fields in the adapter. Normalize `/input` and `/output` image directories to the specified `/inputs` and `/outputs` before input/output integration.
- [ ] Prove root/bootstrap and workload identities are compatible with protected execd/control files and read-only source/input paths; demonstrate a small fixed test program without enabling the application capability.
- [ ] Register the `sandbox` pytest marker and exclude it from ordinary unit shards/hooks in this task, as soon as real-service tests are introduced. Task 11 completes the strict launcher and dedicated workflow; ordinary tests must remain service-independent throughout implementation.
- [ ] Record actual commands, image provenance/inventory/SBOM/scan results, expected responses and compatibility failures in `docs/operations/opensandbox.md` and a new `docs/reviews/2026-10-28-phase6b-qualification.md`. Do not label this protocol probe as isolation qualification.

**Verify:** Existing client/packaging/preflight tests plus `uv run pytest -q -m sandbox tests/sandboxes/test_protocol.py` in `apps/api`. The real-service run must execute cases without skips.

### Task 2: Enforce and qualify the host profile

**Depends on:** Task 1. **Deliverable:** A support tuple that demonstrably meets the isolation/deadline/storage requirements, or an explicit blocking result with execution still disabled.

**Files:** `deploy/sandbox/`, `docker-compose.sandbox.yml`, both preflight scripts, new `apps/api/tests/sandboxes/test_isolation.py` and `test_limits.py`, operations/evidence documents.

- [ ] Implement explicit host/network provisioning and protected deadline supervision compatible with the pinned upstream backend. Keep any engine-owning guard alongside manager infrastructure and outside generated code. Do not change the host default runtime or restart its engine implicitly.
- [ ] Enforce read-only root/source/inputs, all writable byte/inode limits, bounded engine logs/metadata/staging, task/FD/swap ceilings and CPU/memory reservations. If upstream lacks a control, record and implement the smallest qualified profile/upstream addition; stop this gate if the control cannot be achieved.
- [ ] Add `test_workload_cannot_reach_host_peer_dns_or_metadata`, `test_workload_cannot_read_or_invoke_execd_control`, `test_missing_runsc_has_no_fallback`, `test_all_writable_roots_are_bounded`, `test_guest_task_and_fd_limits` and `test_deadlines_hold_while_manager_is_down`. Include IPv4/IPv6, secret canaries, privilege/devices/socket probes, fork/thread/output floods and surviving workload descendants.
- [ ] Implement `check_opensandbox_runtime.py --profile ... --report ...` as a read-only host inspection command. Inspect installed engine/images/enforcement and a controlled fixture provisioned by the qualification harness; missing fixture/control is nonzero. The sandbox tests, not this inspection command, create probe workloads. Bind evidence to the exact tuple/configuration and record when it becomes stale; a static config success cannot satisfy it.
- [ ] Demonstrate manager proxy reachability while workload egress is denied. Inspect actual `runsc` selection and effective limits for created sandboxes; verify program stop within 30 s plus the declared stop allowance and the 120 s absolute lifetime during lifecycle-server failure.

**Verify:** Both preflights and the real isolation/limit modules; save pass/fail measurements, enforcement configuration and cleanup evidence. **Checkpoint A:** No application execution until this gate passes; host-control changes receive security review.

### Task 3: Qualify logical files on PostgreSQL

**Depends on:** Existing migration/capture implementation. **Deliverable:** Proven concurrency, idempotency and lifecycle behavior for current file APIs.

**Files:** `artifacts/logical_files.py`, `file_routes.py`, `models.py`, `service.py`, `workspace/maintenance.py`; new `apps/api/tests/integration/test_logical_files_postgres.py`, `test_phase6b_migration.py`; existing logical-file tests. Source paths in this and later tasks are under `apps/api/src/openship/` unless a full path is given.

- [ ] Add `test_same_base_concurrent_edits_have_one_winner`, `test_edit_retry_returns_original_capture_after_head_moves`, `test_conflicting_retry_is_409`, `test_prune_checks_every_head` and `test_prune_racing_capture_cannot_delete_new_head` using independent real database sessions/barriers.
- [ ] Validate cross-project/conversation/base/head consistency in database constraints and service checks; reject wrong owner/archive writes, missing/deleted captures and invalid references. Preserve tombstone IDs/digests and quota accounting across verified deletion and operator purge.
- [ ] Fix failed replacement handling so CAS/quota/body/commit failures leave no unbounded ready artifacts, stuck reservations or quota leaks. Test deterministic recovery after failure between upload/capture/head commit; return actionable conflict data with the current version ID.
- [ ] Test upgrade from `phase6a020`, migration through `phase6b021` with existing artifact data, schema/model parity and permitted downgrade/maintenance behavior. Use an additive follow-up migration for changes to committed schema; do not rewrite deployed migration history.

**Verify:** `uv run pytest -q tests/artifacts/test_logical_files.py` and `uv run pytest -q -m postgres tests/integration/test_logical_files_postgres.py tests/integration/test_phase6b_migration.py` with `TEST_POSTGRES_URL`. No skipped qualification cases.

### Task 4: Add bundle contracts and durable attempt metadata

**Depends on:** Tasks 1 and 3; Task 2 qualification is required before any executable capability is enabled. **Deliverable:** Strict, canonical execution contracts and additive storage for provenance/recovery.

**Files:** New `sandboxes/contracts.py`, `models.py`; `capabilities/contracts.py`, `artifacts/models.py`, database model registration and the next unused Alembic revision; new `apps/api/tests/test_sandbox_contracts.py` and migration tests.

- [ ] Implement the shared frozen contracts above with explicit schema versions, strict unknown-field rejection, digest formats, unique safe aliases, bounded parameters and effective limits. Output declarations carry media/mutability/expected bases. Profile changes alter the bundle digest; installation/run/fence data belongs to a separate authority record.
- [ ] Define a new manifest version (v4 if unused) with explicit local-write effects for maintained source capture and the sandbox execution-kind variant. Add the next configuration version (v5 if unused), a new manual graph version (4 if unused) and chat graph version (5 for source writing if unused), with exact dispatch allowlists and typed safe projections. Pin the chosen version/effect constants in contract tests before consumers are registered. Old manifests/configurations/checkpoints must decode identically and cannot acquire write/sandbox permission through defaults.
- [ ] Add sandbox attempt detail keyed uniquely to `ToolAttempt`, holding installation/transport epoch, request/bundle/profile identities, immutable refs, create/command phases, sandbox/command IDs, deadline, exit reason and independent cleanup state. Keep one-attempt uniqueness and existing operation status authoritative.
- [ ] Extend artifact kinds/constraints for source/report/diagnostics and provenance without changing existing immutable byte semantics. Add scope-consistent foreign keys/indexes, active source/input/output pins and a durable installation sandbox-capacity reservation that cannot be released merely because its owner lease expires.
- [ ] Test `test_bundle_digest_changes_for_every_execution_input`, `test_bundle_rejects_duplicate_aliases_and_unknown_fields`, `test_legacy_config_does_not_enable_sandbox` and `test_attempt_detail_cannot_escape_operation_scope`; test additive upgrades on SQLite fixtures and real PostgreSQL.

**Verify:** Contract, migration and historical decoding tests. No new capability becomes executable from schema installation alone.

### Task 5: Let the agent create captured source through shared storage

**Depends on:** Tasks 3–4. **Deliverable:** A chat request produces a bounded source capture/file ref, with no code execution.

**Files:** New `workspace/write.py`; existing artifact file service/routes, `capabilities/contracts.py`, `catalog.py`, `definitions.py`, `policy.py`, `runs/agent_graph.py`, `execution/gateway.py`; new `apps/api/tests/workspace/test_write_file.py` and relevant capability/gateway tests.

- [ ] Extract a transaction-aware capture service callable from HTTP and maintained tool execution without constructing an internal HTTP upload request. Define `WorkspaceWriteArguments` (safe name, bounded UTF-8 text, optional file/expected base) and `WorkspaceWriteResult` (file/version/artifact refs); bind retries to the gateway operation identity or HTTP request key.
- [ ] Add direct `POST /api/conversations/{conversation_id}/files` with bounded raw text upload plus existing digest/idempotency conventions; retain the implemented `/from-artifact` and captured-version APIs. Route creates/edits through the same quota/reservation/lifecycle service.
- [ ] Register maintained `workspace_write_file` using Task 4's additive manifest/local-write effect and chat graph/configuration versions. Update policy/projections/dispatch consumers while preserving v1–v3 manifest interpretation; existing read-only manifests must not acquire write authority.
- [ ] Test `test_agent_write_captures_immutable_source`, `test_write_retry_does_not_duplicate_file`, `test_stale_base_does_not_publish_source`, `test_oversize_utf8_is_bounded` and `test_workspace_write_obeys_policy_and_archive`. The model receives refs/bounded excerpts; it never receives storage paths or execution authority.

**Verify:** Focused writer/capability/gateway tests plus PostgreSQL writer races. **Checkpoint B:** Chat can generate a parser, the existing editor can inspect/edit it, and no sandbox starts.

### Task 6: Admit an exact reviewed bundle through the existing run path

**Depends on:** Tasks 2, 4–5. **Deliverable:** Authorized manual commands pin exact source/inputs/profile and reserve capacity/quota before dispatch.

**Files:** New `sandboxes/admission.py`, `readiness.py`, `routes.py`; `main.py`, `execution/admission.py`, `availability.py`, `capabilities/catalog.py`, `definitions.py`, `runs/service.py`, `routes.py`, `tool_graph.py`, `config.py`; new `apps/api/tests/integration/test_sandbox_admission_postgres.py` and unit counterparts.

- [ ] Implement `build_execution_bundle` and `PythonExecuteArguments`; accept immutable refs/expected heads, selected aliased inputs, parameters, output declarations, requested lower limits and an inspected bundle digest. Derive principal/project/run authority server-side. Server recomputation must match the inspected digest.
- [ ] Add authenticated `POST /api/conversations/{conversation_id}/sandbox-bundles/inspect` using `PythonExecuteArguments` without its inspected digest. Return the authorized resolved bundle, canonical digest, safe profile/limits and current readiness; this request creates no run, pin, quota reservation or execution authority. Recompute everything on manual command submission and reject changes with `execution_bundle_changed` or stale-head 409. Document this additive inspection contract alongside the spec's existing tool command.
- [ ] Register manual-only `python_execute` through existing `ToolCommandRequest` and the versioned model-free tool graph. Explicitly exclude it from model tool exposure even if policy enables it. Do not use Connector selection or credential resolution for this capability.
- [ ] Add worker-only manager URL/key-file/profile configuration, default-disabled execution and independent readiness. API catalog reads only safe worker availability/profile projections; worker/manager secrets never enter model context, events, checkpoint or API environment.
- [ ] Under existing lock order, pin ready source/inputs and the canonical captured parameter payload, and reserve all output/log byte/count budgets plus the installation sandbox slot before any network dispatch. Recheck live claim, current policy/archive, messaging gates and readiness before create/command/publication. No database transaction spans HTTP waits.
- [ ] Test wrong-owner/scope/deleted/unselected refs, stale source heads, changed image/dependency/limits, revoked policy, unsupported hosts and ten concurrent submissions under capacity one. Capacity rejection/waiting is bounded; chat remains usable. Add `test_prune_and_purge_refuse_active_sandbox_pins` and verify edit-after-admission preserves the admitted bytes.

**Verify:** Focused admission tests and real PostgreSQL lock/quota/pin races. **Checkpoint C:** Tampered/unavailable bundles never create a sandbox; replayed client requests return the same run/operation.

### Task 7: Execute and transfer once under durable fencing

**Depends on:** Task 6 and the qualified Task 2 profile. **Deliverable:** The gateway can prepare and execute one admitted attempt without replay on uncertainty.

**Files:** `sandboxes/client.py`, `executor.py`, new `sandboxes/materialization.py`; delegation points in `execution/gateway.py`, `runs/worker.py`; `apps/api/tests/test_sandbox_executor.py`, `test_opensandbox_client.py`, new PostgreSQL dispatch tests.

- [ ] Define typed materializer/recorder protocols and implement the planned `execute_python` interface. Persist create intent before POST; bind a ready sandbox ID to the exact live attempt before source transfer; persist command intent before the one command POST.
- [ ] Stream only verified source, fixed read-only parameter file and selected input bytes into `/code/main.py`, `/inputs` and fixed profile paths. Verify materialized length/digest, then execute the fixed non-root argv with the qualified workload environment and deadlines.
- [ ] Bound stdout/stderr while consuming events; fail missing/invalid/nonzero completion and preserve stable safe failure reasons. Continue worker lease/cancellation supervision during preparation, transfer and execution.
- [ ] Add `test_create_reply_loss_records_uncertainty_without_retry`, `test_duplicate_delivery_does_not_repeat_command`, `test_lost_fence_between_ready_and_command_prevents_dispatch`, `test_input_digest_mismatch_prevents_command` and cancellation tests at every await boundary. Persist uncertain status for Task 9; do not silently retry or free physical capacity.
- [ ] Change the current executor's destroy-in-finally sequence so successful execution enters protected capture/publication coordination before final destruction. Until Task 8 lands, incomplete output capture must fail closed rather than claim success or expose a partial report.

**Verify:** Client/executor tests, PostgreSQL dispatch tests and the real fixed-program protocol fixture. Review all HTTP transport/retry settings, including proxies/SDK defaults.

### Task 8: Capture and atomically publish reports

**Depends on:** Task 7. **Deliverable:** Summary/report bytes survive sandbox destruction with exact provenance and no partial successful publication.

**Files:** `sandboxes/collector.py`, new `sandboxes/publication.py`; `artifacts/service.py`, `filesystem.py`, `execution/publication.py`, `projections.py`; new `apps/api/tests/integration/test_sandbox_publication_postgres.py` and collector/crash tests.

- [ ] Stop all workload descendants while preserving execd, then collect declared remote outputs using the qualified file type/link/metadata/open semantics from Tasks 1–2. Do not treat local `collect_outputs(Path, ...)` as proof of remote-file safety or mount the whole artifact store into a sandbox.
- [ ] Stream bounded files to existing trusted staging; recheck digest/size, plain-text/JSON validity and alias/media declarations. Reject symlinks, hardlinks, directories, FIFOs/devices, traversal, races and undeclared/missing outputs; never extract an untrusted archive.
- [ ] Implement `publish_sandbox_result` under live claim/current policy: atomically publish every required artifact, provenance, optional mutable-head CAS, operation result and semantic outbox events. Any output/head conflict rolls back the entire success transaction; reserved log/output budgets remain recoverable.
- [ ] Test `test_reports_survive_sandbox_destruction_and_restart`, `test_missing_second_output_publishes_no_success`, `test_mutable_output_conflict_preserves_newer_head`, `test_late_result_cannot_publish`, `test_output_transfer_rejects_link_swap` and failures before/after staging, DB commit and acknowledgement.
- [ ] Preserve bounded authorized diagnostics on failure, discard reports/head changes, then destroy and verify backend removal. Diagnose physical cleanup separately from committed logical results; keep sandbox capacity/pins while cleanup is unresolved.

**Verify:** Collector/crash unit tests, real remote-output probes and PostgreSQL publication tests. **Checkpoint D:** The API parser path produces a durable summary/report; inspect/admit/execute/source digests match.

### Task 9: Recover orphaned attempts and gate unhealthy capacity

**Depends on:** Tasks 7–8. **Deliverable:** Worker/server restarts cannot replay code or hide pending cleanup.

**Files:** New `sandboxes/recovery.py`; `sandboxes/readiness.py`, `client.py`, `execution/recovery.py`, worker/maintenance lifecycle; new `apps/api/tests/integration/test_sandbox_recovery_postgres.py`, `apps/api/tests/sandboxes/test_recovery.py`.

- [ ] Implement bounded `reconcile_sandbox_attempts` at startup and periodically. Match installation/attempt metadata, including unknown/multiple matches; recover uncertain creates, inspect known instances and destroy abandoned work without another create/command.
- [ ] Reconcile DB terminal/dispatch/cleanup state, staged artifacts, reservations, pins and physical slots idempotently. A 204 deletion response alone cannot release physical capacity; require profile-specific stopped/removal evidence.
- [ ] Keep new sandbox admission closed on manager outage, stale/unqualified tuple, unresolved unknown instances or cleanup failure. Add safe readiness reasons and metrics for active attempts, pending cleanup/oldest orphan, queue/prepare/run/collection latency, terminations, reserved/staged bytes and proxy failures.
- [ ] Inject crashes before create, after create-before-ID commit, during command SSE, during capture/publication and after commit. Include lost DB/HTTP/NATS acknowledgements, cancellation/archive/revoke/fence races and manager downtime beyond TTL. Assert no second program, no late publication and no reservation/slot leak after verified cleanup.
- [ ] Demonstrate orphan reaping within **60 s of manager recovery**, while Task 2's independent physical deadlines bound execution during downtime. Restore does not resume Python or resend POSTs.

**Verify:** PostgreSQL recovery tests and real crash/restart tests with recorded stop/reap times. **Checkpoint E:** Recovery and cleanup gates pass before the UI can expose executable availability.

### Task 10: Complete the reviewed execution workspace

**Depends on:** Tasks 5–6, 8–9. **Deliverable:** A user can inspect, edit, explicitly execute and revisit exact captured versions through existing run/event UI.

**Files:** `apps/web/src/components/chat/LogicalFiles.vue`, new `SandboxExecution.vue`, existing operation/run/file cards as needed, `services/api.ts`, `types/index.ts`, `tests/LogicalFiles.test.ts`, new `tests/SandboxExecution.test.ts`.

- [ ] Add paginated source/version history, file tombstoning, explicit prune controls and deleted-byte/provenance states. Explain completed-run reproducibility loss before pruning; show active/head-pin conflicts and reloadable edit conflicts.
- [ ] Show source/version digest, selected immutable input refs, parameter/output declarations, qualified dependency/profile and effective limits. Clear pending Run inspection when any of these or the source editor changes; do not execute unsaved or stale source implicitly.
- [ ] Fetch the canonical inspected bundle through Task 6's inspection endpoint and submit its digest with the existing manual tool command/client identity. Server admission remains authoritative. Enable Run only from current policy/readiness; show an actionable safe reason otherwise.
- [ ] Use existing run cards/SSE replay/cancel commands for progress and result links. Test reconnect, cross-conversation switches, double clicks/request retries, cancel during transfer/run, unavailable runtime, safe escaped text/JSON and downloads after cleanup/restart.
- [ ] Add `test_run_invalidates_after_source_or_input_change`, `test_run_submits_inspected_version_once`, `test_version_history_handles_pruned_capture` and hostile filename/content/ANSI tests. Include keyboard/focus/error states and pagination beyond the first page.

**Verify:** Frontend typecheck, focused Vitest modules and browser tests against a qualified test profile; mocks cannot satisfy actual execution qualification.

### Task 11: Wire strict qualification entrypoints and CI

**Depends on:** Task 1 marker setup and backend/UI fixtures. **Deliverable:** Required checks execute the relevant tests and cannot turn green by skipping the sandbox lane.

**Files:** `apps/api/pyproject.toml`, new `apps/api/scripts/qualify_sandbox.py`, `.github/workflows/python-ci.yml`, new `.github/workflows/sandbox-ci.yml`, full-stack harness/config, local hook/runner exclusions where necessary.

- [ ] Verify Task 1's `sandbox` marker/exclusions remain correct across unit shards and hooks. Audit lane selections so any full-stack sandbox fixture cannot accidentally run in the ordinary full-stack job without its runtime.
- [ ] Implement `qualify_sandbox.py --profile <profile.json> --report <report.json>` in `apps/api`: validate configuration/readiness, run real marked cases, require nonzero test counts and **zero skips**, record JUnit/measured tuple/cleanup evidence and return nonzero for any missing service/control.
- [ ] Add a qualified Linux sandbox runner job using isolated disposable services/credentials. Do not expose a privileged persistent runner to untrusted fork code; use a protected dispatch/ephemeral runner arrangement and document how candidate SHA evidence is attached to the PR. Missing infrastructure is a missing qualification, never a passing substitute.
- [ ] Retain Python **3.11/3.12/3.13** API unit/PostgreSQL/JetStream gates, existing synthetic full-stack lanes, frontend typecheck/tests/build and secret scans. Add sandbox parser qualification to an explicit real-service lane and verify CI trigger coverage for deployment/scripts as well as API changes.
- [ ] Test launcher failures for absent runsc/server, wrong credentials, zero collection and any skipped case. Store bounded sanitized reports with exact candidate SHA/profile digests; report synthetic provider fixtures honestly.

**Verify:** Focused launcher/packaging tests and an actual candidate workflow run. Review workflow permissions/triggers and confirm the ordinary suite remains service-independent.

### Task 12: Demonstrate the complete parser journey and regressions

**Depends on:** Tasks 9–11. **Deliverable:** Reproducible end-to-end acceptance evidence for the milestone.

**Files:** New `apps/api/tests/full_stack/test_sandbox_parser.py`, parser fixture harness/sample logs/source, `apps/web/tests/full-stack/sandbox-parser.spec.ts`, `apps/api/tests/sandboxes/test_capacity.py`, qualification evidence document.

- [ ] Add a clearly labelled synthetic model fixture for deterministic source generation, then use real API, PostgreSQL, JetStream, artifact filesystem, browser and OpenSandbox/runsc for execution. If provider-live evidence is collected, record it separately with no fabricated traffic claims.
- [ ] Run two log datasets through generated/inspected parser versions producing `summary.json` and `report.txt`; assert exact expected bytes/refs after destruction and application restart. Edit and rerun while earlier provenance remains unchanged.
- [ ] Exercise code/input/profile tampering, concurrent edit/prune, wrong-owner/unselected reads, hostile previews, overflow/unsafe output, cancellation, reconnect, crash/restore and disabled runtime. Carry forward chat/search/Connector/AWS/artifact/SSE/archive journeys through 6A.
- [ ] Measure **20 sequential executions** and **10 concurrent submissions** under the one-sandbox ceiling: admission/queue/start/run/collection times, peak memory/storage, rejection/wait behavior and cleanup. Repeat infinite-loop/memory/task/FD/byte/inode/output exhaustion while unrelated chat remains responsive.
- [ ] Map each specification acceptance-table row to actual test IDs, commands, candidate SHA/profile tuple, results and limitations. Any failure or absent evidence keeps that gate unchecked.

**Verify:** Strict real sandbox launcher, PostgreSQL/JetStream lanes and real parser browser suite. **Checkpoint F:** Every acceptance-table row has passing evidence, with no mock-only isolation claims.

### Task 13: Finish deployment, rollback and acceptance handoff

**Depends on:** Task 12. **Deliverable:** Reproducible installation/operation and a reviewable completed Phase 6B candidate.

**Files:** `docker-compose.sandbox.yml`, dev/prod Compose and `scripts/openship-stack` as needed, `docs/operations/opensandbox.md`, links from artifact/messaging runbooks, roadmap/spec status and qualification evidence.

- [ ] Document exact host prerequisites, image build/preload, API-key provisioning/rotation, private proxy/TLS settings, firewall/storage setup, preflight, startup and shutdown commands. Demonstrate native worker plus standalone server and selected dev/prod Compose topologies; workers have no engine socket and the API has no manager key.
- [ ] Rehearse homogeneous worker drain/migrate/deploy with sandbox admission closed; old workers cannot claim new graph/config variants. Demonstrate storage-full handling, disabling execution, metadata/orphan inspection and cleanup before admission reopens.
- [ ] Rehearse coordinated DB/artifact backup/restore plus upstream metadata recovery. Reconcile old attempts before admitting work; rollback prefers the new binary with writes/execution disabled and retained additive metadata. Document incompatible older binaries and any maintenance restore requirement.
- [ ] Run final required checks on the exact candidate, review the complete diff for correctness/security/regressions and scan messages/staged files. Commit and push verified changes through the repository workflow; open/update a PR only when the candidate is complete and ready for review. Keep all required checks green without suppressions, skipped required lanes or weaker thresholds.
- [ ] Update roadmap/spec/plan with actual implementation and qualification state. Reconcile applicable 6A prerequisite evidence and present the runnable parser demonstration/results for user acceptance; record accept/revise/reject separately from implementation or CI status.

**Verify:** Installation, migration, rotation, disable/rollback and restore rehearsals plus the final checks below. **Checkpoint G:** Feature readiness, required PR checks, runtime qualification and user acceptance are individually recorded.

## Phase 6B Pull Requests and completion checklist

The existing foundation is pushed but has no PR. Keep the current feature branch while implementing related tasks; make reviewed task commits rather than publishing incomplete slices as ready features. If a split is necessary, each PR must contain a complete independently usable deliverable and name its remaining gates. No merge or production rollout is implied by this plan.

- [ ] Every Task 1–13 deliverable and checkpoint is verified, or a user-approved spec change explicitly revises the requirement.
- [ ] All applicable existing PR checks are green on the final head SHA.
- [ ] Dedicated real-sandbox check/evidence is passing for that same SHA and pinned runtime tuple; no required tests skipped.
- [ ] PostgreSQL concurrency/migrations/publication and JetStream duplicate/recovery checks pass.
- [ ] Browser parser demonstration, regressions, exhaustion, cancellation/restart and 20/10 measurements pass.
- [ ] Native/dev/prod deployment, credentials/TLS rotation, storage-full, disable/rollback and restore evidence is recorded.
- [ ] Qualified tuple, operator guide, provenance/limits, known limitations and applicable 6A prerequisite evidence are complete.
- [ ] User acceptance is recorded before claiming Phase 6B accepted or enabling it for normal use.

## Verification commands and evidence rules

Existing focused checks, runnable now:

```bash
# apps/api
uv run pytest -q tests/artifacts/test_logical_files.py tests/test_opensandbox_client.py tests/test_opensandbox_packaging.py tests/test_sandbox_collector.py tests/test_sandbox_executor.py tests/test_sandbox_preflight.py

# apps/web
pnpm exec vitest run tests/LogicalFiles.test.ts
```

Final regression commands (update ordinary-lane exclusions when Task 1 introduces `sandbox`; provide real service settings through protected operator configuration):

```bash
# apps/api
uv run ruff check .
uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream and not sandbox'
uv run pytest -q -m 'postgres and not jetstream and not sandbox' tests/integration
uv run python scripts/qualify_jetstream.py
uv run pytest -q -m 'full_stack and not sandbox' tests/full_stack/test_manual_search.py tests/full_stack/test_managed_connectors.py

# apps/web
pnpm typecheck
pnpm test:run
pnpm build
pnpm test:e2e:full-stack

# repository root
git diff --check
```

Planned commands, **not implemented or verified by this plan update**:

```bash
# repository root: host-side, read-only verification
python scripts/check_opensandbox_runtime.py --profile deploy/sandbox/profile.json --report /tmp/openship-sandbox-host-report.json

# apps/api: strict real runtime + parser journey; report paths are operator-selected
uv run python scripts/qualify_sandbox.py --profile ../../deploy/sandbox/profile.json --report /tmp/openship-sandbox-qualification.json
uv run pytest -q -m sandbox tests/sandboxes
uv run pytest -q -m 'full_stack and sandbox' tests/full_stack/test_sandbox_parser.py
```

Task 11 must make the strict launcher include both real sandbox tests and the parser full-stack fixture, with no skipped tests. Direct pytest commands aid diagnosis; their success alone cannot replace the full report. Run browser tests with the real-service harness and use the current `scripts/openship-stack -e dev --sandbox start` only after provisioning the required images/configuration/secrets. Record the exact final startup commands when Task 13 qualifies production/native packaging.

## Risks and next action

| Risk | Required response |
| --- | --- |
| Upstream main-based research does not match a compatible pinned release. | Task 1 establishes the tuple/API before application integration; record any upstream addition. |
| Docker backend lacks enforceable writable-layer/inode/control separation or independent lifetime. | Task 2 proves protected enforcement; keep admission disabled until all controls pass. |
| Current adapter destroys before report capture and treats deletion acceptance as cleanup. | Tasks 7–9 introduce staged capture, durable dispatch/cleanup and verified removal. |
| Source capture/upload spans commits and quota/CAS races. | Tasks 3 and 5 prove rollback/recovery, canonical service ownership and PostgreSQL locking. |
| Required host or protected CI runner is unavailable. | Continue independent contracts/file tests, record missing evidence and leave qualification unchecked; do not substitute mocks. |

**Next action:** Execute Task 1's pinned compatibility probe and Task 2's host-control qualification, retaining the current disabled execution state. Carry the result forward into bundle/dispatch implementation; do not redo the committed foundation.
