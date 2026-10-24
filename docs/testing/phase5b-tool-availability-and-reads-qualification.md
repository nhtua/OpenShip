---
type: testing
summary: Candidate qualification evidence for Phase 5B managed tool availability, local S3 binding inventory and retained bucket metadata.
date: 2026-10-23
status: candidate
---

# Phase 5B tool availability and reads qualification

**Revision requested (2026-10-23):** The user requested replacement of resource
bindings with connector-scoped discovery/reads through the basic role. The
proposed [replacement plan](../superpowers/plans/2026-10-23-phase5-remove-bindings-and-enable-aws-discovery.md)
has separate acceptance requirements. Results below remain evidence for the
original binding-based candidate; they do not qualify the replacement.

**Status: candidate; awaiting user acceptance.** Implementation and the
synthetic qualification recorded here are complete enough for review. Phase 5
remains incomplete until the open API selector/live-comparison gaps are
resolved or explicitly accepted and the user accepts 5B. The user accepted
5A.2 on 2026-10-23; this report does not alter that gate.

No agent-initiated live AWS requests were made. The browser and API flows use
synthetic AWS credentials, synthetic service responses, or API fixtures. The
user's prior live 5A.2 tests are separate evidence.

## Implementation identity and behavior

The candidate adds managed installation-wide availability for installed
built-in tools, with revisioned operator policy and `TOOL_ENABLED_IDS` retained
as an explicit deployment ceiling. Project readiness continues to depend on
the existing project-owned AWS Connector and selected binding.

The local `aws_s3_list_bound_buckets` capability reads only active bindings
owned by the current project. It uses bounded, project-scoped cursors and does
not call the broker, decrypt credentials, read AWS keys, or make provider
requests. Its output does not change the run's selected resource references.

The `aws_s3_bucket_metadata` capability accepts one selected binding, resolves
its pinned account, region and bucket, verifies caller identity, owner and
location, and reads versioning through the production AWS SDK transport. Its
operation profile permits `GetBucketVersioning` only for this capability. It
does not widen the storage or connection-check handlers. It writes normalized
metadata to immutable `s3-bucket-metadata.json` evidence and publishes the
existing resource observation/event. It makes no bucket discovery, object
read, or mutation request.

## Verification evidence

| Check | Result | Limits |
| --- | --- | --- |
| Focused API policy, catalog, inventory, metadata, broker and SDK suite: `uv run pytest tests/test_tool_policy.py tests/integration/test_tool_policy_postgres.py tests/test_capability_routes.py tests/tools/test_bound_inventory.py tests/tools/test_s3_metadata.py tests/connectors/test_aws_operation_profiles.py tests/execution/test_availability.py tests/execution/test_aws_sdk.py tests/execution/test_contracts.py -q` | **71 passed, 1 skipped**, 2 existing deprecation warnings, 20.44s. | One PostgreSQL-specific policy test is skipped in this SQLite-only invocation; the configured PostgreSQL lane below covers marked integration tests. |
| PostgreSQL integration lane: `uv run pytest tests/integration -q -m postgres` | **56 passed, 65 deselected**, 2 warnings, 403.48s; no skips among selected tests. | Ran against a disposable PostgreSQL service/container; no live AWS calls. |
| API lint: `uv run ruff check src tests` | **Passed.** | Apps API directory. |
| Synthetic full-stack manual-search restart/cancellation test: `uv run pytest tests/full_stack/test_manual_search.py::test_manual_search_survives_restart_and_cancel_is_not_redispatched -q -m full_stack` | **1 passed.** | Synthetic providers and disposable PostgreSQL. |
| Managed Connector full-stack test: `uv run pytest tests/full_stack/test_managed_connectors.py::test_managed_search_reuse_isolation_rotation_revocation_and_restart_safety -q -m full_stack` | **1 passed**, 1 warning; its embedded Playwright run passed **4 Chromium browser scenarios**. | Covers live policy update without restart, shared search defaults, definite origin rejection, AWS binding/storage/metadata selection and automatic inventory selection through fixture-backed browser/API boundaries. The HMAC-missing setup test is excluded by the harness because this fixture intentionally provisions its HMAC file. |
| Web typecheck: `pnpm typecheck` | **Passed.** | Apps web. |
| Web unit suite: `pnpm test:run` | **36 files and 344 tests passed.** | Existing expected mock-error messages and framework warnings appear on stderr. |
| Web production build: `pnpm build` | **Passed.** | Apps web. |
| Focused metadata publication regression, included in the PostgreSQL lane | **Passed.** The synthetic native SDK path checks role acquisition, identity, location and versioning; the test verifies 5 requests, expected-owner headers, normalized observation/evidence, and opens the same verified evidence after reinitializing the artifact store. | Synthetic credentials and protocol-compatible provider responses. The artifact store is reopened; this does not claim a full API/worker restart during the metadata flow. |
| AWS policy-fence publication cases, included in the PostgreSQL lane | **Passed.** Disabling policy before dispatch results in zero provider requests; disabling after read, Connector revocation, or binding retirement prevents observation publication and removes any provisional artifact. | PostgreSQL transaction/publication tests with synthetic AWS transport. |
| Resource and policy isolation | **Passed in targeted cases.** Inventory excludes a foreign project's binding and rejects a cursor from another owner/project; metadata refuses unselected bindings before acquiring AWS access. | Separate focused API cases rather than one cross-owner live AWS workflow. |
| `git diff --check` | **Passed.** | Local worktree. |

The metadata unit cases cover enabled, suspended and never-enabled versioning,
malformed/unknown responses, mismatched identity/account/region, selected
binding enforcement, cancellation, output normalization and secret omission.
The capability-profile test confirms `get_bucket_versioning` is denied to
storage and connection-check access. The production-transport test uses a
real Botocore client and stubs network I/O beneath destination validation.

The policy tests cover the unset, empty and explicit deployment ceilings,
legacy cutover, revision preconditions, operator authorization, fail-closed
missing state and consistent shared policy. Browser coverage confirms that an
operator can enable and disable metadata from Global Settings and the API
reflects the update without a service restart.

## Remaining qualification gaps

- The complete non-service API selector was attempted with
  `uv run pytest tests/ -q -m 'not postgres and not provider_live and not full_stack'`
  but did not reach a completion summary. It stopped making progress at the
  existing HTTP binding test
  `test_binding_http_accepts_uuid_strings_and_persists_selected_target`, whose
  synchronous ASGI `TestClient` startup hangs in the current local FastAPI /
  Starlette environment. This is **not** counted as a pass; the full API
  selector still needs a successful CI result or a test-harness fix.
- No independent live AWS comparison was run. That requires the separate live
  opt-in in the plan and would add bucket-scoped `s3:GetBucketVersioning`
  authority to the independent comparison role.
- The candidate does not claim the entire accepted 5A.1/5A.2 regression list
  was rerun as one complete selector. User acceptance for 5A.2 remains valid;
  the 5B-specific regression subset and the listed full-stack flows passed.
- A second-owner end-to-end metadata flow was not run. Owner/project isolation
  is covered by the local inventory tests, and metadata evidence is verified
  after reopening its artifact store; the metadata workflow was not repeated
  across a full API/worker restart under another owner.
- Synthetic/browser evidence is distinct from the user's own AWS test. No
  prohibited mutation was sent.

## Gate

5B is a **candidate** for user review. The full non-service API selector and
separate live AWS comparison remain open; user acceptance is pending. Phase 5
must remain incomplete until its delivered units are accepted.
