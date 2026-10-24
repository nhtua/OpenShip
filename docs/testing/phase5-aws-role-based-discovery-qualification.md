---
type: testing
summary: Candidate qualification for binding-free AWS discovery and bounded reads through the project's basic role.
date: 2026-10-24
status: candidate
---

# Phase 5 AWS role-based discovery qualification

**Status: candidate; user acceptance and broader configured-lane qualification
remain open.** This report covers the replacement for the binding-based 5B candidate,
not the already accepted 5A.2 demonstration. No live AWS requests or mutations
were made for this qualification.

The implementation follows the [AWS discovery design](../superpowers/specs/2026-10-23-phase5-aws-role-based-discovery-design.md)
and [replacement plan](../superpowers/plans/2026-10-23-phase5-remove-bindings-and-enable-aws-discovery.md).
Old environment, resource, binding, observation and evidence rows are retained
for history. New AWS reads take targets at execution time and use only the
selected project's pinned Connector and basic-role session. AWS decides access
for each request. A failed discovery request does not prevent a separate read
of a named target when that read is permitted.

## Verified behavior

- The maintained `aws_read_catalog` reports supported local SDK operations and
  parameter schemas; `aws_read` executes one bounded operation. Catalog support
  is not a claim that the connected role has permission.
- The checked-in catalog is validated against Botocore 1.43.118. The acceptance
  fixtures exercise S3 discovery and reads, CloudWatch `GetMetricData`, EC2
  `DescribeInstances`, RDS `DescribeDBInstances` and STS identity checks.
- The direct S3 storage and metadata helpers accept a project Connector plus a
  bucket name and optional region. They do not need an OpenShip resource row,
  environment or binding.
- New observations and evidence publish under the existing connector, policy,
  cancellation, ownership and run-lease fences. Historical result projections
  remain available. Cancelled provisional artifact reservations are tombstoned,
  quota is released and uncommitted bytes are absent.
- Resource/environment creation endpoints return owner-scoped
  `410 resource_binding_retired`; foreign owners are hidden with `404`. The
  routes do not create new resource, environment or binding rows.
- Global Settings has one broad AWS reader control. `TOOL_ENABLED_IDS` remains
  an optional deployment ceiling, not a required per-operation list. The
  reader's supported catalog is the runtime boundary; AWS policy remains the
  permission boundary.

## Read and reporting limits

Each provider operation is bounded to 30 seconds, 8 physical requests
(including role acquisition and identity checks), 2 MiB per HTTP response,
64 KiB of evidence and 16 KiB of model result. Catalog pages default to 20 and
cap at 50. AWS calls are read-only and use the configured basic role. Mutations,
secret-value retrieval, secondary role assumption, event streams and arbitrary
object-body downloads are unsupported. Unsupported operations are reported
separately from AWS permission denials.

For the S3 report, `AWS/S3` `BucketSizeBytes` and `NumberOfObjects` values are
daily CloudWatch metrics. `NumberOfObjects` uses
`StorageType=AllStorageTypes`. These values are not an instantaneous object
scan. Missing or denied fields remain unknown; measurements from different
dates are not combined as if they were simultaneous. Public policy status,
Block Public Access settings and ACL grants are distinct evidence and do not
prove every caller's effective access. See the [operations guide](../operations/aws-s3-observations.md)
for IAM setup and interpretation.

The direct-reader execution profile allows up to 12 tool calls, 13 model turns
and 15 generation attempts, subject to any lower operator/profile limit. A
complete 12-call report needs 13 generation attempts so the model has a final
turn to summarize results. The qualified report profile must set
`max_attempts=15`; global model defaults are unchanged by this feature.

## Verification evidence

| Check | Result | Qualification limit |
| --- | --- | --- |
| API non-service suite: `uv run pytest tests/ -q -m 'not postgres and not provider_live and not full_stack'` | **919 passed, 67 deselected**, 96 warnings, 281.67s. | Run outside the sandbox because its AnyIO `BlockingPortal` blocks under sandbox thread coordination. This selector excludes PostgreSQL, provider-live and full-stack tests. |
| API Ruff: `uv run ruff check src tests scripts` | Passed. | No database or live provider calls. |
| Web unit suite: `pnpm test:run` | 34 files, 343 tests passed. | Expected test doubles emit network-failure and SSE diagnostics on stderr. |
| Web typecheck and production build: `pnpm typecheck`, `pnpm build` | Both passed. | Browser artifacts are built locally. |
| AWS discovery migration, SQLite | **1 passed** after adding invalid connector, revision, operation and artifact reference cases. | Temporary SQLite file; no live provider calls. |
| AWS discovery migration, PostgreSQL | **1 passed** on a fresh UUID-named database. | Test database was dropped after the run; the configured database was not modified. |
| Legacy resource and project lifecycle migrations, PostgreSQL | **5 passed** across the two modules. | Separate fresh UUID-named databases; stale `phase5b013` expectations were updated to the new `phase5b014` head. |
| AWS publication / recovery, PostgreSQL: `tests/integration/test_cloud_observation_publication_postgres.py -m postgres` | **9 passed, 12 deselected**. | Fixture uses a unique temporary schema and synthetic Botocore responses; includes catalog discovery, unbound AWS read, worker publication and evidence reopen. No live AWS calls. |
| Managed-connector API/worker and browser fixture: `tests/full_stack/test_managed_connectors.py -m full_stack` | **1 API test passed**; all five embedded Playwright scenarios passed across the normal and missing-HMAC browser runs. | Uses a fresh UUID-named temporary PostgreSQL database, which the fixture drops. Search lifecycle uses synthetic upstream responses. AWS browser coverage mocks cloud responses and verifies direct target payloads, automatic mode and zero binding/environment setup requests. |
| Full PostgreSQL integration selector: `pytest tests/integration -m postgres` | Not run as a whole. | Existing fixtures include direct migrations/truncations against `TEST_POSTGRES_URL` and drops of fixed-name databases. Relevant AWS, resource migration, project lifecycle and publication lanes were instead run on isolated databases/schemas to avoid touching existing data. |
| Independent live AWS comparison | Not performed. | Requires separate authorization. No IAM changes or live resource reads were made. |

## Manual review scenarios

1. Configure and check one AWS Connector; create no environments or resource
   bindings.
2. Enable AWS reads in Global Settings without adding individual operation IDs
   to `TOOL_ENABLED_IDS`.
3. Ask: “Discover my S3 buckets. For `<bucket-name>`, report size, object
   count, versioning and public-access configuration. Cite evidence and mark
   inaccessible fields unknown.”
4. With bucket listing denied but a known bucket readable, ask for a direct
   read and confirm OpenShip reports the actual operation result.
5. Ask for EC2 instances and RDS databases in the requested region; inspect
   the connector/account, retrieval time, partial results and exact evidence.
6. Reopen old binding-based evidence and new observations after restart. Check
   project isolation, archive/restore and automatic artifact refresh.

This is candidate evidence only. Phase 5 remains incomplete until the full
PostgreSQL selector can run safely, the remaining qualification gaps are
resolved or explicitly accepted, and the user accepts the revised flow.
