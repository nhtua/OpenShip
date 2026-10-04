# Phase 2 Durable Workspace and Observable Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed-in user can reconnect or restart OpenShip and recover completed messages plus the honest state of every chat run.

**Architecture:** Keep FastAPI and PostgreSQL as the control plane. A send command commits the user message, run, queue record, and semantic event together; a separate worker owns model execution through a fenced lease and a PostgreSQL LangGraph checkpointer. The browser reconstructs state from a snapshot and replays durable events by cursor.

**Tech Stack:** Existing Python 3.11+/FastAPI/SQLAlchemy/Alembic, PostgreSQL, Vue 3/TypeScript/Pinia; add compatible, lockfile-pinned `langgraph` and `langgraph-checkpoint-postgres` with Psycopg 3 for checkpoints. Keep the current Psycopg 2 SQLAlchemy connection initially. No Temporal, broker, cloud connector, or sandbox in this phase.

**Spec:** [Phase 2 in roadmap](../../roadmap.md), [architecture §§10, 11, 14–15](../../architects.md), and [technology candidates](../../technology-candidates.md). The roadmap is still marked proposed; this plan is a reviewable proposal, not authorization to implement it.

## Global Constraints

- The initial user experience remains a personal workspace; add one persisted default project per existing or new user and backfill existing conversations. Project selection is server validated, not trusted from a JWT claim.
- Preserve existing authentication and public chat routes while migrating the web client. The old POST stream becomes a compatibility adapter over the durable command; disconnection must never decide run completion.
- A conversation is the UI workspace; a run is one model response attempt; a LangGraph `thread_id` is a separate stable identifier stored on the run. Never use a conversation ID as a thread ID.
- Completed user and assistant messages, run state, usage, and semantic events are durable. Token deltas may be transient and must never be the sole copy of an answer.
- Checkpoints are framework state, not the authoritative run projection or event log. Use the maintained PostgreSQL saver with strict deserialization; never pickle untrusted data or build a homemade saver.
- Phase 2 makes no external mutations. An in-flight model request at worker loss may fail visibly; no promise of exactly-once provider calls or recovered token streams.
- Every read and write path must authorize the conversation's project membership and return 404 for inaccessible object IDs. No auto-creation for a valid but unknown conversation ID.
- All migrations must upgrade a populated Phase 1 database and have a tested downgrade or an explicit irreversible-data rationale. Do not create tables at API request time.
- Use finite per-run budgets (input length, model timeout, output limit), finite SSE buffers, and bounded retention. Do not place model credentials, tokens, or raw prompts in logs/events.

## Current-State Corrections and Decisions

The API currently stores users, conversations, and messages; `GET /api/workspace` only synthesizes a user-scoped workspace. `POST /api/chat/{conversation_id}/messages` performs the model call in the response generator, buffers all chunks in memory, and saves the assistant message only at the end. The browser stores a local optimistic message and reads one non-reconnectable POST stream. The PoC LangGraph code lives separately in `apps/agent/` with `InMemorySaver`; it is not the browser-chat runtime. Existing API tests use SQLite, so PostgreSQL-specific behavior needs a new integration lane. `docker-compose.yml` currently names PostgreSQL 15; use the version chosen by the project at implementation time and test the actual Compose image instead of assuming PostgreSQL 16.

The selected approach adds a minimal chat graph to the API runtime. Keep the PoC workflow builder separate until Phase 7, when workflow execution is in scope. In Phase 2, the graph records input preparation and a model step; the application database remains authoritative for status, message publication, and delivery. The maintained [PostgreSQL checkpointer](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/README.md) requires setup and a separate Psycopg 3 connection; its strict msgpack option should be enabled before loading stored state. LangGraph's checkpoint boundary does not make an in-flight model request replay-safe, so lease recovery must choose a visible status rather than silently calling the provider again.

## File Map and API Contract

| Area | Create or modify | Responsibility |
| --- | --- | --- |
| Schema | `apps/api/src/openship/workspace/models.py`, `chat/models.py`, `runs/models.py`, `events/models.py`, `database/models.py`, `alembic/versions/002_*.py` | Project ownership, conversation backfill, runs, queue/fence, event sequence/outbox, usage; indexes and constraints. |
| Authorization | `auth/routes.py`, `auth/service.py`, `workspace/service.py`, `workspace/routes.py`, `chat/routes.py` | Reuse authenticated user dependency; resolve an owned project/conversation in one place. |
| Commands | `chat/commands.py`, `runs/routes.py`, `runs/service.py` | Idempotent submit/cancel and scoped run/snapshot reads. |
| Execution | `runs/queue.py`, `runs/worker.py`, `runs/graph.py`, `runs/checkpoints.py`, `chat/llm.py` | Claim, heartbeat, fence, execute, account usage, and reconcile. |
| Events | `events/service.py`, `events/routes.py`, `events/retention.py` | Transactional event writes, replay cursor, snapshot boundary, pruning. |
| App/package | `main.py`, `config.py`, `pyproject.toml`, `uv.lock`, `Dockerfile`, root `docker-compose.yml`, `apps/api/README.md` | Router, dependencies, worker process, deployment and runbook. |
| Browser | `apps/web/src/stores/chat.ts`, `services/api.ts`, `types/index.ts`, `views/ChatView.vue`, `components/chat/RunCard.vue` | Submit command, hydrate snapshot, reconnect/dedupe, display run status and usage. |

Public contract proposed for the new client:

- `POST /api/conversations` creates a conversation in the current owned project (existing path retained).
- `POST /api/conversations/{conversation_id}/runs` accepts `{content, client_request_id}`; returns `202 {run_id, conversation_id, status, graph_thread_id}`. A unique `(conversation_id, client_request_id)` constraint makes resubmission idempotent. An unknown/foreign conversation returns 404; a second active run in the same conversation returns 409. The browser creates a conversation first instead of submitting to `new`.
- `GET /api/conversations/{conversation_id}/snapshot` returns `{conversation, messages, runs, cursor}` from one consistent database view. Runs expose status, timestamps, error code, and nullable usage; no secrets or checkpoint payloads.
- `GET /api/conversations/{conversation_id}/events?after=<sequence>` is an authenticated SSE stream with `id: <sequence>` and versioned JSON events. `Last-Event-ID` is accepted as an alternative cursor. The snapshot's cursor is the start point for a fresh subscription. A cursor older than retained history emits `snapshot.required` and closes; the browser fetches a snapshot and reconnects. Invalid/future cursors return 422.
- `GET /api/runs/{run_id}` and `POST /api/runs/{run_id}/cancel` use the same project authorization. Cancel is idempotent and returns the durable status; it is a request to stop, not a claim that the provider stopped instantly.
- The old `POST /api/chat/{conversation_id}/messages` stays during transition, delegates to the same submit path, and translates durable events into its existing SSE shapes (`conversation_created`, `chunk`, `complete`, `error`, `[DONE]`). Its client disconnect does not cancel the run. Remove it only in a later, separately approved compatibility change.

Persisted event envelope: `{schema_version: 1, id: UUID, sequence: int, project_id: UUID, conversation_id: UUID, run_id: UUID|null, type: string, occurred_at: ISO8601, actor_id: UUID|null, payload: object}`. Persist `message.created`, `run.queued`, `run.started`, `run.paused`, `run.failed`, `run.cancelled`, and `run.succeeded`; the terminal event carries message ID and usage reference, not duplicate message text. Assign sequence under a conversation row lock and enforce `UNIQUE(conversation_id, sequence)` and `UNIQUE(event_id)`.

## Review Focus

1. A client retries a POST after losing its 202 response: the idempotency key must return the original run and create no second user message or provider call (Task 2).
2. A worker stalls beyond its lease and later returns: the stale fence must prevent final message, status, and event writes (Task 4).
3. An SSE cursor falls behind pruning: the server must request a snapshot and the browser must converge without repeated messages (Tasks 5 and 6).
4. A foreign user guesses a conversation, run, or stream URL: every path must return 404 without leaking existence or event payloads (Tasks 1, 2, 5).
5. The provider ends after partial output or without usage: the run must end in an honest visible state with nullable usage, never a fabricated completion or token count (Tasks 3 and 7).

## Task 1 — Migrate Phase 1 data into project-scoped durable records

**Files:** Schema rows in the file map; `apps/api/tests/integration/test_phase2_migration.py`; update `apps/api/tests/conftest.py` only to add a PostgreSQL fixture alongside existing SQLite unit tests.

**Interfaces:** `Project(id, owner_user_id, name)`, `Conversation.project_id`, `Message.run_id?`, `Run(id, conversation_id, project_id, graph_thread_id, status, attempt, fence, provider_started_at?, timestamps, error_code?, usage?)`, `RunJob(run_id, available_at, lease_until?, owner_id?, fence)`, `Conversation.next_event_sequence`, `Event`, `Outbox`. Use UUIDs, foreign keys, status checks, and indexes on project/conversation, queue availability, events by conversation/sequence, and terminal-run retention. Messages are unique by `(run_id, role)` for chat runs; historical messages have null run IDs.

- [ ] **Test first:** Seed the exact Phase 1 schema with two users and conversations/messages; apply the new Alembic revision; assert one owned default project per user, all old conversations/messages retain IDs and content, and foreign-project joins fail. Verify `alembic upgrade head` on an empty database too.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/integration/test_phase2_migration.py -q` must fail because the new schema is absent.
- [ ] **Implement:** Add models and a hand-written migration that creates project and run/event tables, backfills projects/conversation.project_id, then makes `project_id` non-null. Use named constraints and an explicit downgrade order. Add registration-time default-project creation in the same transaction as user creation; do not create projects on login.
- [ ] **Run green:** Re-run the migration tests against PostgreSQL, then `cd apps/api && uv run alembic upgrade head` against a disposable Compose database. Expected: no lost Phase 1 rows and all new constraints present.
- [ ] **Commit:** `feat: add project and durable run schema`.

## Task 2 — Make send and cancel authenticated, transactional commands

**Files:** `workspace/service.py`, `chat/commands.py`, `chat/routes.py`, `runs/routes.py`, `runs/service.py`, `main.py`; `apps/api/tests/test_run_commands.py`, `apps/api/tests/integration/test_run_commands_pg.py`.

**Interfaces:** `require_owned_conversation(db, user_id, conversation_id) -> Conversation`; `submit_turn(db, user_id, conversation_id, content, client_request_id) -> Run`; `request_cancel(db, user_id, run_id) -> Run`. One DB transaction persists user message, queued run/job, initial event, and outbox row. `client_request_id` is a UUID generated once per browser send, accepted again for retry; same key with changed content returns 409. Keep one active run per conversation with a database-enforced partial unique index.

- [ ] **Test first:** Cover no auth, foreign/unknown ID (404), empty/oversized content (422), duplicate command replay, same-key/different-content conflict, concurrent sends (one 202 and one 409), and cancel replay. Assert failed transactions leave no orphan run, message, job, or event. Include the Review Focus retry and cross-project cases.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/test_run_commands.py tests/integration/test_run_commands_pg.py -q` must fail at the new contract.
- [ ] **Implement:** Centralize project authorization; make old chat stream an adapter of this command. Remove the current behavior that creates a new conversation when a valid but unknown ID is supplied. On cancel, record `cancelling` or terminal `cancelled` in the same transaction as its event; a worker observes the state.
- [ ] **Run green:** Re-run the targeted tests. Existing `test_chat_send.py` and `test_chat_conversations.py` must be updated to assert the new durable semantics and old SSE envelope compatibility.
- [ ] **Commit:** `feat: submit chat turns as durable scoped runs`.

## Task 3 — Add a PostgreSQL-checkpointed chat graph and bounded model step

**Files:** `runs/graph.py`, `runs/checkpoints.py`, `chat/llm.py`, `config.py`, `pyproject.toml`, `uv.lock`; `apps/api/tests/integration/test_chat_checkpoints_pg.py`, `apps/api/tests/test_model_step.py`.

**Interfaces:** `build_chat_graph(checkpointer) -> CompiledStateGraph`; `execute_model_turn(run_id, thread_id, fence) -> ModelResult`. State carries only serializable chat inputs/output and run ID, no API key. Use a stable run-owned `thread_id` and the maintained `PostgresSaver`/`AsyncPostgresSaver` appropriate to the worker execution model. Run setup as an install/migration step, never on a request. Enable `LANGGRAPH_STRICT_MSGPACK=true`; do not enable pickle fallback. Pin a compatible pair of LangGraph/checkpointer versions in `uv.lock` after a smoke test.

- [ ] **Test first:** A graph run creates a checkpoint readable from a new process/connection with the same thread ID; different runs cannot read each other's thread by application API. Verify a provider timeout, error, and partial stream leave no completed assistant message; missing credential yields a safe configuration error. Verify absence of usage remains null.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/test_model_step.py tests/integration/test_chat_checkpoints_pg.py -q` must fail before graph wiring.
- [ ] **Implement:** Move the Phase 1 model call out of the HTTP generator into the worker graph. Use a bounded request and output budget. Persist only a complete cleaned assistant answer after the model step; transient deltas may be delivered live but are discarded on reconnect. Keep the current title extraction behavior only if it is covered by a test and the cleaned text is what gets stored.
- [ ] **Run green:** Re-run targeted tests with a fake provider and real PostgreSQL saver. Verify checkpoint setup and restart from a second process. Expected: no model credential in checkpoint rows.
- [ ] **Commit:** `feat: checkpoint chat graph in PostgreSQL`.

## Task 4 — Add fenced queue ownership and recovery

**Files:** `runs/queue.py`, `runs/worker.py`, `runs/service.py`, `runs/checkpoints.py`, `config.py`, `Dockerfile`, root `docker-compose.yml`; `apps/api/tests/integration/test_worker_recovery_pg.py`.

**Interfaces:** `claim_next(worker_id) -> Claim|None` returns `(run_id, fence, lease_until)` using `FOR UPDATE SKIP LOCKED`; `renew(run_id, fence) -> bool`; `finalize(run_id, fence, result) -> bool`; `reconcile_expired() -> int`. Every status/message/event write from a worker includes `WHERE run_id=:id AND fence=:fence AND status='running'`. Increment fence on each claim. Give each claim a separate LangGraph `checkpoint_ns=attempt-{fence}` under the run's stable `thread_id`; only the current fenced namespace is readable as the run's active checkpoint. An obsolete worker may leave bytes in its old namespace, but cannot publish them as current state. Record `provider_started_at` durably immediately before calling the provider.

- [ ] **Test first:** Two independent PostgreSQL connections race to claim a job; only one wins. A stale worker attempts heartbeat, checkpoint, and finalize after lease expiry; heartbeat/finalize are refused and any old-namespace checkpoint is ignored. Crash injection covers (a) before claim commit: remains queued, (b) after claim but before `provider_started_at`: reconciles to queued, (c) during provider call: becomes `failed` with `model_interrupted`, never `succeeded`, and (d) after assistant/event transaction commit: remains succeeded exactly once. Cancelled runs cannot be claimed or finalized.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/integration/test_worker_recovery_pg.py -q` must fail before queue implementation.
- [ ] **Implement:** Add a worker entry point and one Compose worker service. Bound lease/heartbeat intervals by configuration and use database time for comparisons. Reconciler handles expired leases; it may requeue only work proven not to have started a provider call. Ambiguous in-flight model work becomes visibly `failed` and requires a new user command to retry. Document that provider billing may still occur for an interrupted call.
- [ ] **Run green:** Re-run race/crash tests with two worker processes. Observe a queued turn complete through Compose while the browser is disconnected.
- [ ] **Commit:** `feat: run chat turns with fenced worker leases`.

## Task 5 — Persist semantic events and serve snapshot/replay

**Files:** `events/service.py`, `events/routes.py`, `events/retention.py`, `runs/routes.py`, `chat/routes.py`, `main.py`; `apps/api/tests/integration/test_events_pg.py`, `apps/api/tests/test_event_routes.py`.

**Interfaces:** `append_event(db, conversation_id, run_id, type, payload, actor_id) -> Event` uses the caller's transaction and conversation sequence lock; `read_snapshot(db, user_id, conversation_id) -> Snapshot`; `stream_events(user_id, conversation_id, after_sequence)` reads committed events in order. Outbox rows contain event ID; a dispatcher or poller may notify SSE listeners at least once, and consumers dedupe by ID. Database polling is an acceptable Phase 2 fallback when notifications are lost. Snapshot captures the maximum committed sequence in the same read transaction as messages/runs.

- [ ] **Test first:** Assert run status and event commit atomically; concurrent event writers produce distinct ordered sequences; stream resumes after process restart; repeated outbox delivery creates no duplicate event; slow listener does not block worker. Exercise malformed/future cursor, old cursor after retention, no auth, and foreign project/event URL (404). Ensure event payloads omit prompts, secrets, and raw provider errors.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/test_event_routes.py tests/integration/test_events_pg.py -q` must fail.
- [ ] **Implement:** Write versioned durable events for meaningful transitions. Use the `id:` SSE field and heartbeat comments; accept bearer auth through fetch because native `EventSource` cannot use the existing Authorization header. For a pruned cursor, emit `snapshot.required` then close. Make current-state snapshot authoritative over event replay and do not persist every token chunk.
- [ ] **Run green:** Re-run targeted tests and a manual curl/fetch replay after API restart. Expected: identical state after replay or snapshot refresh.
- [ ] **Commit:** `feat: add durable events and reconnect snapshots`.

## Task 6 — Migrate the browser to durable runs and run cards

**Files:** `apps/web/src/stores/chat.ts`, `services/api.ts`, `types/index.ts`, `views/ChatView.vue`, `components/chat/RunCard.vue`; `apps/web/tests/ChatStore.test.ts`, `ChatSSE.test.ts`, `ChatView.test.ts`, `tests/e2e/auth-chat.spec.ts`.

**Interfaces:** `sendMessage()` creates a stable client request UUID, posts the command, then hydrates/subscribes; `loadConversation()` fetches snapshot before subscribing from its cursor. Store messages keyed by server message ID and runs by run ID, apply each event ID once, and keep the last committed cursor per selected conversation. A run card shows queued/running/paused/failed/cancelled/succeeded, start/end time, usage when known, and a safe reason/action for interruption.

- [ ] **Test first:** Simulate a dropped 202 response and command retry, SSE duplicate/out-of-order/reconnect, snapshot-required, switching conversations during a stream, and a tab reload after a partial model answer. Assert no duplicate user or assistant messages and no false completed card. Add an end-to-end test that closes/reopens the page while a fake delayed provider runs and confirms eventual state.
- [ ] **Run red:** `cd apps/web && pnpm test --run` and the focused Playwright test must expose current store behavior.
- [ ] **Implement:** Replace optimistic ID substitution with server IDs from the command/snapshot; keep a pending local draft keyed by request ID until the server confirms it. Parse SSE frames across arbitrary fetch chunks; aborting a browser fetch only closes that subscription. Resume from last cursor, then fetch snapshot on gap/expiry. The cancel button calls the command endpoint and waits for durable state.
- [ ] **Run green:** `cd apps/web && pnpm test --run` and `cd apps/web && pnpm exec playwright test tests/e2e/auth-chat.spec.ts` pass against a real API/worker fixture.
- [ ] **Commit:** `feat: reconnect chat runs and show durable progress`.

## Task 7 — Add honest usage, retention, logs, and operations guidance

**Files:** `runs/service.py`, `chat/llm.py`, `events/retention.py`, `config.py`, root `docker-compose.yml`, `apps/api/README.md`, root `README.md`; `apps/api/tests/test_usage_retention.py`, `apps/api/tests/integration/test_retention_pg.py`.

**Interfaces:** Usage fields `input_tokens?`, `output_tokens?`, `total_tokens?`, `provider_model?`, `estimated=false`; values come only from provider metadata. Configure default completed-event retention of 30 days, terminal checkpoint retention of 30 days, and structured operational log retention of 7 days (or deployment log-driver equivalent). Keep messages and run summaries until an explicit delete policy exists. Do not prune active/waiting runs or a checkpoint needed to interpret them. A retention watermark drives the stale-cursor response.

- [ ] **Test first:** Provider usage is recorded once on success, remains null when omitted, and is not double-counted on event redelivery. Retention removes only eligible terminal event/checkpoint data while snapshot still shows messages and run outcome. A simulated expired cursor requests snapshot. Structured logs include request/project/conversation/run/event IDs and state transition without access token, model key, prompt, or raw response.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/test_usage_retention.py tests/integration/test_retention_pg.py -q` must fail.
- [ ] **Implement:** Normalize usage from the existing provider adapter only when returned; mark incomplete usage visibly. Implement retention as a worker maintenance command with dry-run metrics. Use the checkpointer's supported whole-thread deletion for terminal runs, rather than deleting internal checkpoint rows ad hoc. Add health/readiness distinction for API database and worker backlog/lease age, plus recovery/backup instructions.
- [ ] **Run green:** Re-run targeted tests and the retention dry run on disposable Compose data. Expected: active state untouched and no secret-bearing logs.
- [ ] **Commit:** `feat: account for runs and bound durable history`.

## Final Verification and Phase 2 Acceptance

Run these gates after all tasks, with a populated database migrated from Phase 1 and a fresh installation:

| Gate | Procedure | Pass condition |
| --- | --- | --- |
| Browser reconnect | Start a delayed response, close/reopen tab, reopen same conversation. | Completed messages and run state match server snapshot; one visible copy per message/transition. |
| API restart | Restart API after a committed user message and while worker runs. | New API serves the same snapshot/cursor and completes or reports a visible interruption. |
| Worker restart | Kill worker before claim, during model request, and after final commit in separate trials. | Queued work recovers; in-flight request becomes visibly failed; committed result remains unique. |
| Fencing | Run two workers and force lease expiry/stale completion. | At most one fenced writer can publish a terminal transition or assistant message. |
| Security | User A and B own separate projects; guess all conversation/run/event/snapshot IDs. | Every foreign read and command is denied with 404; no event or payload leaks. |
| Compatibility | Exercise Phase 1 auth, conversation list, and legacy POST SSE routes. | Existing client-facing contract works while new client uses command/replay path. |
| Operations | Install/restart with Compose; run migrations and retention dry run. | No undocumented steps; worker/readiness status and recovery are documented. |

Required commands: `cd apps/api && uv run pytest -q` (SQLite unit lane), PostgreSQL integration suite with Compose database, `cd apps/web && pnpm test --run`, `cd apps/web && pnpm exec playwright test tests/e2e/auth-chat.spec.ts`, and build/start/stop the root Compose stack. CI should run the PostgreSQL lane because SQLite cannot prove row locking, JSONB, migrations, or checkpointer behavior. Capture test outputs and manual demo observations before asking the user to **accept, revise, or stop** Phase 2.

**Exclusions:** No tool execution, cloud access, workflow editor, approvals, exactly-once model/provider calls, or recovery of partial model tokens. The run card may show an interrupted request and offer a new attempt; it must not claim the old provider request was cancelled if that cannot be established.
