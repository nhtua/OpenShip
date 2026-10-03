---
type: roadmap
summary: Dependency-ordered delivery phases for OpenShip, with runnable milestones, measurable acceptance gates, and user sign-off.
status: proposed
date: 2026-10-02
---

# OpenShip Delivery Roadmap

Build OpenShip through **small, end-to-end milestones**, rather than finishing entire backend subsystems before users can try them.

This roadmap is based on the updated [OpenShip Harness Architecture](./architects.md) in PR #18, at architecture revision `9fe9f37`. **All phases below are proposals, not completed work.**

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

## 2. Roadmap Overview

**Size:** S = focused increment; M = new boundary or several closely related behaviors. Broader phases are explicitly divided into independently accepted increments.

Dependencies identify prerequisites; they do not require every preceding phase to be complete.

| Phase | Working milestone | Dependencies | Size |
| --- | --- | --- | --- |
| 1 | Authenticated browser chat runs locally | — | S |
| 2 | Conversations and run progress survive reconnects | 1 | M |
| 3 | The same workspace works across model providers | 2 | S per adapter |
| 4 | One tool executes through an enforced sandbox boundary | 2 | M |
| 5 | OpenShip inspects one real cloud environment safely | 4 | S–M |
| 6 | Generated code produces controlled artifacts | 4 | S–M |
| 7 | A saved, typed workflow can be run repeatedly | 2, 4 | M |
| 8 | Workflows wait for input or approval across restarts | 7 | S–M |
| 9 | Conversation becomes a reviewed, reusable workflow | 7, 8 | M |
| 10 | One real external change is approved, applied, and verified | 5, 8, 9 | M |
| 11 | Relevant tools and skills are discovered progressively | 5, 7, 9 | M |
| 12 | Local and remote MCP tools use the same policy boundary | 4, 5, 11 | M |
| 13 | A webhook or schedule starts a durable workflow | 8, 9, 10 | M |
| 14 | A cloud alarm or broker message drives investigation | 5, 13 | S–M per adapter |
| 15 | Workflows support bounded branching and composition | 9, 10 | M per increment |
| 16 | Requirements produce reviewed Git artifacts and Terraform plans | 5, 6, 9, 10 | M |
| 17 | The exact approved Terraform plan provisions tracked resources | 8, 10, 16 | M |
| 18 | Resource drift and additional clouds are supported | 5, 15, 17 | M per increment |
| 19 | Chat, email, file, and state-change adapters reuse automation | 11, 14 | S per adapter |
| 20 | A team can operate and recover a remote installation | 10, 14, 18, 19 | M per qualification gate |
| 21 | The qualified product runs and scales on Kubernetes | 20 | M per qualification gate |

### Wave A — A Small, Usable Harness

#### Phase 1 — Authenticated Local Browser Chat

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

#### Phase 2 — Durable Workspace and Observable Runs

**Milestone:** "I can reconnect or restart OpenShip without losing my conversation or the recorded state of my work."

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

#### Phase 4 — Execution Gateway and First Isolated Tool

**Milestone:** "OpenShip runs a real tool without giving it access to my host or other projects."

**Build**

- Minimal versioned capability manifests with exact-ID lookup.
- Gateway argument validation, policy decisions, operation records, and execution-request authentication.
- Separate sandbox worker using the proposed gVisor profile.
- One prebuilt, credential-free tool.
- Selected artifact transfer, bounded logs, resource limits, cancellation, and orphan cleanup.

**User demonstration:** Run a packaged log-summary tool against an uploaded sample log.

**Exit gates**

- The tool returns its typed result and execution status.
- Actual sandbox tests block host-file, administrative-socket, metadata, cross-run, and unauthorized-network access.
- Resource exhaustion is contained.
- Cancellation terminates the sandbox and cleans temporary data.
- An incompatible or unavailable worker causes execution refusal—never host-subprocess fallback.

**Not yet:** Arbitrary package installation, cloud credentials in sandboxes, or the PoC executor exposed through an API.

#### Phase 5 — First Read-Only Cloud Connector

**Milestone:** "OpenShip can tell me what is happening in my real staging environment."

**Build**

- One maintained connector, preferably AWS unless the first user needs another platform.
- Project/environment connector bindings.
- Credential broker with short-lived delegated credentials where supported.
- Narrow health, inventory, and log-read operations.
- Secret-safe audit records and evidence-linked responses.

**User demonstration:** Ask OpenShip to inspect a staging deployment and explain its observed health.

**Exit gates**

- Results match an independent console/API check.
- Credentials cannot perform a representative prohibited mutation.
- Credentials do not appear in model context, browser responses, logs, or persisted checkpoints.
- Expired or missing access produces a recoverable blocked state.
- Every observation identifies its target, source, and observation time.

**Not yet:** Remediation or generic credentialed shell access.

**Important distinction:** Short-lived credentials reduce exposure; they do not automatically remove standing permissions or make issued cloud tokens universally revocable.

#### Phase 6 — Generated Code and Controlled Artifacts

**Milestone:** "OpenShip can write and run a small analysis program, then show me its outputs."

**Build**

- Generated-code artifacts with digest, runtime, inputs, dependencies, and limits.
- Credential-free, network-denied execution by default.
- Immutable output artifacts, metadata, authorized downloads, and safe previews.
- First supported runtime, followed by others as separate increments.

**User demonstration:** Upload deployment logs and ask for a generated parser that produces a summary and report.

**Exit gates**

- The inspected code digest matches the executed artifact.
- Only selected inputs are accessible.
- Successful outputs remain available after sandbox destruction.
- Infinite loops and excessive output terminate within configured limits.
- Unsafe previews or filenames cannot execute browser code or escape artifact storage.

**Not yet:** Generated code receiving cloud credentials or unrestricted internet access.

### Wave C — Repeatable, Reviewed Workflows

#### Phase 7 — Typed Reusable Workflow Execution

**Milestone:** "I can run the same saved process with different inputs and inspect each step."

**Build**

- Immutable JSON workflow revisions and dependency lockfiles.
- Typed inputs, outputs, restricted bindings, and maintained node factories.
- Initially sequential tool and artifact steps.
- Compatibility-aware compilation cache.
- Run history, step inspector, and schema-driven input forms.

**User demonstration:** Run a saved "collect logs → summarize → publish report" workflow twice with different inputs.

**Exit gates**

- Repeated runs use the same revision and graph structure.
- Invalid references and incompatible dependencies fail before execution.
- Inputs and outputs are visible for every step.
- Restart recovery works for the supported credential-free/read-only steps.
- Cache hits still recheck current policy and connector readiness.

**Not yet:** Natural-language workflow generation or complex composition.

**Clarification:** Deterministic compilation means the same specification produces the same graph—not that model answers or live external results are identical.

#### Phase 8 — Durable Inputs and Approval Decisions

**Milestone:** "A workflow can wait for me overnight, survive a restart, and resume only the correct request."

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

**Not yet:** A workflow-definition review automatically authorizing future mutations.

#### Phase 9 — Freeform Workflow Standardization

**Milestone:** "I describe a process, review OpenShip's formal interpretation, save it, and run it later."

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

**Not yet:** Broad autonomous repair or all graph node types.

**Release checkpoint:** A usable workflow-authoring alpha.

#### Phase 10 — First Controlled External Mutation

**Milestone:** "OpenShip proposes one real change, waits for my approval, applies it, and verifies the result."

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

**Not yet:** Production autonomy, arbitrary cloud writes, or automatic rollback promises.

**Release checkpoint:** The architecture's investigation → proposal → approval → change → verification scenario works end to end.

### Wave D — Capabilities and Reactive Automation

#### Phase 11 — Discovery, Skills, and Package Lifecycle

**Milestone:** "OpenShip finds relevant permitted capabilities without loading the entire catalog."

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

**Not yet:** A hosted marketplace or automatic installation from search results.

#### Phase 12 — MCP Integration

**Milestone:** "A configured MCP tool behaves like other OpenShip tools, including policy and audit."

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

**Not yet:** Trusting a remote server's implementation merely because its schema digest is unchanged.

#### Phase 13 — Webhooks and Scheduled Workflows

**Milestone:** "A webhook or schedule starts a workflow without an open browser."

**Build**

- Durable event inbox and normalized event envelope.
- Authenticated webhook endpoints.
- Scoped trigger bindings to explicit workflow revisions.
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

**Not yet:** Exactly-once guarantees for arbitrary external side effects.

#### Phase 14 — Cloud Alarms and Message Brokers

**Milestone:** "A real alert initiates an investigation and delivers an evidence-based report."

**Build**

- One cloud-monitoring adapter.
- One broker adapter, selected from actual user infrastructure.
- Source-specific authentication and envelope validation.
- Durable acknowledgement/cursor handling, backpressure, retry/dead-letter behavior, and reconciliation.
- Notification/report delivery.

**User demonstration:** Generate a staging alarm, investigate automatically, and inspect the report.

**Exit gates**

- Alarm, accepted event, workflow run, and report are traceable end to end.
- Duplicate delivery and consumer restart do not create unintended duplicate admissions.
- Poison messages are isolated without blocking the stream.
- A supported missed-event scenario is recovered through reconciliation.
- Investigation stays within preauthorized reads; remediation still uses normal gates.

**Not yet:** Every broker or provider.

**Acceptance units:** Cloud alarm adapter first; broker adapter second.

### Wave E — Full Workflow and Infrastructure Capabilities

#### Phase 15 — Bounded Workflow Composition

**Milestone:** "A reusable workflow handles conditions, parallel work, and reusable child processes."

Deliver as separate increments:

1. Conditions and bounded loops.
2. Parallel branches and joins.
3. Child workflows and bounded agent steps.

**User demonstration:** Check several services, investigate unhealthy ones in parallel, then combine results into one report.

**Exit gates**

- Graph validation rejects invalid references and unbounded execution.
- Inspector views distinguish branches, loop iterations, and attempts.
- Child runs inherit project context and no broader authority.
- Concurrency, nesting, token, and step budgets are enforced.
- Resume/cancellation works at each newly supported boundary.
- Dynamic requests for new authority cannot bypass review.

**Not yet:** Arbitrary model-generated orchestration code.

#### Phase 16 — Git-Backed Documents and Terraform Planning

**Milestone:** "My requirements become inspectable infrastructure code and a plan, without creating infrastructure."

**Build**

- Requirements/design artifacts and infrastructure workflow templates.
- Generated Terraform code with pinned tool profiles.
- Policy-controlled Git export or pull-request creation.
- Credentialed CLI profile with explicit bundle, target, scope, and duration authorization.
- Protected Terraform backend configuration.
- Validation and saved-plan inspection.

**User demonstration:** Describe a small staging stack, review its generated files, and inspect the resulting Terraform plan.

**Exit gates**

- Git contains only approved, non-secret material.
- Credentials and Terraform state are never committed.
- The plan identifies account, region, backend, workspace, changes, and artifact digest.
- Failed validation or inadequate credential scope prevents planning.
- No apply occurs through the plan-only workflow.
- Git publication itself follows external-publication policy.

**Not yet:** Applying infrastructure.

#### Phase 17 — Exact-Plan Apply and Resource Bindings

**Milestone:** "The exact plan I approved creates resources that OpenShip can locate later."

**Build**

- Digest-bound approval and exact saved-plan apply.
- Terraform locking plus OpenShip conflict controls.
- Resource objects and concrete bindings.
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

**Not yet:** Generalized autonomous infrastructure repair.

#### Phase 18 — Resource Intelligence and Multi-Cloud Coverage

**Milestone:** "OpenShip recognizes drift, explains it, and supports the same core journeys on additional platforms."

Split into independently accepted increments:

- **18A:** Drift reporting, mapping reconstruction, and reviewed adopt/reconcile actions.
- **18B:** A second cloud's read/investigate/change journey.
- **18C:** A third cloud's equivalent journey.
- **18D:** Versioned built-in workflows for the supported platforms.

**User demonstration:** Change a managed resource outside OpenShip, detect drift, then choose report, adopt, or reconcile.

**Exit gates**

- Desired configuration, management state, and live observations are distinct.
- Missing resources and uncertain ownership are not guessed away.
- Terraform and direct APIs cannot accidentally become competing managers.
- Each added platform passes the common authorization/recovery tests.
- Built-in workflows declare supported platforms and prerequisites.
- No unsupported cross-cloud equivalence is implied.

**Not yet:** Every cloud service or a universal infrastructure abstraction.

### Wave F — Complete Interfaces and Operational Deployment

#### Phase 19 — Additional Trigger and Client Adapters

**Milestone:** "The same workflows can be invoked through the channels my team uses."

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

**Not yet:** Separate privileged execution paths for bots or integrations.

#### Phase 20 — Team Operations and Remote Deployment Qualification

**Milestone:** "A team can run OpenShip remotely, recover it, and upgrade it without losing control of work."

This phase **qualifies** controls developed earlier; it is not when security, metrics, or cleanup first appear.

Deliver in separate qualification gates:

1. Team access and secure remote installation.
2. Backup/restore and uncertain-operation recovery.
3. Upgrade/drain compatibility and operational soak testing.

**Build**

- Membership and viewer/operator/approver/admin roles.
- Authenticated workers, TLS, private endpoints, and remote deployment runbooks.
- Coordinated database/artifact/configuration/key recovery.
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

**Not yet:** Kubernetes qualification or availability guarantees beyond the tested remote deployment profile.

**Release checkpoint:** Operationally qualified self-hosted team release on supported Linux infrastructure.

#### Phase 21 — Kubernetes Deployment and Scaling

**Milestone:** "The same qualified product installs through Helm and remains correct through pod replacement and scaling."

Split into:

- **21A:** Single-cluster Helm deployment and security qualification.
- **21B:** Multi-worker scaling, rolling upgrade, and failure qualification.

**Build**

- Helm packaging, probes, resource requests/limits, and controlled upgrades.
- External or appropriately managed PostgreSQL and artifact storage.
- Restricted service accounts and enforced network policies.
- Compatible sandbox `RuntimeClass`/worker-node configuration.
- Graceful draining, disruption behavior, and capacity testing.

**User demonstration:** Install on a supported cluster, run an approval workflow, replace pods, and scale workers.

**Exit gates**

- The full reference workflow works after pod replacement and rolling upgrade.
- Additional workers do not violate run ownership or mutation serialization.
- Network and sandbox isolation tests run against the real cluster configuration.
- Application pods and generated-code sandboxes remain unprivileged; runtime administration is confined to the designated worker infrastructure.
- Missing isolation support blocks executable capabilities.
- Measured throughput and queue delay improve under the agreed workload.

**Architecture clarification:** Use supported Pod Security Admission/admission controls, not removed PodSecurityPolicy APIs.

**Not yet:** Untested cluster/runtime combinations or stronger isolation providers outside the declared support matrix.

**Release checkpoint:** The architecture's declared target scope is implemented and qualified.

## 3. User Sign-Off at Every Phase

A phase is **ready for acceptance** only when its evidence package includes:

| Evidence | What the user receives |
| --- | --- |
| Runnable increment | Versioned build and documented setup |
| Demonstration | Exact steps, inputs, and expected observable results |
| Acceptance results | Pass/fail results against the agreed phase checks |
| Failure evidence | Restart, denial, timeout, or cancellation scenarios appropriate to the phase |
| Measurement | Relevant latency, usage, recovery, throughput, or retrieval-quality results |
| Limitations | Explicit unsupported behaviors and known issues |
| Decision | Accept, revise, or reject |

A passing test suite does not automatically mean the phase is done. **The user accepts the milestone after observing that it solves the intended problem.**

If a phase proves too large, divide it at a point that still produces a usable increment. Do not turn "database tables finished" or "worker interface finished" into a product milestone.

## 4. Important Boundaries Across the Roadmap

These distinctions should remain explicit throughout implementation:

- **Workflow review is not mutation approval.**
- **Trigger configuration is not unrestricted execution authority.**
- **Deterministic graphs do not imply deterministic model or cloud results.**
- **Event deduplication does not guarantee exactly-once external effects.**
- **Sandbox isolation does not compensate for overpowered cloud credentials.**
- **Git history does not roll back infrastructure.**
- **"Read-only" is enforced by backend authority, not a tool's label.**

Stronger isolation providers, additional brokers, a public marketplace, and alternative orchestration/storage systems remain follow-on choices justified by demonstrated needs—not prerequisites for this roadmap.

**Recommended first commitment:** Approve the overall ordering, then design and implement **Phase 1 only**. Reassess later phases using actual user feedback and measured complexity. Saving this roadmap does not authorize implementation or mark any phase accepted.
