---
type: runbook
summary: Explain reversible project and conversation archival, recovery, and retained resources.
date: 2026-10-22
status: current
---

# Project and conversation archive operations

Archiving hides an item from active navigation while preserving its identity,
history, files, workspace, and resource associations. Archive and restore
change lifecycle metadata only. They never run Terraform, delete or alter
external resources, detach associations, delete workspace files or state, or
revoke connector credentials.

## Conversations

Use the three-dot menu beside a conversation in the left history list and
choose **Archive**. The conversation disappears from active history. The
conversation history menu at the top of that list contains **Archived
conversations**, including when the active list is empty.

Archived conversations can be opened from that recovery list or through an
existing direct link. Their messages and saved files remain readable, and
files can still be previewed and downloaded. The composer and file deletion
stay disabled until the conversation is restored. Use **Restore conversation**
in the archived conversation view or in the recovery list to continue the same
conversation. Restoring never submits a retained draft or starts a run.

## Projects

Project Settings archives a project after an explicit confirmation. The owner
must keep at least one active project. Archived projects are hidden from the
active project picker and can be restored from the Archived Projects section
of Project Settings. A direct link to an archived project remains available
for review, with edits disabled until restoration.

Project and conversation archive states are independent. Archiving a project
also hides its conversations from active history and makes them read-only.
Restoring the project makes individually active conversations available again,
but does not clear an individual conversation's own archive state. Restore the
project before restoring or continuing any of its conversations.

Existing work admitted before an archive may finish and publish its outputs.
Archive does not cancel work. Users may still inspect history, run status,
outputs, and resources. Restore the relevant project and conversation before
continuing work or performing resource cleanup from the conversation.

## API and migration behavior

`POST /api/projects/{id}/archive` and `/restore` change project lifecycle
state. `POST /api/conversations/{id}/archive` and `/restore` do the same for a
conversation. Active lists omit archived rows by default; archived conversation
lists include conversations archived individually and conversations hidden by
an archived parent project. Owned metadata, history, and snapshots remain
readable for direct links.

The legacy `DELETE /api/projects/{id}` endpoint is retained as an archive
alias. It returns its existing `204` response and never physically deletes the
project.

Migration `phase5a009f` adds nullable archive timestamps without replacing
existing projects, conversations, or related records. Its downgrade refuses
to remove the lifecycle columns while any archived rows exist, so restoring
those rows is required before downgrade. No project or conversation archive
command invokes resource cleanup.
