---
type: plan
summary: Implementation sequence for the Phase 6A transport migration, deployment scripts, compatibility qualification and drained cutover.
status: in_progress
date: 2026-10-25
---

# Phase 6A NATS messaging implementation plan

> **For agentic workers:** The user approved implementation on 2026-10-26.
> Follow the tasks in order, using
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`
> task by task. Use failing behavior tests, focused implementation, passing
> verification and review before each atomic commit.

**Goal:** Deliver existing OpenShip work through JetStream while retaining
Phase 1–5 behavior and providing reproducible local/dev/prod deployment.

**Architecture:** PostgreSQL remains authoritative. Transactional outbox
notifications wake a relay; JetStream delivers commands to exact fenced claims
and event references to independent API consumers. A drained maintenance
window replaces the old scheduler and SSE polling together.

**Tech stack:** Existing Python/FastAPI/SQLAlchemy/Alembic/LangGraph and
Vue/TypeScript, PostgreSQL 15+, async Python NATS client, file-backed JetStream,
Docker Compose, pytest/Vitest/Playwright. No CDC service, Kubernetes or ECS.

**Spec:** [Phase 6A design](../specs/2026-10-25-phase6a-nats-messaging-design.md).
Also read the [source review](../../reviews/2026-10-25-phase6a-messaging-review.md)
and [runbook proposal](../../operations/messaging.md).

## Global constraints

- Preserve all HTTP/event contracts and Phase 1–5 behavior enumerated in the
  spec. Event UUID, integer conversation sequence, dispatch generation and
  ownership fence are distinct identities.
- Only an exact admitted run/generation can be claimed from a delivery.
  Broker redelivery never authorizes repeating an uncertain external effect.
- Keep database leases/fences, 0.5-second safety checks, 10-second renewal and
  60-second recovery timers initially. Remove normal dispatch/SSE polling.
- Proposed capacities: four run slots and one background slot per worker;
  existing `TOOL_CONCURRENCY` and provider limits remain authoritative.
- Commands/events/controls are at most 16 KiB and contain bounded references.
  No secrets, prompts, artifact bodies or arbitrary executable selectors.
- Use additive schema migrations only. Keep existing volume names, storage
  and installation IDs, Connector keys/defaults and legacy history intact.
- Production is single-host Compose with a single persistent broker and no
  high-availability promise. Existing root Compose remains the dev entry point.
- The user approved runtime implementation and selected a drained maintenance
  cutover. Finish tooling and restore/cutover rehearsal before changing a real
  installation; this approval does not authorize a production cutover.

## Review focus

1. An outbox confirmation is lost after broker storage: Task 2 proves stable
   publication identity; Task 3 proves no second effect outside dedup windows.
2. A Connector resumes the same run: Task 1/3 prove distinct generations and
   harmless delayed delivery of the old command.
3. A client subscribes while completion commits: Task 4 proves replay/live
   overlap, multiple API replicas and slow-client recovery.
4. New containers point at fresh volumes or wrong secrets: Tasks 6/8 reject
   mismatched project/storage/installation identities before admission.
5. Restored DB state predates a dispatched effect: Task 8 keeps all execution
   closed and requires reconciliation; it never auto-replays restored work.

## File and responsibility map

API paths below are relative to `apps/api/`; the table maps implemented
responsibilities to their current modules. Migration revisions are additive
and ordered from the repository's Alembic head.

| Files | Responsibility |
| --- | --- |
| `src/openship/messaging/{contracts,models,outbox}.py`, `runs/models.py`, new Alembic revision | Envelope/disposition/gate records, additive outbox/job fields and constraints, transactional enqueue, outbox wakeup trigger. |
| `src/openship/messaging/{connection,topology,relay}.py` | Async connection lifecycle, declared resources, confirmed publication and notification-driven catch-up. |
| `src/openship/messaging/{exhaustion,exhaustion_supervisor}.py` | Content-free observation state machine plus durable MaxDeliver consumption, restart recovery and exact source cleanup. |
| `message_exhaustions` / Alembic 017 | Content-free, source-keyed recovery state with bounded rechecks and settlement claims. |
| `src/openship/messaging/{consumer,handlers,supervisor}.py`, `runs/{queue,worker}.py` | Exact fenced acquisition, bounded execution, cancellation controls and recovery. |
| `events/{service,routes}.py`, `messaging/fanout.py`, `main.py` | Transactional event coverage, API connection lifespan, authorized SSE replay/live bridge, readiness. |
| `runs/service.py`, `connectors/blockers.py`, `providers/attempts.py`, `execution/{publication,recovery}.py`, artifact/workspace event producers | Preserve domain transactions while adding complete command/event notifications. |
| `messaging/{cli,migration}.py`, `runs/worker_commands.py`, `events/retention.py` | Bootstrap/doctor, read-only preview and bounded cutover/rollback/recovery commands, explicit maintenance task. |
| `config.py`, both `.env.example` files, `pyproject.toml`, `uv.lock` | Validated shared configuration, auth paths, chosen client pin and retirement diagnostics. |
| Root `docker-compose.yml`, `docker-compose.dependencies.yml`, `docker-compose.dev.yml`, `docker-compose.prod.yml`, `scripts/openship-stack`, `scripts/init_messaging.py` | Compatible dev entry point, local dependencies, hot reload, standalone production services, protected generated broker configuration and environment-aware operations. |
| API/web Dockerfiles, `.dockerignore` files, `.gitignore`, `deploy/versions.env` | Health probes, supported Node build, reproducible images and exclusion of local secret/env/build material; ignore `.env.prod` and generated backup/credential files explicitly. |
| Tests and `.github/workflows/{python-ci,frontend-ci}.yml` | Unit, real DB/broker, browser, image/deployment and failure-injection gates. |

## Task 0 — Freeze the transport qualification matrix

**Depends on:** approved design. **Deliverable:** tested server/client/CLI
versions and configuration capabilities, without activating transport.

- [x] Add `tests/integration/test_messaging_topology.py` cases against a
  disposable authenticated broker: file storage, progress ACK, confirmed ACK,
  delayed NAK, consumer filters, ACL-restricted creation and `sync_interval`.
- [x] Record exact NATS server image tag/digest, Python distribution/version,
  NATS CLI release/checksums and supported Docker Compose minimum in
  `deploy/versions.env`. Verify the APIs against those release sources and
  lock the dependency in `uv.lock`.
- [x] Add a `jetstream` pytest marker. Tests requiring a real broker fail in
  their required CI lane if the service is unavailable; no blanket skip.
  Exclude that marker from offline unit/pre-commit selections. Define
  `TEST_NATS_URL` / `TEST_NATS_AUTH_FILE` alongside `TEST_POSTGRES_URL` for the
  integration harness; generate disposable credentials and isolated
  per-test stream/consumer namespaces rather than using application secrets.
- [x] Verify the declared server/client pins locally using
  `uv run python scripts/qualify_jetstream.py` from `apps/api`. The initial
  topology-only run exercised six authenticated broker tests; Task 2 extends
  the same pinned lane to include real PostgreSQL relay coverage.
- [x] Confirm the required Python 3.11/3.12/3.13 CI matrix passes before
  completing the qualification task.
- [x] Review and commit the isolated topology/client contract.

## Task 1 — Make all admissions and events durably publishable

**Depends on:** Task 0. **Files:** models/outbox/contracts, additive Alembic
revision, event and command producers in the map; focused migration tests.

**Interfaces:**
`enqueue_run(db, run_id: UUID, generation: int, *, epoch: UUID) -> UUID`;
`enqueue_event(db, event: Event) -> UUID`;
`enqueue_control(db, run: Run, *, event_id: UUID) -> UUID`.
They participate in the caller transaction and never commit or access NATS.

- [x] Add `test_messaging_outbox.py` and PostgreSQL cases:
  commit/rollback atomicity, every producer family, event uniqueness,
  initial/resumed/requeued generations, duplicate job audit and safe schema
  downgrade before activation. Include terminal failure and title events.
- [x] Extend outbox and run jobs; add disposition/gate records and the
  notification trigger. Implement canonical event+outbox creation and migrate
  direct event writers; remove duplicate manual notification rows.
- [x] Add pending-count/age checks serialized with submission. Tests prove
  10,000 pending or 300-second oldest age rejects a new submission without
  partial rows, while cancel remains durable. Preserve request idempotency.
- [x] Verify `uv run pytest tests/test_messaging_outbox.py tests/integration/test_messaging_migration.py -q`
  plus affected submit/Connector/provider/artifact tests; verify upgrade/down
  on disposable PG. Review the producer inventory and commit.

## Task 2 — Deliver committed outbox rows without idle table polling

**Depends on:** Task 1. **Files:** connection/topology/relay modules, CLI,
settings and real broker relay tests.

**Interfaces:** `async run_relay(stop: asyncio.Event) -> None`;
`async publish_record(record: OutboxEnvelope) -> PublicationReceipt`;
`async ensure_topology(*, apply: bool) -> TopologyReport`.
Bootstrap owns resource creation; runtime verifies compatibility.

- [x] Add failing `tests/integration/test_messaging_relay.py` cases: commit
  before listener startup, reconnect during commit, notification coalescing,
  lost PubAck, crash before processed flag, expired publisher claim, broker
  full, NATS reconnect catch-up and per-conversation sequence ordering.
- [x] Implement dedicated LISTEN connection, advisory leadership, bounded
  row claims and confirmed publish; retry on scheduled due/error timers.
  Broker reconnect triggers catch-up even without a later DB notification.
- [x] Add `bootstrap --check` and `doctor`: detect missing resources and
  immutable configuration drift; `bootstrap --apply` is idempotent but never
  recreates/deletes streams or lowers limits silently.
- [x] Verify relay tests against real DB/broker and trace idle SQL: no
  recurring empty-outbox SELECT. Test role ACLs, clean shutdown and auth/TLS
  redaction. The pinned local lane ran 14 broker/PostgreSQL tests with no skips;
  Python 3.11–3.13 CI coverage is configured for the same lane. Review and
  commit this task.

## Task 3 — Execute broker commands through existing fenced runtime

**Depends on:** Task 2. **Files:** `messaging/command.py`,
consumer/handlers/supervisor, queue/worker, Connector blockers, worker
commands, execution recovery and tests.

**Interfaces:**
`claim_run(db, *, run_id: UUID, generation: int, epoch: UUID, worker_id: str, lease_seconds: int) -> ClaimDecision`;
`ClaimDecision` is `claimed(Claim)`, `defer(delay)`, `disposed(code)` or
`reconcile_required`, never a claim for another run.
`async handle_command(message: ValidatedEnvelope) -> DeliveryDisposition`
routes an allowlisted kind to existing execution or explicit retention.

**Implementation slices:**

- [x] **3A — Exact command acquisition:** parse bounded run references, bind
  them to the persisted outbox identity, validate the saved payload digest, and
  claim only the exact due generation under conversation → run → job → shared
  transport-state locks. Local unit cases and real PostgreSQL contention,
  state-lock compatibility and non-claim cases pass.
- [x] **3B1 — Delivery outcomes and execution resources:** classify ACK intent
  from committed state, keep preparation/execution/settlement explicit, and
  provide independent bounded slot threads with cancellation retention and
  Worker/checkpointer/session cleanup. Focused worker regressions and the full
  API commit gate pass; task review is clean.
- [x] **3B2 — Broker consumer and supervision:** complete the ordered slices
  below. The production entry switches only after 3B2b–3B2e qualify.

- [x] **3B2a — Shared consumer topology and pause primitives:** declare and
  verify the two shared execution durables, bound aggregate pending capacity,
  expose confirmed pause/resume, and preserve existing broker state during
  bootstrap races. Pinned real-broker race tests and scoped review pass.

- [x] **3B2b — Inactive-safe exact acquisition:** require both
  `transport.active` and `execution_open` under the shared state lock before a
  claim; preserve distinct closed-gate outcomes so a fetched delivery can be
  held with progress rather than NAKed until exhaustion. Restore messaging
  fixture gates after each SQLite test.

- [x] **3B2c — Bounded run pulls and committed ACKs:** use one bound pull
  subscription per reserved run slot, stop fetches while disconnected/closed,
  progress fetched messages independently, and ACK only after committed
  settlement. Preserve exact claim evidence through settlement; a deferred
  unclaimed command cannot authorize ACK. Focused unit and pinned
  PostgreSQL/JetStream qualification passed; independent review approved.
  Keep background capacity and legacy production entry unchanged.

- [x] **3B2d — Owner controls and supervision:** add bounded per-run control
  subscriptions, initial authoritative ownership checks, wakeable 0.5-second
  safety supervision, monotonic 10-second renewals, correct-owner cancellation,
  and isolated resource cleanup. The affected API unit modules passed (88
  tests), pinned PostgreSQL/JetStream qualification passed (47 tests, no
  skips), and independent spec/quality review approved.

- [x] **3B2e — Independent recovery, health and exhaustion:** run availability,
  Connector expiry and lease recovery outside run slots; add bounded health
  RPC; persist failure/quarantine before acknowledging exhausted deliveries;
  preserve healthy live owners and require explicit retention enqueue.

  - [x] **3B2e.1 — Independent recovery scheduler:** run 10-second tool
    availability and 60-second Connector/lease recovery on a dedicated bounded
    execution resource, independent of broker connectivity and run slots. Keep
    the legacy polling entry's recovery behavior unchanged. The JetStream
    consumer starts this scheduler before broker admission; its run Workers
    disable per-run readiness refreshes while legacy polling Workers retain
    their long-turn refresh. The focused API modules passed (103 tests), the
    consumer factory assertion passed separately (1 test), Ruff and diff checks
    passed, and independent review approved. The pinned PostgreSQL/JetStream
    qualifier was not rerun because `TEST_POSTGRES_URL` is not configured here.
  - [x] **3B2e.2 — Bounded worker health RPC:** add source-owned request/reply
    subjects, subscribe-before-publish, strict reply/request binding, and a
    two-second total unavailable deadline. Use the exact reply subject
    `openship.<installation>.v1.reply.<api-instance>.<request-id>` with
    server-generated UUID tokens. A worker responds only while its consumer,
    recovery supervisor, broker topology and authoritative execution gate are
    ready; multiple workers may receive the broadcast and the API accepts the
    first valid ready reply. Recheck intake and worker task liveness after the
    awaited gate/topology check so shutdown cannot race a ready response. No
    responder returns safe `unavailable`. Task 4 may consume this contract for
    API readiness without changing browser or run-result semantics; do not
    change HTTP health routes in this subtask. Seven focused health/readiness
    unit tests passed; the pinned PostgreSQL/JetStream qualifier passed all 48
    cases with no skips; Ruff, formatting and diff checks passed; independent
    follow-up review approved.
  - [x] **3B2e.3 — Durable failure handoff:** persist safe failure records and
    failure-notice outbox references before a quarantine/reconcile handler can
    authorize ACK; require JetStream PubAck and committed outbox settlement
    before source ACK. Key source identity from trusted stream metadata plus the
    database transport epoch, use the shared exact-row relay lease, record
    digest conflicts without payloads, and delayed-NAK uncertain handoffs to
    free run capacity. Ownership/recovery reconciliation stays recoverable for
    3B2e.4; ledger pruning is deferred to 3B2e.5. The focused failure/relay and
    outbox suites passed (24 tests), focused consumer handoff/backoff cases
    passed (12 tests), and Alembic SQLite upgrade/downgrade passed. Ruff,
    formatting and diff checks passed; independent review approved after the
    publisher seam was tightened to require the committed marker. Full SQLite
    consumer/recovery tests still encounter the existing executor-to-event-loop
    wakeup stall, and the real PostgreSQL/JetStream qualifier remains in the
    overall Task 8 acceptance gate.
  - [x] **3B2e.4 — Exhaustion and live-owner observation:** qualify pinned
    MaxDeliver advisory and stream/consumer recovery behavior; persist
    `watching_live_owner` until a committed outcome or lease expiry, and resume
    observations after restart without treating delivery count as authority.

    Implementation: `OS_ADVISORIES` captures the pinned
    MaxDeliver subject in a durable stream; `exhaustion-v1` is required before
    worker run intake/readiness, and runtime ACLs add `exhaustion-v1` consume
    APIs plus exact `OS_RUNS` source GET/DELETE. Alembic 017 and the
    content-free observation ledger cover live fenced owners, safe requeue,
    terminal settlement, uncertain dispatch blocking and restart recovery.
    Focused SQLite tests cover parser/state transitions, failure conflicts,
    readiness and topology (15 exhaustion, including the mocked supervisor
    persist/delete/ACK ordering path, 11 failure, 4 consumer lifecycle, and 35
    topology/CLI tests passed on 2026-10-27). The authenticated pinned CI
    qualifier executed 50 cases per Python version; all three matrix jobs
    reported 49 passed and 1 failed because the test required
    `OS_ADVISORIES.paused is False`, while NATS reports `None` when it has never
    been paused. The assertion now accepts any value except `True`. The earlier
    shared-pause scope and advisory INFO-permission fixes are also present.
    The paused-state assertion now accepts NATS's unpaused `None` value while
    still rejecting `True`; the pinned run later passed all cases, including
    runtime denial for other-stream GET/DELETE. The required 75% capacity alert
    and full-stream recovery procedure are implemented: the supervisor emits
    transition logs at the 75%/full/recovered boundaries, `doctor` reports
    stored bytes and fails readiness at the alert threshold, and bounded
    `reconcile --preview` / `--apply` scans exact retained run subjects through
    the consumer delivered high-water mark. Apply requires the active transport,
    both DB gates closed, and both execution durables paused with zero pending
    ACKs. Migration 019 records `capacity_scan`, operator identity, and a zero
    delivery count until an advisory supplies its real diagnostic count. Apply
    reuses the existing fenced state machine, preserves uncertain/unbound
    sources, and reports a resumable cursor. The runbook now describes the
    recovery sequence. Focused local capacity, CLI and exhaustion suites pass
    (33 tests). The real-broker next-by-subject cursor contract and end-to-end
    lost-advisory recovery passed. The pinned JetStream qualifier passed on
    Python 3.11, 3.12 and 3.13 with all 53 cases passing on each version.
    PostgreSQL migration and consumer lanes, API unit lanes and both full-stack
    lanes passed. The first Python 3.13 broker attempt had a transient
    ACK/recovery timeout; the same job passed on retry. Production entry wiring
    remains the next slice, 3B2f.

    The first real-broker rerun passed the end-to-end lost-advisory recovery
    case. It exposed two fixture/assertion issues: the generic topology fixture
    queried `OS_RUNS` instead of its disposable stream, and the fanout test
    expected two ordered event outbox rows from one batch even though relay
    ordering claims one event per conversation at a time. The scanner now takes
    an explicit stream name and the fanout test publishes the two rows in
    successive batches. PostgreSQL migration tests also had stale head-version
    assertions; they now expect `phase6a019`. The corrected real-broker and
    PostgreSQL rerun passed all three PostgreSQL lanes and confirmed the
    fanout and lost-advisory recovery cases. The subject-scan contract test then
    exposed that pinned `next_by_subj` treats its sequence as the first eligible
    position; the scanner now advances its request cursor by one to avoid
    returning the prior message twice. The follow-up pinned JetStream run passed
    all 53 cases on Python 3.11–3.13; the transient Python 3.13 ACK/recovery
    timeout passed on retry.
  - [x] **3B2e.5 — Explicit retention maintenance:** accept only an explicitly
    enqueued, bounded retention task with operator identity and durable
    requested/running/completed/failed state; preserve direct maintenance CLI.
    Alembic 018 adds the task ledger and exact maintenance outbox scope. The
    worker commits `running` before a bounded pass, holds the task row lock
    through pruning, and commits the result/delivery digest before ACK. It
    persists exact JetStream sequence intent before ACK, records confirmation
    after ACK, and reconciles a lost post-ACK PostgreSQL write against the
    durable consumer ACK floor; retention cannot prune an unacknowledged task.
    The relay publishes only the task reference. Settled
    ledgers and notices are pruned; unpublished event/failure notices,
    requested/running tasks, queued-run dispositions, and unresolved exhaustion
    observations remain. Direct retention stays available, and enqueue rejects
    requests unless JetStream and both admission gates are active. Added
    defaults, README/runbook guidance, and a PostgreSQL migration downgrade
    guard test. After ACK-floor recovery, focused maintenance passed 16 tests;
    the combined maintenance/outbox/settings/usage-retention/PostgreSQL-retention
    suite passed 50 with the known health-endpoint hang case deselected. Ruff
    lint, selected format checks, and `git diff --check` passed. Disposable
    SQLite Alembic runs passed upgrade, empty downgrade, re-upgrade and schema
    checks, and refused downgrade with task history. The PostgreSQL migration
    downgrade test skipped locally because `TEST_POSTGRES_URL` is unavailable;
    all three PostgreSQL CI lanes passed, including migration coverage.

  **Source constraint:** the pinned NATS MaxDeliver advisory identifies the
  failed stream sequence and delivery count. Advisory-only handling is not
  sufficient for the spec's missed-advisory recovery requirement; the
  exhaustion slice must prove a durable restart/reconnect discovery path.

- [x] **3B2f — Production entry switch:** remove `claim_next` discovery and idle
  polling from the production entry only after 3B2b–3B2e are qualified. Keep no
  automatic polling fallback. `python -m src.openship.runs.worker` now starts
  `RunConsumer`, preserves `--worker-id` and `--log-level`, drains on SIGINT or
  SIGTERM, and closes its owned NATS client. The synthetic full-stack fixtures
  use an explicit test-only legacy polling harness until Task 8 converts them
  to real JetStream consumers. Five focused entry/identity tests and all commit
  hooks pass. CI run `40491206490` passed API unit, PostgreSQL, JetStream and
  full-stack lanes; frontend checks and lint passed as well.

- [x] Add failing unit and PG/broker cases for every spec delivery-state
  branch, including inactive/closed gates, lost-ACK redelivery, obsolete
  generation, other live owner, stale fence and gate-owner settlement. The
  command, delivery, consumer and exhaustion suites cover these branches; the
  PostgreSQL/JetStream qualifier passed in CI run `40491206490`.
- [x] Reuse `process_job` through per-slot execution objects in a bounded
  executor. `test_two_slots_execute_independently_while_one_worker_setup_blocks`
  proves another slot progresses while setup blocks; executor tests cover
  cancellation, progress, independent resources and
  `model_interrupted`/`execution_interrupted` cleanup.
- [x] Commit outcomes/waits before confirmed ACK; requeue plus new generation
  plus outbox is atomic. Unit tests cover failed commits, pre-dispatch restart,
  and atomic generation handoff; broker tests cover lost ACK after committed
  result and transport loss without repeating the effect.
- [x] Implement quarantine ledger/notices, exhaustion recovery and guarded
  replay, including unconfirmed publication. `enqueue-retention --dry-run`
  carries explicit task arguments without changing direct retention. Tests
  preserve a healthy live owner's observation until outcome/lease expiry and
  never use transport delivery count as replay authority.
- [x] Verify consumer, recovery, tool safety and provider worker suites plus
  real-broker ACK-loss and transport-loss cases. Resource close behavior is
  covered by executor and broker tests; CI run `40491206490` passed all unit,
  PostgreSQL, JetStream and full-stack lanes. Task 8 retains the broader
  process-kill and restore rehearsal.

## Task 4 — Bridge replay and live SSE through independent API consumers

**Depends on:** Tasks 1–3. **Files:** fanout, events routes/service, API
lifespan/readiness, event tests and browser regression tests.

**Interfaces:** `async subscribe_conversation(scope: AuthorizedScope, after_sequence: int) -> AsyncIterator[EventRead]`;
`resolve_event_cursor(db, scope: AuthorizedScope, header: str | None, query: int) -> CursorDecision`.
Reuse the existing event projection; no broker types leak to the web client.

- [x] Add failing `test_messaging_sse.py` and real two-API tests: commit during
  subscription, replay/live overlap, out-of-order notices, late old events,
  UUID/numeric headers, foreign-conversation UUID, pruned history and overflow.
- [x] Implement subscribe/flush-before-high-water, bounded replay, dedup and
  gap repair with short-lived off-loop sessions. Enforce 256-notice client
  queues; emit `snapshot.required` and close on unsafe overflow.
- [x] Remove the one-second DB event polling loop. Keep 15-second heartbeat,
  HTTP auth/snapshot routes and event UUID `id`. Broker disconnect closes
  streams for existing browser recovery; test reopened clients on both APIs.
- [x] Add readiness detail and two-second health RPC with source-owned reply
  validation; timeout/no-responder cannot repeat a run. Prove slow work does
  not block history, auth, cancel or readiness requests.
- [x] Verify event suites and `pnpm test:run`, `pnpm typecheck`, affected
  Playwright journeys; trace idle SSE SQL and review/commit.

  The implementation is committed in `6c3fad2`; the first pinned JetStream
  run found that relay event publications still carried the generic envelope,
  while `EventFanout` requires an exact `{"event_id": ...}` reference. The
  relay now publishes only that reference and validates its conversation
  subject. A late-old-notice regression covers both the unit fanout and real
  two-API path: after replay advances, the stale reference emits no duplicate
  and does not prevent the next committed event. All 14 focused SSE tests pass;
  PR CI run `40494389435` passes the Python 3.11–3.13 unit, PostgreSQL and
  JetStream matrices, both full-stack lanes, frontend typecheck/tests/build and
  lint. The complete Playwright E2E suite passes (66 tests). Its existing
  conversation-row locators and two stale text assertions were corrected to
  match current accessible names and user-facing copy. Idle-stream tests and
  the browser reconnect journey pass.

## Task 5 — Build resumable cutover, rollback and inspection commands

**Depends on:** Tasks 1–4. **Files:** messaging migration/CLI, retention,
populated PG fixtures and `tests/integration/test_messaging_cutover.py`.

**Interfaces:** `plan_cutover(db, *, cutover_id: UUID) -> MigrationReport`
is read-only; `apply_cutover(db, *, cutover_id: UUID, batch_size: int) -> MigrationReport`
is resumable. `plan_rollback(db, *, cutover_id: UUID) -> MigrationReport`
requires all executable work to have a known safe old-runtime disposition.

- [x] Add read-only preview fixtures for queued/terminal/live/expired/dispatched/
  waiting runs, duplicate jobs, missing event notifications, malformed and
  duplicate notices, pruned references, bounded samples and multi-batch scans.
- [ ] Add apply/verify fixtures for interruption and restart after each batch,
  stable cutover IDs, identities, sequences and configuration. Cover
  queued/terminal/live/expired/dispatched/
  waiting runs, duplicate jobs, mixed legacy outbox rows, missing retained
  event notifications and pruned event references.
- [x] Implement read-only `cutover preview` for Phase 5/6A run dispositions,
  command identity, notification coverage, event identity conflicts and
  orphan references.
- [x] Implement resumable event/run `cutover apply` and read-only DB
  `cutover verify`. Apply requires explicit legacy-writer shutdown attestation,
  ready broker topology through the CLI, no preview blockers and no active
  retention tasks. It closes admission/execution, rechecks and locks each
  bounded batch, and commits progress cursors with deterministic event/command
  identities.
- [x] Implement explicit execution and admission gate opening. Execution is
  reopened only after DB re-verification under the state lock; admission waits
  for execution-open confirmation. The CLI also checks JetStream topology.
- [x] Implement `rollback preview|apply` with bounded content-free reports.
  Refuse old live owners, wrong installation/epoch, incomplete dispositions,
  active retention, unresolved exhaustion/claims and pending command or
  maintenance publications. Apply requires operator, stopped-process and
  drained-consumer attestations; it rotates and persists the rollback epoch,
  requeues retained event notices, bumps safe queued generations, and resumes
  from transactional cursors. The rollback epoch is reused by a later forward
  cutover with a new cutover ID.
- [ ] Add retention rules for unpublished event references and delivery
  ledgers. Verify no cleanup loses an unpublished command or changes current
  event/message retention policy.
- [x] Prove rollback preserves additive schema and stops/fences new delivery;
  reject maintenance/uncertain work until explicitly resolved. Verify a later
  forward cutover mints new dispatch generations and command identities, while
  a retry resumes the same rollback epoch/cursors. Closed execution gates stop
  normal pulls; smoke tests use a separate disposable synthetic namespace.
  Rollback has focused SQLite coverage; real PostgreSQL locking and broker
  pending-delivery qualification remain part of Task 8.

  Read-only preview slices: `plan_cutover()` inventories Phase 5/6A run states,
  queue/event/outbox totals, duplicate jobs, and bounded blocker IDs. Its
  exclusive dispositions apply ownership and fence checks before waiting or
  terminal skips; queued eligibility requires no provider/tool dispatch
  history and a `(kind, graph_version)` supported by the runtime registry.
  Unsupported persisted graph versions block replay. Phase 6A commands are
  reused only when the current installation,
  epoch, generation, payload and identity match, with only a digest-matching
  closed-gate hold accepted. `created`/`running` and unknown states block.
  Event preview separately reports Phase 5 notification backfill needs and
  verifies exact Phase 6A reference identities, bounded issue IDs, malformed
  or duplicate notices, and orphan/pruned references. Event identity conflicts
  and orphans block preview; missing notifications remain backfill work.
  Focused SQLite tests cover run dispositions, event scan batches, identity,
  bounded output and no-write/content-free reporting. `status=preview` still
  does not certify apply readiness. Resumable apply records event/run cursors
  and stable counters; interruption coverage verifies same-ID resume without
  duplicate notices or commands. Verification waits for all required DB
  publications to receive PubAck and checks JetStream topology in the CLI.
  Resumable apply/verification, explicit gate open/close commands and rollback
  preview/apply are implemented. Retention semantics and PostgreSQL/broker
  rollback qualification remain open.

## Task 6 — Package all three supported deployment modes

**Depends on:** Tasks 0–5. **Files:** deployment/Compose/image/config files in
the map, READMEs/runbooks and `tests/test_messaging_packaging.py`.

- [x] First add failing packaging checks: standalone prod does not inherit
  dev ports, reload, source mounts, sample DB passwords or build steps;
  dev/local preserve service/volume/storage identities and entry points.
- [x] Implement local/dev/prod lifecycle, init, bootstrap, doctor and
  cutover/gate/reconcile/rollback operations in `scripts/openship-stack`;
  generate protected role credentials and private NATS configs without
  overwriting existing identities; gate startup on authenticated JetStream
  account readiness. Backup/restore orchestration and credential rotation are
  explicitly deferred and the wrapper rejects those command names.
- [x] Keep root `docker-compose.yml` as dev default; add a separate local
  dependency file, source-reload override and standalone production file with
  immutable image requirements, resource/log limits, restart/grace behavior,
  private broker/DB, TLS and role-specific auth mounts. API readiness checks
  the event consumer and worker RPC.
- [x] Repair API health probe without assuming curl; align Node build with
  documented support, freeze dependency installation and exclude secrets,
  `.env`, local artifacts and virtualenvs from image build contexts. Ignore
  `.env.prod` and generated backup/credential files in version control.
- [x] Add native localhost instructions, examples for both env files and
  production config, startup/dependency order, broker outage/full-disk
  behavior, non-destructive stop, upgrades and rollback. Backup/restore wrapper
  automation remains deferred; use the existing storage/database procedures.
- [x] Preserve the user's existing native hot-reload workflow: Vite with
  `pnpm run dev --host 0.0.0.0`, the API with Uvicorn `--reload`, and the
  worker and relay with Watchdog `watchmedo auto-restart`. Static command and
  mount checks pass; actual Compose file-watch/restart behavior remains to be
  exercised on a Docker host.
- [ ] Verify `docker compose config --quiet` for dev/default, dev/override
  and standalone prod, then build and cold-start each declared arrangement.
  Test image probes, actual hot reload, worker/relay restart and existing
  artifact/Connector mounts. Docker/Compose is unavailable in the current
  environment, so these checks remain a release gate.

## Task 7 — Gate first dev startup on explicit cutover confirmation

**Depends on:** Task 6. **Files:**
- Create `apps/api/src/openship/messaging/startup.py`.
- Modify `apps/api/src/openship/messaging/cli.py`.
- Modify `scripts/openship-stack` and `docs/operations/messaging.md`.
- Modify this plan without changing other unchecked Phase 6A work.

**Spec:** [Dev stack first-start cutover](../specs/2026-10-27-dev-stack-auto-cutover-design.md).

**Interfaces:**
`startup_cutover_id(db: Session) -> UUID` resolves an in-progress persisted
ID first, otherwise a stable UUIDv5 derived from the installation identity.
`startup_report(db: Session) -> StartupReport` reports `active_ready`,
`confirmation_required`, `blocked`, `resume_apply`, `finish_apply`, or
`manual_required` with the cutover ID and read-only cutover report.
`apply_startup_cutover(db: Session, *, cutover_id: UUID, writers_stopped: bool,
development_confirmed: bool) -> CutoverApplyReport` rechecks the expected ID
and readiness before delegating to existing resumable `apply_cutover`. The CLI
exposes `startup preview` and `startup apply --cutover-id UUID --dev-confirmed
--writers-stopped`; the explicit ID binds apply to the preview the user
confirmed, and the dev-only flag prevents production operators from
accidentally choosing the deterministic startup apply path.

- [x] Add a DB-backed startup report that includes transport activity, gates,
  cutover progress and a stable per-installation cutover ID. Reuse a persisted
  in-progress ID; refuse automatic forwarding after rollback.
- [x] Add `startup preview` and `startup apply --cutover-id UUID --dev-confirmed --writers-stopped`
  CLI operations. Preview is read-only. Apply recomputes the preview and calls
  the existing bounded `apply_cutover` only when unblocked; it never bypasses
  run/event classification blockers.
- [x] Update `scripts/openship-stack` to accept `dev start --cutover`. Without
  the flag, inactive transport prints preview/ID and exits nonzero asking for
  confirmation. With it, stop Compose API/worker/relay/web (the operator must
  also stop native writers pointed at the same database), re-preview, apply
  with the same ID, and exit nonzero after successful apply. On a blocked
  preview, show the manual runbook link and preserve all data/volumes.
- [x] Make `scripts/openship-stack -e dev stop` run Compose `down` using both
  `docker-compose.yml` and `docker-compose.dev.yml` even when the stack was
  started without reload. Preserve named volumes; leave local/prod stop
  behavior unchanged.
- [x] On the next ordinary dev start, resume an already-confirmed incomplete
  apply or start relay under closed gates, wait for cutover verification, open
  execution then admission, and start the full stack. Keep active installations
  on the normal startup path.
- [x] Make `prod start` refuse to start application services while transport
  is inactive; never auto-apply there. Expand `docs/operations/messaging.md`
  with production ID creation, preview/blocker resolution, stopped-writer apply,
  verification, gate opening and ingress restoration steps.
- [x] Verify shell syntax, Python compilation, Compose configuration rendering
  when an engine is available, and `git diff --check`. Preserve all existing
  unchecked Phase 6A qualification work.

## Task 8 — Qualify compatibility, failures, capacity and operator recovery

**Depends on:** Tasks 6 and 7. **Files:** integration/full-stack fixtures and tests,
CI workflows, `docs/testing/phase6a-messaging-qualification.md` (create),
READMEs and this plan's completion status.

- [ ] Adapt synthetic worker launchers to use real broker consumers; keep
  injected external services synthetic. Run all existing applicable Phase 1–5
  journeys and new wait/resume/retry/replica/reconnect coverage.
- [ ] Add required real JetStream+PG CI and deployment lanes with nonzero
  executed-test/no-skip checks. Run unit, Ruff, typecheck, frontend tests and
  build; do not replace existing PG/browser gates with mocks.
  Offline API selection becomes `-m 'not postgres and not provider_live and not full_stack and not jetstream'`;
  the dedicated broker lane runs `-m jetstream` with its real services, and
  full-stack broker fixtures use the declared disposable connection settings.
- [ ] Run the 50-conversation qualification with two APIs/two workers, slow
  synthetic calls and explicit maintenance. Measure spec latency/capacity
  targets, idle SQL, memory/connections and retained-resource cleanup.
- [ ] Exercise broker/DB disconnect, disk full, exhausted deliveries, bad
  ACL/schema, lost acknowledgements and pre/post-dispatch process kills.
  Record durable outcomes and prove no extra provider/tool invocation.
- [ ] Rehearse fresh install, populated Phase 5 upgrade, interrupted cutover,
  rollback, broker-only loss and coordinated DB/artifact/JetStream restore in
  all modes. Restore stays closed until uncertainty is reconciled.
- [ ] Record pins/digests, hardware, results, known limits, backup/RPO/RTO
  evidence and acceptance demonstration. A missing required environment is
  an explicit incomplete gate, never a passing skip.
- [ ] Fresh review of the complete migration and operator docs; fix findings,
  rerun affected checks, commit and present for user acceptance. Only then
  update roadmap status and allow Phase 6B work.

## Execution order and review gates

Tasks 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 are the main dependency chain.
Packaging drafts can be prepared alongside runtime work after Task 0, but
cannot pass without real scripts and built images. Producers can be migrated
in small reviewed groups within Task 1; no group may silently omit its events.

Each implementation slice includes tests for observable behavior, a focused
review and a working commit. Use the repository `git-workflow` skill for
worktrees, file-based messages and required Rayloc scans. Do not copy inline
commit-message examples from general skills. Keep deployment and destructive
actions confined to disposable fixtures until the production cutover is
explicitly authorized.
