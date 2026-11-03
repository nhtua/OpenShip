---
type: review
summary: Approved small-scope restart and selective carry-forward after reviewing 5fe6e03..0a1fc6b.
status: scope-approved-implementation-not-started
date: 2026-11-03
---

# Phase 6B scope reset

## Basis and decision

Reviewed the previous 852-line spec, 821-line plan and the changed-file inventory
for `5fe6e03..0a1fc6b`: **160 files, 32,379 insertions, 81 deletions**. Much of
that line count is JSON evidence, not product code. Inspected the production
dispatch/availability seams, sandbox execution/publication/recovery modules and
the deployment/bootstrap dependency closure; this is not a claim of exhaustive
correctness/security review of every changed line.

The old docs delegated lifecycle in prose but required OpenShip to retain
physical capacity until deletion, find/reap provider orphans, reconcile within
60 seconds and block admission/publication on cleanup. `sandboxes/publication.py`
required confirmed cleanup and released physical capacity before publishing;
`executor.py` could discard a collected result when destruction failed. These
are provider lifecycle responsibilities, not the useful task/result increment.

At `0a1fc6b`, `sandboxes/definition.py` still rejects execution with
`sandbox_gateway_required`, availability reports sandbox unavailable, and the
dispatch table lacks the tool graph version used by new sandbox submission.
The isolated helpers/tests are not a working production request/result path.

The user approved the smaller scope and then clarified:

- Request **1,800 seconds** and leave lifecycle/cleanup/deletion to OpenSandbox.
- Keep a **workflow-scoped reusable sandbox record**, not the old physical
  reservation-until-confirmed-deletion admission gate.
- Reuse compatible live sandboxes. Requirements changes or definite expiry/
  absence permit on-demand replacement; earlier completed steps use saved
  outputs. This supersedes the initial always-fresh-on-resume proposal.
- Validate/import text and non-executable data; expected returned code is inert
  text judged against workflow expectations. Security scanning is deferred.
- Offer A: rerun the interrupted step, B: rerun from initial inputs. Recovery
  never silently dispatches sandbox work. Preserve current `AGENTS.md` edits.
- Defer the dedicated qualification platform and broader nonessential gates.

## Old plan disposition

| Old task | Revised scope |
| --- | --- |
| 1 — provider tuple | Carry release pins and a small compatible client; no rebuild/scan/certification project. |
| 2 — deployment | Carry rootful Docker socket/gVisor configuration and dependency bootstrap; no new provider lifecycle project. |
| 3 — logical files | Defer editor/mutable heads/history/pruning; use existing artifacts. |
| 4 — contracts/attempt machinery | Small request/result contracts and one workflow handle; existing operation ledger stays authoritative. |
| 5 — source writer | Keep bounded source/input artifact preparation; no mutable-file prerequisite. |
| 6 — inspection/admission | Existing policy/run commands and exact selected refs; no custom bundle-inspection UI or profile-consensus gate. |
| 7 — execution | Narrow provider adapter and existing run supervision; no physical lifecycle recorder. |
| 8 — publication | Bounded validation, returned-code expectation judge and retained outputs; no deletion gate/mutable heads. |
| 9 — orphan recovery | Remove; use provider TTL and explicit A/B application resume. |
| 10 — workspace UI | Existing run/results plus small resume controls; defer editor/history/inspector. |
| 11 — qualification platform | Defer custom launcher, dedicated privileged runner, new CI workflow and certification reports. |
| 12 — e2e/load | One real parser/reuse/resume journey; defer fixed 20-sequential/10-concurrent measurements. |
| 13 — production operations | One development setup/run guide; defer expanded deployment/rotation/restore matrices. |

Existing application authorization, quotas, fencing, cancellation, escaped
previews and applicable tests are retained, not replaced by an LLM judgment.
Earlier phase/evidence documents remain historical; this reset does not turn
their pending work into new 6B blockers or clear their recorded findings.

## Carry-forward boundary

The fresh `task/phase6b-sandbox-client` worktree starts at fetched `origin/main`
(`5fe6e03`). The old branch/worktree remains at `0a1fc6b`; its user-edited
`AGENTS.md` is copied intact. No source worktree changes are discarded.

Carry paths explicitly, not whole commits: `c2d65cb` alone mixes bootstrap and
application work across 108 files.

- `scripts/bootstrap/**` plus `test_bootstrap.py` and
  `test_docker_runsc_bootstrap.py`. Preserve the full role dependency closure:
  the rootful role uses `docker_engine/files/verify_runsc.py`. Optional rootless
  profiles remain available but do not gate the first Docker execution target.
- `docker-compose.sandbox.yml`, optional rootless overlay, provider TOML,
  Python Dockerfile, authenticated Nginx configuration, focused `--sandbox`
  stack wiring and packaging tests. Raise the example's maximum lifetime from
  120 to 1,800 seconds; start both manager and gateway.
- Small image pin reference without old completion/qualification flags or
  broken links to evidence not carried here; new spec/plan/operator guide and
  focused roadmap/architecture alignment.

Do **not** carry old application `sandboxes/**`, logical-file/source-writer
modules, migrations 021–025, old capability/graph/config wiring, sandbox UI,
qualification probes, provider patches or large scan/evidence exports. They
remain accessible at the old revision. Use a fresh baseline database, not the
abandoned branch's newer schema.

## Status

This is a planning/infrastructure handoff, **not Phase 6B implementation**.
Every new-plan checkbox is unchecked. Verification of copied setup files is
reported at handoff and does not mark future application tasks complete.
