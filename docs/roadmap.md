---
type: roadmap
summary: Cumulative delivery phases that establish shared foundations with one working use case before expanding capabilities and deployment scale.
status: proposed
date: 2026-11-08
---

# OpenShip Delivery Roadmap

Build OpenShip through **small, end-to-end milestones**, rather than finishing entire backend subsystems before users can try them.

This roadmap is based on the [OpenShip Harness Architecture](./architects.md) in PR #18, at architecture revision `9fe9f37`, and was reviewed after delivery of Phases 1–3.

**Delivery baseline:** The user confirmed **Phases 1–5 complete**, **6A implemented** (PR #31 at `5fe6e03`), and **6B complete** (PR #32 at `60f0917`). Earlier descriptions/qualification records retain their historical evidence scope, not new deployment guarantees; the 6B operations record still distinguishes the verified chat-directed Python journey from pending analysis-specific journeys. The [provider support matrix](./provider-support.md) remains authoritative for provider/model qualification claims. **6C's implementation and local image validation are complete; dev-stack recreation, the live `logs-data` chart journey and user acceptance remain pending; Phases 7A, 7B and 8–21 remain pending delivery work.** The Phase 10B two-role direction is an agreed design, not implementation or acceptance.

**Phase 7 split (requested 2026-11-08):** Add **7A — Outcome-Driven Agentic Planning and Execution** before the former Phase 7, now **7B — Typed Reusable Workflow Execution**. The former 7A/7B acceptance units become **7B.1/7B.2**. Historical references to Phase 7's saved workflows refer to 7B; later numbered phases are not renumbered. The new 7A requirements are proposals for collaborative spec/plan review, not an approved implementation design.

**Accepted forward architecture change:** NATS with JetStream is the main internal message broker. [Architecture §5](./architects.md#nats-messaging-foundation) defines routing, request/reply, and durable delivery. Phase **6A** introduces that transport and concurrent chat/SSE delivery; **6B** retains the generated-code/artifact milestone. This change applies from Phase 6 onward and does not rewrite or reopen Phases 1–5. Broker selection is accepted; implementation details and phase acceptance still require their normal review.

The [foundation review](./reviews/2026-10-14-roadmap-foundations-review.md) explains the dependency corrections and the responsibilities established by each remaining phase.

The roadmap covers the architecture's target scope: a self-hosted conversational harness, secure execution, reusable workflows, event-driven automation, community capabilities, infrastructure management, and Kubernetes deployment.

## 1. Delivery Principles

### Prioritization

Order phases by:

1. **Dependency:** build what subsequent capabilities require.
2. **Safety:** establish enforcement before exposing the capability it protects.
3. **User value:** deliver an observable, useful outcome.
4. **Size:** among ready phases, choose the smallest useful increment.
5. **Breadth:** support one complete integration before expanding to more.

"Small first" does not mean phase sizes increase mechanically. A small Slack integration still belongs after the execution and authorization foundations it depends on.

### Every Phase Must Deliver

- A runnable product increment—not only schemas, interfaces, or documentation.
- One reproducible user demonstration.
- Measurable acceptance checks.
- Visible failure behavior.
- Explicit exclusions.
- A user decision: **accept, revise, or stop**.

### Completion Is Scoped

"Complete OpenShip" means implementing the architecture against a **declared support matrix**: supported models, platforms, brokers, execution profiles, and deployment environments. It does not mean supporting every cloud service or every community package.

The numeric checks below are **proposed acceptance thresholds**, not measured results or delivery-time estimates.

### Build Once, Expand Through the Same Contracts

Phases are cumulative product increments. Independent acceptance means that an increment can be evaluated on its own; it does not authorize an independent chat service, workflow engine, queue, authorization path, or event protocol.

- **Start from the delivered foundation.** Extend the Phase 2 run/queue/lease/checkpoint/outbox/event infrastructure and the Phase 3 provider boundary. Preserve authentication, project isolation, conversations, auto-titles, cancellation, reconnect, and pinned provider selection.
- **Establish the boundary with its first consumer.** The first tool establishes the gateway and minimal artifact contract; the first connector establishes credential and target bindings; the first workflow establishes revision and compiler compatibility. Implement one usable instance, with real failure tests, before adding breadth.
- **Keep the first implementation small.** Start with one maintained tool, one local artifact backend, one cloud connector, and sequential workflows. Do not build unused node types, a generic plugin framework, distributed storage, or another scheduler in anticipation of later phases.
- **Separate extension from replacement.** Later adapters, node types, storage backends, and deployment profiles use existing contracts. A necessary incompatible change needs an explicit migration, compatibility/upgrade tests, a removal boundary for superseded paths, and preservation of accepted user behavior.
- **Carry acceptance forward.** Every phase reruns the applicable earlier user journeys and adds its new failure/recovery cases. New provider capabilities extend the Phase 3 conformance suite; they do not silently become supported on all profiles.
- **Introduce controls before exposure.** Authorization, worker authentication, resource limits, cleanup, and audit begin with the capability they protect. TLS is required before remote exposure. Each new durable store includes backup/restore and upgrade instructions. Phase 20 qualifies these controls under team operation; it does not first introduce them.

### Foundation Ownership

These are responsibility boundaries, not a requirement to implement future functionality early.

| Foundation | First working implementation | Later expansion through the same contract |
| --- | --- | --- |
| Durable execution, HTTP commands, semantic events, provider pinning | Delivered in 2–3 | Tools in 4, agentic task continuation in 7A, saved workflow steps in 7B, waits in 8, triggers in 13, composition in 15 |
| Gateway authorization context and capability identity/version/digest | 4: current project-owner rules and one maintained tool | Connector operations in 5, imports/discovery in 11, MCP in 12, team roles in 20 |
| Operation identity, fenced dispatch, bounded result publication | 4: one maintained read operation | Mutation reconciliation and conflict control in 10, Git/Terraform in 16–17 |
| Artifact identity, ownership, immutable versions, bounded transfer, storage interface | 4: uploaded files and search results on durable local storage | Generated code/previews in 6, plans in 16, shared storage when multi-host operation requires it |
| Model/tool conversation loop | 4: one tool-capable provider/profile, one tool, bounded calls | Outcome-driven planning/verification and execution-context selection in 7A, discovery in 11, MCP in 12, agent steps in 15; each provider/profile requires qualification |
| Connector credentials, scoped target references, timestamped observations | 5: one staging target and one read operation | Mutation in 10, Terraform bindings in 16–17, drift and further clouds in 18 |
| Workflow revision, step identity, compiler/runtime compatibility | 7B: one sequential saved workflow | Wait nodes in 8, authoring in 9, additional node types in 15 |
| Decision identity, eligible actor, digest binding, atomic consumption | 8: one durable input/approval request | Concrete mutation approval in 10, Terraform approval in 17, other clients in 19 |
| External-event inbox, trigger identity, scoped initiator, admission deduplication | 13: one signed webhook and saved workflow | Schedules in 13B, alarm/broker adapters in 14, other sources in 19 |
| Safe deployment and recovery | 2–3 baseline; extend whenever a store, worker, or privilege is added | Team and remote qualification in 20, Kubernetes packaging/scaling in 21 |
| NATS internal commands/events, correlated responses, concurrent worker intake | 6A: migrate delivered chat/background dispatch and SSE delivery | Agentic continuation in 7A, saved workflows/waits in 7B–8, external admission in 13, source adapters in 14/19, operations/scaling in 20–21 |

## 2. Roadmap Overview

**Size:** S = focused increment; M = new boundary or several closely related behaviors. Broader phases are explicitly divided into independently accepted increments.

Dependencies identify prerequisites; they do not require every preceding phase to be complete.

Keep the phase IDs stable except for the explicit Phase 7 split above. Phases 4 onward are dependency-ordered branches, not a requirement to finish every lower number before starting a ready increment. In particular, Phase 20 can qualify the first complete mutation journey after Phase 10; it need not wait for more clouds, brokers, or client adapters. Each subsequently enabled feature must pass the relevant deployment checks before remote use.

| Phase | Working milestone | Dependencies | Size |
| --- | --- | --- | --- |
| 1 | Authenticated browser chat runs locally — complete | — | S |
| 2 | Conversations and run progress survive reconnects — complete | 1 | M |
| 3 | The same workspace works across model providers — complete | 2 | S per adapter |
| 4 | Trusted search and persistent conversation files — implemented | 3 | M per increment |
| 5 | OpenShip inspects one real cloud environment safely | 4 | S–M |
| 6 | NATS carries concurrent chat/events, generated code produces controlled artifacts, then prebuilt images expand offline tooling | 5; 6B builds on 6A; 6C builds on 6B | M per increment |
| 7A | A request becomes a tool-aware plan that the agent executes through a verified outcome | 4, 6A, 6B; 6C for the expanded execution profiles | M per increment |
| 7B | A saved, typed workflow can be run repeatedly | 4, 6, 7A | M per increment |
| 8 | Workflows wait for input or approval across restarts | 7B | S–M |
| 9 | Conversation becomes a reviewed, reusable workflow | 5, 7B, 8 | M |
| 10 | One approved change, then scoped temporary maintenance authority | 5, 8, 9; 10B builds on 10A | M per increment |
| 11 | Relevant tools and skills are discovered progressively | 4, 7B, 9 | M per increment |
| 12 | Local and remote MCP tools use the same policy boundary | 4, 5, 11 | M |
| 13 | A webhook or schedule starts a durable read-only workflow | 7B, 8 | M per increment |
| 14 | A cloud alarm or broker message drives investigation | 5, 13 | S–M per adapter |
| 15 | Workflows support bounded branching and composition | 7B, 8, 10 | M per increment |
| 16 | Requirements produce reviewed Git artifacts and Terraform plans | 5, 6, 9, 10 | M |
| 17 | The exact approved Terraform plan provisions tracked resources | 8, 10, 16 | M |
| 18 | Resource drift and additional clouds are supported | 5, 17 | M per increment |
| 19 | Chat, email, file, and state-change adapters reuse automation | 8, 11, 13; source-specific prerequisites below | S per adapter |
| 20 | A team can operate and recover a remote installation | 4, 5, 7B, 8, 10; all features enabled in that deployment | M per qualification gate |
| 21 | The qualified product runs and scales on Kubernetes | 20 | M per qualification gate |

### Wave A — A Small, Usable Harness

#### Phase 1 — Authenticated Local Browser Chat

**Status:** Complete. Its user-facing features are retained in the current Phase 3 application; its original execution service is historical, not a second runtime to maintain.

**Milestone:** "I can install OpenShip, open my browser, sign in, and have a conversation."

**Build**

- Minimal Vue/TypeScript workspace and FastAPI service.
- PostgreSQL-backed installation identity and personal project.
- One model-provider adapter.
- Streaming responses, bounded requests, cancellation, and safe error reporting.
- Local Compose packaging and health checks.
- Authentication and default-deny API access from the first usable release.

**User demonstration:** Install from documented instructions, sign in, ask a DevOps question, and cancel a second response.

**Exit gates**

- A user completes installation without undocumented developer steps.
- Ten conversation turns complete with visible responses or actionable errors.
- Unauthenticated requests cannot access the workspace.
- Missing model credentials produce a configuration message, not a crash or secret-bearing response.

**Not yet:** Tools, executable code, cloud access, or reusable workflows.

#### Phase 2 — Durable Workspace and Observable Runs ✅

**Milestone:** "I can reconnect or restart OpenShip without losing my conversation or the recorded state of my work."

**Status:** Complete — PR #21. Its durable runtime remains the execution foundation for subsequent phases.

**Build**

- Persistent conversations, messages, runs, and distinct graph-thread identities.
- Project-scoped access checks.
- PostgreSQL checkpoints, queue ownership, leases, and transactional outbox.
- Durable semantic events, SSE reconnect cursors, and current-state snapshots.
- Basic run cards, usage accounting, retention limits, and structured logs.

**User demonstration:** Start a conversation, disconnect the browser, restart a runtime worker, and reopen the same workspace.

**Exit gates**

- Completed messages and run state survive three restart/reconnect scenarios.
- Repeated event delivery does not duplicate visible messages or transitions.
- Concurrent workers cannot become unfenced writers of the same run.
- An interrupted model request is visibly resumed, paused, or failed—not silently reported as complete.
- Cross-project conversation and event access is denied.

**Not yet:** Exactly-once external effects or cloud mutations.

#### Phase 3 — Model-Provider Portability

**Status:** Complete — PR #22. Supported tuples and live qualification remain scoped by the provider support matrix.

**Current chat contract:** Use explicit model profiles and the durable run/event
API. The Phase 1 POST chat stream, direct OpenAI service, and implicit single-provider
configuration are retired. Authentication, conversation history, auto-titles, and
historical data migrations remain part of the application.

**Milestone:** "I can choose my model provider without changing how I use OpenShip."

**Build**

- Adapter conformance suite.
- Anthropic, Google, and configurable OpenAI-compatible support, added one at a time.
- Normalized streaming, errors, cancellation, usage, and capability declarations.
- Run-pinned provider configuration and explicit fallback behavior.

**User demonstration:** Perform the same conversation scenario on each supported adapter.

**Exit gates**

- Each adapter passes the same applicable conformance suite.
- Unsupported capabilities are explained before invocation.
- Rate limits and outages respect retry and time budgets.
- Provider changes do not change execution authority.
- As tools and workflows arrive, their scenarios are added to this suite.

**Not yet:** Compatibility claims for arbitrary endpoints merely because they advertise an OpenAI-compatible API.

**Acceptance unit:** Each additional adapter is independently demonstrated and accepted.

### Wave B — Useful Work Without Unsafe Execution

#### Phase 4 — Trusted Tools and Persistent Conversation Files

**Status:** Implemented. Qualification evidence and separate acceptance decisions remain scoped by the [Phase 4 acceptance record](./testing/phase4-acceptance.md).

**Milestone:** "OpenShip can use a bounded search tool in chat and continue working with files saved to the same conversation."

**Build on:** The existing run worker, ownership fencing, provider contracts, and command/event API. Phase 4 establishes durable capability admission, bounded trusted-tool execution, immutable artifact snapshots and the first provider-neutral tool loop. Maintained tools execute in the existing runtime worker through the gateway; they do not need a sandbox.

**Acceptance units**

- **4A — Trusted search tools:** Invoke Brave or Tavily through explicit tool selection, persist the bounded observation as an immutable local artifact and recover the conversation after a service restart. Tool enablement and credential bindings remain operator-managed.
- **4B — Model-directed search:** Add a bounded model → exact registered search tool → gateway → result → model loop for one qualified model profile. Plain chat remains available; other profiles add tool support only after qualification.
- **4C — Persistent conversation files:** Associate a stable workspace with each conversation, list/read exact retained artifact versions, and reopen the files panel after restart. Explicit purge uses durable tombstones and coordinated restore verification.

**Build**

- Separate capability manifests, provider adapters, and gateway-owned operation lifecycle with exact IDs/digests, bounded contracts, authorization and fencing.
- Brave and Tavily search as distinct capabilities, sharing only suitable typed transport helpers; provider credentials stay in runtime configuration and out of database/browser/model state.
- Bounded filesystem artifacts with stable IDs, immutable bytes/digests, conversation association, quota counters, safe list/read/download, deletion tombstones, retention recovery and backup/restore verification.
- Provider-neutral tool-call IDs, complete transcript messages and shared call/time/output budgets. Provider-specific wire formats stay inside adapters.
- The compact conversation files panel and inline operation presentation; exact retry payloads and stale-response guards remain in the existing chat UI.

**User demonstration:** Search from chat, inspect the compact result, reopen the same conversation after restarting the API/worker, and ask the agent to find and read the exact saved artifact without issuing a new search.

**Exit gates**

- Search services remain independently enabled and report their own safe failure states; search credentials never reach the browser, model context, or durable transcript.
- Unknown/disabled tools, changed digests, malformed calls, unsupported model profiles, stale fencing, and unauthorized artifact references fail closed.
- Exact artifact IDs and digests survive API/worker restart. Missing/corrupt restored bytes become unavailable; verification does not create a replacement volume or rerun a dispatched search.
- An active run/operation input pin prevents purge. Purge dry-run is non-mutating; interrupted unlink is recoverable and preserves ID/digest tombstones.
- Cross-conversation file reads are denied. Tool result UI, model selection, retries, reconnect, cancellation and existing plain chat journeys pass the applicable browser checks.
- PostgreSQL migration/locking, filesystem lifecycle and synthetic real-stack recovery lanes run without required skips before acceptance.

**Not yet:** User-managed connector credentials (Phase 5), sandbox drivers/images and generated-code execution, mutable logical files or edit/version capture (Phase 6), or shared/distributed artifact storage. Phase 4 only promises immutable captured versions; it does not require future step outputs to be immutable.

#### Phase 5 — First Read-Only Cloud Connector

**Status:** Complete, as confirmed by the user on 2026-10-25. Earlier qualification records and the implementation-era descriptions below retain their original evidence scope; this confirmation does not broaden provider/cloud qualification claims.

**Milestone:** "OpenShip can discover and inspect AWS resources through my configured basic role and show evidence—for example, an S3 bucket stats report."

**Build on:** Phase 4's gateway, tool loop, operation records, and artifacts. Add project-scoped connectors and managed credentials through the gateway's existing capability contract; do not create a parallel provider-adapter or permission path for external services.

**Acceptance units:** **5A.1** establishes managed global/project search Connectors, shared use and isolation. **5A.2** delivered the accepted single-bucket storage demonstration. Its manual binding step is superseded by the user's 2026-10-23 direction for new execution: the basic role determines resource access, without registering or selecting individual resources in OpenShip. **5B** retains managed tool availability and replaces binding-based local inventory/metadata with live discovery and connector-scoped reads. CloudWatch daily storage metrics are timestamped observations, not an instantaneous object scan; missing or partial metrics are not zero. S3 is the report example, with EC2/RDS proving the same reader extends across services. Add other clouds in Phase 18. The [replacement design](./superpowers/specs/2026-10-23-phase5-aws-role-based-discovery-design.md) and [implementation plan](./superpowers/plans/2026-10-23-phase5-remove-bindings-and-enable-aws-discovery.md) capture the approved replacement, which is being implemented and still requires qualification and acceptance.

**Build**

- One maintained AWS connector, initially using S3 bucket-storage observations rather than an EC2/ECS deployment-health example.
- A dedicated organization-owned workload identity, independent of an employee's personal credentials. For AWS prefer an IAM role and temporary sessions; retain the human initiator separately for audit. A role ARN is an identifier, not authentication: the configured credential source must prove a trusted identity before AWS issues a session. An IAM user is not required by the architecture; AWS-attached roles, federation and certificate sources are deployment alternatives requiring separate qualification. The proposed first localhost source is a dedicated assume-role-only machine key; declare its storage, standing authority, expiry and renewal/rotation behavior.
- Project-owned connector configuration pins the source, basic role and expected execution account. New reads choose resource names/ARNs and regions at execution time, with no environment or resource binding prerequisite. Timestamped observations retain target and connector identities as evidence; historical resource/binding rows remain readable without becoming an access whitelist. Phase 17 adds provisioning relationships only when needed.
- Managed Brave/Tavily Connectors with explicit `global|project` visibility. Configure global search once for reuse across authorized projects, with operator-only management; project owners manage their own project Connectors. AWS remains project-scoped. Pin the selected Connector revision per run, preserve project-owned evidence, resolve defaults/ambiguity explicitly and never fall back silently. Migrate Phase 4 operator keys only on explicit request with declared scope; Phase 4 stores no search credentials in the database or browser.
- Internal credential broker in the existing worker, with one configured AWS source and short-lived delegated credentials where supported. The setup API encrypts secrets in PostgreSQL; queued runs carry references; the worker authorizes scope/revision/target, decrypts the selected secret and supplies a restricted key or authenticated SDK context to a trusted tool. Keep AWS bootstrap keys inside acquisition code. Protect encryption keys separately and document rotation, revocation and recovery before accepting real credentials. This is a trusted-process boundary, not malicious-tool isolation.
- Developer extension through the existing tool definition table and a shared SDK read catalog/reader. Generate supported read operation schemas from versioned AWS/Botocore metadata rather than adding a handwritten handler for every API method. Native boto3/botocore clients own provider protocols; the existing runner preserves bounded transport, cancellation, accounting and safe publication. AWS evaluates each request's permissions. Keep unsupported operations distinct from denied permissions and reject mutations before dispatch; do not promise every SDK operation is qualified.
- Retain 5B's installed built-in tool discovery and shared operator enable/disable controls in Global Settings without restarting API/worker. Derive readiness from the connector and runtime, without requiring a resource selection. Keep `TOOL_ENABLED_IDS` as an optional deployment restriction with an explicit legacy cutover; ordinary setup must not require adding each tool ID or AWS API method. Phase 11 still owns plugin discovery/imports; the new AWS API catalog does not import plugins. See the [replacement 5B plan](./superpowers/plans/2026-10-23-phase5-remove-bindings-and-enable-aws-discovery.md).
- Validated read operations with targets chosen by the agent or supplied by the user, bounded requests/results and honest coverage/freshness. A denied discovery call does not block an independently permitted named-resource read. The bucket stats example needs no object-content download, cloud mutation, deployment or log provisioning.
- Secret-safe audit records and evidence-linked responses.

**User demonstration:** First configure one global search Connector and use it from separate projects, then verify a project Connector is unavailable outside its project and non-operators cannot change globals. Configure the AWS connector's basic role without creating resource bindings, discover S3 buckets, and ask for a bucket stats report. Inspect metric time, coverage, permission gaps and exact evidence; demonstrate the same reader with EC2/RDS and reopen the conversation after restart. Compare selected live results independently only with separate authorization.

**Exit gates**

- Results match an independent console/API check.
- Credentials cannot perform a representative prohibited mutation.
- Credentials do not appear in model context, browser responses, logs, or persisted checkpoints.
- Expired or missing access produces a recoverable blocked state.
- Every observation identifies its target, source, measurement time and retrieval time. Bucket-size results disclose stale, missing and partial storage-type coverage rather than claiming an exact current total.
- Altered connector references cannot cross project authority. Requested resource access is evaluated by AWS under the pinned basic-role session, including any permitted shared resource; discovery does not promise a complete permission inventory. Untrusted cloud/log content cannot change gateway policy or receive credentials.
- Global search reuse does not copy secrets or grant cross-project management/evidence access; selection/defaults, global revocation and scope-bound credential storage are verified.
- A test-only additional SDK handler reuses the same authentication and execution controls without changing credential acquisition; SDK upgrades do not automatically expose new agent capabilities.
- Credential expiry/revocation blocks new dispatches; recovering the connector does not silently replay a potentially dispatched operation.

**Not yet:** Remediation, maintenance-role execution, permission-elevation approvals, generic credentialed shell/generated-code access or independently portable Connector packaging/lifecycles. Portable components are deferred without assigning a roadmap phase. Phase 5 qualifies only basic read authority in trusted built-in tools; the two-role model below is not a reason to build an unused permission manager here. Later sandbox/CLI executors may reuse credential acquisition only after their own execution and secret-handoff qualification.

**Important distinction:** Short-lived credentials reduce exposure; they do not automatically remove standing permissions or make issued cloud tokens universally revocable.

#### Phase 6 — NATS Messaging and Controlled Execution

**Milestones:** **6A:** "Several conversations progress through NATS without dispatch/event polling, and worker responses reach the correct client." **6B:** "OpenShip can write and run a small analysis program, then show me its outputs." **6C:** "I can build and select a prebuilt sandbox image with the libraries and CLI tools my offline task needs."

**Build on:** The delivered Phases 1–5, preserving their HTTP commands, conversation history, pinned models/connectors, cancellation, ownership/fences, checkpoints, and event identities. The [accepted NATS direction](./ideas.md#nats-messaging-foundation) changes internal transport; it does not introduce a second runtime or permission path. All later execution/event work builds on 6A; the explicit Phase 7 split does not renumber Phases 8–21.

**Acceptance units:** **6B** builds on merged **6A** and carries forward applicable regressions. **6C** extends 6B's image packaging without changing execution authority or interfaces. Each is a usable increment with its own evidence and user decision; neither image expansion nor the 6B scope reset reopens 6A deployment qualification.

##### 6A — NATS Foundation and Concurrent Chat

**Status:** Implemented and merged in [PR #31](https://github.com/nhtua/OpenShip/pull/31) at `5fe6e03`, as confirmed by the user. Earlier plans/runbooks retain their evidence scope; this status does not claim production cutover or every qualification scenario passed. Kubernetes/ECS are outside this migration. See the [source review](./reviews/2026-10-25-phase6a-messaging-review.md), [migration design](./superpowers/specs/2026-10-25-phase6a-nats-messaging-design.md), [implementation plan](./superpowers/plans/2026-10-25-phase6a-nats-messaging.md) and [deployment runbook](./operations/messaging.md).

**Build**

- Package a pinned NATS server and async Python client with file-backed JetStream storage, scoped service credentials, private endpoints, health/readiness checks, and repeatable installation/upgrade/recovery instructions. Declare the initial availability and disk-durability profile; do not imply single-node high availability.
- Define versioned command/event/correlation envelopes, subject ownership, registered handler routing, and separate execution-command and replayable-event retention. Start with the existing chat/tool turns plus a supported maintenance task, then reuse the contract for later workflows and sources.
- Publish committed commands/events from the transactional outbox with broker confirmation and recoverable publication identity. Specify an event-driven relay and startup/reconnect catch-up; replace routine PostgreSQL job/event polling. Database ownership checks and bounded recovery/lease timers remain necessary.
- Share durable execution consumers among compatible workers; give API processes independent event subscriptions. Route cancellation to its active owner/interested supervisors. Add bounded concurrent execution and separate chat/background capacity; isolate synchronous graph/SDK work from API and broker event loops.
- Support correlated worker-to-source replies with bounded asynchronous waits and safe timeout/no-responder errors. Preserve the accepted-run HTTP contract and SSE reconnect/snapshots; ordinary NATS request/reply is one response, while progressive updates use subscriptions. Losing an inbox or timing out does not cancel or repeat a durable run.
- Acknowledge only after a committed result, terminal disposition, or durable handoff/wait. Configure progress acknowledgements, eligible retries/backoff, an inspectable failure destination, retention/overflow limits, consumer cleanup, and controlled replay. Retain operation fencing and uncertain-dispatch recovery.
- Migrate existing queued runs/outbox records and preserve old event cursors/history. Drain or reconcile active pre-migration work under a documented cutover; remove the superseded dispatch/SSE polling loops rather than leave two schedulers competing. Specify rollback eligibility and how new-format in-flight work is drained or blocked.

**User demonstration:** Run several independent conversations and a maintenance task; observe correlated replies and SSE progress, disconnect/reopen a client, and restart a worker, API, and broker without losing a committed answer.

**Exit gates**

- Existing Phases 1–5 user journeys still work through the new transport; no fabricated completion, duplicated assistant answer, or implicit provider/tool retry follows a disconnect or request timeout.
- Fifty concurrent synthetic conversations within configured execution limits remain responsive; ready work uses more than one execution slot, slow work cannot freeze unrelated API requests, and background backlog cannot consume all chat capacity. Record API/SSE latency, queue delay, provider limits, and resource usage against agreed targets; synthetic results are not live-provider capacity claims.
- Repeated publication/delivery, lost publish confirmation, lost consumer acknowledgement, and worker failure before/after dispatch preserve one logical run and safe external-operation outcomes. Malformed/exhausted messages have an inspectable disposition instead of an endless retry loop or silent loss.
- Two API instances with clients observing the same conversation both receive authorized progress. Snapshot/replay and live subscription meet without a gap; slow-client overflow recovers through snapshot/replay and never blocks worker acknowledgement.
- Wrong-project messages/reply subjects, stale fences, expired credentials, and altered execution identities cannot grant authority. Secrets do not enter general event broadcasts or failure records.
- Broker outage retains accepted work in the outbox or reports admission unavailable explicitly. Recovery, storage-full behavior, consumer cleanup, backup/restore, and upgrade/cutover/rollback follow the declared profile. No normal dispatch/SSE table-polling loop remains.

**Not yet:** External triggers, new model capabilities, generated-code execution, or a guarantee of exactly-once model/cloud effects.

##### 6B — Generated Code and Controlled Artifacts

**Status:** Complete as confirmed by the user on 2026-11-07; implemented and merged in [PR #32](https://github.com/nhtua/OpenShip/pull/32) at `60f0917`. The [operations record](./operations/opensandbox.md#real-chat-directed-python-journey--2026-11-06) documents the verified chat-directed Python journey and remaining analysis-specific qualification work; this delivery status does not claim those pending checks passed. The implementation followed the revised [spec](./superpowers/specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md)/[plan](./superpowers/plans/2026-10-28-phase6b-opensandbox-implementation.md). The earlier implementation through `0a1fc6b` remains abandoned historical reference.

**Build on:** Merged 6A and existing gateway/run/artifact contracts. OpenShip prepares source and selected input files, sends authenticated OpenSandbox requests, validates returned data and saves accepted results in workflow state. OpenSandbox owns sandbox lifecycle, isolation, resource enforcement, expiration, cleanup and deletion. Rootful Docker/gVisor is the first provider deployment; only manager infrastructure mounts the engine socket.

**Build**

- Generate/capture Python source, selected text/JSON inputs and bounded parameters using existing immutable artifacts.
- Request a **30-minute (1,800-second)** sandbox lifetime. Keep one **project/conversation-scoped reusable sandbox pointer** for this slice; reuse compatible live sandboxes with a fresh directory per execution, replace on changed runtime requirements or expiry/absence only when execution is requested. Future workflow identity must separate workflows within a conversation. No OpenShip cleanup/reconciliation controller or deletion-gated capacity reservation.
- Accept bounded declared text/JSON outputs; expected code/script outputs remain inert text and require an LLM expectation check before workflow use. Security scanning is deferred, not certified by the judge.
- Save accepted output refs and use them in the next step. Offer **A: rerun the interrupted step using saved prior outputs**, or **B: rerun from the beginning as a new root run**. Neither restart nor broker redelivery silently reruns sandbox code.
- Preserve credential-free execution, existing authorization/quotas/fencing, authorized downloads and escaped previews. Provider configuration owns network/runtime controls.

Start with one credential-free Python standard-library profile and a fixed analysis journey in the existing runtime, not Phase 7B's saved-workflow engine. Mutable logical files/editor/history, additional runtimes, dedicated qualification infrastructure, provider security certification and production/load matrices are deferred.

**User demonstration:** Upload deployment logs, generate/run a parser, validate and retain `summary.json`/`report.txt`, and use them in the answer. Show compatible sandbox reuse and both explicit resume choices.

**Exit gates**

- Exact selected source/input bytes are transferred for each request, without using residual sandbox inputs.
- Create requests ask for 1,800 seconds; compatible requests reuse only the same workflow's handle. OpenShip does not delete, renew or reconcile sandboxes.
- Accepted outputs survive provider expiration and application restart; unsupported/oversized outputs and rejected/unavailable code review cannot advance the workflow.
- A uses saved completed-step outputs without rerunning their sandboxes; B reruns from initial refs under a new root. Repeated resume commands are idempotent, and restart/redelivery does not execute code automatically.
- Provider failure/cancellation produces visible bounded failure; unsafe previews cannot execute browser code or escape storage. Existing applicable tests and one real-service demonstration pass, followed by user acceptance.

**Not yet:** Generated code receiving cloud credentials or unrestricted internet access; OpenShip-owned sandbox lifecycle; a general workflow authoring system; provider isolation/security certification or the dedicated qualification platform.

##### 6C — Prebuilt Sandbox Images

**Status:** Implementation and local image validation complete 2026-11-07. Four images build on the normal Docker daemon and pass offline `runsc` smoke within unchanged limits; the developer stack has **not** been restarted, and live chat/user acceptance remains pending. See the [6C design](./superpowers/specs/2026-11-07-phase6c-sandbox-images-design.md), [implementation plan](./superpowers/plans/2026-11-07-phase6c-sandbox-images.md), and [local image validation record](./operations/opensandbox.md#local-image-validation-record-2026-11-07).

**Build on:** 6B's workload Dockerfiles, deployment image inventory, operator-selected runtime identity and existing Python execution path. Preserve the legacy standard-library and plotting images and their identities.

**Build**

- Add four version-pinned images/profiles: **`logs-data`** (CSV/JSON, Pandas, NumPy, Matplotlib, regex, pygrok, Drain3), **`iac-validation`** (jsonschema, PyYAML, Checkov, TFLint, Graphviz), **`standard`** (Python 3.12+, Node.js, SymPy and standard utilities), and **`devops-shell`** (Bash, Git, AWS CLI, Azure CLI, Google Cloud CLI, Terraform and Terragrunt).
- Preinstall dependencies at image-build time; retain credential-free, offline execution. Include Python 3.12+ in each new image for compatibility with the existing Python entrypoint. CLI tools may be invoked from Python; packaging does not add a shell execution API.
- Use a shared base Dockerfile plus one per profile, hash-verified Python locks, pinned CLI archives and OS repository snapshots. Build locally without a registry account through `sandbox-images list|build|smoke` in `scripts/openship-stack`; normal sandbox startup only checks the selected managed image, while explicit `start --build-sandbox` may build it if missing/stale. Reuse matching images; do not upgrade dependencies implicitly or build from an agent tool invocation.
- Document image contents, exact dependency/runtime identities, build/preload commands, current operator selection, and dev-stack rebuild/restart instructions. Test imports and representative local operations with runtime networking disabled.

**User demonstration:** Build/preload all four images on the selected sandbox engine, run their offline smoke checks, select `logs-data` using existing deployment settings, recreate the relevant dev-stack services, and produce a retained chart through the existing Python tool.

**Exit gates**

- All four images build with recorded base digests, dependency pins and resulting image identities; offline smoke checks pass as the unprivileged workload user.
- Local build commands use the sandbox manager's actual engine/socket, record build-input fingerprints separately from local image IDs, and need no registry login. Normal startup never builds; opt-in startup builds only the selected profile and stops on build failure or an inconsistent runtime selection.
- Analysis parses/clusters sample logs, processes tabular data and renders a headless chart; validation checks sample schemas/configuration and renders a Graphviz diagram; standard execution exercises Python, Node.js and SymPy; DevOps CLIs report their versions and Terraform formats local configuration without cloud access.
- Packaging/configuration regressions pass; legacy profile identities remain unchanged. No mount, network, credential, lifecycle, execution API or UI behavior changes are introduced.
- Operator instructions distinguish image builds from service recreation and list limitations such as absent provider/module/plugin bundles. Live dev-stack testing and user acceptance are recorded separately from image smoke checks.

**Deferred, not rejected:** Repository checkout and directory mounting/staging, writable workflow workspaces, per-task profile selection, a first-class shell tool, network-enabled execution, scoped cloud credentials, connected Terraform planning/apply, and their approval/UI flows. Phase 7A now proposes per-task selection of configured profiles through the existing execution boundary; it does not enable the other deferred capabilities. Installing binaries does not enable them in 6C.

### Wave C — Agentic Tasks and Repeatable, Reviewed Workflows

#### Phase 7A — Outcome-Driven Agentic Planning and Execution

**Status:** Pending implementation; conversational design direction agreed 2026-11-08. The user authorized planning from the [written spec](./superpowers/specs/2026-11-08-phase7a-agentic-planning-and-execution-design.md); the [implementation plan](./superpowers/plans/2026-11-08-phase7a-agentic-planning-and-execution.md) is drafted for review, including carried-forward technical defaults, exact interfaces, migrations, recovery and qualification gates. Plan approval and execution-method selection remain pending; no product changes are authorized yet. See the [related idea](./ideas.md#outcome-driven-reasoning-planning-and-continuous-execution).

**Milestone:** "I describe the result I want; OpenShip clarifies what matters, presents a practical plan, and carries it through without making me prompt every tool call."

**Problem:** The reported weather-chart request stops after `download_file` instead of continuing through analysis, generated Python, sandbox execution and chart delivery. Available tools, four packaged sandbox profiles and provider reasoning support do not by themselves provide reliable task orchestration. This phase extends the existing loop with explicit planning, progress and outcome verification; it is not merely a reasoning-model toggle or a saved-workflow compiler.

**Build on:** Phase 4's model/tool loop, gateway and conversation artifacts; 6A's durable runtime, NATS commands/events, ownership and cancellation; 6B's Python/artifact execution and explicit interrupted-execution resume choices; and 6C's prebuilt images for expanded profile selection. Preserve provider/connector pinning, current enablement policy, authorization and exact operation/artifact identities. No second scheduler, chat runtime or permission path.

**Build**

- Interpret the user's desired outcome and define observable completion criteria. Ask focused questions when missing information materially changes the answer; surface unsupported requests or missing capabilities/configuration instead of inventing them.
- Create a concise ordered plan in conversation/run context: steps, dependencies, relevant permitted tools/available skills, execution context and expected outputs. No required plan file, Git persistence, reusable JSON workflow revision or new skill-import/discovery subsystem. Phase 11 remains responsible for portable skills and progressive discovery.
- Present the plan and a concise approach explanation, not private model reasoning. Support **Copilot** (default: wait for the user's plan confirmation before executing) and **Auto** (show the plan, then execute without a separate plan-confirmation turn). Both retain clarification, cancellation, policy and operation-specific approval controls; Auto does not grant broader authority.
- Provide an Auto/Copilot selector in Chat UI with a server-side account-level preference restored across reloads/sign-ins/browsers. Capture the selected mode for each new task; later preference changes affect subsequent tasks only, never an active/waiting run or its pending confirmation. Copilot confirmation binds the exact task/plan revision and eligible actor, persists waiting state while releasing execution capacity, and resumes through authorized idempotent commands. Revisions invalidate prior confirmation; stale/repeated/ambiguous replies and selecting Auto cannot release another plan. Ordinary informational chat needs no task-plan confirmation. Establish this minimal contract without a circular dependency on Phase 8's later general durable decision nodes; confirmation is not cloud authority or mutation approval.
- Follow the current plan through a bounded **plan → act → observe → verify → continue** loop. Pass exact retained results/artifact references into dependent steps, show progress, and do not finish simply because one tool returned or the model emitted prose. Completion requires checking the requested deliverable, not just a successful download or zero exit code.
- Support agreed bounded self-repair after a known failed step: revisit relevant inputs/outputs/errors, reason about the failure and propose an implementation-level correction before another admitted attempt. Preserve the intended outcome, selected data and current authority; show repairs in progress and rerun normal validation. Stop on uncertain execution, missing authority, cancellation, no progress or exhausted budgets; never turn 6B's explicit interruption recovery into blind replay.
- Keep necessary plan changes visible. Do not silently skip, reorder or replace reviewed steps, change requested outputs or broaden authority. Material changes produce a revised plan and renewed confirmation in Copilot; Auto remains within the original request and existing authority. Define exact repair/retry budgets, allowed revisions and no-progress rules in the spec. A saved workflow's reviewed graph remains fixed unless a later authoring revision changes it.
- Implement the agreed shared internal abilities as typed runtime-controlled stages: `agent_reasoning` (interpret context/evidence and diagnose), `agent_planning` (plan/draft revisions), `agent_tracking` (verified step/attempt/evidence state) and `agent_switch` (validated next-action routing). These responsibilities are not four optional model-selected meta-tools or independent nested agents. Invoke reasoning again when observations/failures need interpretation, not solely before the first plan. Deterministic tracking/routing need no model call; logical decomposition must not force four inference calls per step. Keep model proposals separate from runtime-authorized transitions, with all inference/repairs sharing run budgets and existing gateway/operation records. Reuse these contracts in saved workflows and later declared branch/agent nodes without building a second workflow engine. Exact schemas remain subject to written-spec review; no implementation is claimed.
- Use one execution tool with a validated `profile_id` selector, not four wrappers. Make the four 6C contexts (`logs-data`, `iac-validation`, `standard`, `devops-shell`) understandable through purpose metadata and selectable where configured, enabled and ready. Bind selection to operator-approved runtime/dependency identities and sandbox compatibility; never accept model-chosen image names, build images from a tool call or imply that installed CLIs authorize cloud actions.
- Map each enabled profile to its image reference and dependency identity in an operator-owned YAML file, without a container registry. Extend `sandbox-images build PROFILE|--all` to ask interactively whether to write `deploy/sandbox/sandbox-profiles.yaml`, or write without prompting with explicit `--overwrite-profiles`. Update successfully built/validated profiles while preserving other existing entries/operator customizations; the flag authorizes this update, not whole-map regeneration or removal of unrelated profiles. Reuse validated local build records; do not add a separate export command or require manual tag discovery. Add `/deploy/sandbox/sandbox-profiles.yaml` to `.gitignore` because resolved image IDs are installation-specific, even when build-input tags match. Specify noninteractive behavior and custom-field ownership within rebuilt entries before implementation; never publish a failed/unverified build as a ready profile.
- Maintain each selectable profile's purpose metadata, task examples, installed-tool/library highlights and limitations in the tracked `deploy/sandbox/images.json` `managed_profiles` catalog. The build command projects metadata into the generated local YAML alongside each runtime binding; the runtime reads that mounted snapshot rather than needing another catalog mount. The model sees bounded descriptions of permitted ready profiles through the execution tool, not image/host details or a separately hardcoded prompt catalog. Exact package versions remain sourced from locks. Descriptions cannot authorize execution or override enforced runtime/policy controls. This metadata direction is agreed; exact schema/snapshot compatibility checks still need specification.
- In `docker-compose.yml`, use host-side `SANDBOX_PROFILES_FILE` to choose the bind-mount source, defaulting to `./deploy/sandbox/sandbox-profiles.yaml`. Mount read-only at fixed container path `/etc/openship/sandbox-profiles.yaml` for the API/runtime services that use it. A host override changes only the source, never the container lookup path. Prevent missing-source directory creation, give actionable enabled-sandbox setup errors and preserve sandbox-disabled startup. Define custom-host-path handling by the build command and container recreation after file replacement in the spec. Images must exist on the manager's serving engine; validate readiness and pin the resolved runtime identity for dispatch. A build-input fingerprint is not a registry manifest digest or proof of an image on another engine.
- Retain offline, credential-free generated-code execution and bounded maintained downloads. Stage exact downloaded artifacts into the selected sandbox and publish only declared, validated outputs through existing access controls. No sandbox internet access, runtime dependency installation, new shell API or credential handoff is implied.
- Stop with a truthful outcome on completion, clarification/approval/configuration need, unrecoverable failure, cancellation, or enforced model/tool/time/output/no-progress budgets. Preserve minimal plan/progress in existing run/checkpoint/event mechanisms for safe recovery; no mandatory plan-file artifact. Browser disconnect is not task completion. Restart/redelivery must not silently replay sandbox code or uncertain external operations.

**User demonstration:** Give one outcome-level request using a controlled valid weather JSON URL, with a fixture defining units, dates and grouping: "Draw a bar chart of average temperature in London from this data." Without subsequent programming instructions, OpenShip presents a plan, downloads/inspects the data, generates and runs suitable Python in `logs-data`, validates/retains the chart and computed values, and presents them with source and aggregation details. Demonstrate default Copilot with one plan confirmation—not per-step prompting—then Auto with no separate plan-confirmation turn; reload/reopen Chat UI to verify that the selected mode persists. Repeat with the deliberately non-existent example URL to demonstrate an honest fetch blocker, not fabricated data or a false completion.

**Exit gates**

- The complete weather-chart journey succeeds from one outcome-level request on at least one explicitly qualified provider/profile. Independently computed expected averages match the retained values; the image is a valid retained artifact with the intended labels, units and grouping. A download-only response fails acceptance.
- A second installed-tool task with a different input shape demonstrates that the agent coordinates steps rather than recognizing only a hardcoded weather recipe. Ambiguous aggregation or missing material inputs produce targeted clarification before the affected execution.
- Every exposed sandbox profile has accurate purpose/readiness metadata and a representative offline execution check. A charting request selects `logs-data` from its declared capabilities rather than a weather-specific routing rule. Model-visible summaries and actual registered profile IDs stay consistent; descriptions do not make unavailable libraries/runtimes supported. Disabled, unavailable, incompatible or unauthorized profiles are blocked; changing a profile cannot reuse an incompatible sandbox or broaden authority. Validated selector behavior and legacy runtime compatibility are tested.
- The local build → profile/image mapping → runtime selection path works without a registry or undocumented tag-discovery steps. Interactive consent/decline, `--overwrite-profiles`, partial builds and noninteractive invocation follow the specified write policy; failed builds cannot enable an unverified profile. The generated default YAML is ignored by Git. Default and custom host paths mount read-only at the same fixed container target; a missing source never becomes a directory, and sandbox-disabled startup still works. Malformed/unknown mappings, missing images, moved tags, changed dependency identities and wrong-engine/platform records cannot silently enable a different runtime. Configuration changes do not reinterpret the identity already pinned for an admitted execution.
- Missing/invalid URLs, malformed data, denied downloads, missing libraries, tool errors and invalid/missing outputs yield a bounded repair or actionable blocked/failed outcome. No invented tool IDs, substitute sources, dependencies, observations or success claims. Untrusted downloaded content cannot rewrite the plan or gateway policy.
- A known failed-step fixture demonstrates evidence-informed diagnosis, a bounded correction and successful revalidation without per-step user debugging prompts. Repeated ineffective repairs stop within the declared budgets. Uncertain/interrupted dispatch is not classified as an ordinary retryable failure. Reasoning/tracking/routing contracts reject unsupported next actions, fabricated completion and stale plan/step references; omitting a model-requested tracking/switch call cannot skip mandatory runtime progression.
- Progress and final results distinguish completed, pending and blocked work with evidence-linked artifacts. Repeated no-progress actions and budget exhaustion terminate visibly; cancellation prevents new dispatches. Plain chat and applicable earlier tool/artifact journeys remain intact.
- Disconnect/reconnect preserves truthful progress and authorized results. Worker restart, broker redelivery and explicit resume retain completed outputs without silently repeating dispatched code; applicable 6B A/B resume and operation-fencing regressions pass.
- With no saved selection, Copilot is the default. The Chat UI changes/restores the account-level value across reloads/sign-ins/browsers without cross-user leakage or false save success. Both modes pass the same outcome/policy tests; missing/invalid preference state cannot silently choose Auto. New tasks capture the selected mode, while existing active/waiting tasks retain theirs despite later preference changes.
- In Copilot, only the intended actor's response to the exact current plan can release execution; rejection, revision, cancellation, stale/repeated responses and restart cannot approve or duplicate work accidentally. Changing the mode preference is not a confirmation response. This remains separate from later concrete mutation approval.

**Not yet:** Saved reusable workflows (7B), freeform-to-typed workflow authoring (9), unrestricted replanning, unbounded autonomy, parallel/subagent orchestration (15), portable skill/package discovery (11), general durable approval nodes (8), cloud mutation authority (10), networked/credentialed generated code or connected Terraform (16–17). Internal plan/execution sequencing may use existing LangGraph facilities; these exclusions concern user-authored reusable graph features, not a ban on implementing the agent loop.

**Implementation-plan review:** Review the [plan](./superpowers/plans/2026-11-08-phase7a-agentic-planning-and-execution.md), including the spec's P1–P8 working defaults for budgets/expiry, noninteractive builds, mapping-field ownership/custom host resolution, sandbox-disabled empty-catalog bootstrap and initial qualification coverage. These defaults are not earlier user decisions. The agreed internal abilities, account-level Auto/Copilot preference captured for new tasks, exact durable confirmation, bounded repair, one profile selector, catalog-to-YAML metadata, partial-profile build updates and host-source/fixed-target mounts remain requirements. Confirm the plan's schemas/interfaces, migrations, activation/recovery and evidence gates, then select an execution method before implementation; this roadmap update is not execution authorization.

#### Phase 7B — Typed Reusable Workflow Execution

**Status:** Pending. Renamed from Phase 7 on 2026-11-08; saved-workflow scope is retained, with Phase 7A's agentic task foundation preceding it.

**Milestone:** "I can run the same saved process with different inputs and inspect each step."

**Build on:** Phase 7A's qualified tool coordination and outcome checks, the existing LangGraph engine, Phase 6A NATS command/event/response contracts with PostgreSQL ownership/checkpoints/outbox, Phase 4 gateway, and Phase 6B artifact/code operations. Reuse applicable capability/artifact/observation contracts, not an ad hoc prose plan as the executable definition. Saved workflows are another graph within this runtime, not another scheduler or execution service.

**Acceptance units:** **7B.1** runs one hand-authored, sequential, credential-free log-analysis workflow using the qualified Phase 6 parser, typed inputs, persisted step results, and a simple timeline. **7B.2** adds reusable forms, inspector detail, and the compatibility-aware compilation cache using that same revision format. A cache is an optimization, never the only source of an executable definition.

**Build**

- Immutable, schema-versioned JSON workflow revisions and dependency lockfiles. Record compiler/runtime compatibility and the graph kind/version used by each run, preserving existing chat-run records.
- Typed inputs, outputs, restricted bindings, and maintained node factories.
- Initially sequential tool, bounded model, and artifact steps. Persist logical step/invocation IDs separately from attempts; unsupported node kinds are rejected until their phase is qualified.
- Compatibility-aware compilation cache.
- Run history, step inspector, and schema-driven input forms.

**User demonstration:** Run a saved "read uploaded logs → execute the Phase 6 parser → save report artifact" workflow twice with different inputs. Cloud collection is an optional extension using Phase 5, not a prerequisite hidden in this demonstration.

**Exit gates**

- Repeated runs use the same revision and graph structure.
- Invalid references and incompatible dependencies fail before execution.
- Inputs and outputs are visible for every step.
- Restart recovery works for the supported credential-free/read-only steps.
- Cache hits still recheck current policy and connector readiness.
- Workflow starts, cancellation, and recovery use the same authorized commands and durable event projections as chat. Conversation/run references remain unambiguous; new run kinds do not overwrite another run's messages or active state.
- A revision/compiler upgrade preserves compatible saved workflows and resumable runs, or blocks incompatible ones visibly without reinterpreting their definitions. Old chat history remains readable.

**Not yet:** Natural-language workflow generation or complex composition.

**Clarification:** Deterministic compilation means the same specification produces the same graph—not that model answers or live external results are identical.

#### Phase 8 — Durable Inputs and Approval Decisions

**Milestone:** "A workflow can wait for me overnight, survive a restart, and resume only the correct request."

**Build on:** Phase 7B's versioned node factories, step identities, and runtime on 6A messaging. Persist waiting states and release the broker delivery/execution slot; an authorized input/approval command resumes the exact recorded wait. Retain existing run status/event meanings, including completed chat runs; a reply inbox or unacknowledged message is not the storage for an overnight approval.

**Acceptance units:** **8A** pauses for a typed input and resumes through one validated command. **8B** adds digest-bound approval/rejection using the same request identity, eligibility, and atomic consumption mechanism. The first eligible actor can be the project owner; team roles expand this policy in Phase 20.

**Build**

- Persisted input and approval requests.
- Browser cards and conversational responses using the same validated commands.
- Actor eligibility, request identity, revision binding, digest binding, and expiry.
- Separate approval nodes from side-effecting nodes.
- Cancellation and cleanup while waiting.

**User demonstration:** Pause a workflow for a decision, restart the installation, then approve or reject it.

**Exit gates**

- Pending requests survive three restart scenarios.
- Stale, repeated, expired, and unauthorized replies cannot resume work.
- An ambiguous "yes" does not approve an unidentified request.
- Changing reviewed inputs or content invalidates approval.
- Waiting does not require a live sandbox or browser connection.
- Concurrent/repeated replies consume a request at most once and publish its decision consistently. Restart/lease turnover cannot execute the adjacent step twice merely because an interrupt node re-enters.
- Cancellation or expiry closes the request and releases execution resources; a delayed reply cannot reopen it.

**Not yet:** A workflow-definition review automatically authorizing future mutations.

#### Phase 9 — Freeform Workflow Standardization

**Milestone:** "I describe a process, review OpenShip's formal interpretation, save it, and run it later."

**Build on:** The Phase 7B executable definition/compiler, Phase 8 wait nodes, installed capability IDs, and Phase 5 staging reads. Phase 7A's ad hoc task planning is a precursor, not a replacement for reviewed reusable definitions. This phase adds an authoring path to the existing workflow service. It does not generate a second execution format or orchestration code.

**Start small:** Author only the sequential and wait nodes already supported. Use one maintained staging-investigation example, then expand the acceptance corpus. Native model structured output is optional; every emitted specification must pass the same server-side schema and capability validation.

**Build**

- Conversation/Markdown → typed specification → validation → human review.
- Refinement and versioned reviewed JSON.
- Human-readable process and graph rendered from the executable definition.
- Capability resolution against the installed catalog.
- Separate authoring review from execution authorization.

**User demonstration:** Describe a staging-health investigation workflow, refine its inputs, save it, and run it twice.

**Exit gates**

- A fixed set of ten representative descriptions produces valid workflows or actionable clarification requests.
- Unsupported operations cannot silently invent tool IDs.
- Editing source or specification creates a new revision.
- A reviewed workflow runs from saved JSON without another standardization call.
- Missing permissions or connectors remain execution blockers despite definition review.
- Manually authored and model-authored versions of the same specification compile and execute through identical factories, policy checks, and revision storage. A correction creates a revision; it cannot mutate an active run.

**Not yet:** Broad autonomous repair or all graph node types.

**Release checkpoint:** A usable workflow-authoring alpha.

#### Phase 10 — First Controlled External Mutation

**Milestone:** "OpenShip proposes one real change, waits for my approval, applies it, and verifies the result."

**Build on:** Phase 4 operation identity/fencing, Phase 5 connector/credential pinning and observed target identities, and Phase 8 decisions. Extend the operation lifecycle with mutation-specific dispatch uncertainty, reconciliation, and conflict control before enabling one staging write. Concrete mutation targets remain part of each approval; they do not require Phase 5 resource registration.

**Start small:** One operation on one explicit resource through a maintained connector. Reversing it is a new authorized operation; recovery never assumes an automatic rollback.

**Acceptance units:** **10A** delivers the first concrete approved mutation and its reconciliation/verification path. **10B** builds on 10A and Phase 8 decisions to generalize approved, temporary use of a predefined maintenance role. The first mutation already requires concrete action approval and valid cloud authority; 10B does not defer those controls.

**Build**

- One narrow, reversible staging operation through a maintained connector.
- Concrete target/change preview and digest-bound approval.
- Stable operation keys, mutation locks, reconciliation, and uncertain-outcome states.
- Fresh policy checks before dispatch.
- Post-change verification and partial-outcome reporting.

**User demonstration:** Change a staging service's replica count through an approved workflow.

**Exit gates**

- Rejection produces no mutation.
- Changing the target or arguments invalidates approval.
- Crash injection before dispatch, after dispatch, and before result persistence causes reconciliation—not blind redispatch.
- Unsupported idempotency guarantees are surfaced honestly.
- Cancellation distinguishes prevented, completed, and uncertain effects.
- Verification checks observed state rather than trusting the agent's summary.
- Ad hoc chat and saved workflows use the same mutation admission and approval checks. A model cannot bypass them by changing how it invokes the operation.
- Conflict keys identify the actual external target, so two conversations or connector bindings for the same resource cannot evade serialization.
- Restoring a pre-dispatch backup does not blindly repeat a possibly completed write; recovery starts in reconciliation mode before mutation dispatch is re-enabled.

**Not yet:** Production autonomy, arbitrary cloud writes, or automatic rollback promises.

**Release checkpoint:** The architecture's investigation → proposal → approval → change → verification scenario works end to end.

##### Phase 10B — Approved Temporary Maintenance Authority

**Agreed direction (2026-10-21):** Configure two AWS role bindings once, rather than changing IAM permissions during normal workflow or conversation execution:

- **Basic role:** Preauthorized low-risk read operations.
- **Maintenance role:** Optional, administrator-created role covering a predefined set of changes. It is not automatically an administrator role or unrestricted read/write access. Its use requires approval of the concrete action and temporary grant.

**Milestone:** "When a task needs configured maintenance authority, I can approve its temporary use without editing AWS IAM for every run."

**Build**

- Persist an access request bound to the execution identity, initiator, project/environment, connector/role revision, exact target/actions, operation/input digest, eligible approver and validity window. Reuse Phase 8 decision identity/atomic consumption and the existing gateway/credential broker; do not introduce another permission or execution path.
- After valid approval, obtain temporary credentials for the already configured role. Where supported, a session policy further restricts the role's authority; it cannot add permissions the role lacks. The model cannot choose arbitrary role ARNs, retrieve credentials or change IAM policy.
- Recheck current grant, actor eligibility, connector scope, cancellation, run ownership and exact action approval before each privileged dispatch. Ordinary low-risk reads continue without repeated approval. A grant is not conversation-wide permission to perform any action allowed by the role.
- Missing maintenance configuration or insufficient predefined permissions blocks only the affected task and gives an authorized administrator a configuration action. The conversation remains usable. Configuration changes neither approve nor silently replay the action.
- Close grants on completion, cancellation, expiry or revocation, stop new dispatches and preserve audit/recovery evidence. Credential renewal cannot extend the approved authority window. Use short sessions and document provider-specific limits on invalidating already-issued credentials.

**User demonstration:** Run a supported change covered by the configured maintenance role; approve the exact action, observe temporary role use and verification, then demonstrate rejection, expiry and a missing-role blocker without editing IAM in the normal success path.

**Additional exit gates**

- Basic credentials cannot perform the representative maintenance action. Maintenance credentials are never issued merely because the model requested them or the connector exists.
- Changed targets/actions/role revisions, stale or repeated approvals, unauthorized approvers and expired grants fail closed. Concurrent replies consume one decision; restart/restore preserves the grant identity without blind redispatch.
- Approval of role use and approval of the operation have distinct meanings, even if one review screen binds and approves both. Additional actions require their own admitted authority and applicable approval.
- Post-expiry/revocation dispatch is denied, and an already-dispatched or uncertain effect follows 10A reconciliation. The UI does not claim that closing a local grant instantly revokes every cloud token or undoes an effect.
- Team deployments qualify approver eligibility and membership revocation through Phase 20A; owner-only operation does not silently authorize a multi-user deployment.

**Security limitation:** If OpenShip's trusted broker can assume the maintenance role at any time, application approval gates provide approval-gated temporary access—not a zero-standing-privilege guarantee against compromise of that broker. An optional independently controlled access broker, such as Apono, can enforce a stronger grant boundary later. It is not a required dependency per cloud provider, and each enabled environment still requires explicit trust/configuration and qualification.

**Not yet:** Dynamic IAM role/policy creation, arbitrary privilege escalation, an Apono-equivalent permission-management platform or mandatory third-party access services. These remain separate proposals justified by a real deployment.

### Wave D — Capabilities and Reactive Automation

#### Phase 11 — Discovery, Skills, and Package Lifecycle

**Milestone:** "OpenShip finds relevant permitted capabilities without loading the entire catalog."

**Build on:** Phase 4 capability identity, exact lookup, enablement, and gateway checks, plus Phase 7B workflow revisions/lockfiles. Phase 7A plans initially use the small installed permitted catalog; this phase expands discovery and skills through that same boundary. Discovery changes selection; package installation changes what is available. Neither changes execution authority.

**Acceptance units**

- **11A:** Access-filtered lexical discovery and bounded metadata/schema loading over a small maintained catalog; exact IDs remain authoritative.
- **11B:** One portable investigation skill with provenance and on-demand references, using the same capability selection and execution rules.
- **11C:** Local embeddings and semantic ranking as a rebuildable enhancement to 11A, qualified against the same retrieval corpus.
- **11D:** One private package import with namespace, dependency/compatibility validation, explicit enablement, and revocation. Expand package types after this complete path works.

**Build**

- Lexical search, local embeddings, semantic ranking, and progressive loading.
- Portable skills with provenance.
- Private/imported namespaces, version pinning, compatibility checks, enablement, and revocation.
- Contributor manifests, examples, and contract tests.

**User demonstration:** Add a private investigation skill and packaged tool without changing the agent loop, then discover and use them.

**Exit gates**

- A benchmark of twenty requests retrieves relevant permitted capabilities within a configured result/context limit.
- Exact IDs resolve independently of semantic ranking.
- Embedding failure preserves lexical discovery.
- Private metadata does not leak across projects.
- Imported instructions cannot self-authorize tools.
- Revocation blocks new calls without silently replacing pinned versions.
- Updates leave active runs pinned to their recorded digests; unavailable pinned content blocks visibly. Importing a package cannot load its executable code into the trusted API/runtime process.

**Not yet:** A hosted marketplace or automatic installation from search results.

#### Phase 12 — MCP Integration

**Milestone:** "A configured MCP tool behaves like other OpenShip tools, including policy and audit."

**Build on:** The Phase 4 gateway/tool-result contract, Phase 5 credential broker, and Phase 11 registry. Phase 6 qualifies the sandbox runtime for local executable servers; remote MCP endpoints remain separately scoped external destinations. MCP is a capability adapter, not a model-specific execution path or another permission system.

**Acceptance units:** **12A** admits one configured local stdio server inside the installed-tool sandbox. **12B** admits one configured remote server with scoped authentication and destination checks. Qualify each transport separately before advertising both.

**Build**

- Local stdio MCP servers inside installed-tool sandboxes.
- Explicitly configured remote MCP endpoints.
- Namespaced tool IDs, schema snapshots, resource references, and bounded results.
- Credential isolation, endpoint allowlists, redirect validation, and SSRF protection.

**User demonstration:** Use one local and one remote MCP tool from conversation and a saved workflow.

**Exit gates**

- Both paths pass through gateway authorization and operation recording.
- Tool-name collisions cannot select another binding.
- Schema changes trigger revalidation.
- Server prompts/resources cannot change harness policy.
- Unapproved destinations are blocked.
- Remote implementation-version limitations are visible.
- MCP calls appear in the existing step/operation/event inspector and share cancellation, output limits, and artifact access checks. Server-supplied references cannot fetch arbitrary destinations or another project's artifacts.

**Not yet:** Trusting a remote server's implementation merely because its schema digest is unchanged.

#### Phase 13 — Webhooks and Scheduled Workflows

**Milestone:** "A webhook or schedule starts a workflow without an open browser."

**Build on:** Existing authorized workflow-start commands, Phase 6A NATS transport/outbox, and Phase 8 waiting decisions. Introduce one external-event inbox for all later sources; persist authenticated admission before publishing execution commands. The inbox records admission while the existing runtime records execution. SSE remains a projection of run state, not an ingestion queue.

**Acceptance units:** **13A** starts one saved, credential-free/read-only report workflow from a signed webhook. **13B** adds one durable schedule using the same admission path. Freeform authoring in Phase 9 is optional for these hand-authored revisions. Enabling a mutating trigger additionally requires Phase 10 and its normal approval/authority checks.

**Build**

- Durable event inbox and normalized event envelope.
- Authenticated webhook endpoints.
- Scoped trigger bindings to explicit workflow revisions, an authorized initiator, project/workspace association, permitted inputs/targets, and bounded concurrency. Capture provider/dependency configuration when admitting each run, as for manual starts.
- Restricted input mapping, deduplication, retries, failure inspection, and controlled replay.
- Durable schedules with timezone and missed-run policies.

**User demonstration:** Send a signed webhook to start an investigation report; schedule the same report.

**Exit gates**

- Ten deliveries of the same source event create one run per intended trigger binding within the declared deduplication window.
- Invalid signatures and cross-project routing are rejected.
- Accepted events survive process restart.
- Schedule catch-up follows its configured policy.
- Trigger enablement has explicit authority; it does not bypass approvals.
- Disabling a trigger prevents new admissions.
- Event acceptance, deduplication, and run admission recover consistently across crashes. Source IDs are scoped to their binding; replay is explicit and audited, and deduplication retention covers the declared redelivery window.
- Webhook size/rate/backlog limits protect manual work. Remote exposure requires TLS and source authentication at this phase; Phase 20 is not a prerequisite for enforcing those controls.
- Missing/revoked initiator authority or an unavailable pinned workflow blocks admission; untrusted payloads cannot select another project, target, profile, or executable revision outside the binding.

**Not yet:** Exactly-once guarantees for arbitrary external side effects.

#### Phase 14 — Cloud Alarms and Message Brokers

**Milestone:** "A real alert initiates an investigation and delivers an evidence-based report."

**Build on:** Phase 13's envelope, inbox, trigger binding, and admission policy, Phase 6A NATS command/event delivery, and Phase 5 cloud reads. Source adapters authenticate and normalize events; they do not create another workflow queue or scheduler. This phase qualifies external broker sources, not the internal NATS broker already established in 6A.

**Build**

- One cloud-monitoring adapter.
- One external broker adapter, selected from actual user infrastructure, forwarding authorized admissions into the internal NATS path. Its source may be RabbitMQ, Kafka, a cloud queue, or another qualified transport without replacing NATS.
- Source-specific authentication and envelope validation.
- Durable acknowledgement/cursor handling, backpressure, retry/dead-letter behavior, and reconciliation.
- Report artifacts and run links visible in the browser. If outbound notification is included, start with one explicitly authorized destination and a recorded delivery operation; Phase 19 reuses this contract for additional channels.

**User demonstration:** Generate a staging alarm, investigate automatically, and inspect the report.

**Exit gates**

- Alarm, accepted event, workflow run, and report are traceable end to end.
- Duplicate delivery and consumer restart do not create unintended duplicate admissions.
- Poison messages are isolated without blocking the stream.
- A supported missed-event scenario is recovered through reconciliation.
- Investigation stays within preauthorized reads; remediation still uses normal gates.
- The source contract declares ordering/redelivery assumptions and when acknowledgement is safe. Inbox retention, cursor recovery, and backpressure are tested against that contract.
- Notification retries preserve the delivery identity and expose uncertain outcomes rather than promising exactly-once delivery or sending to payload-selected recipients.

**Not yet:** Every broker or provider.

**Acceptance units:** **14A** qualifies one alarm source and a read-only investigation. **14B** adds one broker from the user's infrastructure through the same inbox/admission path. Additional sources require their own qualification rather than a new ingestion service.

### Wave E — Full Workflow and Infrastructure Capabilities

#### Phase 15 — Bounded Workflow Composition

**Milestone:** "A reusable workflow handles conditions, parallel work, and reusable child processes."

**Build on:** Phase 7B's executable schema, compiler, step identities, and existing runtime; Phase 8 wait/decision nodes; Phase 10 operation recovery. Add supported node types with explicit schema/runtime compatibility. Sequential workflows stay valid and use the same factories and scheduler. Bounded agent nodes reuse Phase 7A's planning/verification behavior within their declared inputs, outputs, authority and budgets, never to rewrite the surrounding reviewed graph.

Deliver as separate increments:

1. **15A:** One conditional branch and bounded loop, with explicit iteration identities.
2. **15B:** Bounded parallel branches and a join, with branch-specific step identities and recovery.
3. **15C:** One child-workflow pattern, followed by a bounded agent-step pattern reusing Phase 4's model/tool loop and applicable Phase 7A reasoning/planning/tracking/routing contracts. A model-mediated branch proposes only a declared successor; the runtime validates the typed decision and current authority. Deterministic branch conditions need no reasoning call, and neither form can rewrite the reviewed graph.

**User demonstration:** Check several services, investigate unhealthy ones in parallel, then combine results into one report.

**Exit gates**

- Graph validation rejects invalid references and unbounded execution.
- Inspector views distinguish branches, loop iterations, and attempts.
- Child runs inherit project context and no broader authority.
- Concurrency, nesting, token, and step budgets are enforced.
- Resume/cancellation works at each newly supported boundary.
- Dynamic requests for new authority cannot bypass review.
- Root-run budgets bound descendants as well as individual steps. Parallel retries and child restarts retain logical operation keys and cannot duplicate a mutation through a new branch/attempt identity.
- Earlier sequential/wait workflows resume correctly after the extension, or receive an explicit compatibility block; no automatic recompile changes the meaning of an active run.

**Not yet:** Arbitrary model-generated orchestration code.

#### Phase 16 — Git-Backed Documents and Terraform Planning

**Milestone:** "My requirements become inspectable infrastructure code and a plan, without creating infrastructure."

**Build on:** Existing requirement/code artifacts, workflow revisions, gateway/approval/operation controls, Phase 5 credential and target bindings, and Phase 6 sandbox profiles. A Terraform workflow is a maintained workflow/tool profile, not a new infrastructure execution engine.

**Acceptance units:** **16A** generates and validates files for one small stack as local artifacts. **16B** exports a selected, reviewed file set to Git through the existing publication gate. **16C** produces and inspects one saved plan using a separately authorized credentialed CLI profile and protected backend. Git publication and cloud planning require separate authority.

**Build**

- Requirements/design artifacts and infrastructure workflow templates.
- Generated Terraform code with pinned tool profiles.
- Policy-controlled Git export or pull-request creation.
- Credentialed CLI profile with explicit bundle, target, scope, and duration authorization.
- Protected Terraform backend configuration and binding to the account/environment, backend, workspace, and management ownership. Plan/state sensitivity, authorized artifact access, and backend recovery are established before a real plan is stored.
- Validation and saved-plan inspection.

**User demonstration:** Describe a small staging stack, review its generated files, and inspect the resulting Terraform plan.

**Exit gates**

- Git contains only approved, non-secret material.
- Credentials and Terraform state are never committed.
- The plan identifies account, region, backend, workspace, changes, and artifact digest.
- Failed validation or inadequate credential scope prevents planning.
- No apply occurs through the plan-only workflow.
- Git publication itself follows external-publication policy.
- The authorized bundle pins source files, providers/modules/dependencies, executable profile, targets, and credential scope. Configuration review or a plan-only command is not assumed to restrict arbitrary credential-bearing code.
- Saved binary plans and their rendered views obey sensitivity/access policy; neither state nor secret-bearing plan content is copied into chat, public artifacts, or Git.

**Not yet:** Applying infrastructure.

#### Phase 17 — Exact-Plan Apply and Resource Bindings

**Milestone:** "The exact plan I approved creates resources that OpenShip can locate later."

**Build on:** Phase 16's saved plan/backend binding, Phase 8 digest-bound decisions, Phase 10 mutation ledger/locks/reconciler, and Phase 5 resource identities. Extend existing bindings with provisioning provenance and confirmed outputs; do not create a parallel resource inventory or apply scheduler.

**Start small:** Apply the one stack qualified in Phase 16, inspect its confirmed resources, and recover one interrupted apply before expanding supported stacks.

**Build**

- Digest-bound approval and exact saved-plan apply.
- Terraform locking plus OpenShip conflict controls.
- Provisioning relationships between existing resource objects, concrete bindings, the approved desired revision/plan, and confirmed apply observations.
- Post-apply observations and lifecycle follow-up.
- Partial-failure and uncertain-outcome reconciliation.

**User demonstration:** Provision a small staging stack, then ask OpenShip to inspect "the deployment created earlier."

**Exit gates**

- Only the reviewed plan is applied.
- A stale plan requires regeneration and new review.
- Conflicting applies are rejected or serialized safely.
- Created resources have confirmed bindings and observations.
- Partial failures report known and uncertain effects.
- State remains in its protected backend; cancellation does not claim to undo completed infrastructure.
- Approval covers the exact plan artifact, source/dependency bundle, target/backend/workspace, credential scope, and validity conditions. Replanning never reuses an old approval.
- A crash or backup restore after dispatch enters the existing reconciliation path before another apply. Terraform/backend locks and OpenShip target locks remain complementary.

**Not yet:** Generalized autonomous infrastructure repair.

#### Phase 18 — Resource Intelligence and Multi-Cloud Coverage

**Milestone:** "OpenShip recognizes drift, explains it, and supports the same core journeys on additional platforms."

**Build on:** Phase 5 scoped resource/observation records and connector contracts, Phase 16 desired revisions/backend ownership, and Phase 17 confirmed provisioning bindings. Drift and extra clouds extend these records and adapters; they do not redefine what a resource is. Advanced graph composition is required only for workflows that actually use it.

Split into independently accepted increments:

- **18A:** Start with report-only drift for the one provisioned stack; then add confirmed mapping reconstruction and reviewed adopt/reconcile actions through existing authoring/mutation gates.
- **18B:** A second cloud's read/investigate/change journey.
- **18C:** A third cloud's equivalent journey.
- **18D:** Expand the library of versioned workflows already introduced in Phase 7B, using the same schema, lockfiles, and provider/connector qualification rules.

**User demonstration:** Change a managed resource outside OpenShip, detect drift, then choose report, adopt, or reconcile.

**Exit gates**

- Desired configuration, management state, and live observations are distinct.
- Missing resources and uncertain ownership are not guessed away.
- Terraform and direct APIs cannot accidentally become competing managers.
- Each added platform passes the common authorization/recovery tests.
- Built-in workflows declare supported platforms and prerequisites.
- No unsupported cross-cloud equivalence is implied.
- Each additional connector passes the same read, credential-scope, mutation, approval, and recovery contracts for its declared operations. A platform missing an operation is explicitly unsupported rather than emulated with unrestricted shell access.

**Not yet:** Every cloud service or a universal infrastructure abstraction.

### Wave F — Complete Interfaces and Operational Deployment

#### Phase 19 — Additional Trigger and Client Adapters

**Milestone:** "The same workflows can be invoked through the channels my team uses."

**Build on:** Phase 13's inbox/admission contract, Phase 8 decision commands, Phase 6A NATS routing/correlated responses, and the current identity/gateway/event APIs. Reuse Phase 14's delivery records if outbound notifications are enabled. A bot or watcher is a client/source adapter, never a second agent runtime. Retain authenticated source/channel/recipient correlation so progress and results return to the intended source or authorized browser; a missing live reply subject falls back to durable status, not duplicate execution.

**Start small:** One channel supports start/status for one read-only workflow; add approvals and notifications only after identity/recipient checks pass. Team use requires Phase 20A membership/role qualification. Resource-state sources additionally require the relevant Phase 5/18 observations; broker-backed sources require their Phase 14 transport qualification.

Add one adapter at a time:

- Slack commands, approvals, and notifications.
- Telegram where required by the support matrix.
- Email webhook or polling.
- Git/file-change events.
- Resource-state-change events.

**User demonstration:** Start an investigation from chat or email, then continue observing it in the browser.

**Exit gates for each adapter**

- External identities map to an authorized installation/project context.
- The adapter uses existing commands, trigger policy, and events.
- Duplicate delivery and restart behavior pass the shared event tests.
- Untrusted message content cannot grant execution authority.
- Notifications respect sensitivity and recipient policy.
- File watchers cannot escape configured roots.
- External identities are explicitly linked to existing actors; channel names, email addresses, message text, or a bare "yes" cannot grant authority. Approval decisions retain the original request/run/digest binding.
- Every new adapter passes the same admission/reconnect/replay tests. Adding a Slack client does not require an unrelated broker or a third cloud to be complete.

**Not yet:** Separate privileged execution paths for bots or integrations.

#### Phase 20 — Team Operations and Remote Deployment Qualification

**Milestone:** "A team can run OpenShip remotely, recover it, and upgrade it without losing control of work."

This phase **qualifies** controls developed earlier; it is not when security, metrics, or cleanup first appear.

**Build on:** Existing authorization context, authenticated worker protocol, scoped credential/artifact access, mutation reconciliation, and each component's recovery/upgrade procedures. Begin qualification after the first complete Phase 10 journey using only the enabled support matrix. Do not wait for all Phase 11–19 expansions; rerun the relevant qualification gates when those are enabled.

Deliver in separate qualification gates:

1. **20A:** Two users with distinct roles on one remote Linux host; qualify TLS, project membership, connector/approval eligibility, and the existing authenticated worker protocol.
2. **20B:** Coordinated backup/restore and uncertain-operation recovery for that installation. Introduce a shared artifact backend behind the Phase 4 interface only if separating components across hosts; validate it before enabling that deployment mode.
3. **20C:** Upgrade/drain compatibility and operational soak testing over the enabled features.

**Build**

- Membership and viewer/operator/approver/admin roles extending the existing authorization context, with permission-change/revocation checks at every boundary.
- Qualification of authenticated workers, TLS, private endpoints, and remote deployment runbooks.
- Coordinated PostgreSQL/JetStream/artifact/configuration/key recovery, including consumer positions, retained commands, and outbox reconciliation.
- Restore reconciliation mode and upgrade procedures.
- Audit export, quotas, retention, dashboards, and operational alerts.

**User demonstration:** Two team members run a remote approval workflow, restore a backup with pending work, and upgrade the installation while inspecting recovery outcomes.

**Exit gates**

- Two users with different roles complete a realistic approval workflow.
- A representative twenty-run soak has no unexplained lost or duplicate transitions.
- Restore does not blindly resume pending mutations.
- Upgrade preserves or explicitly blocks incompatible resumptions.
- Expired grants, abandoned sandboxes, and retained artifacts follow documented policies.
- Recovery duration and data loss are measured against agreed deployment targets.
- Qualify the selected broker availability profile, disk/retention limits, service permissions, failure inspection, and worker drain. An old inbox or replayed command cannot repeat a previously dispatched mutation after restore; broker transport health and run outcome are measured separately.
- An owner-only deployment upgrades without reassigning existing projects, approvals, runs, or connector authority. Role/membership revocation blocks subsequent privileged dispatch and decisions.
- API, runtime worker, and sandbox worker version mismatches fail safely or use a declared compatible protocol; upgrades do not reinterpret in-flight workflow or execution-request versions.

**Not yet:** Kubernetes qualification or availability guarantees beyond the tested remote deployment profile.

**Release checkpoint:** Operationally qualified self-hosted team release on supported Linux infrastructure.

#### Phase 21 — Kubernetes Deployment and Scaling

**Milestone:** "The same qualified product installs through Helm and remains correct through pod replacement and scaling."

**Build on:** The Phase 20 qualified distribution, Phase 6A NATS subjects/consumers and PostgreSQL ownership/outbox, existing worker protocols/fencing, storage interfaces, configuration, and operation locks. Kubernetes packages and scales the same processes; it does not introduce another workflow service, broker, or privileged execution path.

Split into:

- **21A:** One supported cluster/runtime combination with a minimal replica count and explicitly durable PostgreSQL/JetStream/artifact storage; demonstrate restart/replacement before scaling. Declare whether NATS is managed externally or deployed with persistent volumes; qualify stream replication/quorum against the advertised failure tolerance.
- **21B:** Multi-worker scaling, rolling upgrade, and failure qualification. Before placing replicas on different nodes, qualify a shared artifact backend and the deployment's database connection/concurrency limits.

**Build**

- Helm packaging, probes, resource requests/limits, and controlled upgrades.
- External or appropriately managed PostgreSQL, NATS JetStream, and artifact storage, with pinned server/client versions and explicit stream/consumer configuration.
- Restricted service accounts and enforced network policies.
- Compatible sandbox `RuntimeClass`/worker-node configuration.
- Graceful draining, disruption behavior, and capacity testing.

**User demonstration:** Install on a supported cluster, run an approval workflow, replace pods, and scale workers.

**Exit gates**

- The full reference workflow works after pod replacement and rolling upgrade.
- Additional workers do not violate run ownership or mutation serialization.
- API replicas receive independent events, worker replicas share execution deliveries, and rolling broker/worker upgrades preserve consumer recovery and fences. Broker replicas use separate persistent storage and the declared failure domains.
- Network and sandbox isolation tests run against the real cluster configuration.
- Application pods and generated-code sandboxes remain unprivileged; runtime administration is confined to the designated worker infrastructure.
- Missing isolation support blocks executable capabilities.
- Measured throughput and queue delay improve under the agreed workload.
- The same workflow artifacts, IDs, provider profiles, approval commands, and recovery semantics remain usable across Compose and Kubernetes qualification; any required storage/configuration migration is explicit and tested.

**Architecture clarification:** Use supported Pod Security Admission/admission controls, not removed PodSecurityPolicy APIs.

**Not yet:** Untested cluster/runtime combinations or stronger isolation providers outside the declared support matrix.

**Release checkpoint:** The enabled support matrix is qualified on the declared Kubernetes deployment. Qualifying an early subset does not mark unfinished capability phases complete. Full architecture-scope completion additionally requires acceptance of all agreed capability phases and their applicable deployment checks.

## 3. User Sign-Off at Every Phase

A phase is **ready for acceptance** only when its evidence package includes:

| Evidence | What the user receives |
| --- | --- |
| Runnable increment | Versioned build and documented setup |
| Demonstration | Exact steps, inputs, and expected observable results |
| Acceptance results | Pass/fail results against the agreed phase checks |
| Continuity | Earlier accepted user journeys still pass; new behavior is exercised through the shared runtime/contracts |
| Foundation and migration | First working consumer of each new boundary; schema/protocol/revision compatibility and removal of any superseded path |
| Failure evidence | Restart, denial, timeout, or cancellation scenarios appropriate to the phase |
| Measurement | Relevant latency, usage, recovery, throughput, or retrieval-quality results |
| Limitations | Explicit unsupported behaviors and known issues |
| Decision | Accept, revise, or reject |

A passing test suite does not automatically mean the phase is done. **The user accepts the milestone after observing that it solves the intended problem.**

If a phase proves too large, divide it at a point that still produces a usable increment. Do not turn "database tables finished" or "worker interface finished" into a product milestone.

## 4. Important Boundaries Across the Roadmap

These distinctions should remain explicit throughout implementation:

- **Workflow review is not mutation approval.**
- **An ad hoc task plan is not a saved executable workflow, and plan confirmation is not new execution authority.**
- **Continuous agentic execution is bounded by the agreed plan, observed results, cancellation, policy and budgets—not repeated “continue” prompts or an infinite loop.**
- **Trigger configuration is not unrestricted execution authority.**
- **Deterministic graphs do not imply deterministic model or cloud results.**
- **Event deduplication does not guarantee exactly-once external effects.**
- **Sandbox isolation does not compensate for overpowered cloud credentials.**
- **Git history does not roll back infrastructure.**
- **"Read-only" is enforced by backend authority, not a tool's label.**
- **A predefined maintenance role is configured once; per-action approval unlocks its temporary use, not new IAM permissions or unrestricted conversation-wide authority.**
- **An agent workload identity does not replace the human initiator/approver in the audit trail.**

NATS with JetStream is the selected internal messaging foundation from Phase 6A. Stronger isolation providers, additional external broker adapters, a public marketplace, and alternative orchestration/storage systems remain follow-on choices justified by demonstrated needs—not prerequisites for this roadmap.

**Next commitment:** Record the remaining Phase 6C live dev-stack/chart evidence and user acceptance separately from its completed image implementation/local validation. In parallel, refine and review the Phase 7A spec, then its implementation plan, for outcome-driven planning and continuous bounded execution; no product implementation is authorized by this roadmap update. Phase 7B retains saved typed workflows. Preserve Phases 1–6 and existing application controls, including 6B's provider-owned 30-minute lifetime, scoped reuse and explicit A/B resume; do not reintroduce OpenShip-owned lifecycle management, mutable-file scope or a qualification platform as prerequisites. The Phase 10B maintenance-authority boundary remains unchanged.
