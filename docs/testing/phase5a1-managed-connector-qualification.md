---
type: testing
summary: Synthetic full-stack qualification evidence and user acceptance gate for Phase 5A.1 managed search Connectors.
status: review
date: 2026-10-22
---

# Phase 5A.1 Managed Connector Qualification

**Original technical qualification: passed. User acceptance: revisions requested.** Task 7 exercised
the real API and worker against a disposable PostgreSQL database and filesystem
artifact store. Every search credential was synthetic. No live search provider,
AWS service, or local `.env` value was used. The 5A.1 acceptance decision is a
separate user gate; do not begin Task 8 or admit AWS credentials before it.

## Qualification evidence

| Check | Evidence | Limit |
| --- | --- | --- |
| CI-selected full-stack lane | 2 passed, 0 skipped in 48.37s: legacy search restart/cancellation and managed Connector lifecycle; Python 3.13.7, pytest 9.1.1, disposable PostgreSQL 15 | Local run of the same file selector used by CI; the CI JUnit guard rejects zero tests or skips. |
| Scope and reuse | One operator-managed global Brave Connector was used by two different owners in separate projects. Each project listed the global Connector; only the owning user could list project Connectors. The project Brave Connector dispatched only for its owner; foreign selection was rejected before provider dispatch. | Synthetic transport; no external provider request. |
| Defaults and replacement | A project Tavily default was resolved when selection was omitted. Replacing its credential kept the Connector identity and changed the synthetic transport's recorded safe label from v1 to v2. | The transport records labels only, never credential bytes. |
| Revocation and recovery | Global revocation during a delayed request prevented late publication and blocked later dispatch. A queued command survived an API restart before dispatch. Killing the worker during a provider request and starting another worker produced a terminal run with one upstream call. | Delays and provider responses are deterministic synthetic fixtures. |
| Retained evidence | Published artifacts and the conversation snapshot remained readable after API restart by their owning users. A different project owner received 404 for the retained artifact. | Artifact storage is a temporary local filesystem root. |
| Secret sentinel scan | Synthetic provider-key sentinels were absent from all reflected PostgreSQL table values, captured HTTP response bodies and headers, API/worker logs, synthetic call records, and artifact files. Call records contained only safe credential labels. | Scans the disposable test database and artifact root created by the test. |
| Static checks | Ruff and `git diff --check` passed. | — |

The worker restart case exposed a configuration-name mismatch: the worker read
an undeclared `worker_reconcile_interval` and silently used 60 seconds instead
of the declared `reconciler_interval`. The worker now reads the declared
setting, allowing expired in-flight work to be reconciled on the configured
interval. The end-to-end no-replay assertion passes with that fix.

The additional focused command
`pytest tests/test_tool_worker.py tests/test_provider_worker.py` emitted ten
passing-test dots and then stopped making progress for over a minute; it was
interrupted without a completion summary. The full-stack worker-recovery case,
including the modified reconciliation setting, passed. This partial unit run
is not counted as a pass or failure.

## Acceptance decision

**Decision: revisions requested on 2026-10-22.** The user identified missing
project lifecycle/settings ownership, a rejected credential submission becoming
an uncertain status-check loop, and the need for a connector-card catalog with
configuration dialogs. The [proposed review revision plan](../superpowers/plans/2026-10-22-phase5a1-project-settings-review.md)
records the findings and scoped follow-up. Its implementation and renewed
qualification remain pending; the original test pass does not establish that
these browser behaviors work.

After the revisions are reviewed and qualified, choose one:

- **Accept 5A.1:** authorize starting Task 8 (AWS SDK runner), still using
  synthetic tests until any separate live-service authorization.
- **Revise:** identify the 5A.1 behavior or evidence that needs more work.
- **Stop:** leave the managed search implementation as the current scope.

No Task 8 implementation or AWS credential setup has started.
