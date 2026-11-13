# One Ordered Inbox with Deferred Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, no subagents — the execution method already selected for this worktree) and superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make every Chat UI send travel through one ordered conversation inbox whose routing (clarification reply vs queued turn) is assigned at processing time, oldest entry first, with replies assessed one at a time in arrival order.

**Architecture:** Admission stops binding destinations and stops scheduling: it only durably admits (`status='awaiting'`, `disposition='pending'`) under the conversation lock. A new assignment pump (`assign_next_message`) binds the oldest awaiting entry — to the active run's open clarification when one is open, otherwise as a queued turn — at every existing scheduling opportunity (admission when idle, worker terminal promotion, reconcile recovery, graph re-park, assessment transitions). Events gain `message.assigned` so reload/SSE reconstruct transitions. The web store folds `awaiting → attached/queued`, the composer routes all text through `runsApi.submitMessage` without branching on `decisionReplyContext`, and automatic-tool/attached-tool state never disables a plain-text send.

**Tech Stack:** Python/FastAPI/SQLQLPy+Pydantic (API), Alembic; Vue 3 + TypeScript + vitest (web); SQLite credential-free lanes only.

**Spec:** `docs/superpowers/specs/2026-11-12-conversation-inbox-deferred-routing-design.md` (supersedes `2026-11-11-conversation-inbox-and-clarification-design.md` for routing).

## Global Constraints

- Keep work local: no push, PR, or merge; stay in this worktree.
- Never run PostgreSQL/full-stack/provider-live/JetStream gates, and never `git clean -fdx`; report them as not run.
- Commit with scanned file-based messages only: write `/tmp/openship-<task>-commit-message.txt`, `rayloc scan` it, then `git commit --file '<path>'` (never inline `-m`).
- Do not edit tracked files while a commit hook or verification lane runs; run lanes as separate tool calls.
- Missing provider usage statistics stay absent; never estimate.
- Explicit decision controls (`taskProgress.submitTaskDecision`, buttons) keep request-bound semantics; typed text is never an approval.
- Historical rows migrated by 035/036/037 stay readable; migrations are additive-only against applied revisions (new migration `inbox038`, never edit applied ones).

## Review Focus

- Admitted-while-open-then-resolved: a message admitted while a clarification is open but processed after resolution must become a queued turn, not a stranded reply — pinned in Task 3.
- Two sends while open (A resolves, B turns): the arrival-order assessment loop must not bind B to the resolved request — pinned in Task 4.
- Reply admitted during worker assessment: the `included_sequence`/high-water fence must keep the verdict honest under deferred binding — pinned in Task 4.
- Crash between admission and assignment: `awaiting` entries with no active run must be rescued by reconcile, and an assigned-attached entry must still schedule the run — pinned in Task 3.
- Composer during an automatic-tool or attached-tool session: plain-text send succeeds, draft survives failed admission, no tool command is emitted — pinned in Task 6.

---

## Retiring the superseded design

The old design (`docs/superpowers/specs/2026-11-11-conversation-inbox-and-clarification-design.md` + `docs/superpowers/plans/2026-11-11-conversation-inbox-and-clarification.md`) made the **binding decision at admission**: `submit_message` probed the active run for an open clarification and stamped the entry with `disposition='clarification', status='attached'`, set `messages.message_kind='clarification_reply'`, and scheduled the clarification reply or promoted an idle message inside the admission transaction. Removing that behavior is a deliberate, reviewable change — not incidental fallout. Each artifact below is touched exactly once by the task named; nothing is silently left behind.

| Old-design artifact | Current location | Action | Task |
| --- | --- | --- | --- |
| `disposition` bound at admission (`'clarification' if decision else 'queued'`, `admission.py:144–145`) | `apps/api/src/openship/runs/inbox/admission.py` | delete the active-run/decision probe from `submit_message`; every fresh admission is `disposition='pending', status='awaiting'` | 2 |
| `message_kind='clarification_reply'` and `run_id` set at admission (`admission.py:137–139`) | same | delete; kind/run are set only when `assign_next_message` attaches an entry | 2 |
| `message.admitted` payload carrying a bound disposition | `admission.py:146–161`, `events/schemas.py:144–152` | widen to `pending`/`awaiting`; binding transitions move to `message.assigned` | 1–2 |
| Admission-time scheduling tail: `enqueue_clarification_reply(...)` and `promote_next_message(...)` called inside admission (`admission.py:176–183`) | same | remove from admission; the pump supplies both at the existing scheduling opportunities | 2–3 |
| `MessageAdmissionResponse(disposition ∈ {'clarification','queued'})` promising a final destination | `inbox/contracts.py:20–25` | widen with `pending`/`awaiting`; response reports "admitted, unassigned" | 1 |
| Inbox CHECK enums without `awaiting`/`pending` | `inbox/models.py:58–64`, migration `035` | additive migration `038`; migrations 035–037 stay untouched | 1 |
| Chat composer branching on `decisionReplyContext` to choose `/messages` vs `/turns` | `ChatView.vue:264–268`, `composerBlocked:324–329` | one inbox path; `decisionReplyContext`/`isSubmitting` no longer gate a plain-text send | 6 |
| `replyContext` disjuncts disabling Send during a clarification | `ChatInput.vue:239–242` | delete; plain text is never blocked by tool state | 6 |
| `decisionActionsDisabled` clarification exemption added to re-open the old composer (`taskProgress.ts:288`) | `apps/web/src/stores/taskProgress.ts` | revert to `readOnly || submitting`; the inbox path never consults it | 6 |
| `chat.submitMessage` leaving no retry state (`chat.ts:1188–1221`) | `apps/web/src/stores/chat.ts` | register pending request and draft restoration on admission failure | 6 |
| Test pins that assert binding at admission (`test_open_clarification_binds_under_lock`, `test_non_clarification_active_run_does_not_claim_message`, `test_idle_message_is_promoted_in_admission_transaction`, `test_reply_during_worker_claim_remains_bound`, `test_expired_clarification_does_not_claim_new_message`, `test_typed_yes_does_not_confirm_plan`, `test_snapshot_binds_clarification_reply_to_the_open_request`) | four inbox test files | rewrite each to the unbound outcome driven through the pump (keep the behavior coverage, flip the expectation); do not delete a test to dodge a pin | 2–4 |
| Old plan/spec status headers presenting binding as the rule | the two 2026-11-11 docs | supersede banner + pointer to the 2026-11-12 pair; keep them as history | 7 |

Historical data written under the old design (rows with `disposition='clarification', status='attached'`, `message_kind='clarification_reply') stays valid: the new pump only handles `awaiting`/`pending` entries, and assessment reads attached entries by the same rules. There is no data migration — only vocabulary widening.

---

### Task 1: Inbox vocabulary and contracts for unassigned entries

**Progress:** Contract and migration implementation uses `null` disposition as specified by the design, rather than the plan's `pending` literal. RED: two contract tests failed before implementation. GREEN: 46 inbox tests passed, the existing-history migration test passed, Ruff and web typecheck passed. A SQLite migration test preserves an old attached row and refuses downgrade with an awaiting row. Remaining: full applicable verification, commit and task completion. The Dev Stack database has not been migrated.

**Files:**
- Create: `apps/api/alembic/versions/038_inbox_deferred_routing.py` (`inbox038` ← `inbox037`)
- Modify: `apps/api/src/openship/runs/inbox/models.py`, `apps/api/src/openship/runs/inbox/contracts.py`, `apps/api/src/openship/events/schemas.py`, `apps/web/src/types/index.ts`

**Interfaces:**
- Produces: inbox `status ∈ {'awaiting','attached','queued','promoted','failed'}`, `disposition ∈ {'pending','clarification','queued'}` (`pending` = unassigned); `MessageAdmissionResponse(disposition: Literal['pending','clarification','queued'], status: Literal['awaiting','attached','queued','promoted','failed'], …)`; `MessageAssignedEvent(schema_version, message_id, disposition: Literal['clarification','queued'], status: Literal['attached','queued'], run_id: UUID|None, request_id: UUID|None)` registered under event type `message.assigned` in `TASK_EVENT_SCHEMAS`; TS `InboxMessageSummary.status`/`disposition` and `MessageAdmissionResponse` widened identically.
- Alembic uses `op.batch_alter_table('conversation_inbox')` to replace `ck_inbox_disposition` (add `pending`) and `ck_inbox_status` (add `awaiting`) on both dialects; downgrade refuses when any row has `status='awaiting'` or `disposition='pending'`.
- TS/Py contracts for events and projections (`InboxMessageSummary`) accept the widened vocabularies; nothing else changes.

- [ ] **Step 1: failing contracts tests** — add `apps/api/tests/test_conversation_inbox_routing.py` asserting: alembic head is `inbox038`; a fresh SQLite build accepts a `status='awaiting'`/`disposition='pending'` row and keeps both CHECK names; `MessageAdmissionResponse(status='awaiting', disposition='pending')` validates; `MessageAssignedEvent` round-trips and is registered for `message.assigned`.
- [ ] **Step 2: red** — `cd apps/api && uv run pytest tests/test_conversation_inbox_routing.py -q` (expect ImportError/KeyError failures before the vocabulary exists).
- [ ] **Step 3: implement** — write migration 038 (read 035/036/037 first for style), widen `models.py` CHECK literals to the Produces vocabularies, update `contracts.py` and `events/schemas.py` (`MessageAdmittedEvent` accepts the new vocab; `MessageStatusChangedEvent` unchanged), mirror in `apps/web/src/types/index.ts`.
- [ ] **Step 4: green** — rerun the routing test; then `uv run pytest tests/test_conversation_inbox_admission.py tests/test_conversation_inbox_promotion.py tests/test_conversation_inbox_snapshot.py -q` (existing rows/tests must still pass: existing rows already use old vocab). Run `uv run ruff check src/openship/runs/inbox src/openship/events/schemas.py tests/test_conversation_inbox_routing.py`; from `apps/web`, `pnpm exec vue-tsc --noEmit`.
- [ ] **Step 5: commit** — scanned file-based message, subject `feat: admit inbox messages without binding their destination`.

### Task 2: Admission admits only — no binding, no scheduling

**Progress:** Added `test_open_clarification_admission_remains_unassigned`; RED confirms existing admission still binds it. An isolated admission-only implementation passed that test but caused 26 failures across admission, promotion and snapshot tests; promotion/reconciliation and the direct-turn backlog guard still assume admission-bound entries. The experimental production change was removed to avoid stranding messages in the hot-reloading Dev Stack. The original three test files pass again (42 passed); the new regression intentionally remains red. Admission and the assignment/recovery/backlog changes must be delivered together. At the user's explicit request the Dev Stack Compose migration service was run alone and the database revision was verified as `inbox038`; this is not PostgreSQL test-gate evidence.

**Files:**
- Modify: `apps/api/src/openship/runs/inbox/admission.py` (rewrite `submit_message`:42–184)
- Modify: `apps/api/tests/test_conversation_inbox_admission.py` (pins flip)

**Interfaces:**
- Consumes: Task 1 vocabulary.
- Produces: `submit_message(db, *, user_id, conversation_id, request) -> MessageAdmissionResponse` unchanged signature; every fresh admission persists `Message(role='user', message_kind='primary', run_id=None)` + inbox entry `status='awaiting'`, `disposition='pending'`, `decision_id=None`, `run_id=None`, `sequence=<admitted event sequence>`, frozen `request_json` (mode/revision/provider selections captured once exactly as now), emits `message.admitted` with `disposition='pending', status='awaiting', run_id=None, request_id=None`. Replay/conflict semantics and ownership/archive rules unchanged. No `enqueue_clarification_reply`, no `promote_next_message` call at admission.

- [ ] **Step 1: failing tests** — extend `test_conversation_inbox_routing.py` (or a new `test_conversation_inbox_admission_unbound.py`): fresh admission against an open clarification returns `status='awaiting'`/`disposition='pending'`, leaves the decision `open`, and creates no Outbox row; idle fresh admission is not promoted at admission (entry stays `awaiting`); replay returns identical stored values; conflicting replay still 409. Update now-obsolete pins in `test_conversation_inbox_admission.py`: `test_open_clarification_binds_under_lock`, `test_non_clarification_active_run_does_not_claim_message`, `test_idle_message_is_promoted_in_admission_transaction` — flip them to the unbound outcome (do not delete coverage).
- [ ] **Step 2: red** — `cd apps/api && uv run pytest tests/test_conversation_inbox_routing.py tests/test_conversation_inbox_admission.py -q` (expect binding-era failures).
- [ ] **Step 3: implement** — retire the old admission design in `submit_message`: delete the active-`Run` query and open-clarification `TaskDecisionRecord` probe (`admission.py:92–115`), the binding writes (`disposition`/`status`, `message_kind='clarification_reply'`, `run_id` on the `Message`) and the tail `enqueue_clarification_reply` / `promote_next_message` calls; keep the replay/conflict lookup, ownership/archive checks, lock order, frozen `request_json` capture, `message.admitted` event and inbox row (now `pending`/`awaiting`). Keep `enqueue_clarification_reply` itself in `tasks/clarification.py` — Task 3 calls it from the pump; deleting it belongs to no task.
- [ ] **Step 4: green** — rerun both files; `uv run ruff check src/openship/runs/inbox/admission.py tests/test_conversation_inbox_admission.py tests/test_conversation_inbox_routing.py`.
- [ ] **Step 5: commit** — `fix: admit inbox messages without claiming a destination`.

### Task 3: Assignment pump at every scheduling opportunity

**Progress:** An unconnected `assignment.py` prototype and focused tests cover assigning a waiting message to a busy run's queue and attaching only one message to an open clarification before assessment. A direct-turn backlog guard regression failed before the guard was extended to include awaiting messages, and then passed alongside the legacy guard regression (2 passed). The normal admission and scheduling paths do not call the prototype yet. FIFO transitions after assessment, recovery, old attached entries, locking, and all call sites remain unverified; this task is not complete.

**Files:**
- Create: `apps/api/src/openship/runs/inbox/assignment.py`
- Modify: `apps/api/src/openship/runs/inbox/promotion.py`, `apps/api/src/openship/runs/tasks/clarification.py`, `apps/api/src/openship/runs/worker.py`, `apps/api/src/openship/runs/task_graph.py`, `apps/api/src/openship/runs/inbox/admission.py` (idle path re-added via pump)
- Test: `apps/api/tests/test_conversation_inbox_assignment.py`

**Interfaces:**
- Produces: `assign_next_message(db, *, conversation_id, now: datetime) -> InboxMessageSummary | None` — under the conversation lock, when no other awaiting entry is already bound and an `awaiting` entry exists: if the active run (`ACTIVE_RUN_STATUSES`) has an unexpired open `TaskDecisionRecord(kind='clarification')` → set entry `disposition='clarification', status='attached', decision_id, run_id`, update its `Message.message_kind='clarification_reply'` and `run_id`, emit `message.assigned` (`attached`), then reuse existing `enqueue_clarification_reply(db, run_id=…, decision_id=…, message_id=…, now=…)` to schedule the fenced worker; otherwise set `disposition='queued', status='queued'` and emit `message.assigned` (`queued`). It never promotes (Task's promotion stays separate). Call sites: admission idle branch (no active run) calls assign then existing `promote_next_message`; worker terminal hook (`worker.py:1360–1372`) calls assign before `promote_next_message`; `reconcile_queued_messages` (`promotion.py:113–142`) rescans conversations that have `awaiting` OR `queued` entries and assigns first, promotes after; `task_graph` clarification re-park and `apply_clarification_assessment` transitions (`clarification.py:182–291` return paths) call assign so the next awaiting entry is claimed; `expire_task_decisions` expiry path calls assign after expiry.
- `enqueue_clarification_reply` guard at `clarification.py:33–112` keeps its fence/high-water discipline; it now receives freshly attached entries instead of admission-bound ones.

- [ ] **Step 1: failing tests** — `test_conversation_inbox_assignment.py` pins: awaiting entry + open clarification → attached with correct `request_id` and exactly one Outbox schedule; awaiting entry + active run without clarification → `queued`; awaiting entry + no active run → `queued` and existing promotion turns it into a run (reuse existing promotion test helpers); A resolves via sufficient verdict then B → B assigned `queued` (not attached); entry admitted during worker claim stays `awaiting` until the claim releases, then assigns; reconcile rescues an `awaiting` entry when its conversation has no active run.
- [ ] **Step 2: red** — `cd apps/api && uv run pytest tests/test_conversation_inbox_assignment.py tests/test_conversation_inbox_promotion.py tests/test_conversation_inbox_clarification.py -q`.
- [ ] **Step 3: implement** — `assignment.py` first (lock order mirrors `_lock_decision_scope`), then the five call sites.
- [ ] **Step 4: green** — rerun the three files above plus `tests/test_conversation_inbox_admission.py` and `tests/test_task_wait_delivery.py`; Ruff the changed modules.
- [ ] **Step 5: commit** — `feat: assign inbox messages to clarifications or turns at processing time`.

### Task 4: One-reply-at-a-time assessment loop and event transitions

**Files:**
- Modify: `apps/api/src/openship/runs/task_graph.py` (`assess(...)` stage:375–505), `apps/api/src/openship/runs/tasks/clarification.py` (`apply_clarification_assessment`:182–291, `release_clarification_claim`:294–334), `apps/api/src/openship/events/schemas.py` (`MessageAssignedEvent` payload already registered in Task 1)
- Modify tests: `apps/api/tests/test_conversation_inbox_clarification.py` (`test_reply_during_assessment_is_not_lost`:438–467, `test_multiple_replies_keep_request_open`:41–87 stay valid semantics but their setup now admits unbound first, then assigns — update fixtures to drive through the pump)

**Interfaces:**
- Consumes: Task 3 assignment + `clarification_replies(db, *, decision_id)` (unchanged: all attached entries ordered by `sequence`).
- Produces: assessment still calls `assess_clarification(stage, progress, question=…, replies=…)` with cumulative attached replies; `included_sequence` remains the newest attached `sequence`; `apply_clarification_assessment` keeps `_latest_reply_sequence > included_sequence → False` and `assessed_sequence` bookkeeping. New: after a `sufficient` commit (`decision.state='answered'`), call `assign_next_message` so any later `awaiting` entry becomes `queued`; after an `insufficient` commit, call `assign_next_message` so the next awaiting entry attaches and gets assessed (this is the arrival-order loop). Expiry/cancel paths likewise re-assign.

- [ ] **Step 1: failing tests** — add `test_assesses_replies_one_at_a_time_in_arrival_order`: two awaiting entries admitted against an open clarification; deterministic fake model says first `insufficient` → second attaches and is assessed; second `sufficient` → request `answered` and no third attachment; add `test_later_awaiting_becomes_turn_after_resolution`. Update obsolete pins listed above to drive admission→assignment instead of admission-time binding.
- [ ] **Step 2: red** — `cd apps/api && uv run pytest tests/test_conversation_inbox_clarification.py tests/test_conversation_inbox_assignment.py -q`.
- [ ] **Step 3: implement** — extend the assessment transitions in `task_graph`/`clarification.py`; keep provider-error retry release semantics.
- [ ] **Step 4: green** — rerun both files plus `tests/test_task_graph.py` and `tests/test_task_decisions.py`; Ruff changed modules.
- [ ] **Step 5: commit** — `fix: assess clarification replies one at a time in inbox order`.

### Task 5: Web store folds awaiting/assigned transitions

**Files:**
- Modify: `apps/web/src/stores/chat.ts` (`inboxStatus`:478–482, `inboxDisposition`:484–491, `applyInboxEvent`:506–542, `hasQueuedInbox`:567–569, `hasOpenClarification`:571–575, `processDurableEvent`:587–590, snapshot fold `applySnapshotInbox`:544–559)
- Test: `apps/web/tests/ChatStore.test.ts` (pins at L637–694, L696–731, L733–773)

**Interfaces:**
- Consumes: `message.admitted` (`pending`/`awaiting`), `message.assigned` events; `Snapshot.inbox` with widened vocab (types already widened in Task 1).
- Produces: fold states `awaiting | attached | queued | promoted | failed` (unknown coerced to `awaiting` now, not `queued`); `hasQueuedInbox` counts `awaiting`, `queued` (stream stays open through terminal events while either exists); `hasOpenClarification` unchanged semantics (attached entries); `message.assigned` folds sequence-guarded like the other inbox events, setting disposition/status and, when attached, `run_id`/`request_id`.

- [ ] **Step 1: failing tests** — add `it('folds awaiting admission then assignment')`: admitted event → entry `{disposition:'pending', status:'awaiting'}` with `hasQueuedInbox true`; `message.assigned` (attached) → disposition/status/`request_id` update with no rewind on replay; snapshot replace keeps `awaiting` entries. Flip obsolete pins: `it('admission and status events fold into the inbox')` (L637) admits `pending`/`awaiting` first; L733 snapshot pin expects pending entry then assignment fold.
- [ ] **Step 2: red** — `pnpm exec vitest run tests/ChatStore.test.ts` in `apps/web`.
- [ ] **Step 3: implement** — extend the fold; snapshot/`awaiting` coercion.
- [ ] **Step 4: green** — rerun; then `pnpm exec vue-tsc --noEmit`.
- [ ] **Step 5: commit** — `feat: fold awaiting and assigned inbox states in the chat store`.

### Task 6: Composer with one admission path and honest labels

**Files:**
- Modify: `apps/web/src/views/ChatView.vue` (`handleSend`:250–270, `composerBlocked`:324–329, bindings L766–789), `apps/web/src/components/chat/ChatInput.vue` (`submitDisabled`:233–243, `handleSubmit`:314–371, `inputPlaceholder`:104–111), `apps/web/src/components/chat/ChatStream.vue` (+ `UserMessage` chip), `apps/web/src/components/chat/TaskDecision.vue` (composer hint stays), `apps/web/src/stores/chat.ts` (`submitMessage`:1188–1221 registers retry pending)
- Test: `apps/web/tests/ChatInput.test.ts`, `apps/web/tests/ChatView.test.ts`, `apps/web/tests/ChatComponents.test.ts` (coverage gap: no test passes `replyContext: true` today — close it), `apps/web/tests/ChatStore.test.ts`
- Modify: `apps/web/src/stores/taskProgress.ts` (`decisionActionsDisabled`:280–291)

**Interfaces:**
- Consumes: `runsApi.submitMessage(conversationId, request: TurnSubmitRequest)` (payload now carries `enable_tools` and selections so a deferred turn keeps its submission semantics; text stays nonblank), `chat.inboxFor`/`inboxStatus`.
- Produces: `handleSend` for an existing conversation always calls `chat.submitMessage(content, conversationId, options)` — no `decisionReplyContext` branch, no `isStreaming`/`isSubmitting` guard for plain text (navigation + read-only still guard); `ChatInput.submitDisabled` never includes a `replyContext`/`automaticToolMode` disjunct for plain-text send (explicit `search` command rules stay); automatic mode emits `send` with `{ enableTools: true }` unchanged for eventual turns but attached-capability `search` stays its own explicit action; failed admission keeps `pendingRequests` entry + ChatView `composerDraft` restoration (draft cleared only on successful admission response); `ChatStream`/`UserMessage` renders inbox chips `Awaiting processing` → `Answer to clarification` / `Queued` → run status when promoted; composer placeholder copy mentions the waiting clarification when one is open.

- [ ] **Step 1: failing tests** — ChatView: `it('sends composer text through the inbox during an open clarification with automatic tools on')` (mock `runsApi.submitMessage` once, no `submitTurn`, textarea enabled despite `isStreaming`/`isSubmitting`, automatic chip state untouched); ChatInput: `it('keeps send enabled for plain text while reply-context tools stay inert')` passes `replyContext: true` + automatic mode → one `send` emit, no `search`; ChatStream: awaiting chip renders for a `pending`/`awaiting` inbox entry; ChatStore: `submitMessage` failure keeps pending retry and restores draft on the `submissionError` watch; TaskProgressStore: `it('accepts no second choice while a decision is under submission')` — `submitting: true` makes `decisionActionsDisabled` true for every kind (revert of the clarification exemption).
- [ ] **Step 2: red** — `pnpm exec vitest run tests/ChatInput.test.ts tests/ChatView.test.ts tests/ChatComponents.test.ts tests/ChatStore.test.ts`.
- [ ] **Step 3: implement** — one-path `handleSend`, relaxed `composerBlocked` (navigation + read-only only), remove the two `replyContext` disjuncts from `submitDisabled`, revert `taskProgress.decisionActionsDisabled` to `card.readOnly || card.submitting`, add inbox chips from `chat.inboxFor(conversationId)` keyed by message id, and pending/draft restoration on admission failure.
- [ ] **Step 4: green** — rerun the four files; `pnpm exec vue-tsc --noEmit`; `pnpm build`.
- [ ] **Step 5: commit** — `fix: route every composer send through one inbox admission path`.

### Task 7: Cross-slice regression, evidence, and honest qualification

**Files:** update this plan's checkboxes/evidence after each task; `docs/validations.md` only if its format has a relevant entry.

- [ ] **Step 1: regression sweep** — credential-free lanes as separate calls: `cd apps/api && uv run pytest tests/test_conversation_inbox_admission.py tests/test_conversation_inbox_promotion.py tests/test_conversation_inbox_assignment.py tests/test_conversation_inbox_clarification.py tests/test_conversation_inbox_snapshot.py tests/test_task_graph.py tests/test_task_decisions.py tests/test_task_wait_delivery.py tests/test_agent_tool_loop.py -q`; `uv run ruff check` on every changed module; `cd apps/web && pnpm exec vue-tsc --noEmit`, `pnpm test:run`, `pnpm build`; `git diff --check`.
- [ ] **Step 2: review gates** — check the Review Focus list and the **Retiring the superseded design** table against actual code paths (not only tests): every old-design artifact in the table is gone or rewritten, and no second binding-at-admission path survives (`grep -n "disposition = \"clarification\" if decision else" apps/api/src/openship/runs/inbox/admission.py` is empty; no admission-path call of `enqueue_clarification_reply`; no `replyContext` disjunct in `ChatInput.submitDisabled`; `decisionActionsDisabled` has no `clarification` exemption).
- [ ] **Step 3: supersede the old documents** — in `docs/superpowers/specs/2026-11-11-conversation-inbox-and-clarification-design.md` (status already `superseded-by-2026-11-12-deferred-routing-design`) add a one-line note under the H1 pointing at the 2026-11-12 spec; in `docs/superpowers/plans/2026-11-11-conversation-inbox-and-clarification.md` set its header status/first line to `superseded: see docs/superpowers/plans/2026-11-12-inbox-deferred-routing.md` while keeping its delivered evidence intact. Commit both doc updates with a scanned file-based message, subject `docs: supersede the admission-binding inbox design`; keep the branch local.
- [ ] **Step 4: commit** final evidence updates with a scanned file-based message; keep branch local. Report exactly: lanes run with counts, and that PostgreSQL, full-stack browser, provider-live and JetStream gates were **not run or qualified**, so the live stuck-conversation `6782d9e4…` is not yet re-verified end to end.

## Execution handoff

Execution method already selected for this worktree: **inline, no subagents** (`executing-plans` + TDD in this session, per the prior plan's handoff). Please review this plan; implementation starts only after you confirm it captures what you want.
