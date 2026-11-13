---
type: spec
summary: Outcome-driven agentic tasks with shared internal abilities, persistent Auto/Copilot modes, bounded repair, and locally mapped sandbox profiles.
status: implementation-in-progress
date: 2026-11-08
---

# Phase 7A — Agentic Planning and Continuous Execution

## 1. Intent and review status

A user should describe a desired result, not supply every tool call. OpenShip must understand the request, clarify material uncertainty, present a feasible plan, execute it continuously, diagnose repairable failures and verify the actual result. Downloading an input is not completion of a request to produce a chart.

This spec consolidates the conversationally agreed direction in [ideas](../../ideas.md#outcome-driven-reasoning-planning-and-continuous-execution) and [roadmap Phase 7A](../../roadmap.md#phase-7a--outcome-driven-agentic-planning-and-execution). The former Phase 7 is now **7B**; its saved-workflow increments are **7B.1/7B.2**. Later numbered phases are unchanged.

**Execution and revision status:** The user selected subagent-driven execution of the [implementation plan](../plans/2026-11-08-phase7a-agentic-planning-and-execution.md). Tasks 1–7 have implementation/review evidence; Task 8 has only a committed partial foundation (`5d2ec7b`). The user subsequently approved the recovery behavior below and requested revision of Tasks 8 onward. This revision is documentation-only and awaits review before implementation resumes. §12 defaults remain the plan's working limits; neither implementation progress nor this revision establishes live qualification or user acceptance.

### Agreed decisions

| Area | Decision |
| --- | --- |
| Execution modes | **Copilot** presents a plan and waits for confirmation; **Auto** presents a plan and proceeds without a separate confirmation turn. Copilot is the default. |
| Preference | Persistent account-level server-side selection, changed in Chat UI and restored across reloads/sign-ins/browsers. Capture the mode for each new task; preference changes do not alter active or waiting tasks. |
| Confirmation | Exact task/plan-revision binding, durable waiting, authorized response and no accidental approval by selecting Auto. One confirmation releases continuous execution, not a confirmation after every step. |
| Repair | Bounded implementation-level self-repair after a known failure, informed by inputs, outputs and error evidence. No automatic replay of uncertain/interrupted execution. |
| Crash recovery | If an approval was not recorded, ask again for the same current plan without restarting completed work. For uncertain execution, diagnose first and let the user choose abort, repeat the affected step, or continue to the next eligible step, in both Auto and Copilot. |
| Internal abilities | Runtime-controlled reasoning, planning, tracking and switching with shared typed contracts, not four optional model-selected tools or separate nested agents. |
| Sandbox selection | One execution tool with a validated `profile_id` selector and purpose metadata for configured, enabled, ready profiles. |
| Image mapping | Registry-free local builds. The existing build command offers writing a local YAML mapping interactively; `--overwrite-profiles` authorizes a noninteractive write. |
| Partial builds | Update successfully built/validated profiles while preserving other profile entries and operator customizations. No separate export command or whole-map regeneration. |
| Metadata | Maintain descriptions in tracked `deploy/sandbox/images.json`; generate a matching YAML snapshot consumed by the runtime and model-facing tool description. |
| Mounts | Default host file `deploy/sandbox/sandbox-profiles.yaml`, ignored by Git. Host-side `SANDBOX_PROFILES_FILE` changes the source only; the read-only container target stays `/etc/openship/sandbox-profiles.yaml`. |
| Workflow relationship | Ad hoc planning complements, not replaces, saved workflow definitions. Reuse contracts in 7B and later declared branch/agent nodes; never rewrite a reviewed graph at runtime. |

## 2. Existing foundation and scope

Extend the delivered runtime; do not introduce another scheduler, queue, agent service, permission path, artifact store or operation ledger.

| Existing location | What it establishes / current gap |
| --- | --- |
| `apps/api/src/openship/runs/agent_graph.py` | Model/tool continuation, recorded transcripts and gateway calls already exist. A completed model text response can currently terminate the loop without an explicit task-plan/outcome check. |
| `apps/api/src/openship/runs/service.py`, `budget.py` | Run admission, pinned configurations and shared budgets. Some existing execution paths have a two-tool-call default, which must not become an accidental ceiling for new multi-step tasks. |
| `apps/api/src/openship/runs/models.py` | Durable run/job/fence identities. Current run constraints do not yet include general plan/input waiting states; this phase must migrate them deliberately rather than claim they already exist. |
| `apps/api/src/openship/auth/models.py` | User accounts exist, but there is no agent-mode preference field yet. |
| `apps/api/src/openship/execution/` | Authorization, operation recording, publication and uncertainty boundaries. All task and repair execution stays here. |
| `apps/api/src/openship/sandboxes/` | Python execution, selected artifact staging, validated outputs, compatible sandbox reuse and explicit 6B A/B interruption recovery. Runtime requirements currently use one operator-selected image/profile. |
| `scripts/sandbox_images.py`, `scripts/sandbox_image_lib/` | Deterministic recipe tags, engine/platform-specific build records and offline smoke. No multi-profile runtime YAML writer yet. |
| `deploy/sandbox/images.json` | Four maintained recipes; add curated purpose metadata without replacing lockfiles. |
| `apps/web/src/stores/chat.ts`, `components/chat/`, `views/ChatView.vue` | Existing conversation/run UI, tool presentation and reconnect paths to extend. |

**In scope:** one complete outcome-driven task loop; ordinary chat preserved; clarification and Copilot confirmation; bounded repair and verification; four maintained profile choices when ready; registry-free local configuration and compatibility/recovery tests.

**Out of scope:** saved-workflow compiler/editor (7B), natural-language workflow standardization (9), portable skills/discovery/imports (11), user-authored graph branching/parallelism/subagents (15), new cloud mutations (10), networked or credentialed generated code, runtime dependency installation, new shell API, repository mounts, connected Terraform and OpenShip-owned sandbox lifecycle management. Installed CLI binaries do not enable cloud authority.

The [provider support matrix](../../provider-support.md) remains authoritative. Start with one explicitly qualified task-capable provider/profile; do not advertise this behavior for every adapter or require a provider-specific private-reasoning API.

## 3. User journey and execution flow

### Informational conversation versus a task

Interpret each submission as an informational answer, a clarification need, an actionable task, or an unsupported/blocked request. An ordinary explanation or greeting must not require plan confirmation. Tool-assisted work still follows the selected task mode; do not relabel an execution request as ordinary chat to bypass Copilot.

For a task:

1. Interpret the requested deliverables and material constraints using bounded conversation context and authorized capability/profile descriptions.
2. Ask a focused clarification if a required decision cannot safely be inferred. An initial plan may explicitly include inspecting data and asking about any units/grouping that inspection cannot resolve.
3. Construct and server-validate an ordered plan. Present its steps, approach and intended outputs. Copilot waits for exact confirmation; Auto continues subject to the same policy.
4. Select an eligible step, obtain its exact current inputs and dispatch through the gateway. External inspection/download and sandbox calls are planned execution, not an unapproved planning backdoor.
5. Persist the verified operation outcome and accepted output references before advancing progress. Inspect results against the step's criteria.
6. Continue, diagnose a known failure, request input, propose a revision, or stop through validated switching. A progress message or a model's final-answer text does not itself end the task.
7. Finish only after required deliverables have evidence, or report a truthful blocked/failed/cancelled/budget-limited disposition with completed and remaining work.

### Weather-chart acceptance journey

Use a controlled valid JSON source with known observations, units, date coverage and grouping. From one outcome-level request, download/inspect the data, select London observations, compute averages, generate/run Python using `logs-data`, and retain a valid chart plus computed values. Present source, aggregation, units and artifact links. No follow-up programming instructions are allowed; Copilot requires only the appropriate plan confirmation and any genuinely missing information.

The deliberately non-existent example URL is a **failure fixture**, not a source to contact during document work. A failed fetch must yield an actionable source blocker, not invented observations, a substituted source or a fictional chart.

## 4. Shared internal ability contracts

Names describe runtime responsibilities, not newly registered tool IDs. Model-assisted stages may share one bounded turn; deterministic stages must not incur an inference call merely to advance state.

| Ability | Inputs | Validated output / enforcement |
| --- | --- | --- |
| `agent_reasoning` | User intent, current plan/step, relevant bounded observations/errors, available capabilities and remaining budgets | Structured assessment: relevant evidence refs, missing information, known failure category, proposed repair or next-action recommendation. Concise explanation, not private chain-of-thought. Cannot dispatch or grant authority. |
| `agent_planning` | Assessment, original request constraints, available capability/profile refs and accepted prior outputs | Draft plan or explicit revision: ordered step IDs, dependencies, intent, expected outputs/completion checks and proposed capability/profile choices. Reject invented/unavailable references. |
| `agent_tracking` | Current plan revision and committed operation/validation results | Fenced, version-checked step/attempt/progress updates and evidence links. A model assertion cannot fabricate execution, artifacts or completion. |
| `agent_switch` | Current tracked state, assessment where needed, decision state, policy/ownership and remaining budgets | An eligible next action: execute, assess, repair, revise, wait for input/confirmation/configuration, verify, finish or stop. Validate every transition before publishing/dispatching it. |

Use strict schema-versioned data and bounded fields, following existing Pydantic contracts. Native model structured output is optional: adapter-independent validation remains mandatory, and malformed outputs consume bounded generation attempts rather than bypass validation.

Do not create a second operation identity or let abilities recursively start independent agents. Every model request, tool call, correction and validation consumes the task's existing root budgets. Preserve the pinned provider and private continuation behavior supported by its adapter; do not expose private continuation in general events.

### Complementary workflow use

- **7A:** choose among validated actions within the current ad hoc plan.
- **7B:** keep the saved sequential definition fixed; reuse tracking, capability, evidence and validation contracts rather than reinterpret prose as an executable graph.
- **15:** explicit conditional/agent nodes may consume typed assessments and propose only declared successors. The runtime validates the choice and authority. Ordinary deterministic conditions need no model call. No ability can invent an edge, change a reviewed node or broaden permissions.

## 5. Plan, progress and confirmation state

The plan lives in durable conversation/run context, not a required workspace file, Git commit or saved reusable workflow revision. Store only the public intent, actionable plan, validated assessments and evidence needed for progress/recovery; do not require raw private reasoning.

The logical contracts include:

| Contract | Required information |
| --- | --- |
| Task context | Root run/project/conversation/initiator, captured mode, graph/schema version, pinned provider/capabilities/profile snapshot and cumulative budgets. |
| Plan revision | Identity and monotonic revision, original request constraints, ordered stable logical step IDs, dependencies, expected outputs, completion criteria and review digest. |
| Step/attempt | Logical step identity distinct from attempts, status, input/source/parameter digests, operation identity, accepted outputs, bounded failure/repair evidence and budget consumption. |
| Decision request | Exact run/plan revision and digest, request identity, decision kind, eligible actor, current state and expiry. Clarification also identifies the missing information and any typed answer constraints. |

The existing application transaction/event/outbox remains authoritative for visible progression and decisions; graph checkpoints carry related runtime state, not an independent writable source of truth. Serialize updates with current ownership/fences and revision checks. Reconcile checkpoint/projection mismatches from committed identities/results before progressing.

Phase 7A does not require a conversation-wide graph whose topology grows with every turn. Use a fixed versioned LangGraph and durable plan-local progress: completed and skipped steps, attempts, exact output refs, pending decisions and remaining budgets. Tracking is a persisted runtime responsibility, not model memory. A dynamically expanding free-form conversation graph is a separate future proposal; it cannot rewrite saved workflows' reviewed topology.

Add the required waiting states and decision records through explicit migrations. Retain existing terminal meanings such as `completed`; do not rename historical statuses or mark a waiting task complete to release its worker. Waiting persists state, releases ownership/execution capacity and broker delivery after durable handoff, and resumes through an authenticated idempotent command on the existing queue/outbox path.

Confirmation/rejection/clarification must be bound to the current request and actor. An unambiguous conversational reply and a browser button use the same validated command. A bare ambiguous “yes”, another user's response, an expired request, stale revision or changed digest cannot release work. Consume a request atomically; repeated identical commands return its recorded result, while conflicting decisions cannot consume it again. Rejection closes the request and admits no planned tool execution; cancellation closes pending requests and prevents new dispatch.

If a crash occurs before the user's choice commits, leave the task waiting and present the same current request for approval/continuation again. Do not infer consent from chat text or a lost HTTP response. If the decision committed, recover its recorded result and existing outbox dispatch instead of requesting approval again or enqueuing twice. One local database transaction records the decision, task/job transition, public event and outbox intent; no distributed exactly-once protocol or new queue is required.

Approval is a separate stage from side effects. On graph resume, code before an interrupt may re-enter; never put an unrecorded model/tool effect before a wait and assume it executes once. Resume checks current policy, ownership, exact request and pinned compatibility without regenerating the reviewed plan. A valid confirmation never authorizes unavailable tools or a cloud mutation.

## 6. Mode preference and Chat UI

Persist `auto|copilot` for the authenticated user on the server, with Copilot as the migration/default value. Users read/change only their own preference. Do not use browser storage as the authoritative setting or share a selection between accounts.

The Chat UI shows the current selection, save/pending/error behavior and that changes affect **new tasks**. A failed preference save must not appear successful or silently start work in Auto. Admission captures the selected mode together with submission identity; retries of that submission preserve it. Specify preference revision/conflict handling in the implementation plan so a concurrent browser change cannot silently alter the mode shown for a submission.

Active runs, tasks waiting for confirmation and in-place recovery of those tasks retain their captured mode. Selecting Auto is never a confirmation command. Switching to Copilot does not retroactively pause an Auto task at an arbitrary point. Starting a deliberately new task captures the new preference. In the new task graph, 6B recovery A remains within the task; explicit recovery B creates a new root, captures the then-selected mode and follows its normal plan gate. A B restart is not implicit confirmation of a newly generated plan. Legacy graph versions retain their recorded recovery behavior.

In v5, recovery A is not blanket permission to replay an uncertain step: require the diagnosis and explicit choice in §7. B remains a separate deliberate restart, not one of the three same-task uncertainty decisions. The three-choice recovery gate applies even in Auto mode.

Render the plan and evidence-linked progress within the existing conversation/run presentation. Distinguish pending, running, repaired, completed and blocked steps; show revised plans and pending decisions clearly. No large standalone workflow editor is required. Reconnect reconstructs the same durable plan/decision/progress; title generation must not pollute tool inputs or duplicate final messages.

## 7. Bounded self-repair and completion

After an observed failed step, tracking records its known disposition and bounded evidence. Reasoning interprets the relevant input shape, stdout/stderr and expected outputs, and proposes a correction. Switching validates scope, budgets and dispatch eligibility before a new attempt. All corrected code/outputs pass normal gateway and output checks; an existing exact-code approval, if applicable, cannot cover changed bytes.

**Permitted repair:** an implementation-level correction preserving the step's intended result, selected data, execution authority and eligible profile. For example, correct a JSON field lookup or output-file path after a known failed credential-free Python execution.

**Material revision:** change step ordering/approach, execution profile or deliverables beyond the current reviewed plan. Make the new revision visible. Copilot requires renewed confirmation before executing the changed portion. Auto may revise only within the original request and authority; new data sources, expanded scope or missing material intent still require user input/authorization. Preserve prior completed evidence; do not silently rerun completed steps.

**Not ordinary repair:** timeout after possible dispatch, interrupted sandbox execution, lost ownership, denied authority, policy revocation or exhausted/no-progress budgets. Stop new dispatch. For uncertain execution in v5, follow the diagnosis and user-decision procedure below; preserve legacy graph versions' existing 6B A/B behavior. Denied authority and exhausted budgets remain blockers, not conditions a recovery choice can waive. Broker replay and worker restart do not authorize a new execution.

### User-controlled recovery from uncertain execution

1. Preserve the interrupted attempt, prior committed outputs, plan and cumulative budgets. Do not silently repeat, skip or mark the affected step complete.
2. Inspect already retained operation records, transcripts, logs, checkpoint state and artifacts within the task's scope. Produce a bounded public diagnosis: what was attempted, what is known, what is uncertain, evidence refs, risks and a recommendation. Reasoning uses the same root inference budget and cannot dispatch the affected command as a debugging shortcut. If inference is unavailable or exhausted, show the deterministic evidence and that limitation; never invent a diagnosis or auto-select recovery.
3. Present **Abort**, **Repeat the step**, and **Continue to the next step** to the authorized user, in both modes. Bind the choice to the exact task, plan revision, affected step/attempt/operation and diagnosis/evidence digest. Persist the request and release execution capacity while waiting. Recheck eligibility when the choice is consumed; an older diagnosis cannot authorize changed work.
4. **Abort** cancels remaining task work and retains partial results; it does not claim an already-dispatched effect was undone. **Repeat** authorizes one distinct attempt of the affected step, preserving the original attempt and all consumption. It cannot silently rerun earlier completed steps or change source/inputs/profile under the same choice. **Continue** records the step as skipped/unresolved and advances only if the next step's required inputs are available and validated. A skip is never verified success; missing dependencies require input or a visible plan revision, and missing required deliverables preclude full completion.
5. Repeat is unavailable while the prior command may still run. Dependent or conflicting next-step execution is also blocked until safe quiescence. Use existing provider evidence/lifetime boundaries without extending, deleting or renewing sandboxes. Show why an option is unavailable rather than silently choosing another. Every new operation remains subject to policy, pins, the per-step attempt ceiling and root limits.

Transport redelivery of the same logical operation retains its recorded identity. A deliberate corrected execution has its own admitted attempt/operation bound to the same logical plan step and changed source/input digests; it must not overwrite an earlier result or disguise uncertainty as a fresh attempt. Persist all cumulative budget use across clarification, confirmation and explicit recovery. A new-root 6B B run is explicitly new work, not a hidden budget reset for an existing run.

Step verification combines backend validation (artifact ownership, exact identities, declared files/media/size and valid execution result) with an evidence-based semantic assessment. Model judgment cannot replace enforcement. A successful process exit is insufficient if a required chart is absent. Final task verification checks every required deliverable and reports missing coverage honestly; retain partial outputs without falsely reporting full completion.

## 8. Profile metadata and runtime bindings

### Maintained catalog → generated snapshot → model context

Extend each `managed_profiles` entry in tracked `deploy/sandbox/images.json` with curated metadata: `label`, `description`, `use_for`, `highlights` and `limitations`. Keep recipe references and exact package versions in existing recipe/lock files. Do not derive guidance from an arbitrary image label or model-generated claim.

The generated local YAML has `schema_version: 1` and a `profiles` map keyed by stable profile IDs. Each entry groups:

- **Operator settings:** `enabled` and a bounded `extensions` namespace for non-executable local annotations.
- **Generated metadata:** the five curated fields above and a metadata digest.
- **Generated runtime binding:** image tag/reference, immutable engine-local image ID, dependency identity, platform, build-input fingerprint and engine identity, plus the matching smoke result/evidence identity.

Generate metadata and image binding together for each updated profile. Pin the validated snapshot/digest for admitted work. Metadata-only changes may refresh a snapshot while reusing an unchanged validated image; they do not mean the image bytes changed. Updating a catalog cannot silently change an active task's description or image binding.

Initially accept the four maintained new IDs: `logs-data`, `iac-validation`, `standard`, `devops-shell`. Expose accurate purpose and limits: data/charting, local validation/diagrams, general Python/Node.js/math, and offline DevOps utilities respectively. Existing Python entrypoint compatibility remains required; Node.js or CLI packaging does not add another execution API.

The runtime loads the one mounted YAML snapshot; the API image need not mount/read the repository's build catalog. Parse safely with bounded file/field sizes, reject duplicate keys, unsupported schema versions, executable YAML tags, aliases/expansion hazards and malformed/unknown profile IDs. Metadata is descriptive data, not instructions that override harness policy. Never put credentials in this file.

### Agent-visible selection and enforcement

Extend the existing Python tool with `profile_id` for new task configurations. Describe only authorized, enabled, ready profiles in its bounded model-facing description/schema. No extra discovery tool or separate reasoning call is needed for four profiles. The model receives purpose/capability summaries, not host paths, image IDs or engine details.

The gateway validates the requested ID against the task's permitted pinned snapshot and current policy. Resolve image, dependencies and runtime requirements server-side; reject a model-provided image name or unsupported runtime override. A missing/disabled profile is a blocker, not permission to fall back silently.

Host preflight validates local records against the manager's actual serving engine/platform. Runtime containers must not gain an engine socket to repeat that inspection. Readiness evidence is not a guarantee that an image cannot disappear later: dispatch uses a qualified immutable local image reference, and provider rejection becomes an actionable failure. If a provider cannot honor the pinned identity, block rather than silently execute a retagged image. Qualify this through the actual manager, not just a host-side build check.

Reuse compatible live sandboxes only within existing scope and exact runtime requirements. Changing profile/image/dependency identity cannot reuse an incompatible sandbox. Preserve 30-minute provider-owned lifetime, fresh per-execution directories, credential-free offline execution, selected artifact transfer and declared validated outputs. OpenShip does not build images from an agent invocation, renew/delete sandboxes or introduce a lifecycle reconciler.

## 9. Local build and YAML update behavior

Extend the existing command, not a separate export command:

```bash
# Existing build entry points; successful builds will offer the new YAML write.
scripts/openship-stack -e dev sandbox-images build logs-data
scripts/openship-stack -e dev sandbox-images build --all

# Proposed new flag, unavailable until this phase is implemented.
scripts/openship-stack -e dev sandbox-images build logs-data --overwrite-profiles
```

Use existing deterministic tags and validated engine/platform build records. Show the exact destination and profiles to update before interactive consent; declining never changes the file. `--overwrite-profiles` authorizes writing but cannot bypass failed smoke, schema validation or file safety.

Merge only successful requested profile updates. Preserve other entries, existing `enabled` settings and supported operator annotations. Replace generated metadata/runtime fields only for those successful profiles; new entries may be enabled only after validated build/smoke and explicit write consent. A failed requested profile is not published as ready. Partial failures remain visible and the command reports failure even if separately successful entries are written.

Validate an existing file before merging; malformed/unsupported content must not be silently replaced. Serialize competing writes, reread under the write lock, and publish the complete validated mapping atomically. Reject unsafe/symlink destinations. Interrupted writing cannot truncate the previous file. Generated settings contain no secrets and must be readable by the unprivileged API/worker users.

Add `/deploy/sandbox/sandbox-profiles.yaml` to `.gitignore`, with a tracked empty/example file describing the schema rather than actual machine identities. Operator-chosen paths elsewhere inside the repository must also be kept out of commits. Existing engine/platform build records remain under ignored local state.

The host source resolver must be shared by build and stack configuration: process environment overrides root `.env`, then the default repository-relative path. Resolve relative paths from the repository/Compose project root, never the caller's arbitrary working directory; do not source/eval `.env`. `SANDBOX_PROFILES_FILE` applies to the host destination as well as the mount source so a custom mapping does not require manual copying. This resolver detail is a proposed default in §12.

## 10. Compose mounts and configuration activation

The default API/worker mapping in `docker-compose.yml` uses long bind syntax:

```yaml
volumes:
  - type: bind
    source: ${SANDBOX_PROFILES_FILE:-./deploy/sandbox/sandbox-profiles.yaml}
    target: /etc/openship/sandbox-profiles.yaml
    read_only: true
    bind:
      create_host_path: false
```

This is a proposed addition, not current Compose behavior. Apply it consistently to services that consume the snapshot without replacing their artifact/secret/event mounts. The application inside the container reads the fixed target; do not forward the host variable as an in-container lookup override. Do not mount the build inventory, repository or engine socket to expose profile metadata.

A missing configured source must never become a directory. With sandbox execution enabled, preflight gives the build/configuration action before starting task execution. Preserve normal sandbox-disabled startup without requiring users to build sandbox images; the concrete empty-catalog fallback proposal is in §12. Explicit invalid host overrides fail visibly rather than select another installation's file.

Do not promise hot reload from an atomically replaced file bind mount. Writing the YAML does not restart services. Document a deliberate API/worker drain/recreation and readiness check to activate a changed snapshot, preserving in-flight pinned identities and pending decisions. Mixed API/worker snapshot revisions block incompatible dispatch; activation cannot reinterpret an admitted run or grant new authority. Test the effective Compose configuration and real mounted bytes, including custom host paths.

## 11. Compatibility, recovery and security

- Introduce a new versioned task graph/execution contract and migrations for preferences, plan/decision/progress state and required waiting statuses. Keep old graph/configuration decoders and recorded terminal/history semantics. An old run either resumes through its compatible implementation or is visibly blocked; never reinterpret it as a newly planned task.
- Preserve legacy single-profile settings and old tool requests for their recorded execution versions. New multi-profile tasks use explicit mappings; no implicit fallback between new YAML and legacy environment images. Do not require old history to contain an agent mode or fabricated profile metadata.
- Migrations/backups retain users, conversations, checkpoints, decisions, artifact refs and plan/operation identities. Restored waiting decisions remain bound to their revision; possibly dispatched code remains interrupted/uncertain, not automatically replayable. Store activation/configuration snapshots needed to explain blocked resumptions without storing secrets.
- Approval/input waits require no live browser, reply inbox or sandbox. Run queues, NATS, semantic events, reconnect cursors, cancellation and ownership continue through delivered contracts. All new state stores include restore/upgrade verification; no parallel chat service or scheduler.
- Treat downloaded files, logs and stderr as untrusted evidence, not instructions. Bound/sanitize model-visible content and UI previews. Cross-project/conversation inputs, outputs, decisions and profile policy remain isolated. Model-requested tracking or completion cannot bypass verified evidence or gateway admission.
- A profile description, plan confirmation, skill or reasoning result never grants credentials, mutation permission, network access or external-publication authority. Keep 6B's exact source/input staging and accepted output validation. Preserve its A/B recovery behavior under worker failure and broker redelivery.

## 12. Proposed technical defaults for written-spec review

These defaults make the draft testable; they are **not previously approved product decisions**. They may be changed during this review without reopening the agreed direction.

| ID | Proposed default |
| --- | --- |
| P1 — Initial task limits | At most 8 plan steps, 16 tool calls, 20 model turns, 24 total generation attempts, 4,000 output tokens/request and 48,000 total output tokens. Provider/operator limits remain ceilings, never increased silently. Planning, diagnosis, repair and verification all count. |
| P2 — Repair/no-progress limits | At most 2 repair attempts after the initial attempt per logical step. Stop after 2 consecutive ineffective repairs with the same normalized failure and no new useful evidence, or earlier if any shared budget/control blocks work. |
| P3 — Waiting and time | Plan/input requests expire after 24 hours. Expiry closes the request and task with a visible reason; later work needs a deliberate new request/task. Track cumulative active execution against the existing configured/provider time ceiling; durable human waiting consumes no execution slot or active-time allowance. Waiting never extends provider credentials or sandbox lifetime. |
| P4 — Noninteractive build | Without a terminal and without `--overwrite-profiles`, finish the requested builds but skip YAML writing with an explicit message. Return build/smoke failures normally. The flag writes without prompting, including initial file creation; it does not delete unrelated entries. |
| P5 — Mapping ownership | Preserve `enabled` and bounded `extensions` when updating an existing profile. Refresh generated metadata/runtime fields from the validated build/catalog; preserve other profiles unchanged. Reject unsupported keys instead of guessing their ownership. |
| P6 — Custom source | Build and stack use the same host resolver: process environment → root `.env` → `deploy/sandbox/sandbox-profiles.yaml`, relative to the repository/Compose project root. Never pass a host override as the container path. |
| P7 — Sandbox-disabled bootstrap | Track `deploy/sandbox/sandbox-profiles.example.yaml` containing a valid empty catalog. Only when sandbox is disabled, no explicit host override exists and the generated default is absent, the supported stack launcher selects that example as the effective mount source for that Compose invocation. It does not write the generated file or change builder destinations. Direct Compose use must supply an existing source. Enabling sandbox execution requires the actual generated/explicit ready mapping. |
| P8 — Initial coverage | Qualify all four maintained profile descriptors/selector paths with offline fixtures and one live `logs-data` task journey. Operators may build/enable a subset; unavailable profiles remain blockers rather than advertised execution choices. Do not qualify arbitrary custom profile IDs in this slice. |

## 13. Validation and acceptance

No acceptance box is complete until evidence is recorded. A unit suite alone does not establish live task completion or user acceptance.

| Lane | Required evidence |
| --- | --- |
| Contract/unit | Strict assessment/plan/action validation; stable step/attempt identities; evidence-backed tracking; no model-text-only completion; ordinary chat; capability/profile selection; malformed output; limits/no progress; known failure repair versus uncertain dispatch. |
| Preference/API/UI | Copilot migration/default; per-user isolation; cross-browser restored selection; save failure/conflict; captured task mode; toggle cannot approve a pending plan; plan/clarification/revision cards; no per-step prompting; retries/titles/cancellation/reconnect remain correct. |
| PostgreSQL/runtime | Atomic/idempotent decisions; stale/repeated/conflicting/wrong-user replies; wait expiry/cancellation; release of lease/delivery/slot; failure between committed decision/outbox/checkpoint boundaries; old-history migration and coordinated restore. |
| Broker/recovery | Crash before decision commit re-prompts the same current request; crash after commit/lost acknowledgement recovers one recorded decision/outbox intent. Completed operations are not replayed. Uncertain execution gets an evidence-backed diagnosis and explicit abort/repeat/continue choice in both modes; repeat has a new attempt identity, skip is not success, missing inputs and a possibly live prior command block unsafe choices. Preserve legacy 6B A/B behavior and saved outputs. |
| Profile/build/config | Four descriptors select the intended contexts; disabled/missing/incompatible profiles fail closed; partial merges/decline/flag/no-terminal behavior; failed smoke; malformed existing YAML; concurrent/interrupted writes; ignored generated file; default/custom host paths and identical fixed container target; missing-source and sandbox-disabled bootstrap. |
| Execution/real stack | Fresh exact input staging; compatible reuse only; immutable selected runtime identity through the real manager; offline credential-free execution; valid chart/data outputs; malformed/oversized outputs and untrusted instructions cannot advance progress or alter policy. |
| Live user journey | On one explicitly qualified provider/profile, weather-chart completion in Copilot and Auto, independent expected averages, real retained image, persisted preference, one known repair and one honest invalid-source blocker. Also demonstrate a second input/task shape, such as uploaded log aggregation, without a weather-specific router. |

Run applicable existing tool-loop, budget, sandbox-resume, image and browser regressions. Add targeted tests for the new contracts and migrations before implementation code (TDD); the implementation plan will name exact new tests and any fixture selectors.

Existing network-free validation entry points, run from their indicated directories:

```bash
# apps/api
uv run ruff check .
uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'

# apps/web
pnpm typecheck
pnpm test:run
pnpm build
```

PostgreSQL, JetStream, synthetic full-stack and live provider/manager lanes additionally require their documented explicit environment selectors. Do not claim those lanes passed from a network-free run, silently skip required acceptance checks, or invoke real providers/clouds merely to draft documents.

## 14. Engineering boundaries and handoff

Follow existing Python/TypeScript module conventions. Keep schemas strict and versioned, adapters provider-specific, gateway checks authoritative and edits scoped to this feature. Existing immutable-contract style, for example from `sandboxes/contracts.py`:

```python
class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
```

**Always:** validate proposals at runtime boundaries, preserve pinned identities, enforce cumulative budgets and current authority, write failure/recovery tests, update the active plan with verified evidence, and keep secrets out of checkpoints/events/artifacts/configuration.

**Review first:** the revised Tasks 8–17 of the [written implementation plan](../plans/2026-11-08-phase7a-agentic-planning-and-execution.md), including recovery choices and the carried-forward P1–P8 limits. Subagent-driven execution is already selected; preserve it rather than asking again. The plan must specify migrations/cutover, safe YAML parsing and any direct parser dependency, activation/rollback eligibility, test fixtures and exact acceptance evidence. An incompatible runtime or broader authority change requires a revised design, not an incidental refactor.

**Never:** implement product code before those reviews, install agent-selected packages, silently replay uncertain work, invent evidence, leak host/credential details into model metadata, bypass gateway checks, mutate saved workflow topology or claim that this document delivers Phase 7A/6C acceptance.

### Sources checked during drafting

- Repository source paths in §2 and the existing [6B design](./2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md)/[6C design](./2026-11-07-phase6c-sandbox-images-design.md). Their earlier qualification/acceptance scopes remain unchanged.
- [LangGraph interrupt/resume semantics](https://docs.langchain.com/oss/python/langgraph/interrupts): resuming can re-enter a node from its beginning; keep approval and effects separate and preserve operation identity.
- [Compose volume reference](https://docs.docker.com/reference/compose-file/services/#volumes), [relative-path rules](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/) and [Compose bind-mount tests](https://github.com/docker/compose/blob/main/pkg/compose/create_test.go): source/target are distinct and `create_host_path: false` prevents missing-source directory creation. Actual supported engine/Compose behavior still needs the implementation's configuration/integration checks.
