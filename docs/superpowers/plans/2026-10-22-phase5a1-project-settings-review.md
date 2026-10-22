---
type: plan
summary: Approved 5A.1 revisions for global Brave/Tavily search, basic Projects, scoped settings, connector cards and setup failures.
status: approved
date: 2026-10-22
---

# Phase 5A.1 Project Settings and Connector Review Plan

> **For agentic workers:** Use `superpowers:executing-plans` for sequential implementation. Read the linked specification and this approved addendum before implementation. Checkboxes track implementation.
>
> **Review boundary:** Approved by the user on 2026-10-22 for implementation of Tasks 1–6. It does not record 5A.1 acceptance or authorize Task 8/AWS.

**Goal:** Make managed search setup usable through real project selection, settings owned by the correct scope, discoverable connector cards and clear failure recovery.

**Architecture:** Configure Brave and Tavily as installation-global search connectors while retaining project-scoped defaults and execution evidence. Extend the existing owner-only Project model, separate project selection from connector state in the browser, introduce Project Settings and Global Settings tabs, and open write-only credential forms only in connector dialogs. Preserve the existing credential broker and historical execution contracts.

**Tech stack:** Existing FastAPI, SQLAlchemy/Alembic, PostgreSQL 15, Vue, Pinia, TypeScript, pytest, Vitest and Playwright; no new product dependencies.

**Spec:** [Approved Phase 5 specification](../specs/2026-10-21-phase5-managed-connectors-and-s3-observations-design.md). This approved addendum adds basic multiple-project lifecycle and replaces the setup-page presentation in section 9. The user's subsequent visibility decision below supersedes section 4.1's Brave/Tavily support for both scopes; Task 1 aligns that contract. Owner-only project authorization and the 5A.1 acceptance gate remain.

## Findings from review investigation

- Defaults already belong to `ProjectConnectorDefault`, keyed by project and service. Two projects can independently choose one global connector without copying its credential. The current view places that setting inside connector configuration and obscures its ownership.
- Project storage and authenticated listing already exist. Registration creates a project, but `uq_projects_owner_user` permits only one project per owner. Multiple projects require a migration, not only a selector change. Conversation creation also currently chooses `.first()` when no project is supplied.
- The project setup POST's 403 path is its browser-origin check, before reading the credential or reserving a request. The global listing 403 is a separate, expected operator-authorization denial.
- `ConnectorCredentialForm.vue` reconciles every rejected submission, including known pre-acceptance failures. This turns an actionable origin rejection into an uncertain-write loop.
- Request-status lookup needs the separate idempotency HMAC key. A missing key produces `credential_store_unavailable`/503. Inspection of local configuration presence and default file availability found Connector provisioning absent; no secret values were printed. Isolated probes reproduced origin rejection as 403 and missing HMAC storage as 503. Process-level overrides and the browser's actual origin still need confirmation when reproducing the running deployment. At the user's request, local encryption/HMAC keyrings and both untracked API `.env` files were provisioned for this database; no generated key value is recorded here or in git.
- The current browser does not normalize the API's FastAPI `detail.error` envelope consistently with its flat `error` fixtures. Fix and test both shapes.
- The synthetic process-stack qualification passed, but did not qualify an unprovisioned installation's browser setup or multiple projects owned by the same user.

## Proposed design

### Search visibility — user decision, 2026-10-22

Brave Search and Tavily Search are **Global** integrations. New connectors for either service are installation-global, configured once by the existing designated operator and reusable by every eligible project. Their supported-service cards and creation dialogs show **Global** visibility; do not offer a Project visibility selector or project-scoped search creation. Global configuration does not grant users access to credentials or other projects' execution data. AWS remains project-scoped in its later phase.

Projects choose their own default global Brave instance and default global Tavily instance in Project Settings. Project selection changes these preferences and conversation/evidence ownership, never a search connector's visibility. The same global instance can be the default in multiple projects without duplicating its secret. Global replacement/revocation retains the existing all-project impact disclosure and server authorization.

Existing project-scoped search connectors require an explicit cutover, not an in-place visibility change or a relabeled badge. Keep their immutable records and admitted references readable, and preserve authorized existing execution/default behavior during cutover. Show existing private defaults as legacy with migration guidance; offer only global instances for new default selections. The operator creates a new global connector through the existing write-only setup flow, project owners explicitly switch defaults, and legacy connectors can be revoked after admitted work drains. Do not automatically read back, copy or publish a project credential. Keep legacy scope visible in migration information outside the supported-service card list so private credentials are never presented as shared globals.

### Projects and settings ownership

Use the existing Project identity and ownership boundary. Support create, list/detail, rename, switch and guarded deletion. Names are trimmed, nonempty and at most 255 characters; project IDs remain the identity. Keep existing projects, conversations and artifacts in place through migration.

Deletion is deliberately limited: reject the last owned project and any project with retained conversations, workspace/artifact data, connectors, defaults, request-ledger, audit or other project-dependent records. Return a clear conflict rather than cascading or moving data. Lock the owner and project consistently so concurrent create/delete/configuration cannot violate these rules. Deleting retained project history is outside this revision.

The sidebar Settings link opens `/projects/:projectId/settings`, with **Project Settings** selected. A second tab opens `/settings/global`. Project Settings contains the project name/lifecycle controls and search defaults. Global Settings shows installation setup readiness, global-management authority and a link to the global connector catalog; it introduces no new global-default semantics or editable deployment-secret settings. Other users see an operator-managed explanation. Keep **Connectors** as a separate sidebar page at `/connectors`, with `/settings/connectors` redirecting there for compatibility. Credential configuration lives in its dialogs, not in either Settings tab.

Keep defaults in a distinct **Search defaults** section of Project Settings, outside connector dialogs. New choices include eligible global search connectors only; legacy project defaults show the cutover guidance above. Each project writes only its own revisioned settings. Keep one independent default per existing service (`brave`, `tavily`); choosing a preferred service for all search is not added here.

The project selector supports creating another project and switching among owned projects. New conversations always submit the selected ID. Opening an existing conversation selects its actual project. Switching does not move conversations or cancel runs. An omitted API `project_id` remains compatible for a sole owned project; with multiple projects return `project_required`/409 rather than choosing an arbitrary project.

### Connector catalog and dialogs

Show Brave Search and Tavily Search as small **Global** cards on the Connectors page before any configuration form. For each configured instance show its name, service, publisher **OpenShip**, local logo or initials fallback, immutable Global visibility and a status dot with text. If no global connector exists for a supported service, show its unconfigured Global card. Allow another global instance through its dialog. The selected project determines eligible usage and defaults; ordinary consumers get read-only global usage details and operator guidance. The operator gets global management controls. Links from Settings enter this catalog; neither a project selection nor a URL parameter may turn Brave/Tavily configuration into Project scope.

Selecting a card opens an accessible dialog containing its relevant create/configuration or credential replacement, disable and revoke controls. Global consumers see usage details only. Do not show forms or secret inputs on the catalog itself. Keep the dialog bound to the original project/scope/connector; close and clear it on project changes or navigation. Retain only non-secret pending request identifiers under their original scope for later reconciliation.

Status semantics are explicit and never depend on card ordering:

| Dot | Label/state | Meaning |
| --- | --- | --- |
| Green | Configured | Stored configuration is eligible according to the API; no live provider validation is implied. |
| Grey | Not configured / Disabled | No instance exists for this service/scope, or the instance is deliberately disabled. |
| Red | Needs attention, with reason | Expired, revoked or unavailable according to known API state. A failed list request is a page error, not evidence that every connector is outdated. |

Do not invent expiry, infer invalid keys without evidence, or perform background provider calls to color a dot. The built-in integration publisher is OpenShip; Brave/Tavily remain the service names. Use bundled assets if suitable assets already exist, otherwise use initials. No remote logo requests are required.

### Setup errors and installation readiness

Distinguish definite rejection from uncertain delivery. A recognized pre-acceptance origin/auth/validation/authorization rejection clears the pending request and presents a fixed actionable error without status polling. Network loss, an unclassified server failure or ambiguous commit outcome retains the original request ID and uses status-only reconciliation; never automatically resend the secret.

Normalize error envelopes in one browser helper. Status lookup must display its actual safe failure reason and recovery action; a 503 must not look like a button that does nothing. Failure to look up an uncertain request must not unlock credential resubmission. A matching complete status or authorized 404/410 resolves the pending request according to the current contract. Classify by explicit server error codes/outcome guarantees, not by assuming every 4xx or every 503 is interchangeable.

Add authenticated `GET /api/connectors/setup-context` returning only `can_manage_global`, `credential_store_ready` and a fixed `unavailable_reason` (`credential_store_unavailable` or null). Check installation identity and encryption/HMAC key availability without exposing key IDs, paths, values or exception details. This snapshot is advisory; writes and dispatch still enforce their own checks. Missing provisioning disables new credential forms with operator guidance while plain chat remains usable. Avoid probing operator-only global listing for every ordinary user.

Keep the explicit browser-origin allowlist. Give origin rejection its own fixed `connector_origin_forbidden`/403 code, document custom frontend ports, and make Vite fail on an occupied configured port instead of silently starting on a disallowed port. Add direct local-development provisioning instructions alongside Compose instructions. Use the existing exclusive key-init command; never initialize or replace encryption keys from an HTTP request.

## Global constraints

- Preserve owner-only authorization; foreign project IDs return 404. Global management remains operator-only.
- Brave/Tavily new configuration is Global only; defaults, runs and evidence remain project-owned. Retain legacy private references through explicit cutover without silently sharing their keys.
- Preserve existing Connector IDs, immutable visibility/revisions, request idempotency, worker admission, revocation and retained evidence.
- No secrets in browser stores, URLs, persistent drafts, request retry payloads, read responses, logs or artifacts.
- No automatic secret resubmission, key generation, credential migration or live-provider health checks.
- Keep Project CRUD limited to the lifecycle above; no teams, membership, conversation moves or cascading history deletion.
- Finish and requalify this revision inside 5A.1 before asking for acceptance. Task 8/AWS remains gated.

## Review focus

1. Same owner, two projects: both can use one global Brave/Tavily instance while A's default never overwrites B's. New project-scoped search creation must be rejected server-side, and legacy private keys must never be silently shared. Owns: Tasks R0, R2, R3 and R5.
2. A project switch during an async load or secret write: late results cannot populate the wrong project, and status checks retain the submitted scope. Owns: Tasks R3 and R4.
3. Origin rejection followed by missing key files: show two actionable, accurately classified failures without an endless uncertain-write loop. Owns: Tasks R1 and R5.
4. Concurrent last-project deletion, new conversations or connector writes: preserve at least one project and all retained references. Owns: Task R2.
5. Multiple instances and mixed statuses: cards preserve per-instance identity, immutable visibility and read-only global usage; colors never imply an unperformed network test. Owns: Tasks R4 and R5.

## Task 1 (R0) — Make Brave and Tavily globally configured integrations

**Files:** Modify `apps/api/src/openship/connectors/{service,routes,cli}.py`, `apps/api/tests/connectors/{test_routes,test_selection,test_operations}.py`, `apps/api/tests/integration/test_connector_commands_postgres.py`, `docs/operations/managed-connectors.md`, the Phase 5 specification and the parent plan's current policy notes. Catalog presentation is delivered in R4.

**Interfaces:** New Brave/Tavily creation and import require `visibility=global` and existing operator authority. Project-scoped search creation/import returns fixed `connector_visibility_unsupported`/422 without persisting a secret. Existing legacy project records remain immutable and available only under their original authorization during explicit cutover. New default choices must be global; existing legacy defaults remain readable and executable until explicitly replaced or revoked.

- [ ] Add failing route/service/CLI tests proving both services can be globally configured by the operator, ordinary project owners cannot create globals, and project-scoped search creation/import is refused without credential persistence. Enforce the policy in the shared service, not only the UI.
- [ ] Add PostgreSQL cutover tests with historical project-scoped fixtures: no automatic scope/credential changes; a newly entered global key creates a separate identity; each owner's explicit default change is independent; already-admitted legacy runs retain their references and revocation protections.
- [ ] Implement the new-configuration visibility policy while retaining legacy credential replacement/revocation needed for safe retirement. Reject assigning a legacy private connector as a new project default. No data migration may widen credential visibility.
- [ ] Update the specification and operations guidance to distinguish global search configuration, project-owned defaults/evidence and legacy cutover. Adjust existing tests that intentionally create new private search connectors to the global contract; retain historical fixtures where compatibility/isolation is the purpose.
- [ ] Run connector route/selection/operations and PostgreSQL command tests; review and commit the verified increment using the scanned message-file workflow.

**Deliverable:** Search connector visibility is Global in policy and API behavior, with an explicit path for existing private configurations.

## Task 2 (R1) — Fix setup rejection, recovery and provisioning feedback

**Files:** Modify `apps/api/src/openship/connectors/routes.py`, `credentials/dependencies.py`, `apps/web/src/components/connectors/ConnectorCredentialForm.vue`, `apps/web/src/stores/connectors.ts`, `apps/web/src/services/api.ts`, `apps/web/src/types/index.ts`, `apps/web/vite.config.js`, `docs/operations/managed-connectors.md`, and `apps/api/.env.example`. Create `apps/web/src/lib/connectorErrors.ts`.

**Interfaces:** `GET /api/connectors/setup-context` returns the fixed safe shape above. `connector_origin_forbidden` is a definite pre-acceptance rejection. `readConnectorError(error: unknown)` returns a recognized safe code/message and submission-outcome classification; it accepts flat and FastAPI error envelopes, and does not echo arbitrary server payloads.

- [x] Write regression tests in `apps/api/tests/connectors/test_routes.py` for origin denial before body consumption, safe setup-context readiness with absent/malformed files, authenticated context access and non-operator management capability. Verify no key metadata or sentinel values appear in responses.
- [x] Extend `apps/web/tests/ConnectorSetup.test.ts`: a definite origin rejection triggers no status call and permits corrected re-entry; ambiguous delivery retains the ID; a status 503 displays storage-unavailable guidance while remaining blocked; later complete/404/410 resolves correctly; both envelope shapes behave identically.
- [x] Run those tests and observe the intended failures before changing product code.
- [x] Implement the safe context route and error normalization/classification. Use context permission before loading operator-only management data; keep server authorization unchanged.
- [x] Document explicit local key initialization/configuration, shared API/worker identity/files, operator setup and matching browser origins. Set Vite `server.strictPort: true`. Keep actual local installation IDs, operator IDs, origins and host key paths in untracked `.env` files; never stage `.env` files or key material.
- [x] Run the targeted API/browser tests, frontend typecheck and API Ruff. Review and commit the tested fix using the repository's scanned message-file workflow.

**Deliverable:** The reported rejection no longer becomes an uncertain-write loop, and an unprovisioned installation explains why credential setup is unavailable.

## Task 3 (R2) — Minimal multiple-project lifecycle

**Files:** Modify `apps/api/src/openship/workspace/models.py`, `workspace/routes.py`, `chat/routes.py` and `main.py`. Create `workspace/projects.py` for lifecycle logic and `workspace/project_routes.py` for project HTTP routes. Create migration `apps/api/alembic/versions/009b_phase5a1_multiple_projects.py` after `phase5a009b`, using revision `phase5a009c`.

**Interfaces:** `GET/POST /api/projects`, `GET/PATCH/DELETE /api/projects/{project_id}`. Summaries retain `{id, name}`. Create/rename accept `{name}`; create returns 201, delete returns 204. Foreign IDs return 404. Unsafe deletion returns `project_not_empty`/409 or `last_project_required`/409. Missing conversation project selection with multiple candidates returns `project_required`/409.

- [x] Add `apps/api/tests/test_project_lifecycle.py` and `tests/integration/test_project_lifecycle_postgres.py` covering two projects for one owner, name validation, foreign reads/writes, empty deletion, final-project refusal, retained-data refusal, and explicit versus omitted conversation project selection.
- [x] Add real PostgreSQL migration/concurrency cases: preserve existing IDs/defaults/artifact references; simultaneous deletion of the last two projects cannot delete both; concurrent child creation cannot leave an orphan; downgrade refuses multiple projects per owner rather than deleting or merging them.
- [x] Run the new tests to establish failure, then remove only the one-project-per-owner uniqueness constraint while retaining the owner index.
- [x] Implement lifecycle commands with consistent owner/project locking and dependency checks. Retain registration's initial project creation. Move the existing listing route without duplicating it. Preserve existing data and require explicit selection when multiple projects exist.
- [x] Run lifecycle, project-context, auth, workspace and connector-selection tests plus the disposable PostgreSQL lane. Review and commit the tested increment.

**Deliverable:** One owner can safely manage multiple projects without moving or deleting retained history.

## Task 4 (R3) — Project selection and scoped settings tabs

**Files:** Create `apps/web/src/stores/projects.ts` and `views/SettingsView.vue`. Modify `stores/connectors.ts`, `services/api.ts`, `types/index.ts`, `router.ts`, `views/ChatView.vue`, `views/ConnectorSettingsView.vue`, `layouts/AppLayout.vue` and `components/layout/AppSidebar.vue`. Add `components/settings/ProjectSettings.vue` and `GlobalSettings.vue`.

**Interfaces:** `useProjectsStore()` owns `projects`, `currentProjectId`, initialization, create, rename, delete, select and clear. Connector state consumes an explicit owned project ID; it does not maintain a second project list. Settings routes are `/projects/:projectId/settings` and `/settings/global`; `/connectors` keeps the independent connector page, and `/settings/connectors` redirects there. Existing project-default APIs remain unchanged.

- [x] Add `apps/web/tests/ProjectStore.test.ts` and `ProjectSettings.test.ts`: CRUD, two-project switching, foreign/stale selection, independent defaults using one global Brave instance, empty/default-disabled explanations, settings tabs and compatibility navigation.
- [x] Extend chat/sidebar tests for selected-project creation, switching conversation history, deep-linking a conversation in another owned project and logout clearing all project/connector state. Add a late-response test proving project A cannot overwrite B after switching.
- [x] Run the tests to observe failure, then separate project lifecycle state from connector state. Capture mutation project IDs at submission and guard asynchronous results against current-scope changes.
- [x] Implement the sidebar selector's create action and the two settings tabs. Put name/lifecycle controls and search defaults in Project Settings. Global Settings shows safe readiness/authority and links to the Global catalog context. Keep the separate Connectors sidebar entry; do not duplicate its credential forms in Settings.
- [x] Run affected component/store tests, all frontend typechecks and production build. Review and commit the tested increment.

**Deliverable:** Settings and defaults belong visibly to the selected project, and the same global connector can be chosen independently by two projects.

## Task 5 (R4) — Connector cards and scoped configuration dialogs

**Files:** Create `apps/web/src/components/connectors/ConnectorCard.vue`, `ConnectorConfigurationDialog.vue` and `apps/web/src/lib/connectorCatalog.ts`. Modify `apps/web/src/views/ConnectorSettingsView.vue`, the catalog links in the settings components from R3 and `ConnectorCredentialForm.vue`; replace the old all-forms connector presentation after compatibility routing is covered.

**Interfaces:** `connectorCatalog` supplies Brave/Tavily built-in display metadata, publisher, local icon/initials and fixed `visibility: 'global'`. Each card represents a global instance or an unconfigured global service. Search dialog identity includes immutable global scope, `kind` and optional `connectorId`; its request reconciliation is independent of the selected project. Legacy request reconciliation retains its original scope and remains separate from global creation.

- [ ] Add `apps/web/tests/ConnectorCatalog.test.ts`: both services have Global cards even with no configurations; project switching never changes their scope; no Project creation/visibility control exists; publisher/status labels, multiple global instances, read-only consumer cards and disabled/attention states work. Failed loading is a page error rather than fabricated red states. Legacy private defaults have truthful migration information outside the global card list.
- [ ] Add dialog tests for click/keyboard opening, focus return, Escape/close cleanup, scope-bound pending reconciliation, project-switch disposal, global mutation authority and creating another instance. Assert no API-key input exists before selection and no credentials enter stores/storage.
- [ ] Run the new tests to observe failure, then replace inline forms with cards/dialogs using the installed Reka UI dialog primitives and current UI styling. Keep create, replace, disable and revoke behavior; keep defaults on Project Settings, outside the connector catalog/dialogs.
- [ ] Run component tests, typecheck, build and responsive/keyboard Playwright cases. Review and commit the tested increment.

**Deliverable:** A compact supported-connector catalog with details and secret inputs shown only after selection.

## Task 6 (R5) — Requalify the user journey and reopen acceptance

**Files:** Extend `apps/web/tests/e2e/connectors.spec.ts`, real-stack browser fixtures and `apps/api/tests/full_stack/test_managed_connectors.py`; update CI selectors if adding a file. Update `docs/testing/phase5a1-managed-connector-qualification.md` and the parent Phase 5 plan.

- [ ] Add a real API/PostgreSQL browser path: create two projects under one owner; configure global Brave and Tavily instances as the operator; select shared instances as each project's defaults independently; switch projects and verify Global badges persist. Confirm non-operator management and new private-search creation are denied, while runs/evidence remain project-owned. Exercise an explicitly seeded legacy private connector's cutover without copying or exposing its key.
- [ ] Add an unprovisioned-installation path and an origin-rejection path. Verify actionable messages, no automatic key resubmission, no misleading status polling after definite rejection and eventual resolution after an actually uncertain write. Use synthetic keys/transports only.
- [ ] Run the focused regression cases, the existing managed/legacy full-stack CI selector, frontend typecheck/tests/build and relevant PostgreSQL migration/lifecycle tests. Required selected lanes must have nonzero tests and zero required skips. Record actual commands/results and any incomplete lane.
- [ ] Review changed request/response, storage, logs and artifacts for secret sentinels; review the diff and commit verified changes with scanned messages.
- [ ] Update the qualification record with this browser journey and remaining limits, then request accept/revise/stop for 5A.1. Keep Task 8 unstarted until acceptance.

**Deliverable:** Reviewable evidence for the revised 5A.1 experience, with the AWS boundary intact.

## Execution and verification notes

Implement Tasks 1–6 (R0–R5) in order in the existing PR #29 worktree. The user's Global search visibility decision is recorded above. Use native sequential execution, consistent with the parent plan. Each task starts with failing tests, ends with targeted verification and a working commit, and follows the repository [Git workflow](../../../.agents/skills/git-workflow/SKILL.md).

Frontend checks run from `apps/web`: `pnpm typecheck`, `pnpm test:run`, `pnpm build`, then the selected Playwright lane. API checks run from `apps/api` with `.venv/bin/pytest`; PostgreSQL tests use a disposable database URL, never a populated development database by default. Full-stack qualification uses the existing CI selector for `test_manual_search.py` and `test_managed_connectors.py`. Setup-context diagnostics do not authorize live provider calls or changes to local credentials.
