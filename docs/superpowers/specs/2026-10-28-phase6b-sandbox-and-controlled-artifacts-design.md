---
type: spec
summary: A small OpenSandbox client integration with workflow-scoped reuse, checked outputs and explicit resume.
status: approved-scope-awaiting-implementation
date: 2026-11-03
---

# Phase 6B: OpenSandbox requests and workflow artifacts

## Decision and deliverable

The user approved this scope reset on 2026-11-03, including a **30-minute
sandbox lifetime**, a **workflow-scoped reusable sandbox record**, and deferral
of the dedicated qualification platform. This replaces the previous broad 6B
specification and its implementation progress, not the accepted Phases 1–6A.

**Deliver:** OpenShip prepares a small Python program and selected input files,
asks OpenSandbox to run it, validates the returned outputs, retains accepted
results and uses them to continue the task. The demonstration is uploaded logs
→ generated parser → `summary.json` and `report.txt` → an evidence-linked answer.

Start again from `main` (`5fe6e03` at the reset). The abandoned implementation
through `0a1fc6b` remains historical reference, not code to finish or evidence
that the new implementation works. See the [scope review](../../reviews/2026-11-03-phase6b-scope-reset.md)
and [replacement plan](../plans/2026-10-28-phase6b-opensandbox-implementation.md).

## Responsibility boundary

| Owner | Responsibility |
| --- | --- |
| OpenShip | Generate/capture source and task inputs; authorize selected artifact refs; request execution; validate/import results; record accepted output refs in workflow state; continue or explicitly resume the task. |
| OpenSandbox | Provision and run sandboxes; isolation, resource enforcement, expiration, cleanup, deletion and provider lifecycle recovery. |
| Operator | Install Docker/Podman and gVisor dependencies, configure the OpenSandbox service and protect its API/engine access. |

OpenShip requests **1,800 seconds of lifetime when creating a sandbox** and
otherwise forgets its lifecycle. It does **not** renew TTLs, destroy sandboxes
after a step, poll for deletion, discover/reap orphans, run a watchdog, or hold
installation capacity until physical deletion. The provider owns those actions.
The client may use the provider's documented create, connect, file and command
APIs; OpenSandbox need not expose an invented single `/execute` endpoint.
Do not let SDK context managers implicitly delete a sandbox after each request.

The worker never receives the Docker socket. Only OpenSandbox infrastructure
mounts the rootful socket at `/var/run/docker.sock`; OpenSandbox selects gVisor
`runsc`. The API/model receives no manager key, and generated programs receive
no OpenShip, broker, database or cloud credentials. There is no host-Python or
ordinary-container fallback.

## First supported slice

- One credential-free Python 3.11 standard-library profile in a configured,
  prebuilt image, through a standalone OpenSandbox service on rootful Docker.
- Request 1 CPU, 512 MiB, a 30-second command timeout and a 1,800-second sandbox
  lifetime. OpenSandbox enforces these settings; OpenShip does not certify them.
- Existing run commands, gateway policy, operation/attempt identities, leases,
  cancellation, JetStream/SSE and artifact storage remain authoritative.
- Execution starts only for an explicit user-requested task/run through the
  existing authorized run/tool path. No new background execution or model
  authority is granted by this spec. A custom editor, inspected-bundle endpoint
  and separate per-file approval UI are not prerequisites.
- Capture source/input artifacts using existing immutable storage. Regenerating
  source creates another artifact, not a mutable-file/version-management system.
- Application I/O bounds: source 256 KiB; up to 8 inputs, 8 MiB each/16 MiB total;
  up to 8 outputs, 4 MiB each/8 MiB total; stdout/stderr 256 KiB combined;
  JSON parameters 16 KiB. Reuse existing artifact quotas and authorized reads.
  These bound OpenShip's memory/storage transfers, not container disk or processes.

Only the working development topology and its client contract need a real-service
demonstration. Podman/rootless bootstrap alternatives may be preserved without
qualifying them as execution targets. AWS CLI is an example of a future different
runtime requirement, not a credentialed CLI deliverable in 6B.

## Prepare → request → validate → continue

1. Resolve selected ready artifacts in the current project/conversation. Capture
   generated Python source and any generated JSON/log input through the existing
   artifact service. Record exact refs/digests; never execute source in OpenShip.
2. Build a small execution request: workflow execution identity, existing logical
   operation identity, runtime requirement key, source/input refs, bounded JSON
   parameters and declared output names/types/expectations. No host paths, caller
   authority fields or service credentials are accepted as task data.
3. Reuse or create the workflow's compatible sandbox as described below. Transfer
   **this request's** source and selected inputs into a new request directory.
   Pass fixed Python argv, not interpolated shell text, then await completion.
4. Collect bounded diagnostics and only declared output files. Successful HTTP
   transport is not successful execution: require explicit completion and zero
   exit status, then the output validation below.
5. Publish accepted output refs and the operation result through existing fenced
   artifact/result publication and events. Save refs in the graph's step state
   before considering the step complete. Required-output failure is not success.
6. The next step consumes saved refs/bounded text, not remote sandbox paths. The
   provider may subsequently delete the sandbox without affecting these results.

No database transaction spans a provider/model HTTP wait. Preserve current
ownership/archive/policy checks, operation deduplication and late-result fencing;
do not build a second queue, scheduler, artifact store or operation ledger.

## Workflow-scoped reuse, not lifecycle management

Persist a small `WorkflowSandbox` record containing the existing root run identity
used as the workflow-execution scope, provider deployment identity, sandbox ID,
runtime requirement key and provider expiration time (or a conservative deadline
derived from creation). This record contains no credentials, input bytes,
workspace snapshot, deletion state or physical-capacity reservation.
The provider deployment identity refers to the configured OpenSandbox installation,
not a model provider; derive runtime requirements server-side.

- Never share it across workflow executions or projects. Conversation identity
  alone is not a workflow scope. Explicit child/resume attempts may reference
  their authorized original root run; a run-from-beginning has a new root.
- On a requested execution, use the existing run claim to serialize selection
  and check the saved provider handle on demand. Reuse a live compatible sandbox.
  Ordinary source, selected-data and parameter changes do **not** require a new
  sandbox; each request supplies its complete selected inputs in a distinct path.
- A different runtime/image/dependency/resource profile changes the requirement
  key. Request a new sandbox and update the reusable pointer. Leave the old one
  to OpenSandbox's original TTL; do not delete it or wait for its deletion.
- If expired or definitively absent before dispatch, create another sandbox only
  as part of the current authorized execution request. If the provider is
  unreachable or command status is ambiguous, surface interruption/unavailability;
  do not create a speculative replacement or repeat a possibly sent command.
- No TTL extension, keepalive job, engine inspection or periodic reconciliation.
  Existing application execution slots cover active requests, not the sandbox's
  30-minute idle life. A completed output is never gated on provider deletion.
- Treat residual sandbox files/process memory as untrusted and non-authoritative.
  Do not reuse old input paths or undeclared files just because they still exist.

## Output validation

Sandbox output is data, never OpenShip instructions or automatic execution.

1. Accept declared UTF-8 plain text and JSON data files for the first slice.
   Check actual received byte counts, names, supported types and JSON parsing.
   Reject binary executables, archives, directories, provider-reported links,
   unexpected/missing files and oversized or malformed content. Never extract
   an archive or trust a filename/MIME label as proof that content is safe.
2. All text is inert: no `eval`, imports, subprocess execution, executable HTML,
   raw-HTML preview or implicit code execution. Use existing escaped previews,
   attachment downloads and generated artifact storage keys, not remote paths.
   Text/JSON may contain code-looking strings without being granted authority.
3. A declared script/source output is a specific exception for **text capture**:
   read it and ask the existing pinned model whether its content matches the
   trusted task/workflow expectation. Keep untrusted returned content separate
   from that expectation. Save a bounded pass/fail reason tied to its exact
   output digest. Unexpected scripts, rejection, review failure/unavailability
   or content too large for the bounded review block the step; do not approve
   an unchecked truncated excerpt or automatically rerun the sandbox.
4. Passing this judge is an expectation check, **not a security scan or safety
   certificate**. Security scanning is deferred. Accepted source remains inert;
   executing it later requires another authorized sandbox request.

Only accepted results advance the workflow. Failed validation can retain bounded
failure diagnostics under existing storage policy, not a successful report.

## Resume is an explicit user decision

Apply these semantics to the fixed analysis journey in the existing graph/runtime;
do not implement Phase 7's saved-workflow compiler/editor or Phase 8 approvals.
Persist initial input refs, accepted step outputs and current/interrupted step
using ordinary OpenShip run/checkpoint state. No sandbox-owned input replay cache
or process snapshot is required. Retained user artifacts are not forbidden.

| Choice | Behavior |
| --- | --- |
| A — Rerun the last interrupted step | Use saved outputs from earlier completed steps. If the current step requests sandbox execution, prepare its inputs from that state and reuse the compatible live workflow sandbox, or request a new one if expired/absent or requirements changed. |
| B — Rerun from the beginning | Start a new root run with the original selected initial input refs, rerun the journey and obtain a new workflow sandbox only when an execution step requests one. Keep earlier run results unchanged. |

The user's later reuse decision supersedes the original always-new-sandbox-on-
resume proposal. Neither choice revives a process or trusts residual sandbox
inputs. Missing/deleted prerequisite artifacts produce an actionable error,
not hidden reruns of earlier steps. Changing initial inputs is a new task/run.

Worker/API restart or duplicate broker delivery alone must not create a sandbox
or resend code. Reuse an already committed result; otherwise record interrupted
execution and offer A/B. A repeated resume command returns the same new run,
not another execution. A lost command response is not proof the program stopped;
an ambiguous still-running sandbox is not reusable until its status is resolved
through the normal request path or its original lifetime has ended.

Cancellation stops OpenShip's wait/progression and rejects late results under
existing fences. It does not promise physical process termination or issue a
sandbox delete; command timeout/expiration remain with OpenSandbox.

## Small acceptance set

| Check | Required observation |
| --- | --- |
| Useful task | Generated parser receives only selected current inputs; expected `summary.json`/`report.txt` are saved and used in the next step/answer. |
| Provider ownership | Create requests carry lifetime 1,800 seconds. No normal-use delete, renewal, orphan-listing or cleanup loop exists in OpenShip. |
| Reuse | Two compatible requests in one workflow reuse its ID with distinct input directories; changed requirements or an expired/absent handle create a new one on demand. Different workflows do not share it. |
| Untrusted outputs | Invalid/oversized/undeclared or executable binary outputs fail; expected returned source passes or fails the LLM expectation check before use and is never executed by OpenShip. |
| Durability | Saved accepted outputs remain readable after provider expiration and application restart. Provider unavailability cannot invalidate already committed results. |
| Explicit resume | A skips completed sandbox steps and uses saved outputs; B creates a new root and reruns them. Restart/redelivery is not a sandbox request; repeated resume commands are idempotent. |
| Ordinary failures | Provider outage, nonzero exit, missing completion, unavailable judge and missing prerequisite artifacts produce visible failures; cancellation/lost claims cannot publish late success. |
| Continuity | Existing application tests and affected chat/files/run UI journeys remain working; demonstrate the parser against the real configured OpenSandbox service. |

Use focused unit/contract tests, database tests for the small new persistence
boundary, existing CI and one reproducible real-service journey. Do not claim
mocks certify provider isolation. Record commands/results and limitations, then
seek user acceptance; resetting this spec marks no implementation complete.

## Explicitly deferred or removed

No OpenShip lifecycle controller, physical reservation-until-deletion gate,
orphan recovery, independent deadline enforcement, provider forks or dependency
patching. No image/security-scanning project, runtime isolation/exhaustion
certification, zero-advisory gate or MicroVM prerequisite. Existing upstream
findings are not cleared; historical records remain on the old branch.

Defer mutable logical files/editor/history/pruning, rich previews, generic
bundle/profile certification and worker-profile consensus, 20/10 load studies,
multi-host/production/Kubernetes qualification, additional runtimes/credentialed
CLI and the **dedicated qualification platform** (custom qualification launcher,
privileged Linux runner, new sandbox CI workflow and certification reports).
These are not prerequisites disguised as later tasks in this plan.
