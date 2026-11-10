# Phase 7A Agentic Planning and Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow `git-workflow` for isolated work and verified, file-based commit messages.

**Goal:** Complete outcome-level requests through a durable, evidence-backed plan/execution/repair loop, with persistent Auto/Copilot modes and validated local sandbox-profile selection.

**Architecture:** Extend the existing run worker, LangGraph dispatch, gateway, operation ledger, artifacts and event/outbox path. Introduce chat graph v5 with typed internal abilities and authoritative application-side task/decision state; retain historical graphs. Generate one validated local profile snapshot through the existing build command, pin it at admission, and resolve runtime bindings server-side.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy/Alembic, Pydantic 2, LangGraph/PostgreSQL checkpoints, NATS JetStream, existing OpenSandbox client, Vue 3/Pinia/TypeScript, pytest/Vitest/Playwright, Docker/Podman Compose; add direct `PyYAML>=6.0.3,<7` for bounded YAML parsing.

**Spec:** [Phase 7A design](../specs/2026-11-08-phase7a-agentic-planning-and-execution-design.md). Read the entire spec before executing this plan.

**Status:** Implementation in progress; Tasks 8–17 revised for review after the approved crash-recovery clarification. Tasks 1–7 retain their recorded implementation/review evidence; Task 8's committed foundation is `5d2ec7b`, not a completed decision lifecycle. Subagent-driven execution remains selected; subsequent subagents must use GPT-6.1 Terra as requested. Resume at Task 8 after this revision is reviewed, without reimplementing completed tasks. P1–P8 remain the working limits. Never check off a live lane from fixture results.

## Global Constraints

- "Extend the delivered runtime; do not introduce another scheduler, queue, agent service, permission path, artifact store or operation ledger."
- "Copilot is the default." Persist `auto|copilot` server-side per account; capture mode per new task. Selecting Auto never consumes a pending confirmation.
- Plans live in durable conversation/run context, not mandatory files or saved reusable workflow definitions. One Copilot confirmation releases continuous execution; material revisions require renewed confirmation.
- Runtime-controlled `agent_reasoning`, `agent_planning`, `agent_tracking`, `agent_switch` are internal responsibilities, not four optional tools, nested agents or four mandatory inferences per step.
- P1: "At most 8 plan steps, 16 tool calls, 20 model turns, 24 total generation attempts, 4,000 output tokens/request and 48,000 total output tokens." Provider/operator limits remain ceilings. All inference, including planning/repair/verification, counts.
- P2: "At most 2 repair attempts after the initial attempt per logical step." Stop after 2 consecutive ineffective repairs with the same normalized failure and no new useful evidence, or earlier on a shared limit/control.
- P3: "Plan/input requests expire after 24 hours." Durable human waiting consumes no worker slot or cumulative active-time allowance; it never extends credentials or the provider's 30-minute sandbox lifetime.
- Four maintained IDs only: `logs-data`, `iac-validation`, `standard`, `devops-shell`. One `python_execute` tool with `profile_id`; expose only permitted, enabled, ready choices, without host/engine/image details.
- Tracked metadata comes from `deploy/sandbox/images.json`: `label`, `description`, `use_for`, `highlights`, `limitations`. Runtime reads the generated YAML snapshot, not the repository catalog.
- Generated schema version is `1`; preserve other profile entries, existing `enabled`, bounded `extensions`. `--overwrite-profiles` authorizes merging successful profiles, not deleting/rebuilding the whole map. No separate export command.
- Host resolution: process environment → root `.env` → `deploy/sandbox/sandbox-profiles.yaml`, relative to the repository root. Fixed read-only container target `/etc/openship/sandbox-profiles.yaml`; `create_host_path: false`.
- P7: only sandbox-disabled launcher invocations, with no explicit override and no generated default, may mount the tracked valid empty example. No unconsented generated-file creation, image build, hot reload or service recreation.
- Preserve gateway authority, exact source/input digests, existing output limits, offline credential-free execution, Python entrypoint, compatible sandbox reuse and provider-owned lifecycle. Uncertain dispatch is never ordinary repair.
- If an approval was not committed before a crash, re-present the same current request; never infer consent or restart completed work. Committed choices resume through the existing transaction/outbox, not a new exactly-once protocol.
- Uncertain execution requires evidence-based diagnosis followed by the user's explicit `abort|repeat_step|continue_next` choice in both modes. Repeat is a new bounded attempt; continue records a skip, not success, and requires valid dependencies. No overlapping prior command, policy bypass or budget reset.
- Phase 7A uses fixed versioned LangGraph nodes plus durable plan-local progress. Dynamic conversation-wide graph growth is deferred; neither checkpoints alone nor model memory establishes that an external operation completed.
- Preserve old graph/configuration decoders and recorded statuses. Legacy single-profile runs use their recorded path or block visibly on incompatibility; never reinterpret them through v5/YAML.
- No saved-workflow engine/editor, arbitrary custom profiles, new shell API, dependency installation by the agent, credentials/network in generated code, cloud mutations, repository mounts or lifecycle reconciler.
- Before execution: user reviews this plan and chooses execution method. Before real builds, stack recreation or paid/live calls: obtain authorization for that environment/lane. This document authorizes none of them.

## Review Focus

1. Two browsers save/select modes concurrently, or retry a submission after a preference change: never execute in a mode different from the submitted, validated selection. Tasks 7 and 13 pin this.
2. Crash before approval commit: re-prompt the same request and dispatch nothing. Crash after commit/lost response or acknowledgement: recover the recorded decision/outbox intent once, without regenerating the plan or replaying completed work. Tasks 8, 11 and 15 pin this.
3. A YAML update is only partially successful, races another writer, or leaves API/worker on different snapshots: preserve prior data and refuse incompatible dispatch, not silently choose a tag. Tasks 2, 3 and 5 pin this.
4. A process exits zero but the chart/data is absent, wrong, cross-conversation or contains prompt-injection text: no fabricated completion or broadened authority. Tasks 10 and 12 pin this.
5. A worker dies after possible dispatch: diagnose, then show abort/repeat/continue in both modes; no silent default, overlapping repeat, dependency fabrication or skipped-as-completed step. Safe resumption retains the root budget. Tasks 9–12 and 14–17 pin this.

---

## File Structure and Ownership

Paths below are relative to the repository. New files are marked **new**. Do not restructure the existing large worker/service modules beyond extracting the v5-specific responsibilities listed here.

| Files | Responsibility |
| --- | --- |
| `apps/api/src/openship/sandboxes/profile_schema.py`, `profile_codec.py` **new** | Strict snapshot/metadata models, bounded YAML codec; small stdin/stdout host-tooling bridge, with no database/settings initialization. |
| `apps/api/src/openship/sandboxes/profiles.py` **new** | Fixed-path runtime load, pinned/current compatibility and profile-to-`RuntimeRequirements` resolution. |
| `scripts/sandbox_image_lib/profiles.py` **new**, existing `catalog.py`, `config.py`, `builds.py`, `scripts/sandbox_images.py` | Purpose metadata projection, shared host-path resolver, safe partial YAML merge, build consent and host image preflight. |
| `deploy/sandbox/images.json`, `sandbox-profiles.example.yaml` **new**, `.gitignore` | Curated descriptions; empty tracked bootstrap example; ignored generated default. No machine-local image IDs in tracked files. |
| `scripts/openship-stack`, `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, `.env.prod.example` | Host-source selection and fixed-target mounts; rollout selectors; keep dev/rootless overrides compatible. |
| `apps/api/src/openship/runs/tasks/` **new**, modules `contracts.py`, `models.py`, `state.py`, `decisions.py`, `clock.py`, `inference.py`, `routing.py`, `verification.py`, `recovery.py`, `projections.py`, `routes.py`, `__init__.py` | One responsibility per file: public typed contracts, task persistence, decisions, clock, bounded inference, deterministic switching, verified evidence, explicit recovery and public API/projections. Not another runtime service. |
| `apps/api/src/openship/runs/task_graph.py` **new**, existing `dispatch.py`, `worker.py`, `service.py`, `schemas.py`, `budget.py`, `transcript.py`, `queue.py`, `resume.py` | v5 graph wiring and small existing-runtime integration points. |
| `apps/api/src/openship/auth/preferences.py` **new**, existing auth models/schemas/routes | Own-account revisioned agent-mode preference. |
| `apps/api/alembic/versions/031_phase7a_task_state.py`, `032_phase7a_agent_mode.py`, `033_phase7a_preplan_decisions.py` **delivered**, `034_phase7a_recovery_decisions.py` **new** | Delivered task/preference/pre-plan schema; additive recovery decision constraints and skipped-step projection. Current head is `phase7a033`; Task 8 extends it, never edits applied migrations to add recovery. |
| `apps/api/src/openship/capabilities/{contracts,catalog,definitions}.py`, `execution/{admission,availability,gateway,projections}.py`, `sandboxes/{python_tool,tool}.py` | Versioned execution configuration, dynamic permitted profile descriptions and authoritative dispatch. Retain existing publication/output validators. |
| `apps/api/src/openship/messaging/{delivery,exhaustion,migration,recovery}.py`, `events/{schemas,service}.py`, `config.py`, `main.py` | Durable wait/requeue/recovery integration and startup registration. |
| `apps/web/src/stores/agentMode.ts` **new**, existing chat/auth stores, services/api and types/index | Preference lifecycle, exact submission/decision payloads, durable task projection reduction. |
| `apps/web/src/components/chat/AgentModeSelector.vue`, `TaskPlan.vue`, `TaskDecision.vue` **new**, existing ChatInput/RunCard/ChatStream, `views/ChatView.vue` | Accessible mode selection and evidence-linked plan/decision/progress views, without a workflow editor. |
| Tests named in each task | Contract and failure-path proof; no full-stack/live tests hidden in unit verification. |
| `docs/operations/opensandbox.md`, `docs/development.md`, `docs/provider-support.md`, `docs/validations.md`, `docs/roadmap.md`, this plan | Operator activation/rollback, honest qualification scope and task-local evidence. |

### Implementation decisions fixed by this plan

**Versioning and rollout.** Add `ExecutionConfigV5` (`schema_version=5`, `graph_kind="chat"`, `graph_version=5`) alongside v1–v4. Keep `kind="chat"`. `AGENTIC_TASKS_ENABLED` defaults false during rollout; `AGENTIC_TASK_PROFILE_IDS` is an explicit comma-separated provider-profile allowlist, initially empty. With the feature enabled, tool-enabled submissions require an allowlisted tool-capable profile and use v5; a nonqualified selection gets an actionable admission blocker, never a silent legacy/Auto fallback. Tool-disabled ordinary chat keeps its delivered behavior. v5 can also answer informational turns without a plan. Neither allowlisting nor tests changes the provider support matrix to accepted automatically.

**YAML dependency boundary.** Keep the host image builder stdlib-only. Its mapping actions call a small API-owned codec using the existing synced API interpreter (`apps/api/.venv/bin/python`, `cwd=apps/api`, `-m src.openship.sandboxes.profile_codec`); no host module imports application initialization. The codec accepts a bounded JSON envelope on stdin and returns JSON/base64-encoded YAML on stdout for `decode`/`encode` only. It cannot write files, execute images or read settings/secrets. Missing interpreter/dependency gives `uv sync --project apps/api` guidance; it never installs dependencies implicitly. Runtime and writer therefore validate through the same models/parser without a new shared-package framework.

**Snapshot schema.** `ProfileCatalog(schema_version=1, profiles: dict[ProfileId, ProfileEntry])`. `ProfileId` is the four-value literal. Each `ProfileEntry` has `enabled: bool`, `extensions: dict[str,str]`, `metadata: ProfileMetadata`, `metadata_digest: str`, `runtime: ProfileBinding`. `ProfileMetadata` holds the five curated fields; `use_for/highlights/limitations` are tuples of strings. `ProfileBinding` holds `image_tag`, `image_id`, `dependency_identity`, `platform`, `fingerprint`, `engine_identity`, `smoke_image_id`, `smoke_fingerprint`, `smoke_passed`, `smoke_evidence_digest`. Readiness requires matching smoke/image/fingerprint and `linux/amd64`. Runtime dispatch uses the immutable `image_id`, not a mutable tag. Digests use the existing canonical JSON/SHA-256 convention; codec canonicalization must match it.

**Bounds (implementation choices, reviewable here).** YAML ≤256 KiB, one document, ≤4 profiles, ≤16 structural levels, ≤4,096 scalar nodes; reject anchors, aliases, merge keys, duplicate keys, non-string keys, explicit tags, non-finite numbers and unknown fields. Labels ≤80 UTF-8 bytes, descriptions ≤512, each metadata list ≤8 strings of ≤160 bytes, extensions ≤16 string values of ≤256 bytes (keys ≤64). No implicit timestamp objects. Decode JSON envelopes with duplicate-key rejection; bound encoded bridge input/output to 1 MiB. Plans/assessments ≤32 KiB, progress snapshots ≤256 KiB, public summaries ≤2 KiB. Existing stricter provider/context/observation bounds still win.

**Authoritative persistence.** Use relational identity/status columns plus canonical validated JSON for plan/assessment payloads, not arbitrary graph-state blobs. `TaskRecord` is one-to-one with the v5 root `Run`; `TaskPlanRecord` is immutable per `(run_id, revision)`; `TaskStepAttempt` references the existing `ToolOperation` rather than copying its ledger. ORM `TaskDecisionRecord` and `TaskDecisionCommandRecord` store exact request identity and idempotent results; the latter is distinct from the `TaskDecisionCommand` API contract. No task rows are fabricated for older history. Maintain lock order: active project/conversation scope → Run/RunJob → task/decision/attempt. Do not hold database transactions across inference/provider calls.

**State and decisions.** Add nonterminal `waiting_plan`, `waiting_input`, `waiting_config` to the run status constraint (all fit the existing 20-character column). Plan/input waits bind a `TaskDecisionRecord`; missing pinned runtime compatibility uses `waiting_config` with a safe blocker and explicit `resume_configuration` command that cannot replace pins. Rejection uses existing terminal `cancelled` with reason `plan_rejected`; decision expiry uses `timed_out`/`decision_expired`; scope or source blockers retain truthful task disposition. Clarification before the first plan binds revision 0 and the request/task-context digest; later decisions bind the current immutable revision/review digest. Revisions supersede outstanding decisions atomically.

**Decision command.** Extend the delivered `TaskDecisionCommand(client_request_id: UUID, request_id: UUID, plan_revision: int, expected_digest: str, action, answer: str|None)` with actions `abort|repeat_step|continue_next`, retaining `confirm|reject|answer|resume_configuration`. Recovery actions are valid only for decision kind `recovery` and positive plan revisions; `confirm` cannot consume a recovery request. Answer ≤10,000 characters applies only to `answer`. Same request+same action/answer returns the recorded result even after a lost response; conflicting action/digest is 409; wrong actor/scope is 404; expired/stale/superseded is 409. Bind recovery digest to the plan and diagnosis, not just the plan digest. An unbound bare “yes” approves neither a plan nor recovery; explicit UI request metadata is required.

**Crash fallback and tracking.** A pre-commit crash leaves the existing request open: show it again after reconnect, preserving its identity, plan and completed work. A committed choice is recovered from the existing command record and outbox, even if its HTTP response was lost. Resume selection uses persisted attempts, verified evidence, skipped steps and unresolved work; graph state is a cursor, not an alternative operation ledger. Ordinary continuation/reconfirmation never repeats completed steps. A deliberate B restart remains a separate new-root action.

**Recovery payload.** Task 8 defines strict/frozen `TaskRecoveryReport(schema_version=1, plan_revision, plan_digest, step_id, attempt, operation_id, explanation, evidence_refs, unknowns, risks, recommended_action, eligible_actions, blockers)`. Plan revision ≥1, attempt 1–3; `operation_id` is a required existing operation UUID; explanation ≤2 KiB, each list ≤16 public strings/refs of ≤512 bytes, the entire report ≤32 KiB. Actions are `abort|repeat_step|continue_next`; `recommended_action` may be null and never executes itself. Backend constructs eligibility and validates evidence/scope; model text supplies only bounded explanation/unknowns/risks/recommendation. Store the immutable report in the decision's existing `request_json`; its `expected_digest` is the canonical digest of the report. Publish the report only when diagnosis is recorded, or clearly identify an evidence-only fallback if inference cannot run. Revalidate current eligibility at consumption; changed report/plan supersedes the request rather than changing its meaning.

**Recovery projection.** Add `TaskProgress.skipped_steps: tuple[str,...]=()` and `TaskRecord.skipped_steps_json` (default `[]`) in Task 8. Keep skipped and verified sets disjoint, retain attempt evidence, and expose step status `skipped` separately. Also define strict/frozen `TaskCallBinding(schema_version=1, plan_revision: int>=1, step_id, attempt: int[1,3], logical_call_id: str)` with the existing `model:<turn>:call:<index>` format. Add default-empty `TaskProgress.call_bindings` (≤16) and `TaskRecord.call_bindings_json`: these are step-to-transcript/operation references only, not a second ledger or copied tool result. Task 11 persists this binding before gateway dispatch so Task 15 can find an interrupted operation even if a result never reached a checkpoint. Consumed recovery commands retain the user choice and bound report. `abort` resolves the decision as rejected and cancels remaining work; `repeat_step`/`continue_next` resolve it as confirmed and record the continuation intent. Recording the intent never dispatches a tool in an HTTP handler. The actual repeat gets a new logical call/operation identity; skip never fabricates one.

**Preferences and admission.** Add `User.agent_mode="copilot"`, `agent_mode_revision=1`. `GET /api/auth/preferences` returns `{agent_mode, revision}`; `PATCH` accepts `{agent_mode, expected_revision}` with compare-and-swap and 409 on stale writes. Extend turn submission with optional paired `agent_mode`/`agent_mode_revision`; v5 checks both against the server preference under lock. Legacy clients omitting both capture the server's current value. Lookup an existing `client_request_id` and compare its recorded submission payload before testing the current preference, so retries preserve mode. A new stale submission fails 409 without a message/run/outbox; do not silently refresh and resubmit as Auto.

### Dependency order and checkpoints

Tasks 1→2→3 establish local snapshots; Task 4 defines task contracts; Task 5 joins those contracts to sandbox enforcement. Tasks 6→7→8→9 establish task state, preferences, durable decisions and budgets. Tasks 10→11→12 add the actual loop. Tasks 13→14 expose it in Chat UI. Tasks 15→16→17 qualify recovery and complete acceptance. Execute in numerical order unless a later reviewed execution plan explicitly coordinates independent work; no subagents are needed to draft this document.

After Tasks 3, 6, 9, 12 and 14, run all affected unit suites, review the interfaces/failure evidence, and record a checkpoint here. Stop at a failed gate rather than stacking more work on unverified contracts.

## Task 1: Safe profile snapshot codec and maintained purpose metadata

**Dependencies:** None. **Deliverable:** One validated codec shared by runtime and host bridge, plus four curated descriptors; no image builds.

**Files:** Create `apps/api/src/openship/sandboxes/profile_schema.py`, `profile_codec.py`, `deploy/sandbox/sandbox-profiles.example.yaml`; modify `apps/api/pyproject.toml`, `apps/api/uv.lock`, `deploy/sandbox/images.json`, `scripts/sandbox_image_lib/catalog.py`; create `apps/api/tests/test_sandbox_profile_catalog.py`.

**Interfaces:** Produces `ProfileId`, `ProfileMetadata`, `ProfileBinding`, `ProfileEntry`, `ProfileCatalog`; `decode_catalog(data: bytes) -> ProfileCatalog`, `encode_catalog(catalog: ProfileCatalog) -> bytes`, `catalog_digest(catalog: ProfileCatalog) -> str` in `profile_schema.py`; codec `main(argv: Sequence[str]|None=None) -> int`. Catalog models are strict/frozen and secret-free. Host metadata projection does not change existing recipe fingerprints.

- [x] **1. Write failing codec/catalog tests.** Include `test_empty_catalog_and_four_profiles_round_trip`, `test_duplicate_keys_tags_aliases_merge_and_oversize_fail`, `test_metadata_only_change_does_not_change_recipe_identity`, `test_smoke_binding_must_match_image_and_fingerprint`. Pin these assertions:
  ```python
  assert decode_catalog(b"schema_version: 1\nprofiles: {}\n").profiles == {}
  assert before_recipe.fingerprint == after_metadata_only_change.fingerprint
  assert catalog_digest(before_metadata) != catalog_digest(after_metadata)
  assert codec_process.returncode != 0  # unsafe tag, duplicate key or alias
  ```
- [x] **2. Run red:** from `apps/api`, `uv run pytest -q tests/test_sandbox_profile_catalog.py`. Expect failures for the missing codec/metadata contract, not absent containers.
- [x] **3. Implement the codec and curated metadata.** Add the direct PyYAML dependency and resolve the lock with uv. Use a restricted `SafeLoader` plus preconstruction token/depth/node checks and duplicate detection; `safe_load` alone is insufficient. Reject implicit non-JSON scalar types, then validate canonical JSON with `model_validate_json` so YAML lists correctly enter strict tuple fields. Write the empty example `schema_version: 1` / `profiles: {}` with comments describing generation and ownership. Project metadata separately from recipe inputs; never hash descriptive text as image bytes.
- [x] **4. Run green:** repeat the targeted tests and `uv run pytest -q tests/test_sandbox_image_catalog.py tests/test_opensandbox_packaging.py`; run `uv run ruff check src/openship/sandboxes/profile_schema.py src/openship/sandboxes/profile_codec.py`. Expected: all pass, legacy recipe identities unchanged by metadata-only edits.
- [x] **5. Commit verified files** using the file-based workflow; suggested subject `feat: define validated sandbox profile snapshots`. Record red/green evidence here.

  Evidence: the red command failed during collection for missing `profile_schema`.
  The targeted catalog suite passed 31 tests; the image catalog/packaging suites
  passed 27 tests; Ruff passed. All four recipe fingerprints were unchanged by
  removing the metadata. The full API suite had 1,706 passes, 88 skips, one
  failure and 53 setup errors because the full-stack PostgreSQL and JetStream
  test-service configuration was not available; those lanes remain to verify.
  Implementation commit `4a92a17` passed the configured API lint and unit,
  frontend typecheck and unit, and staged-secret pre-commit hooks.

## Task 2: Build-command consent and atomic partial YAML updates

**Dependencies:** Task 1. **Deliverable:** Existing `sandbox-images build` safely writes successful mappings when authorized.

**Files:** Create `scripts/sandbox_image_lib/profiles.py`; modify `scripts/sandbox_images.py`, `scripts/sandbox_image_lib/config.py`, `builds.py`, `.gitignore`; create `apps/api/tests/test_sandbox_profile_writes.py`; extend `test_sandbox_image_commands.py`.

**Interfaces:** Consumes Task 1 codec. Produces `resolve_profiles_source(root: Path, environ: Mapping[str,str]) -> tuple[Path,bool]` in `config.py` (bool means explicit override); `merge_profile_updates(root: Path, destination: Path, updates: Mapping[str,dict]) -> tuple[str,...]` in `profiles.py`. `updates` contains only reinspected successful build/smoke records plus matching curated metadata. Codec subprocess timeout 10 seconds, no shell and no secret-bearing environment dump.

- [x] **1. Write failing write/command tests.** Names: `test_decline_and_non_tty_leave_bytes_unchanged`, `test_flag_creates_or_merges_without_deleting_other_profiles`, `test_partial_build_writes_successes_and_returns_failure`, `test_concurrent_writers_reread_under_lock`, `test_failed_codec_replace_or_symlink_preserves_old_file`, `test_custom_in_repo_path_requires_ignore`. Assert:
  ```python
  assert merged.profiles["standard"] == previous.profiles["standard"]
  assert merged.profiles["logs-data"].enabled is False  # existing operator choice
  assert merged.profiles["logs-data"].extensions == {"owner": "platform"}
  assert exit_code == 1 and published_ids == ("logs-data",)  # another build failed
  ```
- [x] **2. Run red:** `uv run pytest -q tests/test_sandbox_profile_writes.py tests/test_sandbox_image_commands.py` from `apps/api`.
- [x] **3. Implement the existing command extension.** Accept exactly `build PROFILE|--all [--overwrite-profiles]`; preserve list/smoke and invalid-input exit 2. Catch per-profile failures so separately successful profiles remain eligible for one consent/write; still exit 1 on any failed build/smoke/write. Show destination/profile IDs before prompting; default refusal on EOF. Without TTY/flag, build normally and visibly skip writing. Reinspect successful images before publishing. Use a stable sibling lock, no-follow regular-file checks on destination/ancestors, reread+validate under lock, same-directory temporary file, fsync and atomic replace; preserve safe existing mode and make new files readable by UID 1000. Reject unsafe/group-or-world-writable destinations/directories rather than trusting raceable paths. Refuse malformed old YAML even with the flag. Ignore `/deploy/sandbox/sandbox-profiles.yaml`; for a custom in-repo destination require `git check-ignore` success before writing, never edit ignores automatically.
- [x] **4. Run green:** repeat red command, plus `uv run pytest -q tests/test_sandbox_image_catalog.py tests/test_sandbox_image_engine.py`. Expected: no lost updates/truncation; process-env/root-`.env`/default resolution works from a different cwd, quoted spaces are literal, empty/interpolated explicit paths fail safely. Build records stay untracked; no real build yet.
- [x] **5. Commit** with subject `feat: write local profiles from sandbox image builds`; record evidence and any blocked real-engine checks.

  Evidence: the red command failed at collection because
  `sandbox_image_lib.profiles` did not exist; the command suite alone failed
  `test_build_accepts_the_overwrite_flag_and_rejects_other_build_shapes` (exit
  2, 1 failed / 12 passed) because `--overwrite-profiles` was parsed as a
  profile. Green: the repeated write/command suite passed 26 tests, and the
  combined write/command/catalog/engine/profile suite passed 50. `uv run ruff
  check` passed on all touched Python modules. A review follow-up validates
  every destination ancestor with `lstat`, permitting sticky group/world-
  writable directories such as `/tmp` while rejecting non-sticky ones. Tests
  exercise the codec bridge
  against the existing API venv with a minimal environment; no image was built,
  pulled, run, or contacted, and no real-engine publication check was attempted.
  The generated default and its sibling lock are ignored. Custom in-repo
  destinations require both the destination and its lock file to pass
  `git check-ignore`; the helper never edits ignore files.

## Task 3: Fixed-target mounts and runtime snapshot readiness

**Dependencies:** Tasks 1–2. **Deliverable:** Supported launchers and containers agree on exact snapshot bytes; disabled startup remains build-free.

**Files:** Create `apps/api/src/openship/sandboxes/profiles.py`; modify `scripts/openship-stack`, `scripts/sandbox_images.py`, `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, `.env.prod.example`, `apps/api/src/openship/execution/availability.py`, `main.py`, `runs/worker.py`; create `apps/api/tests/test_sandbox_profile_mounts.py`; extend `test_sandbox_image_stack.py`.

**Interfaces:** Produces `load_runtime_catalog(path: Path=Path("/etc/openship/sandbox-profiles.yaml")) -> ProfileCatalog`, `eligible_profiles(catalog: ProfileCatalog, permitted: frozenset[ProfileId]) -> tuple[ProfileId,...]`. Runtime uses the fixed default; explicit path injection is for native-host setup/tests, never host `SANDBOX_PROFILES_FILE` forwarded into container settings. `preflight` validates enabled mapping entries using their engine/platform/build/smoke identities without requiring legacy single-profile settings.

- [x] **1. Write failing mount/readiness tests.** `test_default_and_custom_sources_share_fixed_readonly_target`, `test_disabled_missing_default_uses_example_without_writing`, `test_explicit_missing_or_bad_override_never_falls_back`, `test_mapping_preflight_uses_manager_engine_and_immutable_identity`, `test_prod_env_does_not_change_root_dotenv_selector_contract`. Assert:
  ```python
  assert api_mount["target"] == worker_mount["target"] == "/etc/openship/sandbox-profiles.yaml"
  assert api_mount["read_only"] is True and api_mount["bind"]["create_host_path"] is False
  assert not generated_default.exists()  # disabled bootstrap mounted tracked example
  assert probe_engine.identity == mapping_engine_identity
  ```
- [x] **2. Run red:** `uv run pytest -q tests/test_sandbox_profile_mounts.py tests/test_sandbox_image_stack.py` from `apps/api`.
- [x] **3. Implement mount/source resolution and load.** Reuse Task 2's resolver through an internal host-helper invocation, not a second Bash dotenv parser. Export only the resolved source for Compose interpolation. The profile-source setting deliberately reads root `.env` in all launcher environments; `.env.prod` keeps its existing role for all other settings, and production overrides this selector through process env. Add long-syntax read-only mounts to API/worker in base and standalone prod files; test effective dev/rootless merge without widening existing mounts. Disabled bootstrap applies only under P7. Enabled new mapping mode preflights every enabled entry in the serving store and requires at least one ready entry; legacy execution remains a separate explicitly versioned path. Native-host setup uses an explicit existing mapping path, no file creation. Load bounded snapshots at process startup only when the new sandbox path needs them; sandbox-disabled native chat exposes an empty catalog and requires no file. Readiness reports safe digest/profile availability without engine sockets or image IDs in public capability descriptions.
- [x] **4. Run green:** repeat tests and `bash -n scripts/openship-stack`. Expected: mount/config fixtures pass. Record Checkpoint 1; real mount-byte checks remain Task 17, not established by a fake Compose process.
- [x] **5. Commit** with subject `feat: mount validated sandbox profile snapshots`; record disabled and explicit-invalid-source evidence.

  Evidence: the red command failed during collection because
  `sandbox_image_lib.config.resolve_compose_source` did not exist. Green: the
  named mount/stack suite passed 27 tests, and the sandbox/profile/availability
  regression set (catalog, commands, writes, engine, profile catalog,
  OpenSandbox packaging, availability, tool, worker entry, capability routes
  and tool-run commands) passed 188 tests.
  `bash -n scripts/openship-stack` and Ruff on every touched Python module
  passed. Disabled startup mounts the tracked empty example and leaves the
  generated default absent; an explicit missing, directory, or interpolated
  override is refused without falling back. Enabled mapping preflight reads the
  validated snapshot and inspects each enabled entry's immutable image ID
  against the manager engine identity; legacy `python311` remains on its
  separate path. `/ready` exposes only the catalog digest and eligible profile
  IDs, never engine sockets or image IDs, and reports ready only when the
  configured `SANDBOX_PROFILE_ID` itself is eligible. Compose interpolates the
  public `SANDBOX_PROFILES_FILE` selector without forwarding it into any
  service environment. **Checkpoint 1:** fixture Compose
  parsing establishes the declared mounts, not real mount bytes; actual
  API/worker byte equality remains Task 17 and is not claimed here.

## Task 4: Versioned task, plan, assessment and switching contracts

**Dependencies:** Task 1. **Deliverable:** Strict v5 contracts, old execution decoders unchanged.

**Files:** Create `apps/api/src/openship/runs/tasks/__init__.py`, `contracts.py`, `routing.py`; modify `apps/api/src/openship/capabilities/contracts.py`; create `apps/api/tests/test_task_contracts.py`, `test_task_routing.py`.

**Interfaces:** Produces `TaskLimits`, `TaskContext`, `TaskPlan`, `TaskStep`, `TaskAssessment`, `TaskAction`, `TaskProgress`, `TaskDecisionCommand`, `VerificationReport`, `ExecutionConfigV5`. `ExecutionConfigV5` extends v4's maintained capability/connector/catalog fields and adds `task_context`, `profile_catalog`, `profile_catalog_digest`, `task_limits`; v4 remains decodable. `select_action(progress: TaskProgress, assessment: TaskAssessment|None, *, policy_allows: bool, budget_allows: bool) -> TaskAction` is deterministic and performs no inference or dispatch.

- [x] **1. Write failing contract/routing tests.** Include `test_plan_rejects_cycles_future_dependencies_duplicate_steps_and_unknown_capability`, `test_limits_and_missing_evidence_fail_closed`, `test_switch_does_not_infer_or_dispatch`, `test_old_execution_versions_decode_without_task_fields`. Pin:
  ```python
  assert TaskLimits().max_steps == 8 and TaskLimits().max_repairs_per_step == 2
  assert select_action(waiting_progress, None, policy_allows=True, budget_allows=True).kind == "wait"
  assert select_action(unverified_progress, None, policy_allows=True, budget_allows=True).kind != "finish"
  assert decode_execution_config(old_v4).schema_version == 4
  ```
- [x] **2. Run red:** `uv run pytest -q tests/test_task_contracts.py tests/test_task_routing.py` from `apps/api`.
- [x] **3. Define the schemas and transition table.** All public contracts have `schema_version=1` and strict/frozen fields. Step IDs are stable ≤64-character lowercase identifiers; ordered dependencies must point only to earlier steps. Each step declares intent, capability ref/profile choice where relevant, expected outputs and completion checks. Model-authored draft IDs/refs remain proposals until server validation. Assessment contains bounded public explanation/evidence refs, not private reasoning. Action literals: `assess`, `execute`, `repair`, `revise`, `wait`, `verify`, `finish`, `stop`; `wait` specifies decision/configuration kind. `TaskProgress` carries current revision, attempts, verified outputs, pending decision and cumulative counters; unknown/unsafe/denied states select stop/wait, never execute. `TaskContext` includes root/initiator/scope/mode and original intent constraints. Task limits copy P1–P3 exactly; lower operator ceilings may reduce them.
- [x] **4. Run green:** repeat tests and `uv run ruff check src/openship/runs/tasks src/openship/capabilities/contracts.py`. Expected: contract matrices pass, no added `agent_*` capability/tool IDs.
- [x] **5. Commit** with subject `feat: define versioned agentic task contracts` and record evidence.

  Evidence: the red command failed during collection with
  `ModuleNotFoundError: No module named 'src.openship.runs.tasks'` for both new
  suites. Green: the named contract/routing suite passed 25 tests, and the
  existing execution-contract plus profile-catalog regressions passed 39 tests;
  Ruff passed on the new package, the modified contracts module and both test
  files. v1–v4 decode to their original types and reject v5-only fields; v5
  pins the task context, profile catalog digest and P1–P3 limits. No
  `agent_reasoning`, `agent_planning`, `agent_tracking` or `agent_switch`
  capability/tool IDs were added; the four remain runtime responsibilities.
  `select_action` is synchronous and deterministic: it performs no inference,
  dispatch, persistence or authority grant, and unverified or waiting progress
  can never select `finish` or `execute` past a wait.

## Task 5: Enforce one profile-selecting Python execution path

**Dependencies:** Tasks 1, 3–4. **Deliverable:** `python_execute` selects only an authorized pinned ready runtime; no new wrappers.

**Files:** Modify `apps/api/src/openship/sandboxes/profiles.py`, `python_tool.py`, `tool.py`, `capabilities/catalog.py`, `definitions.py`, `execution/admission.py`, `gateway.py`, `projections.py`; create `apps/api/tests/test_task_profile_selection.py`; extend `tests/test_sandbox_profile_selectors.py`.

**Interfaces:** Produces `resolve_task_requirements(execution: ExecutionConfigV5, profile_id: ProfileId, current: ProfileCatalog) -> RuntimeRequirements` in `profiles.py`; `task_tool_definitions(execution: ExecutionConfigV5, enabled_ids: frozenset[str]) -> tuple[ToolDefinition,...]` in `capabilities/definitions.py`. `PythonArguments` gains optional `profile_id` for compatibility; new v5 model schema and gateway require it, legacy graphs retain omission. Existing single tool ID and gateway `execute_tool` signature remain.

- [x] **1. Write failing selector/gateway tests.** `test_each_maintained_profile_is_selected_by_purpose`, `test_hidden_disabled_changed_and_unknown_profiles_never_dispatch`, `test_model_cannot_override_image_or_runtime`, `test_profile_change_prevents_incompatible_sandbox_reuse`, `test_legacy_no_profile_request_stays_legacy`. Assert:
  ```python
  assert requirements.image == pinned_entry.runtime.image_id  # not image_tag
  assert set(exposed_profile_ids) == {"logs-data", "standard"}  # enabled ready subset
  assert "image_id" not in model_description and "engine_identity" not in model_description
  assert create_count == 0  # selected binding no longer matches mounted snapshot
  ```
- [x] **2. Run red:** `uv run pytest -q tests/test_task_profile_selection.py tests/test_sandbox_profile_selectors.py` from `apps/api`.
- [x] **3. Implement v5 selection at both description and gateway boundaries.** Derive bounded profile enums/descriptions from the pinned permitted subset; recheck current enablement/binding, policy and exact runtime identity before admission and dispatch. Reject omitted selector in v5 and unsupported image/settings fields. Pass resolved requirements through the existing invocation; keep `runtime_requirements()` only for legacy graphs. Expose no host image metadata to the model/browser. Existing implementation/manifest digest changes may make old queued refs incompatible: preserve historical decoders and block those refs visibly, never forge a new manifest digest or silently repin. Do not bypass installed capability policy to preserve history.
- [x] **4. Run green:** repeat tests plus `uv run pytest -q tests/sandboxes tests/test_agent_tool_loop.py tests/execution/test_definition_registry.py`. Expected: exact selected runtime, unchanged sandbox lifetime/security/output validation. Before investing in graph rollout, obtain authorization for a bounded real-manager immutable-ID capability probe; record its result under Task 17. Unsupported identity semantics block the design, not permission to weaken it to tags.
- [x] **5. Commit** with subject `feat: select pinned sandbox profiles through python execution`; record any legacy refs now blocked by truthful compatibility checks.

  Evidence: the red command failed during collection because
  `task_tool_definitions` did not exist. Green: the named selector/gateway suite
  passed 9 tests, the sandbox/agent-loop/definition-registry regression set
  passed 206 tests, and the profile catalog/mount/write, task contract,
  capability route and image catalog regressions passed 81 tests. Ruff passed
  on every touched module. Task-v5 `python_execute` exposes only pinned profiles
  that are still enabled, unchanged and smoke-qualified; the model sees a
  bounded purpose enum and no image, engine or settings fields. Admission
  rejects an omitted selector and rechecks the exact binding and current tool
  policy; dispatch rechecks the current mount before any provider creation.
  Legacy v1–v4 execution still resolves the single operator-selected runtime
  through `runtime_requirements()`, and legacy requests that supply a profile
  are refused. Adding the profile modules and `profile_id` schema field changes
  the Python manifest digest, so queued `python_execute` refs pinned before this
  change are now blocked visibly as `changed`; historical decoders are
  unchanged and no digest was forged or silently repinned. The bounded
  real-manager immutable-ID probe has not been authorized or run and remains
  Task 17 evidence.

## Task 6: Durable task projection and immutable plan/attempt identity

**Dependencies:** Task 4. **Deliverable:** Migrated task records with fenced/idempotent progression; no graph activation yet.

**Files:** Create `apps/api/alembic/versions/031_phase7a_task_state.py`, `apps/api/src/openship/runs/tasks/models.py`, `state.py`, `projections.py`; modify `apps/api/src/openship/runs/models.py`, `apps/api/alembic/env.py`; create `apps/api/tests/test_task_state.py`, `tests/integration/test_phase7a_task_state_postgres.py`; update migration-test isolation in `tests/conftest.py` for the new owning module.

**Interfaces:** Produces `read_progress(db: Session, run_id: UUID) -> TaskProgress`; `save_plan(db: Session, claim: Claim, plan: TaskPlan, *, expected_revision: int) -> TaskProgress`; `record_attempt_result(db: Session, claim: Claim, *, step_id: str, attempt: int, operation_id: UUID) -> TaskProgress`. Caller commits; these functions validate claims and append public events within that transaction.

- [x] **1. Write failing state/migration tests.** `test_upgrade_keeps_old_history_without_fabricating_tasks`, `test_plan_revision_is_immutable_and_fenced`, `test_committed_operation_is_only_source_of_attempt_outcome`, `test_duplicate_result_does_not_advance_twice`, `test_downgrade_refuses_v5_rows`. Assert:
  ```python
  assert updated.current_revision == previous.current_revision + 1
  assert len(read_progress(db, run_id).steps[0].attempts) == 1  # duplicate publication
  assert legacy_run.status == "completed" and legacy_task is None
  assert attempt.operation_id == existing_tool_operation.id
  ```
- [x] **2. Run red:** `uv run pytest -q tests/test_task_state.py` and, with authorized disposable `TEST_POSTGRES_URL`, `uv run pytest -q -m postgres tests/integration/test_phase7a_task_state_postgres.py` from `apps/api`. Expected: missing schema/state behavior fails; missing explicit PostgreSQL URL must fail an enabled lane.
- [x] **3. Implement migration `phase7a031`, parent `phase6b030`, and state functions.** Add the three waiting statuses, task/plan/attempt/decision/decision-command tables and indexes. Uniqueness: task run; plan `(run,revision)`; attempt `(run,step_id,ordinal)` and nonnull operation ID; decision command `(run,client_request_id)`; at most one open decision per run. Columns bind project/conversation/initiator, revision/digest/status, safe payloads/timestamps, accepted evidence, active-time counters, and operation FK. Validate operation scope/digests/outcome/artifact ownership from existing records before tracking progress. Plans are immutable; supersession creates another row. Add task model imports for migration metadata without circular runtime initialization. Refuse destructive downgrade if v5 rows/waits exist. No historical status rewrites.
- [ ] **4. Run green:** repeat both lanes and inspect DB uniqueness/FK/fence assertions. Expected: upgrade/downgrade guard passes on SQLite and PostgreSQL, rollback of an event/state transaction leaves neither half visible. Record Checkpoint 2; do not mark PostgreSQL complete when unavailable.
- [x] **5. Commit** with subject `feat: persist fenced task plans and evidence-backed progress`; record migration evidence.

  **Checkpoint 2 evidence (SQLite lane, complete):** `uv run pytest -q tests/test_task_state.py` passed 10 tests after an initial collection failure (`ModuleNotFoundError: src.openship.runs.tasks.models`, the missing schema/state behavior). The suite proves old history upgrades without fabricated task rows (`legacy_run.status == "completed"`, `task_count == 0`), plan revisions are immutable and fenced (`updated.current_revision == previous.current_revision + 1`, stale revision/fence refusals), a committed `ToolOperation` is the only attempt-outcome source (`attempt.operation_id == existing_tool_operation.id`, input/result digests and owned artifacts, including a stale identity-map regression that must refresh the locked operation before validating), repeated publication stays at one attempt/one event, a state-plus-event transaction rolls back to neither half, the decision tables enforce one open request per run and per-client-command uniqueness, and downgrade refuses v5 rows/waits while succeeding on an otherwise empty task schema. The full network-free API lane passed 1772 tests (146 deselected). `uv run ruff check` passed on every touched module.

  **PostgreSQL lane (controller-reported green):** The controller ran `uv run pytest -q -m postgres tests/integration/test_phase7a_task_state_postgres.py`: 4 passed, with 1 existing SQLAlchemy warning. The previous local attempt was skipped because its connection was unavailable. The PostgreSQL red phase remains unverified.

## Task 7: Own-account preference API and exact mode capture at admission

**Dependencies:** Tasks 4, 6. **Deliverable:** Persisted preference and v5 admission payload pinning, behind the rollout gate.

**Files:** Create `apps/api/alembic/versions/032_phase7a_agent_mode.py`, `apps/api/src/openship/auth/preferences.py`; modify `auth/models.py`, `schemas.py`, `routes.py`, `runs/schemas.py`, `service.py`, `config.py`; create `apps/api/tests/test_agent_mode_preferences.py`, `test_task_admission.py`.

**Interfaces:** Produces `read_preferences(db: Session, user_id: UUID) -> AgentPreferences`, `update_preferences(db: Session, user_id: UUID, command: AgentPreferencesUpdate) -> AgentPreferences`. Auth schemas define `AgentPreferences(agent_mode, revision)` / `AgentPreferencesUpdate(agent_mode, expected_revision)`. `submit_turn` retains its existing call shape and consumes extended `TurnSubmitRequest`; v5 captures `TaskContext`/profile snapshot and records exact submission digest atomically with message/run/job/outbox.

- [x] **1. Write failing API/admission tests.** `test_existing_and_new_accounts_default_copilot`, `test_preference_cas_and_cross_account_isolation`, `test_stale_new_submission_creates_nothing`, `test_retry_after_preference_change_returns_original_run`, `test_feature_gate_and_unqualified_profile_fail_visibly`. Assert:
  ```python
  assert preferences.json() == {"agent_mode": "copilot", "revision": 1}
  assert stale_patch.status_code == 409
  assert retry_run.id == original.id and retry_execution.task_context.mode == "copilot"
  assert (new_message_count, new_run_count, new_outbox_count) == (0, 0, 0)  # stale send
  ```
- [x] **2. Run red:** `uv run pytest -q tests/test_agent_mode_preferences.py tests/test_task_admission.py` from `apps/api`.
- [x] **3. Implement preference migration `phase7a032` after `phase7a031`, APIs and admission.** Add mode check constraint and positive revision default 1. Preference GET/PATCH uses authenticated identity, not request-supplied account ID; same-mode save need not increment. Validate paired mode/revision and idempotency in the order specified above. Include waits in active-conversation admission checks so a second execution task cannot bypass one waiting for approval. Add rollout settings without enabling existing deployments. Reject a v5 profile that cannot support tools/contract output; never silently choose an unselected provider or execution mode. Explicit already-pinned fallback rules and private-continuation restrictions remain authoritative; qualify initially with fallbacks disabled. Tool-disabled chat remains usable when sandbox is disabled. Task 9 supplies task-specific effective model/loop limits before gate activation.
- [ ] **4. Run green:** repeat tests plus `uv run pytest -q tests/test_auth_login.py tests/test_auth_register.py tests/test_run_commands.py tests/test_tool_run_commands.py`. Verify 031→032 upgrade on PostgreSQL through Task 6 migration suite extended for preference rows; stale writes race under the user lock and return 409, not last-writer-wins.
- [x] **5. Commit** with subject `feat: persist agent mode and pin it at task admission`; record evidence.

  **Task 7 evidence (SQLite lane, complete):** the red command failed first at
  collection (`ImportError: cannot import name 'Message' from
  'src.openship.runs.models'`), then with 9 failed / 1 passed once the import
  was corrected (missing preference settings attributes, routes and admission
  behavior). Green: `uv run pytest -q tests/test_agent_mode_preferences.py
  tests/test_task_admission.py` passed 10 tests, and the named follow-up suites
  (`test_auth_login.py test_auth_register.py test_run_commands.py
  test_tool_run_commands.py test_task_state.py test_task_profile_selection.py`)
  passed 56 tests. Additional regressions (`test_tool_turn_commands.py
  test_task_contracts.py test_agent_tool_loop.py test_conversation_lifecycle.py
  test_archive_admission.py`) passed 58 tests, and `uv run ruff check` passed on
  every touched module. The suite proves existing and new accounts default to
  `{"agent_mode": "copilot", "revision": 1}` across the 031→032 SQLite upgrade,
  PATCH uses revision CAS sequentially (a stale revision returns 409 and a
  same-mode save does not increment) and is isolated by authenticated identity,
  and a stale new submission returns 409 while creating no message, run, job or
  outbox rows. The *concurrent* same-account race is covered only by the
  PostgreSQL test reported below; SQLite does not establish
  concurrent-writer evidence. A retry with the same `client_request_id` returns
  the original run and pinned `copilot` mode even after the account preference
  changed. The rollout gate defaults off with an empty profile allowlist; enabled
  submissions require a tool-capable allowlisted profile built as
  `ExecutionConfigV5`, and unqualified selections return actionable 422 blockers
  with no legacy/Auto fallback. Wait statuses (`waiting_connector`,
  `waiting_plan`, `waiting_input`, `waiting_config`) participate in
  active-conversation admission, both resume paths, connector-wait resume and
  artifact-pin retention, and unpaired `agent_mode`/`agent_mode_revision`
  fields are rejected. `save_plan` now derives mode/scope/graph version from the
  pinned `TaskContext` instead of hardcoding `copilot`/`conversation`. The full
  API lane reported 1 failed / 1784 passed / 92 skipped / 53 errors; the single
  non-integration failure was `tests/full_stack/test_sandbox_python_chat.py`
  failing only because `TEST_POSTGRES_URL` is unset, and
  `test_opensandbox_packaging.py` passed when re-run alone. The 53 errors are
  external-service integration lanes (NATS/JetStream/PostgreSQL).

  **PostgreSQL lane (controller-reported green):** The controller ran
  `uv run pytest -q -m postgres tests/integration/test_phase7a_task_state_postgres.py`:
  4 passed, with 1 existing SQLAlchemy warning. No connection details were shared.

## Task 8: Durable waits, re-approval fallback and bounded decision integration

**Dependencies:** Tasks 6–7. **Deliverable:** A task can park without occupying a worker, accept one authorized decision, and resume through the existing outbox; a choice lost before commit is simply requested again. Deliver in four independently tested/reviewed slices below, not one all-or-nothing subagent assignment.

**Files:** Extend existing `apps/api/src/openship/runs/tasks/decisions.py`, `contracts.py`, `models.py`, `state.py`, `projections.py`, `runs/queue.py`, `service.py`, `worker.py`, `main.py`, `events/schemas.py`, `messaging/delivery.py`, `exhaustion.py`, `migration.py`, `recovery.py`; create `runs/tasks/routes.py`, `apps/api/alembic/versions/034_phase7a_recovery_decisions.py`. Add a data-aware downgrade guard to `033_phase7a_preplan_decisions.py` without changing its applied upgrade. Extend `apps/api/tests/test_task_decisions.py`, `test_task_contracts.py`, `test_task_state.py`; create `tests/integration/test_task_decisions_postgres.py`, `tests/test_task_wait_delivery.py`. Update the owning migration fixture exclusion in `tests/conftest.py`.

**Interfaces:** Extend `TaskDecision.kind` with `recovery` and optional `recovery_report: TaskRecoveryReport|None`; extend `TaskDecisionCommand`/ORM action checks as defined above. Produce `park_task_wait(db: Session, claim: Claim, *, kind: str, prompt: str, now: datetime, recovery_report: TaskRecoveryReport|None=None) -> TaskProgress`, `apply_decision(db: Session, *, principal_id: UUID, run_id: UUID, command: TaskDecisionCommand, now: datetime) -> TaskProgress`; retain `expire_task_decisions(db: Session, *, now: datetime, batch_size: int=100) -> int`. Caller commits. Route `POST /api/runs/{run_id}/task-decisions` returns the public task projection. Recovery transport/schema is established here; evidence diagnosis and actual repeat/skip resumption are integrated in Task 15 before qualification, not claimed complete by this task.

**Committed starting point, not work to repeat:** `5d2ec7b` added standalone expiry, revision-zero contracts/migration 033 and queue-claim disposal for task waits. Its focused verification passed 69 tests after fixing revision-zero `confirm`; all commit hooks passed. The earlier 54-test slice is historical evidence, not the current acceptance total. Expiry is not wired to maintenance; parking/apply/routes/delivery integration are absent. Tasks 6/7 PostgreSQL evidence is 4 passing tests, not evidence for Task 8 or migration 033.

### Slice 8A — contract and migration compatibility

**Review follow-up:** Added downgrade pre-check locking, rejection of plan
revisions that omit recovery-referenced steps, and the named pre-plan answer
test. Focused SQLite tests: 42 passed, one warning; Ruff passed. A concurrent
writer PostgreSQL regression test was added but remains unrun because the
service URL was unavailable. The previously reported PostgreSQL result
predates these fixes and is not evidence for them. Controller verification
and review of this follow-up remain pending.

**Slice 8A evidence:** Focused SQLite contract/state suite: 38 passed, one
SQLAlchemy deprecation warning; Ruff passed. The controller independently
reports the authorized PostgreSQL migration lane passed: 1 passed, one
existing SQLAlchemy warning, 17.33s. The migration matrix covers six 034
blockers and three 033 revision-zero blockers with preservation and clean
downgrade checks; nine isolated missing-guard mutations were detected. The
guard tests were written after implementation, so mutation evidence does not
constitute original red-before-green evidence. Independent slice review and
the explicitly named pre-plan answer test remain outstanding; do not treat
Task 8 or its later slices as complete. See the 8A report for details.

- [ ] **8A.1 Write failing tests:** extend `test_task_contracts.py`/`test_task_state.py` and create the owning `tests/integration/test_task_decisions_postgres.py` with `test_recovery_contracts_bind_report_and_positive_revision`, `test_preplan_answer_allowed_but_confirmation_forbidden`, `test_skipped_is_disjoint_from_verified`, `test_recovery_migration_upgrade_and_lossy_downgrade_guard`. Assert recovery requests require a report and revision ≥1, pre-plan answers remain valid, old progress defaults new fields to empty, bindings refer to unique logical call IDs, and downgrade cannot erase recovery/skip/call-binding or revision-zero data. Decision-kind/action matching is enforced against the persisted request in 8C, not a command-supplied `kind`.
- [ ] **8A.2 Run red:** `uv run pytest -q tests/test_task_contracts.py tests/test_task_state.py`; authorized DB: `uv run pytest -q -m postgres tests/integration/test_task_decisions_postgres.py`. Expect missing recovery contracts/schema, not missing containers.
- [ ] **8A.3 Implement:** add `TaskRecoveryReport`, `TaskCallBinding` and default-empty progress fields above; migration `phase7a034` after `phase7a033` adds `skipped_steps_json`, `call_bindings_json`, recovery decision kind/action checks and positive-revision recovery constraints. Reports use existing `request_json`; no second ledger/table. Update the existing head assertion in `test_task_state.py` to 034; keep explicit historical 031→032 tests at their named revisions. Guard lossy downgrade of new data and revision-zero data before tightening constraints.
- [x] **8A.4 Run green:** repeat both red commands and `uv run ruff check src/openship/runs/tasks alembic/versions/033_phase7a_preplan_decisions.py alembic/versions/034_phase7a_recovery_decisions.py`. Record actual SQLite/PostgreSQL scope; a skipped lane is not green evidence.
- [ ] **8A.5 Commit:** use scanned file-based subject `feat: define task recovery decision contracts`, obtain a slice review and record the commit/evidence here.

### Slice 8B — park and expire without executing

**8B implementation evidence (2026-11-09):** Added caller-owned fenced parking,
revision-zero pinned-context binding, recovery operation/report binding, and
recovery expiry. The named red command initially produced 4 missing-API
failures and 30 passes; after review fixes it produced **41 passed** (SQLite,
one SQLAlchemy deprecation warning). Ruff passed on the changed task modules
and decision tests. Parking rollback restores the run, lease and request state.
An additional dispatched-interruption regression failed before its fix and
passed afterward. The author performed a self-review; an independent slice review remains pending
because this assignment prohibits subagents. PostgreSQL concurrency evidence,
delivery/maintenance integration and recovery resumption remain future work.
The API unit-test pre-commit hook initially exceeded 120s and 600s timeouts;
the subsequent unmodified commit attempt passed all applicable hooks and
created implementation commit `53a7e7a`.
Review fixes require a matching persisted operation-to-step binding or attempt,
reject conflicting identities, and conservatively treat every interrupted
operation as unsettled. The corresponding missing/conflicting identity and
missing tool-attempt regressions were observed failing before the fix and
passing afterward. A bounded 120s verbose API run reached 17% with no failed
test or 30s stalled-test traceback; it did not finish the suite.

- [ ] **8B.1 Write failing tests:** extend `test_task_decisions.py` with `test_park_preserves_completed_work_and_releases_claim`, `test_preplan_wait_binds_pinned_context_digest`, `test_park_replay_keeps_request_identity`, `test_expiry_cancels_recovery_wait_without_dispatch`. Assert `ended_at is None`, no owner/lease, one open request, unchanged attempts/output refs/counters and no provider calls. Recovery retains the explicit unsettled operation; ordinary waits cannot conceal in-flight effects.
- [ ] **8B.2 Run red:** `uv run pytest -q tests/test_task_decisions.py tests/test_task_state.py`; expect missing parking behavior, not external-service failures.
- [ ] **8B.3 Implement:** implement `park_task_wait` with scope→run/job→task/decision locks and refreshed claim/fence checks. Create pre-plan context from pinned execution if needed; revision 0 binds its context digest. Use `waiting_plan`/`waiting_input`/`waiting_config`; recovery maps to `waiting_input`/`last_disposition="uncertain"` with the same 24-hour input-decision expiry. Persist state/decision/event and fence/release ownership before acknowledgement. Never mark waiting complete. Extend expiry's kind mapping without dispatch or sandbox mutation.
- [ ] **8B.4 Run green:** repeat the red command and Ruff on `src/openship/runs/tasks/decisions.py`; verify transaction rollback leaves no half-parked state.
- [ ] **8B.5 Commit:** use scanned file-based subject `feat: park task decisions durably`, obtain a slice review and record evidence here.

### Slice 8C — apply a choice with one local transaction

**Implementation and review evidence:** Ordinary decisions, recovery abort and
narrowly eligible repeat/continue intents are implemented. The focused SQLite
decision, task-state and contract run passed 88 tests; Ruff passed for the
decision module and tests. Confirmation rollback and recovery replay were
tested. Recovery continuations require a bound admitted operation with no
physical attempt or uncertain sandbox reservation; previously dispatched
operations fail closed. The controller adjudicated that old waits without pin
evidence remain visibly blocked rather than inventing pins; parking replay
preserves their original identity. The controller reports PostgreSQL concurrency
after alias reservation: 1 passed, 2 deselected, 2 warnings, and the scoped re-review
found no new critical/important defects, so gates 8C.1–8C.5 are checked. The controller
reran the complete authorized PostgreSQL Task 8 lane afterwards with 4 passed, 2 warnings.
See the 8C report for evidence and the credential-safe
test warning. Task 15 remains responsible for actual recovery graph execution.

- [x] **8C.1 Write failing tests:** add `test_crash_before_choice_commit_reprompts_same_request`, `test_lost_response_replays_same_decision_once`, `test_conflicting_stale_expired_wrong_actor_and_bare_yes_do_not_release`, `test_cancel_wins_over_confirmation`, `test_configuration_resume_cannot_replace_pins`, `test_confirm_cannot_consume_recovery_request`, and PostgreSQL `test_two_decisions_cannot_enqueue_twice`. Assert rollback leaves request open with zero new outbox rows; replay has one command/dispatch generation; wrong actor is 404, stale/conflicting command 409; abort enqueues nothing; no command handler calls the gateway.
- [x] **8C.2 Run red:** `uv run pytest -q tests/test_task_decisions.py`; authorized DB: `uv run pytest -q -m postgres tests/integration/test_task_decisions_postgres.py`.
- [x] **8C.3 Implement:** implement `apply_decision` using existing connector-resume transaction/outbox patterns, not its zero-operations-only helper. Authorize owned scope before replay lookup; return an already committed result without another transition. Under locks validate request kind/revision/digest, actor, cancellation and execution eligibility. One local transaction records command/result, consumes request, updates task/run/job, advances dispatch generation, appends event and `enqueue_run` intent. Reject/abort cancel remaining work even when execution configuration is unavailable, without an undo claim or enqueue. Recovery repeat/continue require backend eligibility against the bound report; Task 15 consumes their intent in the recovery-aware graph. Changed eligibility blocks/re-presents a request, never selects a substitute.
- [x] **8C.4 Run green:** repeat both red commands and Ruff on `src/openship/runs/tasks/decisions.py`; inspect rollback, replay and concurrent-decision counts separately.
- [x] **8C.5 Commit:** use scanned file-based subject `feat: consume task decisions through the existing outbox`, obtain a slice review and record evidence here.

### Slice 8D — HTTP and delivery/maintenance wiring

**Partial progress (2026-11-09):** Added authenticated decision route, durable-wait delivery and exhaustion settlement, maintenance expiry, cancellation, worker wait guard, decision event validation and shared cutover/restore classification. SQLite task/state lane: 87 passed; messaging regressions: 157 passed; connector blockers: 13 passed. Tests cover pool reservation reuse, HTTP confirmation/replay and wrong owner, and incomplete wait reconciliation. PostgreSQL not run (credentials unavailable). Slice remains open pending authorized PostgreSQL, end-to-end restore/cutover and broker-fault tests, worker/consumer slot integration and review. Recovery waits with active operations remain conservatively blocked during cutover; Task 15 recovery execution remains disabled. See `task-8d-report.md` in the task handoff directory.

**8D review follow-up:** Decision event schemas reject undeclared fields, preventing new sensitive extras from being persisted. Added unauthenticated HTTP and consumer-to-single-slot reuse tests, and a PostgreSQL cancellation/confirmation race test (collected only; authorized lane still required). Focused task/delivery/consumer/claim suite: 187 passed; event service/routes/tool events: 27 passed. Previously persisted event payloads are not rewritten by this change. Direct end-to-end restore/cutover and broker-fault qualification remain open.

**8D verification pass (2026-11-09, commit `8d72347`):** Cutover/restore is now exercised end to end in
`tests/test_messaging_cutover_preview.py` (`test_cutover_apply_preserves_a_parked_task_wait`,
`test_rollback_restore_never_readmits_a_parked_task_wait`,
`test_cutover_apply_refuses_a_task_wait_with_an_unsettled_operation`): apply preserves the wait,
creates no command for it, restore rotates the epoch without re-admitting it, and an admitted
operation beside a wait blocks preview and refuses apply. These passed on first run and are
descriptive coverage, not new red/green. The committed pool test (`test_task_wait_delivery.py:461-484`)
already proves real `ExecutionPool` capacity reuse. The controller's authorized PostgreSQL Task 8
lane now passes 4 tests, 2 existing warnings. Broker faults and live lanes stay Task 15's gate.
Gates 8D.1–8D.4 are checked; 8D.5 waits for this slice commit plus the whole-branch review, which
is the review gate for inline execution.

- [x] **8D.1 Write failing tests:** create `test_task_wait_delivery.py` tests `test_wait_ack_releases_slot_without_terminal_message`, `test_stale_delivery_cannot_claim_waiting_task`, `test_expiry_runs_without_worker_slot`, `test_cancel_supersedes_pending_decision`. Assert parked wait leaves capacity available, no terminal message, stale deliveries disposed and cancelled requests unable to enqueue.
- [x] **8D.2 Run red:** `uv run pytest -q tests/test_task_wait_delivery.py tests/test_task_decisions.py`; expect missing lifecycle behavior.
- [x] **8D.3 Implement:** add authenticated route/router registration with no tool dispatch. Worker/delivery/exhaustion distinguish parked waits from completion, retryable failures and delivery exhaustion; acknowledge only after durable parking. Use the existing RecoverySupervisor tick for expiry. Extend cancellation/cutover/restore/active-run predicates while preserving connector behavior. Register needed decision event schemas in `events/schemas.py`; Task 14 later extends public presentation. Recovery choices still require Task 15 before rollout.
- [x] **8D.4 Run green:** repeat the red command, Task 8 PostgreSQL lane, `uv run pytest -q tests/test_messaging_consumer.py tests/test_messaging_command_claim.py tests/test_messaging_exhaustion.py tests/test_messaging_maintenance.py`, and separately `uv run pytest -q tests/connectors/test_blockers.py`. Run Ruff on changed modules. Record exact outcomes and remaining broker faults under Task 15, not an all-skipped pass.
- [ ] **8D.5 Commit:** use scanned file-based subject `feat: integrate task waits with delivery and maintenance`, obtain a slice review and record evidence here. Task 8 completes only after all four slice gates pass.

## Task 9: Root budgets, active execution windows and stable tool-call admission

**Dependencies:** Tasks 4, 6, 8. **Deliverable:** Planning/repair/waits share honest bounded accounting without the accidental two-call/three-attempt legacy ceiling.

**Files:** Create `apps/api/src/openship/runs/tasks/clock.py`; modify `runs/budget.py`, `queue.py`, `transcript.py`, `worker.py`, `tasks/routing.py`, `providers/registry.py`, `config.py`; create `apps/api/tests/test_task_budgets.py`; extend `tests/providers/test_tool_budgets.py`.

**Interfaces:** Produces `open_task_window(db: Session, claim: Claim, *, now: datetime) -> datetime` (effective deadline), `close_task_window(db: Session, claim: Claim, *, now: datetime) -> None` in `clock.py`. Extend `RunBudget` for v5 using existing `reserve_generation` / `admit_tool`; add `ProviderRegistry.resolve_task(profile_id: str|None, fallback_ids: Sequence[str]=(), *, task_limits: RunLimits) -> RunModelConfig`, mirroring existing `pin_selection` authorization while applying explicit profile limits before pinning. Retain original `ProviderProfile` declarations in the registry alongside legacy pinned profiles, so task pinning never loses which limits were explicit. Do not mutate a legacy already-pinned configuration to inflate it; include task limits in the registry cache key.

- [x] **1. Write failing budget tests.** `test_all_inference_and_repairs_share_root_caps`, `test_tool_count_is_not_model_turn_ordinal`, `test_human_wait_excludes_time_but_not_previous_consumption`, `test_crash_does_not_refund_unsettled_active_window`, `test_task_defaults_respect_explicit_lower_provider_limits`, `test_recovery_diagnosis_and_user_repeat_keep_root_consumption`, `test_lowered_repair_limit_blocks_automatic_and_user_repeat`. Assert:
  ```python
  assert effective.max_tool_calls == 16 and effective.max_model_turns == 20
  assert effective.max_generation_attempts == 24 and effective.max_total_output_tokens == 48_000
  assert effective.max_output_tokens_per_request == 4_000
  assert active_seconds_after_wait == active_seconds_before_wait
  assert explicit_profile_limit_2.max_attempts == 2
  assert counters_after_reapproval == counters_before_reapproval
  assert repeat_at_attempt_ceiling_allowed is False
  ```
- [x] **2. Run red:** `uv run pytest -q tests/test_task_budgets.py tests/providers/test_tool_budgets.py` from `apps/api`.
- [x] **3. Implement task-scoped accounting.** New task defaults replace only old path-specific defaults (2 tools/3 turns and implicit 3 attempts), never explicit operator/profile caps; construct the v5 provider snapshot from declared profiles with `RunLimits(max_attempts=24, ...)` before applying those caps. Legacy registry resolution stays unchanged. Expose a lowering-only `AGENTIC_MAX_GENERATION_ATTEMPTS` default 24; reject values above 24. Count durable generation reservations, globally increasing model turns and admitted unique tool-call IDs separately. Continue using existing transcript-to-operation IDs (`model:<turn>:call:0`) and `ToolOperation` uniqueness; associate those IDs with plan step/attempt rows rather than replacing them with another ledger. Persist active interval start/deadline and accumulated milliseconds in task state; atomically close on durable human wait, resume with remaining active time and revalidated credentials. On crash conservatively charge the unfinished interval up to fence/lease-loss or its deadline, never reset/credit it from an old checkpoint. Legacy wall deadlines stay unchanged. Reserve enough budget for verification or stop honestly; deterministic tracking/routing uses no model reservation.

  Count recovery diagnosis inference in the same root budget. Re-prompting an existing request and waiting for recovery consume no fresh tool reservation or active execution time. Each authorized repeat consumes a new admitted tool attempt and any needed inference, within the same maximum of 3 total attempts per step (or lower explicit ceiling); it does not erase earlier charges or require classifying uncertainty as a known repair. `continue_next` records a skip without refunding consumption. Feed lowering-only effective repair/attempt limits into routing via the validated budget decision, replacing the Task 4 hardcoded-default seam. If there is no inference budget for diagnosis, retain the deterministic evidence-only report and user choices allowed by current controls, never add a secret recovery allowance.
- [x] **4. Run green:** repeat tests and existing run/provider budget regressions. Expected: 17th unique tool call, 21st model turn, 25th generation and output reservations over 48,000 stop; duplicate transport replay consumes no second tool admission; waits cannot reset tokens/repairs. Record Checkpoint 3.
- [ ] **5. Commit** with subject `feat: account for cumulative agentic task execution`; record evidence.

  **Checkpoint 3 evidence (2026-11-09, inline execution):** Red was captured by `git stash push` of the seven modified source modules (the new `clock.py` and the new test file stayed on disk, untracked) and running `uv run pytest -q tests/test_task_budgets.py tests/providers/test_tool_budgets.py` from `apps/api`: 5 failed, 10 passed. The two active-window tests were not a genuine RED because `clock.py` is new code present during that stashed red run; they are recorded as descriptive coverage, not new-red. Green on the same command after restoring the implementation: 16 passed, 1 warning, 8.42s (the 7 new task-budget tests plus the 9 provider budget tests, including the new `test_v5_budget_ignores_the_legacy_per_loop_defaults`). Regressions green: task+provider lanes 397 passed (151.22s); messaging/cutover/wait batch 182 passed (60.66s); execution/worker/transcript batch 163 passed (59.12s). `uv run ruff check` clean on all eight changed source modules and both test files. No PostgreSQL lane exists for Task 9 (no postgres-marked tests added). Wiring note: closing the durable-wait window required one line in `tasks/decisions.py` (`close_active_window` inside `park_task_wait`), outside the plan's Task 9 file list. The window deadline comes from the run's pinned `ExecutionConfigV5.loop_limits.deadline_seconds`, never a fresh setting; effective ceilings come from pinned `task_limits`, lowered only by explicit profile caps and lowering-only `AGENTIC_MAX_GENERATION_ATTEMPTS` (default 24, `le=24`). Gate 5 stays open until this commit plus the single whole-branch review lands.

## Task 10: Bounded reasoning/planning inference and request classification

**Dependencies:** Tasks 4, 6–9. **Deliverable:** Adapter-independent validated assessments/plans, with ordinary chat and material clarification preserved.

**Files:** Create `apps/api/src/openship/runs/tasks/inference.py`; modify `tasks/contracts.py`, `tasks/routing.py`; create `apps/api/tests/test_task_inference.py`; add `apps/api/tests/fixtures/agentic_tasks/` controlled JSON/log/assessment/plan fixtures.

**Interfaces:** Produces `assess_task(invocation, progress: TaskProgress, observations: tuple[PublishedToolResult,...]) -> TaskAssessment`; `plan_task(invocation, progress: TaskProgress, assessment: TaskAssessment) -> TaskPlan`. Use existing `run_model_turn` and attempt recorder/budget; `TaskAssessment.classification` is `informational|clarification|task|blocked`. Runtime may ask for assessment+plan in one validated structured text envelope, not a new model-selected function tool.

- [ ] **1. Write failing inference tests.** `test_greeting_answers_without_confirmation`, `test_actionable_request_cannot_dispatch_during_planning`, `test_missing_units_requests_focused_input`, `test_malformed_envelope_consumes_attempts`, `test_untrusted_log_instructions_do_not_expand_plan_authority`, `test_no_four_model_calls_for_deterministic_progress`, `test_uncertain_assessment_diagnoses_without_repeating_command`, `test_recovery_recommendation_is_not_user_consent`. Assert:
  ```python
  assert gateway_calls == []  # all planning and clarification before confirmation
  assert greeting_decision is None
  assert accepted_plan.steps[-1].expected_outputs == ("chart.png", "averages.json")
  assert no_progress_route_inference_calls == 0
  assert uncertain_diagnosis_gateway_calls == []
  assert recommendation_consumed_decision_count == 0
  ```
- [ ] **2. Run red:** `uv run pytest -q tests/test_task_inference.py tests/test_task_routing.py` from `apps/api`.
- [ ] **3. Implement bounded prompts/envelope validation.** Provide original intent, selected exact input refs, permitted capabilities/profile purposes and bounded labelled untrusted observations. Native provider structured output is optional; parse strict one-object JSON text, reject duplicate keys/invalid refs and consume generation attempts for corrections. No tools available to classification/planning requests, no hidden download/read preflight. Informational responses have no operations or plan gate; any proposed execution must classify as task and obtain the selected mode's gate. Clarifications have a focused public prompt and expected answer constraints. Record accepted model turn/transcript before consuming its proposal; on replay use that record, not a fresh plan. Retain adapter-owned private continuation internally, never copy it into TaskAssessment/events.

  Reuse `assess_task` with `progress.last_disposition="uncertain"` for bounded recovery reasoning over already retained records/logs/transcripts/output refs. Add optional public `unknowns`, `risks` and `recommended_recovery_action` to `TaskAssessment` with the report bounds from Task 8; no provider/debugging tool is exposed for this inference. The model may recommend, not select eligibility or consume a decision. Task 15 validates cited evidence and combines the assessment with backend eligibility into `TaskRecoveryReport`. Missing/malformed/budget-exhausted inference yields an explicit evidence-only fallback, not a model-memory reconstruction of successful work. Missing required tools/profiles are visible blockers, not silently omitted steps.
- [ ] **4. Run green:** repeat tests with synthetic Chat Completions and Responses continuations, no native structured-output capability. Expected: unknown capabilities/profile IDs, instruction-bearing evidence and malformed JSON cannot reach dispatch; ordinary text still works.
- [ ] **5. Commit** with subject `feat: reason and plan through validated task assessments`; record evidence.

## Task 11: Continuous v5 graph with committed-result replay

**Dependencies:** Tasks 5–10. **Deliverable:** One admitted outcome-level task progresses past downloads to its validated next steps without repeated prompting, and resumes ordinary human waits from durable progress. Full uncertain-operation diagnosis/choice execution is completed in Task 15; do not activate rollout before that gate.

**Files:** Create `apps/api/src/openship/runs/task_graph.py`; modify `runs/dispatch.py`, `worker.py`, `transcript.py`, `tasks/state.py`, `tasks/routing.py`; create `apps/api/tests/test_task_graph.py`, `tests/integration/test_task_graph_postgres.py`.

**Interfaces:** Produces `build_task_graph(checkpointer=None, gateway_factory=None, invocation_factory=None)` registered only as `("chat",5)`, plus `record_task_call(db: Session, claim: Claim, *, binding: TaskCallBinding) -> TaskProgress` in `tasks/state.py`. Caller commits the accepted transcript call and its validated step binding before dispatch. Consumes existing gateway `execute_tool(claim, context, logical_call_id, ref, arguments, inputs, control)`, Task 6 state, Task 8 decisions/bindings, Task 9 budgets, Task 10 inference. Invocation-only objects never enter checkpoint state. `TaskGraphState` stores run identity, expected plan revision and serializable cursor, not independent authoritative task edits.

- [ ] **1. Write failing graph tests.** `test_copilot_no_dispatch_before_exact_confirmation`, `test_auto_announces_plan_then_downloads_executes_and_verifies`, `test_one_confirmation_allows_multiple_steps`, `test_committed_download_replayed_without_refetch_after_restart`, `test_assistant_final_text_does_not_complete_missing_outputs`, `test_lost_uncommitted_approval_shows_same_plan_without_restart`, `test_plan_growth_uses_fixed_nodes_and_persisted_step_progress`, `test_call_binding_survives_dispatch_without_result_checkpoint`. Assert:
  ```python
  assert pre_confirmation_operations == 0
  assert capability_calls == ["download_file", "python_execute"]
  assert download_dispatches == python_dispatches == 1  # replay same committed graph work
  assert user_programming_followups == 0 and final_task_status == "completed"
  assert after_reapproval.completed_operation_ids == before_crash.completed_operation_ids
  assert graph_node_names_after_new_plan == graph_node_names_before_new_plan
  assert interrupted_operation.logical_call_id == progress.call_bindings[-1].logical_call_id
  ```
- [ ] **2. Run red:** unit `uv run pytest -q tests/test_task_graph.py`; authorized DB `uv run pytest -q -m postgres tests/integration/test_task_graph_postgres.py` from `apps/api`.
- [ ] **3. Wire explicit graph nodes.** Separate classify/assess, propose-plan, persist/present-plan, wait, generate-current-step-call, gateway dispatch, commit tracking and verify/switch. Persist accepted assessments/plans/calls before progression. A wait node returns a parked disposition to the worker; the worker must not emit its ordinary final message/completion for it. Resume rebuilds from authoritative state and saved transcript/committed operations; an out-of-date checkpoint cannot regenerate approved plans or repeat completed operations. Any LangGraph interrupt resume uses the same persisted thread and a previously validated decision; no model/provider side effect before an interrupt assumed once-only. Recheck policy/fence/current profile compatibility at every dispatch. Treat checkpoint-without-committed-result after possible dispatch as uncertainty, not absence of work. Allow each step bounded multiple tool attempts where needed, all associated with step/attempt identity and the existing operation. Publish task progress before moving to successors; do not infer progress from narrative text.

  Keep graph topology fixed: plan steps and revisions are data, not newly generated LangGraph nodes. Restore task/step/attempt/output/skip/decision state from durable records before selecting work, including when no useful graph checkpoint exists. Gate `("chat",5)` against an `ExecutionConfigV5` snapshot; mismatches block, never fall back to legacy requirements. An uncommitted approval leaves the same request open for re-presentation; no automatic replan or replay of prior steps. Preserve interrupted operation identity before leaving a worker, even when the result is not settled; never interpret a missing result as a never-dispatched call. Until Task 15's recovery integration is present, uncertain work remains visibly blocked with no automatic repeat/skip. Ordinary approved continuation may execute only the next unfinished, dependency-satisfied step.
- [ ] **4. Run green:** repeat unit/DB tests plus `uv run pytest -q tests/test_agent_tool_loop.py tests/test_task_admission.py`. Expected: graph v1–v4 route identically or block on exact incompatible refs, while only v5 uses plan gates. At most one user-facing final message per run; title-generation instructions do not contaminate task tool arguments.
- [ ] **5. Commit** with subject `feat: execute outcome-driven tasks continuously`; record replay/fence evidence.

## Task 12: Evidence-backed verification, bounded repair and material revisions

**Dependencies:** Tasks 6, 9–11. **Deliverable:** Known repairable failures recover within scope; false completion and uncertain replay stop.

**Files:** Create `apps/api/src/openship/runs/tasks/verification.py`; modify `tasks/inference.py`, `tasks/routing.py`, `tasks/state.py`, `tasks/projections.py`, `task_graph.py`; create `apps/api/tests/test_task_verification.py`, `test_task_repairs.py`.

**Interfaces:** Produces `verify_step(db: Session, *, run_id: UUID, step_id: str, assessment: TaskAssessment) -> VerificationReport`, `verify_task(db: Session, *, run_id: UUID, assessment: TaskAssessment) -> VerificationReport`; `classify_revision(previous: TaskPlan, proposed: TaskPlan) -> Literal["repair","material","expanded_scope"]` in `routing.py`. `VerificationReport` contains typed passed/missing criteria and exact evidence refs, never model-authored artifact IDs accepted without lookup.

- [ ] **1. Write failing verification/repair tests.** `test_zero_exit_without_chart_is_not_completion`, `test_wrong_averages_fail_independent_check`, `test_cross_scope_or_changed_artifact_fails_verification`, `test_known_keyerror_repairs_twice_at_most`, `test_repeated_ineffective_repairs_stop`, `test_profile_or_approach_revision_requires_new_copilot_confirmation`, `test_unknown_dispatch_policy_denial_and_cancel_never_repair`, `test_user_skip_is_not_verified_success`, `test_missing_skipped_output_blocks_dependent_step`, `test_auto_uncertainty_cannot_use_known_failure_repair`. Pin:
  ```python
  assert repairs_after_initial <= 2 and total_attempts_for_step <= 3
  assert missing_chart_report.passed is False
  assert current_decision.plan_revision == prior_revision + 1
  assert uncertain_execution_new_dispatches == 0
  assert skipped_step_id not in progress.verified_steps
  assert continue_with_missing_required_input_allowed is False
  ```
- [ ] **2. Run red:** `uv run pytest -q tests/test_task_verification.py tests/test_task_repairs.py` from `apps/api`.
- [ ] **3. Implement verification and revision policy.** Backend validates operation status, declared outputs, exact scope/hash/media/bytes and recorded provenance before semantic assessment. Model criteria must cite those refs; a semantic failure cannot be renamed success by a final answer. A known failed exit/result permits evidence-informed corrected source/input handling only within the reviewed step/profile/authority; corrected bytes get a new admitted attempt and exact source capture. Preserve all prior outputs/attempts. Compare normalized failure code plus relevant evidence digest for no-progress stops. Material changes create a visible new plan/review digest and supersede approval; Copilot gates remaining changed work, Auto stays within original intent. Expanded sources/scope always asks the user regardless of mode. Emit truthful partial/blocked/failed/budget-limited final disposition; do not silently substitute a working URL or promise an output that failed validation.

  Make routing use `skipped_steps` separately from `verified_steps`: a user skip is not automatic success and does not manufacture evidence. Dependencies on skipped steps are satisfied only by independently validated required inputs already available in scope; otherwise wait for user input or a visible plan revision. If skipped work leaves a required deliverable uncovered, final verification remains partial/blocked, not completed. Safe next-step selection after an explicit choice must not reopen the same recovery request in a loop, but unresolved uncertainty/history remains visible. A material revision must preserve prior attempts/evidence for retained logical steps and cannot discard a known completed step merely to rerun it. Task 15 supplies the explicit recovery choice; these validators enforce its limits.
- [ ] **4. Run green:** repeat tests and existing sandbox output/publication tests. Expected: wrong/missing/oversize artifacts, injected stdout, denied authority and uncertainty without an explicit eligible user choice never advance execution. A user skip is recorded but never passes output verification on its own. Record Checkpoint 4 with one synthetic known repair and one honest source blocker.
- [ ] **5. Commit** with subject `feat: verify task outcomes and bound evidence-informed repair`; record evidence.

## Task 13: Persistent Chat UI mode selection

**Dependencies:** Task 7. **Deliverable:** Accessible per-account selector with honest save state and exact-mode sends.

**Files:** Create `apps/web/src/stores/agentMode.ts`, `components/chat/AgentModeSelector.vue`, `tests/AgentModeStore.test.ts`, `tests/AgentModeSelector.test.ts`; modify `services/api.ts`, `types/index.ts`, `stores/auth.ts`, `stores/chat.ts`, `components/chat/ChatInput.vue`.

**Interfaces:** Produces `AgentMode = 'auto'|'copilot'`, `AgentPreferences`, `preferencesApi.get()/update(payload)`; `useAgentModeStore()` with `load(): Promise<void>`, `save(mode: AgentMode): Promise<void>`, `reset(): void`, `submissionSelection(): {agent_mode: AgentMode; agent_mode_revision: number}`. Consumes Task 7 API. Store has committed selection separate from unsaved/pending selection; no authoritative localStorage preference.

- [ ] **1. Write failing store/component tests.** `loadsAccountPreferenceAfterReload`, `failedSaveKeepsCommittedModeAndBlocksAmbiguousSend`, `staleBrowserGetsConflictNotSilentAutoSend`, `logoutDiscardsLatePriorAccountResponse`, `retryKeepsOriginalModePayload`, `switchingToAutoDoesNotConsumeRecoveryDecision`. Assert:
  ```typescript
  expect(store.mode).toBe('copilot')
  expect(retryPayload).toEqual(firstPayload)
  expect(submitCalls).toHaveLength(0) // preference save/conflict unresolved
  expect(selector.attributes('aria-label')).toBe('Agent execution mode')
  expect(taskDecisionCallsAfterModeToggle).toHaveLength(0)
  ```
- [ ] **2. Run red:** from `apps/web`, `pnpm exec vitest run tests/AgentModeStore.test.ts tests/AgentModeSelector.test.ts`.
- [ ] **3. Implement selector/API/store integration.** Restore on authenticated app load, including reload with token but no in-memory user; reset/generation-guard pending loads on logout/account switch. Display `Copilot`/`Auto`, pending/error and “Applies to new tasks”; never show successful Auto save before server acknowledgement. Disable task submission while selection is unknown/saving/conflicted. On preference-load failure, offer retry or an explicit text-only send with tools disabled and no mode override; ordinary chat remains usable without guessing task mode. Extend the existing exact pendingRequests payload; do not rewrite a retry after selection changes. A stale send surfaces the conflict and refreshes for a deliberate new send. Mode toggles send no task-decision command.
- [ ] **4. Run green:** repeat Vitest command; `pnpm typecheck`; `pnpm exec vitest run tests/ChatInput.test.ts tests/ChatStore.test.ts tests/AuthPages.test.ts`. Expected: keyboard selection and account isolation pass; existing messages/retries remain correct.
- [ ] **5. Commit** with subject `feat: add persistent Auto and Copilot selection`; record evidence.

## Task 14: Durable plan, decision and progress presentation

**Dependencies:** Tasks 6, 8, 11–13. **Deliverable:** Reload/reconnect shows the same plan/progress and binds all user responses to exact decisions.

**Files:** Modify `apps/api/src/openship/events/schemas.py`, `service.py`, `runs/schemas.py`, `routes.py`, `tasks/projections.py`; create `apps/web/src/components/chat/TaskPlan.vue`, `TaskDecision.vue`, `tests/TaskPlan.test.ts`, `tests/TaskDecision.test.ts`, `tests/TaskProgressStore.test.ts`; modify web types/services/chat store, ChatStream/RunCard/ChatInput and ChatView; create `apps/api/tests/test_task_projections.py`.

**Interfaces:** Produces `TaskSummary` on run response and conversation snapshot, plus versioned `task.plan_proposed`, `task.decision_requested`, `task.decision_resolved`, `task.progress`, `task.disposition` events. Each carries run ID, plan revision and bounded public projection; use the existing conversation `Event.sequence`/snapshot cursor for monotonic updates, not a new task-revision counter conflated with plan revision. Web produces `submitTaskDecision(runId: string, command: TaskDecisionCommand): Promise<void>` and reduces by run/plan revision/event cursor. Projection includes captured mode, plan revision/digest, step statuses/evidence links, skipped steps, safe blocker and active pending decision. A `recovery` pending decision includes Task 8's report, eligibility/blockers and diagnosis digest, never runtime bindings, internal call bindings or private continuation. Ordinary plan-confirmation digest remains the plan's review digest; recovery buttons send the recovery request's `expected_digest` instead.

- [ ] **1. Write failing projection/UI tests.** `test_snapshot_matches_event_replay_without_private_fields`; frontend `reconnectDoesNotDuplicatePlanOrFinalMessage`, `oldRevisionButtonsCannotApproveNewPlan`, `answerAndContextBoundChatUseSameDecisionApi`, `repairedAndPartialOutputsRemainVisible`, `rejectCancelExpiryAndReadOnlyScopeDisableActions`, `unrecordedApprovalIsAskedAgainWithoutResettingProgress`, `autoRecoveryShowsDiagnosisAndThreeExplicitChoices`, `unavailableRecoveryChoiceShowsReasonWithoutFallback`, `skippedStepIsNotRenderedCompleted`. Assert:
  ```typescript
  expect(decisionPayload.request_id).toBe(snapshot.task.pending_decision.request_id)
  expect(decisionPayload.expected_digest).toBe(snapshot.task.plan.review_digest)
  expect(renderedPlanRevisions).toEqual([1, 2])
  expect(visiblePrivateContinuation).toBe(false)
  expect(recoveryPayload.expected_digest).toBe(snapshot.task.pending_decision.expected_digest)
  expect(recoveryButtonLabels).toEqual(['Abort', 'Repeat the step', 'Continue to the next step'])
  expect(automaticRecoverySubmissions).toHaveLength(0)
  ```
- [ ] **2. Run red:** API `uv run pytest -q tests/test_task_projections.py`; web `pnpm exec vitest run tests/TaskPlan.test.ts tests/TaskDecision.test.ts tests/TaskProgressStore.test.ts` in their directories.
- [ ] **3. Implement snapshots/events/components.** Public projections derive from the same authoritative task query for GET and snapshot. Show pending/running/repaired/completed/blocked step states and artifact links; use existing safe Markdown/image handling, not raw HTML from evidence. Confirmation/rejection buttons and clarification form generate one stable command ID and bind exact request/revision/digest. For conversational response, activate explicit reply-to-decision context in the composer; recognize only deterministic affirmative/reject text in that context, route typed answers through the same API, and leave unbound “yes” nonapproving. Stale/repeated/rejected responses show recorded/current state, never a second local task. Preserve archived/read-only ownership checks and cancellation. Keep plan events distinct from assistant-final messages and title-generation payloads.

  Render known/unknown facts, retained evidence, risks, optional recommendation and all three explicit recovery options in `TaskDecision.vue`. Disable unavailable options with backend reasons (including possible live command, missing dependencies, policy or attempt limits); never preselect or submit the recommendation. In recovery reply context, only explicit abort/repeat/continue choices map to their matching action; a bare “yes” does not choose among them. Show `skipped`/unresolved work distinctly from verified completion and preserve the diagnostic/choice history after resolution. Reconnect after uncommitted approval re-presents the same request with completed steps intact; committed approval renders its recorded outcome. The UI may offer a deliberate retry of a failed HTTP command, never silently turn it into a different action.
- [ ] **4. Run green:** repeat targeted tests; `pnpm typecheck`; existing ChatSSE/ChatTimeline/RunCard/RunResume/Markdown-rendering regressions. Expected: ordered and duplicated events plus snapshot reload converge. Record Checkpoint 5.
- [ ] **5. Commit** with subject `feat: show durable task plans decisions and progress`; record evidence.

## Task 15: Diagnose uncertain execution and apply explicit user recovery

**Dependencies:** Tasks 8–12, 14. **Deliverable:** In both Auto and Copilot, uncertainty yields an evidence-backed diagnosis and a durable abort/repeat/continue decision. No agent-selected recovery, replayed completed steps or budget reset. Legacy A/B behavior remains separate.

**Files:** Create `apps/api/src/openship/runs/tasks/recovery.py`, `apps/api/tests/test_task_recovery.py`, `tests/integration/test_task_recovery_postgres.py`, `test_task_recovery_jetstream.py`, `test_task_restore_postgres.py`; modify `runs/resume.py`, `queue.py`, `worker.py`, `task_graph.py`, `tasks/decisions.py`, `tasks/routing.py`, `tasks/state.py`, `tasks/projections.py`, `tasks/routes.py`, `messaging/recovery.py`, `messaging/migration.py`; extend existing chat-Python-resume and workspace-restore tests.

**Interfaces:** Produce `reconcile_task_projection(db: Session, *, run_id: UUID) -> TaskProgress` from committed operation/input/artifact identities, never model memory; `prepare_task_recovery(db: Session, *, run_id: UUID, assessment: TaskAssessment|None, now: datetime) -> TaskRecoveryReport` to validate retained evidence, compute backend eligibility and persist an immutable recovery decision/report. Caller owns transactions; no inference/provider calls inside them. Use Task 10's `assess_task` outside the transaction with a captured evidence snapshot, then revalidate that snapshot before storing the report. Produce `resume_task(db: Session, *, principal_id: UUID, run_id: UUID, command: ResumeChatPythonRequest) -> Run` for the existing v5 resume endpoint; legacy calls keep their old implementation. A (`last_step`) restores the same v5 root but cannot bypass a pending recovery choice; it presents/returns the recovery-required blocker, never translates A into `repeat_step`. B (`from_beginning`) remains a distinct deliberate new root with current account mode, fresh plan gate and prior-root lineage, not implicit approval. Recovery choices use Task 8's decision endpoint; there is no second command API.

**Execution slices:** implement/review diagnosis + persisted request first, choice-aware repeat/skip graph integration second, then broker/restore fault tests. Each slice has its own failing/green tests and verified commit; do not dispatch this entire task as an unbounded single assignment. Complete all steps below before marking Task 15 complete.

- [ ] **1. Write failing recovery/fault tests.** Unit names: `test_uncertain_auto_and_copilot_diagnose_before_user_choice`, `test_abort_retains_partial_outputs_and_never_claims_undo`, `test_repeat_creates_new_attempt_without_replaying_completed_steps`, `test_continue_marks_skip_and_requires_dependency_inputs`, `test_recommendation_and_bare_yes_cannot_choose_recovery`, `test_uncertain_command_blocks_repeat_until_quiescent`, `test_no_inference_budget_shows_evidence_only_diagnosis`, `test_a_cannot_bypass_recovery_and_retains_root_budget`, `test_b_captures_current_mode_and_requires_copilot_gate`. PostgreSQL/broker/restore names: `test_approval_rollback_reprompts_same_request`, `test_commit_before_checkpoint_or_lost_ack_never_reexecutes`, `test_repeat_choice_redelivery_dispatches_new_attempt_once`, `test_restore_waiting_recovery_report_and_private_continuation`, `test_missing_restored_artifact_or_snapshot_blocks`. Assert:
  ```python
  assert dispatches_before_user_recovery_choice == 0  # both modes, including diagnosis
  assert repeat_attempt.operation_id != interrupted_attempt.operation_id
  assert original_completed_step_dispatches == 1
  assert skipped_step_id not in progress.verified_steps
  assert counters_after_recovery.tool_calls >= counters_before_recovery.tool_calls
  assert counters_after_recovery.generation_attempts >= counters_before_recovery.generation_attempts
  assert restored_waiting.request_id == backup_waiting.request_id
  ```
- [ ] **2. Run red:** unit `uv run pytest -q tests/test_task_recovery.py`; authorized PostgreSQL `uv run pytest -q -m postgres tests/integration/test_task_recovery_postgres.py tests/integration/test_task_restore_postgres.py`; authorized broker `uv run pytest -q -m jetstream tests/integration/test_task_recovery_jetstream.py`. All run in `apps/api`; selected missing prerequisites fail safely and never count as passing evidence. Write each slice's tests before its code rather than implementing all recovery at once.
- [ ] **3. Implement diagnosis, user-choice resumption and versioned recovery.** Reconcile root progress, exact source/input/output refs, operation identities and skipped work first. Preserve the interrupted attempt, including possible dispatch with no settled result. Diagnosis reads only retained scope-validated evidence; it cannot run the uncertain command as a probe. Budgeted inference runs outside locks; missing/invalid inference yields a labeled evidence-only report. Under existing recovery-supervisor/claim fences, persist report plus request as `waiting_input`/kind `recovery`, clear worker ownership and release delivery/capacity. Reuse a recorded current diagnosis after restart; do not repeatedly infer or guess the user's choice. Extend the existing supervisor for v5 recovery preparation, not another scheduler. Legacy interrupted statuses/paths remain untouched.

  Apply exact user choice through Task 8's local transaction/outbox, rechecking current evidence, quiescence, source/input bytes, pins, scope, policy and budget. If the prior command might still run, disable repeat and dependent/conflicting continuation; provider TTL or existing authoritative outcome evidence must establish safety, without renewal/deletion. If updated evidence changes the diagnosis/eligible actions, supersede and re-present a bound request; an old button cannot select the replacement. Abort cancels remaining work without claiming to undo effects. Repeat authorizes one distinct attempt of the same affected step and exact reviewed inputs/source/profile, with a new operation ID and cumulative charges; changed bytes/approach need normal repair/revision controls, not reuse of this approval. Continue records the unresolved step as skipped, validates successor inputs and never silently skips further steps. Missing inputs require clarification or a visible revision. Task 12 verification determines whether required deliverables are still incomplete.

  Extend `task_graph.py` to consume the committed recovery intent, clear the active recovery blocker only for that exact choice, and select the authorized unfinished work without replaying earlier completed operations. A crash before dispatch reuses that intent; after possible dispatch it must reconcile the new operation and, if uncertain again, obtain a new diagnosis/choice rather than reuse permission to repeat indefinitely. Preserve mode, root limits, plan and attempt history. B is separate new work with current mode; it cannot inherit confirmation or overwrite the prior task's recovery history.

  Restore task/skip/decision/report/command state with users/runs/checkpoints/artifacts/outbox/transport epoch. Fence old broker commands by generation/epoch; missing snapshot parks `waiting_config` and cannot repin. Missing evidence blocks repeat/continue or requires input, never fabricates success. Inject failures before decision commit, after decision+outbox commit, around operation dispatch/result commit, checkpoint save and acknowledgement. Re-prompt only uncommitted choices; recover committed choices/outbox without duplicate work.
- [ ] **4. Run green:** repeat all enabled lanes plus `tests/integration/test_chat_python_resume.py`, existing sandbox resume and workspace restore regressions. Expected: no dispatch before explicit recovery in either mode; exactly one execution per admitted attempt (an explicit repeat is a different attempt); no completed-step replay; skip is never verified success; one consumed choice per request; restored diagnosis and choice history retain identity. Record exact commands/counts and unrun lanes separately. Rollout remains disabled until the whole recovery gate is verified.
- [ ] **5. Commit** verified slices using subjects `feat: diagnose uncertain task execution`, `feat: resume tasks from explicit recovery choices`, and `test: qualify task recovery across crashes and restore`. Record evidence under the owning slice and leave incomplete lanes unchecked; no push, deployment or live probe is authorized by these commits.

## Task 16: Controlled weather/log journeys and browser qualification

**Dependencies:** Tasks 11–15. **Deliverable:** Repeatable end-to-end acceptance fixtures independent of model-specific weather routing.

**Files:** Create `apps/api/tests/full_stack/test_agentic_tasks.py`, `synthetic_task_worker.py`, `apps/web/tests/full-stack/agentic-tasks.spec.ts`; extend Task 10's `tests/fixtures/agentic_tasks/` and existing full-stack helpers only where necessary. Do not silently replace the old Phase 6B journey.

**Interfaces:** Consumes ordinary HTTP turn/preference/decision/snapshot APIs and real worker/gateway/event/broker paths. Only model transport and controlled external source service may be synthetic in this lane; record sandbox transport as real or synthetic explicitly. Add `OPENSHIP_FULL_STACK_AGENTIC_TASKS=1` selector; selected lane fails on missing prerequisites. Browser reuses the repository's `playwright.full-stack.config.ts` and API URL selectors.

- [ ] **1. Write failing journey fixtures/tests.** `test_weather_task_copilot_and_auto`, `test_invalid_source_is_honest_blocker`, `test_uploaded_logs_are_not_weather_special_case`, `test_browser_reload_preference_repair_and_revision`, `test_unrecorded_approval_reprompts_without_repeating_download`, `test_uncertain_task_requires_explicit_recovery_in_both_modes`. Browser cases cover all three choices, unavailable repeat while a command may live, and continue blocked by missing inputs. Fixture weather: city/month/temperature_c, London January `[4,6]`, February `[7,9]`, a non-London row that must be excluded, expected `{"January":5,"February":8}` °C. Logs: API durations `[100,200]`, worker `[50]`, expected service averages `{"api":150,"worker":50}` ms. Assert:
  ```python
  assert retained_averages == {"January": 5, "February": 8}
  assert decoded_png.width > 0 and decoded_png.height > 0
  assert copilot_confirmations == 1 and auto_confirmations == 0
  assert invalid_source_chart_count == 0 and programming_followup_count == 0
  assert uncertain_branch_dispatches_before_recovery_choice == 0
  assert repeat_branch_download_dispatches == 1  # retain earlier completed download
  assert missing_chart_skip_branch_final_status != "completed"
  ```
- [ ] **2. Run red:** from `apps/api`, with authorized disposable stack settings, `OPENSHIP_FULL_STACK_AGENTIC_TASKS=1 uv run pytest -q -m full_stack tests/full_stack/test_agentic_tasks.py`. Browser: from `apps/web`, `pnpm exec playwright test -c playwright.full-stack.config.ts tests/full-stack/agentic-tasks.spec.ts` against that harness. Expect the new missing journey behavior to fail before implementation.
- [ ] **3. Implement fixtures and harness integration.** Synthetic model follows the general typed protocol, supplies one known JSON-field repair, and separately proposes a material revision. Exercise Copilot gate before every first side effect and renewed gate for revision, Auto without per-step prompting, focused clarification, persistent account selection in another browser context, partial output and invalid source reporting. Use a controlled resolvable HTTPS fixture via the existing network-test seams; do not contact the illustrative invalid public URL. Independently calculate expected numbers outside generated Python; decode images and fetch retained artifacts through authenticated APIs. Include browser/API restart and event reconnect without duplicated final messages. No product code conditional on weather/log fixture names.

  Add deterministic fault points using Task 15's hooks: rollback an approval transaction, lose a committed response, and interrupt after possible Python dispatch. Confirm re-prompt preserves the same plan/request/download and that diagnosis precedes recovery buttons in both modes. Exercise abort with partial outputs retained; repeat with one new operation and no earlier-step replay; continue with an available independent successor and a separate missing-input blocker fixture. Keep skipped work visibly unresolved and final output claims truthful. Normal happy-path confirmation counts above exclude explicit fault-recovery choices. These are controlled fault fixtures, not a requirement to crash an unrelated live provider or stack.
- [ ] **4. Run green:** repeat journeys/browser lane plus all credential-free regressions below. Expected: explicit passing counts and provenance for every output, not merely an HTTP 200/process exit. Record synthetic versus real sandbox/model scope in `docs/validations.md`.
- [ ] **5. Commit** with subject `test: qualify continuous agentic task journeys`; record evidence and remaining live checks.

## Task 17: Authorized real-manager/live qualification and operator handoff

**Dependencies:** Tasks 1–16. **Deliverable:** Honest feature qualification and documented activation/rollback, with user acceptance separate.

**Files:** Create `apps/api/tests/live/test_agentic_task_journey.py`; extend `tests/live/test_sandbox_profiles.py` only additively for immutable-reference qualification; update `docs/operations/opensandbox.md`, `docs/development.md`, `docs/provider-support.md`, `docs/validations.md`, `docs/roadmap.md`, `.env.example`, `.env.prod.example` and this plan.

**Interfaces:** New live selector `OPENSHIP_LIVE_AGENTIC_TASKS=1`; explicit `OPENSHIP_LIVE_TOOL_PROFILE_ID` chooses an already supported tool-capable provider tuple, with fallbacks disabled for qualification. Require `OPENSHIP_LIVE_AGENTIC_PROFILES_FILE`, `OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT`, `OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE`, authorized disposable `TEST_POSTGRES_URL` and broker selectors below. Do not infer credentials/endpoints from unrelated installations or echo secret values.

- [ ] **1. Write the failing live assertions before running real calls.** `test_immutable_profile_reference_executes_through_manager`, `test_live_outcome_chart_in_both_modes`, `test_live_known_repair_invalid_source_and_uploaded_logs`. The tests must fail when explicitly selected without the required files/tuple/services, and skip only when unselected. Assert exact immutable image identity through manager response/engine inspection, chart+numeric output/provenance, user mode persistence, known repair and truthful invalid-source blocker. A manager accepting a SHA-shaped string without proving the selected image is insufficient.
- [ ] **2. Obtain authorization and run the enabled live lane.** From `apps/api`: `OPENSHIP_LIVE_AGENTIC_TASKS=1 uv run pytest -q -m provider_live tests/live/test_agentic_task_journey.py`. Run all four descriptor/selection cases offline; actual maintained-image smoke through the manager uses the existing separately authorized `OPENSHIP_LIVE_SANDBOX_PROFILES=1` selector. If the manager cannot execute the pinned engine-local image ID without pulling/retag fallback, block this gate and review the design; do not weaken to a mutable tag. Preserve earlier tag-based evidence as historical, not proof of immutable execution. Task 5's bounded profile-selection immutable-ID probe remains unrun and is claimed by this task.
- [ ] **3. Write activation and rollback instructions, then verify them in an authorized disposable stack.** Generate only through `sandbox-images build` consent/flag, validate `.gitignore`, drain using existing messaging gates/cutover, preserve DB/checkpoints/artifacts and pending decisions, recreate API/worker together, and verify both mounted file bytes/digests plus worker readiness before reopening execution/admission. API/worker mismatches block v5 dispatch. Writing YAML alone never recreates services. On downgrade/rollback: stop v5 admissions first; use only a runtime that understands existing v5/waits or keep them visibly parked until restored to compatible code/config. Do not downgrade schema with v5 state or restore only the DB without matching checkpoint/artifact/transport identities. Custom host paths and sandbox-disabled empty fallback each get real mount-byte evidence. Do not change credentials/provider lifetime or delete sandboxes as part of activation.

  Document the crash decision table: pre-commit approval → re-present current request; committed approval → recover existing outbox; possible tool dispatch → diagnosis and user choice, including in Auto. Runbooks describe disabled recovery options, attempt budgets, retained partial outputs, skip-versus-success and missing dependencies. Preserve migration 033/034, diagnosis request/command records and skipped-step state during coordinated backup/restore. An operator or UI refresh must not silently repin/restart a task. Keep rollout off until Task 15 recovery and Task 16 browser gates pass; pending live immutable-ID qualification still blocks activation. Any real fault injection/provider crash requires separate explicit authorization; record synthetic recovery evidence separately from real-manager/live journey evidence, leaving unrun live fault checks pending.
- [ ] **4. Run final verification and review evidence.** Use the full command matrix below; record actual tuple/model/API/profile snapshot/engine/platform, immutable image ID and artifact hashes without credentials in validation records. Keep fixture, live-qualified and user-accepted states distinct; add a Phase 7A feature qualification row instead of claiming every adapter now supports tasks. Update roadmap acceptance items only for demonstrated criteria. Have the user inspect chart/progress/known repair/source blocker plus the controlled recovery diagnosis and abort/repeat/continue views, and explicitly accept; leave acceptance pending otherwise.
- [ ] **5. Commit** verified qualification/docs with subject `docs: record agentic task qualification and rollout`. Whole-branch review must resolve correctness/security findings before integration. No push, PR, merge, worktree removal or deployment is authorized by this plan alone.

## Verification Commands and Required Environment

Run commands from the stated directory. They are execution instructions, **not results of drafting this plan**. Do not leak credentials by printing environment files or passing secrets in logged command arguments.

### Credential-free gates

```bash
# apps/api
uv run ruff check .
uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'

# apps/web
pnpm typecheck
pnpm test:run
pnpm build

# repository root
bash -n scripts/openship-stack
git diff --check
```

All new unit tests join the default gate. Retain existing tool-loop, transcript, provider-budget, policy/authority, download, output-publication, sandbox reuse/resume, image-builder, messaging and UI regressions. Do not use unconditional broad skips to make a lane green.

### Durable PostgreSQL and broker gates

- `TEST_POSTGRES_URL` selects an explicitly authorized disposable service. New owning integration modules create/drop only their own uniquely named test databases, following existing helpers; they never migrate a shared production DB. Mark DB tests `postgres`; add each owning migration fixture to the existing SQLite-autouse exclusion rather than changing the session fixture broadly.
- Reuse the disposable broker contract in `tests/integration/test_messaging_topology.py`: `TEST_NATS_URL`, protected `TEST_NATS_AUTH_FILE`, `TEST_NATS_SERVER_CONFIG`, and unique test stream/subject namespaces. Map role credentials to the application harness's `NATS_SERVERS`/`NATS_AUTH_FILE` only inside that harness; do not interchange test admin and worker roles. New explicitly selected broker tests fail with a safe prerequisite message when absent; no silent localhost/credential fallback. Mark them `jetstream`, and also `postgres` when both services are used.
- Run the exact per-task PostgreSQL commands first, then the full `uv run pytest -q -m postgres` lane on the disposable service. Broker recovery: `uv run pytest -q -m jetstream tests/integration/test_task_recovery_jetstream.py`. Do not interpret an all-skipped result as passed evidence.

### Full-stack and live gates

- Task 16 requires `OPENSHIP_FULL_STACK_AGENTIC_TASKS=1`, disposable PostgreSQL, isolated authenticated JetStream and a runnable API/worker/browser harness. The harness must label synthetic model/source/sandbox boundaries. A real sandbox lane additionally requires authorized endpoint/key and preloaded immutable profiles; missing selected prerequisites fail.
- Task 17 requires `OPENSHIP_LIVE_AGENTIC_TASKS=1`, the explicit provider profile and endpoints/protected key files named there, and a ready `logs-data` mapping built on the manager's actual serving store. Preserve existing provider support/secret binding rules. No paid provider, download/build or service restart during plan drafting.
- Live evidence is needed for both Copilot and Auto, retained PNG and independently verified numeric data, one known repair, invalid source and uploaded logs. Offline fixtures cover all four maintained profiles; partial operator enablement is allowed. Qualification scope must never imply arbitrary custom profiles or untested providers.

## Spec Coverage and Review Gates

| Spec sections | Owning tasks / evidence |
| --- | --- |
| §1–3 outcome, scope, ordinary chat and chart journey | 4, 10–12, 16–17 |
| §4 shared abilities and workflow boundaries | 4, 9–12; no new `agent_*` tools or saved graph edits |
| §5 durable plan/progress/confirmation, re-prompt and fixed-graph boundary | 6, 8A–8D, 11, 14–15 |
| §6 persistent modes and new-task-only capture | 7, 13–15 |
| §7 repair, material revisions, verification and diagnosis/user recovery | 8A/8C contracts, 9–12, 14–17 |
| §8 metadata, readiness, pinned selector and compatible reuse | 1, 3–5, 15, 17 |
| §9 local build consent, partial merge, safety and host source | 1–3, 17 |
| §10 Compose bootstrap and deliberate activation | 3, 15, 17 |
| §11 historical compatibility, restore, isolation and security | 5–9, 11–12, 14–15, 17 |
| §12 P1/P2/P3 working defaults | 4, 8–9, 12 |
| §12 P4/P5/P6/P7 working defaults | 1–3 |
| §12 P8 / §13 unit, DB, broker, UI, real stack and live acceptance | 5, 15–17; full verification matrix above |
| §14 scoped engineering, migration/activation/rollback and handoff | All tasks; explicit gates in 6–9, 15, 17 |

### Risks and stopping rules

- **Manager immutable local image support:** determine actual capability early in an authorized probe; Task 17 cannot pass on tag-only evidence. Incompatibility requires design review, not an unrecorded fallback.
- **Provider limits below the task corpus:** preserve explicit ceilings. Report budget-limited partial work or adjust the operator's reviewed profile; never silently grant extra attempts/tokens.
- **Projection/checkpoint disagreement:** only committed identity/evidence resolves it. Possible dispatch without a settled operation remains uncertain: diagnose and request the user's choice, even in Auto. A recommendation or checkpoint cursor cannot authorize repeat/skip. If no useful checkpoint exists, durable tracking still resumes from retained progress; missing evidence is a visible blocker, not permission to guess.
- **Codec bridge dependency:** mapping generation needs the explicitly synced API environment; existing builds/list/smoke still work without YAML writing. Do not start installing packages from an image build.
- **Acceptance availability:** unavailable broker/provider/engine/user review blocks only the corresponding evidence checkbox, not an excuse to claim all Phase 7A acceptance complete.

### Plan self-review and handoff

Before handing this plan off, verify file references, task dependencies, named interfaces and spec coverage; check that the five Review Focus cases have owning tests. All tasks end with a red/green cycle and an atomic verified commit; live/restore gates remain distinct. During implementation keep this plan current with evidence and remaining work, and revise it explicitly if an interface or acceptance requirement changes.

**Drafting evidence — 2026-11-08:** Inline spec/step/type review completed; document validation checked 71 local links/anchors, 17 ordered tasks with 85 unchecked steps, dependencies, 52 existing-or-declared test paths, working-default consistency and documentation-only scope. Whitespace checks and plan secret scanning passed. No application tests, builds, migrations, broker/live calls or stack changes were run as part of drafting; these checks do not establish implementation acceptance.

**Revision self-review:** Tasks 8–17 now distinguish lost uncommitted choice from committed choice and uncertain tool execution. Slice 8A owns additive schema/contracts; 8B parking; 8C transaction/outbox decisions; 8D delivery/maintenance. Tasks 9–12 own budgets, diagnosis inference and fixed-graph tracking/skip validation; 14 presents choices; 15 integrates recovery and fault/restore proof; 16–17 qualify and document it. Existing evidence is historical, not a claim that revised acceptance tests ran. No product code, migrations, service configuration or runtime changed in this revision.

**Revision validation:** The read-only documentation validator checks all local links/anchors, ordered task dependencies, unchanged historical Tasks 1–7, existing-or-declared test paths, Python assertion syntax, shared contract names and preserved limits. Remaining Task 8 slices and Tasks 9–17 acceptance remain unchecked. Whitespace and secret scans cover all four changed documents; these checks do not establish product-test or live acceptance evidence.

**Pending user gate:** review the revised Tasks 8–17 and aligned recovery spec. **Subagent-driven** execution remains selected, with GPT-6.1 Terra for subsequent subagents; do not ask for the execution method again. After review, resume at Slice 8A from committed `5d2ec7b`, preserving delivered Tasks 1–7 and Task 8's partial work. No push, PR, merge, real build, stack recreation or paid/live call is authorized by this documentation revision.
