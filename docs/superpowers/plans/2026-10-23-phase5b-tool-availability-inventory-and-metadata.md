---
type: plan
summary: Proposed Phase 5B implementation detail aligned with accepted 5A.1/5A.2, adding managed built-in tool availability, local bucket inventory and bounded bucket metadata.
date: 2026-10-23
status: in_progress
---

# Phase 5B Tool Availability, Inventory and Metadata Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` for sequential implementation. The user approved this addendum on 2026-10-23; implement Tasks 15–16 in order. Completion still awaits qualification and user acceptance.

**Goal:** Make configured built-in tools usable without maintaining a per-tool environment list, then add local bound-bucket inventory and evidence-backed metadata reads.

**Architecture:** Extend the maintained definition registry, existing designated-operator authorization, shared PostgreSQL policy, run admission/gateway and Global Settings. Keep the existing AWS broker, native SDK runner, resource identities and atomic evidence publication.

**Tech stack:** Existing FastAPI/Pydantic/SQLAlchemy/Alembic/PostgreSQL, boto3/botocore and Vue/Pinia/Vitest/pytest/Playwright.

**Spec:** [Phase 5 specification](../specs/2026-10-21-phase5-managed-connectors-and-s3-observations-design.md), especially sections 3.2 and 8. [Parent implementation plan](2026-10-21-phase5-managed-connectors-and-s3-observations.md).

## Accepted baseline and alignment review

| Accepted implementation | Consequence for 5B |
| --- | --- |
| Brave/Tavily are global; AWS is project-scoped | Global tool policy must not change Connector ownership or expose other projects' credentials, bindings or evidence. |
| One project search default; automatic alternate-provider retry after an error; manual selection always available | Reuse configuration v3's preferred search provider. Tool policy must not add per-provider defaults or bypass explicit manual selection. |
| Projects and conversations archive/restore; resource associations survive | Inventory and observation history remain readable under existing ownership rules. Archived conversations/projects cannot dispatch new work until restored. |
| Revoke removes active setup and purges secret bytes while retaining non-secret revisions | Inventory can describe retained local bindings without decrypting credentials; metadata must refuse revoked access. |
| UUID JSON requests and foreign-key insert ordering were corrected | Exercise actual HTTP requests and databases with foreign keys enabled; service-only tests with unenforced SQLite constraints are insufficient. |
| Connection checks submit before navigation and report disabled/admission errors | Preserve this behavior; eligibility updates must reach the composer, menu and setup actions consistently. |
| Explicit bucket selection bounds automatic AWS reads | Inventory results do not extend admitted `selected_resource_binding_refs`. A model cannot turn a newly listed bucket into an authorized cloud-read target. |
| Native SDK requests pass production destination checks; debug credential logs are suppressed | Test SDK requests through `AwsPinnedHttpTransport`, mocking the network below its validation rather than replacing the entire transport with `MockTransport`. |
| Shared AWS source-file digests currently cover all AWS handlers | Adding handlers will affect shared identity. Preserve exact historical refs or refuse them visibly; do not fabricate compatibility hashes or expand the storage tool's operations. |

5A.2 was accepted by the user on 2026-10-23 after live UI testing. Its acceptance record retains outstanding independent comparison and PostgreSQL evidence; this addendum does not turn those into completed checks. Live AWS requests by the implementer remain separately opt-in.

## Global constraints

- Reuse owner-only projects, designated installation operator, registry, queue, gateway, artifact storage and event/history projections.
- Preserve manifest schemas v1/v2 and execution configuration schemas v1/v2/v3, existing graph versions, exact retries, pinned Connector/binding revisions and all accepted search behavior.
- Register only installed distribution-owned qualified read tools. Registration is not permission to discover arbitrary SDK methods, execute imports or grant credentials.
- Policy updates take effect without API/worker restarts. Do not maintain independent effective allowlists in each process.
- `TOOL_ENABLED_IDS` is an optional deployment ceiling. Never silently ignore an explicit restriction or broaden a legacy chat-only installation during migration.
- Every AWS read still needs an eligible Connector and explicit owned target. Preserve search's existing Connector selection rules. No account-wide AWS discovery, object contents, writes, maintenance roles, background probes or automatic replay.
- No data deletion except secret purging. Archive/restore and revocation preserve resource associations, immutable observations and non-secret history.
- Preserve deadlines, cancellation, budgets, network pinning, response limits, secret-safe logs and fenced publication.

## Review focus

1. An old environment list continues silently hiding new tools: prove the one-time managed-settings cutover, and show an explicit restriction reason while a deployment ceiling remains active.
2. API and worker see different policy revisions: prove fail-closed dispatch and consistent refresh without restart, including disablement during a run.
3. Local inventory works only with working AWS credentials: prove no broker, key-file read, STS or other external request occurs, even for a binding whose Connector was revoked.
4. Inventory output silently authorizes another cloud target: prove metadata/storage admission remains limited to explicitly selected owned bindings.
5. Synthetic SDK tests bypass real transport validation: exercise STS, S3 location/versioning and CloudWatch through the production transport, including query paths, malformed destinations and debug-log secret sentinels.

## Task 15.1 — Shared installed-tool catalog and operator policy

**Files:** Add `capabilities/{policy,models}.py` and the next Alembic migration after `phase5a012`. Extend `capabilities/{catalog,contracts,routes}.py`, `config.py`, model registration, `execution/{admission,gateway}.py`, runtime availability publication and `runs/{service,agent_graph}.py`. Keep implementation helpers out of the credential broker.

**Interfaces:** `list_installed_tool_definitions()` enumerates maintained definitions; `read_effective_tool_policy(db)` returns a revision and effective IDs; `set_tool_policy(db, actor, capability_id, enabled, expected_revision)` performs an operator-only revision-checked update. Public catalog entries expose permission and a closed unavailable-reason code separately from Connector/target readiness. Existing `resolve_capability(ref, enabled_ids)` consumes the effective ID set instead of independently reparsing environment configuration.

- [ ] Write failing tests in `tests/test_tool_policy.py` and `tests/integration/test_tool_policy_postgres.py`: an installed definition appears without editing configuration; foreign/non-operator writes fail; concurrent writes use revision preconditions; API and worker agree after an update; missing policy state fails safely.
- [ ] Test migration and configuration semantics separately: unset allowlist means no deployment ceiling; an explicit empty legacy allowlist preserves chat-only intent; a nonempty explicit ceiling remains authoritative; unknown IDs are reported; enabling a tool in the UI cannot bypass a deployment restriction. Do not change global empty-value parsing for unrelated settings.
- [ ] Persist global overrides with revision/audit metadata. Derive defaults from the installed qualified built-in definitions; retain explicit disabled overrides. Migration records no secrets and makes no provider requests.
- [ ] Provide an operator-visible one-time preview/cutover from the legacy allowlist to stored global policy. Preserve existing allow/deny intent; document removing the legacy deployment list once the operator chooses managed settings. Update `.env.example` so a fresh managed installation does not accidentally start with a permanent empty ceiling. No per-tool configuration edit or process restart is needed after cutover for ordinary policy or readiness changes.
- [ ] Use the shared policy at admission and before external dispatch/publication. Pin the admitted effective IDs and policy identity using the existing operation/bundle mechanisms; version any changed frozen DTO explicitly. Enabling later does not replay a failed or interrupted run. Old history remains readable.
- [ ] Run `uv run pytest tests/test_tool_policy.py tests/integration/test_tool_policy_postgres.py tests/test_capability_routes.py -q`; expect passing SQLite/contract cases and zero required PostgreSQL skips in its configured lane. Commit the working policy increment using the repository's scanned file-based messages.

## Task 15.2 — Global Settings and live eligibility refresh

**Files:** Extend `components/settings/GlobalSettings.vue`, `stores/{connectors,chat}.ts`, `services/api.ts`, types and the existing composer/menu/setup controls. Add a tool-policy store if needed to share revisioned state; keep transient form state in the view.

**Interfaces:** Operator-only tool-policy management routes return the effective revision and installed tool rows. Project capability routes expose only authorized metadata and distinguish disabled-by-operator, deployment-restricted, missing-Connector, missing-target and incompatible-runtime states. Use the existing operator check, not project ownership alone, for installation changes.

- [ ] Write failing component tests in `GlobalSettings.test.ts`, `ToolAttachmentMenu.test.ts`, `ChatInput.test.ts`, `ConnectorSettingsCatalog.test.ts` and policy-store tests. Verify one operator toggle, non-operator visibility, deployment-ceiling explanations, stale response protection and cross-project isolation.
- [ ] Add Global Settings controls for built-in tool enablement. Connector save/revoke, binding changes and policy changes refresh the same effective catalog; navigating back or refocusing the page refreshes stale state. Use ordinary bounded refresh/revision reads; no background AWS checks.
- [ ] Keep one project search-default selector and manual choices. Preserve the connection-check admission-before-navigation fix and first-open AWS dialog behavior. A bucket selector alone must not imply a check has run.
- [ ] Verify an already-running API and worker can use an enabled ready tool after a UI update, with no environment edits or process restarts. A blocked/disabled tool remains unavailable with a useful reason.
- [ ] Run `pnpm typecheck`, the named component tests, and the fixture browser policy flow; expect passing checks. Commit the working settings increment.

## Task 15.3 — Local bound-bucket inventory

**Files:** Add `tools/aws/bound_inventory.py`, argument/result contracts and definition registration; reuse `resources/service.py` ownership and pagination. Add `tests/tools/test_bound_inventory.py` and admission/model-selection cases.

**Interfaces:** `aws_s3_list_bound_buckets` is a `read_only_local` maintained capability with no Connector requirement, no AWS operations and no output artifact requirement. Arguments are `limit` (default 20, maximum 50) and an opaque optional cursor. Results label `source=stored_bindings`, contain bounded owned binding metadata and a next cursor, and make no live-existence claim.

- [x] Write failing tests for owner/project filtering, foreign or malformed cursors, stable pagination, limits, revoked/expired Connector credentials and zero external/broker/decryption calls. Scope cursors to the owning project and query.
- [x] Register and implement the handler through the existing local gateway path. Return active local bindings with their retained Connector/binding references; do not hide stored resources solely because live access is unavailable. Retired bindings remain retained history, outside the active inventory result.
- [x] Test that returned IDs do not modify the run's admitted resource selection. Storage/metadata on an unselected or foreign binding remains refused even if its ID appears in prior text or another inventory result.
- [x] Run `uv run pytest tests/tools/test_bound_inventory.py tests/test_tool_run_commands.py tests/test_tool_turn_commands.py -q`; expect passing cases. Commit the inventory increment.

## Task 15.4 — Bound-bucket metadata and retained evidence

**Files:** Add `tools/aws/s3_metadata.py`; extend AWS contracts, per-definition manifests/registration, admission, broker access profile and observation publication. Extend browser operation/resource projections and typed result rendering. Add `tests/tools/test_s3_metadata.py`, production-transport SDK tests and publication integration coverage.

**Interfaces:** `aws_s3_bucket_metadata` takes only `resource_binding_id`. Resolve account/region/bucket from the pinned owned binding, acquire the same configured read role, verify identity/expected owner/location, then call native `get_bucket_versioning`. Normalize only versioning/MFA-delete states, account, region, bucket and retrieval time; absent versioning status is `not_enabled`, not a transport failure or inferred `Suspended`. Store immutable `s3-bucket-metadata.json` through the existing cloud-observation publication path.

Use the documented `Enabled`/`Suspended` versioning states and distinguish absent MFA-delete metadata from `Disabled`; AWS returns no versioning status for a bucket that has never enabled versioning. See the [GetBucketVersioning API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketVersioning.html).

- [ ] Write failing tests for enabled/suspended/never-enabled versioning, malformed/unknown states, owner/account/region mismatches, changed/revoked Connector refs, foreign/unselected targets and publication rollback/cancellation. No raw response fields or session values enter public results/evidence.
- [ ] Keep bounds of 30 seconds, at most 8 physical requests including role acquisition/checks, 2 MiB HTTP responses, 64 KiB evidence and 16 KiB model result. Preserve the recorded request count and retrieved time; metadata has no invented CloudWatch measurement time.
- [ ] Add bucket-scoped `s3:GetBucketVersioning` to the 5B permission documentation. Do not edit the user's IAM role automatically. Bind allowed SDK operations to the admitted capability: adding versioning support must not make it callable by storage or connection-check handlers. Never add mutation methods.
- [ ] Give new handlers their own manifest identities. Shared broker/transport source changes may invalidate old exact refs; retain compatible frozen definitions only when executable, otherwise fail visibly as changed. Do not recompute an old digest for changed code. Keep the storage manifest's declared operation set and existing admitted configuration semantics intact.
- [ ] Generalize the existing selected-resource checks used by admission, automatic model definitions and the composer to include metadata. Keep `aws_check_connection` model-free; local inventory does not need a selected cloud target. Use existing definition hooks rather than another provider dispatch abstraction.
- [ ] Test successful native SDK requests through production destination validation with only network I/O stubbed. Retain the 5A.2 empty-userinfo, origin/path/query, foreign-key and secret-debug-log regressions.
- [ ] Show compact metadata results and retained evidence in the conversation and resource observations, with automatic refresh after publication. Keep archive/restore and file-history behavior unchanged.
- [ ] Run the metadata, broker, SDK/transport and publication tests plus affected browser tests; expect passing configured lanes. Commit the working metadata increment.

## Task 16 — Qualify 5B and obtain acceptance

**Files:** Extend the existing synthetic-stack/browser/PostgreSQL fixtures and AWS runbook; add `docs/testing/phase5b-tool-availability-and-reads-qualification.md`. Update parent-plan checkboxes only against observed evidence.

- [ ] Run full API Ruff and the non-service API selector, PostgreSQL integration cases against a disposable test database, synthetic full-stack flows and browser policy/inventory/metadata flows. Run web typecheck, unit tests and build. Record exact commands, results and any skips; required CI lanes must show zero skips.
- [ ] Demonstrate: configure an eligible Connector, enable/disable its built-in tools in Global Settings, list local bindings without cloud credentials, explicitly select a bucket, read metadata, and reopen its exact evidence after restart. Repeat under a different project/owner and verify isolation.
- [ ] Exercise policy disablement between admission and dispatch/publication, stale worker/catalog state, revocation, cancellation/lease loss and crashes after dispatch intent. No implicit retry, credential fallback or publication after a fence is lost.
- [ ] Verify accepted 5A.1/5A.2 journeys: one search default and eligible error fallback, manual choices, archive/restore without resource changes, UUID binding requests, foreign-key-safe resource insertion, accepted-check navigation, storage coverage/evidence and secret-safe debug logging.
- [ ] With separate live opt-in, compare metadata to an independent read and record the added IAM authority. User UI testing and synthetic proof are separate evidence; do not claim an unperformed live comparison or send prohibited mutations.
- [ ] Request accept/revise/stop for 5B. Until accepted, report 5B as a candidate and Phase 5 as incomplete. Phase 11's broader discovery/import system remains outside this unit.

## Verification and handoff

Use the parent plan's verification commands and shared CI lanes. Keep this plan synchronized with the task ledger and observed evidence; sequential execution remains the established method.
