---
type: plan
summary: Approved Phase 4 tasks for separate Brave/Tavily tools with operator-configured keys, the shared gateway/model loop, and basic persistent conversation files; implementation is underway.
status: in_progress
date: 2026-10-20
---

# Phase 4 Trusted Tools and Conversation Files Implementation Plan

> **Approval:** The user approved implementation on 2026-10-20 and directed continuation through the remaining tasks after visually accepting Task 9A. Work proceeds through Tasks 10–17 without pausing at interim review gates. Live provider requests remain opt-in and require a separate explicit request.
>
> **UI revision:** The user reports that Phase 4A functionality works and requested three UX changes in an annotated screenshot. The approved Task 9A/spec §8.1 revision is implemented, fixture-verified and visually accepted on 2026-10-20. The user directed continuation through the remaining plan; Task 10 is now underway.
>
> **For agentic workers:** Use `superpowers:executing-plans` for native execution. Do not delegate unless the user explicitly selects it. Checkboxes track implementation progress.

**Goal:** Run `brave_web_search` and `tavily_web_search` manually and from qualified OpenAI chat, preserving bounded results in persistent conversation directories for later list/read use.

**Architecture:** Extend the existing API, PostgreSQL queue/lease/fences, runtime worker, LangGraph, provider boundary, outbox/snapshots and browser. The gateway admits separate maintained tool implementations and publishes immutable local artifacts. Phase 4 resolves operator credential references; Phase 5 owns managed Connectors/credentials and Phase 6 owns sandbox execution and editable/versioned files.

**Tech stack:** Existing Python 3.11+, FastAPI/Pydantic 2, SQLAlchemy/Alembic, PostgreSQL 15, LangGraph, httpx, OpenAI Chat Completions, Vue/TypeScript/Pinia, pytest/Vitest/Playwright.

**Spec:** [Phase 4 trusted tools and conversation files](../specs/2026-10-19-phase4-execution-and-conversation-workspace-design.md). Also read [architecture alignment review](../../reviews/2026-10-19-phase4-architecture-review.md).

## Global constraints

- Canonical search tools are exactly `brave_web_search` and `tavily_web_search`, each version `1.0.0`, with independent manifests/credential requirements and one shared gateway.
- No combined search-provider router, new Connection model, SearchProvider base framework, persistent search credential store, browser key-entry UI, Vault client or sandbox implementation in Phase 4.
- Trusted tool I/O runs in the existing runtime worker, not a long-lived API request. Preserve current HTTP/event names, `completed` status, owner authorization, cancellation, first-chat titles, reconnect, history and provider pinning.
- Phase 4 output policy is immutable snapshots. Phase 6 adds mutable logical file heads and captured immutable versions; do not introduce future edit/history tables now or make immutability a permanent workspace constraint.
- One active run per conversation; initial `TOOL_CONCURRENCY=1`, configurable and shared across workers. Busy work waits through the existing run/queue/supervision foundation. Dispatched interruption fails visibly and never silently repeats a search.
- Exact limits, output schemas, transaction boundaries, credential rules and error copy follow spec §§4–11. Python 3.11/PostgreSQL 15 remain the baseline; no runtime/SDK migration is implied.
- Follow proposed spec §8.1 for Task 9A: one composer, tools under `[+]`, compact chronological operation entries and a model dropdown defaulting to the configured `gpt-6-luna` profile. Automatic model invocation belongs to Tasks 10–14. Preserve exact IDs, historical provider provenance and retry payloads.
- User approval for implementation is recorded above. No subagent/delegation is preselected. Git operations follow `.agents/skills/git-workflow/SKILL.md`, including scanned file-based commit messages; do not push/merge/clean this managed worktree merely because tasks finish.

## Review focus

1. Registered underscore tool IDs must pass unchanged through model requests/results, with exact allowed-name validation and independent credential bindings (Tasks 1, 10–12).
2. Cancellation/fence movement during HTTP or artifact fsync/commit must close local I/O and reject stale publication while keeping earlier artifacts (Tasks 3, 6–7, 14).
3. Partial tool-call fragments and unknown external outcomes must never trigger hidden retry, provider switching or duplicate physical dispatch (Tasks 4–7, 11–12).
4. Concurrent requests/slots/quotas/workspace creation must produce one stable result or safe conflict; saturation waits without a second queue (Tasks 2–3, 6–7, 15).
5. Missing/replaced storage, hostile result URLs/filenames and file-type swaps must fail boundedly without an empty replacement history, network fetch or path escape (Tasks 3–5, 15–17).

## 1. Delivery order and verification lanes

| Unit | Tasks | Acceptance |
| --- | --- | --- |
| 4A | 1–9, 9A | Explicit Brave then Tavily calls, no model requirement, durable result artifacts, HTTP/cancel/recovery/secret handling, and user-reviewed integrated composer/result UX |
| 4B | 10–14 | Same tools through one qualified OpenAI loop with complete transcript, exact tool IDs and cumulative budgets |
| 4C | 15–17 | Stable workspace association, bounded file tools/UI, restart continuation, retention/restore |

Complete and obtain accept/revise/stop for each unit before the next. Tasks consume earlier interfaces and are executed sequentially by default. Commit only complete verified units of work using a UTF-8 message file under `/tmp/`, clean `rayloc scan` immediately before `git commit --file`, as required by the repository workflow. Commit subjects below are suggestions, not shell commands.

Commands assume `apps/api` for Python and `apps/web` for frontend:

```bash
# Network-free API lane
uv run pytest tests/ -q -m 'not postgres and not provider_live and not full_stack'
uv run ruff check .

# Browser gates
pnpm typecheck
pnpm test:run
pnpm build
pnpm test:e2e
```

Real PG tests use a disposable PostgreSQL 15 service via `TEST_POSTGRES_URL`. `full_stack` tests use actual API/runtime/PG/filesystem with synthetic search/model services. Existing fixture Playwright tests stay separate from `playwright.full-stack.config.ts`, which never intercepts app API commands. Live search and model tests use `provider_live` and explicit `OPENSHIP_LIVE_SEARCH_TOOL_ID` / `OPENSHIP_LIVE_TOOL_PROFILE_ID`; ordinary CI has no live credentials/costs. No Phase 4 sandbox lane, runsc install or container-execution qualification.

Required lanes must not report success from zero tests/all skips. Record exact test counts, commands, versions and unavailable evidence. Live rankings/content are variable; assert integration/shape/provenance rather than fixed answers.

## 2. File ownership map

Source paths below are relative to `apps/api/src/openship/`; API tests/migrations to `apps/api`; browser paths to `apps/web`. Every new source package includes minimal `__init__.py`.

| Files | Ownership |
| --- | --- |
| `capabilities/contracts.py`, `catalog.py`, `routes.py` | Exact maintained manifests, enabled/available public catalog, output policies |
| `tools/search/contracts.py`, `http.py`, `brave.py`, `tavily.py` | Shared bounded data/transport helpers and two independent maintained tool implementations; no provider routing |
| `execution/contracts.py`, `errors.py`, `models.py`, `admission.py`, `gateway.py`, `publication.py`, `recovery.py`, `projections.py` | Tool context, operation/attempt/slot records, caller-owned transactions, execution/publication/recovery and safe summaries |
| `artifacts/contracts.py`, `models.py`, `filesystem.py`, `service.py`, `routes.py`, `maintenance.py` | Local immutable snapshots, safe bounded transfers, quotas, publication and lifecycle |
| `runs/dispatch.py`, `tool_graph.py`, `budget.py`, `transcript.py`; existing run modules | Maintained graph versions, model-free runs, supervision, durable transcript/shared budgets |
| Existing `providers/contracts.py`, `runner.py`, `attempts.py`, `openai.py`, adapter request guards | Normalized tool messages, unchanged tool IDs, cumulative reservations and retained text behavior |
| `workspace/service.py`, `schemas.py`, `tools.py`; existing `workspace/models.py`, `routes.py` | Stable directory associations and bounded current-conversation list/read tools |
| Existing `main.py`, `config.py`, `database/models.py`, `events/schemas.py`, `service.py`, `logging.py` | Integration, snapshot/events, fixed errors and safe availability projection |
| Migrations `006_phase4_execution_artifacts.py`, `007_phase4_tool_transcripts.py`, `008_phase4_conversation_workspaces.py` | Additive 4A/4B/4C schema, history and downgrade protection |
| Browser `stores/artifacts.ts`, `components/chat/SearchToolForm.vue`, `ToolOperationCard.vue`, `ConversationFiles.vue`; existing chat/types/API files | Manual query/tool selection, transcript/result cards, files and stable retry payloads |
| Root Compose/README/new `.env.example`; API `.env.example`, README, pyproject/lock if needed; CI; testing/operations docs | Runtime-only operator keys, shared persistent files, unit/PG/browser/full-stack/live documentation |

Do not create `sandbox/`, sandbox host services, tool images, install scripts, Secret/Connection tables, search-provider catalogs, or secret-storage UI in this plan. Installation/image work is an explicit Phase 6 handoff.

## 3. Implementation tasks

### Task 1 — Exact catalog, exact tool identity and output contracts

**Files:** Create `capabilities/{contracts,catalog,routes}.py`, `execution/{contracts,errors}.py`, `tools/search/contracts.py`, `artifacts/contracts.py`; extend `config.py`, `main.py`, API `pyproject.toml` and unit CI selectors. Tests: `tests/execution/test_contracts.py`, `tests/test_capability_routes.py`.

**Interfaces:** Produce spec §13 DTOs: `CapabilityRef`, `CapabilityManifest`, `OutputPolicy`, `SearchArguments`, `SearchItem`, `SearchObservation`, `SearchToolResult`, `ArtifactRef`, `ExecutionConfig`, `LoopLimits`, `ToolCommandRequest`, `AuthorizationContext` and `PublishedToolResult`. Produce `canonical_digest(value: dict) -> str`, `resolve_capability(ref: CapabilityRef, enabled_ids: frozenset[str]) -> CapabilityManifest`, and `list_capabilities() -> tuple[CapabilitySummary,...]`. Defaults are exact spec §11 values; the two tool definitions are independent.

- [x] Write failing `test_tool_ids_and_bindings_are_independent`, `test_manifest_digest_covers_implementation_schema_and_limits`, `test_changed_disabled_unknown_capability_refused`, `test_mutable_output_policy_not_dispatchable_in_phase4`, and `test_catalog_auth_and_secret_redaction`. Assert query/result strict bounds and forbidden endpoint/key/executable fields.
- [x] Run the targeted contract and capability-route tests; the missing contracts/routes were observed before implementation.
- [x] Implement frozen DTOs, exact maintained catalog, safe unavailable reasons and immutable-only output dispatch. Do not resolve to a generic `web_search` handler that chooses a service.
- [x] Verify targeted tests/lint and old config round trips; add `full_stack` marker/exclusion before dependent test modules land. Commit: `3e49308 feat: define maintained tool contracts`.

### Task 2 — Durable operations, shared capacity and model-free commands

**Files:** Create `execution/{models,admission}.py`, `runs/dispatch.py`, `artifacts/models.py`, migration 006; extend run models/schemas/service/routes, database registration and integration fixtures. Tests: `tests/test_tool_run_commands.py`, `tests/integration/test_phase4_execution_migration.py`, `tests/integration/test_tool_admission_postgres.py`.

**Interfaces:** `submit_tool_run(db: Session,user_id: UUID,conversation_id: UUID,command: ToolCommandRequest) -> Run`; `require_live_claim(db: Session,claim: Claim,*,lock: bool) -> Run`; `admit_operation(db,claim,context,logical_call_id,ref,arguments,inputs) -> ToolOperation`; `acquire_execution_slot(db,claim,operation_id) -> ExecutionSlot|None`; `release_execution_slot(db,claim,slot_id) -> bool`. Caller owns commits. Produce `ToolOperation`, `ToolAttempt`, `ExecutionSlot` and a bounded timestamped `ToolAvailability` record (capability/digest/worker identity, available/reason/observed time; no binding/key values).

- [x] Write failing cases for manual execution without model/user message, exact replay, changed/foreign key conflict, simultaneous active-run admission, shared capacity lease/fence expiry and historical chat defaults. Use PG barriers, asserting one run/job/event/outbox and no duplicate slot owner.
- [x] Run command unit tests and the named PG modules; PostgreSQL-only tests are skipped when `TEST_POSTGRES_URL` is unavailable.
- [x] Implement migration `phase4a006` after `hij012`: run kind/version/config/dispatch/deadline fields, operations/attempts/slots/availability, artifact/quota tables and non-null request-key uniqueness. Detect legacy duplicates and stop with repair guidance. Never invent tool/provider history. Update autouse integration fixture boundaries for isolated migration databases.
- [x] Verify old history remains readable, unsupported graph versions fail safely, exact replay precedes changed-config validation and downgrade refuses protected Phase 4 data. Commit: `6ae27f6 feat: admit fenced model-free tool runs`.

### Task 3 — Local snapshot storage, quotas and bounded transfers

**Files:** Create `artifacts/{filesystem,service,routes,maintenance}.py`; extend artifact models/config/routes. Tests: `tests/artifacts/test_filesystem.py`, `test_transfers.py`, `test_publication_crashes.py`, `tests/integration/test_artifact_quota_postgres.py`.

**Interfaces:** `LocalArtifactStore(root: Path,storage_id: UUID)` exposes `initialize()`, `verify()`, `stage(artifact_id,chunks,expected: ArtifactRef) -> StagedArtifact`, `publish_file(staged,relative_key)`, `open_verified(artifact) -> BinaryIO`, and `remove(artifact)`. `reserve_artifact(db,conversation_id,project_id,max_bytes,kind,request_key) -> Artifact` and `mark_artifact_ready(db,artifact_id,observed) -> Artifact` join caller transactions. Create artifact init/verify/retention CLI entry point.

- [x] Write streamed/chunked 8 MiB upload, digest-bound replay, invalid UTF-8/JSON-depth/filenames, symlink/hardlink/FIFO/file-swap, quota race and missing/replaced-root tests. Cover crash after reservation/fsync/file publication and before/after ready metadata commit.
- [x] Run artifact tests and the real PG quota test separately. The local PG test is skipped because `TEST_POSTGRES_URL` is unavailable.
- [x] Implement final conversation-scoped layout, explicit storage identity, no-overwrite file publication/fsync before ready commit, bounded raw upload/download, reservations, tombstones and unavailable state. Search output reservation is 64 KiB; no editable file heads/history APIs.
- [x] Verify restart preserves exact bytes/digests, failed upload/commit releases or reclaims staging/quota, active inputs block deletion, and HTML-like plain-text downloads are attachment-only with `nosniff`.
  - Validation: `.venv/bin/pytest` targeted Phase 4 and artifact set → 53 passed, 3 skipped; `.venv/bin/ruff check` on changed API source/tests → clean. PostgreSQL concurrency and ASGI serialization remain environment-dependent evidence.
  - Commit: `2fc8508 feat: retain immutable conversation artifacts on local storage`.

### Task 4 — Maintained Brave search tool

**Files:** Create `tools/search/{http,brave}.py`; integrate existing operator credential resolver without browser-controlled binding names. Tests: `tests/tools/test_brave_search.py`, `tests/tools/test_search_transport.py`.

**Interfaces:** `search_brave(arguments: SearchArguments,context: ToolInvocationContext,control: InvocationControl) -> SearchObservation` is async and belongs only to the Brave capability. `BoundedSearchHttp` owns fixed-origin requests, TLS/connected-address validation, redirects/proxy denial, streaming byte limits and cancellation; it does not choose a provider. `ToolInvocationContext` carries principal/project/run/capability, pinned non-secret settings and a runtime-only credential resolver; no key enters serialized state.

- [x] Write failing exact Brave request/mapping tests and cases for missing/rejected key, empty success, malformed/oversize compressed response, hostile/unsafe result URLs, response containing a sentinel key, 429/timeout and cancellation. Assert one upstream request, no result-URL fetch and no Tavily fallback.
- [x] Run `uv run pytest tests/tools/test_brave_search.py tests/tools/test_search_transport.py -q` before implementation.
- [x] Implement spec §5 using fixed maintained Brave endpoint and operator reference, bounded decompressed data/normalized snapshots/model excerpts, safe provenance/time and fixed errors. Verify the connected peer satisfies destination policy; no unrestricted URL client or SDK hidden retries.
- [x] Verify fixture mapping, URL/DNS/proxy/redirect controls and no secret/query-bearing request URL in logs/events/public exceptions. Commit subject: `feat: add the maintained brave_web_search tool`.
  - Validation: focused search suite → 20 passed; cumulative Phase 4 regression set → 95 passed, 3 skipped; Ruff, API-wide Ruff pre-commit, Vue typecheck, staged secret scan and offline lock check passed. PostgreSQL lanes were skipped because `TEST_POSTGRES_URL` is unavailable. The API and frontend broad test hooks remain skipped for the previously recorded local harness stalls.
  - Commit: `ef692f7 feat: add the maintained brave_web_search tool`.

### Task 5 — Independent Tavily search tool and shared conformance

**Files:** Create `tools/search/tavily.py`; extend search fixture helpers; tests `tests/tools/test_tavily_search.py`, `tests/tools/test_search_conformance.py`.

**Interfaces:** Async `search_tavily(arguments: SearchArguments,context: ToolInvocationContext,control: InvocationControl) -> SearchObservation`, independently registered to `tavily_web_search`. Consume shared bounded DTO/HTTP utilities without adding a SearchProvider registry or routing by a model-supplied provider field.

- [x] Write failing Tavily request/result tests and the shared contract suite over both functions: empty/partial fields, unsafe URLs, unavailable scores/usage, truncation/provenance, wrong-key isolation and independent enablement. Assert basic/general mode and disabled auto-parameters/answers/raw content/images/favicons.
- [x] Run `uv run pytest tests/tools/test_tavily_search.py tests/tools/test_search_conformance.py -q`; expect missing Tavily behavior.
- [x] Implement the fixed Tavily search endpoint and runtime-only operator key resolution; same bounds/cancel/errors as Brave. No extract/crawl endpoints, advanced auto-upgrades, Brave fallback or browser key settings.
- [x] Verify both tools pass the shared contract and retain exact tool/credential identity. Commit subject: `feat: add the independent tavily_web_search tool`.
  - Validation: focused search suite → 34 passed; cumulative Phase 4 regression set → 109 passed, 3 skipped; Ruff and offline lock check passed. PostgreSQL lanes were skipped because `TEST_POSTGRES_URL` is unavailable. The API and frontend broad test hooks remain skipped for the previously recorded local harness stalls.

### Task 6 — Gateway dispatch, capacity waits and atomic result publication

**Files:** Create `execution/{gateway,publication,projections}.py`; extend admission/artifact service. Tests: `tests/execution/test_gateway.py`, `test_output_validation.py`, `tests/integration/test_tool_publication_postgres.py`.

**Interfaces:** `ToolGateway.execute_tool(*,claim,context,logical_call_id,ref,arguments,inputs=(),control) -> PublishedToolResult` is async and dispatches through an exact maintained-handler map. `publish_tool_result(db,claim,attempt_id,observation,staged_outputs) -> PublishedToolResult` joins one caller-owned transaction. Produce safe `OperationSummary`/`ToolAttemptSummary`; credential references remain server-side.

- [x] Write failing invalid authority/disabled/digest/argument/credential tests, busy-slot waits, same-logical-call replay, lost HTTP acknowledgement and cancel/fence vs fsync/commit races. Assert zero dispatch before admission and operation/artifact/events become visible together.
- [x] Run gateway unit and publication/slot tests. Invoke the PostgreSQL lanes; record their local skip when PostgreSQL is unavailable.
- [x] Implement check/pin → reserve quota/wait capacity → commit live-owner intent → invoke exactly one maintained function → validate/stage bytes → fenced atomic ready/result/event publication → release slot. No automatic second request after uncertain I/O. Capacity waits observe renewal, deadlines and stop signals without holding DB locks.
- [x] Verify stale owner cannot mark a file ready, failed/uncertain publication leaves no ready artifact, completed re-entry returns the recorded result, and saturation is not a provider-configuration error. Commit subject: `feat: execute and publish maintained tools through the gateway`.
  - Validation: cumulative Phase 4 regression set → 128 passed, 4 skipped; targeted Ruff and `git diff --check` passed. The named PostgreSQL publication, admission and quota lanes were invoked but skipped because `TEST_POSTGRES_URL` is unset and no local PostgreSQL service is accepting connections. SQLite tests cover both commit-applied and commit-unapplied acknowledgement loss; PostgreSQL publication visibility remains required in the real-Postgres lane before 4A acceptance.

### Task 7 — Worker graph, availability, cancellation and recovery

**Files:** Create `runs/tool_graph.py`, `execution/recovery.py`; extend existing run worker/queue/dispatch/service/maintenance commands and availability projection. Tests: `tests/test_tool_worker.py`, `tests/integration/test_tool_worker_postgres.py`, `tests/integration/test_tool_recovery_postgres.py`.

**Interfaces:** `build_tool_graph(checkpointer=None,gateway_factory=None)` uses logical ID `manual:0`; `resolve_graph(kind,graph_version) -> GraphFactory` is a maintained closed table. `publish_tool_availability(db,worker_id,observations)` writes only safe per-tool state/time; API treats stale/missing metadata as unknown. Extend existing `Worker.process_job(claim)` and `reconcile_expired(db)`; no new scheduler.

- [x] Write failing no-model manual-run, first-chat-title-after-manual-run, local cancellation ≤2 seconds, expired claim, capacity wait, runtime restart and no-redispatch tests. Check only the relevant tool's key affects its availability, API responses contain no binding/value, and dispatch rechecks credentials regardless of cached availability.
- [x] Run targeted worker tests plus existing model/provider worker tests; run real PG cancellation/recovery modules separately.
- [x] Implement maintained graph selection, one persisted execution deadline, per-thread DB sessions and current supervision. Persist tool-dispatch marker separately from provider marker. Safe runtime-owned availability lets API remain free of search key values; existing model credential behavior is unchanged.
- [x] Verify never-dispatched work may requeue, dispatched work fails visibly as interrupted, cancellation moves run/job fences and events/outbox together, active HTTP closes and earlier artifacts remain. Commit subject: `feat: supervise trusted tool runs in the durable worker`.
  - Validation: cumulative Phase 4/Task 7 unit set → 129 passed, 4 skipped. Ruff and `git diff --check` pass. The 4 skipped lanes require PostgreSQL (`TEST_POSTGRES_URL` is unset); new `test_tool_worker_postgres.py` and `test_tool_recovery_postgres.py` now cover real-PG worker completion, deadline-preserving requeue, dispatched interruption and atomic cancellation when that service is available. Existing provider-worker tests are blocked at the shared `auth_token` fixture: TestClient waits in `/api/auth/register` before worker execution; two pure title helper tests pass. No live search/provider calls were made.

### Task 8 — Explicit search UI, operation events and snapshots

**Files:** Extend API run/event schemas/routes/projections; browser types/API/chat store/ChatView/run cards. Create browser `SearchToolForm.vue`, `ToolOperationCard.vue`, `stores/artifacts.ts`. Tests: API `tests/test_tool_events.py`, `tests/integration/test_tool_snapshot_postgres.py`; browser `tests/SearchToolForm.test.ts`, `ToolOperationCard.test.ts`, `tests/e2e/tool-execution.spec.ts` and fixture API helpers.

**Interfaces:** Safe public catalog, `runsApi.submitTool/getOperations`, artifact metadata/download API and additive run/snapshot `kind`, `graph_version`, `operations`, `artifacts`, pagination/continuation fields. Default page 50, max 100. Exact submitted payload is retained for browser retries.

- [x] Write failing manual tool/query selection, missing key per tool, status/error/download, snapshot after event pruning, duplicate event and delayed old-conversation response tests. Neither model text nor a late event may fabricate operation success.
- [x] Run API event and targeted Vitest/fixture Playwright cases before changes.
- [x] Implement the form/cards in the existing conversation UI. Display Brave/Tavily identity and observation time, safe links and retained-file download; no combined provider selector/configuration or key-entry settings.
- [x] Verify targeted tests, typecheck/build and old auth/title/cancel/reconnect journeys. Label fixture browser evidence accurately. Commit subject: `feat: inspect search operations in conversation`.
  - Validation: API event/output/command/snapshot suite → 18 passed, 1 skipped (real PostgreSQL snapshot lane requires `TEST_POSTGRES_URL`); changed API Ruff clean. Browser suite → 137 passed; Vue typecheck and production build pass. Fixture Playwright tool/auth/provider-portability journeys → 23 passed, and full fixture E2E suite → 49 passed. Browser evidence uses synthetic fixture services. No live provider calls were made.

### Task 9 — 4A packaging, real-stack proof and operator setup

**Files:** Modify root Compose/README; create root `.env.example`; extend API `.env.example`/README, packaging tests, CI Python/frontend; create `docs/testing/phase4-acceptance.md`, `docs/operations/artifact-storage.md`, browser `playwright.full-stack.config.ts`, `tests/full-stack/tool-execution.spec.ts`, API `tests/full_stack/test_manual_search.py`.

**Interfaces:** Existing services plus persistent artifact volume; runtime-only operator search-key bindings and shared safe enablement/settings. Create documented artifact-storage initialize/verify/retention CLI entry points. Full-stack tests use synthetic search services/transports and actual app/API/runtime/PG/files without intercepting app commands.

- [x] Add packaging assertions for shared persistent files, runtime-only search key forwarding, loopback ports, no Docker socket/manager/runsc/image setup, and plain chat/manual search with unrelated credentials absent.
- [x] Document setting each operator key/enablement, exact-tool invocation, bounded provider failures, persistent storage identity, drain/upgrade/backup/restore and the future Phase 5 Connector transition. No real key values in examples.
- [x] Wire unit exclusion `not postgres and not provider_live and not full_stack`; add required new PG modules to real-PG CI and reject skips/zero-test qualification. Include frontend typecheck. Do not introduce sandbox CI or live provider calls on ordinary PRs.
- [x] Run actual app/runtime/PG/filesystem with synthetic Brave/Tavily: manual call saves a snapshot, restart/reconnect reads exact bytes, cancel/recovery has no duplicate upstream request. Run cumulative unit/lint/PG/browser checks; record evidence and 4A accept/revise/stop before Task 10.
  - Validation: `ruff check .` clean and packaging assertions 4 passed; Compose config valid; PostgreSQL lane 28 passed/0 skipped; synthetic API/runtime/PG/filesystem test 1 passed/0 skipped; browser unit tests 137 passed; typecheck and production build passed; fixture Playwright 49 passed; full-stack Playwright 1 passed against the synthetic API/runtime with real `/api` calls. No live providers were called. The user reported 4A functionality works, accepted the Task 9A visual revision, and directed continuation through the remaining implementation.

### Task 9A — Integrated composer and compact conversation tool results

**Placement:** After Task 9 and before Task 10; Phase 4A acceptance revision. Follow the user's screenshot priorities in order: compact results, `[+]` tool attachment, model dropdown. Spec §8.1 defines the approved interaction. Do not begin 4B to obtain automatic invocation early.

**Files:** Modify browser `src/components/chat/{ChatInput,ChatStream,ToolOperationCard,RunCard}.vue`, `src/components/ProviderSelector.vue`, `src/views/ChatView.vue`, `src/stores/chat.ts`, `src/types/index.ts`; create `src/components/chat/ToolAttachmentMenu.vue`, `src/lib/chatTimeline.ts`. Replace the permanent use of `SearchToolForm.vue`; after checking there were no other app consumers, remove it and migrate its applicable tests to the integrated flow. Extend API `execution/projections.py` and operation/snapshot tests with the additive owned `query` field for queued/failed query hydration. Update root/API `.env.example`, `docs/provider-support.md` and `docs/testing/phase4-acceptance.md`. Tests: browser `ToolOperationCard.test.ts`, `ChatStore.test.ts`, `ChatView.test.ts`, `ProviderSelector.test.ts`, new `ChatInput.test.ts`, `ToolAttachmentMenu.test.ts`, `ChatTimeline.test.ts`, fixture tool-execution journeys, and the visual UX capture test. The full-stack tool journey was not rerun because it submits a real external search.

**Interfaces:** Keep `runsApi.submitTool` and its server-owned capability refs/idempotency contract. `ChatInput` accepts the existing model catalog/selection plus capability summaries and emits ordinary chat or the existing manual-tool command; a manual command uses the shared text input and one attached capability with a default count of 5. `ToolAttachmentMenu` emits attach/remove and bounded count changes; it never dispatches a tool. `buildConversationTimeline(messages: ChatMessage[], operations: ToolOperationSummary[]): ConversationEntry[]` yields a discriminated union of message and tool entries, keyed by existing IDs and ordered by recorded creation time/stable ID. The store retains operation maps per conversation and hydrates them from every available snapshot run/operation page, not only the latest run. `ToolOperationCard` owns expanded/collapsed presentation; opening it performs no search. An additive nullable operation `query` is validated from persisted search arguments on authorized reads only, with no raw argument/SSE/log exposure. No new database tables, graph versions or model calls are needed.

- [x] **Pin the UX acceptance cases:** Added failing tests for a collapsed search row with exact service/query/status/count, expandable safe results/time/download, zero results, inline errors, cancellable states and one status indicator. Covered mixed chronology, snapshot operation retention, duplicate durable refresh, conversation switching, pagination, missing/legacy query and hostile content without synthetic persisted messages. `ChatTimeline` tests cover stable ordering.
- [x] **Verify the gaps before changes:** Ran the focused browser tests first and recorded the expected failures before implementation. The local Python HTTP test harness was already known to stall; after implementation, the targeted operation projection/event service suites passed (6 and 5 tests) and Ruff passed. See the acceptance record for test evidence and limits.
- [x] **Implement compact conversation results:** Built the presentation timeline from existing authoritative data, moved operations into `ChatStream`'s scroll region and removed duplicated pinned search run/result panels. Added collapsed/expanded result presentation and preserved run provenance, cancellation, scroll-follow/jump-to-latest and operation paging. The authorized operation projection exposes only the validated owned query field.
- [x] **Pin composer behavior:** Added failing-first `ChatInput`/menu/selector tests for `[+]`, available/unavailable tools, removable chip/draft preservation, count range/default, direct search, normal chat, IME handling, failed-submission draft recovery, model choice and Escape/focus return. Existing store tests cover immutable pending retry payloads and conversation-scoped state. Manual search remains model-independent.
- [x] **Implement the unified composer:** Replaced and removed the permanent search form after confirming no other app consumer, integrated `ToolAttachmentMenu` and removable tool chip, and moved the model selector below the input. Updated supplied Luna examples to `max_completion_tokens`; historical profile IDs and run snapshots remain untouched. Automatic model invocation remains Phase 4B.
- [x] **Verify interaction and visual evidence:** Browser unit suite passed (151 tests across 16 files), typecheck and production build passed, fixture Playwright passed (50 tests), and the focused visual-capture test passed on the final source. API operation projection/event tests passed (6) and event-service tests passed (5); Ruff passed. Captured 375px/1440px states in both themes, with viewport-width assertions. Keyboard arrow navigation, Escape/focus return and IME handling are covered. The full-stack browser tool journey and live providers were not run because it would issue an external search; asynchronous stale-conversation behavior is protected by generation/ownership checks but was not given a new dedicated delayed-response test here. Screenshot path: `.superpowers/sdd/2026-10-19-phase4-execution-and-conversation-workspace/screenshots/`.
- [x] **User visual acceptance:** The user accepted Task 9A on 2026-10-20 and directed continuation through the remaining implementation plan. Functional and visual evidence are recorded in the 4A acceptance record. Suggested verified commit subject: `feat: integrate compact search results and composer controls`.

### Task 10 — Complete tool-message contracts and durable transcript

**Files:** Extend model contracts, run admission/config and model registration; create `runs/transcript.py`, migration 007. Tests: `tests/providers/test_tool_contracts.py`, `tests/test_tool_turn_commands.py`, `tests/integration/test_tool_transcript_migration.py`, `tests/integration/test_tool_transcript_postgres.py`.

**Interfaces:** Produce normalized `ToolDefinition`, `ToolCall`, role-validated assistant/tool `ChatMessage`, `tool_call_delta`/`tool_call_ready`, `ProviderTurnResult.tool_calls`, `RunTranscriptEntry`, `append_transcript(db,claim,model_turn_ordinal,message)`, `read_transcript(db,run_id)`. Extend turn submission with tool enablement and optional artifacts, not credentials/provider routing.

- [x] Write failing old text/tool-message round trips, call/result ID checks, complete-vs-partial validation, exact request allowlist, changed replay payload and unsupported model/fallback tests. Search needs no uploaded input; unsupported tool-enabled requests fail before queueing.
- [x] Run the new unit and migration/concurrency cases before implementation. Contract and admission tests first failed on missing normalized contracts and tool-mode request fields; PostgreSQL transcript tests collect but skip when `TEST_POSTGRES_URL` is absent.
- [x] Implement revision `phase4b007` after `phase4a006`; persist ordered complete transcript messages, model-turn/reservation metadata and request digests while leaving historical values null. Pin exact currently enabled capability refs and selected ready immutable artifact refs; preserve chat v1 and select chat v2 only for tool-enabled turns.
- [x] Verify transcript call/result identity, per-turn and per-run ordering, exact allowlist checks, live lease fencing, caller-owned commit, no persistence of partial provider fragments, replay conflicts and downgrade protection. Validation: Task 10 targeted suite 18 passed, 1 PostgreSQL test skipped because `TEST_POSTGRES_URL` is unset; all nine provider modules passed separately (183 tests); changed API Ruff checks passed. A combined API run was stopped at the known local TestClient stall. Commit subject: `feat: persist normalized tool conversations`.

### Task 11 — OpenAI mapping and tool streaming conformance

**Files:** Extend existing `providers/openai.py`, event observation in runner, adapter unsupported-request guards and shared test fixtures; create `tests/providers/test_tool_conformance.py`; extend shared text conformance and OpenAI tests/docs.

**Interfaces:** Extend existing `OpenAIAdapter.stream(request,config,control)`. Registered IDs (`brave_web_search`, `tavily_web_search`, later `workspace_list_files`, `workspace_read_file`) pass unchanged into function definitions and normalized `ToolCall.name`. Validate returned names against the exact request allowlist; retain provider call IDs separately. No namespace conversion or alias mapping.

- [x] Write failing exact-name preservation/duplicate-definition/unknown-name, fragmented names/IDs/Unicode arguments, empty text valid call, duplicate/oversize/multiple calls, missing finish, actual-model mismatch and cancellation/stream-close tests. Tool fragments count as observed output; a transient failure after a fragment produces one generation request and no dispatch/fallback.
- [x] Run tool/OpenAI/shared text conformance before implementation. The first run failed on the optional canonical schema and the missing stream/runner behavior; after contract adaptation, all shared text cases remained in the verification set.
- [x] Implement direct registered function names, strict function schemas (all properties required, nullable optional fields, no extras, normalized defaults) and disabled parallel calls through existing Chat Completions; retain SDK retry/time/error/usage behavior. Other adapters reject advanced messages explicitly while retaining text support.
- [x] Verify all text adapters and the first OpenAI tool adapter; no request/result ID or tool name is lost across the round trip. Validation: all provider suites → 200 passed; changed API Ruff and `git diff --check` passed. No live provider calls were made. Commit subject: `feat: support registered tools through OpenAI function calling`.

### Task 12 — Shared budgets and bounded model/gateway graph

**Files:** Create `runs/budget.py`; extend graph/worker/dispatch/transcript, provider runner/attempts. Tests: `tests/test_agent_tool_loop.py`, `tests/providers/test_tool_budgets.py`, `tests/integration/test_agent_tool_loop_postgres.py`.

**Interfaces:** `RunBudget(session_factory,claim,limits,execution)` exposes `reserve_generation(profile,reason,model_turn_ordinal,requested_output_tokens,reserved_tokens,reserved_cost) -> BudgetReservation` and `admit_tool(logical_call_id)`. Extend recorder start with optional caller DB/session plus model-turn/output/total reservation metadata; supplied sessions never commit internally. `run_model_turn(...,budget=None,model_turn_ordinal=0)` keeps v1 compatibility. Budget reservation inserts one attempt/event atomically and returns its ID; existing finish writes that identity, without a second counter/start.

- [ ] Write failing model→Brave/Tavily→final flow, exact tool re-entry, two sequential calls, third-call/fourth-turn refusal, max-attempts total 3 across turns/retries, one 120-second deadline and output reservations 4,000/request /12,000 total. Test schema/result context counting and missing usage conservatively.
- [ ] Add cancellation after search publication, disabled tool on next turn, partial JSON/final disconnect, typed tool error explanation and authority violation tests. Assert committed snapshots stay readable while stale/final partial text never becomes a completed answer.
- [ ] Run unit and actual PG loop/budget cases before implementation, then extend the existing graph using only `ToolGateway.execute_tool`.
- [ ] Verify shared durable reservations, complete transcripts, no secret/client objects in checkpoints, unchanged text fallback and final-only first-chat title publication. Commit subject: `feat: orchestrate bounded trusted tools in the chat runtime`.

### Task 13 — Conversational tool UI and immutable retry payloads

**Files:** Extend browser types/services/chat/artifact stores, composer/ChatView/run/operation cards; tests `tests/ChatStore.test.ts`, `ChatView.test.ts`, `ToolOperationCard.test.ts`, `tests/e2e/tool-chat.spec.ts` and fixtures.

**Interfaces:** Reuse Task 9A's composer, `[+]` menu, model dropdown and compact conversation entries. Explicit tool chips retain the model-free manual path; chat tool mode enables automatic LLM invocation from the exact allowed catalog capabilities without requiring a forced attachment. It has no search-provider field. A profile change affects an unsent draft only. Submitted request retries preserve original tool mode, selected version IDs and profile/fallback selection. Results show exact executed tool identity and observation/artifact refs.

- [ ] Write failing supported/unsupported model, independent unavailable search tools, no-upload search, exact retry payload, unsafe snippet/link and old-conversation response cases. Exercise attached manual invocation separately from automatic LLM search without an attached chip, using the authorized tool-mode allowlist. Both appear in the same compact timeline. Failed operations must not appear successful because final model text says so.
- [ ] Run targeted tests, implement in existing components, and verify typecheck/unit/build plus fixture tool-chat/auth/provider/title/cancel/reconnect journeys.
- [ ] Commit verified task; subject: `feat: expose trusted tool use in the chat composer`.

### Task 14 — Independent live qualification and 4B acceptance

**Files:** Create `tests/live/test_search_tools.py`, `tests/live/test_tool_provider_smoke.py`, `tests/full_stack/test_tool_chat.py`, browser `tests/full-stack/tool-chat.spec.ts`; update support/conformance/Phase 4 acceptance docs and explicit full-stack workflow if needed.

**Interfaces:** `OPENSHIP_LIVE_SEARCH_TOOL_ID` selects exactly one installed Brave/Tavily capability; `OPENSHIP_LIVE_TOOL_PROFILE_ID` selects one declared OpenAI model tuple. Missing opt-in skips ordinary discovery; an explicitly requested qualification run with absent prerequisites fails rather than pretending success. Network-free guard tests do not need secrets.

- [ ] Add fixture and guard tests first; then real-stack synthetic services exercise both tools, restart/cancel/transcript/snapshot behavior and no hidden provider switching/duplicate request.
- [ ] Only after explicit operator approval run `uv run pytest tests/live/test_search_tools.py -m provider_live -vv` for each tool and the model/tool smoke separately. No unapproved calls or cost; no stable-ranking assertions or real credential outputs.
- [ ] Record exact successful tuples/tool revisions/IDs/times/digests and outstanding evidence. Keep other model adapters text-only until qualified; run cumulative unit/PG/browser gates. Obtain 4B accept/revise/stop before Task 15.

### Task 15 — Persistent association and bounded workspace tools

**Files:** Extend workspace models/routes, create `workspace/{service,schemas,tools}.py`, migration 008; extend catalog/database models. Tests: `tests/test_conversation_workspace.py`, `tests/test_workspace_tools.py`, `tests/integration/test_workspace_association_postgres.py`, `tests/integration/test_workspace_migration_postgres.py`.

**Interfaces:** `ensure_workspace(db,user_id,conversation_id,store) -> WorkspaceSummary`; `list_workspace_files(arguments,context,control) -> WorkspaceListResult`; `read_workspace_file(arguments,context,control) -> WorkspaceReadResult`, with exact current-conversation artifact/version scope. Produce `ConversationWorkspace` and spec §9 bounded DTOs; integrate `workspace_list_files`/`workspace_read_file` through the same gateway and direct OpenAI function names.

- [ ] Write failing stable association across title/model/restart, concurrent ensure, unchanged 4A/4B IDs/digests/paths, lazy old-conversation association, missing/replaced root, cross-conversation ID/path and UTF-8 offset/page/read-bound tests.
- [ ] Run unit and named PG modules before implementation; migrate fixture artifacts from pre-4C without file copying.
- [ ] Implement `phase4c008` after `phase4b007`, no filesystem I/O in Alembic. Keep file keys server-controlled, exact version refs and bounded context. No mutable-file heads, patching/history UI or shared writable directories.
- [ ] Verify agent list/read uses no external search, counts against tool budgets and cannot exceed authority or bytes. Commit subject: `feat: associate and read persistent conversation files`.

### Task 16 — Retention, purge, restore and future versioning handoff

**Files:** Extend artifact maintenance/workspace service and operator CLI; update `docs/operations/artifact-storage.md`, Phase 4 acceptance docs and Phase 5/6 roadmap boundaries. Tests: `tests/artifacts/test_workspace_maintenance.py`, `tests/integration/test_workspace_retention_postgres.py`, `tests/full_stack/test_workspace_restore.py`.

**Interfaces:** `plan_workspace_purge(db,workspace_id) -> PurgePlan`, `purge_workspace(db,store,plan) -> PurgeReport`, `verify_restored_artifacts(db,store) -> RestoreReport`. Use existing quota/tombstone protocol, exact IDs/digests and expected state; dry-run is non-mutating and active pins refuse deletion.

- [ ] Write failing purge isolation/dry-run/active-input cases, unlink/commit interruption, quota reconciliation, missing/changed file on restore and no resumed dispatched search. Retain ID/digest tombstones; default age retention remains disabled.
- [ ] Implement and verify actual DB/filesystem backup/restore into fresh storage with a synthetic real stack; saved files retain bytes/association, missing files become unavailable and no search is issued to reconstruct them.
- [ ] Document Phase 5 binding migration and Phase 6 logical file heads/expected-base edits/version capture, explicit per-output mutability and sandbox install/image/qualification ownership. Do not implement those future tables/services or require their gates for Phase 4.
- [ ] Commit verified task; subject: `feat: retain and recover conversation snapshots`.

### Task 17 — Files panel, restart continuation and final review

**Files:** Create browser `ConversationFiles.vue`; extend artifact store/API/types/ChatView, tests `tests/ConversationFiles.test.ts`, `tests/e2e/conversation-files.spec.ts`, `tests/full-stack/conversation-workspace.spec.ts`; update README and Phase 4 evidence record.

**Interfaces:** `workspaceApi.get(conversationId)` and existing artifact/list/read/select/download/delete commands. Current-conversation async state remains scoped; list/read selection carries exact IDs, never host paths or a mutable latest-file lookup.

- [ ] Write failing retained file hydration/selection, pagination/quota, same-name distinct snapshots, active-input delete refusal, storage failure vs empty history, safe links/filenames and delayed previous-conversation response cases; implement panel after targeted failures.
- [ ] Run real-stack search → saved artifact → service restart → reopen → agent list/read the same saved ID/digest with zero new search requests. Verify a different conversation cannot gain ambient access.
- [ ] Run final cumulative API/lint/PG/filesystem/browser/full-stack and earlier auth/title/provider/cancel/reconnect checks. Repeat live qualification only for changed integration behavior with operator approval; record missing evidence honestly. No sandbox test/install gate belongs to Phase 4.
- [ ] Complete R01–R18 evidence mapping, present 4C accept/revise/stop and prepare changes for review. Phase completion and any PR/deployment remain explicit user decisions.

## 4. Planned requirement coverage

| Requirement | Tasks | Primary evidence |
| --- | --- | --- |
| R01 | 2, 7–9 | Manual no-model run, historic chat defaults, existing runtime and browser journeys |
| R02 | 1–2, 6 | Exact independent tools/digests/enablement/owner authorization |
| R03 | 2, 6–7 | Logical ID/attempt uniqueness, committed intent, PG publication/fence races |
| R04 | 4–5, 7, 9, 16 | Runtime operator bindings, no persisted keys/UI, future Connector compatibility |
| R05 | 4–5 | Fixed origins/connected destination, bounded HTTP/cancel, no returned-URL fetch |
| R06 | 6–7, 12, 14 | Cancel/interruption/unknown result, no hidden retry and stale publication denial |
| R07 | 3, 6, 9 | Filesystem crash/link/transfer/quota tests and restart persistence |
| R08 | 4–5, 14 | Independent Brave/Tavily contracts and opt-in real integration |
| R09 | 8–9, 13 | Authoritative snapshot after event expiry, dedup/reconnect/results UI |
| R10 | 10–11, 14 | Exact unchanged tool IDs, normalized calls/results and exact model tuple |
| R11 | 2, 6–7, 12 | Shared capacity waiting, per-conversation serialization and cumulative budgets |
| R12 | 10–14 | Complete transcript, fragment retry denial, final-only answers/titles |
| R13 | 15, 17 | Stable association with original bytes/IDs unchanged |
| R14 | 15, 17 | Bounded list/read after restart, no external search on retained-file access |
| R15 | 3, 15–17 | Missing storage, races, quota/retention/purge and safe UI failure |
| R16 | 2–3, 10, 15–16 | Sequential migrations, legacy data/downgrade and coordinated restore |
| R17 | 9, 14, 17 | Honest unit/PG/filesystem/fixture-browser/real-stack/live lane evidence |
| R18 | 1, 3–8, 10–17 | Sentinel credential/query leakage, untrusted snippets/files, safe bounded errors |
| R19 | 9A, 13 | Annotated-screenshot requirements, compact timeline/composer, Luna default, responsive/accessibility evidence and user visual acceptance |

## 5. Review and handoff

The user approved the base plan on 2026-10-20. Tasks 1–9 and the accepted Task 9A revision have recorded implementation evidence. The user directed continuation through all remaining plan tasks. Task 10 is complete and Task 11 is underway; later tasks remain unstarted. Live provider qualification remains opt-in and has not been authorized. The active SDD ledger records task evidence and local environment limits.

The user has directed implementation to continue through Tasks 10–17. Record technical and live-provider evidence honestly; do not issue live provider requests without explicit opt-in. No PR, merge, deployment or Phase 4 completion is implied by implementation approval.
