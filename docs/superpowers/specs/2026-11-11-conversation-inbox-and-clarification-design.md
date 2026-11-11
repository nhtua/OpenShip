---
type: spec
summary: Durable conversation inbox, queued follow-up turns, and multi-message agent-judged clarifications.
status: proposed-awaiting-review
date: 2026-11-11
---

# Conversation inbox and clarification flow

## Intent and scope

OpenShip conversations are multi-turn collaborations, not a sequence of locked
composers. A person must be able to send another message while a run is working.
When the run has an open clarification, every newly accepted text message belongs
to that clarification until it closes; the agent assesses whether the accumulated
replies supply the missing information. An irrelevant or insufficient reply is
still part of the conversation and leaves the request open. Without an open
clarification, a message sent during active work waits its turn and runs after
the current run ends. Buttons, not free text or model judgment, authorize plan
approval and recovery choices.

This design fixes the live revision-0 clarification reply path and the disabled
composer, while preserving existing task limits, provider-pinned execution,
durable waits, explicit approvals, tool-dispatch safety, and recorded usage
statistics. Missing provider usage is never inferred. Historical messages and
decision records remain readable. Scope is conversation text admission and
ordering, not live interruption of a running tool or a new scheduler/service.

## Architecture and alternatives

**Selected:** a durable per-conversation inbox routes each accepted message
under the conversation lock. A message is either attached to the open
clarification on the active run or queued as a subsequent ordinary turn. One
run at a time occupies the execution slot. An existing worker/outbox path
promotes the oldest queued message after that run is terminal. The browser
renders the server's committed disposition instead of guessing a route from a
possibly stale task card.

Rejected alternatives: separate answer/new-turn endpoints ask the browser to
make a race-prone routing decision; interrupting or steering every active run
would mix new intent with already admitted tool operations and their evidence.
OpenCode V2's distinction between input for active work and queued input is a
useful interaction reference, not a claim of identical execution semantics.

## Admission contract and ordering

- Add a single authenticated message-admission operation for the web composer.
  It receives bounded nonblank text, a stable client request ID, and the same
  optional provider/tool/mode selections a new turn can carry. Its response
  names the durable message ID, disposition (`clarification` or `queued`),
  associated run/request IDs where applicable, and the current queue status.
  A message with no active run and no older queued message can become a run
  immediately; a new send never jumps an existing backlog. Preserve existing
  request-bound decision commands for explicit buttons. The old clarification
  `answer` command must use the same durable message/evaluation path or refuse
  it explicitly; it must not bypass sufficiency assessment. Preserve the
  current direct turn endpoint's active-run rejection for old clients rather
  than silently changing its response contract.
- Persist the user message and an inbox admission row together. The row owns
  the unique `(conversation_id, client_request_id)`, a monotonic conversation
  order, the immutable disposition and request binding, selected turn inputs
  for queued work, and a processing state. An exact retry returns the original
  result; reusing the key with different text or selections conflicts. Do not
  expose secrets in inbox rows or events.
- Lock conversation then project and affected run/decision/job rows in the
  established order. Under that lock, an unexpired, open clarification on the
  active run wins, including when an earlier reply is being evaluated. Otherwise
  append to the FIFO of new turns. If a run becomes terminal concurrently,
  admission either precedes its promotion or sees its successor; it never
  retrospectively changes a message's disposition. Archived/read-only scope
  rejects new admission and does not execute queued work.
- Queued text is a new turn, not an answer or approval. Retain its submitted
  model/mode/tool selection (and artifact/connector references where supported)
  rather than taking mutable browser defaults at promotion. Revalidate
  ownership, permissions, references, profile availability, and current
  execution policy at promotion; if an admitted choice can no longer execute,
  record a visible failed/blocked turn without silently changing selections.
  Build the run's immutable execution snapshot when it is actually admitted
  for execution, and do not let earlier queued messages leak into the context
  of the preceding run.

## Clarification state machine

- The existing open `TaskDecisionRecord(kind=clarification)` remains the
  request identity across multiple user replies and follow-up prompts. It
  retains its original revision, digest, authorization, and 24-hour expiry;
  attaching a reply does not mark it `answered`. Each reply has its own durable
  message ID and association to that request. The message appears immediately
  in the timeline, linked to the current run.
- Accepting the first unprocessed reply schedules the same run through its
  existing job/outbox and fenced worker. While queued or evaluating, the open
  request still owns additional messages. Only one evaluation executes at a
  time; the worker reads the recorded replies in their committed order and
  considers the original intent, the question, and the current plan/progress.
  Replies are untrusted user input, not authority to approve a plan, change
  tool policy, or replay an uncertain operation.
- Use a bounded, validated model assessment of sufficiency. It must explicitly
  say `sufficient` or `insufficient` and, for the latter, supply a bounded
  user-visible follow-up question. Insufficient leaves the same request open,
  persists the assistant follow-up, emits a request-update event for the new
  displayed question without erasing the original ask, and parks the run again
  without completing it. Sufficient atomically marks the request
  answered and permits the existing graph to continue with the cumulative
  clarification in context. Re-entering a graph with an existing plan must
  evaluate an open clarification before taking the normal resume path.
- The evaluation binds the highest included reply order. If another reply
  arrives while the model is thinking, it is not lost: a proposed verdict is
  committed only against the still-current inbox high-water mark, otherwise
  evaluate the additional reply before resolving or re-parking. A reply
  admitted after the request actually resolves is instead queued as a new
  turn. Decision expiry, cancellation, fencing, budgets, and configuration
  checks still govern continuation. If assessment is unavailable, keep the
  reply and request durable and display the failure; bounded retries or a
  later user reply can schedule another assessment, but never guess sufficiency.
  Exhausted hard limits or expired requests terminate/expire visibly rather
  than leave an unserviceable wait.

## Queue promotion and recovery

Promotion takes the oldest eligible queued inbox entry under the conversation
lock, creates its run, links the user message to that run, and enqueues its job
in the same transaction. There is never more than one active execution run for
a conversation. Completion, failure, cancellation, rejection, timeout, and
wait expiry all make the next entry eligible; a wait that remains open does
not. Recovery also scans for conversations with queued entries and no active
run, so a crash between terminal publication and a promotion attempt cannot
strand the queue. Promotion is idempotent across retries and workers, using
the inbox entry as the run identity source. Preserve the existing run/outbox
fencing and uncertain-tool rules; a new message does not resume a tool command
or acknowledge an approval.

## Events and browser behavior

- Persist a message-admitted event with the committed disposition and ID,
  and a status transition when a queued message becomes a run or cannot be
  promoted. Snapshot and SSE expose the same bounded inbox states and request
  bindings, ordered by the conversation's durable sequence, not browser time.
  A reload reconstructs queued messages and open clarifications without a
  local pending-request map as the source of truth.
- Include the durable `expected_digest` on live `task.decision_requested`
  events as well as snapshot projections. The store binds revision-0 requests
  to that digest, never fabricates one from a nonexistent plan. Version or
  backward-compatibly decode older persisted events without a digest, using
  the authoritative snapshot for actionability. Stale, expired, resolved,
  read-only, and in-flight decisions remain unactionable.
- Keep the main composer available during active runs. It sends text through
  message admission: when a clarification is open its label explains that the
  message joins the question; otherwise it explains that the message will be
  queued. A reply does not inherit an attached tool's special input fields or
  automatic-tool validation. Explicit approval/recovery controls stay in the
  timeline and run card; typed text never counts as those controls. Queued
  messages show `Queued`, then running/failed/completed status when promoted.
  If approval blocks progress, show both the pending action and that queued
  messages will wait for it. Do not label ordinary replies as thinking.

## Verification and limits

Test exact and conflicting retries, concurrent sends, send-versus-resolution
and send-versus-completion races, FIFO promotion, worker crash/restart, expiry,
cancel/archive, stale selection and read-only cases. Test multiple insufficient
replies followed by a sufficient one, including a reply during assessment,
revision-0 live digest delivery, explicit approval/recovery safety, and no
unintended tool redispatch. Web tests cover SSE/snapshot reload and the
composer in automatic-tool and attached-tool modes; confirm usage/context
statistics remain recorded-only. Run credential-free targeted API and web
checks, typecheck, and build. PostgreSQL/full-stack/provider-live/JetStream
gates and `git clean -fdx` are outside the requested verification scope;
report that limitation rather than claiming a live-browser proof.

This is a design awaiting review, not an implementation authorization. Keep
the branch local; do not push, open a PR, or merge.
