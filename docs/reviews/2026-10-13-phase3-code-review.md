---
type: review
summary: Deep review and follow-up implementation of findings across the 19 Phase 3 commits, with reproduction evidence, fixes, and regression coverage.
status: addressed-pg-unverified
date: 2026-10-13
---

# Phase 3 Code Review

**Range:** `d0aa5a7b6fb1ea34e30e938bf691d9eacbc6c050..dd05217e0b5c75ab8472bcf43a7ab0e2b292ac6e` — 19 commits, 103 changed files. Includes the fixes in `c136ef8` and the semantic events added in `dd05217`.

**Requirements:** [Phase 3 plan](../superpowers/plans/2026-10-12-phase3-model-provider-portability.md), [roadmap Phase 3](../roadmap.md#phase-3--model-provider-portability), and [architecture](../architects.md), particularly provider compatibility, durability, authorization, and browser observability.

**Assessment:** The first review found 20 security, consistency, recovery, and qualification defects. Follow-up changes in this worktree address all 20 and add focused regression coverage. The separation into contracts, adapters, a shared runner, and durable run records remains useful. PostgreSQL concurrency qualification is still pending because no test database is configured in this environment.

P1 means a release-blocking security, normal-use, or consistency defect. P2 means an important correctness, reliability, or validation gap. Each finding below states a concrete trigger; recommendations are not implementation changes.

## Initial Review Verification and Limits

Checks recorded for the reviewed head before follow-up changes:

| Check | Result |
| --- | --- |
| API unit suite, excluding `postgres` and `provider_live` | **306 passed, 19 deselected**, 3 warnings |
| API Ruff | Passed |
| Web TypeScript check | Passed |
| Web unit suite | **122 passed** |
| Web production build | Passed |
| Working tree at review start | Clean |

API tests ran outside the sandbox because its test-client thread bridge previously stalled there. Targeted reproductions used installed SDKs with mock HTTP transports, actual SDK response types, the actual browser store transpiled in memory, temporary dotenv files, and isolated in-memory database/worker setups. No live provider calls were made.

The PostgreSQL lane was **not rerun**: `TEST_POSTGRES_URL` was not configured. Playwright was not rerun; its fixtures were inspected. Claims about database races were checked through code tracing and isolated reproductions, not a fresh PostgreSQL qualification. No implementation files, commits, branches, or index entries were changed by the review.

## Findings

### 1. P1 — Native SDK redirects bypass destination authorization

**Locations:** [OpenAI client construction](../../apps/api/src/openship/providers/openai.py#L111), [Anthropic client construction](../../apps/api/src/openship/providers/anthropic.py#L155).

Both clients inherit the SDK transport's `follow_redirects=True`. An HTTP 307 from the approved endpoint sends the complete conversation to the redirect target without going through the explicit fallback policy. Anthropic also forwards its `x-api-key` header to that other origin.

**Evidence:** With the installed SDKs and `httpx2.MockTransport`, a synthetic redirect produced two POST requests, one to the approved origin and one to an unapproved origin. Both carried the conversation; Anthropic's second request carried the synthetic key. No external request was involved.

**Fix:** Supply nonredirecting transports and normalize redirect responses as configuration errors. Test redirects through the actual SDK HTTP layer, asserting that only the approved origin receives a request and no credential/context reaches the target.

### 2. P1 — The documented dotenv setup fails

**Locations:** [Credential resolution](../../apps/api/src/openship/providers/configuration.py#L142), [credential availability](../../apps/api/src/openship/providers/errors.py#L129), [Settings dotenv configuration](../../apps/api/src/openship/config.py#L43).

`Settings` reads `.env`, but the new availability check and adapters read only `os.environ`. A configured OpenAI key in `.env` therefore populates Settings but leaves the catalog unavailable and the adapter unable to authenticate. Adding a nonempty `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` to the documented example additionally raises `extra_forbidden`, because those bindings are not Settings fields.

**Evidence:** Temporary dotenv reproduction: Settings contained the synthetic OpenAI key while the resolver returned no key; a filled Anthropic binding failed Settings validation.

**Fix:** Provide one secret-safe credential source for both exported environment variables and dotenv bindings, including operator-configured reference names. Ensure dotenv entries outside the typed non-secret settings do not prevent startup. Add startup/catalog/adapter tests using a real temporary `.env`, rather than only `monkeypatch.setenv()`.

### 3. P1 — Real SSE removes the run identity and hides cancellation

**Locations:** [SSE serializer](../../apps/api/src/openship/events/routes.py#L159), [event parser](../../apps/web/src/stores/chat.ts#L494), [run-card update](../../apps/web/src/stores/chat.ts#L366), [cancel-control eligibility](../../apps/web/src/views/ChatView.vue#L101).

The actual SSE payload omits `run_id`. The browser parses it as null, then overwrites the submitted card's run ID with an empty string on `run.started`. The new cancel button requires a nonempty run ID, so it disappears when the real worker starts.

**Evidence:** Feeding a real-shaped frame through the actual store changed the card's run ID to `""`. Vitest and Playwright fixture frames include `run_id`, hiding the mismatch. The wire defect predates this branch, but it breaks the newly delivered cancellation behavior.

**Fix:** Use the same complete event envelope for snapshots and SSE, including run identity and timestamp. Preserve a known ID when an event lacks one. Add a contract test between the API serializer and browser parser, plus a cancel test driven by that serializer.

### 4. P1 — Publication failure can commit an incomplete terminal result

**Locations:** [Worker exception handler](../../apps/api/src/openship/runs/worker.py#L523), [finalize transaction behavior](../../apps/api/src/openship/runs/queue.py#L189).

The success path stages a completed run, assistant message, and attempt records. If terminal-event construction raises, the outer handler calls `finalize()` without rolling back. Its second update matches no running row, but its unconditional commit still commits the already-staged success transaction.

**Evidence:** Injecting a `RuntimeError` while constructing `run.succeeded` made the actual Worker return `False`, while the database held a completed run, one assistant message, and an attempt, with no terminal success event.

**Fix:** Roll back failed publication first. Then use a fresh fenced transaction to publish a failure status and event together. Add failure injection through `Worker.process_job()` at message/event construction and commit boundaries; a helper test that manually rolls back does not cover this exception path.

### 5. P1 — Removing a profile does not revoke dispatch

**Locations:** [Existing snapshot binding](../../apps/api/src/openship/runs/service.py#L259), [adapter admission](../../apps/api/src/openship/providers/runner.py#L289).

A pinned run bypasses the current registry. Removing its profile from installation configuration while retaining the credential binding still allows a queued run to send context to the removed endpoint. Later fallback requests have the same problem. Pinning immutable settings and checking current authorization are separate obligations.

**Evidence:** A run pinned to a removed profile dispatched through a controlled adapter after the current registry was replaced with one containing only another profile.

**Fix:** Recheck current profile eligibility/revocation and credential availability immediately before each dispatch, while executing the original pinned endpoint/model/settings. Do not repin or substitute another destination. Test profile removal between submission and execution, and between the primary request and fallback.

### 6. P2 — Admitted attempts disappear after crash or owner cancellation

**Locations:** [Recorder admission](../../apps/api/src/openship/providers/attempts.py#L182), [worker recorder setup](../../apps/api/src/openship/runs/worker.py#L343), [lease-loss return](../../apps/api/src/openship/runs/worker.py#L407).

`start()` flushes but does not commit. Attempts and their events are committed only with terminal publication. A crash or cancellation that moves ownership closes/rolls back the worker transaction, erasing actual requests, fallback destinations, reservations, and known usage. The durable provider-start marker remains, but it cannot describe which request occurred.

**Evidence:** A controlled Worker used the real recorder to open an attempt, then simulated process exit. The persisted run had `provider_started_at`, but zero attempts. The PostgreSQL crash test explicitly commits recorder admission, which the real Worker never does.

**Fix:** Commit admission metadata and its event/outbox in a short fenced transaction before network dispatch, then record outcomes separately. Release locks before provider I/O; do not hold the conversation row lock across the turn. Preserve historical admitted attempts when the worker later loses its lease, while still forbidding stale answer publication.

### 7. P2 — A late cancellation can overwrite completion

**Location:** [Cancellation transition](../../apps/api/src/openship/runs/service.py#L342).

Cancellation reads status without a lock and later writes status/fence unconditionally. A worker can complete between that read and the cancellation commit. The cancellation then changes the completed run to cancelled despite its completed assistant message and success event. This race is inherited; synchronizing the run/job fences in this branch does not resolve it.

**Evidence:** In a two-session reproduction, one session held a running row, another completed it, and the first cancelled. Final state contained a cancelled run, a completed assistant answer, and both success/cancel events.

**Fix:** Lock and refresh the run before deciding, or perform a conditional transition against the current nonterminal status/fence. Return the terminal result that won the race. Keep lock ordering consistent with publication and test simultaneous completion/cancellation on PostgreSQL.

### 8. P2 — Google thought text is rendered as answer text

**Locations:** [Google part handling](../../apps/api/src/openship/providers/google.py#L303), [thought fixture](../../apps/api/tests/providers/conftest.py#L539).

A real `types.Part(text=..., thought=True)` contains both text and the thought flag. The adapter tests only the text field, so private deliberation is emitted and can be persisted with the completed answer. The fixture instead puts the text into `thought` and omits `text`, so the test cannot catch this.

**Evidence:** Feeding actual SDK `Part`/response types through the adapter emitted the synthetic private deliberation followed by the public answer and a normal finish.

**Fix:** Skip `thought=True` before processing answer text. Build this fixture from the real SDK type and assert that reasoning does not appear in normalized deltas, messages, browser output, or persisted state.

### 9. P2 — Counting and backoff bypass cancellation/deadline supervision

**Locations:** [Token counting](../../apps/api/src/openship/providers/runner.py#L130), [retry sleep](../../apps/api/src/openship/providers/runner.py#L347).

Only generation is supervised. Counting calls and retry sleeps are directly awaited. A stop signal therefore cannot interrupt them, and native counting uses a profile timeout rather than the remaining run deadline. The no-further-request guarantee is weaker than the advertised bounded interruption guarantee.

**Evidence:** An async counting reproduction remained pending after deadline expiration and cancellation; a rate-limited turn remained asleep after cancellation during a ten-second `Retry-After`. Existing tests use an instantaneous sleeper.

**Fix:** Supervise all external counting I/O and backoff under the same deadline/stop signal. Cap counting timeouts to remaining time and check deadline before dispatch. Test blocked counting and real cancellable waits, not only whether another request eventually starts.

### 10. P2 — Native 5xx responses do not retry or fall back

**Locations:** [OpenAI caught errors](../../apps/api/src/openship/providers/openai.py#L85), [Anthropic error handling](../../apps/api/src/openship/providers/anthropic.py#L56).

The caught-error sets omit ordinary `InternalServerError`/general 5xx status errors. They escape into the runner's crash path and become nonretryable `invalid_response`. Anthropic's overload-specific handling does not cover an ordinary HTTP 500.

**Evidence:** Installed SDKs against mock 500/503 responses made one wire request and returned nonretryable `invalid_response`, bypassing the intended retry/fallback policy.

**Fix:** Normalize SDK status errors by status, mapping applicable 5xx responses to `provider_unavailable`. Add actual-SDK transport tests for retry, exhaustion, and explicitly authorized fallback.

### 11. P2 — OpenAI content filtering is misclassified as malformed output

**Location:** [OpenAI terminal-event construction](../../apps/api/src/openship/providers/openai.py#L175).

The native `finish_reason` is passed directly into the normalized contract. Native `content_filter` is not a member of its `stop/length/refusal/tool_calls` vocabulary, so Pydantic rejects it and the runner reports malformed output instead of refusal. Current tests use invented native `refusal` finish reasons.

**Fix:** Explicitly map native finish reasons and refusal deltas. Test actual SDK-shaped filtered responses and assert the public `model_refused` outcome with no completed assistant message.

### 12. P2 — Public hosts pass the private-HTTP validation

**Location:** [Private-host classifier](../../apps/api/src/openship/providers/configuration.py#L77).

String prefixes classify all `172.*`, `192.*`, and `10.*` hosts as private. Public addresses such as `172.67.1.1` and public names such as `10.attacker.example` pass validation for insecure HTTP. Prompts and bearer credentials can then be sent in plaintext despite the validator claiming to allow HTTP only for local/private destinations.

**Evidence:** The actual classifier accepted public `172.67.1.1` and `192.88.99.1`.

**Fix:** Parse literal addresses with `ipaddress` and explicitly check approved private/loopback networks. Handle configured local/container names separately; do not treat a public DNS name as private based on its first characters. Add positive and negative IPv4/IPv6/hostname cases.

### 13. P2 — Incompatible snapshots break failure reporting and public reads

**Location:** [Raw snapshot projection](../../apps/api/src/openship/runs/schemas.py#L112).

Execution safely rejects incompatible snapshots, but error reporting and GET/snapshot projections immediately assume the current raw JSON shape. The existing mismatch test's payload lacks `primary.label`; projection raises `KeyError` while trying to report the configuration error. The run becomes unreadable and the terminal failure event is absent.

**Evidence:** The exact existing fixture, `{"schema_version":2,"primary":{"id":"primary-fixture"}}`, produced a failed run with only `turn_submitted`; its public projection raised `KeyError: 'label'`.

**Fix:** Validate or safely tolerate incompatible snapshot shapes in public projections. Return unknown provider metadata while retaining the normalized error. Assert working GET/snapshot responses and a terminal failure event in the mismatch test, not merely the database status.

### 14. P2 — Retained snapshots cannot reconstruct run cards

**Location:** [Conversation hydration](../../apps/web/src/stores/chat.ts#L137).

The store ignores authoritative `snapshot.runs` and builds cards only from events. Retention can remove all events while preserving runs/messages, leaving no card to hydrate. When a submission event survives, hydration still omits authoritative status/timestamps, so a terminal run can remain displayed as queued.

**Evidence:** An actual-store probe with a retained run and `events=[]` produced zero cards. The existing pruned-snapshot test retains the old submission event and does not assert the authoritative status.

**Fix:** Rebuild cards directly from snapshot runs, including IDs, status, timestamps, errors, provider data, and attempts; then apply subsequent events. Test zero-event retention and a snapshot containing a failed/completed run.

### 15. P2 — Lost submission responses cannot be replayed idempotently

**Location:** [Submission identity and failure handling](../../apps/web/src/stores/chat.ts#L534).

Each send creates a new UUID. Failure tells the user to try again, but stored pending payloads are not actually replayed. If the server committed the first request and its response was lost, resending after completion creates another billed run and can use a different provider selection.

**Evidence:** Two sends after simulated response loss generated different UUIDs and different selected profile IDs. Capturing a payload once within one call does not implement retrying that call.

**Fix:** Add a retry/reconciliation path that preserves the original conversation, payload, request ID, and provider choices until the submission is resolved. Test server commit followed by response loss, including a provider-selection change before retry.

### 16. P2 — Runs completed after browser reload keep stale details

**Location:** [Background terminal-event handling](../../apps/web/src/stores/chat.ts#L181).

The reload subscription refreshes messages on terminal events but does not refresh run details. Its only run read happened while the run was active. Newly recorded usage, attempts, actual model, and actionable error copy therefore remain missing/stale after completion.

**Evidence:** Reload followed by a failed terminal event left the actual store with failed status, no error code/message, no usage, and no attempts; `getRun()` was called only once.

**Fix:** Hydrate authoritative run details on terminal events in both subscription paths, guarded by run ID so an old result cannot overwrite a newer card. Test reload during a running turn followed by success, failure, and fallback completion.

### 17. P2 — PostgreSQL test failures can produce green CI

**Location:** [PostgreSQL CI run step](../../.github/workflows/python-ci.yml#L115).

The pytest output is piped through `tee` without `pipefail` or an explicit `shell: bash`. The default Linux run shell's pipeline status comes from `tee`. A failing pytest run with no skipped tests can therefore pass the subsequent no-skips guard and the job.

**Evidence:** A shell reproduction using the same failure/pipeline/guard structure exited zero.

**Fix:** Set `shell: bash` or explicitly enable `set -o pipefail`; retain the no-skips guard. Test that an intentionally failing PostgreSQL case makes the workflow step fail.

### 18. P2 — Fork PRs cannot initialize the PostgreSQL CI service

**Location:** [PostgreSQL service configuration](../../.github/workflows/python-ci.yml#L87).

The disposable service password and connection URL depend on repository secrets. Ordinary fork `pull_request` workflows receive neither, so the database service has no configured password and initialization fails before tests run. This blocks an essential gate for external contributors to this public repository. [GitHub documents the fork-secret restriction](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

**Fix:** Use an isolated test-only PostgreSQL service with credentials independent of repository secrets, or test-only trust authentication, and construct its matching local URL. Keep production credentials out of the workflow and do not solve this by executing untrusted PR code with privileged secrets.

### 19. P2 — Provider projections mix primary identity with fallback results

**Locations:** [Model projection](../../apps/api/src/openship/runs/schemas.py#L108), [snapshot model projection](../../apps/api/src/openship/events/service.py#L65), [run-card label](../../apps/web/src/components/chat/RunCard.vue#L66).

The model summary takes an actual model from a successful fallback attempt but keeps the primary profile label, adapter, and destination. A card can say “Primary · backup-model at primary.test/v1” when the backup destination answered. Separately, snapshot projection omits its `attempts` argument altogether, losing the actual serving-model alias even when GET run reports it.

**Evidence:** Projection returned a primary destination paired with the backup model; the same run/attempt reported an actual alias through GET projection but null through snapshot projection.

**Fix:** Represent pinned configuration and actual serving attempt distinctly. Populate serving adapter/profile/destination/model consistently in GET, snapshot, and terminal events, and render them without conflation. Test successful cross-provider fallback and requested-model aliases across reload.

### 20. P2 — Live qualification rejects aliases the runner permits

**Location:** [Smoke assertion](../../apps/api/tests/live/test_provider_smoke.py#L151).

The smoke test requires the actual serving model to equal the requested model, although the runner allows explicitly qualified aliases. An authorized successful alias response therefore cannot pass live qualification.

**Fix:** Apply the same allowed-model rule as the runner, while recording requested and observed model names separately. Add a network-free smoke-test regression using an explicitly qualified alias.

## Recommended Fix Sequence

1. Close destination leaks: disable native SDK redirects, correct private-HTTP validation, and recheck profile revocation before dispatch.
2. Restore normal startup and browser controls: support dotenv credential bindings and align actual SSE envelopes with the browser parser.
3. Repair transaction boundaries: rollback before failure publication, commit admitted attempts before requests, and serialize cancellation against completion.
4. Correct provider normalization and supervision: filter Google thought parts, map native 5xx/refusal responses, and supervise counting/backoff.
5. Repair recovery/UI truthfulness: safe incompatible-snapshot projections, snapshot-first run-card hydration, stable submission retries, terminal detail refresh, and distinct serving metadata.
6. Make qualification trustworthy: failing CI must fail, forks must run the disposable PostgreSQL lane, and smoke tests must accept explicitly qualified aliases.

Pair each change with the regression listed in its finding. Add a small set of wire-level SDK tests using mock HTTP transports and real SDK response types; the large shared suite can remain, but fake SDK-shaped clients should not be the only evidence for request counts, redirects, finish vocabulary, or reasoning filters.

## What Is Working Well

- Frozen run configuration separates model choice from mutable installation defaults.
- The provider contracts and common runner keep SDK objects out of graph state and browser contracts.
- Explicit fallback selections and server-owned profile IDs avoid browser-supplied endpoints or credentials.
- Unknown usage remains nullable, and conservative reservations avoid treating unknown failed usage as free.
- Partial output is not retried as though no answer had begun.
- API lint, unit, PostgreSQL, browser, and live qualification are separated into useful lanes.
- Support documentation correctly keeps real-provider qualification and user acceptance pending.

## Review Boundaries

Tools, workflows, cloud mutations, and a new live token-delta transport were not evaluated as missing Phase 3 functionality because the plan explicitly defers them. No live model tuple is certified by this review. Full historical database-upgrade compatibility remains unqualified: the tests reconstruct Phase 2 using the edited migration chain rather than upgrading a database created by the original historical migrations.

## Follow-up Implementation

The fixes cover all 20 findings: SDK redirect protection, dotenv credential resolution, dispatch-time profile authorization, complete SSE envelopes, atomic failure publication, durable attempt admission, serialized cancellation, Google thought filtering, supervised counting and backoff, provider 5xx/refusal normalization, private-host validation, safe snapshot projections, snapshot-based browser recovery, idempotent submission retry, terminal-detail refresh, serving-provider projection, PostgreSQL CI failure/fork behavior, and qualified live aliases.

Verification on the updated worktree:

| Check | Result |
| --- | --- |
| API unit suite, excluding PostgreSQL and live-provider lanes | **338 passed, 20 deselected**, 2 existing deprecation warnings |
| API Ruff and diff check | Passed |
| Web unit suite | **128 passed** |
| Web TypeScript check and production build | Passed |
| Focused OpenAI adapter tests after using the real SDK content-filter type | **18 passed** |
| Network-free live-smoke alias qualification | **1 passed, 2 live cases deselected** |

The new PostgreSQL stale-cancel and crash-admission regressions were added but not run because `TEST_POSTGRES_URL` is unavailable. Live provider calls and Playwright were not run. The API tests use synthetic providers and make no live model requests.
