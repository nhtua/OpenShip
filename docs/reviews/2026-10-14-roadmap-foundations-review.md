---
type: review
summary: Review of Phases 4–21 for cumulative foundations, small first consumers, explicit compatibility, and expansion without parallel runtimes.
status: proposed
date: 2026-10-14
---

# Roadmap Foundation Review After Phase 3

## Scope and Intended Outcome

The user confirmed that Phases 1–3 are complete and requested a careful review of the remaining roadmap: establish strong foundations at a small scale, then expand them in later phases. The review changes the proposed delivery design; it does not implement or authorize future features.

Reviewed the [roadmap](../roadmap.md), [architecture](../architects.md), Phase 2/3 implementation plans, and the delivered API/web structure at Phase 3 commit `ce9eec7`. Phase 3 is on [PR #22](https://github.com/nhtua/OpenShip/pull/22); completion does not imply merge/release or unqualified support for every model/endpoint.

The phase numbers and target product scope remain intact. The revised roadmap identifies what each phase extends, assigns each shared foundation to its first real consumer, splits broad milestones into usable acceptance units, and carries earlier behavior forward.

## Delivered Foundation to Preserve

The following exist today and must be extended rather than recreated:

- Authentication, project ownership, persistent conversations/messages, and first-response conversation titles.
- The shared [run model](../../apps/api/src/openship/runs/models.py), PostgreSQL queue, leases/fencing, worker, checkpoints, transactional outbox, semantic events, reconnect cursor, and snapshots.
- The Phase 3 provider contracts, explicit installation profiles, per-run configuration snapshots, bounded request supervision, attempt records, and safe usage/error projections.
- The browser workspace's HTTP commands, event reducers, run cards, and history recovery.

These are a chat foundation, not already a complete agent/tool/workflow system. The current [chat graph](../../apps/api/src/openship/runs/graph.py) performs one bounded model turn and rejects tool-call results. Tool calls and structured output are not generally qualified by the existing profiles. Phase 4 must explicitly introduce and qualify the first model/tool loop; merely registering a tool is insufficient.

The current database/API uses `completed` for successful runs. The architecture's illustrative lifecycle vocabulary is not permission to rename existing states or discard historical events. Waiting states, graph kinds, step identities, and schema versions need migrations and compatibility checks when introduced.

## Findings and Corrections

| Priority | Gap in the previous roadmap | Consequence if left ambiguous | Proposed correction |
| --- | --- | --- | --- |
| High | Phase 4 demonstrated an uploaded-log tool, but artifact ownership/storage appeared principally in Phase 6. | A temporary upload/volume mechanism could become a second artifact system. | Establish minimal immutable, project-scoped local artifacts and bounded transfer in 4A; extend them for code/previews in 6 and shared storage when deployment requires it. |
| High | The first conversational model/tool loop had no explicit delivery owner. | Cloud tools could exist while chat remained unable to invoke them, or a provider-specific agent loop could bypass Phase 3. | Add 4B after a working explicitly selected tool in 4A; extend provider contracts and the current graph, qualifying one profile first. |
| High | Capability versioning, authorization, and operation responsibilities were spread across Phases 4, 10, 11, and 20. | Imports, mutations, or team access could introduce competing lookup/policy/dispatch systems. | Start with exact version/digest identity, owner-based authorization context, operation identity, fenced dispatch, and basic disablement in 4. Add mutation recovery/locks in 10, discovery/import lifecycle in 11, and team roles in 20. |
| High | Workflow delivery did not explicitly protect existing graph/run semantics or define compatibility before later node expansion. | Saved workflows or in-flight runs could be recompiled under different meanings; chat could acquire a parallel queue/event API. | Record schema/compiler/runtime compatibility and stable step invocation identity in 7; add supported nodes in 8/15 and reject unsupported kinds until then. Keep the current queue, gateway, and command/event contracts. |
| High | Some security/recovery controls were listed chiefly under Phase 20, and Phase 20 depended on broad feature completion. | Teams might postpone safe remote operation until all clouds/channels were implemented, or expose earlier features without their controls. | Require controls and recovery instructions when each capability is exposed. Qualify one complete supported journey after 10; requalify additional enabled features rather than waiting for all of 11–19. |
| Medium | Scoped observations existed in Phase 5, but explicit resource objects/bindings first appeared in Phase 17. | Provisioning could introduce identifiers incompatible with earlier read-only investigations. | Establish minimal target/resource references and timestamped observations in 5, backend/desired-state ownership in 16, then provisioning provenance in 17 and drift/more clouds in 18. |
| Medium | Read-only automation depended on freeform authoring and first external mutation; other adapters depended on unrelated breadth. | Independent useful increments would be blocked, encouraging ad hoc automation paths. | Let 13 start a hand-authored read-only workflow after 7/8; require 10 additionally for mutating triggers. Remove mandatory advanced composition from 18 and unrelated broker completion from 19. |
| Medium | Acceptance focused on each phase's new demonstration without an explicit cumulative behavior requirement. | Earlier title, authentication, history, or recovery behavior could regress while the new milestone passed. | Require continuity evidence, compatibility/migration evidence, and applicable earlier acceptance journeys at every phase. Retire superseded paths explicitly. |

## Phase-by-Phase Review

| Phase | Small first consumer | Foundation established or reused | Expansion boundary |
| --- | --- | --- | --- |
| 4 | 4A: explicitly select one maintained log-summary tool; 4B: invoke the same tool from chat on one qualified profile. | Existing durable runs plus the common gateway, version/digest identity, authenticated sandbox request, local artifacts, and bounded tool loop. | More tools/profiles use these contracts; imports, cloud secrets, and generated code are separate capabilities. |
| 5 | 5A: one health read on one bound staging target; 5B: inventory/log reads. | Gateway execution kind, credential broker/source, scoped target/resource reference, safe timestamped observations. | Add credentials/operations/clouds through the connector contract; provisioning does not replace identity. |
| 6 | One Python program with a prebuilt dependency set and selected log input. | Phase 4 artifact storage/transfer and sandbox lifecycle, extended with a digest-bound generated-code profile. | Additional runtimes/dependencies/previews are separately qualified; no direct host execution. |
| 7 | One hand-authored sequential workflow invoking the Phase 6 parser; forms/cache follow the working run. | Same LangGraph/queue/events; versioned definition, dependency lockfile, logical step identities, compatibility, persisted step results. | Waits and composition add supported node kinds to this schema/compiler, not another workflow engine. |
| 8 | One typed input wait, then one approval request. | Shared durable request identity, actor eligibility, digest binding, atomic consumption, and resume command. | Team roles and other clients reuse the decision contract; mutation approval is bound to a concrete operation. |
| 9 | One natural-language staging-investigation workflow using supported sequential/wait nodes. | An authoring frontend to Phase 7's validated executable definition and factories. | Broader examples and later node kinds reuse the same format; a model never invents capability IDs or authority. |
| 10 | One explicit staging change through a maintained connector. | Existing operations/targets/decisions gain mutation reconciliation, actual-target conflict keys, and verification. | Terraform and future mutations reuse admission/recovery; ad hoc chat gets no alternative write path. |
| 11 | Lexical selection over a small maintained catalog, then one skill, semantic ranking, and one private import. | Existing capability IDs/digests/lockfiles and gateway policy. | Discovery, skill loading, and package lifecycle extend the catalog; search is neither install nor execution authority. |
| 12 | One local MCP server, then one remote server. | Common registry/gateway, sandbox profile, credential broker, tool-result/artifact limits. | Qualify each transport/destination; provider-native execution and server-defined privileges are excluded. |
| 13 | One signed webhook starts a saved read-only report; one schedule follows. | A shared external-event inbox/envelope and scoped admission identity feeding existing workflow-start commands/queue. | Mutating bindings require 10; new sources reuse the inbox and do not create schedulers. |
| 14 | One cloud alarm investigation, then one broker adapter. | Phase 13 admission/deduplication plus source acknowledgement/cursor contracts and existing cloud reads. | Additional transports use declared source guarantees. Optional notification uses one authorized destination and recorded delivery identity. |
| 15 | One branch/loop, then a parallel join, then a child workflow and bounded agent step. | Phase 7 schema/compiler/step IDs, Phase 8 waits, Phase 10 operation recovery, Phase 4 model/tool loop. | Each new node type preserves old workflows and enforces descendant budgets and operation identity. |
| 16 | Local generated/validated files, then reviewed Git export, then one saved Terraform plan. | Existing artifacts/revisions/gates; credentialed CLI profile; protected plan/backend/desired-state binding. | Publication and cloud planning have separate authority; a plan-only path never applies or publishes secret-bearing state. |
| 17 | Apply the one qualified stack and recover an interrupted apply. | Existing exact-plan artifact, approval, operation reconciler, target locks, backend, and resource references. | Extend bindings with confirmed provisioning provenance; broader stacks use the same apply/recovery path. |
| 18 | Report-only drift on that stack, then confirmed reconstruction/adoption/reconciliation; qualify each new cloud separately. | Existing desired revisions, resource ownership/observations, connector contracts, and workflow library. | No universal cloud abstraction or replacement inventory; advanced nodes are conditional on actual workflow needs. |
| 19 | Start/status for one read-only workflow from one channel. | Existing actor mappings, inbox/admission, decision commands, gateway, events, and any enabled delivery ledger. | Add approvals/notifications after identity checks; team channels require 20A, source-specific adapters require their actual source foundations. |
| 20 | Two users/roles on one remote host for the first complete mutation journey. | Extend current owner-based authorization; qualify existing worker/TLS/credential/storage/recovery/version controls. | Qualify additional enabled features and shared storage only as used; more clouds/channels are not prerequisites. |
| 21 | One cluster/runtime with minimal replicas, then cross-node workers and controlled scaling. | Package the qualified processes, fencing/locks, worker protocols, storage interfaces, and upgrade procedures. | Shared artifacts/database limits must be qualified before cross-node replicas; early deployment qualification does not claim unfinished capability phases are complete. |

## Dependency Corrections

- **4 depends on 3:** the model/tool loop extends Phase 3 provider contracts rather than assuming a direct OpenAI client.
- **7 depends on 4 and 6:** the first demo explicitly reuses the implemented log/code/artifact operations. It does not silently require live cloud collection.
- **9 includes 5:** its staging-health authoring demo uses actual connector capabilities.
- **13 depends on 7 and 8:** hand-authored, saved read-only workflows need neither freeform authoring nor mutation support. Mutating trigger bindings additionally require 10.
- **15 depends on 7, 8, and 10:** composition extends executable nodes, waits, and mutation recovery; freeform authoring is a client, not the execution foundation.
- **18 no longer requires all of 15:** report-only drift and another connector can use sequential workflows; advanced composition is required if the selected workflow uses it.
- **19 uses 8, 11, and 13 with explicit source-specific requirements:** an external client does not inherently require a broker. Team use adds 20A; resource-state/broker sources add their respective observation/transport qualification.
- **20 starts after the first complete 10 journey:** its supported features are qualification inputs. It no longer requires all brokers, clouds, or channels. Later enabled features rerun the applicable qualification gates.
- **21 qualifies the enabled deployment matrix:** Kubernetes qualification may follow that first supported journey. Claiming the architecture's full target scope additionally requires all agreed capability phases to be accepted; deployment qualification alone cannot establish their completion.

These changes do not make all phases independent. Credential-bearing calls still require the broker, executable code requires the real isolation boundary, mutations require concrete authority/recovery, and remote exposure requires its controls before use.

## Cumulative Acceptance and Migration Evidence

Each implementation design must name the existing contracts it extends and map its acceptance checks to these shared journeys:

| Journey | Preserved evidence as the product grows |
| --- | --- |
| Authenticated conversation | Sign-in/out, project isolation, ten turns, history, first-turn title extraction/clean answer, and no later retitling. |
| Durable run | Browser reconnect, worker restart, cancellation, lease turnover, no duplicate terminal publication, and authoritative snapshots/events. |
| Provider boundary | Pinned selection/fallback, bounded retries/budgets, secret-safe errors, capability refusal, and new applicable adapter conformance cases. |
| Tool/artifact boundary, from 4 | Same gateway for every caller, real sandbox denial tests, selected immutable inputs, bounded outputs, and unauthorized/cross-project access denial. |
| Workflow/decision boundary, from 7–8 | Same revision/graph meaning, versioned step identity, safe resume, request consumption under concurrent/repeated replies, and explicit incompatibility blocks. |
| Mutation boundary, from 10 | Target-bound approval, fresh authority, stable operation key, conflict serialization, uncertain-outcome reconciliation, and restore without blind redispatch. |
| Automation boundary, from 13 | Authenticated/scoped admission, source-specific deduplication, bounded backlog, and no bypass of the same workflow/operation controls. |

Future phases need not claim unimplemented controls already exist. Their completion evidence must show the first real consumer and its failure behavior. Incompatible schema/protocol/runtime changes require migration tests over old records and resumable work; retaining an undocumented parallel service is not a compatibility strategy.

## Proposed Next Delivery

Design Phase **4A** against the delivered Phase 3 branch: one user-visible uploaded-log tool invocation through the existing run lifecycle, with minimal durable artifacts and the enforced authenticated sandbox boundary. Its design should enumerate the run/operation/artifact and worker-request contracts, prove their first consumer, and preserve the delivered chat journeys. Follow with **4B** to add one qualified conversational tool-call loop through the same gateway.

Review and acceptance of these roadmap revisions remain separate from approval to implement Phase 4. No future capability or support-matrix expansion is marked delivered by this review.
