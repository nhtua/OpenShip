---
type: plan
summary: Remove resource binding from new AWS execution and add discovery and bounded SDK reads through the connector basic role.
date: 2026-10-23
status: approved
---

# Phase 5 Binding Removal and AWS Discovery Implementation Plan

> **For agentic workers:** The user approved this plan on 2026-10-23 and authorized implementation. Use `superpowers:executing-plans` for sequential implementation in the existing PR #29 worktree. Read the spec first. Track each step with its checkbox and preserve the previous acceptance records.

**Goal:** Let OpenShip discover and inspect AWS resources through a project's basic role without registering, binding or selecting each resource first.

**Architecture:** Reuse the maintained tool registry, shared operator policy, role broker, native SDK runner, durable gateway and atomic evidence publication. Add a versioned SDK read catalog and shared reader; authorize the connector locally and let AWS decide resource access for each request. Keep old binding records only where historical references require them.

**Tech Stack:** Existing Python/FastAPI/Pydantic/SQLAlchemy/Alembic/PostgreSQL, boto3/Botocore, LangGraph, Vue/TypeScript/Pinia, pytest/Vitest/Playwright. No new runtime service, credential source or sandbox.

**Spec:** [AWS discovery through the basic role](../specs/2026-10-23-phase5-aws-role-based-discovery-design.md). This plan replaces the remaining binding-based work in the [earlier 5B addendum](2026-10-23-phase5b-tool-availability-inventory-and-metadata.md), while preserving its managed tool availability work.

## Global Constraints

- Phase 5 uses the basic role only. Maintenance execution remains in Phase 10A/10B.
- There is no OpenShip per-resource whitelist, binding prerequisite or mandatory resource selection for new reads.
- AWS decides effective resource access for each supported request. Do not require IAM introspection, list permission, resource ownership by the execution account or a successful optional preliminary read.
- Authenticate only through the configured source and pinned basic role; never fall back to source credentials, another connector, ambient SDK credentials or a different role.
- Writes, permission changes, credential vending, application-secret extraction, role chaining, event streams and arbitrary streaming body downloads are unsupported. Unknown operation classification fails before provider dispatch. Report unsupported and access denied separately.
- Preserve project/actor authorization, archive/restore, secret-safe logs, cancellation, budgets, exact retries, operator policy, deployment ceilings and fenced publication.
- Per external operation: 30 seconds, 8 physical requests including role acquisition/identity checks, 2 MiB per HTTP response, 64 KiB evidence, 16 KiB model result; provider pages default 20 and maximum 50.
- New AWS chat runs: execution schema v4, chat graph v4, 12 tool calls, 13 model turns, 15 generation attempts, 1,000 output tokens/request, 15,000 reserved output tokens total, and a 180-second loop deadline. Lower existing profile/run caps remain authoritative; reserve the final affordable generation for a summary. Old versions retain their own limits and meaning.
- No data deletion except existing secret purging. No hidden bindings. History, evidence and original acceptance records remain readable.
- Implement and qualify sequentially. Live AWS comparisons require separate authorization; use synthetic provider responses and disposable databases by default.

## Review Focus

1. Listing is denied, but a named target is readable: execute the direct read without requiring discovery/location permission; Task 3 and Task 5.
2. SDK/metadata mismatch or a misleading `Get` operation exposes writes or credentials: require trusted complete classification and protocol support; Task 1 and Task 3.
3. Existing binding/evidence data or pending runs are silently deleted/reinterpreted: migrate forward, preserve foreign keys and reject unavailable exact refs visibly; Task 2 and Task 7.
4. Regions, shared resources, cursors or several connectors cause credential substitution: pin connector/catalog, validate cursor scope and use only the requested session; Task 3 and Task 4.
5. A multi-read report exhausts budgets or has denied/missing fields: preserve partial findings, honest timestamps/coverage and exact evidence; Task 5 and Task 7.

## File and Responsibility Map

| Files | Responsibility |
| --- | --- |
| `apps/api/scripts/update_aws_read_catalog.py`; `src/openship/connectors/aws/read_catalog.py`; `src/openship/connectors/aws/data/read_operations.v1.json` | Developer-only snapshot generation, complete read classification, SDK shapes and catalog identity |
| `src/openship/tools/aws/{read_catalog,read,contracts}.py` | Local API discovery capability, bounded SDK read handler, public/evidence contracts |
| `src/openship/connectors/aws/{broker,clients}.py`; `src/openship/execution/{aws_sdk,aws_transport}.py` | Existing role acquisition, operation gate, service/region-aware SDK contexts and pinned endpoints |
| `alembic/versions/014_aws_observations_without_bindings.py`; `src/openship/resources/{models,contracts,service,routes}.py` | Forward migration, binding-free observations, retirement of setup, historical reads |
| `src/openship/capabilities/{contracts,catalog,definitions,policy,routes}.py`; `src/openship/runs/{schemas,routes,service,agent_graph,dispatch,worker,queue,budget,transcript}.py`; `src/openship/connectors/blockers.py`; `src/openship/execution/{admission,gateway,publication,availability,projections}.py` | Versioned admission/dispatch, model definitions, policy/readiness, recovery and publication |
| `src/openship/tools/aws/{checks,s3_storage,s3_metadata}.py` | Identity-only setup check and existing S3 helpers with direct targets |
| `apps/web/src/{types/index.ts,services/api.ts,stores/chat.ts,stores/resources.ts,stores/connectors.ts}`; chat and connector components below | Connector selection, direct target forms, backward-compatible history, automatic refresh |
| `docs/roadmap.md`, Phase 5 specs/plans, AWS runbooks and qualification report | Consistent scope, setup, acceptance and limitations |

Paths under `src/` and `alembic/` above are relative to `apps/api/`. Add small
modules alongside existing AWS code rather than making a second registry,
generic provider hierarchy, IAM editor or discovery service.

---

## Task 1 — Versioned AWS Read Catalog

**Files:** Create `apps/api/scripts/update_aws_read_catalog.py`, `apps/api/src/openship/connectors/aws/read_catalog.py`, `apps/api/src/openship/connectors/aws/data/read_operations.v1.json`, `apps/api/src/openship/tools/aws/read_catalog.py`, and `apps/api/tests/connectors/test_aws_read_catalog.py`.

**Interfaces:**

- `AwsReadOperation`: immutable descriptor with `service`, `operation`, `api_name`, `iam_actions`, `input_schema`, `output_schema`, `protocol`, `pagination`, `supported` and `unsupported_reason`.
- `AwsReadCatalog`: immutable `digest`, SDK version, source provenance and operation descriptors.
- `load_aws_read_catalog() -> AwsReadCatalog` validates the packaged snapshot against the installed SDK.
- `require_read_operation(service: str, operation: str) -> AwsReadOperation` refuses missing/unsafe/unsupported entries.
- Local `aws_read_catalog` arguments: optional `service`, optional `operation`, `limit=20` (maximum 50), optional scoped cursor. Return bounded service/operation pages or one parameter schema. No connector or AWS request is required; normal project/conversation tool authorization still applies.

- [x] **Write classification tests.** `test_read_catalog_requires_complete_action_annotations` accepts known read/list mappings, refuses absent booleans, missing/cross-service action records and mixed write/read mappings. `test_get_prefix_does_not_prove_read_only` refuses a credential-vending or mutation operation despite its name. `test_secret_value_operations_are_explicitly_unsupported` refuses Secrets Manager value reads and SSM parameter reads even with `WithDecryption=false`, without returning secret fields or mislabeling the refusal as AccessDenied. `test_unknown_sdk_version_or_digest_fails_closed` refuses stale/incomplete snapshots.
- [x] **Write schema/catalog tests.** Prove nested required fields, unions supported by the installed models, enums and list/map limits; reject unknown parameters, excessive depth and recursive/input-schema expansion beyond 16 KiB. Keep the compact model result at 16 KiB; allow trusted output schemas up to 64 KiB so broad SDK responses such as EC2 `DescribeInstances` remain representable. Paginate service/operation lists; never silently omit required parameter schema fields. Catalog lookup must make zero broker/network calls.
- [x] **Run the new tests red.** From `apps/api`: `uv run pytest tests/connectors/test_aws_read_catalog.py -q`; expect missing interfaces or assertions to fail.
- [x] **Implement snapshot generation and validation.** Fetch only official AWS service-reference metadata in the developer command, record provenance/SDK/schema-conversion versions, per-operation schema digests and catalog digest, and emit deterministic output. Join SDK method mappings to all authorized IAM actions and their complete annotation properties; do not infer safety from names. Include supported and explicitly unsupported descriptors. Runtime reconstructs schemas from the exact installed Botocore models and verifies their pinned digests; it does not download metadata. Check in the generated snapshot. Record reviewed overrides with sources when metadata lacks a required acceptance operation.
- [x] **Prove broad reuse.** Catalog fixtures must cover S3 `list_buckets`, `get_bucket_location`, `get_bucket_versioning`, `get_bucket_policy_status`, `get_public_access_block`, `get_bucket_acl`; CloudWatch `get_metric_data`; EC2 `describe_instances`; RDS `describe_db_instances`; and STS `get_caller_identity`. Support protocols implemented by the pinned Botocore serializer and parser, including Smithy RPC v2 CBOR. Missing official mappings are resolved explicitly, not bypassed by a permissive fallback.
- [x] **Verify and commit.** Run the catalog tests and API Ruff for touched files. Commit a self-contained, tested catalog module using the repository's scanned message-file workflow. No existing execution changes yet.

## Task 2 — Observation Migration and Versioned Contracts

**Files:** Create `apps/api/alembic/versions/014_aws_observations_without_bindings.py` (`phase5b014`, parent `phase5b013`), `apps/api/tests/integration/test_aws_discovery_migration.py`; modify `apps/api/src/openship/resources/{models,contracts,service}.py`, `apps/api/src/openship/capabilities/contracts.py`, `apps/api/src/openship/tools/aws/contracts.py` and `apps/api/tests/execution/test_contracts.py`.

**Interfaces:**

- `ExecutionConfigV4`: standalone frozen contract preserving common limits, capabilities, artifact refs, connector refs, admitted message refs and preferred search kind; adds `aws_read_catalog_digest`; contains no resource-binding refs. Extend `ExecutionConfigLike` and its exact decoder without changing v1–v3.
- `CapabilityManifestV3`: extends maintained manifest v2 with `aws_read_catalog_digest` and exact schema version 3. Require the digest for `aws_read`/`aws_read_catalog`; their operation set is the pinned catalog, not an unrestricted empty `operations` tuple. Extend the manifest type alias/decoder while retaining v1/v2 validation and identities.
- `AwsReadArguments`: `connector_id: UUID`, `service: str`, `operation: str`, `region: str | None`, `parameters: dict[str, object]`, `cursor: str | None`, `page_size: int = 20` (1–50).
- `AwsReadObservation`: evidence schema 2; exact tool/operation/project/connector/catalog identities, execution account, service/operation/region, safe parameters/target metadata, retrieval time, request count, bounded normalized data and continuation/coverage.
- `AwsReadResult`: compact identity/source/time/outcome, bounded preview with explicit `preview_truncated`, optional cursor and exact artifact reference; no binding/environment requirement. Failures use the existing public `ToolFailure` shape.
- `record_aws_read_observation(db, *, authorization, operation, observation, artifact) -> ResourceObservation`: transaction-local publication helper that creates no binding.

- [ ] **Write migration and decoder tests.** Seed a pre-migration database with accepted resource/environment/binding/observation/operation/artifact rows; upgrade and assert every original ID, link, digest and timestamp survives. Insert a new generic observation without resource/binding/environment rows. SQLite foreign keys must be enabled; the PostgreSQL variant must run in its configured lane.
- [ ] **Run contract and migration tests red.** `uv run pytest tests/execution/test_contracts.py tests/integration/test_aws_discovery_migration.py -q -m 'not postgres'`.
- [ ] **Implement the forward migration.** Retain all legacy tables and rows. Make legacy resource/binding references optional only for the new observation shape; preserve old composite foreign keys when present. Add explicit observation schema version, connector ID/revision, execution account, service, operation, region and bounded target JSON, with project/connector revision foreign keys. Enforce a check distinguishing valid legacy rows from complete v2 rows. List/discovery observations may have no single resource object. Do not rewrite old evidence bytes.
- [ ] **Implement manifest-v3, execution-v4 and result contracts.** Test exact manifest decoding, catalog-digest validation and rejection of unrestricted generic declarations; keep all existing decoders/renderable public shapes. Version S3 helper evidence/results for direct-target observations, rather than reusing old required binding fields with fake IDs. Use shared canonical JSON and existing artifact/operation contracts. Implement `record_aws_read_observation` as a transaction-local database helper with no artifact/network I/O; test valid v2 insertion and rejected foreign references before later gateway integration.
- [ ] **Verify and commit.** Run the contract/migration tests on SQLite and disposable PostgreSQL. Verify foreign connector/operation/artifact references fail on both databases; no required test skips in the PostgreSQL lane.

## Task 3 — Shared Reader, Regional SDK Access and Safe Failures

**Files:** Create `apps/api/src/openship/tools/aws/read.py`, `apps/api/tests/tools/test_aws_read.py`; modify `apps/api/src/openship/connectors/aws/{clients,broker}.py`, `apps/api/src/openship/execution/{aws_sdk,aws_transport}.py`; extend `apps/api/tests/connectors/test_aws_operation_profiles.py`, `apps/api/tests/execution/{test_aws_sdk,test_aws_transport}.py`.

**Interfaces:**

- `execute_aws_read(arguments: AwsReadArguments, context: ToolInvocationContext, control: InvocationControl) -> AwsReadObservation`.
- Extend `AwsRoleAccess.client(service: str, *, region: str | None = None)` and the SDK context's matching client resolver; use catalog-validated `(service, operation)` descriptors for generic calls. Check the descriptor against the manifest/execution catalog digest before dispatch; a method name alone cannot authorize a same-named method on another service. Existing exact capability profiles remain authoritative for historical helpers.
- General-reader pagination cursor binds project, pinned connector revision, catalog digest, service/method, resolved region and canonical parameter digest. Reuse the installation HMAC facility through a dedicated namespace; reject foreign/tampered/expired tokens. Cursors expire after 30 minutes and require a fresh tool call and authorization check.

- [ ] **Write direct-reader tests.** List live synthetic S3 buckets with no local resource rows; read a named bucket after listing is denied; read EC2/RDS with no bespoke handlers; allow a supplied cross-account target when AWS permits it; deny without switching roles/connectors. An optional location lookup being denied must not block an explicitly regional versioning read.
- [ ] **Write boundary tests.** Mutation, credential issuance, unknown operation, invalid parameter, alternate endpoint, foreign connector and unknown catalog are rejected with zero provider requests. Exercise legitimate regional/global endpoints, S3 query paths, private DNS resolutions, redirects and malicious destinations through the production transport.
- [ ] **Write budget/pagination/error tests.** Assert 20/default and 50/max page limits, one provider page per call, 30-second/8-request/2-MiB bounds, scoped 30-minute cursor expiry, 64-KiB evidence and 16-KiB compact result. `test_oversized_provider_page_does_not_advance_cursor` fails the entire page, publishes no observation, and successfully retries the same cursor with a smaller page size; model-preview truncation must not change continuation. Use AWS-style AccessDenied/403 and service-specific not-found/throttle responses; distinguish them from trust failure, unsupported protocol and unavailable service. Secret sentinels must never reach result/error/log output.
- [ ] **Run the tests red.** `uv run pytest tests/tools/test_aws_read.py tests/connectors/test_aws_operation_profiles.py tests/execution/test_aws_sdk.py tests/execution/test_aws_transport.py -q`.
- [ ] **Implement the reader and extend native SDK contexts.** Use the installed endpoint resolver and exact expected endpoint origin with existing network pinning. Keep explicit credential injection and disabled ambient/profile endpoint configuration. Validate parameters before acquisition; normalize timestamps and decimals, omit SDK transport metadata, and reject unsupported binary/stream/token-bearing outputs before publication. Do not use IAM simulation or listing as a permission precheck.
- [ ] **Implement permission copy.** Map actual authorization refusal to `aws_access_denied`: `The AWS connector role does not have permission to perform this operation.` Do not persist raw ClientError bodies. Preserve a separate trust-denied state for `AssumeRole`, and typed codes for not-found, throttle, invalid parameters and unsupported operations.
- [ ] **Verify and commit.** Run the named tests and Ruff. Provider protocol fixtures stub network I/O below destination validation, not the entire SDK/transport path. No live calls.

## Task 4 — Connector Admission, Policy and Model-Directed Discovery

**Files:** Modify `apps/api/src/openship/capabilities/{catalog,definitions,policy,routes}.py`, `apps/api/src/openship/runs/{schemas,routes,service,agent_graph,dispatch,worker,queue,budget,transcript}.py`, `apps/api/src/openship/connectors/blockers.py`, `apps/api/src/openship/execution/{admission,gateway,availability,publication,projections}.py`; extend `apps/api/tests/{test_tool_policy,test_capability_routes,test_tool_turn_commands,test_tool_run_commands,test_run_dispatch,test_run_transcript,test_archive_admission}.py`, `apps/api/tests/providers/test_tool_budgets.py`; add `apps/api/tests/integration/test_aws_discovery_admission_postgres.py` and `apps/api/tests/integration/test_aws_read_publication_postgres.py`.

**Interfaces:**

- Extend turn/manual-command `connector_selections` to accept optional `aws: UUID` alongside existing search selections. A new AWS helper/general read carries `connector_id` and must match the admitted pinned connector. New requests omit resource selections; explicitly nonempty legacy binding-selection fields return `resource_binding_retired` rather than being ignored.
- `_resolve_aws_connector_for_run(db, *, principal_id, project_id, selected_id) -> ConnectorRef` resolves the explicit owned connector or sole eligible one. Several connectors require a connector choice; no automatic cross-account credential fallback.
- Register `aws_read_catalog` (local) and `aws_read` (external) in the maintained registry, using the existing shared operator policy and worker readiness machinery.

- [ ] **Write admission/model tests.** Automatic AWS tools appear with a configured connector and no binding rows. The model can call catalog → list → read using a target obtained from prior output. Manual read/target arguments are pinned into operation identity. Foreign/archived projects, ambiguous connectors, disabled tools and mismatched connector IDs cannot dispatch. Search defaults/manual choices retain their current behavior.
- [ ] **Write version/budget tests.** `(chat, 4)` and `(tool, 3)` resolve explicitly. V4 permits 12 tool calls/13 model turns with 15 maximum generation attempts, 1,000 output tokens/request, 15,000 reserved output tokens total and the capped 180-second deadline. A profile with `max_attempts=15` reaches a final summary after 12 reads through real budget reservations; a profile capped at 3 retains that cap and gets tools removed on its final affordable turn. Retries consume attempt/output reservations; no thirteenth tool dispatch or unreserved final generation is permitted. V1–v3 retain their old schemas/budgets. Cover worker, queue, transcript, gateway and budget version checks, not only the graph registry.
- [ ] **Write policy and exact-ref tests.** On upgrade, seed `aws_read` disabled; a designated operator enables it once in Global Settings without restarts. Fresh installs use the same explicit opt-in. Preserve old overrides, unknown/retired deployment IDs and explicit `TOOL_ENABLED_IDS` ceilings. A catalog/digest mismatch or unavailable historical handler stops execution visibly without replay or automatic widening.
- [ ] **Run the tests red.** Run the touched admission/policy/dispatch/transcript tests plus `tests/providers/test_tool_budgets.py`; run the PostgreSQL admission tests separately with `-m postgres`.
- [ ] **Implement the new admission path.** Pin connector and catalog identity in V4, remove selected-resource model filters for new runs, and include safe connector/default-region context for the model. Preserve actual target arguments in each gateway operation; no binding lookup is part of new dispatch. Model tool definitions describe permission failures, targeted discovery and finite pagination. Remove tool definitions on the last affordable generation, accounting for existing reservations/profile caps, to retain room for a summary; never silently increase the model profile's 3-attempt default. Never treat local SDK catalog support as proof of AWS access.
- [ ] **Integrate publication, recovery and policy fences.** Attach the generic reader's definition to existing cloud-observation artifact publication and the Task 2 database helper. Recheck owned/active project and conversation, connector revision/expiry/revocation, catalog identity, tool policy and run lease before requests/publication. Test atomic generic observation/evidence publication, failed fences and exact evidence reopening now, so this task delivers a usable reader. Register chat-v4/tool-v3 across every durable version gate. Preserve existing terminal statuses and connector-wait behavior; no new scheduler or permission system.
- [ ] **Verify and commit.** Focused API and PostgreSQL admission lanes pass. Explicitly verify a running API/worker observes policy enable/disable without restart and no credential fallback after AccessDenied.

## Task 5 — Direct S3 Helpers and Binding-Free Evidence

**Files:** Modify `apps/api/src/openship/tools/aws/{contracts,checks,s3_storage,s3_metadata}.py`, `apps/api/src/openship/capabilities/definitions.py`, `apps/api/src/openship/execution/{publication,projections}.py`, `apps/api/src/openship/resources/service.py`; extend `apps/api/tests/tools/{test_aws_checks,test_s3_storage,test_s3_metadata}.py`, `apps/api/tests/integration/test_aws_discovery_admission_postgres.py`; add `apps/api/tests/integration/test_aws_read_publication_postgres.py`.

**Interfaces:**

- New storage/metadata input version: `connector_id: UUID`, `bucket_name: str`, `region: str | None = None`, `expected_owner: str | None = None`. Use shared target validation without importing binding-create contracts. Their exact capability refs/version identify the new signature.
- Setup connection check accepts only connector ID and verifies role identity. Bucket inspection uses regular target-based read commands.
- `record_aws_read_observation` from Task 2 participates in the existing atomic artifact/operation/event publication transaction. Evidence names for direct storage/metadata remain `s3-bucket-storage.json` / `s3-bucket-metadata.json`; generic reads use `aws-read.json`.

- [ ] **Write helper tests.** Inspect/measure a never-registered bucket; accept a supplied region when optional region-discovery permission is absent; use ExpectedBucketOwner only if supplied; preserve storage missing/partial/stale semantics and metadata enabled/suspended/not-enabled/MFA-not-reported states. Do not turn absent CloudWatch metrics into zero.
- [ ] **Write publication tests.** Successful observation and immutable evidence publish together without a binding row. Cancellation, lease loss, policy disablement and connector revocation after a read prevent publication and clean provisional bytes. Reopen exact legacy and v2 evidence after artifact-store and API/worker restart; no provider request occurs on reread.
- [ ] **Run tests red.** `uv run pytest tests/tools/test_aws_checks.py tests/tools/test_s3_storage.py tests/tools/test_s3_metadata.py -q`; run new publication integration cases with configured disposable PostgreSQL.
- [ ] **Implement helper/publication changes.** Reuse the new reader's shared catalog/client/normalization utilities without a nested gateway call or second operation. Each helper retains its exact bounded operation profile. Store safe per-operation target metadata and connector revision; no automatic binding is created. New discovery results may have no single resource ID. Old observation projections keep their original fields and renderers.
- [ ] **Add report protocol fixtures.** Through the generic reader, exercise timestamped `NumberOfObjects` (`AWS/S3`, `BucketName`, `StorageType=AllStorageTypes`, daily `Average`, `Count`), policy-public status, bucket Block Public Access and ACL reads. Record permissions and interpretation in the runbook. Do not create a new reporting subsystem or claim these fields were already supported in the accepted storage helper.
- [ ] **Verify and commit.** Helpers, production-transport SDK tests and PostgreSQL publication/recovery tests pass; all new observations/evidence are binding-free and old evidence remains byte-identical.

## Task 6 — Remove Binding Setup and Simplify the Browser

**Files:** Modify `apps/api/src/openship/resources/{routes,service,contracts}.py`, `apps/api/src/openship/capabilities/{catalog,definitions}.py`; remove active `apps/api/src/openship/tools/aws/bound_inventory.py`; modify `apps/web/src/views/ConnectorSettingsView.vue`, `apps/web/src/components/connectors/AwsConnectorDialog.vue`, `apps/web/src/components/chat/{ChatInput,ToolAttachmentMenu,ToolOperationCard,S3StorageObservation,S3MetadataObservation}.vue`, `apps/web/src/stores/{chat,resources,connectors}.ts`, `apps/web/src/services/api.ts`, `apps/web/src/types/index.ts`, `apps/web/src/components/settings/GlobalSettings.vue`; remove setup-only `apps/web/src/components/connectors/AwsResourcesPanel.vue`; add `apps/web/src/components/chat/AwsReadObservation.vue`. Update existing corresponding web tests and add `apps/web/tests/AwsReadObservation.test.ts`.

**Interfaces:**

- Chat drafts/retry payloads carry AWS connector selection and direct target arguments, never a required binding ID. New project state does not fetch binding/environment lists on every conversation load.
- New operation renderer shows service/operation, connector/account/region, retrieval time, bounded preview, partial/continuation status and exact evidence. It treats cloud data as untrusted content.
- Authenticated owner-scoped POST `/api/projects/{id}/resources` and POST `/api/projects/{id}/environments` return `410` with `resource_binding_retired`. Retain authorized historical GET/observation/evidence reads only. Remove active binding services and inventory definitions without deleting stored rows or old serialized contracts.

- [ ] **Write browser/API tests.** Connector settings has no binding/environment form or bound-bucket check selector. A new conversation can enable AWS automatic tools with zero resource rows. Manual S3 helpers accept bucket text/region; multiple connectors have a clear connector choice. Status check submits before navigation. Foreign owners receive 404 rather than retirement details; archived-project lifecycle behavior remains explicit.
- [ ] **Write rendering/refresh tests.** Old binding-based operations still render and download exact evidence. Generic results and safe permission errors render without raw SDK bodies. Completing a read refreshes files/results without reload. A late response from another project/connector cannot replace current state; exact retries preserve connector and direct parameters.
- [ ] **Run tests red.** Run named component/store tests using `pnpm exec vitest run` and the affected API route tests. Replace setup-only tests with binding-retirement/no-binding coverage; retain historical evidence tests.
- [ ] **Implement setup removal and new browser flow.** Keep existing connector card/dialog action layout and configured-state styling. Remove the S3 binding selector and request dependencies, add connector choice only when useful, keep manual helper entries, and expose generic AWS reader availability. Global Settings has one broad AWS reader enablement, not one toggle per SDK operation. Old helper toggles control helper availability only; the generic reader independently exposes supported reads and is not an IAM action denylist.
- [ ] **Verify and commit.** From `apps/web`: `pnpm typecheck`, affected Vitest tests, then `pnpm build`. Verify new workflows make no binding/environment setup requests. API retirement/history tests pass.

## Task 7 — Qualify the Replacement and Update Acceptance Records

**Files:** Extend `apps/api/tests/full_stack/{synthetic_aws_worker,test_managed_connectors}.py`, affected `apps/web/tests/e2e/` fixtures/specs and API regression fixtures; create `docs/testing/phase5-aws-discovery-qualification.md`; update `docs/operations/{aws-s3-observations,tool-availability}.md`, `docs/roadmap.md`, `docs/architects.md`, parent Phase 5 spec/plan and earlier 5B qualification links.

**Interfaces:** One synthetic end-to-end fixture provisions two project owners,
AWS connector credentials and protocol-compatible cloud responses, with zero
environment/binding rows for the new flows. No real key is required.

- [ ] **Add and run the no-binding browser flow.** Enable the shared reader in Global Settings, discover S3, inspect a discovered and separately supplied bucket, read EC2/RDS, change region, and retain evidence through restart. Repeat under a second owner/project and verify isolation. The role may expose a shared external resource while OpenShip artifacts remain project/conversation-owned.
- [ ] **Add denial/partial-report scenarios.** Deny ListBuckets but permit direct GetBucketVersioning; deny public-access reads while permitting size; omit object metrics; paginate more than 50 buckets; exhaust the 12-call budget. Assert the resulting tool transcript and report do not claim unknown fields are zero/private, all resources were enumerated, or unsupported operations lack IAM permission.
- [ ] **Verify role and catalog boundaries.** Try a mutation and a credential-vending operation, stale catalog, revoked connector, expired source, mismatched account identity, malformed cursor and cursor reuse under a different owner. Prove zero prohibited dispatches and no secret sentinels in logs/evidence/transcripts. Disable policy between admission/read/publication and prove the existing fences.
- [ ] **Run required API checks.** From `apps/api`: `uv run ruff check src tests scripts`; `uv run pytest tests/ -q -m 'not postgres and not provider_live and not full_stack'`; `uv run pytest tests/integration -q -m postgres`; and `uv run pytest tests/full_stack/test_managed_connectors.py -q -m full_stack`. Use disposable services and collect exact results. The previously stalled synchronous TestClient case must complete in CI or be fixed in the harness; do not skip it or call an unfinished suite a pass.
- [ ] **Run required web checks.** From `apps/web`: `pnpm typecheck`, `pnpm test:run`, `pnpm build`, and the affected Playwright scenarios through the synthetic stack. Required configured lanes must have zero unexpected skips. Run binding-free flows plus accepted search/default/fallback, archive/restore and evidence-refresh regressions once; repeat only after a relevant failure or change.
- [ ] **Update documentation and handoff.** Record commands, counts, limits, upgrade steps and the supported/unsupported catalog surface. Keep earlier 5A.2/5B evidence intact and link the replacement. Explain SDK catalog support versus AWS permission, optional deployment ceilings, direct-target access despite denied discovery, known cross-account/region behavior, object metric semantics and qualified public-access reporting. Configure the qualified report-test profile with `max_attempts=15`; document that a complete 12-call report needs at least 13 generation attempts and that existing lower operator/profile budgets intentionally produce shorter/partial runs. Do not change global model defaults as an incidental binding-removal edit.
- [ ] **Optional live comparison with separate authorization.** Use the user's basic role to compare selected discovery/read results against an independent console/API read; record each permission denial honestly. No IAM edits, maintenance-role assumption or mutations. Synthetic evidence is not a claimed live comparison.
- [ ] **User acceptance.** Present the plan's manual scenarios below, report this replacement as a candidate and request acceptance. Do not declare Phase 5 complete until acceptance and recorded qualification gaps are resolved or explicitly accepted.

## Manual Evaluation After Implementation

1. Configure/check one AWS connector; create no environments or bindings.
2. Enable AWS reads in Global Settings, without adding per-operation environment entries. Toggle availability with API/worker running.
3. Ask: "Discover my S3 buckets. For `<bucket-name>`, report storage size, object count, versioning and public-access configuration. Cite evidence and mark inaccessible fields Unknown."
4. Ask directly about a known bucket under a role that cannot list buckets; verify direct reads still work where permitted.
5. Ask: "List EC2 instances in `<region>`" and "Describe RDS databases in `<region>`." Confirm the generic reader, connector/account and actual AWS outcomes.
6. Inspect a resource in another region; verify no binding/setup step is introduced.
7. Deny one read and verify a clear permission explanation, other successful fields and no fallback credentials. Ask for an unsupported operation and verify a distinct explanation.
8. Reopen old and new evidence after restart. Check automatic artifact refresh, project isolation and archive/restore.

## Planning Review

Before implementation, review both this plan and its spec. Execute sequentially
in the existing PR worktree after approval. The plan changes the access model;
it does not authorize live cloud calls or maintenance authority. Concrete SDK
metadata limitations must be reported as support gaps rather than silently
reintroducing bindings or a handwritten handler per AWS operation.
