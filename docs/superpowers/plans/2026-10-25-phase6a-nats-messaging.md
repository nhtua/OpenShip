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
4. New containers point at fresh volumes or wrong secrets: Task 6/7 reject
   mismatched project/storage/installation identities before admission.
5. Restored DB state predates a dispatched effect: Task 7 keeps all execution
   closed and requires reconciliation; it never auto-replays restored work.

## File and responsibility map

API paths below are relative to `apps/api/`; new names are proposed interfaces.
Choose Alembic revision IDs from the actual head when implementing.

| Files | Responsibility |
| --- | --- |
| `src/openship/messaging/{contracts,models,outbox}.py`, `runs/models.py`, new Alembic revision | Envelope/disposition/gate records, additive outbox/job fields and constraints, transactional enqueue, outbox wakeup trigger. |
| `src/openship/messaging/{connection,topology,relay}.py` | Async connection lifecycle, declared resources, confirmed publication and notification-driven catch-up. |
| `src/openship/messaging/{consumer,handlers,supervisor}.py`, `runs/{queue,worker}.py` | Exact fenced acquisition, bounded execution, cancellation controls and recovery. |
| `events/{service,routes}.py`, `messaging/fanout.py`, `main.py` | Transactional event coverage, API connection lifespan, authorized SSE replay/live bridge, readiness. |
| `runs/service.py`, `connectors/blockers.py`, `providers/attempts.py`, `execution/{publication,recovery}.py`, artifact/workspace event producers | Preserve domain transactions while adding complete command/event notifications. |
| `messaging/{cli,migration}.py`, `runs/worker_commands.py`, `events/retention.py` | Bootstrap/doctor, read-only preview and bounded cutover/rollback/recovery commands, explicit maintenance task. |
| `config.py`, both `.env.example` files, `pyproject.toml`, `uv.lock` | Validated shared configuration, auth paths, chosen client pin and retirement diagnostics. |
| Root `docker-compose.yml`, new `docker-compose.dev.yml`, `docker-compose.prod.yml`, `deploy/nats/`, `scripts/openship-stack` | Compatible dev entry point, hot reload, standalone production services, broker configuration and environment-aware operations. |
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
- [ ] **3B2 — Broker consumer and supervision:** complete the ordered slices
  below. Production remains on the legacy entry point until 3B2f.

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

- [ ] **3B2e — Independent recovery, health and exhaustion:** run availability,
  Connector expiry and lease recovery outside run slots; add bounded health
  RPC; persist failure/quarantine before acknowledging exhausted deliveries;
  preserve healthy live owners and require explicit retention enqueue.

- [ ] **3B2f — Production entry switch:** remove `claim_next` discovery and idle
  polling from the production entry only after 3B2b–3B2e are qualified. Keep no
  automatic polling fallback.

- [ ] Add failing unit and PG/broker cases for every spec delivery-state
  branch, especially inactive/closed gates, redelivery after dedup expiry,
  old-generation arrival after resume, another live owner and stale fences.
- [ ] Reuse `process_job` through per-slot execution objects in a bounded
  executor. Test two slots progressing while another fixture blocks and
  background slots are full; do not execute synchronous work on the broker/API
  loop. Keep `model_interrupted`/`execution_interrupted` behavior.
- [ ] Commit outcomes/waits before confirmed ACK; requeue plus new generation
  plus outbox is atomic. Test crashes before/after model/tool dispatch and
  after result commit.
- [ ] Implement quarantine ledger/notices, exhaustion recovery and guarded
  replay, including failure before quarantine confirmation. Add explicit
  `enqueue-retention --dry-run` task arguments without changing direct CLI.
  Test MaxDeliver reached while a healthy long-running owner still holds its
  lease: retain a durable observation handoff until outcome or lease expiry;
  never fail or replay the effect based on transport count alone.
- [ ] Verify consumer, recovery, tool safety and provider worker suites plus
  real broker crash cases. Check executor/checkpointer/DB resources close;
  review and commit in focused independently verified slices.

## Task 4 — Bridge replay and live SSE through independent API consumers

**Depends on:** Tasks 1–3. **Files:** fanout, events routes/service, API
lifespan/readiness, event tests and browser regression tests.

**Interfaces:** `async subscribe_conversation(scope: AuthorizedScope, after_sequence: int) -> AsyncIterator[EventRead]`;
`resolve_event_cursor(db, scope: AuthorizedScope, header: str | None, query: int) -> CursorDecision`.
Reuse the existing event projection; no broker types leak to the web client.

- [ ] Add failing `test_messaging_sse.py` and real two-API tests: commit during
  subscription, replay/live overlap, out-of-order notices, late old events,
  UUID/numeric headers, foreign-conversation UUID, pruned history and overflow.
- [ ] Implement subscribe/flush-before-high-water, bounded replay, dedup and
  gap repair with short-lived off-loop sessions. Enforce 256-notice client
  queues; emit `snapshot.required` and close on unsafe overflow.
- [ ] Remove the one-second DB event polling loop. Keep 15-second heartbeat,
  HTTP auth/snapshot routes and event UUID `id`. Broker disconnect closes
  streams for existing browser recovery; test reopened clients on both APIs.
- [ ] Add readiness detail and two-second health RPC with source-owned reply
  validation; timeout/no-responder cannot repeat a run. Prove slow work does
  not block history, auth, cancel or readiness requests.
- [ ] Verify event suites and `pnpm test:run`, `pnpm typecheck`, affected
  Playwright journeys; trace idle SSE SQL and review/commit.

## Task 5 — Build resumable cutover, rollback and inspection commands

**Depends on:** Tasks 1–4. **Files:** messaging migration/CLI, retention,
populated PG fixtures and `tests/integration/test_messaging_cutover.py`.

**Interfaces:** `plan_cutover(db, *, cutover_id: UUID) -> MigrationReport`
is read-only; `apply_cutover(db, *, cutover_id: UUID, batch_size: int) -> MigrationReport`
is resumable. `plan_rollback(db, *, cutover_id: UUID) -> MigrationReport`
requires all executable work to have a known safe old-runtime disposition.

- [ ] Add failing fixture tests for queued/terminal/live/expired/dispatched/
  waiting runs, duplicate jobs, mixed legacy outbox rows, missing retained
  event notifications and pruned event references. Inject interruption after
  each batch, rerun and assert stable counts, IDs, sequences and config.
- [ ] Implement `cutover preview|apply|verify`, gate controls and
  `rollback preview|apply` with exact reports defined in the runbook. Refuse
  old live owners, wrong installation/epoch or incomplete dispositions.
- [ ] Add retention rules for unpublished event references and delivery
  ledgers. Verify no cleanup loses an unpublished command or changes current
  event/message retention policy.
- [ ] Prove rollback preserves additive schema and stops/fences new delivery;
  reject maintenance/uncertain work until explicitly resolved. Verify a later
  forward cutover and broker reconstruction mint new dispatch generations and
  identities, while rerunning the same cutover ID does not. Closed execution
  gates stop normal pulls; smoke tests use a separate disposable synthetic
  namespace. Review and commit.

## Task 6 — Package all three supported deployment modes

**Depends on:** Tasks 0–5. **Files:** deployment/Compose/image/config files in
the map, READMEs/runbooks and `tests/test_messaging_packaging.py`.

- [ ] First add failing packaging checks: standalone prod does not inherit
  dev ports, reload, source mounts, sample DB passwords or build steps;
  dev/local preserve service/volume/storage identities and entry points.
- [ ] Implement the runbook's `scripts/openship-stack` interface, pinned
  broker configurations, role auth initialization/rotation, disposable
  bootstrap job and real JetStream readiness probes. Initialization refuses
  overwrite; normal start never generates new installation IDs/keys.
- [ ] Keep root `docker-compose.yml` as dev default; add hot-reload override
  and standalone production file with locked API/web images, resource/log
  limits, restart/grace behavior, private broker/DB, TLS and protected auth
  mounts. Include relay and independent worker readiness.
- [ ] Repair API health probe without assuming curl; align Node build with
  documented support, freeze dependency installation and exclude secrets,
  `.env`, local artifacts and virtualenvs from image build contexts. Ignore
  `.env.prod` and generated backup/credential files in version control.
- [ ] Add native localhost instructions, examples for both env files and
  production config, startup/dependency order, broker outage/full-disk
  behavior, non-destructive stop, upgrades, backup, restore and rollback.
- [ ] Preserve the user's existing native hot-reload workflow: Vite with
  `pnpm run dev --host 0.0.0.0`, the API with Uvicorn `--reload`, and the
  worker (plus relay) with Watchdog `watchmedo auto-restart`; verify the dev
  Compose override watches mounted source and restarts the matching services.
- [ ] Verify `docker compose config --quiet` for dev/default, dev/override
  and standalone prod, then build and cold-start each declared arrangement.
  Test image probes, actual hot reload, worker/relay restart and existing
  artifact/Connector mounts. Scan examples/images for credentials. Commit.

## Task 7 — Qualify compatibility, failures, capacity and operator recovery

**Depends on:** Task 6. **Files:** integration/full-stack fixtures and tests,
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

Tasks 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 are the main dependency chain. Packaging
drafts can be prepared alongside runtime work after Task 0, but cannot pass
without real scripts and built images. Producers can be migrated in small
reviewed groups within Task 1; no group may silently omit its events.

Each implementation slice includes tests for observable behavior, a focused
review and a working commit. Use the repository `git-workflow` skill for
worktrees, file-based messages and required Rayloc scans. Do not copy inline
commit-message examples from general skills. Keep deployment and destructive
actions confined to disposable fixtures until the production cutover is
explicitly authorized.
