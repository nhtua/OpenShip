---
type: spec
summary: Proposed global/project managed Connectors, explicit worker credential consumption and extensible native-SDK S3 observations; maintenance authority remains in Phase 10.
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
- Support `global` and `project` Connector visibility in Phase 5: search services can be configured once for reuse across projects; AWS access stays project-scoped.
- Keep credential management inside the existing trusted API/runtime. Independently portable Connector packaging or a separate lifecycle/service is deferred without assigning it a roadmap phase.
- Developers add ordinary typed tool handlers using native provider SDKs and runtime-supplied authentication. Extend the existing tool registration contract; do not recreate every provider operation or expose a generic cloud RPC tool.

**Proposed details for written-spec review:** The first AWS source is a dedicated machine IAM user's access key/secret, optionally temporary session credentials, used only to assume the configured basic role. This is the smallest localhost-compatible bootstrap without requiring AWS compute, an external IdP or certificate infrastructure. It is an alternative to the preferred keyless deployment pattern, not a claim that long-lived keys are preferable. Source keys have only the necessary role-assumption authority and no direct S3/CloudWatch read or mutation authority. Customer approval of this source is required before the implementation plan is finalized. GCP OIDC and IAM Roles Anywhere remain separately qualified follow-ups behind the same broker contract, not silently supported modes.

An IAM user is not required by the architecture. The authentication source proves **who the workload is**; the basic role defines **what its session may do**. On AWS, an attached workload role can supply temporary credentials without a machine user. Outside AWS, configured federation or IAM Roles Anywhere can establish trust without long-lived AWS keys. A role ARN alone proves neither identity nor permission to assume it. Phase 5's proposed key source is one explicitly qualified deployment option; attached-role/federated/certificate sources need their own qualification, not an unconfigured SDK fallback. Section 5.2 describes the proposed bootstrap and renewal sequence.

This document proposes implementation contracts; only the direction above is agreed. It does not authorize product implementation, real credential entry, cloud calls, maintenance-role execution or live destructive tests. The written spec requires user review before preparing the detailed implementation plan.

## 2. Scope and acceptance units

| Unit | Runnable outcome | Boundary |
| --- | --- | --- |
| 5A.1 | Configure global or project Brave/Tavily Connectors through write-only setup; select an eligible Connector, run the same search and reopen evidence after restart | First encrypted-store/broker consumer; demonstrate cross-project global use and project isolation; each service remains independent |
| 5A.2 | Configure one AWS basic role/source, bind one existing general-purpose S3 bucket, and run an explicit storage observation, then the same tool from qualified chat | First cloud connector, environment/resource identity and scoped observation |
| 5B | Inspect a bounded inventory of explicitly bound buckets and selected bucket metadata through the same contracts | Proposed expansion after 5A; not account-wide discovery, object listing or log provisioning |

Accept 5A.1 before admitting AWS credentials, then 5A.2 before expanding reads. These are user-visible checkpoints inside 5A, not backend-only milestones. Existing operator-configured searches remain usable until explicitly migrated. If 5A is accepted alone, record 5B as undelivered rather than declaring the whole phase complete.

Initial support: existing owner-only personal projects, one local Compose installation, PostgreSQL 15, the Phase 4 local POSIX artifact backend, commercial AWS partition, one configured account/region per AWS connector revision, general-purpose S3 buckets and the already qualified sequential OpenAI tool profile. Other model adapters retain their accepted text behavior.

Exclude maintenance-role configuration/execution APIs, general permission management, Apono integration, cloud writes, generated code, sandboxes/CLIs, arbitrary URLs or model-selected SDK operations, object-content downloads, full object-size scans, directory buckets, automatic bucket discovery, log collection, continuous monitoring, Terraform ownership and cross-host storage. Independently portable Connector components and a general plugin framework are not Phase 5 prerequisites. Health/log reads are future individually accepted operations against a concrete source; they are not prerequisites hidden in the S3 example.

## 3. Architecture and alignment

```text
Browser setup → existing authenticated API → global/project Connector + encrypted credential version
Browser/chat command → existing run admission/queue (references only) → supervised runtime
    → gateway admission + visibility/target/policy checks
    → internal credential module → temporary AWS basic-role session / service API key
    → maintained typed tool → native provider SDK/service API → validated bounded observation
    → existing fenced artifact/operation/outbox publication → conversation UI
```

No second scheduler, provider adapter hierarchy, event stream, resource inventory or search-routing service. Connector denotes a configured access binding with explicit visibility; its immutable revision separates configuration from the maintained tool implementation. Credential acquisition is an internal module in the existing worker, not a provider-API proxy or independently deployed Connector service. Do not introduce a redundant Connection entity unless a later reviewed requirement needs it. Model `ProviderProfile` remains unrelated to service/cloud authentication.

| Architecture | Phase 5 implementation responsibility |
| --- | --- |
| §3, §7, §10 | Same runtime/queue/gateway; exact capability IDs; versioned execution config; fences and no blind replay |
| §6, §14 | Environment, resource object/binding and timestamped observation identities reusable by later provisioning |
| §8, §13 | Project-filtered capabilities, connector requirements and current scope checks; connector availability never grants authority |
| §4, §13 | Encrypted credential versions, separately protected keys, rotation/revocation and coordinated recovery |
| §11, §14 | Safe observation excerpts and retained evidence with source/time; external identifiers and logs are not executable instructions |

The current gateway/admission/publication modules assume `SearchArguments`/`SearchObservation` for external tools, and model tool descriptions use a fixed search/workspace map. Replace those assumptions with a small maintained definition table containing the manifest, argument validator, handler and output validator/projector. Keep operation lifecycle, artifact publication and authorization common. This is not an importable plugin framework: only distribution-owned definitions execute in the trusted runtime.

### 3.1 Developer extension contract

A developer adds an input/output schema, an ordinary handler, and a registered manifest/definition declaring its Connector kind, supported operations/targets and limits. The gateway resolves the selected Connector and provides an invocation-scoped credential context. An AWS handler uses runtime-created boto3 clients and normal SDK operations; it never loads keys from the database, decrypts a source, assumes its own role or searches the host credential chain. Service API-key tools receive only their selected key through the corresponding restricted resolver. Registration also supplies result validation/projection, so provider payloads do not become model context automatically.

Illustrative handler body (interface names are finalized in the implementation plan):

```python
s3 = context.aws.client("s3")  # Already bound to the authorized role and region.
response = await context.aws.run(
    s3.get_bucket_location,
    Bucket=binding.bucket_name,
    ExpectedBucketOwner=binding.account_id,
)
return project_bucket_location(response, binding)
```

Here `binding` is the gateway-resolved owned target, and `run` applies the common execution controls. The developer writes SDK calls and a safe result projection, not secret retrieval or role renewal.

The common AWS runner owns SDK client configuration, bounded transport integration, blocking-call scheduling, deadline/cancellation checks, request/byte accounting and safe errors. The SDK owns service definitions, serialization, signing and protocol parsing. The handler owns task semantics, target-derived parameters and interpretation of results. Adding another supported AWS read reuses the same authentication/runner path; it does not require a new credential backend, signing implementation or gateway branch for each API. SDK upgrades do not automatically register new capabilities or broaden a manifest's admitted operations.

Only the initial checks/storage reads and accepted 5B reads are qualified here. Future credentialed CLI or sandboxed boto3 execution can consume the same credential-acquisition contract behind their own execution boundary; no such executor, credential handoff or sandbox support is delivered by this phase.

## 4. Domain contracts and identity

Use frozen, extra-forbidden, bounded Pydantic boundary DTOs and existing canonical JSON/digest rules. Keep database models out of model/provider schemas.

| Record | Required meaning |
| --- | --- |
| `Environment` | Project-owned ID, unique project/name, display name; no permissions inferred from labels such as staging |
| `Connector` | Stable ID, kind `brave`, `tavily` or `aws`, visibility `global` or `project`, project ID required only for project scope, management actor, name, current revision, enabled/revoked state, creator/time |
| `ConnectorRevision` | Immutable schema version, connector/visibility/owning scope, monotonically increasing revision, config digest, non-secret typed configuration, credential-version reference and creation actor/time |
| `CredentialVersion` | Opaque ID, owning scope/connector/source kind, encrypted bounded payload, key ID/nonce, optional expiry, state and creation/revocation timestamps; no public secret projection |
| `ProjectConnectorDefault` | Unique consuming project/service, eligible Connector ID and configuration revision for concurrent updates; admission resolves/pins the Connector's current revision |
| `ResourceObject` | Stable project-owned `openship://{project_id}/resource/{uuid}` identity and display label; no invented desired configuration |
| `ResourceBinding` | Immutable revision mapping object/environment/connector revision to AWS account, region, bucket ARN/name and expected owner; management method `external` or `unknown`, with no claim OpenShip provisioned it |
| `ResourceObservation` | Immutable ID, object/binding revision, operation/run, source, retrieved time, measurement time/coverage, outcome and evidence artifact reference/digest |
| `ConnectorAvailability` | Project/connector/revision + worker identity, safe readiness reason and observation time; capability-wide installation availability cannot substitute for project readiness |
| `ConnectorBlocker` | A pre-dispatch blocked run's exact connector revision, reason, request ID, expiry and consumed/cancelled state; not an approval or permission grant |

Composite ownership constraints prevent cross-project environment/AWS-connector/resource associations. Global search eligibility is an explicit rule, not a nullable-project shortcut around project checks. A resource binding is a locator, not proof the bucket still exists or that the caller owns it. Unique active mapping per project/environment/provider/account/region/bucket prevents duplicate local identities accidentally representing the same target. Later provisioning records extend this identity with provenance; they must not recreate it.

A Connector stores configuration references, not credentials inline. Each AWS revision requires expected account ID, explicit region, configured basic-role ARN and source credential reference. No maintenance role is used or silently assumed in Phase 5. Secret replacement creates another credential/connector revision; changing the current pointer cannot rewrite an admitted run's binding.

### 4.1 Visibility, management and selection

`global` means installation-wide availability to authenticated projects, not public access or permission to read credentials. Brave/Tavily support both scopes; AWS supports only `project` in Phase 5. Global credentials are stored once, while each consuming run, policy decision, output and evidence remains associated with its own project/conversation. Global use never grants access to another project's configuration, runs or evidence.

Proposed management policy for the current owner-only deployment: project owners manage their project Connectors; a deployment-configured existing operator user ID manages global search Connectors through authenticated setup. Only that operator can create, replace, disable or revoke globals. Other project owners see only eligible non-secret usage metadata and can select them. Missing operator configuration disables global management, not existing authorized use. This is a narrow installation setting, not a new administrator/team-role system. Record global management audit entries at installation scope; project consumption retains project audit entries.

Connector visibility and owning project are immutable. Moving a project secret to global scope requires explicit creation of a new global Connector; never silently widen an existing secret's audience. Rotation/revocation of a global affects all consuming projects, and setup must disclose that impact.

Selection is explicit or uses a per-project, per-service default pointing to an eligible Connector ID; admission pins its current revision. With no selection/default, exactly one eligible enabled Connector may be resolved; multiple global/project candidates require selection. Do not silently prefer a global, shadow it by name or fall back to another provider/credential after failure. Record the resolved scope and revision before execution. Project policy can disallow an otherwise visible global; availability alone never grants operation authority.

Default changes use a monotonically increasing settings revision. Clearing a default retains a tombstone with no selected Connector, so a stale write cannot reset the revision and overwrite a newer clear.

## 5. Credential ownership and broker

### 5.1 Write-only managed store

Use a small encrypted instance credential store in PostgreSQL with `cryptography` AES-256-GCM. Encrypt each bounded JSON payload with a fresh 96-bit nonce. Authenticated associated data binds installation ID, visibility, owning scope (project ID or an explicit installation-global marker), Connector ID, credential-version ID, schema version and credential kind, preventing ciphertext substitution across records or visibility scopes. Do not re-encrypt/copy global credentials per consuming project. Never persist a plaintext hash, key suffix or caller-provided secret label as proof of identity. Public status exposes only configured/expired/revoked/unavailable.

The operator provisions versioned encryption keys and a separate secret-write idempotency HMAC key outside the database and artifact volumes, using protected read-only key files; never automatically generate replacement keys when a key is missing. Back up these keys separately and record their key IDs. The trusted API setup path needs the encryption key to accept credentials; the trusted runtime broker decrypts only admitted references. This is not cryptographic protection against a compromised API/operator possessing that key. Plain chat and unrelated operator-bound tools continue when managed credential keys are unavailable.

Browser setup submits secrets once over an authenticated, origin-checked write endpoint (TLS remotely). The API immediately encrypts and never echoes credentials. Request validation errors must not include rejected secret input; exclude these bodies from request logging, exception serialization and telemetry. Do not persist browser form secrets in local/session storage, drafts, frontend stores or retry queues; clear input after success/navigation. The browser-originated setup submission is the only intentional browser secret-transfer path, not a credential-read API.

Search API keys and bootstrap AWS credentials may persist only as ciphertext here. AWS issued sessions remain runtime-only, with secret-safe representations, no checkpoint/transcript/event/artifact storage and no durable response-body digests of secret-bearing STS payloads. No raw provider exception escapes a fixed safe error mapping.

### 5.2 AWS bootstrap and renewal proposal

There are two identities in this proposal: the bootstrap source authenticates to STS and may assume only the configured role; the returned basic-role session performs the permitted cloud reads. The bootstrap key is never handed to an observation tool. An IAM role cannot authenticate a localhost process solely from its ARN. AWS-hosted role credentials or explicitly configured federation can replace the bootstrap source after separate qualification; this sequence does not require an IAM user for every deployment.

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

### 5.4 Worker access and secret consumption

The existing Phase 4 gateway resolves an operator environment binding and passes a tool a resolver restricted to that one binding. Phase 5 changes the backing source to the managed store and adds scope/revision authorization; it does not require a separate Connector service.

1. Authenticated setup encrypts the submitted secret in PostgreSQL. The API setup process can encrypt, and the worker credential module can decrypt, using separately mounted protected keys. The tool context exposes neither key files nor a credential-store interface.
2. Admission records the consuming project, selected eligible Connector/revision and credential-version reference in secret-free execution configuration. Queue rows, graph state and checkpoints contain references only.
3. The worker loads the pinned metadata through its existing database access. The gateway checks project ownership, global/project eligibility, enabled/revoked state, capability requirements and target policy before acquiring credentials.
4. The internal credential module decrypts only the selected version. For search it returns that service's API key to the restricted tool context. For AWS it retains the bootstrap source internally, acquires the configured role session under section 5.2, and constructs invocation-scoped SDK clients using those explicit temporary credentials and configured region.
5. The trusted tool calls the provider directly through the common bounded SDK runner or existing search transport. The credential module acquires/renews access; it does not translate or proxy S3/CloudWatch/search operations. No tool-specific authentication logic or installation-wide credential resolver is needed.
6. The runtime validates and publishes only the safe result/evidence. Plaintext and AWS sessions stay in worker memory, with bounded cache lifetimes and revocation checks; they never enter public responses or durable run state.

This is a trusted-process boundary. The limited interface prevents accidental access to unrelated credentials but does not sandbox malicious Python imported into the worker; worker/database/key compromise remains within the trusted computing base. Phase 5 executes distribution-owned tools only. Independently portable Connector packaging/lifecycles and hostile-tool isolation are deferred; future sandbox execution must establish its own credential handoff and provider-authority limits.

## 6. Execution, compatibility and recovery

### 6.1 Versioned admission

Keep manifest schema/execution config v1 and historical chat-v1/chat-v2/tool-v1 handling readable. Introduce manifest schema v2 with connector-kind requirements instead of per-installation environment names, and `ExecutionConfig` v2 with immutable permitted Connector/binding revisions, selected target refs and source/credential references only. New connector-aware manual/chat runs use tool graph v2/chat graph v3 respectively. Exact capability IDs remain unchanged for Brave/Tavily; a new definition/version/digest is never silently substituted into an old run.

Resolve definitions and eligible global/project Connector selections server-side in the authorized consuming project using section 4.1. A pinned global reference does not bypass current project policy or global revocation. For a given search service, require one resolved enabled Connector; ambiguity requires selection, never hidden provider fallback. The browser/model may name an owned opaque target ID, but never a credential source, role ARN, endpoint or SDK method. Include consuming project, resolved visibility/target/connector revisions and effective limits in the operation bundle digest and audit projection.

An explicit migration command previews service, global/project scope and project-default bindings, then imports operator search keys into encrypted Connector versions only at operator request. Importing a global requires the configured operator's authority; never silently publish a project's key globally. Do not scan arbitrary environment variables or persist source values in migration output. Retain the narrow v1 dispatch compatibility path for already-admitted runs until they drain or visibly expire; before removing it, verify no pending v1 references remain. If an originally pinned implementation/digest is no longer available, fail visibly as changed; never manufacture an old manifest digest for replacement code. Old transcript/history/file reads stay supported afterward. Model-provider credential configuration is not migrated in this phase.

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

Use native boto3/botocore STS, CloudWatch and S3 clients; let the SDK maintain service models, request serialization, signing and response parsing. The internal credential module creates the STS client from the explicit configured source and supplies temporary role credentials to runtime-created read clients. Tool handlers call ordinary SDK methods through the common bounded runner, without implementing an AWS-compatible protocol layer or placing service operations in the credential module.

The runner must qualify one shared SDK transport integration that preserves the existing destination enforcement and checks response byte limits before SDK parsing, including secret-bearing STS bodies. Use fixed regional endpoints from the supported region set, TLS, public destination pinning, no redirects/proxy inheritance and no provider debug logs. Disable SDK retries (`total_max_attempts=1`) and implicit pagination/waiters; every explicit request/continuation shares the operation's deadline and budgets. Blocking SDK calls run off the event loop under bounded concurrency and remaining-time transport limits. Cancellation/lease loss prevents further calls and fences publication; an already-sent request can finish but cannot cause an automatic retry or late publication. Qualify accounting, cancellation and byte limits at the actual transport, not only around a completed SDK call; if the selected integration cannot enforce them, stop for design review rather than silently weakening limits.

Credential acquisition never uses an ambient SDK chain, credential process, shell, metadata lookup or caller endpoint in this initial profile. A later attached-role/metadata/OIDC source needs explicit configuration and its own destination policy and qualification; it cannot be enabled by making all metadata endpoints reachable. Use `cryptography` for the encrypted store as specified in section 5, independently of SDK authentication.

For the size read, use CloudWatch `GetMetricData` with server-constructed `AWS/S3` / `BucketSizeBytes` queries, `BucketName` and a versioned maintained list of supported `StorageType` dimensions, statistic `Average`, 86,400-second period and the preceding seven days. At most 64 queries, 1,024 returned datapoints, 8 HTTP requests total (including credential/identity/binding checks and any bounded continuation), 30 seconds wall time, 2 MiB decompressed bytes per response and 64 KiB normalized evidence. Exceeding request/byte/time limits produces a bounded failure or explicitly partial observation—never an unbounded scan. Track coverage of the versioned storage-type set; unknown future types are not silently treated as included.

**IAM scope limitation:** CloudWatch metric reads do not provide S3 bucket-ARN-level IAM restriction. The example grants only necessary metric-read actions, generally with `Resource: "*"`, plus applicable account/region controls, and bucket-scoped `s3:GetBucketLocation`. The gateway enforces selected bucket scope. Document that a compromised holder of the role session can read other metrics allowed by AWS; do not describe the session itself as bucket-isolated. A deployment requiring provider-enforced per-bucket metric isolation must choose another qualified collection design before accepting this profile.

Return a typed observation containing source, bucket/object/binding/Connector refs, account/region, operation ID, retrieved time, per-storage-type datapoint time/value, covered/queried types, truncation and coverage state. Choose a common latest measurement timestamp where possible; never sum datapoints from different days into a purported coherent total. Missing datapoints mean unknown, not zero. Without independently established full coverage, label the sum **reported storage subtotal** rather than total bucket size. An empty successful response is `no_data`; it is not "empty bucket". Mark a datapoint older than 48 hours as stale while preserving its actual time.

Explain that S3 daily storage metrics may include noncurrent versions, multipart-upload parts and metadata according to storage type; this is not just the sum of current object bodies, a real-time measurement or a billing estimate. The compact model result is at most 16 KiB and links exact immutable evidence `s3-bucket-storage.json`. The artifact adds kind `cloud_observation` through migration; its ownership, quota, pinning, purge/tombstone and restore behavior are unchanged. Do not copy raw AWS responses or session information into evidence.

## 8. Proposed 5B reads

- `aws_s3_list_bound_buckets`: paginated local inventory of owned resource bindings, default 20/max 50; not AWS account-wide discovery or a claim of live existence. The response explicitly labels its source as stored bindings.
- `aws_s3_bucket_metadata`: one selected bound bucket's location and versioning configuration using a bounded maintained API set, adding bucket-scoped `s3:GetBucketVersioning` only after this unit is approved. This cannot enable versioning or read objects. Observations use the same resource/operation/artifact publication path.

5B gets separate manifest digests, permission documentation and a live read qualification. No new credential backend, resource registry or model abstraction is introduced. Additional metadata/log operations require their own accepted scope rather than arbitrary AWS calls.

## 9. API and browser

Project routes use current JWT/project-owner checks; foreign IDs return 404. Global-management routes additionally require the configured operator user from section 4.1; global use is resolved through an owned consuming project. No browser credential-read endpoint exists. Caller-supplied revision preconditions prevent lost configuration updates; request IDs make creation/replacement/activation safe under uncertain submission. Exact secret-write retries use a request ledger scoped to the management actor, owning scope and command, with a keyed server-side HMAC of the secret-bearing canonical request (and a separately protected idempotency key). Never persist plaintext or an unkeyed secret digest. Keep that key ID valid for the ledger's declared 24-hour retry window; reject an expired request ID rather than creating another credential version. Do not automatically retry a cleared secret form; allow checking the submitted request's authorized status before re-entry.

Setup writes require an Origin matching a configured browser origin, parse bounded JSON bodies, reject duplicate properties and non-finite numbers, and use a database-backed per-actor/scope write limit. Authentication and project/operator authorization run before the route reads secret-bearing content. Validation and persistence errors expose fixed messages without rejected values or driver details.

| Route family | Contract |
| --- | --- |
| `/api/projects/{id}/connectors` | List eligible project/global non-secret usage metadata; create project-owned Connectors only; typed service forms, no arbitrary backend/plugin selector |
| `/api/projects/{id}/connectors/{connector_id}` | Owned metadata/current revision and enable/disable/revoke commands with expected revision |
| `/api/projects/{id}/connectors/{connector_id}/credentials` | Write-only replacement; owned request-status lookup; never a credential read endpoint |
| `/api/projects/{id}/connectors/{connector_id}/checks` | Admit a model-free, conversation-associated typed identity/binding check through the existing run queue/gateway; never perform AWS calls in the route |
| `/api/global-connectors`, `/{connector_id}` and `/{connector_id}/credentials` | Operator-only list/create/configuration/enable/disable/revoke/write-only replacement and request-status lookup for global Brave/Tavily, with the same revision/idempotency rules; project mutation routes cannot modify globals |
| `/api/projects/{id}/connector-defaults/{service}` | Owner sets/clears an eligible project/global Connector ID with a revision precondition; admission pins its current revision; no provider or credential fallback |
| `/api/projects/{id}/environments` and `/resources` | Bounded owned environment/resource/binding setup and list; non-secret references are still sensitive |
| `/api/projects/{id}/resources/{resource_id}/observations` | Bounded observation metadata/evidence refs; no cross-conversation artifact access expansion |
| Existing tool/chat commands | Add selected target refs and eligible global/project Connector selection; preserve exact submitted payload/retry identity |
| `/api/runs/{id}/resume-connector` | Validated atomic pre-dispatch resume command described above |
| Existing capabilities/snapshot/SSE | Project-aware permitted definitions/readiness, blockers and observations; same durable event protocol |

For AWS, the check route invokes maintained capability `aws_check_connection` through the same model-free tool command contract: an owned Connector reference and optional owned resource binding, 30-second/8-request bounds, configured-role acquisition, `GetCallerIdentity`, and optional expected-owner/region validation. Its result is safe identity/readiness metadata, never credentials, and its manifest is not exposed to automatic model selection. For search Connectors, setup checks only local credential-store readiness; it does not issue a hidden billable search or advertise upstream authentication as verified. A normal explicit search supplies that evidence.

The Connector setup page offers independent Brave/Tavily forms with global/project visibility and AWS **Assume configured IAM role** with project scope. Only the configured operator sees global management forms; other project owners can select eligible globals and set project defaults. Display scope, management authority and the all-project impact of global rotation/revocation. For AWS, explain authentication source versus execution role; show the configured account, readiness, required trust/permission setup, expiry/rotation guidance and revoke/replace controls. Explicitly label the proposed machine-key source and attached-role/federated/certificate alternatives that are not yet qualified. Do not display maintenance controls before their phase is enabled.

The user selects a configured target in the existing composer and can attach the explicit storage tool or enable qualified automatic tool use. Include only authorized selected resource metadata in model context; ambiguous names require selection, not cross-project search. Cloud observations use the compact operation entry and existing files panel; show measured-at versus retrieved-at time, stale/missing/partial state, source and evidence separately from the assistant's interpretation. Keep safe Markdown, keyboard controls, mobile layout, exact retries and stale-response guards. Setup never submits secrets as chat messages.

Add fixed versioned events `connector.blocked`, `connector.resumed` and `resource.observed` through the current outbox/SSE machinery; no secret values, role-source details or raw AWS bodies. Configuration audit records have their own append-only rows at the owning project or installation-global scope, not fabricated conversation messages. Global management records are operator-only; each consuming project's execution audit remains project-owned. Record actor, visibility/owning scope, consuming project where applicable, Connector/binding revision, policy decision, declared execution identity, operation/result/evidence IDs and time. Existing observation artifacts remain conversation-owned: a project observation list cannot grant access to another conversation's evidence bytes.

## 10. Migrations, operational controls and defaults

Add sequential Alembic revisions after `phase4c008`: `phase5a009` for global/project Connector/credential/default/audit records and scope constraints, followed by `phase5a009b` to allow a cleared-default tombstone while retaining monotonic settings revisions; `phase5a010` for environments/resources/observations, new artifact kind and connector wait state; `phase5b011` only when 5B requires an additional schema change. Do not add an empty migration for metadata-tool breadth. Preserve existing rows/provider configs/transcripts and explicitly reject destructive downgrade when protected new data exists. No live API call, filesystem access or secret import occurs during schema migration.

Upgrade drains old workers before enabling new execution/manifest versions. Unknown versions fail closed and remain readable. The runtime advertises versioned managed-Connector support and fresh per-project readiness; API cannot dispatch a new graph to an incompatible worker. Qualify the upgrade/downgrade boundary with old queued runs and ready files. No team role tables, sandbox process or shared storage is introduced here.

Initial limits: 10 project Connectors per project plus 10 global Connectors per installation, 10 environments per project, 100 resource bindings per project, non-secret config 16 KiB, decrypted credential payload 16 KiB, list pages default 20/max 50, identity/observation operation deadline 30 seconds, existing tool concurrency/artifact quotas and existing per-run/model budgets. Charge global use to the consuming project's existing run quotas; one global key does not create unbounded tool capacity. Do not globally increase Phase 4 tool-call budgets for these reads. Configuration command bodies and attempts are size/rate limited; blocked waits expire and cannot create unlimited retained run/artifact pins.

Safe errors distinguish `connector_required`, `connector_ambiguous`, `connector_expired`, `connector_revoked`, `credential_store_unavailable`, `connector_scope_denied`, `connector_revision_changed`, `aws_trust_denied`, `aws_account_mismatch`, `resource_owner_mismatch`, `resource_region_mismatch`, `metric_unavailable`, rate/timeout/output limits and interruption. Never include upstream credential/source/request contents. Unknown errors fail closed with a fixed message, not a raw exception.

## 11. Requirements and acceptance evidence

| ID | Acceptance requirement | Unit |
| --- | --- | --- |
| R01 | Current auth/project isolation, plain chat, provider pinning/titles, cancellation/reconnect and exact file recovery remain valid | All |
| R02 | Browser-managed independent global/project Brave/Tavily Connectors work through the same gateway; explicit migration preserves v1 pins | 5A.1 |
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
| R13 | One global search Connector is usable from separate projects without copying its secret; project credentials stay isolated; operator-only management, immutable visibility, deterministic defaults/ambiguity and global revocation are enforced | 5A.1 |
| R14 | A second test-only registered AWS handler reuses the same runtime-authenticated SDK context and common runner without changing credential acquisition or adding provider-protocol code; it is not a shipped capability | 5A.2 |

Required lanes:

- **Unit/contracts:** strict schemas/refs/digests, global/project eligibility and operator management, defaults/ambiguity without fallback, immutable visibility, scope-bound AEAD substitution/tampering, owned role selection, expiry/rotation, per-consumer credential-cache separation, SDK client configuration/runner integration and safe result mapping. Qualify actual transport request/deadline/byte accounting, retries disabled, blocking-call cancellation, fixed endpoint/DNS/proxy/redirect rejection, secret sentinels and hostile cloud strings. Assert no credentials in HTTP error validation bodies, logs, browser read responses, model requests, checkpoints, transcripts, artifacts or events. A synthetic second handler proves extension reuse; a restricted interface is not evidence of malicious in-process tool isolation.
- **Real PostgreSQL 15:** migrations/history/protected downgrade; concurrent configuration versions/idempotency; global search reuse across distinct personal projects, denied non-operator management, denied cross-project secret/target/evidence access and scope constraints; project defaults versus global rotation/revocation races; blocker consumption/resume versus cancel/active-run races; observation/artifact/outbox publication; restore of stale revocations and mixed worker versions. Required cases run with zero required skips.
- **Real filesystem and synthetic stack:** actual API/worker/PG/artifact bytes with deterministic STS/S3/CloudWatch/model transports; distinct process restarts before intent, after acquisition/read and before publication; missing keys/volume; retained-evidence continuation; no automatic cloud/model redispatch. Synthetic credentials never reach persisted/public surfaces.
- **Browser fixture and real-stack:** operator global setup and project-owner use, project search setup, scope/default/ambiguity presentation and global mutation rejection; key replacement/revocation without secret persistence, target selection, direct/tool-chat observation, safe evidence/time/coverage rendering, responsive keyboard-accessible configuration blockers and conversation continuity. Fixture tests do not establish AWS trust or permissions.
- **Live opt-in:** explicitly chosen account/role/bucket and declared source, independent identity and metric comparisons, real renewal and safe access-denial evidence. No CI cloud credentials and no unapproved writes. Never probe `DeleteBucket` against the user's real bucket; use administrator-reviewed effective-policy evidence and declare simulator limits, or an independently authorized disposable denied-mutation fixture. Record exact source/profile/tool revisions, time and limits; mocks do not qualify IAM authority.

Deliver separate 5A.1/5A.2/5B demonstrations and user accept/revise/stop decisions. An AWS-specific qualification matrix belongs with the Connector runbook; model-provider qualification still belongs in `docs/provider-support.md`. Documentation, schemas and green tests alone do not accept a milestone.

## 12. Implementation boundaries for the subsequent plan

| Location | Responsibility |
| --- | --- |
| `apps/api/src/openship/connectors/` (new) | Global/project revision/secret models, eligibility/operator management/defaults, typed configuration, write-only setup service/routes, availability and scoped audit; no model adapter inheritance |
| `connectors/credentials/` (new) | AEAD storage/key rotation, runtime broker, scoped in-memory leases; no graph-serializable credentials |
| `connectors/aws/` (new) | Explicit source-to-role acquisition and authenticated native SDK client construction; no service-operation proxy or generic cloud RPC |
| `resources/` (new) | Project/environment object/binding/observation records and owned projections |
| `tools/aws/` (new) | Ordinary boto3-based S3 storage and later metadata/inventory handlers, DTOs and output validation; no authentication or signing implementation |
| Existing `capabilities/` and `execution/` | Maintained definition table, v1/v2 resolution, visibility/project/target admission, common SDK runner with bounded transport and cancellation, fencing/publication and safe result projections |
| Existing `runs/` | Versioned graph dispatch, secret-free config pinning and preflight wait/resume; preserve budgets and no partial-loop replay |
| Existing `artifacts/`, migrations and Compose | New observation kind through existing storage, key mounts/configuration, coordinated recovery and version qualification |
| Existing browser routes/services/stores/components | Owned Connector setup and target selection, safe compact observations/blockers; preserve current chat/files/model controls |

The implementation plan follows written-spec approval and must give failing tests, exact files/contracts, focused changes and verification for each runnable acceptance unit. No product implementation is authorized by preparing this document.

## 13. Sources and declared limits

- [AWS IAM roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles.html): roles, trust and temporary sessions.
- [STS AssumeRole](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html): session duration and session-policy intersection; no privilege expansion by session policy.
- [AWS-hosted application roles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_switch-role-ec2.html): temporary credentials without an IAM user; hosting/trust configuration is still required.
- [Boto3 clients](https://docs.aws.amazon.com/boto3/latest/guide/clients.html) and [sessions](https://docs.aws.amazon.com/boto3/latest/reference/core/session.html): SDK-owned service models/operations and explicit credential/client construction; runtime execution controls remain OpenShip's responsibility.
- [Boto3 retry configuration](https://docs.aws.amazon.com/boto3/latest/guide/retries.html): `total_max_attempts` includes the first request; set it to one to disable SDK retries.
- [GCP workload identity to AWS](https://aws.amazon.com/blogs/security/access-aws-using-a-google-cloud-platform-native-workload-identity/) and [IAM Roles Anywhere](https://docs.aws.amazon.com/rolesanywhere/latest/userguide/introduction.html): alternatives requiring explicit source/trust setup, not qualified here.
- [S3 metrics and dimensions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html): daily storage measurements, storage types and semantics.
- [CloudWatch permission reference](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/permissions-reference-cw.html) and [service authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazoncloudwatch.html): action/resource restriction limits.

Source documentation establishes provider mechanics, not OpenShip implementation or live qualification. The first credential source, CloudWatch read-scope trade-off and limited pre-dispatch resume behavior are explicit proposals for user review.
