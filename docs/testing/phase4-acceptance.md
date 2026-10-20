---
type: testing
summary: Acceptance boundary and qualification evidence for Phase 4A trusted manual search and immutable filesystem artifacts.
status: review
date: 2026-10-20
---

# Phase 4A Acceptance

**Decision: Task 9A visually accepted; continue implementation.** The user
reports Phase 4A functionality works but requested compact search entries in
the conversation, tool attachment through `[+]` below the input, and a small
model dropdown defaulting to `gpt-6-luna`. Spec §8.1 and plan Task 9A record the
approved design. Task 9A is implemented and fixture-verified; the user accepted
the visual result and directed continuation on 2026-10-20. This report is user
walkthrough feedback, not qualification of every live provider/model tuple. The earlier
technical evidence below remains historical evidence for Tasks 1–9.

Phase 4A is limited to explicit, model-free Brave
and Tavily search runs, safe operation snapshots, and immutable filesystem
artifacts. Phase 4B adds model-directed tool calls through the same gateway.
Phase 4C associates immutable artifacts with conversations and exposes bounded
list/read tools. Mutable files and sandbox execution remain in Phase 6.

## Phase 4B qualification status

The first tool-capable tuple is OpenAI Chat Completions with GPT-6 Luna. Its
profile must explicitly declare `tool_calls: true`, keep parallel calls off,
and set `tool_call_reasoning_effort: "none"`; the adapter includes that
parameter only when the request carries tools. The official [GPT-6 Luna model
documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)
requires `reasoning_effort` to be `none` for Chat Completions function calls.

On 2026-10-20 UTC, one bounded direct search qualification passed for each of
`brave_web_search@1.0.0` and `tavily_web_search@1.0.0`. A separate live
GPT-6 Luna smoke completed one Brave tool call and a final answer using two
model requests against the declared OpenAI endpoint, with no fallback. No
result-ranking or answer-quality claim is made. Exact tool manifest and
implementation digests are recorded in `docs/provider-support.md`. The direct
handler smoke creates no durable operation ID.

Offline provider/guard tests and the fixture browser tool-chat test pass. The
synthetic PostgreSQL chat-v2 full-stack test is implemented but skipped here
because `TEST_POSTGRES_URL` is unavailable. The actual-browser full-stack spec
also requires the running application services and was not run. Phase 4B
acceptance remains pending; implementation is continuing at the user's
direction.

Phase 4A is ready for acceptance only when the checks below have evidence from
the CI qualification lanes or an equivalent disposable environment. A local
unit run or fixture browser run does not substitute for PostgreSQL locking,
process restart, or persistent filesystem proof.

## Acceptance checks

- Plain chat remains available when search is disabled and unrelated search
  credentials are absent. Brave and Tavily availability are independent.
- Compose gives the API and worker the same persistent artifact volume,
  `ARTIFACT_ROOT`, and stable storage ID. Host ports bind to loopback.
- Compose passes Brave and Tavily keys to the runtime worker only. Public
  capability summaries, operation reads, logs, and browser state contain no
  credential values.
- A manual search uses the exact selected tool and request. Results and
  immutable artifact bytes survive an API restart and snapshot reconstruction.
- Cancellation interrupts active synthetic transport; restarting the worker
  does not repeat that upstream request.
- The ordinary API unit lane excludes `postgres`, `provider_live`, and
  `full_stack`. PostgreSQL and synthetic full-stack qualification each run at
  least one case and reject skips. Ordinary PR checks make no live provider
  requests and require no provider credentials.
- Frontend CI runs unit tests, Vue typecheck, and production build. Fixture E2E
  evidence remains labeled separately from full-stack browser evidence.
- Task 9A: search operations appear as compact expandable entries in the
  chronological conversation, with one status, visible query/service, bounded
  result details and secondary retained-file actions; earlier searches survive
  later chat/search runs and reload.
- Task 9A: one composer provides `[+]` tool attachment and a model-name dropdown
  below the input. An attached search uses the existing manual path; ordinary
  chat defaults to the configured `gpt-6-luna` profile. Exact retries, draft
  preservation, independent tool availability and historical provider records
  remain correct. Automatic model invocation is still Phase 4B.
- Task 9A: provide keyboard/accessibility checks and screenshots at 375px and
  1440px in both themes for key composer/result states, followed by the user's
  visual accept/revise/stop decision.

## Local evidence recorded for this review

| Check | Evidence | Limit |
| --- | --- | --- |
| Task 8 API operation and snapshot tests | 18 passed, 1 skipped in the original focused run | The skipped PostgreSQL snapshot case was subsequently exercised in the full PostgreSQL lane below. |
| Task 9 packaging assertions and API lint | 4 passed; Ruff clean | Static assertions cover volume sharing, key placement, loopback ports, sandbox exclusion, examples, and production API/SSE proxying. |
| Compose configuration | `podman compose --env-file .env.example config` passed | Uses placeholder-only values; rendered configuration was kept out of the terminal output. |
| PostgreSQL integration lane | 28 passed, 0 skipped | Ran against a disposable local PostgreSQL 15 container. |
| Synthetic API/runtime process-stack lane | 1 passed, 0 skipped | Runs the real API and worker with PostgreSQL and filesystem artifacts; synthetic transport covers both tools, API restart/download recovery, cancellation, and no duplicate upstream request. |
| Tasks 1–9 browser unit baseline | 137 passed | All service responses are local fixtures. |
| Vue typecheck and production build | Passed | — |
| Tasks 1–9 fixture browser journeys | 49 passed in the complete E2E suite | Brave/Tavily behavior used deterministic browser fixtures. |
| Browser full-stack test | 1 passed against the temporary synthetic API/runtime stack | Playwright used the real API and worker through Vite, with both synthetic search tools enabled and no `/api` interception. No deployed Compose browser stack was used. |
| Live provider qualification | Not run | It is opt-in and is not required for ordinary CI or Phase 4A acceptance. |

## Task 9A implementation evidence

| Check | Evidence | Limit |
| --- | --- | --- |
| Browser unit tests | 151 passed across 16 files | Deterministic local fixtures; expected failure-path logging and legacy component test warnings do not fail the suite. |
| Vue typecheck and production build | Passed | — |
| Fixture Playwright | 50 passed in the full fixture suite; the updated visual capture test also passed on the final source | No live Brave, Tavily or model requests. A local Vite server required loopback execution permission. |
| API projection/event service tests | 6 operation projection/event tests and 5 event-service tests passed; changed API Ruff checks passed | The existing local Python HTTP harness stall still prevents claiming end-to-end HTTP route serialization from this run. |
| Responsive and theme review | Captured and inspected 375px and 1440px views in light and dark themes. The capture test checks document width against viewport width at desktop and mobile sizes. Keyboard tests cover arrow movement, Escape/focus return and IME submission behavior. | Screenshots are local review artifacts, not committed product assets. |
| Full-stack browser search journey | Not rerun for Task 9A | That journey dispatches a real external search; the revision does not require a live-provider request. Earlier synthetic API/runtime/PostgreSQL qualification remains recorded above. |

Screenshots are in `.superpowers/sdd/2026-10-19-phase4-execution-and-conversation-workspace/screenshots/`.
Representative files: `task9a-desktop-collapsed.png`,
`task9a-desktop-expanded.png`, `task9a-desktop-tool-menu.png`,
`task9a-desktop-model-menu.png`, `task9a-mobile-dark-expanded.png`,
`task9a-mobile-dark-tool-menu.png`, and `task9a-mobile-dark-model-menu.png`.

## Commands

From `apps/api`, run the network-free API lane with:

```bash
uv run pytest -q -m "not postgres and not provider_live and not full_stack"
```

Run PostgreSQL integration and synthetic full-stack qualification against a
disposable PostgreSQL 15 service:

```bash
TEST_POSTGRES_URL='postgresql+psycopg://openship@localhost:5432/lane' \
  uv run pytest -vv -ra -m postgres tests/integration
TEST_POSTGRES_URL='postgresql+psycopg://openship@localhost:5432/lane' \
  uv run pytest -vv -ra -m full_stack tests/full_stack/test_manual_search.py
```

The full-stack API test launches the real FastAPI process and durable worker,
uses PostgreSQL and a shared filesystem directory, and substitutes only the
search network transport with deterministic synthetic Brave/Tavily responses.
It verifies exact downloaded bytes after an API restart and one upstream
request after cancellation and worker restart.

The browser full-stack test uses the application without intercepting `/api`
commands. Either point it at a running Compose web endpoint, or start the API
and runtime separately and let Playwright start Vite:

```bash
cd apps/web
OPENSHIP_FULL_STACK_API_URL=http://127.0.0.1:8000 pnpm test:e2e:full-stack
```

Both search tools must be enabled in that stack and backed by synthetic
transports for repeatable qualification. For a deployed Compose UI, set
`OPENSHIP_FULL_STACK_URL=http://127.0.0.1:8080` and run the same command.

## Review state

The implemented UI amendment in [spec §8.1](../superpowers/specs/2026-10-19-phase4-execution-and-conversation-workspace-design.md#81-composer-and-tool-result-ux-amendment)
and [Task 9A](../superpowers/plans/2026-10-19-phase4-execution-and-conversation-workspace.md#task-9a--integrated-composer-and-compact-conversation-tool-results)
was visually accepted by the user on 2026-10-20. The user directed continuation
through the remaining plan. Task 10's normalized contracts, tool-mode turn
pinning, migration and durable transcript are implemented; Task 11 is underway.
Task 14 remains the planned Phase 4B evidence checkpoint. Live provider tests
remain opt-in and were not run.

The previously recorded PostgreSQL and synthetic full-stack evidence remains
part of the Phase 4A qualification record. If a required lane skipped, ran zero
tests, redispatched after cancellation, or failed exact-byte recovery, record
that gap for the final acceptance review.
