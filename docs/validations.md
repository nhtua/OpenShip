---
type: story
summary: Community validation of OpenShip concepts against DevOps pain points gathered from Reddit, Hacker News, and developer forums.
status: approved
date: 2026-09-22
---

# OpenShip Ideas — Community Validation

Based on online discussions from Reddit, Hacker News, and DevOps communities, this document validates OpenShip's core ideas against real user feedback and pain points.

---

## Strongly Validated Concepts

### 1. Co-Pilot, Not Replacement ✅

- "AI is more reliable as a decision-support layer rather than a full automation engine"
- "Agentic DevOps has a place, but not as full automation. More like copilots that watch, summarize, and propose, while engineers retain control"
- "AI to automatically manage infra? Hell no! AI to help me out with boilerplate, error fixing? Hell yeah!"

### 2. Document-Driven, Requirements-First ✅

- "Documentation is what typically holds most of this automation back, but it works amazingly well when stuff is documented"
- "Requirements aren't tossed over the wall to Ops"
- Strong consensus that well-defined requirements are critical for automation success

### 3. Human-in-the-Loop at Decision Points ✅

- "The key was being conservative with what we let it automate vs just recommend"
- "As long as you're carefully reviewing and understanding the code, I think it's totally fine"
- Fear of AI "fixing" things that break compliance boundaries

### 4. Chat-First Entry Point ✅

- "Instead of you adapting to the tools, the AI adapts to your request"
- Users are already comfortable with ChatGPT-style interfaces
- But also: "Don't blindly chat along. AI is a tool that needs tuning" → supports document-driven workflow underneath

## Validated by Pain Points

### 5. Workflow Templates as First-Class Objects ✅

- "You have to learn to use Agents effectively as an engineer, there's a learning curve, and new work patterns"
- "Creating deterministic islands - aka bash scripts - as you go so agents accumulate automation"
- Users want proven patterns, not to reinvent the wheel each time

### 6. Git Integration & PR-Based Workflow Sharing ✅

- "SDLC for infra as code follows the same best practices as SDLC for anything else"
- "Enforce pull requests and reviews, basic linters and automated tests"
- Git is already the trust model for infrastructure code

### 7. Security Scanning for Shared Workflows ✅

- "AI agents are shipping faster than anyone can track them. Most engineering orgs now run dozens in production with no shared inventory"
- "The agent only had read permissions into AWS. Don't be a fool, cover your tool."
- Security and trust are major concerns

## Real Use Cases Mentioned

Users consistently mention these practical scenarios:

- Log triage and incident response
- Terraform plan checks
- CI/CD optimization and automation
- Automated incident summaries
- Deploying applications to production
- Managing infrastructure changes safely

These align directly with OpenShip's planned workflow templates (provision/build, investigation/debug, CI/CD creation).

## Key Insight

The recurring theme is: **engineers want AI that understands context, proposes solutions, and executes safely — but they want to remain in control.** OpenShip's design (requirements → design → plan → human approval → execute) maps precisely to this expectation.

The chat-first home page with workflow template selection underneath is also validated — users want both the familiar conversational entry point AND structured, proven workflows they can trust.

## Engineering validation records

This page records community validation of OpenShip concepts; it is not a
delivery or build record. Implementation evidence belongs with the feature
runbooks. For Phase 6C sandbox images, see the
[local image validation record](operations/opensandbox.md#local-image-validation-record-2026-11-07).

### Phase 7A agentic task journeys — qualification boundary (2026-11-10)

What was validated, and where each boundary stays synthetic, for the Phase 7A
continuous-task journeys in
`apps/api/tests/full_stack/test_agentic_tasks.py`. Command lines, counts and
per-journey assertions are recorded with the plan that owns them
([Phase 7A plan, Task 16](superpowers/plans/2026-11-08-phase7a-agentic-planning-and-execution.md)).
This is fixture qualification only: it is not live-provider qualification and
not user acceptance, and the live gates in that plan remain open.

| Boundary | Status in the selected lane |
| --- | --- |
| Model transport | Synthetic. A deterministic stage script answers the v5 protocol turns from `apps/api/tests/fixtures/agentic_tasks/`; provider selection, budgets, transcripts and the model-attempt records around it are the product's own. |
| External source | Synthetic. A canned weather CSV and logs JSON are served to the real `download_file` capture through the real URL policy gate; the illustrative invalid public URL is never contacted. |
| Sandbox transport | **Real.** The OpenSandbox HTTP client runs against the authorized loopback gateway (`127.0.0.1:18080`) with authenticated preflight, creating one sandbox per chart step from the operator's profile snapshot at its pinned immutable image ID (`logs-data` → `sha256:260308ad02e5…`). Nothing in the lane stubs the sandbox transport, and the lane fails when the gateway is not answering. |
| Durable runtime | Real. A disposable PostgreSQL database, the run queue, leases and reconciler, the v5 task graph, the tool gateway, the artifact store, the event/outbox path and the authenticated preference door. |
| Browser | Real. Chromium through the repository's `playwright.full-stack.config.ts` against the vite dev server proxying the same API process. |

Unqualified by these fixtures, and therefore still open: live-manager
immutable-image execution, the separately authorized provider tuple for tasks,
real provider fault injection, and the user's inspection and acceptance of the
chart, progress, known-repair, source-blocker and recovery views.

### Changed-file test selection — measurement (2026-11-10)

What the narrowed commit gate costs, and what the full lane still costs, measured
on this tree. Design and rules:
[the spec](superpowers/specs/2026-11-10-changed-file-test-selection-design.md);
steps and pinned behaviours:
[the plan](superpowers/plans/2026-11-10-changed-file-test-selection.md).

| Measurement | Result |
| --- | --- |
| Import graph build (`apps/api/src` + `apps/api/tests`, stdlib `ast`, no cache) | 1185 modules, 9039 import edges, 0.63 s (re-measured 2026-11-10 after the relative-import fix) |
| `apps/api/src/openship/runs/tasks/projections.py` staged alone | 1 selected module, 1 passed, 4.32 s (6.6 s wall) |
| `apps/api/src/openship/runs/service.py` staged alone | 109 selected modules |
| `apps/api/src/openship/chat/service.py` (no test imports it) | full lane: "maps to no test module" |
| `apps/web/src/stores/taskProgress.ts` staged alone | plan `changed`, 13 suites / 201 tests, 4.26 s (5.9 s wall) |
| `--explain` on both launchers | API: plan, per-file reasons, exact command, nothing run; web: plan `changed`, suites it reaches, exact command, no vitest process started |
| `uvx pre-commit run --all-files` (new config) | 7 hooks passed in 4 s: ruff, the poc suite, API ruff, `vue-tsc` and the web suite ran over the tree. The two test hooks read the index, so with nothing staged they selected nothing (see the caveat in `docs/development.md`). |
| `bash scripts/openship-verify` (full lane) | Python lane `2037 passed` in 152.4 s across 8 shards; `vue-tsc --noEmit` clean; web `400 passed` in 42 files (4.54 s). The gate exits 1 at its secret-scan leg on three findings that already exist at the pre-branch commit `858b7d0` (the dev compose's throwaway `openship` password and two documentation placeholders, all unchanged lines); none is introduced by this branch. |
| Full credential-free lane, in the tool commit's own hook run | 2001 passed, 169 deselected, 853.60 s |

No `postgres`, `full_stack`, `provider_live` or `jetstream` lane ran for these
numbers, and none was needed: the credential-free lane needs no database,
provider key, sandbox endpoint, broker or browser. The lanes that need those
prerequisites are run by the commands named in
[the testing guide](testing/phase4-acceptance.md) — for the API, `uv run pytest -q -m postgres`
with a disposable `TEST_POSTGRES_URL`, `-m full_stack` with the full-stack
launcher, `-m provider_live` with authorized credentials, and `-m jetstream`
with the qualified broker.

### Parallel shards for the full lane — measurement (2026-11-10)

Same tree, same machine, same minute, one run each. Design:
[the sharding spec](superpowers/specs/2026-11-10-launcher-test-sharding-design.md).

| Measurement | Result |
| --- | --- |
| Full credential-free lane, single process | 851.34 s, `2020 passed, 169 deselected` |
| Full credential-free lane, 4 shards | 249.1 s (3.4x the single process) |
| Full credential-free lane, 8 shards | 152.6 s and 152.4 s on two runs (5.6x); per-shard walls 118.7-149.6 s, 254-255 passed each |
| The sharding commit's own hook run (auto, 4 shards then) | `API Unit Tests` passed, `2037` tests, 252.4 s |
| `--explain` on a full-lane plan | prints `lane shards: 8`, the plugin command and one env line per shard; runs nothing |

Every sharded run's summed counts must equal the single-process run's. The
single process reported `2020 passed, 169 deselected` before the 17
launcher/mapper tests this branch adds; the final lane reports `2037 passed`.
A shard that collected nothing exits 5 and is not counted as a failure.

### Agentic task journeys on the dev stack — what the durable doors do (2026-11-11)

Operator QA against the running dev stack (the driver is a scratch script in
`/tmp`, not a committed tool): sign in, submit one outcome-level request in
`copilot` mode, watch the parked plan, confirm it through `POST
/api/runs/{run}/task-decisions`, and read the run back. This is a journey check,
not acceptance: no `postgres`, `full_stack`, `provider_live` or `jetstream` test
lane ran, and no user was asked to accept anything.

| Step | Result |
| --- | --- |
| Plan stage before the prompt fix | four recorded answers each wrote a sentence into `recommended_recovery_action` and left `step_id` null, so every assessment failed and the chat answered "no plan could be validated" for any request |
| Replay of those recorded answers through the product's own validators | `PLAN VALID` against the run's own pinned `execution_configuration` - the plan was always fine; only the assessment's field values were refused |
| After `fb90e74` (the prompt states the contract's rules) | three runs parked a real plan: `find_source/download_data/make_chart/verify_chart`, `download/inspect/chart/confirm`, `download_data/create_chart/confirm_chart`, each at `waiting_plan` with pending kind `plan_confirmation` |
| Confirming one parked plan | `POST /api/runs/{run}/task-decisions` → HTTP 200, execution started, and the plan's steps moved through `queued`/`running`/settled states in the run view |
| The `.txt` download plan after the attempt-recording fix | run `completed`: `download_data:failed`, `create_chart:failed`, `confirm_chart:succeeded` - the refused step is recorded and the switcher keeps routing, instead of the worker dying |
| Before that fix | the same step aborted the run: `TaskStateError: the committed operation has no result digest` at `runs/tasks/state.py`, then `failed`/`internal_error` |

Two real breaks remain open in this area, with their evidence:

- `workspace_read_file` at its ceiling kills a task run. The tool read
  15,759 bytes and the operation committed `result_json` at exactly
  `max_model_result_bytes` (16,384), so the envelope `_result_message` adds on
  top of a payload already allowed at the ceiling made the model-visible
  message too large: `ProviderError("output_limit_exceeded")` raised inside
  `dispatch` (`task_graph.py`), which aborts the run instead of leaving a step
  outcome. The manifest pairs `max_read_bytes` and `max_model_result_bytes` at
  the same 16 KiB (`capabilities/catalog.py:522-524`).
- A confirmed plan that finishes publishes no closing turn. Run
  `e917c027-c321-4443-806f-74b786e6feaa` ended `completed` with only two
  messages in the conversation - the request and the plan presentation - while
  its task record stayed `status: running`, `last_disposition: none`. The chat
  shows a plan and silence, so the durable task's own terminal state is never
  published. `task.status` reaches `completed` only through the verification
  path (`runs/tasks/state.py:995`), which a run with no verified output never
  enters. The plan's own Task 12 evidence already treats durable
  `task_status == "running"` with `verified_steps == ()` as the honest
  pre-verification state, so the missing closing turn is the reportable half of
  this finding, not the status itself.
