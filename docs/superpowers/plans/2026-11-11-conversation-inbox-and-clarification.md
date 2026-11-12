# Conversation Inbox and Clarification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **For this work, the user selected inline execution without subagents: use executing-plans.**

**Goal:** Let people keep sending messages during active runs, route them durably to an open clarification or a FIFO follow-up queue, and let the agent judge clarification sufficiency without implicit approval.

**Architecture:** Add a conversation-locked message inbox alongside existing messages/runs. Only the server binds an accepted message to an open clarification or queues it for later; existing fenced workers/outbox evaluate replies and promote one queued turn after the current run ends. Snapshot/SSE and the browser render the committed disposition rather than deciding it locally.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL/SQLite tests, LangGraph v5, existing run job/outbox, Pydantic, Vue 3/Pinia/TypeScript, pytest/Vitest.

**Spec:** [Conversation inbox and clarification design](../specs/2026-11-11-conversation-inbox-and-clarification-design.md). Read it before implementing this plan.

**Status:** Plan approved; implementation in progress inline in the current isolated worktree, with no subagents, push, PR, or merge. Tasks 1–3 are committed and their hooks passed. Task 4 is in progress; Tasks 5–8 remain open.

## Global Constraints

- A clarification keeps the **same request ID, revision, digest, authorization and original 24-hour expiry** across replies. It is resolved only by an explicit model sufficiency result, never by receipt of text alone.
- Text cannot confirm a plan, select recovery, resume configuration, or authorize/re-dispatch an uncertain tool operation; existing decision buttons and fences remain authoritative.
- One active run per conversation. A queued message has no executable run until FIFO promotion; an older queued message always wins over a new send.
- Submission IDs are stable across retries; duplicate identical requests return the original disposition, conflicting payloads return 409. Owner/read-only/archive checks precede disclosure.
- Capture omitted mode/profile defaults at inbox acceptance; retain the exact submitted selection. A later account preference change does not rewrite the captured mode. Validate current permissions and build immutable execution snapshots at promotion; never silently substitute a new default or unavailable profile.
- Keep historical task events and old graph versions readable. Missing provider usage/context counts remain missing; no estimates. Do not remove historical messages by text matching.
- Preserve existing submission size limits (text at most **10,000 characters**, no blank text), task/model budgets, 24-hour waits, worker fencing, outbox durability, and current tool policy.
- Use credential-free API/web verification only. Do not run PG/`postgres`/`full_stack`/`provider_live`/`jetstream` gates or `git clean -fdx`; do not edit tracked files while a commit hook or verification lane runs.
- For every commit: validate the changed slice, put the exact message in a task-specific `/tmp/` file, review it, call `rayloc scan` on that file in a **separate call immediately before** `git commit --file`, and scan staged content. Never use `git commit -m`.

## Review Focus

1. A whitespace-only or duplicate send during an open clarification: reject blank content; an exact retry records one reply and never schedules two evaluations (Tasks 2 and 4).
2. Two sends during model assessment, one sufficient and one late: neither disappears or changes route; resolution checks the included high-water mark (Tasks 4 and 5).
3. An old, read-only, or archived browser submits a button/text concurrently with cancellation or expiry: no approval, new run, leaked request state, or trapped queued work (Tasks 2, 3, 4 and 7).
4. A default model/profile or connector changes before a queued turn is promoted: fail/block visibly under the originally accepted selection, never pick a new one (Tasks 2 and 3).
5. A completed run has queued messages but the worker dies before promotion: recovery starts only the oldest once; a legacy direct send cannot jump ahead (Task 3).

---

## File Structure and Ownership

| Files | Responsibility |
| --- | --- |
| `apps/api/alembic/versions/035_conversation_inbox.py` **new**, `runs/inbox/models.py` **new** | Additive inbox table, unique conversation/request and sequence indexes, immutable routing binding and bounded status fields. Existing migrations unchanged. |
| `runs/inbox/contracts.py`, `admission.py`, `promotion.py`, `projection.py` **new** | Request/response models; transaction-scoped intake; FIFO promotion/recovery; bounded public reads. Keep model inference out of admission. |
| `runs/routes.py`, `runs/service.py`, `runs/queue.py`, `runs/worker.py`, `runs/tasks/decisions.py`, `messaging/recovery.py`, `chat/lifecycle.py`, `workspace/projects.py` | Small integration points for inbox, worker lifecycle, archival, and no backlog bypass. Extract turn creation instead of copying validation/pinning code. |
| `runs/tasks/clarification.py` **new**, `runs/tasks/inference.py`, `runs/task_graph.py`, `runs/tasks/contracts.py` | Exact request-bound sufficiency assessment, high-water commit and graph re-entry. No new runtime scheduler. |
| `events/schemas.py`, `events/service.py`, `runs/tasks/projections.py` | Version-compatible request digest and inbox/request-update SSE and snapshot projections. |
| `apps/web/src/types/index.ts`, `services/api.ts`, `stores/chat.ts`, `stores/taskProgress.ts`, `views/ChatView.vue`, `components/chat/{ChatInput,TaskDecision,ChatStream}.vue` | Submission/store reconstruction, digest binding, active-run composer and honest timeline. |
| Targeted tests under `apps/api/tests/test_*inbox*.py`, `test_task_decisions.py`, `test_task_resume.py`, `test_task_wait_delivery.py`, and `apps/web/tests/{ChatInput,ChatView,TaskProgressStore}.test.ts` | Database/worker/web red-green and reload/race regression proof. |

### Task 1: Deliver the live decision digest without breaking historical events

**Files:** Modify `apps/api/src/openship/runs/tasks/decisions.py:863-885`, `apps/api/src/openship/events/schemas.py:28-42`, `apps/web/src/stores/taskProgress.ts:150-164`; test `apps/api/tests/test_task_decisions.py`, `apps/web/tests/TaskProgressStore.test.ts`.

**Interfaces:** `TaskDecisionRequestedEvent.expected_digest: str | None` (old persisted v1 event may omit it; all newly emitted events carry the persisted value). The web fold prefers `payload.expected_digest`, falls back to an existing review/diagnosis only for historical events, and does not enable a revision-0 request without either source or snapshot.

- [x] **Step 1 — failing API/web tests:** Added revision-0 event/digest and historical decode assertions and a web store binding/unbound regression.
- [x] **Step 2 — red:** API: 1 failed, 46 passed (missing event digest). Web: 1 failed, 427 passed (unbound live digest). The planned pnpm command ran all tests because of `--` argument handling.
- [x] **Step 3 — implementation:** Emit the stored digest, validate its SHA-256 format when present, and prefer the event field in the store. Do not synthesize a missing digest.
- [x] **Step 4 — green/commit:** API task tests: 47 passed; focused web test: 12 passed; API Ruff and web typecheck passed. Four-shard hook failed with 13 pool-exhaustion failures and 1,604 passes. Tracing identified retained `Lane` sessions from integration tests; moving its cleanup fixture to shared conftest fixed a regression (red: 1 failed, 1 passed; green: 2 passed). The eight-shard credential-free API lane passed 2,064 tests; four-shard selection shard 0 passed 405 tests (new tests changed shard assignments). Commit `dde2f20` passed all applicable hooks.

### Task 2: Persist and accept conversation messages under one routing lock

**Files:** Create `apps/api/alembic/versions/035_conversation_inbox.py`, `apps/api/src/openship/runs/inbox/{models,contracts,admission}.py`, `apps/api/tests/test_conversation_inbox_admission.py`; modify `apps/api/src/openship/runs/{models,routes,schemas}.py`, `apps/api/src/openship/events/schemas.py`.

**Interfaces:** `MessageAdmissionRequest` retains every `TurnSubmitRequest` field, with the same stable `client_request_id`; `MessageAdmissionResponse(message_id, disposition: 'clarification'|'queued', status: 'attached'|'queued'|'promoted'|'failed', run_id?, request_id?)`. `submit_message(db: Session, *, user_id: UUID, conversation_id: UUID, request: MessageAdmissionRequest) -> MessageAdmissionResponse` does not commit. `ConversationInboxEntry` owns `message_id`, `conversation_id`, `client_request_id`, canonical request digest, monotonic event sequence, immutable disposition, `run_id?`, `decision_id?`, selected request JSON, and status. `message.admitted` carries the IDs/disposition/status but not full content or selection. Endpoint: `POST /api/runs/{conversation_id}/messages` (202). An attached clarification uses text only; other request fields do not change its run or authorize tools.

- [x] **Step 1 — admission tests:** Added replay, blank/conflict, clarification binding, concurrent sends, foreign/archive, non-clarification wait, backlog ordering and frozen-default coverage.
- [x] **Step 2 — red:** Initial test run: 3 failed and 1 passed for missing inbox model/route/admission. Archive replay regression also failed before its fix.
- [x] **Step 3 — implementation:** Added migration, contracts, transactional admission and authenticated endpoint; preserved the old `/turns` behavior. Replay and fresh admission follow existing ownership and archive rules.
- [x] **Step 4 — green/commit:** Admission and existing turn tests: 24 passed; Ruff passed. The first eight-shard credential-free lane exposed a hard-coded old migration-head assertion (1 failed, 2,077 passed); after updating that assertion, the lane passed 2,078 tests across eight shards. Commit `24479e4` passed applicable hooks; the admission suite then passed 14 tests.

### Task 3: Promote FIFO turns through existing run creation, even after crashes

**Files:** Create `apps/api/src/openship/runs/inbox/promotion.py`, `apps/api/tests/test_conversation_inbox_promotion.py`; modify `apps/api/src/openship/runs/{service,queue,worker}.py`, `apps/api/src/openship/runs/tasks/decisions.py`, `apps/api/src/openship/messaging/recovery.py`, `apps/api/src/openship/chat/lifecycle.py`, `apps/api/src/openship/workspace/projects.py` if project archival needs a queue-state transition.

**Interfaces:** `promote_next_message(db: Session, *, conversation_id: UUID) -> Run | None` runs inside the caller's transaction and the conversation lock. `reconcile_queued_messages(db: Session, *, batch_size: int = 100) -> int` reuses the installed recovery loop. Share an internal turn builder with `submit_turn`; it accepts a persisted user `Message` and frozen selection rather than creating a duplicate user message. `ConversationInboxEntry.status` becomes `promoted` or `failed` with a public reason; `message.status_changed` carries the message ID, status, run ID/reason (when present), not the full request.

- [x] **Step 1 — tests:** Added FIFO, recovery, idle admission, terminal outcomes, rejected/expired decisions, open waits, archive/restore scheduling, unavailable profile/connector, captured mode, direct chat/tool backlog, competing promoters and active-run context regressions.
- [x] **Step 2 — red:** Initial FIFO and recovery tests failed for the missing promotion module. Idle promotion, installed recovery, active-run context, cancellation, racing promoters and recovery starvation also failed before their respective changes.
- [x] **Step 3 — implementation:** Added no-commit promotion through the existing turn creation path, persisted safe failure reason, status events, idle/worker/cancel/restore and periodic recovery scheduling, FIFO backlog guard, and active context filtering. Rejection and expiry release the slot for recovery to promote.
- [x] **Step 4 — green/commit:** Focused promotion, admission, task admission and task wait suites: 63 passed; Ruff passed. Earlier full lane: 2,097 passed across eight shards. Commit `13154c1` passed hooks; post-commit promotion/task suites: 49 passed.

### Task 4: Keep clarification open while replies arrive and close the legacy bypass

**Files:** Create `apps/api/src/openship/runs/tasks/clarification.py`, `apps/api/tests/test_conversation_inbox_clarification.py`; modify `apps/api/src/openship/runs/inbox/admission.py`, `apps/api/src/openship/runs/tasks/{decisions,routes,projections}.py`, `apps/api/src/openship/runs/{queue,worker}.py`.

**Interfaces:** `enqueue_clarification_reply(db: Session, *, run_id: UUID, decision_id: UUID, message_id: UUID, now: datetime) -> None` schedules the same fenced run only when the bound reply high-water exceeds the assessed high-water; it never resolves the decision. `clarification_replies(db: Session, *, decision_id: UUID) -> tuple[ConversationInboxEntry, ...]` returns **all** ordered request-bound messages, including earlier insufficient replies. A fresh old `TaskDecisionCommand(action='answer')` returns an actionable 409 instructing the client to submit a message instead; previously committed historical idempotent replays remain readable. Button commands are unchanged.

- [x] **Step 1 — tests:** Added multiple replies, retry, in-flight claim, expiry/fencing, expired-request routing, typed-plan-control safety, fresh legacy refusal, historical replay and primary-message uniqueness coverage. Task 2 covers foreign ownership and Task 3 covers non-clarification waits and cancellation.
- [x] **Step 2 — red:** New tests initially failed on missing clarification module and on the legacy answer bypass; in-flight expiry and claim regressions also failed before their respective fixes. Multiple replies exposed the existing one-message-per-run-role unique index.
- [x] **Step 3 — implementation:** Added durable scheduling and ordered replies, refused fresh legacy answers, extended expiry fencing, selected the primary run message explicitly, and added migration `inbox037` to classify clarification messages while preserving primary message uniqueness.
- [ ] **Step 4 — green/commit:** Focused clarification and compatibility suites: 99 passed. The credential-free lane passed 2,110 tests across eight shards; commit and final task verification remain. Assessment itself is Task 5.

### Task 5: Evaluate accumulated clarification, then park or continue

**Files:** Modify `apps/api/src/openship/runs/tasks/{contracts,inference,projections}.py`, `apps/api/src/openship/runs/task_graph.py`, `apps/api/src/openship/runs/worker.py`, `apps/api/src/openship/events/schemas.py`; extend `apps/api/src/openship/runs/tasks/clarification.py`, `apps/api/tests/test_conversation_inbox_clarification.py`, `apps/api/tests/test_task_resume.py`.

**Interfaces:** `ClarificationAssessment(verdict: Literal['sufficient','insufficient'], follow_up: str | None)` requires bounded nonblank `follow_up` only for insufficient. `assess_clarification(stage, progress, *, question: str, replies: tuple[str, ...]) -> ClarificationAssessment` is a budgeted model turn. `apply_clarification_assessment(db: Session, claim: Claim, *, decision_id: UUID, included_sequence: int, assessment: ClarificationAssessment, now: datetime) -> bool` atomically checks the reply high-water mark and current fence; `False` means reload/re-evaluate, not lose the reply. Event: `task.decision_updated` carries the same request ID, follow-up prompt and persisted assistant message ID for insufficient, leaving historical `task.decision_requested` intact.

- [ ] **Step 1 — failing tests:** `test_insufficient_then_sufficient`: assert the first assessment leaves `decision.state == 'open'` and persists a follow-up, the second makes `decision.state == 'answered'` and continues the same `run_id` with all replies. `test_reply_during_assessment`: assert `apply_clarification_assessment(..., included_sequence=old_sequence, ...) is False` after a later send. Cover revision 0/existing plan, expiry, provider error, budget exhaustion, no extra tool dispatch and pinned model/usage.
- [ ] **Step 2 — red:** Run `cd apps/api && uv run pytest tests/test_conversation_inbox_clarification.py tests/test_task_resume.py -q` with deterministic fake model results; confirm expected failures.
- [ ] **Step 3 — implementation:** Evaluate an open clarification before the normal v5 assess/resume route, build bounded context from original intent/question/recorded replies, and make those replies available to the subsequently resumed graph even when the old execution snapshot pinned only the initial message. Validate verdict; commit follow-up+wait or resolution+continuation only if fence/high-water still matches. On transient failure keep the request and messages; on hard limits/expiry end visibly. Do not infer approval from model text.
- [ ] **Step 4 — green/commit:** Rerun both tests, `tests/test_task_wait_delivery.py`, Ruff changed API paths; commit with a scanned message file.

### Task 6: Project inbox/decision updates identically on SSE and reload

**Files:** Create `apps/api/src/openship/runs/inbox/projection.py`, `apps/api/tests/test_conversation_inbox_snapshot.py`; modify `apps/api/src/openship/events/{schemas,service}.py`, `apps/api/src/openship/runs/tasks/projections.py`, `apps/web/src/{types/index.ts,services/api.ts,stores/chat.ts,stores/taskProgress.ts}`, `apps/web/tests/TaskProgressStore.test.ts`.

**Interfaces:** `InboxMessageSummary(message_id, disposition, status, run_id?, request_id?, sequence, error_code?)`; `SnapshotResponse.inbox: list[InboxMessageSummary]`, bounded and paginated consistently with message history. `runsApi.submitMessage(conversationId: string, request: TurnSubmitRequest): Promise<{data: MessageAdmissionResponse}>`. The chat store merges idempotently by message ID and event sequence, and can resume event delivery across successive queued runs.

- [ ] **Step 1 — failing tests:** `test_snapshot_matches_inbox_events`: assert snapshot status/request bindings equal the last folded event for each message; `it('keeps the queue after run A finishes')`: assert message B remains queued, then transitions when promoted after A's terminal event. Test prompt update/reload, actionable digest, historical digestless event and `usage === null` when provider supplied none.
- [ ] **Step 2 — red:** Run `cd apps/api && uv run pytest tests/test_conversation_inbox_snapshot.py -q` and `cd apps/web && pnpm test:run -- TaskProgressStore.test.ts`; record failures.
- [ ] **Step 3 — implementation:** Add bounded inbox projection and event schemas, fold `task.decision_updated` only for matching open request, reconcile snapshot after reconnect, and keep web messages keyed by durable IDs/statuses. Preserve historical task cards and old events.
- [ ] **Step 4 — green/commit:** Rerun both tests, `pnpm typecheck` in `apps/web`, and Ruff for API changes; commit after verification.

### Task 7: Make the composer conversational, with safe plain-text clarification replies

**Files:** Modify `apps/web/src/views/ChatView.vue`, `apps/web/src/components/chat/{ChatInput,TaskDecision,ChatStream}.vue`, `apps/web/src/stores/chat.ts`; test `apps/web/tests/{ChatInput,ChatView}.test.ts`, plus relevant `apps/web/tests/ChatComponents.test.ts`.

**Interfaces:** Composer text always uses `chat.sendMessage` through `runsApi.submitMessage` for an existing conversation; display server disposition, not a locally guessed reply command. Explicit decision buttons remain `taskProgress.submitTaskDecision`. During an open clarification, tool attachment/automatic tool restrictions are not allowed to reinterpret or block plain text; the selected tool does not travel with the bound reply.

- [ ] **Step 1 — failing tests:** `it('sends text during streaming')`: assert the composer stays enabled and `submitMessage` returns `queued`; `it('sends plain text from an attached-tool composer during clarification')`: assert no tool command and one inbox send with text; `it('does not approve from typed text')`: assert no decision command during a plan wait. Test status change during send, duplicate response/retry, read-only/stale request, FIFO timeline, draft restoration and independent buttons.
- [ ] **Step 2 — red:** Run `cd apps/web && pnpm test:run -- ChatInput.test.ts ChatView.test.ts ChatComponents.test.ts`; confirm failing assertions.
- [ ] **Step 3 — implementation:** Remove composer dependence on `isStreaming` for ordinary text, route text to inbox, preserve stable retry IDs and drafts, show queued/clarification labels and approval blockers, and leave attached tool commands explicitly unavailable while a run owns the slot. Show server-bound message status and assistant follow-ups without duplicate optimistic entries.
- [ ] **Step 4 — green/commit:** Rerun those web tests, `pnpm typecheck`, `pnpm build` in `apps/web`; commit with a scanned message file.

### Task 8: Cross-slice regression and honest qualification

**Files:** Update this plan's status/evidence after each task and `docs/validations.md` only if its current format has a relevant entry; add targeted tests to existing files identified above when an uncovered boundary appears. No live stack changes.

- [ ] **Step 1 — failing regression first if needed:** Add a test for any uncovered cross-slice race (in particular, cancellation/expiry vs promotion and a second reply during evaluation) before fixing it; demonstrate red if an implementation change is needed.
- [ ] **Step 2 — verify:** From `apps/web`, run `pnpm test:run`, `pnpm typecheck`, and `pnpm build` as three **separate tool calls**; run permitted credential-free targeted API tests from Tasks 1–6, `uv run ruff check` for changed API files, and `git diff --check`. Record exact outcomes in this plan; do not run excluded gates. SQLite/deterministic race tests do not constitute PostgreSQL concurrency proof.
- [ ] **Step 3 — review and commit:** Check spec coverage, idempotency/authorization, event ordering, message history, absence of synthetic usage, and actual changed-file diff; commit any final tested changes via scanned file-based Git message. Keep the branch local and report unverified browser/PG paths explicitly.

## Execution handoff

The user selected **inline, no subagents**. After reviewing this plan, use `executing-plans` and TDD in this worktree; keep this plan's checkboxes, status and evidence current. Do not start implementation until the user confirms this written plan.
