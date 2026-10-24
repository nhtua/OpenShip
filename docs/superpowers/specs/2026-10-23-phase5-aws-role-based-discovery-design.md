---
type: spec
summary: Replace manual AWS resource bindings with discovery and reads through a project's configured basic role, preserving historical evidence.
date: 2026-10-23
status: proposed
---

# Phase 5 AWS Discovery Through the Basic Role

## 1. Intent and decision boundary

The user has requested removal of resource binding from Phase 5. One bound S3
bucket was a tooling demonstration, not the intended access model. OpenShip
should discover and inspect resources through an AWS connector's basic role;
AWS evaluates whether each request is permitted. A resource need not be
registered in OpenShip before the agent can inspect it.

The user confirmed that Phase 5 uses the basic role only. Maintenance roles,
privileged changes and their approval lifecycle remain in Phase 10A/10B.
The product direction above is requested; the technical design below is
proposed for review. Existing 5A.1/5A.2 acceptance remains historical evidence.
The delivered binding-based 5B implementation remains an unaccepted candidate.

This document supersedes binding requirements, local bound-bucket inventory,
mandatory selected-resource admission and the prohibition on model-selected
read operations in the earlier Phase 5 design **for new execution once this
replacement is implemented and accepted**. It does not claim implementation.

## 2. Approach

Use connector-scoped AWS access in the existing gateway, with an SDK-backed
catalog of read operations and a shared bounded reader. The model selects the
service, operation, region and target parameters; the runtime validates the
operation and uses only the connector's assumed basic-role session.

Alternatives considered:

| Approach | Consequence | Decision |
| --- | --- | --- |
| Automatically create a binding for every discovered resource | Retains a resource registry and target admission machinery the user explicitly wants removed | Reject |
| Remove bindings but add one handwritten tool for every AWS operation | Fixes target setup but recreates the developer scalability problem discussed earlier | Reject as the general path; retain useful report helpers |
| Connector-scoped discovery and shared read execution | Supports resource choices at execution time and reuses authentication across services | Selected direction; detailed implementation proposed here |

S3 remains the acceptance example. The reader must also prove EC2 and RDS
reads through the same path without service-specific handlers. This does not
require qualifying every AWS operation, region, data protocol or endpoint.
Unsupported operation/protocol and AWS permission denial are different states.

## 3. User experience

- Configure the AWS connector's source, expected account, default region and
  basic-role ARN once. Check status verifies the assumed role identity.
- Remove the environment and bucket-binding forms, bound-bucket verification
  mode and the composer's mandatory S3 binding selector.
- In automatic mode, one eligible project AWS connector is chosen
  automatically. With several eligible connectors, use the user's explicit
  connector selection; otherwise request connector choice before cloud work.
  The agent does not need another user selection for each resource.
- Expose an **AWS** connector selector when useful. Keep the connector
  selection separate from search-provider selection and project search default.
- The user can ask, "Find my S3 buckets and report their size and versioning,"
  or supply a bucket name directly. Listing permission is not a prerequisite
  for reading a supplied target. A name/ARN is an input, not an OpenShip grant.
- Preserve the manual **S3 bucket storage** and **S3 bucket metadata** helpers,
  using connector plus bucket name and optional region. Their buttons submit
  direct tool commands as today; no model call is required.
- Keep results and evidence in conversation operations and files. Opening
  evidence or connector settings does not query AWS automatically.

## 4. Access and execution contract

### 4.1 What remains authoritative

Project ownership authorizes use of the connector; installation tool policy
authorizes use of AWS tooling; the connector pins the identity and basic role;
AWS's effective policy evaluation decides access to each external resource.
There is no additional OpenShip per-resource whitelist or selection grant.

Do not read or simulate IAM policies as a prerequisite. Effective access can
depend on identity/resource policies, session policies, permission boundaries,
organization controls and request conditions. Attempt the requested supported
read and report its actual result. Discovery results are not a complete list
of everything the role can access. A failed list call is not evidence that a
named resource is unreadable, and an absent list entry is not proof of absence.
See [AWS policy evaluation](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic_policy-eval-denyallow.html).

Never fall back to bootstrap credentials, another role or another connector
after denial. Source keys remain exclusively inside role acquisition. The
model cannot supply a role ARN, credentials, endpoint URL or executable code.
The basic-role session is used for supported reads only, even if an
administrator accidentally gave that role write permissions.

### 4.2 Catalog and general reader

Add two maintained capabilities:

- `aws_read_catalog`: local, paginated discovery of installed AWS services,
  supported read operations and their validated parameter schemas. It reports
  runtime support, not the role's permissions, and makes no AWS requests.
- `aws_read`: execute one supported native SDK operation through an eligible
  pinned AWS connector. Inputs: `connector_id`, `service`, SDK `operation`,
  optional `region`, JSON `parameters`, optional continuation `cursor`, and
  `page_size` (default 20, maximum 50). No binding/environment ID is accepted.

Build the operation catalog from a checked-in, versioned snapshot of AWS's
service reference and the installed Botocore service models. AWS publishes
SDK-operation-to-IAM-action mappings and action properties for reads/lists,
writes, tagging and permission management. The mappings are incomplete for
some services; missing information must be an explicit unsupported state.
See [AWS programmatic service reference](https://docs.aws.amazon.com/service-authorization/latest/reference/service-reference.html).

An operation is eligible only when its SDK mapping and all mapped actions are
known, all annotation booleans are present, and none is write, permission
management or tagging-only. Unknown/mixed mappings are refused. A small,
explicitly reviewed exception may accept an AWS-documented alternate action
namespace only when the exact Boto3 operation is linked by the official
reference and every linked action has complete read-only annotations. The
initial exception is S3 `list_objects_v2` with
`s3-object-lambda:ListBucket`; AWS evaluates the requested target. Never infer
read safety from a `Get`/`List`/`Describe` prefix, HTTP verb, model assertion or
the role's name. Reviewed metadata overrides may fill specific gaps with
documented evidence; no per-operation handler is required.

Catalog generation also records protocol/endpoint compatibility and response
handling. Support bounded structured protocols implemented by the pinned
Botocore serializer and parser, including JSON, XML and Smithy RPC v2 CBOR used
by S3, CloudWatch, STS, EC2 and RDS. Credential/token vending, application-secret
extraction, secondary role assumption, event streams and arbitrary streaming
object/body downloads are
explicitly unsupported in this increment. These are operation/protocol
limitations, never fabricated permission denials. Stored SDK metadata can
describe unsupported reads and why they cannot run. Do not promise every
permission of every AWS role is executable in the first implementation.

For this increment, application-secret extraction includes Secrets Manager
`GetSecretValue`/`BatchGetSecretValue`, SSM `GetParameter`/`GetParameters`/
`GetParametersByPath`, and comparable operations dedicated to returning
secret values. Descriptor generation uses SDK sensitive-output metadata and
known credential field names to mark individual response fields. The reader
must drop those fields before compact results, evidence or artifacts are
created; operations that return secrets as their primary result remain
unsupported. Do not depend on guessing secrets from response text or expose a
secret value and then try to redact it from the transcript. This is a proposed
scope limit for review, not a claim that the role lacks those permissions.

The snapshot is updated by a developer maintenance command, outside request
handling. It records official operation/action metadata, schema-conversion and
SDK versions, per-operation input/output schema digests, and a catalog digest.
Runtime reconstructs schemas from the exact installed Botocore models and
verifies their digests; it does not download metadata. Its digest, schema
conversion version and SDK version are part of the capability identity.
Startup/SDK upgrades cannot silently add operations; API and worker must agree
on the same catalog identity. A metadata update does not require another tool
ID, authentication adapter or environment entry for each added operation.
This is installed AWS API discovery, not Phase 11 plugin importing or Phase 6
generated-code execution.

Use manifest schema v3 for the two catalog-backed capabilities, with an
explicit `aws_read_catalog_digest`. For these capabilities the complete
allowed `(service, operation)` set comes from that exact verified catalog;
an empty legacy `operations` tuple must never be interpreted as unrestricted
SDK access. Other tools retain their existing manifest schemas and profiles.
Authorize the service/method pair, not a bare method name that another
service could also expose.

Use Botocore's input shapes to validate nested required fields, types, enums,
lengths and supported bounds. Keep input schemas within 16 KiB and trusted
output schemas within 64 KiB; the latter is local SDK metadata, while the
compact model result remains limited to 16 KiB. Reject parameters that attempt
alternate endpoints, public/unsigned access, role assumption or credential
injection.
Where AWS accepts cross-account target parameters, let AWS evaluate them;
retain expected-role-account verification without forcing every target to
have that owner. S3 `ExpectedBucketOwner` is used only when explicitly known,
not guessed from the connector's account.

### 4.3 Regions, endpoints, pagination and limits

Resolve service endpoints using installed Botocore endpoint rules for the
commercial AWS partition. Respect service-global endpoints and S3 bucket
location. The connector region is a default, not a per-resource access fence.
Use other commercial regions when the task or discovered location requires
them. Keep endpoint origin/path/query validation and public-address network
pinning. Never allow arbitrary URLs, blanket `*.amazonaws.com` matching,
private endpoints, local metadata services or uncontrolled redirects.

Each external tool operation retains: 30 seconds, at most 8 physical requests
including session acquisition/identity checks, 2 MiB per HTTP response,
64 KiB evidence and 16 KiB compact model output. One general reader call
fetches one provider page; no automatic account/region-wide crawl. Use
Botocore paginator metadata for provider page limits and tokens. Continuation
cursors bind to project, connector revision, catalog identity, service,
operation, region and parameter digest. Reject a cursor in a different scope.
Cursors expire after 30 minutes. If any complete provider page exceeds 64 KiB
of normalized evidence, fail with `aws_output_limit_exceeded`, publish no
observation for that page, and do not advance or return the provider's next
token. A later explicit call may retry the same cursor with a smaller page
size. Do not retain a prefix while skipping the rest of the page. Operations
without a compatible bounded page-size control fail explicitly on excessive
record/output bounds. For successful pages, retain complete bounded evidence
and explicit continuation. Compact model output may preview that evidence
with a `preview_truncated` flag; preview truncation never changes pagination.

New AWS automatic runs use configuration schema v4 and chat graph v4; new
manual AWS commands use tool graph v3 with the same configuration schema.
Automatic runs have 12 tool calls, 13 model turns, at most 15 total generation
attempts, 1,000 output tokens per model request, 15,000 reserved output tokens
total, and a 180-second loop deadline capped by the existing run/model deadline.
These are shared across AWS, search and local tools in that run. Every
catalog call/page counts; provider requests still have their independent
physical request budget. Keep lower operator/profile token, cost and attempt
caps authoritative. The existing model attempt default is 3; a qualified
profile must explicitly allow at least 13 attempts to exercise the complete
12-read demonstration. Document that one-time model-budget setup rather than
silently raising a profile's limit. Reserve a final model generation/output
allowance: remove tools on the final affordable turn so partial findings can
be summarized. If a deadline/provider failure prevents that summary, retain
the successful tool operations and show the terminal budget/error state.
Old runs retain their original budgets and versions.

### 4.4 Permission and other failures

For an actual AWS authorization refusal, use code `aws_access_denied` and
fixed copy: **"The AWS connector role does not have permission to perform
this operation."** The operation already records service, operation and
target parameters; the model can identify the failed read without persisting
raw provider error bodies. Do not assert a specific missing policy or action
unless the catalog proves that mapping.

Keep distinct safe states for missing connector, ambiguous connector,
revoked/expired source, role trust denial, unsupported operation/protocol,
invalid parameters, resource not found, region resolution, throttling,
deadline and output limits. Permission failures become tool results the model
can explain while retaining successful report fields. Unknown metrics,
missing metadata and inaccessible visibility are not zero, private or absent.

## 5. Existing S3 reporting

Refactor storage and metadata helpers to accept connector, bucket name and
optional region directly, reusing the existing SDK runner and retained
evidence publication. They need no binding row or environment and can inspect
a bucket that has never been seen in OpenShip. Retain storage metric time,
freshness, type coverage and metadata's never-enabled/MFA-not-reported states.

The shared reader enables `ListBuckets`, configuration reads and CloudWatch
`GetMetricData` without another bespoke handler. The acceptance report covers
size, CloudWatch `NumberOfObjects`, versioning and public-access-related
configuration when permitted. Object count must carry its metric timestamp
and definition; it is not an exact live count of current files. See
[S3 metric definitions](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html).
Public access
must distinguish policy public status, bucket/account Block Public Access and
ACL evidence. Missing inputs produce **Unknown**; do not infer private from
a failed permission check or a single setting. No anonymous public probe or
full object scan is needed. See [S3 public access controls](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html).
Report synthesis is model work using recorded
reads; do not add an S3 reporting framework.

## 6. Persistence and removal

- Add a forward migration after `phase5b013`; do not rewrite migrations used
  by accepted installations or delete existing data.
- New observations record connector ID/revision, execution account, service,
  operation, region, target metadata, timestamps and evidence. Binding,
  environment and pre-created resource IDs are not prerequisites. Keep a
  stable resource object only when it is useful for an identifiable target;
  creating that identity happens during publication and grants no access.
- Retain legacy environment/binding rows solely for existing history and
  foreign keys. New execution neither reads them for authorization nor
  creates hidden replacement bindings. Binding creation routes return
  authenticated/owner-scoped `410 resource_binding_retired`; historical GET
  and evidence routes remain readable with their existing ownership rules.
- Remove binding setup services and the `aws_s3_list_bound_buckets` active
  definition. Keep only contracts/read helpers required by old history.
- Version new execution and evidence schemas. Decode configurations v1–v3
  and retain old result renderers. Old pending exact references either use
  genuinely retained compatible code or stop with a visible version-change
  error; never silently reinterpret a binding ID as a free target.
- Preserve managed policy overrides and explicit deployment ceilings.
  Existing installations must explicitly enable the new broad `aws_read`
  capability in Global Settings; do not reinterpret an old S3-only allowlist
  as broad AWS permission. One enablement covers the reader, not each SDK
  operation. Retain old policy history without exposing a live binding tool.
- Keep observation/artifact/operation/event publication atomic under existing
  ownership, lease, cancellation, connector and policy fences. Refresh files
  and observations after publication without reloading the page.

## 7. Acceptance examples and boundaries

1. With no environments/bindings, discover live S3 buckets and inspect one.
2. With bucket listing denied but a supplied bucket readable, inspect it and
   explain that discovery was unavailable.
3. Discover EC2 instances and describe RDS databases with the same reader.
4. Read a resource in another commercial region without rebinding anything.
5. Deny a specific read and report the permission gap while retaining other
   successful report fields. Prohibited writes make zero provider requests.
6. Produce a bucket stats report with timestamped size/object metrics,
   versioning, qualified public-access findings and saved evidence.
7. Reopen old binding-based evidence and new discovery evidence after restart;
   archival, revocation and project isolation continue to work.

Synthetic tests prove expected behavior before any separately authorized live
AWS comparisons. No maintenance role, cloud mutation, IAM modification,
credentialed generated code, third-party plugin loader, background resource
scanner or inventory synchronization service is added.

## 8. Implementation relationship

The [binding-removal plan](../plans/2026-10-23-phase5-remove-bindings-and-enable-aws-discovery.md)
replaces remaining binding-based 5B delivery work. Qualification must label
this replacement separately from the accepted 5A.2 demonstration and the
earlier 5B candidate. Phase 5 is incomplete until the revised flow is accepted.
