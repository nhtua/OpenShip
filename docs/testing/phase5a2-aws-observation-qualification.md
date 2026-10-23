---
type: testing
summary: User-accepted Phase 5A.2 AWS observations, review fixes, synthetic qualification evidence and remaining independent checks.
date: 2026-10-23
status: accepted
---

# Phase 5A.2 AWS observation qualification

**User acceptance: accepted on 2026-10-23 after testing the corrected live AWS flow.**
Automated qualification uses synthetic credentials and protocol-compatible
AWS responses. The user separately configured and tested their own AWS
Connector, bound bucket and storage measurement, then explicitly accepted
5A.2. Agent troubleshooting inspected local configuration and bounded local
database metadata, but made no live AWS requests. The independent live
comparison and required PostgreSQL evidence below remain distinct open items.
The user subsequently requested a revised 5B plan; no 5B implementation is
included in this acceptance change.

## Implementation identity

| Surface | Recorded revision |
| --- | --- |
| AWS Connector configuration | schema version 1; project visibility only |
| AWS source credential | `AwsBootstrapAuth` schema version 1; access key plus secret and optional expiring source session token |
| Connection check | `aws_check_connection` version 1.0.0; manifest digest `42652615a50bca716d20922d0c358536fe04aeb0cee137097a746160478c8647` |
| S3 storage observation | `aws_s3_bucket_storage` version 1.0.0; manifest digest `c55b4550df2c32ef87affd6a14ba5fe566f3a964ff0b4a4990e193d8bc620d41` |
| Shared AWS implementation | digest `eab9ee08998c69f47adf06c806a666b1e3d51cd52c7df9795d3ce57b21c021dc` |
| AWS SDK | Boto3 1.43.118 / Botocore 1.43.118 |
| Storage observation bounds | 30 seconds, 8 AWS requests, 64 queries, 1,024 datapoints, 2 MiB maximum HTTP response, 64 KiB evidence and 16 KiB model result |

Digests are from the checkout qualified on 2026-10-23. A change to the
registered schemas, limits or implementation files changes the manifest
identity and requires requalification.

## Verification evidence

| Check | Result | Limit |
| --- | --- | --- |
| AWS broker, SDK transport, connector lifecycle, readiness, bindings, blockers, storage handlers, publication and resume targeted API suites | 154 passed, 3 skipped in 46.43s. The three skips are PostgreSQL-only cases because local `TEST_POSTGRES_URL` was not configured. | Python 3.13; synthetic keys and HTTP responses. |
| AWS worker crash/restart checkpoints | 3 passed. Separate worker subprocesses hard-exit before intent, after six synthetic AWS requests, and immediately before artifact publication. The pre-intent run is safely requeued and completes once; dispatched runs become interrupted, and replacement workers issue no additional AWS requests. Each crash state is reopened through the run-status route with a fresh database session. | File-backed SQLite and synthetic transport. The route function was exercised directly; the API server process was not started locally because sandbox socket creation is denied. |
| Resource and Connector migration checks | 4 passed, 4 PostgreSQL cases deselected. The SQLite migration cases verified migration head `phase5a012`, schema parity and refusal to downgrade after a credential payload has been purged. | PostgreSQL execution remains for the required CI PostgreSQL lane. |
| Revocation with a bound AWS resource | Regression test passed. Revocation prevents execution, replaces active credential bytes with non-secret sentinels, retains Connector revision and resource binding history, and removes the Connector from active lists. | Synthetic encrypted credential and SQLite test database. |
| AWS browser flow | One Playwright test passed. It covers project AWS setup, environment and bucket binding, then confirms the manual storage request carries only the selected binding ID. | Browser routes use synthetic API responses; no AWS request or real API write. |
| Frontend after review fixes | `pnpm test:run` passed 327 tests; `pnpm typecheck` and `pnpm build` passed. | Local web worktree; includes admission-before-navigation and disabled/submission-error cases. |
| Production AWS destination checks and SDK/broker/storage/publication regressions | 77 passed, 2 PostgreSQL-only cases skipped in 22.60s. Native SDK and positive production transport tests cover normal credential-free URLs, S3 paths/queries, rejected unsafe targets and suppression of SDK debug credentials. | Synthetic credentials; network I/O stubbed below the production transport's destination validation. No live AWS request. |
| HTTP bucket-binding and foreign-key regressions | Resource/migration selector: 16 passed, 2 PostgreSQL-only cases skipped in 4.47s. Real HTTP UUID input persists a binding with foreign keys enabled; resource and binding roll back together. | Temporary SQLite databases; PostgreSQL cases remain for a configured test service. |
| Final combined accepted-fix API selector | 93 passed, 4 PostgreSQL-only cases skipped in 26.64s across resource, migration, AWS transport/SDK/broker/check/storage and synthetic publication tests. | No live provider requests; the broad unit lane remains inconclusive below. |
| API formatting | Full Ruff and `git diff --check` are recorded after the final plan/docs edits. | Local checkout. |

The SDK proof creates actual Boto3 clients and passes signed requests through
the bounded transport into synthetic STS, S3 and CloudWatch responses. It checks
caller account and role, bucket owner and region, 22 fixed storage dimensions,
partial/missing/mixed-time values, request and output bounds, cancellation,
revocation, and atomic publication of the safe result, evidence and observation.
The test-only AWS handler also uses the same authenticated runtime context and
runner.

The existing API and worker CI selectors already include the new integration
and full-stack test files. The PostgreSQL CI lane runs every `postgres`-marked
integration case and fails if it reports skips. The synthetic full-stack lane
includes the managed Connector qualification file and likewise rejects skips.
No workflow selector change was required for these additions.

## User review fixes

- HTTP binding contracts accept JSON UUID strings while rejecting malformed IDs and invalid revisions. The parent resource is flushed before its binding in the same transaction; tests enforce the foreign key and verify rollback.
- AWS Connector details open on the first card selection. Check actions refuse disabled tools before creating a conversation, submit the tool command before navigation, and show admission failures instead of leaving an idle empty chat.
- The setup runbook and environment example name both AWS tool IDs. A development configuration reload was verified with a fresh API process and HTTP 200 response; this is not proof of durable in-flight recovery across an API restart.
- The production HTTPX transport accepts absent URL userinfo as empty strings and validates the endpoint origin separately from the SDK's S3 path/query. Destination, TLS, Host, proxy and public-address restrictions remain enforced.
- Managed SDK contexts suppress Boto3/Botocore debug output that includes signed request headers or secret-bearing STS bodies.

## Remaining qualification items

- The complete non-service API selector remains inconclusive locally. An
  earlier fail-fast run identified and fixed a stale catalog assertion; its
  focused file passed. A fresh run outside the sandbox on 2026-10-23 again
  stopped making progress and reached the 180-second timeout after more than
  600 passing-test progress markers, with no completion summary. This is not
  recorded as a passing full suite; CI must supply the complete lane result.
- The AWS worker hard-crash boundaries are covered in separate durable runs.
  Durable work across a local API server restart was not exercised by this
  AWS qualification; the initial sandbox denied socket creation and the
  route's persisted status projection was read directly with a fresh database
  session after each crash. The later development reload was only an idle
  configuration smoke check. Existing generic
  full-stack managed-search tests cover API restart and worker death in a real
  server process.
- The required PostgreSQL cases were not run locally because no local test
  service was configured. CI must show all PostgreSQL cases passing with zero
  skips.
- The user's successful live UI test establishes their acceptance, but no
  independently recorded identity/metric comparison for matching dimensions
  and timestamps was supplied. Agent-run live comparison remains unperformed
  and separately opt-in.
- No denied mutation was sent to any AWS target. The registered AWS clients
  expose only `GetCallerIdentity`, `GetBucketLocation` and `GetMetricData`.

## Acceptance decision

**Decision: accepted.** On 2026-10-23 the user stated that the corrected flow
worked and explicitly accepted implementation of Phase 5A.2. This satisfies
the user acceptance gate; it does not erase the qualification limits above.
The [revised 5B plan](../superpowers/plans/2026-10-23-phase5b-tool-availability-inventory-and-metadata.md)
aligns the next work with the accepted implementation and adds the requested
managed built-in tool availability. That plan is for review; 5B is undelivered.
