---
type: plan
summary: Implement reversible project and conversation archival, preserve history and resource associations, add contextual archive controls, and consolidate connector dialog actions.
status: proposed
date: 2026-10-22
---

# Project and Conversation Archive and Connector Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Direct execution in this session is recommended because lifecycle admission, metadata, and UI state share closely related interfaces. Implementation starts after plan review and execution-method selection.

**Goal:** Archive and restore projects and conversations without deleting their history or changing associated resources, keep rare archive actions in overflow menus, and place connector configuration actions together.

**Architecture:** Add nullable lifecycle timestamps to the existing Project and Conversation models. Enforce active-state checks inside the same database transactions that admit new user work, while keeping historical reads, cancellation, and publication from already-admitted runs available. Extend existing owner-scoped APIs and Pinia stores; use Reka UI menus/dialogs and the current credential form rather than adding another state or authentication framework.

**Tech Stack:** Existing FastAPI, SQLAlchemy, Alembic, PostgreSQL, Python/pytest, Vue, Pinia, TypeScript, Reka UI, Tailwind, Vitest, and Playwright. No new product dependencies or external services.

**Spec:** [Approved Project and Conversation Archive and Connector Dialog Actions Design](../specs/2026-10-22-project-archive-and-connector-dialog-actions-design.md), approved by the user on 2026-10-22. This is a focused follow-up to 5A.1, not permission to begin later Phase 5 work.

## Global Constraints

- “Archiving changes lifecycle metadata only; a conversation is never physically deleted through this lifecycle.”
- “Archive must not run Terraform, delete or modify external resources, detach resource associations, revoke credentials, delete workspace files or state, or schedule resource cleanup.”
- “Already-admitted runs may finish and retain their outputs, as with project archival; cancellation of existing work remains available.”
- “Project and conversation archive states are independent.”
- “The owner cannot archive the final active project.”
- “There is no final-active-conversation restriction: users may archive every conversation and create a new one in an active project.”
- “Keep this infrequent action out of the permanent conversation controls.”
- “Both existing conversation list APIs (`/api/conversations` and `/api/workspace/conversations`) exclude archived conversations and conversations in archived projects by default.”
- “Keep the existing `DELETE /projects/{id}` route as a compatibility alias for archive during this transition.”
- “The action controls share the row, have clear primary/secondary/destructive emphasis, and preserve existing loading, disabled, and in-progress safeguards.”
- Keep owner isolation and existing connector visibility/default semantics. No Terraform/resource registry implementation, automatic cleanup, or credential migration belongs in this work.
- Reuse the PR #29 worktree. Preserve the existing staged and unstaged changes; capture a baseline of both kinds of changes before execution. Commit only this task's hunks, preserving pre-existing index entries; naming a dirty file alone does not isolate its earlier changes. Follow `.agents/skills/git-workflow/SKILL.md`, including message files in `/tmp/`, a separate successful `rayloc scan` immediately before each message-consuming command, and relevant checks before committing. Do not rebase or discard the dirty worktree.

## Review Focus

1. A stale tab or simultaneous archive/submission must not admit new work after archive wins; admitted work must still finish. Pin with PostgreSQL ordering tests in Task 3.
2. A user upload spanning archive must not publish a new ready file; existing artifact bytes, resource references, and worker publication must survive. Pin with upload and worker tests in Task 3.
3. A lost response retried after archive must return the original admitted run for an exact idempotent replay without executing again; a fresh request must be rejected. Pin with chat/tool admission tests in Task 3.
4. A slow list response, route change, logout, or project switch must not reinsert an archived entry or enable a stale composer. Pin with store/view tests in Tasks 4–5.
5. Archiving every conversation must not hide the recovery menu; keyboard and touch users must reach Archive and Restore without hover. Pin with empty-list component and browser tests in Tasks 5 and 7.

---

## Working contracts

These decisions finalize details left to implementation in the spec. All routes below include the `/api` prefix in production.

| Endpoint | Result |
| --- | --- |
| `GET /projects?state=active` | Default; active owned projects, existing creation/id ordering |
| `GET /projects?state=archived` | Archived owned projects |
| `POST /projects/{id}/archive` / `restore` | `200` ProjectSummary; idempotent, same archived timestamp on repeated archive |
| `DELETE /projects/{id}` | Legacy archive alias; preserve `204`, never physically delete |
| `GET /conversations?state=active` | Default; individually active conversations in active projects |
| `GET /workspace/conversations?state=active` | Same filter, retain the existing `message_count` field |
| Either conversation list with `state=archived` | Union of individually archived conversations and conversations in archived projects, scoped to the owner |
| `GET /conversations/{id}` | Owned metadata, including archived items; supports direct links and snapshot fallback |
| `POST /conversations/{id}/archive` / `restore` | `200` ConversationResponse; owner checked and idempotent while parent project is active |

Use `Literal['active', 'archived']` query validation; reject unsupported state values with `422`. Keep list responses as arrays. Conversation lists retain descending `updated_at` ordering and add descending ID as a deterministic tie breaker. Archived-list entries include `project_name` and `project_archived_at` so callers can distinguish the reason for being hidden. Archived lists are owner-wide; the active sidebar continues its existing selected-project filter.

`ProjectSummary` gains `archived_at: string | null`. `ConversationResponse` and the corresponding browser Conversation gain `archived_at: string | null`, `project_archived_at: string | null`, and `project_name: string`. SnapshotResponse gains the two lifecycle timestamps. New response fields are explicit nulls for active objects, not missing values. Preserve existing IDs and all current fields.

Use `409` with the existing `detail.error` envelope for `project_archived` ("Restore this project before making changes.") and `conversation_archived` ("Restore this conversation before continuing."). If both are archived, report `project_archived` first. Retain `last_project_required` with updated copy "Keep at least one active project." Missing or foreign-owned objects remain indistinguishable `404`s.

**Locking:** Project lifecycle commands retain the existing owner-row → project-row order. Operations that mutate an existing conversation lock conversation → project, matching artifact scope locking; never introduce project → conversation locking. Refresh ORM state when obtaining these locks so a previously loaded object cannot hide an archive that committed while the caller waited. Locks, lifecycle checks, and database mutation/admission must share a transaction. Project archive does not lock every conversation or scan/delete dependencies.

Exact replay of an already-accepted run is a read of the original result, not new execution: retain existing ownership/digest/conflict validation, return the original run, and create no new message, queue job, or event. A fresh request ID requires active project and conversation. Do not add archive checks to shared worker lease, credential acquisition, artifact publication, read, or cancel paths.

## File responsibilities

| Files | Responsibility |
| --- | --- |
| `apps/api/alembic/versions/009f_phase5_archive_lifecycle.py`; `workspace/models.py`; `chat/models.py` | Add lifecycle columns without replacing rows or changing foreign keys |
| `apps/api/src/openship/workspace/lifecycle.py` (new) | Active-state assertions and consistent archived-state HTTP translation |
| `workspace/projects.py`; `workspace/project_routes.py` | Project archive/restore, final-active invariant, legacy DELETE compatibility |
| `apps/api/src/openship/chat/lifecycle.py` (new); `chat/routes.py`; `chat/schemas.py`; `workspace/routes.py` | Owned conversation lifecycle, shared list filtering, metadata projection |
| `events/schemas.py`; `events/service.py` | Lifecycle metadata in snapshots without changing events/cursors |
| `runs/service.py`; `runs/routes.py`; `artifacts/routes.py`; `connectors/service.py`; `connectors/routes.py` | Guard new user mutations atomically and preserve existing replay/publication paths |
| `apps/web/src/types/index.ts`; `services/api.ts`; `stores/projects.ts`; `stores/chat.ts` | Typed lifecycle transport and state, stale-response protection |
| `components/settings/ProjectSettings.vue` | Project Archive, archived-project list, Restore |
| `components/layout/AppSidebar.vue`; `layouts/AppLayout.vue`; new `components/chat/ConversationActionsMenu.vue` and `ArchivedConversationsDialog.vue` | Contextual archive controls, recovery list, presentational event wiring |
| `views/ChatView.vue`; `components/chat/ConversationFiles.vue` | Read-only archived conversation, restore prompt, guarded user actions |
| `components/connectors/ConnectorCredentialForm.vue`; `ConnectorConfigurationDialog.vue`; `lib/connectorCatalog.ts` | One action row, Save label, consistent status treatment and shared busy state |

Paths abbreviated above are relative to `apps/api/src/openship/` or `apps/web/src/` as indicated. Tasks below name exact test paths. Do not edit unrelated modules or apply schema changes to the user's working database during implementation tests.

### Task 1: Add lifecycle persistence and active-state assertions

**Files:** Create `apps/api/alembic/versions/009f_phase5_archive_lifecycle.py`, `apps/api/src/openship/workspace/lifecycle.py`, `apps/api/tests/integration/test_archive_lifecycle_migration.py`, and `apps/api/tests/test_archive_state.py`. Modify `apps/api/src/openship/workspace/models.py` and `apps/api/src/openship/chat/models.py`.

**Interfaces:**
- Produce timezone-aware nullable `Project.archived_at` and `Conversation.archived_at`, default null.
- Produce `ArchiveStateError(code: Literal['project_archived', 'conversation_archived'])`, `assert_active_project(project: Project) -> None`, `assert_active_conversation(conversation: Conversation, project: Project) -> None`, and `archive_http_error(error: ArchiveStateError) -> HTTPException` in `workspace/lifecycle.py`. The conversation assertion checks the parent first; these helpers do not query, commit, or acquire locks.
- Migration revision `phase5a009f`, parent `phase5a009e`. Keep the existing chain; do not rewrite earlier revisions.

- [ ] **Step 1 — Write migration and assertion tests.** Name the preservation case `test_upgrade_preserves_existing_archive_scope_records` and the state matrix `test_active_scope_error_precedence`. Follow the existing Alembic subprocess pattern. Seed project/conversation/message/artifact/workspace/default records at `phase5a009e`, upgrade, and compare IDs, relations, counts, and artifact keys. Test active, archived, and both-archived cases; representative assertions:

  ```python
  assert project.archived_at is None
  assert conversation.archived_at is None
  assert after_record_ids == before_record_ids
  assert after_workspace_root == before_workspace_root
  assert archive_http_error(ArchiveStateError('project_archived')).status_code == 409
  ```

  Add `test_downgrade_refuses_to_lose_archive_state`: downgrade is rejected before removing columns if either table contains archived rows. An unused migration may downgrade without deleting existing data.
- [ ] **Step 2 — Confirm the tests fail.** From `apps/api`, run `uv run pytest tests/test_archive_state.py tests/integration/test_archive_lifecycle_migration.py -q`. Expect missing lifecycle fields/helpers before implementation.
- [ ] **Step 3 — Implement columns, migration, and helpers.** Add only the nullable timestamps and the shared error/assertion functions. The upgrade performs no filesystem/provider calls or secret import. Guard downgrade before any schema changes when archive state exists.
- [ ] **Step 4 — Verify.** Rerun the tests and `uv run alembic heads`; expect passing tests and one head, `phase5a009f`.
- [ ] **Step 5 — Commit only these files** with a scanned message file describing additive archive persistence.

### Task 2: Implement owner-scoped archive/restore APIs and metadata reads

**Files:** Modify `apps/api/src/openship/workspace/projects.py`, `workspace/project_routes.py`, `chat/routes.py`, `chat/schemas.py`, `workspace/routes.py`, `events/schemas.py`, and `events/service.py`. Create `apps/api/src/openship/chat/lifecycle.py` and `apps/api/tests/test_conversation_lifecycle.py`. Modify `apps/api/tests/test_project_lifecycle.py`, `test_chat_conversations.py`, and `test_conversation_workspace.py`.

**Interfaces:**
- Consume Task 1 columns/assertions and the working contracts above.
- Extend `list_owned_projects(db: Session, owner_id: UUID, *, state: Literal['active', 'archived'] = 'active') -> list[Project]`.
- Add `archive_project(db: Session, *, owner_id: UUID, project_id: UUID) -> Project` and `restore_project(db: Session, *, owner_id: UUID, project_id: UUID) -> Project`; neither commits. Replace physical deletion in `delete_project` with delegation to archive so direct legacy service callers are also safe.
- In `chat/lifecycle.py`, add `get_owned_conversation_scope(db: Session, *, owner_id: UUID, conversation_id: UUID, for_update: bool = False) -> tuple[Conversation, Project]`, `list_owned_conversations(db: Session, *, owner_id: UUID, state: Literal['active', 'archived'] = 'active') -> list[tuple[Conversation, Project]]`, and `archive_conversation(db: Session, *, owner_id: UUID, conversation_id: UUID) -> tuple[Conversation, Project]` / `restore_conversation(db: Session, *, owner_id: UUID, conversation_id: UUID) -> tuple[Conversation, Project]`. Return no foreign data.
- Add `conversation_response(conversation: Conversation, project: Project) -> ConversationResponse` in `chat/schemas.py`; reuse it for both list APIs, metadata, create, archive, and restore. The workspace list adds its existing message count.

- [ ] **Step 1 — Write route/service tests.** Add `test_project_archive_preserves_populated_scope`, `test_archive_restore_idempotency`, `test_final_active_project_cannot_be_archived`, `test_conversation_lists_use_same_lifecycle_filter`, and `test_parent_restore_preserves_child_archive`. Cover both state filters, own/foreign IDs, malformed query state, repeat archive/restore, final active project, archiving a populated project, DELETE compatibility, direct metadata/messages/workspace/snapshot reads after archive, and independent project/conversation restoration. Preserve timestamps on no-op archive. Assert:

  ```python
  assert legacy_delete.status_code == 204
  assert retained_project_id == original_project_id
  assert archived_conversation['id'] == restored_conversation['id']
  assert archived_conversation['project_name'] == project_name
  assert active_list_ids.isdisjoint(hidden_ids)
  assert archived_list_ids == individually_archived_ids | parent_archived_ids
  assert project_restore_did_not_clear_conversation_archive
  ```

  Record rows, file keys, and secret-free references before and after; patch external invocation and cleanup entry points to fail if archive/restore calls them. There is no Terraform resource registry to add just for this test.
- [ ] **Step 2 — Confirm failure.** Run `uv run pytest tests/test_project_lifecycle.py tests/test_conversation_lifecycle.py tests/test_chat_conversations.py tests/test_conversation_workspace.py -q` from `apps/api`; expect absent archive endpoints/filters.
- [ ] **Step 3 — Implement commands and routes.** Use the defined lock orders. Count only active projects under the owner lock; repeated archive of an already-archived project bypasses the final-active check. Conversation archive/restore requires an active parent. Reject rename and explicit conversation creation in archived projects; implicit project selection considers active candidates only. Translate archive errors into the exact conflict envelope, not generic 500/503 responses. Remove the obsolete retained-data deletion restriction and dependency-scanning code used solely by physical project deletion.
- [ ] **Step 4 — Implement metadata projection and list consistency.** Join projects with owner constraints; default both conversation lists to active scopes. Include timestamps in direct metadata and snapshots without filtering archived reads or changing event sequences. Never treat a missing active-list entry as a missing conversation.
- [ ] **Step 5 — Verify and commit.** Rerun Step 2 and the existing project-context tests: `uv run pytest tests/test_project_context.py -q`. Expect all selected tests to pass; commit the API/lifecycle unit using the scanned-file workflow.

### Task 3: Fence new user work and prove race/resource-preservation behavior

**Files:** Modify `apps/api/src/openship/runs/service.py`, `runs/routes.py`, `artifacts/routes.py`, `connectors/service.py`, and `connectors/routes.py`. Modify `apps/api/tests/test_run_commands.py`, `test_tool_run_commands.py`, `artifacts/test_routes.py`, `connectors/test_routes.py`, `test_tool_worker.py`, and `integration/test_project_lifecycle_postgres.py`. Create `apps/api/tests/integration/test_archive_lifecycle_postgres.py`.

**Interfaces:**
- Consume Task 1 assertions and Task 2 lifecycle commands.
- `submit_turn` and `submit_tool_run` retain their signatures and exact idempotency behavior. For fresh work, lock conversation then project, refresh state, assert both active, and admit within that transaction.
- User upload and file-delete endpoints enforce the same scope requirement. Shared worker artifact reservation/publication helpers stay usable for already-admitted runs.
- Project connector/default writes translate `ArchiveStateError` to `409`; status/history GETs remain readable. Installation-global connector management remains independent of any selected project's archived state.

- [ ] **Step 1 — Write fresh-admission and replay tests.** Add `test_archived_scope_rejects_fresh_admission` and `test_exact_accepted_request_replay_survives_archive`, parameterized over chat/tool submissions. Assert the replay run ID and all persistent counts stay identical; assert fresh rejection status is `409` and its code matches the archived scope. Cover archived conversation, archived project, and both states for fresh chat/tool request IDs. Assert no new Message, Run, queue Job, Event, or outbox row after rejection. For exact already-accepted requests, assert the original run is returned after archive with unchanged counts; changed payloads keep existing idempotency conflicts.
- [ ] **Step 2 — Write file and settings tests.** Add `test_archive_during_upload_rejects_publication_without_touching_existing_files` and `test_archived_project_rejects_settings_mutations`, parameterized over the applicable routes. New uploads, user file removal, project rename/default/credential/connector mutation fail with the matching `409` before changing state. Simulate archive after upload reservation but before bytes are published: publication is rejected, the new reservation is safely released through existing cleanup, and pre-existing files remain intact. Confirm preview/download, request-status reads, and global operator management still work.
- [ ] **Step 3 — Write real PostgreSQL ordering tests and a worker completion test.** Add `test_archive_serializes_with_admission`, `test_concurrent_project_archives_leave_one_active`, `test_restore_serializes_with_parent_archive`, and `test_admitted_worker_publishes_after_archive`. Use bounded waits and two sessions/barriers, following the existing disposable-database fixtures. Test archive-first and admission-first orders, simultaneous attempts to archive the owner's final two active projects, and conversation restore versus parent archive. Refresh preloaded ORM objects when locks are acquired. Assert:

  ```python
  assert fresh_work_after_archive.code in {'project_archived', 'conversation_archived'}
  assert active_project_count >= 1
  assert worker_result.run_id == admitted_run_id
  assert retained_artifact_bytes == original_artifact_bytes
  assert observed_external_calls_from_archive == []
  ```

  The worker test archives after run admission, completes a deterministic synthetic search, verifies retained output publication, and confirms cancellation is still accepted for another active run. No live provider key or Terraform execution is needed.
- [ ] **Step 4 — Confirm failures.** Run `uv run pytest tests/test_run_commands.py tests/test_tool_run_commands.py tests/artifacts/test_routes.py tests/connectors/test_routes.py tests/test_tool_worker.py -q`; expect archived work to be admitted before guard implementation. Run `uv run pytest tests/integration/test_archive_lifecycle_postgres.py -q -m postgres` against a disposable PostgreSQL database using the existing test harness. Skipped PostgreSQL tests are not evidence that ordering works.
- [ ] **Step 5 — Implement admission guards and error translation.** Check only after ownership/replay validation and while holding the conversation/project locks for fresh admission. Keep accepted-run replay, reads, event delivery, cancellation, and worker publication outside the active-state restriction. For user uploads, recheck under scope locks before filesystem publication/final commit after the body has streamed; do not hold the transaction open while reading the request body. For project connector writes, acquire and refresh the project before mutable connector/default rows; if request-ledger conflict handling rolls back the transaction, reacquire the scope lock and revalidate before applying a write. Do not add project → conversation lock acquisition.
- [ ] **Step 6 — Verify and commit.** Rerun the Step 4 unit command and `uv run pytest tests/integration/test_archive_lifecycle_postgres.py tests/integration/test_project_lifecycle_postgres.py tests/integration/test_tool_admission_postgres.py tests/integration/test_artifact_quota_postgres.py -q -m postgres`. Require nonzero tests and zero required skips. Commit this safety unit with a scanned message file; report any unavailable PostgreSQL lane instead of claiming it passed.

### Task 4: Add typed lifecycle stores and Project Settings archive/restore

**Files:** Modify `apps/web/src/types/index.ts`, `services/api.ts`, `stores/projects.ts`, `stores/chat.ts`, and `components/settings/ProjectSettings.vue`. Modify `apps/web/tests/ProjectStore.test.ts`, `ProjectSettings.test.ts`, `ChatStore.test.ts`, and `helpers/chatFixtures.ts`, plus existing response fixtures that need the new fields.

**Interfaces:**
- Consume Task 2 API/metadata contracts. Add `type ArchiveListState = 'active' | 'archived'` and the required nullable lifecycle fields to public types.
- `projectsApi.list(state: ArchiveListState = 'active')`, `.archive(projectId: string)`, and `.restore(projectId: string)` return ProjectSummary data. Remove the web client's delete method and its callers.
- Add `conversationsApi.list(state: ArchiveListState = 'active')`, `.get(conversationId: string)`, `.archive(conversationId: string)`, and `.restore(conversationId: string)`, returning Conversation data; list uses `/workspace/conversations`.
- Project store: `archivedProjects`, `loadArchivedProjects(): Promise<void>`, `archiveProject(id: string): Promise<void>`, `restoreProject(id: string): Promise<ProjectSummary>`.
- Chat store: `archivedConversations`, `loadArchivedConversations(): Promise<void>`, `archiveConversation(id: string): Promise<void>`, `restoreConversation(id: string): Promise<Conversation>`, state `conversationMetadataReady: boolean`, and computed `currentConversationReadOnly: boolean`. Existing `loadConversation` obtains owned metadata even when the conversation is absent from the active list; metadata failure cannot enable the composer. ChatView disables the composer for an existing-conversation route until `conversationMetadataReady` is true; an intentional new-conversation route remains usable.

- [ ] **Step 1 — Write store/settings tests.** Add `archive keeps the selected conversation readable`, `ignores a list response started before archive`, `metadata failure keeps an existing conversation read-only`, and `archive switches to the remaining active project` cases. Assert active project archive selects the first remaining active project; failed archive keeps selection and lists; restored projects reappear without silently selecting them. Archive the selected conversation and retain its messages/current view while removing its active-list entry. Load direct archived metadata and retain the archived guard despite snapshot failure. Restore preserves identity and messages. Use deferred responses to prove stale list loads, project switches, and logout cannot reinsert entries or restore a superseded current conversation.

  ```typescript
  expect(chat.currentConversation?.id).toBe(conversationId)
  expect(chat.currentConversationReadOnly).toBe(true)
  expect(chat.conversations.some(c => c.id === conversationId)).toBe(false)
  expect(projects.currentProjectId).toBe(remainingProjectId)
  ```

- [ ] **Step 2 — Confirm failure.** From `apps/web`, run `pnpm test:run tests/ProjectStore.test.ts tests/ProjectSettings.test.ts tests/ChatStore.test.ts`; expect missing archive methods/state.
- [ ] **Step 3 — Implement transport and stores.** Extend load/mutation generation guards to both active/archived lists and current metadata. Apply list/current-selection changes only after successful mutation responses; invalidate older reads at mutation start. Use each request's captured object identity so completion for one scope cannot mutate another. On lifecycle `409`, reload authoritative metadata/lists and display the safe server message; keep readable history available. Clear archived lists and pending lifecycle state on logout. Never clear pending accepted run IDs merely because a conversation is archived.
- [ ] **Step 4 — Update Project Settings.** Replace Delete with Archive and archival copy, retain the final-active restriction, and use red border/text on `bg-background`. Render Archived Projects with Restore and per-row busy/error state. Direct settings routes for an archived project show retained metadata and Restore, disable edits, and keep the active picker selection unchanged; resolve the project from the archived list or owned metadata rather than treating it as missing. After archiving the selected project, navigate to the remaining project settings and reload connector context. Coordinate chat history reload so that project's conversations disappear from active navigation; do not erase messages or artifacts from storage.
- [ ] **Step 5 — Verify and commit.** Rerun Step 2 and `pnpm typecheck`, updating existing fixtures with explicit null lifecycle values. Commit the typed clients/stores/settings changes with the required scanned message file.

### Task 5: Add contextual conversation controls and the archived read-only view

**Files:** Create `apps/web/src/components/chat/ConversationActionsMenu.vue`, `ArchivedConversationsDialog.vue`, and `apps/web/tests/ConversationArchive.test.ts`. Modify `apps/web/src/components/layout/AppSidebar.vue`, `layouts/AppLayout.vue`, `views/ChatView.vue`, `components/chat/ConversationFiles.vue`, and tests `AppLayout.test.ts`, `ChatView.test.ts`, and `ConversationFiles.test.ts`.

**Interfaces:**
- Consume Task 4 chat/project stores. Keep AppSidebar and AppLayout presentational, adding `archiveConversation: [id: string]` and `showArchivedConversations: []` events.
- `ConversationActionsMenu` props: `{ conversationId: string; title: string; disabled?: boolean }`; emit `archive: [id: string]`. Use existing Reka dropdown-menu primitives with a labelled overflow trigger, not a new menu dependency.
- `ArchivedConversationsDialog` props: `{ open: boolean; conversations: Conversation[]; loading: boolean; restoringId: string | null; error: string | null }`; emit `update:open`, `openConversation: [id: string]`, `restoreConversation: [id: string]`, and `restoreProject: [id: string]`. Parent project requirements take precedence over conversation restore. Route project restoration through the existing Project Settings restore UI.
- `ConversationFiles` gains `readOnly?: boolean` default false; previews, pagination, and downloads remain available; deletion emits are blocked in read-only mode. ChatView also guards mutation handlers, rather than depending solely on disabled controls.

- [ ] **Step 1 — Write component/view tests.** Add `row archive does not select the conversation`, `empty history keeps archive recovery reachable`, `archived conversation keeps files readable`, and `restore does not submit the retained draft` cases. Check there is no standalone Archive button in the conversation toolbar. Opening a row menu reveals Archive without selecting the row; clicking it archives only that ID. The list overflow contains Archived conversations when the list is empty. Test Escape, focus return, keyboard navigation, and the touch trigger. Opening an archived item displays retained messages/files, disables chat/tool submission and file deletion, and shows Restore; restore does not auto-send a draft. The parent-project case offers project restoration first.

  ```typescript
  expect(wrapper.find('button[aria-label="Archive conversation"]').exists()).toBe(false)
  expect(menu.get('[role="menuitem"]').text()).toContain('Archive')
  expect(files.emitted('delete-file')).toBeUndefined()
  expect(chat.sendMessage).not.toHaveBeenCalled()
  ```

- [ ] **Step 2 — Confirm failure.** Run `pnpm test:run tests/ConversationArchive.test.ts tests/AppLayout.test.ts tests/ChatView.test.ts tests/ConversationFiles.test.ts` from `apps/web`.
- [ ] **Step 3 — Implement contextual menus and recovery dialog.** Put overflow triggers beside, not inside, the row's existing button to avoid nested interactive elements. Reveal row triggers on hover/focus for pointer devices and provide visible usable targets with coarse pointers. Add a list-level overflow trigger independent of the nonempty-list branch; put Archived conversations only inside that menu. Use red menu text for Archive; any dialog Archive button uses red border/text on the theme background. Keep focus usable if the archived row disappears, falling back to the list trigger. Show request failures in the relevant view without falsely moving an entry.
- [ ] **Step 4 — Wire read-only state and restoration.** Keep a currently open archived conversation at its existing URL with history and a contextual restore banner. Disable composer/attachment/tool/retry controls and file mutations while preserving run status, refresh, cancellation, preview, and download. A restore enables editing only when the parent is active. Direct links to conversations in archived projects must not silently switch the active project picker or load that project's tool context as writable. Refetch lifecycle metadata on view re-entry and after lifecycle conflicts; never auto-run anything during archive/restore.
- [ ] **Step 5 — Verify and commit.** Rerun Step 2 and `pnpm typecheck`. Confirm all-conversations-archived recovery works; commit this UI behavior with a scanned message file.

### Task 6: Consolidate connector actions without weakening credential safeguards

**Files:** Modify `apps/web/src/components/connectors/ConnectorCredentialForm.vue`, `ConnectorConfigurationDialog.vue`, and `apps/web/src/lib/connectorCatalog.ts`. Modify `apps/web/tests/ConnectorConfigurationDialog.test.ts` and `ConnectorSettingsCatalog.test.ts`; create `apps/web/tests/ConnectorCredentialForm.test.ts` to pin the slot/reconciliation contract.

**Interfaces:**
- Add an `actions` scoped slot to ConnectorCredentialForm, rendered within its existing action row after Save and any Check request status button. Slot props: `{ blocked: boolean; submitting: boolean }`; neither exposes the key. Add optional `disabled?: boolean` default false, combined with the form's existing blocked state for input/submission. Emit `busyChange: [busy: boolean]` for the owning dialog; preserve the existing complete event and pending-request scope keys.
- Add `connectorStatusChipClass(status: ConnectorCardStatus): string` beside `connectorStatusDotClass` so the dot and chip share the existing configured/not-configured/needs-attention mapping. Keep status semantics; a transient check failure must not claim that a key was revoked.

- [ ] **Step 1 — Write focused interaction tests.** Add `replacement actions share the save row` and `uncertain credential save keeps reconciliation available without resubmission`. Assert credential-submit is called exactly once even after request-status reconciliation. Confirm Save, Check status, Revoke, and Add another have the same action-row ancestor; Rotate API key is absent. Confirm both global and legacy project replacement paths still call their existing credential endpoints. During an uncertain save, Save/check/revoke/add-another cannot interfere, but Check request status remains usable and reconciles without resending the secret. Confirm successful Save updates dot/chip while the dialog remains open, Revoke uses outline styling, and no Add another button appears on the catalog page.
- [ ] **Step 2 — Confirm failure.** Run `pnpm test:run tests/ConnectorConfigurationDialog.test.ts tests/ConnectorSettingsCatalog.test.ts tests/ConnectorCredentialForm.test.ts` after creating the new test file.
- [ ] **Step 3 — Implement the shared action row.** Rename credential replacement submit labels to Save, project and global. Keep create-specific labels in the create form. Supply Check status, Revoke, and Add another through the scoped slot; preserve Close/Back header navigation. The dialog combines credential busy notifications with provider/revoke busy state for its slot actions; pass only external provider/revoke busy state back into the form's `disabled` prop to avoid a self-latching busy loop. Busy state includes credential submission/reconciliation, provider checks, and revocation; keep Check request status available when recovering an uncertain request. Disable unrelated actions for the whole operation, retain secret clearing before the network request, and never expose keys to slot consumers or read responses. Permit responsive wrapping in the same action container on narrow screens.
- [ ] **Step 4 — Apply semantic chip and destructive-outline styling.** Green configured, grey not-configured/disabled, red needs-attention, matching the existing dot mapping. Keep the successful-save status update visible and honor subsequent authoritative connector refreshes. Existing revoke continues to remove setup/secrets and preserve historical evidence; this task adds no new deletion behavior.
- [ ] **Step 5 — Verify and commit.** Rerun Step 2, `pnpm typecheck`, and the existing connector-store tests. Commit this independent connector UI cleanup with the scanned-file workflow.

### Task 7: Qualify full user journeys and document archive semantics

**Files:** Create `apps/web/tests/e2e/archive-lifecycle.spec.ts`, `apps/web/tests/full-stack/archive-lifecycle.spec.ts`, and `docs/operations/project-conversation-archive.md`. Modify `apps/web/tests/e2e/fixtures/api.ts` for lifecycle-aware response fixtures and `docs/operations/managed-connectors.md` for Save/action-row wording. Record outcomes in this plan and the 5A.1 review plan without marking later Phase 5 units complete.

**Interfaces:** Consume the preceding API, store, and UI contracts; no additional runtime interfaces or dependencies.

- [ ] **Step 1 — Write browser acceptance tests.** Name the journeys `archives through contextual menus and restores from empty history` and `keyboard and touch can recover archived conversations`. A mocked browser journey covers desktop hover/focus, touch-accessible row menus, Archive, empty-list recovery menu, Restore, failed mutations, and no persistent Archive button. Use role/name selectors and retained IDs, not screenshots alone.
- [ ] **Step 2 — Write the real-API/filesystem journey.** Name the case `preserves conversation identity and file bytes through archive and restore`. Reuse the existing full-stack harness with a disposable test account/database/artifact volume. Create two projects and a conversation with an uploaded file. Archive it through the menu, inspect it read-only from Archived conversations and by direct URL, verify identical downloaded bytes/digest, then restore and continue the same conversation through a deterministic model/tool transport. Archive the parent, restore it, and verify an individually archived child remains archived. Assert resources/workspace paths and execution counts do not change merely from archive/restore. No real credentials or Terraform are needed.
- [ ] **Step 3 — Run the targeted browser tests.** From `apps/web`, run `pnpm test:e2e tests/e2e/archive-lifecycle.spec.ts` and `pnpm test:e2e:full-stack tests/full-stack/archive-lifecycle.spec.ts` with the existing synthetic API/worker stack running. Expect executed passing cases; listing/discovering tests alone does not qualify the flow.
- [ ] **Step 4 — Document the implemented behavior.** Explain where the two overflow menus live, archived read-only inspection, Restore before cleanup/resource work, project/child independence, last-active-project restriction, legacy DELETE archive semantics, and the fact that archive/restore never triggers resource cleanup. Document additive migration and the archive-aware downgrade guard. Use generic paths and no local connection details or secrets.
- [ ] **Step 5 — Run final checks once after all changes.** From `apps/api`, run `uv run ruff check .` and `uv run pytest -q -m 'not postgres and not provider_live and not full_stack'`. Run the Task 3 PostgreSQL lane with zero required skips. From `apps/web`, run `pnpm typecheck`, `pnpm test:run`, and `pnpm build`, followed by the Task 7 browser journeys. If a lane is unavailable, report exactly what was not run and retain that acceptance gap; do not substitute live development data or keys.
- [ ] **Step 6 — Review the resulting diff against the spec.** Confirm no physical project/conversation delete remains in the API/service lifecycle, no archive-triggered resource or workspace cleanup was introduced, worker publication still works, and staged unrelated PR changes were preserved. Use the appropriate review skill with the execution method chosen by the user. Record actual command/results and any limitations; mark this plan implemented only when its required work and verification are complete.
- [ ] **Step 7 — Commit documentation and acceptance tests** through the repository Git workflow. Keep the current worktree for the user's review; do not merge, publish, or remove it as part of this plan.

## Spec coverage and handoff

| Spec acceptance criteria | Owner tasks |
| --- | --- |
| 1–4, 7: project retention, archive/restore, final-active invariant, legacy DELETE | 1–4; real races in 3 |
| 5–6: archived scope guards and admitted-run completion | 2–3, 5 |
| 8: Project Settings archive/restore and outline style | 4 |
| 9–11: connector action row, Save, status semantics | 6 |
| 12: isolation, default behavior, retained evidence | 2–3, 6–7 |
| 13–18: conversation retention, restore, resource preservation, parent independence | 1–5, 7 |
| 19: contextual, keyboard/touch, empty-list recovery | 5, 7 |

Plan self-review: every spec criterion has an owner, all five Review Focus cases have explicit tests, endpoint/type names are shared across tasks, and no Terraform or later Phase 5 implementation is implied. The user approved the written spec; this implementation plan is awaiting review. Recommended execution: directly in this session using `superpowers:executing-plans`, preserving the existing worktree and completing tasks in order.
