---
type: spec
summary: Proposed managed Connector credentials and scoped AWS S3 storage observations through the delivered gateway, with predefined maintenance-role escalation reserved for Phase 10.
status: proposed
date: 2026-10-21
---

# Phase 5: Managed Connectors and S3 Observations

## 1. Intent, decisions and review boundary

The user wants OpenShip to inspect resources they actually own without depending on an employee's credentials or an EC2/ECS deployment. The first cloud demonstration is the latest observed storage size of an existing S3 bucket. OpenShip must preserve its conversation/files, identify its execution identity and evidence, and explain missing access safely.

**Agreed direction:**

- Use a dedicated organization-owned workload identity. Record the human initiator separately; leaving the team must not invalidate the workload identity by design.
- Prefer AWS IAM roles with temporary sessions. A role ARN identifies a role; a configured credential source and AWS trust policy authenticate its use. A service-account email alone is not an AWS authentication method.
- Configure a basic role for routine reads and an optional predefined maintenance role for supported changes. Normal execution must not require editing IAM on every run. Maintenance use, approvals and temporary grants belong to [Phase 10A/10B](../../roadmap.md#phase-10b--approved-temporary-maintenance-authority), not Phase 5.
- Replace the staging-deployment health example with S3 bucket size. Read-only does not imply public/non-sensitive data or authorization to fetch object contents.
- Extend the existing gateway, operations, runtime, semantic events and filesystem artifacts. The accepted [Phase 4 specification](./2026-10-19-phase4-execution-and-conversation-workspace-design.md) assigns managed Brave/Tavily credentials and browser setup to this phase as well.

**Proposed details for written-spec review:** The first AWS source is a dedicated machine IAM user's access key/secret, optionally temporary session credentials, used only to assume the configured basic role. This is the smallest localhost-compatible bootstrap without requiring AWS compute, an external IdP or certificate infrastructure. It is an alternative to the preferred keyless deployment pattern, not a claim that long-lived keys are preferable. Source keys have only the necessary role-assumption authority and no direct S3/CloudWatch read or mutation authority. Customer approval of this source is required before the implementation plan is finalized. GCP OIDC and IAM Roles Anywhere remain separately qualified follow-ups behind the same broker contract, not silently supported modes.

This document proposes implementation contracts; only the direction above is agreed. It does not authorize product implementation, real credential entry, cloud calls, maintenance-role execution or live destructive tests. The written spec requires user review before preparing the detailed implementation plan.

## 2. Scope and acceptance units

| Unit | Runnable outcome | Boundary |
| --- | --- | --- |
| 5A.1 | Configure a project-owned Brave or Tavily Connector through a write-only secret form; run the same search and reopen its evidence after restart | First encrypted-store/broker consumer; each service remains independent |
| 5A.2 | Configure one AWS basic role/source, bind one existing general-purpose S3 bucket, and run an explicit storage observation, then the same tool from qualified chat | First cloud connector, environment/resource identity and scoped observation |
| 5B | Inspect a bounded inventory of explicitly bound buckets and selected bucket metadata through the same contracts | Proposed expansion after 5A; not account-wide discovery, object listing or log provisioning |

Accept 5A.1 before admitting AWS credentials, then 5A.2 before expanding reads. These are user-visible checkpoints inside 5A, not backend-only milestones. Existing operator-configured searches remain usable until explicitly migrated. If 5A is accepted alone, record 5B as undelivered rather than declaring the whole phase complete.

Initial support: existing owner-only personal projects, one local Compose installation, PostgreSQL 15, the Phase 4 local POSIX artifact backend, commercial AWS partition, one configured account/region per AWS connector revision, general-purpose S3 buckets and the already qualified sequential OpenAI tool profile. Other model adapters retain their accepted text behavior.

Exclude maintenance-role configuration/execution APIs, general permission management, Apono integration, cloud writes, generated code, sandboxes/CLIs, arbitrary URLs or SDK operations, object-content downloads, full object-size scans, directory buckets, automatic bucket discovery, log collection, continuous monitoring, Terraform ownership and cross-host storage. Health/log reads are future individually accepted operations against a concrete source; they are not prerequisites hidden in the S3 example.

## 3. Architecture and alignment

```text
Browser setup → existing authenticated API → project Connector + encrypted credential version
Browser/chat command → existing run admission/queue → supervised runtime
    → gateway admission + target/policy checks
    → credential broker → temporary AWS basic-role session / service API key
    → maintained typed tool → validated bounded observation
    → existing fenced artifact/operation/outbox publication → conversation UI
```

No second scheduler, provider adapter hierarchy, event stream, resource inventory or search-routing service. Connector denotes the project-owned configured access binding; its immutable revision separates configuration from the maintained tool implementation. Do not introduce a redundant Connection entity unless a later reviewed requirement needs it. Model `ProviderProfile` remains unrelated to service/cloud authentication.

| Architecture | Phase 5 implementation responsibility |
| --- | --- |
| §3, §7, §10 | Same runtime/queue/gateway; exact capability IDs; versioned execution config; fences and no blind replay |
| §6, §14 | Environment, resource object/binding and timestamped observation identities reusable by later provisioning |
| §8, §13 | Project-filtered capabilities, connector requirements and current scope checks; connector availability never grants authority |
| §4, §13 | Encrypted credential versions, separately protected keys, rotation/revocation and coordinated recovery |
| §11, §14 | Safe observation excerpts and retained evidence with source/time; external identifiers and logs are not executable instructions |

The current gateway/admission/publication modules assume `SearchArguments`/`SearchObservation` for external tools, and model tool descriptions use a fixed search/workspace map. Replace those assumptions with a small maintained definition table containing the manifest, argument validator, handler and output validator/projector. Keep operation lifecycle, artifact publication and authorization common. This is not an importable plugin framework: only distribution-owned definitions execute in the trusted runtime.

## 4. Domain contracts and identity

Use frozen, extra-forbidden, bounded Pydantic boundary DTOs and existing canonical JSON/digest rules. Keep database models out of model/provider schemas.

| Record | Required meaning |
| --- | --- |
| `Environment` | Project-owned ID, unique project/name, display name; no permissions inferred from labels such as staging |
| `Connector` | Stable project-owned ID, kind `brave|tavily|aws`, name, current revision, enabled/revoked state, creator/time |
| `ConnectorRevision` | Immutable schema version, connector/project, monotonically increasing revision, config digest, non-secret typed configuration, credential-version reference and creation actor/time |
| `CredentialVersion` | Opaque ID, project/connector/source kind, encrypted bounded payload, key ID/nonce, optional expiry, state and creation/revocation timestamps; no public secret projection |
| `ResourceObject` | Stable project-owned `openship://{project_id}/resource/{uuid}` identity and display label; no invented desired configuration |
| `ResourceBinding` | Immutable revision mapping object/environment/connector revision to AWS account, region, bucket ARN/name and expected owner; management method `external|unknown`, with no claim OpenShip provisioned it |
| `ResourceObservation` | Immutable ID, object/binding revision, operation/run, source, retrieved time, measurement time/coverage, outcome and evidence artifact reference/digest |
| `ConnectorAvailability` | Project/connector/revision + worker identity, safe readiness reason and observation time; capability-wide installation availability cannot substitute for project readiness |
| `ConnectorBlocker` | A pre-dispatch blocked run's exact connector revision, reason, request ID, expiry and consumed/cancelled state; not an approval or permission grant |

Composite ownership constraints prevent cross-project environment/connector/resource associations. A resource binding is a locator, not proof the bucket still exists or that the caller owns it. Unique active mapping per project/environment/provider/account/region/bucket prevents duplicate local identities accidentally representing the same target. Later provisioning records extend this identity with provenance; they must not recreate it.

A Connector stores configuration references, not credentials inline. Each AWS revision requires expected account ID, explicit region, configured basic-role ARN and source credential reference. No maintenance role is used or silently assumed in Phase 5. Secret replacement creates another credential/connector revision; changing the current pointer cannot rewrite an admitted run's binding.

## 5. Credential ownership and broker

### 5.1 Write-only managed store

Use a small encrypted instance credential store in PostgreSQL with `cryptography` AES-256-GCM. Encrypt each bounded JSON payload with a fresh 96-bit nonce. Authenticated associated data binds installation ID, project ID, Connector ID, credential-version ID, schema version and credential kind, preventing ciphertext substitution across records. Never persist a plaintext hash, key suffix or caller-provided secret label as proof of identity. Public status exposes only configured/expired/revoked/unavailable.

The operator provisions versioned encryption keys and a separate secret-write idempotency HMAC key outside the database and artifact volumes, using protected read-only key files; never automatically generate replacement keys when a key is missing. Back up these keys separately and record their key IDs. The trusted API setup path needs the encryption key to accept credentials; the trusted runtime broker decrypts only admitted references. This is not cryptographic protection against a compromised API/operator possessing that key. Plain chat and unrelated operator-bound tools continue when managed credential keys are unavailable.

Browser setup submits secrets once over an authenticated, origin-checked write endpoint (TLS remotely). The API immediately encrypts and never echoes credentials. Request validation errors must not include rejected secret input; exclude these bodies from request logging, exception serialization and telemetry. Do not persist browser form secrets in local/session storage, drafts, frontend stores or retry queues; clear input after success/navigation. The browser-originated setup submission is the only intentional browser secret-transfer path, not a credential-read API.

Search API keys and bootstrap AWS credentials may persist only as ciphertext here. AWS issued sessions remain runtime-only, with secret-safe representations, no checkpoint/transcript/event/artifact storage and no durable response-body digests of secret-bearing STS payloads. No raw provider exception escapes a fixed safe error mapping.

### 5.2 AWS bootstrap and renewal proposal

1. An AWS administrator creates OpenShip's dedicated machine identity and basic role, establishing narrowly scoped `sts:AssumeRole` permission/trust. It is not an employee identity or AWS root credential.
2. The owner configures the role ARN/account/region and write-only source key/secret. An optional session token requires an explicit expiration; such a source cannot renew itself.
3. After current run/connector/target authorization, the broker assumes only that configured role, using a server-derived session name that correlates to the run. Verify the returned identity/account before any resource read.
4. Cache sessions only in memory, keyed by project, Connector/revision, credential version, role, region and scope. Request a 15-minute STS session initially. Renew on demand when less than 120 seconds of usable lifetime remains; bounded acquisition shares the operation's deadline and never enables permission fallback.
5. Recheck the credential/version and connector revocation state before every dispatch, including a dispatch using a cached session. Expired source sessions require explicit replacement; permanent source keys require administrator rotation. Renewal is another assumption of the same role, not an OAuth refresh token or permission escalation.

The source identity must not be able to assume arbitrary roles, and the basic role must not include IAM mutation, role chaining to maintenance, object reads, writes or bucket deletion. Document the permanent source key's standing assumption authority and trade-off. Keyless GCP OIDC or Roles Anywhere can later replace the bootstrap through an explicit source adapter and compatibility tests, not an ambient SDK credential chain.

### 5.3 Rotation, revocation and restore

- Credential replacement creates a new version and explicitly activates a new Connector revision. New runs pin it; queued/active runs retain their original references or block/fail visibly if revoked. Never substitute a new secret under an old admitted reference.
- Revoking/disabling a Connector or credential blocks new dispatches and invalidates matching local caches. A request admitted immediately before revocation may already be in flight; refuse late publication under the revised policy. Do not promise universal instant AWS token invalidation.
- Encryption-key rotation re-encrypts ciphertext with a fresh nonce and key ID without changing logical credential version or authority. Provide resumable progress and verify all active/retained ciphertext before retiring an old key. Key loss is not recoverable from database backups alone.
- Back up PostgreSQL, artifacts/storage identity, configuration and encryption keys as separate protected components. A restored installation starts with managed connectors dispatch-disabled until an owner/operator verifies keys, binding revisions, externally revoked credentials and ready artifact bytes. Restored dispatched work never automatically repeats. A backup can restore stale revocation state, so current external access must be verified before re-enablement.

## 6. Execution, compatibility and recovery

### 6.1 Versioned admission

Keep manifest schema/execution config v1 and historical chat-v1/chat-v2/tool-v1 handling readable. Introduce manifest schema v2 with connector-kind requirements instead of per-installation environment names, and `ExecutionConfig` v2 with immutable permitted Connector/binding revisions, selected target refs and source/credential references only. New connector-aware manual/chat runs use tool graph v2/chat graph v3 respectively. Exact capability IDs remain unchanged for Brave/Tavily; a new definition/version/digest is never silently substituted into an old run.

Resolve definitions and Connector selections server-side from the authorized project. For a given search service, require one explicitly selected/default enabled Connector; ambiguity requires selection, never hidden provider fallback. The browser/model may name an owned opaque target ID, but never a credential source, role ARN, endpoint or SDK method. Include resolved target/connector revisions and effective limits in the operation bundle digest and audit projection.

An explicit migration command previews service/project bindings, then imports operator search keys into encrypted Connector versions only at operator request. Do not scan arbitrary environment variables or persist source values in migration output. Retain the narrow v1 dispatch compatibility path for already-admitted runs until they drain or visibly expire; before removing it, verify no pending v1 references remain. If an originally pinned implementation/digest is no longer available, fail visibly as changed; never manufacture an old manifest digest for replacement code. Old transcript/history/file reads stay supported afterward. Model-provider credential configuration is not migrated in this phase.

### 6.2 Connector blockers, not generic approval waits

Add only `waiting_connector` to the existing run status vocabulary; preserve `completed` and all historical terminal meanings. A local connector preflight may park a new run only **before any model or tool external dispatch intent**. It checks configured references, key/decryption readiness and known expiry without an external authentication probe. Persist the exact blocker/event/outbox and release its job/slot/lease; connector setup is never performed inside an API request's cloud call. STS acquisition and live connection tests are recorded tool dispatches, not unjournaled preflight calls. This is not the Phase 8 input/approval engine.

`POST /api/runs/{id}/resume-connector` requires owner, blocker ID, request ID and expected configuration digest. Atomically consume an eligible unexpired blocker, recheck the same pinned refs/readiness and queue the same run under a new fence. Repeated identical commands return the recorded result; conflicting/stale/foreign commands fail safely. A changed role/target/credential revision requires an explicit new run, not mutation of the old pinned configuration. Supply the user's original task text for an explicit resubmission without automatically dispatching it.

Waiting blocks execution of that task, not plain conversation. A plain chat turn may proceed while a connector-blocked run exists; queued/running work still has one writer per conversation. Resume checks the conversation lock and refuses while another run is active. Cancellation, user dismissal or blocker expiry closes the wait; timeout/cancellation releases artifact pins. Blocker lifetime is at most 24 hours, shortened to the original execution deadline if one has started. Resume never extends that deadline; if it has expired, require a new run. Pin admitted context message IDs/cutoff so a resumed older turn does not reinterpret later conversation messages as its original context. Preserve finalized messages/titles, position replies by their original run association and never let an older run overwrite a newer active-run projection.

**Conservative mid-run policy:** If access expires/disappears after any model/tool dispatch intent, fail the affected run with a safe connector error, retain prior messages/observations/artifacts and offer explicit retry as a new run. Do not restart the single-node Phase 4 model loop or repeat a saved tool call to simulate resume. Durable in-progress workflow connector waits require later step-level recovery contracts. This limitation must be visible in setup/help and acceptance, not advertised as arbitrary mid-task resume.

### 6.3 Dispatch and publication

Reuse operation IDs, one logical invocation/one physical attempt, ownership fences and shared capacity. One maintained operation may issue a declared bounded set of typed AWS reads; it is not restricted to one HTTP packet. Persist intent before the first service dispatch and stop all further requests after cancellation/lease loss. No hidden AWS SDK/transport retries, page following beyond the tool limit or model-request replay. Broker acquisition and each read share the same deadline, request count and byte budget.

Before intent and before publication recheck current ownership, project ownership, Connector enablement/revocation, pinned refs, target scope and artifact quotas. Do not hold database locks over network or filesystem I/O. Publish observation row, ready artifact metadata, safe operation result and semantic events/outbox together after durable bytes exist. Missing/corrupt bytes become unavailable; rereading evidence must never cause another cloud request.

## 7. First AWS operation: `aws_s3_bucket_storage`

Arguments: one `resource_binding_id` UUID; no arbitrary bucket/account/region, time expression, storage filter, role, URL or query text. Derive these from the pinned owned binding. Initially accept only configured general-purpose buckets in the connector's account/region. Verify account identity and use S3 `GetBucketLocation` with expected bucket owner to verify the configured binding; normalize the documented legacy/default region values. A mismatch blocks rather than following a model-selected destination.

Use fixed AWS regional STS, CloudWatch and S3 endpoints derived from the supported region set. Reuse the existing async bounded HTTP transport's destination enforcement, with explicit AWS protocol serialization/signing/parsing through maintained `botocore` utilities and `cryptography` for the credential store. Credential acquisition never uses an ambient SDK chain, credential process, shell, metadata lookup or caller endpoint. Enforce TLS, public destination pinning, no redirects/proxy inheritance and no provider debug logs. Treat STS payloads as secrets before logging/parsing errors. A later metadata/OIDC source needs its own explicit destination policy; it cannot be enabled by making all metadata endpoints reachable.

For the size read, use CloudWatch `GetMetricData` with server-constructed `AWS/S3` / `BucketSizeBytes` queries, `BucketName` and a versioned maintained list of supported `StorageType` dimensions, statistic `Average`, 86,400-second period and the preceding seven days. At most 64 queries, 1,024 returned datapoints, 8 HTTP requests total (including credential/identity/binding checks and any bounded continuation), 30 seconds wall time, 2 MiB decompressed bytes per response and 64 KiB normalized evidence. Exceeding request/byte/time limits produces a bounded failure or explicitly partial observation—never an unbounded scan. Track coverage of the versioned storage-type set; unknown future types are not silently treated as included.

**IAM scope limitation:** CloudWatch metric reads do not provide S3 bucket-ARN-level IAM restriction. The example grants only necessary metric-read actions, generally with `Resource: "*"`, plus applicable account/region controls, and bucket-scoped `s3:GetBucketLocation`. The gateway enforces selected bucket scope. Document that a compromised holder of the role session can read other metrics allowed by AWS; do not describe the session itself as bucket-isolated. A deployment requiring provider-enforced per-bucket metric isolation must choose another qualified collection design before accepting this profile.

Return a typed observation containing source, bucket/object/binding/Connector refs, account/region, operation ID, retrieved time, per-storage-type datapoint time/value, covered/queried types, truncation and coverage state. Choose a common latest measurement timestamp where possible; never sum datapoints from different days into a purported coherent total. Missing datapoints mean unknown, not zero. Without independently established full coverage, label the sum **reported storage subtotal** rather than total bucket size. An empty successful response is `no_data`; it is not "empty bucket". Mark a datapoint older than 48 hours as stale while preserving its actual time.

Explain that S3 daily storage metrics may include noncurrent versions, multipart-upload parts and metadata according to storage type; this is not just the sum of current object bodies, a real-time measurement or a billing estimate. The compact model result is at most 16 KiB and links exact immutable evidence `s3-bucket-storage.json`. The artifact adds kind `cloud_observation` through migration; its ownership, quota, pinning, purge/tombstone and restore behavior are unchanged. Do not copy raw AWS responses or session information into evidence.

## 8. Proposed 5B reads

- `aws_s3_list_bound_buckets`: paginated local inventory of owned resource bindings, default 20/max 50; not AWS account-wide discovery or a claim of live existence. The response explicitly labels its source as stored bindings.
- `aws_s3_bucket_metadata`: one selected bound bucket's location and versioning configuration using a bounded maintained API set, adding bucket-scoped `s3:GetBucketVersioning` only after this unit is approved. This cannot enable versioning or read objects. Observations use the same resource/operation/artifact publication path.

5B gets separate manifest digests, permission documentation and a live read qualification. No new credential backend, resource registry or model abstraction is introduced. Additional metadata/log operations require their own accepted scope rather than arbitrary AWS calls.

## 9. API and browser

All routes use current JWT/project-owner checks; foreign IDs return 404. Use explicit project routes even while one personal project is the implementation limit. Caller-supplied revision preconditions prevent lost configuration updates; request IDs make creation/replacement/activation safe under uncertain submission. Exact secret-write retries use a request ledger, keyed server-side HMAC of the secret-bearing canonical request (with a separately protected idempotency key), and never persist plaintext or an unkeyed secret digest. Keep that key ID valid for the ledger's declared 24-hour retry window; reject an expired request ID rather than creating another credential version. Do not automatically retry a cleared secret form; allow checking the submitted request's owned status before re-entry.

| Route family | Contract |
| --- | --- |
| `/api/projects/{id}/connectors` | Owned list/create and versioned non-secret configuration; typed service forms, no arbitrary backend/plugin selector |
| `/api/projects/{id}/connectors/{connector_id}` | Owned metadata/current revision and enable/disable/revoke commands with expected revision |
| `/api/projects/{id}/connectors/{connector_id}/credentials` | Write-only replacement; owned request-status lookup; never a credential read endpoint |
| `/api/projects/{id}/connectors/{connector_id}/checks` | Admit a model-free, conversation-associated typed identity/binding check through the existing run queue/gateway; never perform AWS calls in the route |
| `/api/projects/{id}/environments` and `/resources` | Bounded owned environment/resource/binding setup and list; non-secret references are still sensitive |
| `/api/projects/{id}/resources/{resource_id}/observations` | Bounded observation metadata/evidence refs; no cross-conversation artifact access expansion |
| Existing tool/chat commands | Add selected target refs and project-owned Connector selection; preserve exact submitted payload/retry identity |
| `/api/runs/{id}/resume-connector` | Validated atomic pre-dispatch resume command described above |
| Existing capabilities/snapshot/SSE | Project-aware permitted definitions/readiness, blockers and observations; same durable event protocol |

For AWS, the check route invokes maintained capability `aws_check_connection` through the same model-free tool command contract: an owned Connector reference and optional owned resource binding, 30-second/8-request bounds, configured-role acquisition, `GetCallerIdentity`, and optional expected-owner/region validation. Its result is safe identity/readiness metadata, never credentials, and its manifest is not exposed to automatic model selection. For search Connectors, setup checks only local credential-store readiness; it does not issue a hidden billable search or advertise upstream authentication as verified. A normal explicit search supplies that evidence.

The Connector setup page offers independent Brave/Tavily forms and AWS **Assume configured IAM role**, not a service-account email field. Show the bootstrap source, execution role/account, current readiness, required trust/permission setup, expiry/rotation guidance and revoke/replace controls. Explicitly label the proposed machine-key source and keyless alternatives that are not yet qualified. Do not display maintenance controls before their phase is enabled.

The user selects a configured target in the existing composer and can attach the explicit storage tool or enable qualified automatic tool use. Include only authorized selected resource metadata in model context; ambiguous names require selection, not cross-project search. Cloud observations use the compact operation entry and existing files panel; show measured-at versus retrieved-at time, stale/missing/partial state, source and evidence separately from the assistant's interpretation. Keep safe Markdown, keyboard controls, mobile layout, exact retries and stale-response guards. Setup never submits secrets as chat messages.

Add fixed versioned events `connector.blocked`, `connector.resumed` and `resource.observed` through the current outbox/SSE machinery; no secret values, role-source details or raw AWS bodies. Project-level configuration audit records have their own project-owned append-only rows, not fabricated conversation messages. Record actor, Connector/binding revision, policy decision, declared execution identity, operation/result/evidence IDs and time. Existing observation artifacts remain conversation-owned: a project observation list cannot grant access to another conversation's evidence bytes.

## 10. Migrations, operational controls and defaults

Add sequential Alembic revisions after `phase4c008`: `phase5a009` for Connector/credential/audit records; `phase5a010` for environments/resources/observations, new artifact kind and connector wait state; `phase5b011` only when 5B requires an additional schema change. Do not add an empty migration for metadata-tool breadth. Preserve existing rows/provider configs/transcripts and explicitly reject destructive downgrade when protected new data exists. No live API call, filesystem access or secret import occurs during schema migration.

Upgrade drains old workers before enabling new execution/manifest versions. Unknown versions fail closed and remain readable. The runtime advertises versioned managed-Connector support and fresh per-project readiness; API cannot dispatch a new graph to an incompatible worker. Qualify the upgrade/downgrade boundary with old queued runs and ready files. No team role tables, sandbox process or shared storage is introduced here.

Initial limits: 10 Connectors per project, 10 environments, 100 resource bindings, non-secret config 16 KiB, decrypted credential payload 16 KiB, list pages default 20/max 50, identity/observation operation deadline 30 seconds, existing tool concurrency/artifact quotas and existing per-run/model budgets. Do not globally increase Phase 4 tool-call budgets for these reads. Configuration command bodies and attempts are size/rate limited; blocked waits expire and cannot create unlimited retained run/artifact pins.

Safe errors distinguish `connector_required`, `connector_ambiguous`, `connector_expired`, `connector_revoked`, `credential_store_unavailable`, `connector_scope_denied`, `connector_revision_changed`, `aws_trust_denied`, `aws_account_mismatch`, `resource_owner_mismatch`, `resource_region_mismatch`, `metric_unavailable`, rate/timeout/output limits and interruption. Never include upstream credential/source/request contents. Unknown errors fail closed with a fixed message, not a raw exception.

## 11. Requirements and acceptance evidence

| ID | Acceptance requirement | Unit |
| --- | --- | --- |
| R01 | Current auth/project isolation, plain chat, provider pinning/titles, cancellation/reconnect and exact file recovery remain valid | All |
| R02 | Browser-managed independent Brave/Tavily Connectors work through the same gateway; explicit migration preserves v1 pins | 5A.1 |
| R03 | Ciphertext/AAD/key rotation, missing keys, secret-safe submission/errors/audit and coordinated restore are verified | 5A.1 |
| R04 | Dedicated AWS identity assumes only the configured basic role; source renewal/expiry and trust failures are visible | 5A.2 |
| R05 | Target references cannot cross project, environment, Connector/account/region or owned conversation evidence scope | 5A.2 |
| R06 | Storage observations match an independent console/API read for the same dimensions/time, not an assumed live size | 5A.2 |
| R07 | Missing, partial, mixed-date, stale, truncated and zero metrics are distinct and retain exact evidence/provenance | 5A.2 |
| R08 | Cancellation/revocation/lease loss stop new requests, fence publication and never replay dispatched work after restart/restore | All |
| R09 | Pre-dispatch connector waits/resume/cancel/expiry are atomic; chat remains usable and old runs cannot overwrite newer state | 5A.2 |
| R10 | Qualified tool chat and explicit invocation use identical admission, credential and evidence contracts | 5A.2 |
| R11 | Read-only authority excludes representative mutations, object contents and maintenance role assumption; CloudWatch's broader read scope is disclosed | 5A.2 |
| R12 | Bound inventory/metadata expansion respects the same budgets, identities, source labels and permission review | 5B |

Required lanes:

- **Unit/contracts:** strict schemas/refs/digests, owned role selection, cryptographic substitution/tampering, expiry and rotation, credential-cache separation, bounded AWS serialization/mapping, fixed endpoint/DNS/proxy/redirect rejection, secret sentinels and hostile cloud strings. Assert no credentials in HTTP error validation bodies, logs, browser read responses, model requests, checkpoints, transcripts, artifacts or events.
- **Real PostgreSQL 15:** migrations/history/protected downgrade; concurrent configuration versions/idempotency; cross-project composite ownership; blocker consumption/resume versus cancel/active-run races; observation/artifact/outbox publication; restore of stale revocations and mixed worker versions. Required cases run with zero required skips.
- **Real filesystem and synthetic stack:** actual API/worker/PG/artifact bytes with deterministic STS/S3/CloudWatch/model transports; distinct process restarts before intent, after acquisition/read and before publication; missing keys/volume; retained-evidence continuation; no automatic cloud/model redispatch. Synthetic credentials never reach persisted/public surfaces.
- **Browser fixture and real-stack:** setup/key replacement/revocation without secret persistence, target selection, direct/tool-chat observation, safe evidence/time/coverage rendering, responsive keyboard-accessible configuration blockers and conversation continuity. Fixture tests do not establish AWS trust or permissions.
- **Live opt-in:** explicitly chosen account/role/bucket and declared source, independent identity and metric comparisons, real renewal and safe access-denial evidence. No CI cloud credentials and no unapproved writes. Never probe `DeleteBucket` against the user's real bucket; use administrator-reviewed effective-policy evidence and declare simulator limits, or an independently authorized disposable denied-mutation fixture. Record exact source/profile/tool revisions, time and limits; mocks do not qualify IAM authority.

Deliver separate 5A.1/5A.2/5B demonstrations and user accept/revise/stop decisions. An AWS-specific qualification matrix belongs with the Connector runbook; model-provider qualification still belongs in `docs/provider-support.md`. Documentation, schemas and green tests alone do not accept a milestone.

## 12. Implementation boundaries for the subsequent plan

| Location | Responsibility |
| --- | --- |
| `apps/api/src/openship/connectors/` (new) | Owned revision/secret models, typed configuration, write-only setup service/routes, availability and audit; no model adapter inheritance |
| `connectors/credentials/` (new) | AEAD storage/key rotation, runtime broker, scoped in-memory leases; no graph-serializable credentials |
| `connectors/aws/` (new) | Explicit source-to-role acquisition and maintained bounded signed service transport; no generic cloud RPC |
| `resources/` (new) | Project/environment object/binding/observation records and owned projections |
| `tools/aws/` (new) | Typed S3 storage and later metadata/inventory tools, DTOs and output validation |
| Existing `capabilities/` and `execution/` | Maintained definition table, v1/v2 resolution, project/target admission, common fencing/publication and safe result projections |
| Existing `runs/` | Versioned graph dispatch, secret-free config pinning and preflight wait/resume; preserve budgets and no partial-loop replay |
| Existing `artifacts/`, migrations and Compose | New observation kind through existing storage, key mounts/configuration, coordinated recovery and version qualification |
| Existing browser routes/services/stores/components | Owned Connector setup and target selection, safe compact observations/blockers; preserve current chat/files/model controls |

The implementation plan follows written-spec approval and must give failing tests, exact files/contracts, focused changes and verification for each runnable acceptance unit. No product implementation is authorized by preparing this document.

## 13. Sources and declared limits

- [AWS IAM roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles.html): roles, trust and temporary sessions.
- [STS AssumeRole](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html): session duration and session-policy intersection; no privilege expansion by session policy.
- [GCP workload identity to AWS](https://aws.amazon.com/blogs/security/access-aws-using-a-google-cloud-platform-native-workload-identity/) and [IAM Roles Anywhere](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html): alternatives requiring explicit source/trust setup, not qualified here.
- [S3 metrics and dimensions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html): daily storage measurements, storage types and semantics.
- [CloudWatch permission reference](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/permissions-reference-cw.html) and [service authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazoncloudwatch.html): action/resource restriction limits.

Source documentation establishes provider mechanics, not OpenShip implementation or live qualification. The first credential source, CloudWatch read-scope trade-off and limited pre-dispatch resume behavior are explicit proposals for user review.
