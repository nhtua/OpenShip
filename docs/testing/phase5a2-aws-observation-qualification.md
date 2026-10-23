---
type: testing
summary: Synthetic qualification evidence and open acceptance items for Phase 5A.2 AWS S3 observations.
date: 2026-10-23
status: candidate
---

# Phase 5A.2 AWS observation qualification

**Technical status: synthetic qualification candidate. User acceptance: pending.**
The 5A.2 implementation uses synthetic credentials and protocol-compatible
AWS responses. No real AWS credentials, customer buckets, or live AWS requests
were used. This record stops at the 5A.2 accept/revise/stop gate; it does not
authorize Phase 5B.

## Implementation identity

| Surface | Recorded revision |
| --- | --- |
| AWS Connector configuration | schema version 1; project visibility only |
| AWS source credential | `AwsBootstrapAuth` schema version 1; access key plus secret and optional expiring source session token |
| Connection check | `aws_check_connection` version 1.0.0; manifest digest `57880713586af60865d60a3c240a2d5b4b3d31674355b6d6eeaf7f097d750491` |
| S3 storage observation | `aws_s3_bucket_storage` version 1.0.0; manifest digest `f3a0db4c6239c4f3a5c45fb8c674272d89e62ec8ced258869f363401ab70c296` |
| Shared AWS implementation | digest `4463abe2439a253aad09a1b51c8ee9fdada5a81274ab9679592826b317259597` |
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
| Frontend | `pnpm test:run` passed 324 tests; `pnpm typecheck` and `pnpm build` passed. | Local web worktree. |
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

## Remaining qualification items

- The complete non-PostgreSQL API selector was not completed locally. A run
  hung in the shared `auth_token` fixture inside Starlette/httpx TestClient's
  AnyIO portal after printing one failure without a summary. A fail-fast run
  identified and fixed a stale capability-catalog assertion after 511 tests;
  the focused catalog file then passed (4 tests), but the broad selector was
  not rerun to completion.
- The AWS worker hard-crash boundaries are covered in separate durable runs.
  A local API server process restart was not exercised because the sandbox
  denied socket creation; the route's persisted status projection was read
  directly with a fresh database session after each crash. Existing generic
  full-stack managed-search tests cover API restart and worker death in a real
  server process.
- The required PostgreSQL cases were not run locally because no local test
  service was configured. CI must show all PostgreSQL cases passing with zero
  skips.
- No live comparison was made against an independently read AWS identity and
  bucket metric. This requires separate opt-in and remains unperformed.
- No denied mutation was sent to any AWS target. The registered AWS clients
  expose only `GetCallerIdentity`, `GetBucketLocation` and `GetMetricData`.

## Acceptance decision

**Decision: pending.** Review the implementation and evidence above, including
the restart and live-comparison limits. Accept, request specific revisions, or
stop 5A.2 here. Do not begin Task 15 / Phase 5B before acceptance.
