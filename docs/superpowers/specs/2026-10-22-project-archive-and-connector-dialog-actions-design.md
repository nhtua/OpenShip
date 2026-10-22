---
type: spec
summary: Archive and restore projects and conversations while preserving history and resource associations, and consolidate connector dialog actions.
status: proposed
date: 2026-10-22
---

# Project and Conversation Archive and Connector Dialog Actions Design

## Intent

Replace project deletion with reversible archival so OpenShip retains project history and related data, while keeping archived projects out of everyday selection. Make connector configuration actions easier to understand by keeping them together and making save, health check, and revocation explicit.

Conversations are durable working contexts for created resources as well as historical records. Archive projects and conversations to hide them from active navigation while preserving their identity, history, and resource associations. Users restore an archived conversation before continuing work, including future infrastructure cleanup through Terraform. Connector revocation remains a separate explicit action.

## Scope

This change covers:

- Project archive and restore lifecycle for the existing owner-only Projects feature.
- Active and archived project discovery and selection.
- Conversation archive and restore, with archived conversations hidden from the normal left-pane list and accessible through a separate archived list.
- Preserving conversation history, workspace/state, and resource associations without changing associated resources.
- Requiring conversation restore before new work or user-initiated changes.
- Rejecting new project work and project-scoped settings mutations while a project is archived.
- Replacing the Project Settings `Delete` action with `Archive` and adding `Restore`.
- Consolidating connector dialog actions into one action row and changing `Rotate API key` to `Save`.

It does not add project membership, permanent project or conversation deletion, Terraform execution functionality, archival retention policies, bulk actions, connector history management, or changes to connector visibility/default selection.

## Project lifecycle and data retention

Add a nullable `archived_at` timestamp to Project. A null value means active; a timestamp means archived. Existing projects remain active after migration. Archiving updates project lifecycle metadata only: it does not delete the project row, conversations, runs, files, artifacts, settings, connector associations, or other project-owned records. It does not implicitly revoke connectors or erase secrets.

Projects have two lifecycle states:

| State | Project picker | Reads | New conversations/turns | Project-scoped settings writes |
| --- | --- | --- | --- | --- |
| Active | Listed | Allowed under existing access rules | Allowed | Allowed |
| Archived | Hidden from active picker; shown in archived-projects list | Allowed under existing access rules | Rejected | Rejected |

An already-admitted run is not canceled by archiving and may finish and publish its normal results. Archiving prevents later conversation creation and turn submission. Existing conversation history and files remain readable. Restoring a project re-enables writes and returns it to the active picker without altering its retained data.

The owner cannot archive the final active project. If the current project is archived, the UI selects another active project. If no other active project exists, the archive action is unavailable and the API rejects the request. A project must be restored before its name, settings, connectors, or conversations can be changed; cancellation of existing work remains available.

## Conversation lifecycle and resource preservation

Add a nullable `archived_at` timestamp to Conversation. Existing conversations remain active. Archiving changes lifecycle metadata only; a conversation is never physically deleted through this lifecycle. Keep its stable ID, owning project, messages, run history, artifacts, workspace files and state, and any associated resource records and references intact.

Archive must not run Terraform, delete or modify external resources, detach resource associations, revoke credentials, delete workspace files or state, or schedule resource cleanup. Restore likewise changes only lifecycle metadata; it does not reprovision resources, rerun previous operations, or replay failed work. Existing and future resource-management features continue to use the same restored conversation and retained state.

| Conversation state | Normal left-pane list | History and retained files | New work and user-initiated mutations |
| --- | --- | --- | --- |
| Active, with active project | Listed | Readable under existing access rules | Allowed under existing access rules |
| Archived | Hidden; discoverable in Archived conversations | Readable under existing access rules | Restore required |
| Parent project archived | Hidden from active navigation regardless of conversation state | Readable under existing access rules | Project restore required; conversation restore also required if individually archived |

Archived conversations reject new chat turns, explicit tool commands, execution retries/resumes, and user-initiated changes to conversation settings or files. The server enforces this even if a stale browser or direct API client attempts to submit work. Already-admitted runs may finish and retain their outputs, as with project archival; cancellation of existing work remains available. Archiving itself neither cancels work nor admits another execution.

Project and conversation archive states are independent. Archiving a project does not overwrite its conversations' `archived_at` values. Restoring a project does not restore conversations archived individually. Restore the project before restoring or resuming one of its archived conversations. There is no final-active-conversation restriction: users may archive every conversation and create a new one in an active project.

## API and persistence behavior

- Add `archived_at` to Project and Conversation through an additive migration; preserve all existing rows and foreign-key relationships.
- Add owner-checked, idempotent conversation archive and restore actions. Do not add a physical conversation deletion endpoint.
- Both existing conversation list APIs (`/api/conversations` and `/api/workspace/conversations`) exclude archived conversations and conversations in archived projects by default. Provide an explicit owner-scoped archived-conversation list for recovery, retaining project identity in each entry. Include both individually archived conversations and those hidden by project archival, and distinguish these states.
- Include lifecycle state in conversation responses and the metadata used when opening a conversation directly, so archived conversations remain readable and the browser can display the restore requirement. Apply the active-project and active-conversation checks at execution admission and user mutation boundaries, atomically with the admitted change.
- The normal project list returns active projects only. Provide an owner-scoped way to list archived projects for the restore UI.
- Add explicit archive and restore actions on an owned project. They are idempotent when the project is already in the requested state.
- Keep the existing `DELETE /projects/{id}` route as a compatibility alias for archive during this transition. It must never physically delete a Project or cascade-delete project data. The web client uses the explicit archive action.
- Enforce owner access and active-project requirements in the shared project/service boundary, not only in button state. Return a stable conflict response for attempts to mutate an archived project or archive the final active project.
- Apply the archived-project write guard to conversation creation, turn/run submission, and project-scoped settings mutations, including connector/default changes. Read-only history endpoints remain available. Restore is permitted for the owner from the archived state.

## Project Settings experience

Replace `Delete` with `Archive` in Project Settings. The destructive-looking control uses a transparent/theme background with a red border and red text, consistent with the application's outline action style. It does not use a solid red fill.

Add an Archived Projects view/list to Project Settings, with project name and a `Restore` action. Archived projects do not appear in the normal project picker. Restore returns the project to the active picker. When archiving the selected project, switch the selection to another active project and refresh both project lists. If there is no other active project, explain why archive is unavailable.

## Conversation navigation and restore experience

Keep this infrequent action out of the permanent conversation controls. Put `Archive` inside a conversation row's overflow (`⋯`) menu in the left pane. Reveal the overflow trigger on pointer hover or keyboard focus, and provide a usable tap target on touch devices; do not rely on hover or long-press alone. Use the theme's destructive menu-item treatment (red text), and apply the red-border/theme-background style to any archive action button in a dialog. Remove the entry from the active list only after the archive request succeeds. If that conversation is currently open, keep its history visible in a read-only view with an archived indicator and `Restore` action; disable the composer and file mutations until restoration.

Place `Archived conversations` inside the conversation list's overflow menu rather than adding a permanent archive button or sidebar navigation item. Keep that menu available even when all conversations are archived. Its entry opens a separate list with each conversation's title, owning project, and `Restore` action. Users can open an archived conversation to inspect history and retained files without restoring it. When the parent project is archived, show the project restore requirement instead of offering conversation restore. A conversation hidden only by project archival returns after project restore; one archived individually still requires conversation restore. Restoring a conversation returns it to normal navigation and enables work only when its project is active. Do not create a replacement conversation, clone history, or initiate resource operations during archive or restore.

## Connector configuration dialog actions

Keep the dialog header and connector configuration content intact. Place the configuration actions together in a single action row within the dialog:

- `Save` saves the entered credential/configuration; this replaces the `Rotate API key` label.
- `Check status` performs the existing provider validity check and reports the resulting status.
- `Revoke` removes the selected connector setup and its secret material; a revoked setup is not retained as an inactive connector.
- `Add another` starts configuration of another credential instance for the same connector, when multiple credentials are supported.

The action controls share the row, have clear primary/secondary/destructive emphasis, and preserve existing loading, disabled, and in-progress safeguards. `Revoke` uses the outline destructive style: red border and text on the normal theme background. Status presentation uses the same semantic colors as the connector dot: green for configured/healthy, grey for not configured, and red for outdated or failed status. Revocation must keep unrelated project data and secret-free historical execution/audit evidence intact.

## Failure behavior

- Archiving and restoring are owner-checked and transactional with the final-active-project invariant.
- If an archive or restore request fails, keep the current project selection and show a recoverable error; refresh lifecycle state before retrying when the server reports a conflict.
- Failed conversation archive/restore requests leave the current view and list membership intact and show a recoverable error. Lifecycle conflicts refresh conversation and parent-project state.
- The UI disables actions when it knows the operation is not available, but the API remains authoritative against stale browser state and concurrent requests.
- Connector save/check/revoke failures remain visible in the dialog, with status refreshed after successful operations. A failed revoke must not make the UI claim that the connector was removed.

## Acceptance criteria

1. Migration gives all existing projects `archived_at = null` and leaves all existing project-owned records intact.
2. An owner can archive a non-final active project; it disappears from the active picker and appears in the archived list.
3. An owner can restore an archived project; it reappears in the active picker with its conversations, files, settings, and associations intact.
4. Archiving the current project selects another active project. Archiving the final active project is rejected by both UI and API.
5. Archived project history remains readable, while creation of conversations, submission of new turns/runs, and project-scoped settings writes are rejected until restore.
6. Already-admitted runs are not canceled by archival and can finish through existing execution and publication paths.
7. The legacy project DELETE route cannot physically delete a project or its related records and follows archive semantics.
8. The Project Settings destructive action is labeled `Archive` and uses red border/text on the regular theme background; the archived list provides `Restore`.
9. Connector dialog actions appear together in one action row; `Rotate API key` is labeled `Save`.
10. `Check status` reports current provider status, `Revoke` removes that connector setup and its secret material, and `Add another` is available only in the connector dialog.
11. Connector configured/failed/not-configured status uses the same green/red/grey semantic treatment for both dot and status chip.
12. Existing owner isolation, connector visibility/default behavior, and historical execution evidence remain intact.
13. Existing conversations migrate as active. Archiving a conversation removes it from both normal conversation-list APIs and the left-pane list while preserving its ID, history, workspace files/state, and resource associations.
14. Archived conversations remain readable and discoverable through Archived conversations. Restore returns the same conversation to the active list and permits continued work when its project is active.
15. An archived conversation rejects new turns, explicit tool execution, retries/resumes, and user-initiated settings/file changes until restored. Direct API requests and stale browser tabs cannot bypass the guard; cancellation of existing work remains available.
16. Archive and restore cause no external resource calls, Terraform execution, workspace/state deletion, resource detachment, or replay. Future cleanup of associated resources requires restoring the conversation and explicitly starting that work.
17. Project archive/restore preserves each conversation's individual lifecycle state. A conversation in an archived project cannot be restored or resumed until the project is restored.
18. Users may archive every conversation. Failed requests do not falsely remove or restore entries, and a currently open archived conversation shows retained history with a restore requirement.
19. Archive is available through a conversation row's overflow menu rather than a permanent conversation action. Archived conversations is reached through the list's overflow menu, including when the active list is empty. Both menus support keyboard and touch access.

## Verification approach

Use focused API/service tests for migration preservation, archive/restore idempotency, owner checks, final-active-project concurrency, archived write rejection, read access, and DELETE compatibility semantics. Exercise an admitted run crossing the archive transition and confirm it completes without permitting a new turn. Cover both conversation-list APIs, archive/restore idempotency and owner isolation, direct archived-conversation reads, execution/file mutation rejection, parent-project lifecycle combinations, and concurrent archive versus run admission. Assert unchanged conversation/history/artifact/workspace records and resource references; archive/restore must not invoke provider, Terraform, or resource-cleanup paths. Add frontend tests for project switching, active/archived project and conversation lists, the archived conversation read-only view and restore flow, archive/restore styling, and connector dialog action placement/labels/status. Run the relevant API and web test suites and the repository's existing type/lint checks for touched areas.
