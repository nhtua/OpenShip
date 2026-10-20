---
type: testing
summary: Acceptance boundary and qualification evidence for Phase 4A trusted manual search and immutable filesystem artifacts.
status: review
date: 2026-10-20
---

# Phase 4A Acceptance

**Decision: pending review.** Phase 4A is limited to explicit, model-free Brave
and Tavily search runs, safe operation snapshots, and immutable filesystem
artifacts. It does not include model-directed tool calls, a provider-configuration
UI, mutable workspace files, or sandbox execution. Those remain in later phases.

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

## Local evidence recorded for this review

| Check | Evidence | Limit |
| --- | --- | --- |
| Task 8 API operation and snapshot tests | 18 passed, 1 skipped in the original focused run | The skipped PostgreSQL snapshot case was subsequently exercised in the full PostgreSQL lane below. |
| Task 9 packaging assertions and API lint | 4 passed; Ruff clean | Static assertions cover volume sharing, key placement, loopback ports, sandbox exclusion, examples, and production API/SSE proxying. |
| Compose configuration | `podman compose --env-file .env.example config` passed | Uses placeholder-only values; rendered configuration was kept out of the terminal output. |
| PostgreSQL integration lane | 28 passed, 0 skipped | Ran against a disposable local PostgreSQL 15 container. |
| Synthetic API/runtime process-stack lane | 1 passed, 0 skipped | Runs the real API and worker with PostgreSQL and filesystem artifacts; synthetic transport covers both tools, API restart/download recovery, cancellation, and no duplicate upstream request. |
| Browser unit tests | 137 passed | All service responses are local fixtures. |
| Vue typecheck and production build | Passed | — |
| Fixture browser journeys | 49 passed in the complete E2E suite | Brave/Tavily behavior used deterministic browser fixtures. |
| Browser full-stack test | 1 passed against the temporary synthetic API/runtime stack | Playwright used the real API and worker through Vite, with both synthetic search tools enabled and no `/api` interception. No deployed Compose browser stack was used. |
| Live provider qualification | Not run | It is opt-in and is not required for ordinary CI or Phase 4A acceptance. |

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

The user should review the recorded PostgreSQL and synthetic full-stack
evidence before Phase 4B implementation begins. If any required lane skipped,
ran zero tests, redispatched after cancellation, or failed exact-byte recovery,
Phase 4A remains open for revision.
