---
type: spec
summary: One ordered conversation inbox; routing (clarification reply vs queued turn) decided at processing time in arrival order.
status: draft-awaiting-review
date: 2026-11-12
---

# One ordered inbox with deferred routing

This design supersedes `2026-11-11-conversation-inbox-and-clarification-design.md`
for routing decisions. Everything that design established about durability,
idempotent admission, FIFO promotion, fenced workers, provider-pinned execution,
explicit approvals, and recorded-only usage stays in force; only the routing
point moves from admission to processing.

## Intent and scope

Every message sent from the Chat UI composer travels through **one ordered
conversation inbox**, regardless of execution mode, automatic-tool state, or a
tool attachment. The composer never guesses a route:

- When the worker processes an inbox entry while its conversation's active run
  has an open, unexpired clarification, the entry is a **reply to that
  clarification**. Replies are assessed **one at a time, in inbox arrival order**.
- When an assessment closes the clarification, **later inbox entries are not
  clarification replies**: the next entry becomes an ordinary queued turn, and
  the next turn performs its own assessment/thinking with the whole-conversation
  context.
- When no open clarification applies (no active run, or active run without one),
  entries are queued turns promoted in FIFO order when the execution slot is
  free.
- A message admitted while a clarification is open but processed after it
  resolved is a queued turn. Processing order — not browser state and not
  admission-time state — decides the final destination.

The Chat UI shows an admitted message as **`Awaiting processing`** until the
worker assigns it a destination; then it shows `Answer to clarification`
(bound) or `Queued` / run status (turn). The composer stays usable while a run
streams or waits; plain text is never blocked or reinterpreted by automatic
tools or an attached capability, and the draft is cleared only after successful
admission.

Explicit decision controls (approve/reject/recovery buttons,
`taskProgress.submitTaskDecision`) keep their existing request-bound semantics;
typed text never becomes an approval.

## Architecture

**Selected:** the existing durable `conversation_inbox` table stays the single
ordered queue, but its routing columns move from "bound at admission" to
"assigned at processing":

- Admission: under the conversation lock, admit the message durably (unique
  client request ID, monotonic conversation sequence, request digest, replay
  identical, conflict different) with `disposition = null`,
  `status = 'awaiting'`. Admission no longer reads decisions/runs to bind, and
  no longer enqueues a clarification schedule or promotion at admit time.
- Assignment (the new step, run under the same lock in the existing worker/
  promotion pump paths): take the **oldest** `awaiting` entry. If the
  conversation's active run has an open unexpired clarification, assign it
  `disposition = 'clarification'`, `status = 'attached'`, bind its
  `decision_id`, and schedule the existing fenced worker to assess that
  clarification with the replies received so far (cumulative context, one new
  reply per evaluation step). Otherwise assign
  `disposition = 'queued'`; promotion creates the next run exactly as the
  existing FIFO machinery does.
- Assessment loop: while the clarification stays open (insufficient verdict,
  follow-up persisted, run re-parked) and another `awaiting` entry exists,
  assign the next entry to it and assess again. When the verdict is sufficient,
  resolve the request, continue the run; the next `awaiting` entry is assigned
  as a queued turn at the next assignment opportunity (after the run ends or at
  a later pump under the conversation lock).
- The existing high-water/fence discipline is retained: a proposed verdict
  commits only against the inbox sequence/fence that is still current, so a
  concurrently admitted message is neither lost nor double-counted.
- A clarification closed by expiry/cancellation leaves no binding target; its
  later entries queue normally.

Rejected alternatives: keeping admission binding and reclassifying on resolution
requires moving already-bound rows and rewriting their events — more state
transitions and weaker replay guarantees. Separate endpoints per destination
reintroduce the client race this design already rejects.

## State machine

`conversation_inbox.status`:

- `awaiting` — admitted, destination not yet assigned.
- `attached` — assigned clarification reply; run scheduled for assessment.
- `queued` — assigned turn, waiting for the slot.
- `promoted` — run created for this entry.
- `failed` — visible failure (assessment unavailable, expired, policy reject).

`conversation_inbox.disposition` is `null` while `awaiting`, `clarification`
when attached, `queued` when destined as a turn. Assignment transitions are
one-way and monotonic per entry; events carry the transitions so reload
reconstructs them. `messages.message_kind` records `clarification_reply` only
when the entry is attached.

Events (extend existing names; payloads bounded, no secrets):

- `message.admitted` (disposition absent, status `awaiting`) — emitted at
  admission as today.
- `message.assigned` — status/disposition transition (`awaiting → attached`
  or `awaiting → queued`); carries `message_id`, sequence, and, when attached,
  the `run_id`/`request_id` it answers.
- existing `message.promoted`/status transitions and `task.decision_requested`
  /`task.decision_updated` semantics are unchanged, with `task.decision_updated`
  still carrying the same request ID plus follow-up prompt and assistant
  message ID.

## Queue promotion and recovery

The existing rules stay: one active execution run per conversation, promotion
takes the oldest eligible `queued` entry under the conversation lock, crash
recovery rescans conversations with `awaiting`/`queued` entries and no active
run, and promotion is idempotent. The pump additionally assigns `awaiting`
entries before promotion so no conversation can hold awaiting messages
indefinitely while its slot is free.

## Client behavior

- One composer path: text sends through `runsApi.submitMessage` for the
  current conversation. No branch on `decisionReplyContext`, `isStreaming`,
  or `isSubmitting`; a plain-text send is blocked only by navigation,
  read-only scope, or an in-flight admission of that same text.
- The automatic-tool chip and attached capability never disable the send
  button for plain text. A tool search command remains an explicit separate
  action only when the existing tool-submission rules allow it.
- ChatInput emits `send` only after accepting it (draft preserved on failure,
  cleared on successful admission response).
- Timeline/queue chips: `Awaiting processing` → `Answer to clarification` or
  `Queued` → run status when promoted. The clarification card shows the open
  request and its assessed replies; unprocessed messages are not yet called
  answers.
- The old `decisionActionsDisabled` special case for clarification replies is
  no longer needed for the composer (it existed to re-open the card mid-
  submission); the clarification card's answer button stays request-bound,
  while plain text now always uses the inbox.

## Verification and limits

Credential-free verification: API tests pin admission without binding,
awaiting/attached/queued transitions, sequential assessment in arrival order,
A-resolves-then-B-queues (including A and B admitted while open), FIFO
promotion unchanged, reply-during-assessment not lost, crash recovery,
idempotent retries and conflicting replays, expiry/cancel visible outcomes.
Web tests pin: composer sends during a waiting clarification regardless of
automatic-tool/attached-tool state, awaiting label, transition updates without
duplicates, no typed-text approval, and the restored-draft rule. Run `pnpm
test:run`, `pnpm typecheck`, `pnpm build`, targeted pytest lanes, Ruff, and
`git diff --check`. PostgreSQL/full-stack/provider-live/JetStream gates and
`git clean -fdx` are out of scope and reported as not run.

Keep the branch local: no push, PR, or merge.
