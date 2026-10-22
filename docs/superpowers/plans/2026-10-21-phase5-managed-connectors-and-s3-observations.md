---
type: plan
summary: Phase 5 implementation tasks for global/project managed search Connectors, worker credential consumption, native SDK extensions and scoped S3 observations.
status: approved
date: 2026-10-21
---

# Phase 5 Managed Connectors and S3 Observations Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` for sequential implementation. Use `superpowers:subagent-driven-development` only if the user selects delegation. Read the specification before starting; checkboxes track implementation.
>
> **Planning baseline:** PR #29, commit `222cc49`.
>
> **Approval boundary:** The user approved this plan and authorized implementation on 2026-10-21. Real AWS credentials and live provider calls remain opt-in; implementation stops at each acceptance gate for review.

**Goal:** Configure reusable global/project search credentials and project-scoped AWS access, then inspect existing S3 buckets through the delivered gateway with retained evidence.

**Architecture:** Extend the existing authenticated API, PostgreSQL queue, runtime worker, tool gateway, artifact publication, and browser. An internal credential module supplies selected API keys or authenticated SDK clients. Registered handlers use native provider SDKs; credential acquisition does not proxy provider operations.

**Tech stack:** Existing Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy/Alembic, PostgreSQL 15, LangGraph, HTTPX/HTTPCore, Vue/TypeScript/Pinia, pytest/Vitest/Playwright. Add explicit boto3 and cryptography dependencies, with compatible versions frozen in the API lockfile.

**Spec:** [Phase 5 managed Connectors and S3 observations](../specs/2026-10-21-phase5-managed-connectors-and-s3-observations-design.md).

## 1. Delivery sequence and implementation contracts

### Acceptance units

| Unit | Tasks | Deliverable and acceptance gate |
| --- | --- | --- |
| 5A.1 | 1–7 | Browser-managed global/project Brave and Tavily connectors, credential lifecycle, manual/qualified chat execution, restart recovery and isolation |
| 5A.2 | 8–14 | Project-scoped AWS connector, bound S3 bucket, identity checks, storage observation, connector blockers and retained evidence |
| 5B | 15–16 | Bounded local inventory and selected bucket metadata through the same contracts |

Execute tasks sequentially. Obtain accept/revise/stop at each acceptance gate before starting the next unit. Planning 5B does not authorize implementing it before 5A.2 acceptance.

### Global constraints

- Preserve current authentication, owner-only personal projects, model/profile pinning, conversation titles, cancellation, reconnect, transcript history and file recovery.
- Keep existing terminal statuses, including `completed`; add only `waiting_connector`.
- Preserve readable manifest/configuration v1 and chat-v1/chat-v2/tool-v1 history. New managed execution uses manifest schema v2, execution configuration v2, chat graph v3 and tool graph v2.
- Search capability IDs remain `brave_web_search` and `tavily_web_search`. Services remain independent.
- Global means installation-wide eligibility for authenticated projects. Only the configured operator manages global search connectors; project owners manage their project connectors. AWS remains project-scoped.
- Execute distribution-owned handlers in the trusted worker. Portable connector packaging, third-party executables, CLI execution and generated-code credential handoff remain deferred.
- Queue rows, checkpoints, transcripts, events, artifacts, browser reads and logs contain no credentials or secret-bearing provider responses.
- Preserve shared capacity and ownership fences. No database locks across network or filesystem I/O.
- No hidden retries, automatic replay of dispatched work, provider fallback, credential fallback, account discovery, object downloads or cloud writes.
- Live provider requests remain explicit opt-in qualification. Ordinary CI uses synthetic credentials and transports.

### Interfaces to establish

Backend module names below are relative to `apps/api/src/openship/`; backend tests are relative to `apps/api/`.

| Interface | Contract |
| --- | --- |
| `ConnectorRef` | Immutable connector ID, configuration revision, visibility and credential-version reference |
| `ResourceBindingRef` | Immutable owned binding ID/revision and associated connector revision |
| `ExecutionConfigV2` | Existing execution/model limits plus resolved connector/binding references and admitted conversation-context IDs; no secret values |
| `decode_execution_config(payload)` | Return the exact supported v1/v2 configuration; reject unknown versions |
| `ToolDefinitionEntry` | Manifest, description, argument/result validators, async handler, safe result/evidence projector and optional transactional publication hook |
| `resolve_connector(db, principal_id, project_id, kind, selected_id)` | Enforce scope, policy and deterministic selection; return the selected immutable reference |
| `CredentialBroker.acquire(ref, invocation, control)` | Recheck authority and return runtime-only credential access; never a serializable DTO |
| `context.aws.client(service)` | Construct a client bound to the admitted role, region and operation profile |
| `await context.aws.run(method, **parameters)` | Execute an ordinary SDK method through shared request, destination, deadline, cancellation and byte controls |
| `PreparedToolResult` | Validated public result plus bounded safe evidence; no raw SDK response passthrough |
| `resume_connector_run(db, principal_id, run_id, command)` | Atomically consume the matching blocker and requeue unchanged admitted configuration under a new fence |

Existing handlers retain the `arguments, context, control` calling convention. Extend the runtime-only context with optional AWS access; never put that context into graph state.

### Review focus

Each condition must have an explicit owning test:

1. Global rotation or revocation races with dispatch/publication: Tasks 3, 4 and 7.
2. Malformed secret submissions leak through validation, logging or retry handling: Tasks 2, 3 and 5.
3. SDK redirects, retries or oversized error responses bypass limits: Tasks 8 and 14.
4. Connector resume reinterprets later messages or overwrites a newer run: Tasks 12 and 14.
5. Missing metrics, mixed dates or absent storage classes produce a misleading total: Tasks 11, 13 and 14.

**Task completion cycle:** Write the named failing tests, observe failure, implement the stated contract, run targeted tests, review the diff and commit a working increment. Git messages must use scanned UTF-8 files under `/tmp/`, following the [repository Git workflow](../../../.agents/skills/git-workflow/SKILL.md). Do not use inline commit messages.

## 2. Tasks 1–7: Managed search connectors

### Task 1 — Versioned contracts and a maintained definition registry

**Files:** `capabilities/{contracts,catalog,definitions}.py`, `execution/{admission,gateway,publication,projections}.py`, `runs/{service,dispatch,tool_graph,agent_graph}.py`.

**Tests:** `tests/execution/test_definition_registry.py`, existing capability, gateway, publication and graph tests.

- [x] Write failing tests proving that a registered test handler supplies its own argument validation, result projection and model description without another gateway dispatch branch.
- [x] Add manifest/configuration v2 and the version decoder. Preserve v1 DTOs, graph mappings and historical public projections.
- [x] Replace fixed search-specific validation and description maps with registered definitions. Keep search and workspace handlers working through the existing gateway.
- [x] Require implementation/schema/limit changes to produce new manifest digests. Historical references must resolve exactly or fail visibly as changed; never synthesize an old digest for replacement code.

**Verification:** Run the new registry tests and existing capability, manual-tool, tool-chat, publication and workspace tests.

**Commit deliverable:** Version-aware registration and dispatch with no new credential behavior.

### Task 2 — Connector schema and encrypted credential storage

**Files:** New `connectors/{models,contracts,errors}.py`, `connectors/credentials/{keys,store}.py`; update database model registration, configuration and API dependencies.

**Migration:** `009_phase5_managed_connectors.py`, revision `phase5a009`, following `phase4c008`.

**Tests:** `tests/connectors/test_credentials.py`, `tests/integration/test_connector_migration_postgres.py`.

- [x] Add connector, immutable revision, credential-version, project-default, request-ledger, scoped audit and availability records.
- [x] Enforce global/project ownership shape, project-only AWS, same-connector credential references and immutable configuration revisions. Downgrade must refuse protected retained data.
- [x] Implement AES-256-GCM using fresh 96-bit nonces and authenticated associated data binding installation, visibility, owning scope, connector, credential version, schema and kind.
- [x] Load versioned encryption keys and the separate idempotency HMAC key from protected files. Require exactly 32 decoded bytes per encryption key. Missing/malformed keys produce fixed unavailable errors; startup must not generate replacements.
- [x] Test that changing ciphertext, nonce, connector, scope or credential kind causes decryption failure. Assert bounded 16 KiB secret payloads and secret-safe representations.

**Verification:** Unit crypto tests plus real PostgreSQL upgrade/history/protected-downgrade tests.

**Commit deliverable:** Durable scoped records and an encrypted store, with no credential execution enabled.

### Task 3 — Connector lifecycle, selection and write-only setup APIs

**Files:** `connectors/{service,selection,routes,audit}.py`, `main.py`, `config.py`.

**Tests:** `tests/connectors/test_routes.py`, `test_selection.py`, `tests/integration/test_connector_commands_postgres.py`.

**Migration extension:** `009a_phase5_connector_default_tombstones.py`, revision `phase5a009b`, follows `phase5a009` and retains a cleared default's monotonic settings revision.

- [x] Implement the specification’s project/global route families and project service-default commands.
- [x] Authenticate before handling secret content. Project management requires ownership; global management requires the configured operator user ID.
- [x] Use locked revision preconditions for changes and immutable new revisions for accepted configuration changes. Credential replacement creates a new credential version and explicitly activates a new connector revision.
- [x] Implement the 24-hour request ledger using keyed HMACs. Identical retries return the original safe result; changed requests, foreign requests and expired IDs fail without revealing input.
- [x] Return only safe usage metadata to global consumers. No read route returns ciphertext, nonce, source credentials or decrypted values.
- [x] Resolve explicit selection, then project default, then a sole eligible candidate. Reject ambiguity; do not prefer a scope by name or fall back after failure.
- [x] Enforce origin checks against configured browser origins, bounded setup bodies and database-backed write-rate limits. Secret validation errors must omit rejected inputs.
- [x] Retain a revisioned tombstone when a project default is cleared so stale writes cannot reset its expected revision.

**Critical tests:** Non-operator global mutation denied; foreign project connector hidden; global ciphertext stored once; stale update conflicts; ambiguous selection; revoked default has no fallback; malformed secrets absent from captured logs/errors.

**Commit deliverable:** Safe managed connector configuration and deterministic selection.

### Task 4 — Worker broker and managed search execution

**Files:** `connectors/credentials/broker.py`, `execution/{gateway,availability}.py`, existing run admission/worker/graph modules and search handlers.

**Tests:** `tests/connectors/test_search_broker.py`, `tests/test_managed_search_commands.py`, `tests/integration/test_managed_search_postgres.py`.

- [ ] Implement broker acquisition for a selected search connector. Supply only that service’s key through the existing restricted resolver pattern.
- [ ] Add managed tool graph v2 and chat graph v3. Persist resolved connector/credential references and exact submitted selections in command/configuration digests.
- [ ] Recheck current connector state, eligibility and credential revocation before dispatch and publication, including cached access.
- [ ] Make capability discovery project-aware while retaining the legacy installation catalog for existing clients. Expose readiness separately from permission.
- [ ] Keep plain chat independent of managed credential-store readiness. Offer automatic tools from the project’s resolved capability set; missing unrelated connectors must not block plain chat or workspace tools.
- [ ] Preserve the explicit operator-key compatibility lane until migration/drain. Exact request retries return their original run rather than resolving today’s default.

**Critical tests:** One global key used from two projects with separate evidence; different project keys remain isolated; disable/revoke during dispatch rejects late publication; no secrets in graph/checkpoint/transcript/result surfaces.

**Commit deliverable:** Manual and qualified chat searches using managed credentials.

### Task 5 — Browser setup, visibility and connector selection

**Files:** New connector views/components/store; extend browser API services, types, router, sidebar and composer.

**Tests:** `ConnectorStore.test.ts`, `ConnectorSetup.test.ts`, `ConnectorSelection.test.ts`, fixture Playwright connector tests.

- [x] Add authenticated connector settings with separate Brave/Tavily forms, visible scope and configured readiness.
- [x] Add an owned project-list read API so the browser can resolve the existing personal project before a conversation exists. Include project identity in conversation/snapshot responses.
- [x] Show global management controls only to the configured operator; enforce the same rules on the server.
- [x] Add explicit connector selection and project defaults. Preserve immutable submitted selections during uncertain retries.
- [x] Keep secrets in component-local memory only. Clear them after successful submission or navigation; never place them in Pinia, storage, chat drafts, retry payload stores or URLs.
- [x] After an uncertain secret submission, query its owned request status before asking for re-entry. Never automatically resend a cleared form.
- [x] Show ambiguity and global rotation/revocation impact clearly. Preserve keyboard access, mobile layout and current chat/file controls.

**Verification:** Component tests, typecheck, browser fixtures and production build.

**Commit deliverable:** User-visible managed search setup and selection.

### Task 6 — Rotation, revocation, migration and recovery operations

**Files:** `connectors/credentials/{rotation,recovery}.py`, `connectors/cli.py`, Compose/config examples and a connector operations runbook.

**Tests:** `tests/connectors/test_operations.py`, `tests/integration/test_connector_rotation_postgres.py`.

- [x] Provide explicit operator commands for key initialization, batched re-encryption, managed-dispatch locking/unlocking and operator-search migration.
- [x] Key initialization creates protected files exclusively and refuses overwrites. Never print generated keys.
- [x] Re-encryption uses fresh nonces while retaining logical credential versions. It must resume after interruption and verify retained ciphertext before retiring an old key.
- [x] Migration previews service, visibility and project-default effects. Import only explicitly selected Brave/Tavily bindings; global import requires operator authority.
- [x] Document and test the supported restore sequence: stop workers, restore database/artifacts/configuration, lock managed dispatch, restore keys separately, verify bindings/external access/evidence, then explicitly unlock.
- [x] Mount keys read-only into API and worker services only. Missing keys must leave plain chat usable.
- [x] Keep secret-write HMAC key versions available through their 24-hour retry windows.

**Commit deliverable:** Operable credential lifecycle with recoverable rotation and a dispatch-disabled restore procedure.

### Task 7 — Qualify and accept 5A.1

**Files:** Synthetic stack fixtures, connector integration/browser tests, CI selectors and acceptance record.

- [x] Exercise real API, worker, PostgreSQL and artifact storage with synthetic search credentials.
- [x] Test restart before dispatch, during dispatch and after evidence publication. Only pre-dispatch work may execute after recovery.
- [x] Exercise global revocation from one session while another project dispatches or publishes.
- [x] Scan database records, captured HTTP responses, logs, events, checkpoints and artifacts for synthetic secret sentinels.
- [x] Extend CI selectors so connector integration/full-stack tests actually run; required lanes must have nonzero tests and zero required skips.
- [x] Demonstrate global reuse, project isolation, replacement/revocation and reopening retained evidence.
- [ ] Record the user's accept/revise/stop decision in the qualification record.

**Gate:** Do not admit real AWS credentials or begin 5A.2 before 5A.1 acceptance.

## 3. Tasks 8–16: AWS, observations and gated expansion

### Task 8 — Native SDK runner with bounded transport

**Files:** New `execution/{aws_sdk,aws_transport,network}.py`; extract reusable pinned-network behavior from the search transport without changing search policy.

**Tests:** `tests/execution/test_aws_sdk.py`, `test_aws_transport.py`, existing search transport tests.

- [ ] Add boto3 and explicitly declared cryptography dependencies; freeze compatible versions in the lockfile and check Python 3.11–3.13.
- [ ] Implement `AwsSdkContext.client(service)` and `await AwsSdkContext.run(method, **parameters)`.
- [ ] Use one isolated transport adapter implementing botocore’s HTTP-session boundary. Its synchronous `send` delegates network I/O to the bounded asynchronous HTTP transport and returns a bounded `AWSResponse`.
- [ ] Run SDK calls in a bounded executor. Bridge to the worker event loop without blocking it; cancellation cancels active transport futures and prevents subsequent requests.
- [ ] Keep serialization, signing and response parsing inside the SDK. Isolate the botocore transport integration behind compatibility tests because it uses an internal SDK seam. See [Botocore endpoint](https://raw.githubusercontent.com/boto/botocore/develop/botocore/endpoint.py) and [response contract](https://raw.githubusercontent.com/boto/botocore/develop/botocore/awsrequest.py).
- [ ] Construct clients with explicit credentials/region/endpoints, no environment proxy inheritance, no ambient credential lookup, standard retries with `total_max_attempts=1`, and no automatic paginators/waiters.
- [ ] Configure regional STS/CloudWatch/S3 endpoints, path-style S3 addressing and public-address pinning. Reject redirects, region redirection, custom endpoints, metadata access and unsupported streaming operations.
- [ ] Enforce the shared 30-second deadline, eight physical requests and 2 MiB decompressed response limit before SDK parsing. Hold executor capacity until cancelled work actually stops; do not free capacity while a background request continues.

**Critical tests:** Real SDK encoding/decoding through the adapter; oversized successful/error/STS responses; compressed expansion; DNS rebinding; proxy variables; redirect/retry attempts; cancellation during reads; slot saturation; safe errors containing no signed headers or STS bodies.

**Commit deliverable:** A qualified common execution mechanism, independently testable without AWS credentials.

### Task 9 — AWS role acquisition and session lifecycle

**Files:** `connectors/aws/{contracts,broker,clients}.py`.

**Tests:** `tests/connectors/test_aws_broker.py`.

- [ ] Support the proposed explicit machine-key source, including session token plus mandatory expiration when provided.
- [ ] Acquire only the configured basic role through the common SDK runner. Bootstrap credentials never reach observation handlers.
- [ ] Derive session names from server-owned run identity; verify returned account/role identity before resource reads.
- [ ] Request 900-second sessions. Cache only in memory, separated by consuming project, connector/revision, credential version, role, region and scope.
- [ ] Renew on demand below 120 seconds remaining. Concurrent acquisition for the same cache key shares one bounded acquisition.
- [ ] Recheck current revocation before every dispatch. Expired source sessions require replacement; denied assumption must not select another role/source.
- [ ] Treat acquisition and verification requests as journaled physical dispatches within the observation’s request/deadline budget.

**Commit deliverable:** Temporary, scoped execution-role clients with safe renewal and failure behavior.

### Task 10 — Resource identities and the second migration

**Files:** New `resources/{models,contracts,service,routes}.py`, connector-blocker models and run/artifact model updates.

**Migration:** `010_phase5_resources_and_connector_waits.py`, revision `phase5a010`, following `phase5a009b`.

**Tests:** `tests/resources/test_bindings.py`, `tests/integration/test_phase5_resources_postgres.py`.

- [ ] Add environments, resource objects, immutable resource bindings and observations, plus connector-blocker records.
- [ ] Enforce project/environment/AWS-connector ownership using composite associations. Reject duplicate active mappings for the same project/environment/account/region/bucket.
- [ ] Add `cloud_observation` artifact support and only `waiting_connector` to the run status constraint.
- [ ] Implement bounded owned environment/resource setup and observation listing; do not discover resources from AWS.
- [ ] Resource bindings contain explicit account, region, bucket and expected owner. They remain locators, not proof of existence.
- [ ] Observation metadata may list retained evidence references but must not broaden existing conversation artifact access.
- [ ] Protect downgrade when resource, observation or waiting-run data exists.

**Commit deliverable:** Stable owned resource identities reusable by later provisioning.

### Task 11 — Connection checks and S3 storage observations

**Files:** `tools/aws/{contracts,checks,s3_storage}.py`, definition registration, resource publication and common result projection.

**Tests:** `tests/tools/test_aws_checks.py`, `test_s3_storage.py`, `tests/integration/test_cloud_observation_publication_postgres.py`.

- [ ] Register `aws_check_connection` as an explicit model-free capability; exclude it from automatic model selection.
- [ ] Register `aws_s3_bucket_storage` with only `resource_binding_id` as its tool argument.
- [ ] Resolve account/region/bucket/owner from the pinned binding. Use `GetCallerIdentity` and `GetBucketLocation`; normalize the documented default and legacy region values. Mismatch blocks execution.
- [ ] Query CloudWatch `AWS/S3` / `BucketSizeBytes`, statistic `Average`, period 86,400 seconds and the preceding seven days.
- [ ] Freeze `STORAGE_TYPES_V1` to the 22 documented general-purpose storage dimensions, excluding `ExpressOneZoneStorage`. Include overhead/staging and Intelligent-Tiering dimensions; do not silently adopt future dimensions. See [AWS dimension definitions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html).
- [ ] Enforce at most 64 queries, 1,024 returned datapoints, eight total requests, 30 seconds, 64 KiB evidence and 16 KiB model result.
- [ ] Preserve per-type timestamps and coverage. Prefer a latest common measurement time; never add different dates into a coherent total. Missing values remain unknown; measured zero remains zero.
- [ ] Use “reported storage subtotal” unless completeness is independently established. Empty responses are `no_data`; measurements older than 48 hours are stale.
- [ ] Publish durable evidence bytes before atomically recording ready artifact metadata, observation, safe operation result and outbox events. Register this through the common publication hook.
- [ ] Add a test-only second handler using the same authenticated context/runner, proving extension without modifying credential acquisition or shipping another capability.

**Critical tests:** Wrong account/owner/region; nonfinite or negative values; missing dimensions; mixed dates; duplicate/mismatched timestamps and values; partial results; continuation limits; publication rollback; cancellation/revocation between read and publication.

**Commit deliverable:** Explicit checks and an evidence-backed S3 storage tool.

### Task 12 — Pre-dispatch connector blockers and exact resume

**Files:** `connectors/{blockers,readiness}.py`, existing run service/routes/worker/queue/recovery and event projections.

**Tests:** `tests/test_connector_resume.py`, `tests/integration/test_connector_resume_postgres.py`.

- [ ] Perform local readiness checks before any model/tool dispatch intent. Do not make an external authentication probe during preflight.
- [ ] Reject missing/ambiguous connector selection before admitting an unbound run. Park only runs with exact pinned references and a recorded pre-dispatch blocker.
- [ ] Atomically record blocker/event/outbox, set `waiting_connector` and release job ownership, slot and lease. The existing queue must not reclaim waiting runs.
- [ ] Implement the specified resume command with blocker ID, request ID and expected configuration digest. Consume once, recheck unchanged references, serialize against conversation activity and requeue under a new fence.
- [ ] Persist the original context message IDs/cutoff. A resumed turn must not consume later messages or overwrite a newer active-run/title projection.
- [ ] Expire blockers within 24 hours, shortened by an existing execution deadline. Resume never extends the deadline.
- [ ] Cancellation, dismissal and expiry close the blocker and release pins.
- [ ] Credential/target revision changes require explicit resubmission as a new run. Any access failure after dispatch intent fails safely with retained prior evidence; no automatic replay.

**Critical tests:** Resume versus cancel/expiry/new chat; duplicate resume; conflicting request ID; restored key with unchanged references; replacement credential requiring a new run; old turn completing after newer conversation activity.

**Commit deliverable:** Safe, limited connector recovery without introducing general workflow approval machinery.

### Task 13 — AWS setup, target selection and observation UX

**Files:** Extend connector browser views/store, resource controls, composer, operation card, run card, files panel and SSE/snapshot handling.

**Tests:** AWS setup, resource selection, observation and blocker component tests; fixture Playwright flows.

- [ ] Add project-only AWS setup with authentication source, configured role/account/region, required trust/permissions and readiness.
- [ ] Add explicit environment/bucket binding and connection-check commands through the existing run queue.
- [ ] Let the composer select owned bindings and attach the storage tool or enable qualified automatic tool use.
- [ ] Display source, retrieved time, measurement time, stale/missing/partial state, coverage and retained evidence independently of assistant interpretation.
- [ ] Show connector blockers with setup, eligible resume, cancellation and explicit resubmission actions. Plain conversation remains available.
- [ ] Preserve exact retry payloads, stale-response guards, keyboard access and responsive layout. Do not render maintenance-role controls.

**Commit deliverable:** Complete user-facing AWS observation and recovery flow.

### Task 14 — Qualify and accept 5A.2

**Files:** AWS synthetic transports/full-stack tests, PostgreSQL race tests, real-stack browser tests, runbook and AWS qualification record.

- [ ] Use actual boto3 clients and protocol-compatible synthetic responses through the bounded transport; do not qualify the transport solely with SDK method mocks.
- [ ] Restart API/worker before intent, after acquisition/read and before publication. Verify zero automatic replay of dispatched requests.
- [ ] Exercise key loss, connector revocation, missing artifact bytes, restore locking, worker incompatibility and blocked-turn conversation continuity.
- [ ] Update existing CI selectors to run the new PostgreSQL, synthetic-stack and browser tests with zero required skips.
- [ ] Document administrator setup for the assume-role-only source and basic read role. Disclose that CloudWatch metric authority is broader than the selected bucket.
- [ ] With separate live opt-in, compare identity and storage observations with an independent read for the same dimensions/time; qualify renewal and safe denial behavior.
- [ ] Never test prohibited mutations against the user’s bucket. Use reviewed policy evidence or a separately authorized disposable denied-mutation fixture.
- [ ] Record the source, SDK/tool/configuration revisions, timestamps, limits and remaining qualification gaps. Obtain accept/revise/stop.

**Gate:** Do not start 5B before 5A.2 acceptance.

### Task 15 — Gated 5B inventory and metadata handlers

**Files:** `tools/aws/{bound_inventory,s3_metadata}.py`, registered definitions and resource/browser projections.

**Tests:** `tests/tools/test_bound_inventory.py`, `test_s3_metadata.py`.

- [ ] Implement `aws_s3_list_bound_buckets` from owned local bindings, with page size 20/max 50 and an explicit stored-inventory source label. It performs no AWS discovery.
- [ ] Implement `aws_s3_bucket_metadata` for one selected binding using location and versioning reads through the same broker/runner.
- [ ] Add only the separately accepted bucket-scoped `s3:GetBucketVersioning` authority.
- [ ] Use independent manifest digests and the common target, budget, observation and evidence contracts.
- [ ] Test foreign bindings/cursors, pagination bounds, missing resources, unsupported responses and inherited cancellation/revocation controls.

**Commit deliverable:** Bounded read expansion without another backend or credential abstraction.

### Task 16 — Qualify 5B and close Phase 5

- [ ] Run PostgreSQL, synthetic-stack and browser scenarios for inventory/metadata.
- [ ] Perform separately opted-in live read comparison and record the additional IAM authority.
- [ ] Verify that enabling 5B neither broadens existing storage-tool manifests nor changes old admitted configuration.
- [ ] Update acceptance/runbook records and obtain accept/revise/stop.
- [ ] Declare Phase 5 complete only when its delivered units are accepted; otherwise identify 5B as undelivered.

## 4. Verification, rollout and assumptions

### Verification commands

Run targeted tests during each task. Run the complete relevant lanes at acceptance checkpoints:

```bash
# apps/api
uv run ruff check .
uv run pytest tests/ -q -m 'not postgres and not provider_live and not full_stack'
uv run pytest tests/integration -q -m postgres
uv run pytest tests/full_stack -q -m full_stack

# apps/web
pnpm typecheck
pnpm test:run
pnpm build
pnpm test:e2e
pnpm test:e2e:full-stack
```

PostgreSQL lanes use a disposable PostgreSQL 15 database via `TEST_POSTGRES_URL`. Browser fixtures remain separate from real-stack qualification. Live selectors stay disabled in ordinary CI.

Required acceptance evidence includes nonzero tests, zero required skips, synthetic secret-sentinel checks and explicit request counts around crash/cancellation boundaries. Tests and documentation alone do not establish live IAM qualification.

### Rollout and compatibility

- Drain old workers and queued legacy execution before activating managed graph/configuration versions.
- Preserve legacy parsers, history reads and exact retry responses. If an old implementation digest cannot execute, fail visibly rather than substituting replacement code.
- Apply migrations in sequence; do not import secrets or call providers during migration.
- Enable managed search first, then AWS only after its runner and acceptance gates pass.
- Advertise supported graph/manifest versions and fresh project readiness through existing runtime availability machinery; incompatible workers must not receive managed runs.
- Restore using the documented dispatch-lock procedure. Retain old encryption/HMAC keys until all dependent ciphertext and retry windows have been accounted for.
- Inspect safe counts/reason codes for unavailable connectors, failed acquisition, blockers, bounded failures and stale publication attempts. Do not log secret-bearing arguments, signed requests or raw provider errors.

### Assumptions and defaults

- The approved specification governs scope; the first AWS qualification uses the proposed dedicated assume-role-only machine-key source.
- Global management uses one deployment-configured existing operator account. Additional team-role administration is deferred.
- Existing personal projects remain the project model; global reuse is demonstrated across distinct owners’ projects.
- Native SDK transport integration is pinned and compatibility-tested. It does not claim compatibility with arbitrary future SDK versions or streaming operations.
- Each acceptance unit includes its own operational documentation and qualification record.
- Implementation has not started. The recommended execution method is native, sequential work because schema, credential, gateway and worker changes share tightly dependent interfaces.
