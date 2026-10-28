---
type: spec
summary: Preview-gated first-start cutover for the development Compose stack, with explicit manual production cutover.
status: in_progress
date: 2026-10-27
---

# Dev stack first-start cutover

## Intent

Make the first NATS-backed development startup understandable and recoverable.
`dev start` currently migrates the database and bootstraps JetStream, then
starts services even when `messaging_state.active` is false. The worker waits
for that flag before pulling `OS_RUNS`, so an API can accept and publish a run
that remains pending in JetStream.

The user selected a preview-first dev flow. The ordinary command must show the
cutover preview and require an explicit `--cutover` confirmation before
applying it. Production remains an operator-led migration.

## Goals

- On an inactive dev installation, `dev start` runs a read-only cutover preview,
  prints the preview and stable cutover ID, then exits nonzero with the exact
  `dev start --cutover` retry instruction. It must not start the app services.
- `dev start --cutover` rechecks the preview, stops the selected Compose app
  services, and applies only an unblocked cutover with the same ID. It exits
  nonzero after a successful apply and leaves the database gates closed so the
  operator can see that this invocation performed the cutover.
- A subsequent plain `dev start` resumes an interrupted apply with its
  persisted ID, or completes a successful cutover by starting the relay,
  catching up and verifying publications, opening execution then admission,
  and starting the full dev stack.
- A blocked preview is never force-applied. The command prints the manual
  reconciliation/cutover section of `docs/operations/messaging.md` and exits
  nonzero. This includes cases where a recheck becomes blocked after app
  services stop.
- `prod start` never auto-applies a cutover. If transport is inactive, it
  refuses to start API, worker, relay, or web and points to the production
  procedure in `docs/operations/messaging.md`. After a manual apply, production
  services may start with gates closed for relay catch-up and verification;
  the operator opens execution/admission and ingress explicitly.
- Preserve PostgreSQL, JetStream, artifact, and Connector volumes and all
  existing setup. `--cutover` is not a volume reset or stream purge.
- Make `scripts/openship-stack -e dev stop` equivalent to Compose `down` with
  both `docker-compose.yml` and `docker-compose.dev.yml`, regardless of which
  manifest set was used for the preceding start. Remove containers/network but
  preserve named volumes; local and production stop behavior stays unchanged.

## Non-goals

- Automatically overriding run classification blockers or deciding how to
  discard/reconcile uncertain runs.
- Changing production cutover policy, public API behavior, NATS topology, or
  worker delivery semantics.
- Resetting dev PostgreSQL data or JetStream state to force a clean start.

## Approaches

1. **Preview-gated dev activation (selected):** Show a read-only preview first;
   require `--cutover` to stop Compose writers and apply only when the preview
   is clear. The next ordinary start catches up, verifies, opens dev gates, and
   starts services. This handles the user's pending dev run while preserving
   the cutover's blocker checks.
2. **Fresh-database-only activation:** Auto-apply only when there are no
   application rows. This offers a narrower automatic path, but does not handle
   the queued run already present in the user's first dev database.

The user approved option 1: dev may apply an otherwise clear cutover even when
the database contains queued work. `--cutover` confirms apply but never bypasses
`status=blocked`; the existing cutover engine refuses blocked reports, and
those require the manual reconciliation procedure.

## Behavior and decisions

- Apply automation is limited to `dev`. Supplying `--cutover` to `local` or
  `prod` is a usage error. The low-level startup CLI apply also requires an
  explicit `--dev-confirmed` acknowledgement so production's manual UUID,
  preview and apply process remains distinct.
- The cutover ID must be stable across preview, confirmation, interruption,
  and resume. Prefer the persisted in-progress ID when one exists; otherwise
  derive/store one per installation so the second invocation reuses the exact
  preview identity. The apply operation receives and checks the preview ID so
  a concurrent cutover cannot substitute a different in-progress ID. A
  rolled-back installation is not auto-forward-cutover; report that a new ID
  and manual procedure are required.
- The `--cutover` invocation stops API, worker, relay, and web from the selected
  Compose file set before the final preview/apply. Processes outside this
  Compose project that can write to the same database must be stopped by the
  developer separately. PostgreSQL and NATS remain
  running. If the final preview blocks before apply begins, restore the app
  services. If apply has begun and is incomplete, leave app services stopped,
  preserve state, print the ID, and direct the operator to resume/reconcile.
- After successful apply, ordinary dev startup starts relay first while both
  gates remain closed. It waits for `cutover verify` to pass before opening
  execution and then admission. It then starts API, worker, relay, and web.
  On catch-up, verification, or gate-opening failure, the wrapper keeps gates
  closed or attempts to close both again; if closure cannot be confirmed, it
  reports that state as uncertain and prints the same cutover ID for manual
  recovery.
- The dev command's nonzero result after preview or successful apply is
  intentional and must distinguish `confirmation_required` from
  `cutover_applied_start_again` in its message.
- Dev `stop` always selects both dev Compose files and runs `down` without
  volume removal. A later ordinary `dev start` recreates the stack from the
  same project and named volumes.
- Production operations documentation will give the concrete order: create a
  unique ID; block ingress and stop old writers; run preview; reconcile any
  blockers; apply with `--operator` and `--writers-stopped`; run the new stack
  with gates closed; verify relay catch-up; open execution then admission; and
  restore ingress. Production `start` refuses an inactive transport rather
  than applying automatically.

## Acceptance criteria

- First `dev start` on an inactive database displays a read-only preview and
  exits nonzero without starting app services.
- `dev start --cutover` uses the preview ID, refuses a blocked report, and
  applies a clear report only after stopping Compose app services; successful
  apply exits nonzero with instructions to run plain `dev start`.
- Plain `dev start` after apply resumes/finishes safely, verifies publication
  catch-up, opens execution before admission, then starts the stack.
- A previously active dev installation starts normally without another
  cutover. A rolled-back installation is never silently forwarded.
- `prod start` does not apply a cutover and refuses to start app services while
  transport is inactive. The production runbook describes manual preview,
  apply, verify, gate-opening, and ingress steps.
- No volume or retained broker data is deleted by these paths.
- `dev stop` removes the stack's containers and network with both dev manifests
  selected, and leaves PostgreSQL, NATS, artifact, and NUI named volumes intact.

## Open implementation calls

- Define the stable per-installation ID derivation/storage and the CLI status
  interface used by the shell wrapper.
- Define bounded relay catch-up polling and its timeout/error text.
- Add the new flow to the existing in-progress Phase 6A plan without replacing
  its unchecked qualification tasks.
