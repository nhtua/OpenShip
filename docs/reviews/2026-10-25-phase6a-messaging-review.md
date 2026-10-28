---
type: review
summary: Source review of PostgreSQL dispatch, event polling and deployment gaps before the Phase 6A JetStream migration.
status: proposed
date: 2026-10-25
---

# Phase 6A messaging migration review

Reviewed repository baseline: `b86b3f7` (the accepted NATS direction), including
the Phase 5 implementation present in this tree. The user confirms Phases 1–5
are finished. This is a source/document review, not a claim that the runtime
or deployment failure scenarios below have been executed.

The user confirmed retaining the roadmap numbering: **6A is messaging; 6B is
generated code and controlled artifacts**. The user also selected a brief
maintenance window with a drained cutover. Detailed transport choices and
capacity targets below remain proposals until reviewed.

## Findings that shape the migration

| Priority | Evidence in the current source | Consequence and proposed resolution |
| --- | --- | --- |
| Critical | `runs/worker.py:Worker.run` calls `queue.claim_next`, sleeps on an empty queue and executes `process_job` synchronously. `queue.py:claim_next` selects across all queued runs using `FOR UPDATE SKIP LOCKED`. | Adding a broker alongside this loop creates competing schedulers. Replace discovery with a JetStream delivery and a **claim for that exact run and dispatch generation**. Retain database ownership/fencing. |
| Critical | `runs/models.py:Outbox` has event-notification fields and `processed_at`. Repository search finds writers but no publisher or `processed_at` updater. Submission creates `RunJob` and an event outbox row; the row is not an execution command. | Do not publish existing rows as commands by interpreting their event type. Add explicit destination, identity and publication state, and backfill commands from eligible queued runs. |
| Critical | `events/service.py:append_event` creates an event only. `providers/attempts.py` and several terminal/title/error paths in `runs/worker.py` use it without adding an outbox row. Other writers, including `execution/publication.py`, create events and outbox rows themselves. | A relay reading today's outbox would miss progress and completed answers. Make every semantic event and its notification one transaction, consolidate writers and enforce one notification per event. Include direct `Event(...)` constructors in the audit. |
| Critical | `queue.py:reconcile_expired` requeues an undispatched run, but fails uncertain tool/provider dispatch as `execution_interrupted` or `model_interrupted`. `execution/admission.py` and publication paths enforce live claims and fences. | Broker redelivery must not mean another model or tool call. Reuse these dispatch boundaries; acknowledge only after a committed outcome, durable wait or safe handoff. Reconciliation that requeues work must create a new dispatch identity transactionally. |
| High | `connectors/blockers.py:park_connector_wait` releases ownership; `resume_connector_wait` makes the same run queued again and resets its job. | Deduplicating by run ID alone loses resumed work; always assigning a new message ID repeats work. Add a durable dispatch generation and stable identity per initial admission, explicit resume and safe requeue. |
| High | `events/routes.py:stream_event_stream` reads the event table every second using a synchronous request session. It emits an event UUID in the SSE `id` field but parses `Last-Event-ID` as an integer sequence. `apps/web/src/stores/chat.ts` reconnects using `after_sequence`. | Replace the live polling loop with an independent API subscription and bounded database catch-up. Preserve UUID event IDs and integer application sequences. Accept legacy numeric headers and resolve UUID headers within the authorized conversation. |
| High | `Worker._supervise` uses short database checks for cancellation/fence loss, periodic lease renewal and availability checks. `request_cancel` atomically cancels and advances both fences. | NATS controls accelerate interruption; they cannot replace persisted cancellation. Keep the existing bounded safety checks and gateway authorization. No cancellation is consumed by a random member of a shared command consumer. |
| High | `Worker.run` schedules Connector expiry, lease reconciliation and tool availability refresh between jobs. | An indefinitely waiting broker pull or a slow turn must not stop these functions. Put them under independently scheduled, bounded supervision; preserve their current semantics. |
| High | `docker-compose.yml` is one development-oriented stack with PostgreSQL, migrations, API, worker, web, and shared artifact storage. There is no NATS service, relay, broker bootstrap, production manifest or deployment wrapper. | Retain the root development entry point and volume identities. Add explicit development overrides and a standalone production stack. Specify broker provisioning, readiness, secrets, backup and restore rather than treating a running port as readiness. |
| High | The API image is `python:3.11-slim`; its Compose healthcheck calls `curl`, which the Dockerfile does not install. The web build uses Node 20 while current README requirements/CI use newer Node versions. | Repair these packaging blockers in the deployment slice: use an available Python HTTP health probe and align the web build with the declared supported Node version. Lock release images/dependencies and test the actual images. |
| High | `main.py:/ready` checks DB/backlog/expired leases only. It has no broker, relay or worker-consumer check. | Separate liveness from execution readiness, report broker/relay degradation honestly and bound admission during outages. Keep read-only history available where possible. |
| High | Existing artifact/Connector runbooks protect storage identity and keys. Their backup instructions do not include JetStream. The artifact guide still describes legacy environment-key setup; README status predates Phase 5. | Add a coordinated messaging runbook and link it from both guides and READMEs. Preserve Connector keyrings, dispatch lock, artifact marker and quotas; explain current managed-tool setup accurately. |
| Medium | CI has real PostgreSQL and synthetic full-stack lanes, but no broker. Several tests call `claim_next` directly, and synthetic workers subclass the existing worker. | Keep database-fencing tests meaningful; adapt end-to-end harnesses to exercise real JetStream delivery. A mock-only broker lane cannot qualify this migration. |

Paths in the table are relative to `apps/api/src/openship` unless otherwise
qualified. These findings are migration requirements, not requests for an
unrelated runtime rewrite.

## Approaches considered

1. **Publish directly after committing a run.** Few initial changes, but a
   process crash between DB commit and publication strands accepted work.
   Publishing before commit allows execution without durable admission.
   Reject as the authoritative delivery path.
2. **Keep a polling outbox and replace worker discovery only.** Easiest bridge,
   but retains routine database dispatch polling and does not address live SSE
   fanout. Useful only as an explicitly temporary implementation checkpoint.
3. **Transactional outbox, notification-driven relay and JetStream consumers.**
   Recommended. PostgreSQL remains the application authority; JetStream owns
   delivery. A committed outbox insert wakes a relay using PostgreSQL
   `LISTEN/NOTIFY`; startup/reconnect scans recover missed wakeups. Notification
   payloads carry no application content. This adds one small relay process and
   provisioning commands, avoids CDC infrastructure, and meets the accepted
   no-routine-dispatch/event-polling requirement.

The proposed approach uses the existing model/tool execution code. It does not
change provider qualification, search/AWS authority, browser workflows,
artifact storage or the Phase 6B sandbox boundary.

## Deliverables and decision boundary

- [Migration design](../superpowers/specs/2026-10-25-phase6a-nats-messaging-design.md)
  defines contracts, failures, compatibility and acceptance.
- [Implementation plan](../superpowers/plans/2026-10-25-phase6a-nats-messaging.md)
  sequences testable changes, deployment tooling and qualification.
- [Deployment and migration runbook proposal](../operations/messaging.md)
  defines local, development Compose and production Compose operation,
  cutover, rollback and recovery.

Only documentation is being changed in this review. New scripts, manifests,
settings and commands described by these documents are implementation work;
the existing PostgreSQL runtime remains operational until that work is built
and qualified. Kubernetes, ECS, sandbox execution and external triggers are
outside this migration.

## Proposal review

A fresh builder review identified two recovery ambiguities, now addressed in
the spec and plan: rebuilding transport under a new epoch must create a new
dispatch generation/identity, and exhausted delivery while a healthy owner
still runs must retain an observation handoff rather than fail/replay the
effect. Closed execution gates stop normal pulls; pre-open smoke tests use a
separate synthetic namespace. These are design corrections, not runtime test
results.
