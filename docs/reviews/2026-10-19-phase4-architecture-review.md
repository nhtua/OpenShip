---
type: review
summary: Approved Phase 4 trusted-tool and conversation-file boundaries, with Connector credentials in Phase 5 and all sandbox/editable-file implementation in Phase 6.
status: accepted
date: 2026-10-20
---

# Phase 4 Architecture Review

## Scope and review status

Reviewed [roadmap Phase 4](../roadmap.md#phase-4--trusted-tools-and-persistent-conversation-files) against [the architecture](../architects.md), the API/runtime/browser structure, migrations, deployment configuration and existing provider contracts. This review supersedes the earlier log-summary/sandbox-first Phase 4 draft. It describes gaps and proposed work, not delivered or qualified capabilities.

The user agreed to the phase split below, selected existing OpenAI Chat Completions for the first model/tool loop, and approved implementation on 2026-10-20. Tool IDs use underscores directly; no dotted canonical-name or wire-alias requirement remains. The [specification](../superpowers/specs/2026-10-19-phase4-execution-and-conversation-workspace-design.md) and [implementation plan](../superpowers/plans/2026-10-19-phase4-execution-and-conversation-workspace.md) are accepted; implementation is underway.

## Conclusion and phase ownership

Phase 4 fits the architecture when trusted tools enter through one gateway in the existing durable runtime. A maintained search function needs no sandbox. Its credentials also do not require introducing another configuration domain: operator references support Phase 4, and the existing Connector domain takes ownership of managed configuration in Phase 5.

| Phase | Required foundation and deliverable | Deferred boundary |
| --- | --- | --- |
| 4A | Independently registered `brave_web_search` and `tavily_web_search`, explicit model-free commands, operation/attempt fencing, operator key references and immutable filesystem result snapshots | No combined search router, SearchProvider framework, Connection entity, browser key entry or secret database |
| 4B | The same tools through one qualified OpenAI Chat Completions profile, complete call/result transcript and cumulative budgets | Other providers retain accepted text support; advanced provider breadth and optimization wait |
| 4C | Persistent conversation-directory association, bounded `workspace_list_files`/`workspace_read_file`, retained-file continuation and recovery | No edit API, mutable logical head, generic storage backend or cross-host filesystem |
| 5 | Managed Brave/Tavily Connectors, browser setup, protected persistent keys and bindings; reuse for later cloud integration | Vault/cloud secret managers are optional integrations, not Phase 4 dependencies |
| 6 | All sandbox installation/driver/image/manager implementation and isolation qualification; generated code and editable logical files with captured versions | More elaborate history, branching and alternative storage follow demonstrated needs |

Deliver and accept 4A before 4B, then 4C. The initial target is one user, one active run per conversation and configurable shared tool capacity with waiting. Dispatched interrupted work fails visibly; it is not automatically redispatched.

## Architecture alignment and implementation gaps

| Architecture requirement | Current evidence / gap | Proposed response and owner |
| --- | --- | --- |
| §3, §10: one runtime, command API, queue, ownership fence, checkpoints and outbox | Existing run service, queue, worker and event service supply the foundation. Execution assumes a user message/model configuration. | 4A adds explicit run kind/graph version and immutable execution configuration. Manual tool runs use the existing worker without a fabricated model call/message. |
| §6: project, conversation, run, operation and artifact identities differ | Existing identities stop at runs/provider attempts; tool operations, artifact records and conversation-workspace association are missing. | Add operations/artifacts in 4A, complete model/tool transcript in 4B, stable workspace association in 4C. Preserve historical records. |
| §8, §13: exact capability lookup and gateway admission | No production maintained-tool registry/gateway exists. | 4A pins manifest/version/digests, settings and authorized identity; recheck enablement/ownership before dispatch/publication. Tool IDs pass unchanged through model function names. |
| §10: persist intent before dispatch and fence publication | Queue finalization is fenced, but existing committing helpers cannot compose every new metadata/outbox publication atomically. | 4A uses caller-owned publication transactions and separate operation/physical-attempt records. Do not hold locks over external I/O. |
| §10: uncertain external work has a visible outcome | Recovery uses provider-start evidence; a model-free tool needs equivalent dispatch evidence. | 4A records tool dispatch intent, closes local work on cancellation and refuses stale publication. Interrupted dispatched runs fail; explicit user retry creates a new run. |
| §7: normalized calls/results and bounded agent loop | Existing provider messages are text-oriented; tool finish reasons/fragments and budgets across several turns need extension. | 4B extends existing contracts/runner/OpenAI adapter. Complete calls only, exact returned-name allowlist, durable transcript, shared retry/time/token budgets and final-only answer/title publication. |
| §8, §13: tools receive only authorized credentials | Phase 3 has operator credential-reference conventions, but no managed Connector store/UI. | 4A resolves fixed server-owned Brave/Tavily environment bindings only during execution. 5 adds encrypted Connector credentials, browser setup and explicit migration of new runs. |
| §4, §12: trusted service access differs from executable isolation | No production sandbox manager or qualified execution profile exists. | Search remains maintained worker code in 4. All sandbox/driver/image/manager work belongs to 6, before the first executable consumer. |
| §14: artifacts preserve exact output identity | No persistent artifact volume/model or safe transfer lifecycle exists. | 4A adds local filesystem bytes plus DB ownership/digest/provenance/quota metadata and staged publication. DB/filesystem crash boundaries need real tests. |
| §11, §12: conversation files survive turns and run cleanup | Current workspace API is account-oriented; no conversation directory association exists. | Use final conversation-scoped physical layout in 4A. 4C associates that same directory without moving bytes or changing IDs and adds bounded list/read. |
| §14: editable working files coexist with immutable evidence | A permanently immutable workspace would block repeated source/Terraform edits. | 4 captures immutable snapshots and an explicit per-output policy. 6 adds logical file heads, expected-base edits and captured versions; past artifact bytes never change. |
| §11: reconnect/pruning cannot erase progress | Existing snapshots omit operations/artifacts. | 4A–4C add safe bounded authoritative projections and semantic events using existing outbox/SSE reducers. |
| §13: results/files do not grant authority or browser execution | Search adds external untrusted strings/URLs; file APIs add a path/transfer boundary. | 4 uses fixed service destinations, bounded HTTP/JSON/text, safe links, no result-URL fetching, opaque artifact IDs, secret-safe errors and untrusted-content tests. |
| §4, §14: durable stores need recovery procedures | PostgreSQL procedures do not yet cover artifact bytes/storage identity. | 4 adds coordinated backup/restore, missing/replaced-root behavior, reservations/retention and restart qualification. 6 separately owns sandbox-manager recovery. |

## Approaches and scope decisions

- Keep Brave and Tavily as independent tools with shared DTO/HTTP helpers where useful. No hidden selection/fallback or redundant provider/connection domain is needed.
- Keep durable work in the existing runtime worker, even though maintained tool functions are shipped with OpenShip. Running long-lived searches inside API request handlers would bypass the established lifecycle.
- Use filesystem storage and immutable artifact/version IDs now. This leaves room for editable logical files later without prematurely adding edit/history tables or a storage plugin framework.
- Preserve existing lifecycle vocabulary, event names, Phase 3 model configuration and accepted text-provider behavior. Target-architecture examples do not require premature waiting states, workflow engines or imported tools.
- Keep sandbox and Connector requirements explicit in later phases. Trusted search tests cannot establish sandbox isolation, and environment references do not establish browser-managed credential storage.

## Risks to verify during approved implementation

- Commit intent before external requests, and test each filesystem/DB publication crash boundary. Neither a DB transaction nor a successful HTTP response alone makes a result safely visible.
- Lock conversation admission and enforce request-key uniqueness; application-only active-run checks are insufficient under simultaneous submissions.
- Count tool-call fragments as observed output and share budgets across turns. Hidden generation retries after partial calls or repeated search dispatch can duplicate external effects/costs.
- Bound HTTP wall time and decompressed response size; test DNS/proxy/redirect behavior and sentinel-key redaction. Cancellation cannot retract a query already received by a search service.
- Keep fixture browser checks, real PostgreSQL/filesystem checks, actual application-stack checks and explicitly opted-in live provider evidence distinct. No sandbox gate belongs to Phase 4.
- Keep earlier queued runs pinned during the Phase 5 binding migration. A new credential backend must not silently reinterpret an already-admitted execution configuration.

## Review and handoff

The accepted specification defines contracts, defaults, compatibility policy and R01–R18. The plan maps them to 17 sequential tasks and separate 4A/4B/4C acceptance gates. Tasks 1–3 are implemented; later provider, runtime, UI and workspace behavior remains subject to the planned verification and unit acceptance gates.
