---
type: testing
summary: Synthetic full-stack evidence and user acceptance gate for Phase 5A.1 managed search Connectors.
status: review
date: 2026-10-22
---

# Phase 5A.1 Managed Connector Qualification

**Technical requalification: passed. User acceptance: pending.** The revised
Project Settings, Global search catalog, legacy cutover and failure-recovery
journeys were exercised against the real API, worker, PostgreSQL and local
artifact store. Search keys and transports were synthetic. No provider or AWS
request was made. Do not start Task 8 or admit AWS credentials before the user
records an acceptance decision.

## Requalification evidence

| Check | Evidence | Limit |
| --- | --- | --- |
| Managed and legacy full-stack selector | 2 passed, 0 skipped in 57.88s: legacy search restart/cancellation and managed Connector lifecycle; Python 3.13.7, pytest 9.1.1. The JUnit guard verified a nonzero test count and zero skips. | The local lane used two uniquely named disposable PostgreSQL databases. The GitHub lane also covers Python 3.11. |
| Real-stack browser journey | Two Playwright browser tests passed in the managed Connector test: the operator configured Brave/Tavily from the Global catalog, created a second project, chose each shared default independently in both projects, switched projects and reopened the Global cards; a second test submitted an actual origin-rejected write and verified a definite rejection, no key resubmission and no status poll. A separate one-test Playwright invocation passed the missing-HMAC setup journey. The harness enforces exactly 2 and 1 passing tests respectively, with zero skips. | Chromium against local Vite and the test API; synthetic keys only. The uncertain Brave write was committed by the real API before Playwright replaced its response with a synthetic 503. |
| Legacy private connector cutover | A historical Project Tavily record and active default were inserted directly as encrypted legacy fixtures. The browser changed that project's default to the shared Global Tavily connector. PostgreSQL checks confirmed the old private connector, credential ID and ciphertext remained unchanged; no key was copied or returned. | The fixture represents a pre-policy record; no production connector was migrated. |
| Scope, authority and evidence ownership | Non-operator Global list/create/update requests were denied; new private Brave creation returned `connector_visibility_unsupported`. Two projects owned by one user independently saved the same Global Brave/Tavily IDs. Synthetic runs and artifacts in both projects were checked against their stored `project_id` and conversation IDs. Existing cross-owner denial and retained-evidence checks also passed. | Deterministic synthetic provider and worker; artifact files were temporary. |
| Uncertain and rejected writes | The Brave create committed in PostgreSQL, its response was replaced with 503, and the UI reconciled the request to completion without resending the key. The origin-rejection browser path received the real API's 403, cleared the key and made no status request. | The lost response was simulated at the browser proxy; the request and subsequent status read used the real API. |
| Unprovisioned installation | Restarted the API with an unavailable HMAC-key path. The page showed an actionable setup message, the rotate action was disabled and the browser observed no write. | The temporary key path was isolated under the test directory; no local keyring or `.env` value was changed. |
| PostgreSQL migration and lifecycle lane | 5 selected tests passed, 0 skipped in 27.78s: Connector migration/data-retention constraints, Project migration and deletion races, and Connector command/idempotency behavior. One non-PostgreSQL parameter case was deselected by `-m postgres`. | Ran on a separate uniquely named disposable database; it was dropped after the lane. |
| Frontend and static checks | `pnpm test:run` — 268 passed; `pnpm typecheck`, `pnpm build`, and Ruff passed. `git diff --check` is recorded with the final review. | Build and test runs were local on the PR worktree. |
| Secret sentinel checks | Synthetic key values were absent from PostgreSQL table values, captured API response bodies/headers, API and worker logs, call records, artifacts, and the browser's create/status/rejection responses. The synthetic transport call log contained safe labels only. | Checks covered the disposable database and artifact directory produced by the tests. |

The browser suite exposed and fixed one project-creation event bug: after the
menu closed, the same document click closed the newly opened Create Project
form. The create-menu action now stops propagation, with AppLayout regression
coverage and the real browser project-creation path.

## Commands and lane selection

The managed/legacy CI selector was run from `apps/api` with the API connection
setting used only to create disposable databases:

```bash
uv run pytest -vv -ra -o faulthandler_timeout=180 \
  --junitxml=/tmp/openship-phase5a1-full-stack.xml \
  -m full_stack \
  tests/full_stack/test_manual_search.py \
  tests/full_stack/test_managed_connectors.py
```

The PostgreSQL migration/lifecycle selector was run against another disposable
database:

```bash
uv run pytest -vv -ra -o faulthandler_timeout=180 \
  --junitxml=/tmp/openship-phase5a1-postgres.xml \
  -m postgres \
  tests/integration/test_connector_migration_postgres.py \
  tests/integration/test_project_lifecycle_postgres.py \
  tests/integration/test_connector_commands_postgres.py
```

The ignored API `.env` supplies `TEST_POSTGRES_URL` from the existing
`DATABASE_URL` only as the service connection used to create and drop uniquely
named disposable test databases; tests did not target the named development
database. Provider keys, temporary encryption/HMAC keys and transports were
synthetic. No provider credential from `.env` was used.

## Acceptance decision

**Decision: pending.** The user requested the 5A.1 revisions before accepting.
Those revisions are implemented and technically requalified. Choose one:

- **Accept 5A.1:** authorize starting Task 8 (AWS SDK runner), still using
  synthetic tests until any separate live-service authorization.
- **Revise:** identify the 5A.1 behavior or evidence that needs more work.
- **Stop:** leave the managed search implementation as the current scope.

Task 8 and AWS implementation remain unstarted. Live provider/AWS qualification
and real AWS credentials remain outside this revision.
