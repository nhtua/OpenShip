"""Read-only preflight reports for the drained messaging cutover."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from types import SimpleNamespace

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from ..execution.models import ToolAttempt, ToolOperation
from ..runs.models import Event, Outbox, ProviderAttempt, Run, RunJob
from .models import MessageDisposition, MessageTransportState
from .relay import _record_envelope

_REQUIRED_CUTOVER_TABLES = {
    "runs",
    "run_jobs",
    "events",
    "outbox",
    "provider_attempts",
    "tool_operations",
    "tool_attempts",
}
_PHASE6A_TABLES = {"messaging_state", "message_dispositions"}
_DUPLICATE_ID_PREVIEW_LIMIT = 100
_RUN_BLOCKER_PREVIEW_LIMIT = 100
_EVENT_ID_PREVIEW_LIMIT = 100
_RUN_SCAN_BATCH_SIZE = 500
_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "timed_out", "stale"}
_SAFE_EXISTING_HOLDS = {"transport_inactive", "execution_closed"}


@dataclass(frozen=True)
class CutoverReport:
    """Content-free inventory of queue rows and their migration disposition."""

    cutover_id: uuid.UUID
    schema_stage: str
    run_counts: dict[str, int]
    run_disposition_counts: dict[str, int]
    blocked_run_groups: int
    blocked_run_ids: tuple[str, ...]
    blocked_run_ids_truncated: bool
    job_rows: int
    duplicate_job_groups: int
    duplicate_job_run_ids: tuple[str, ...]
    duplicate_ids_truncated: bool
    event_rows: int
    event_notification_rows: int
    missing_event_notification_rows: int
    missing_event_ids: tuple[str, ...]
    missing_event_ids_truncated: bool
    event_identity_conflicts: int
    orphan_event_references: int
    event_issue_ids: tuple[str, ...]
    event_issue_ids_truncated: bool
    outbox_rows: int

    @property
    def read_only(self) -> bool:
        return True

    @property
    def status(self) -> str:
        return (
            "blocked"
            if (
                self.blocked_run_groups
                or self.duplicate_job_groups
                or self.event_identity_conflicts
                or self.orphan_event_references
            )
            else "preview"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "cutover_id": str(self.cutover_id),
            "read_only": self.read_only,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True)


def _exact_current_command(
    *,
    run,
    job,
    rows: list[SimpleNamespace],
    state,
    dispositions: dict[uuid.UUID, SimpleNamespace],
) -> bool:
    """Accept no row or one exact current identity with a safe closed-gate hold."""
    epoch = state.transport_epoch
    message_id = uuid.uuid5(
        state.installation_id,
        f"run.execute:{run.id}:{job.dispatch_generation}:{epoch}",
    )
    if not rows:
        return message_id not in dispositions
    if len(rows) != 1:
        return False

    row = rows[0]
    expected_payload = json.dumps(
        {
            "run_id": str(run.id),
            "dispatch_generation": job.dispatch_generation,
            "transport_epoch": str(epoch),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    if (
        row.destination != "command"
        or row.type != "run.execute"
        or row.schema_version != 1
        or row.run_id != run.id
        or row.conversation_id != run.conversation_id
        or row.event_id is not None
        or row.dispatch_generation != job.dispatch_generation
        or row.transport_epoch != epoch
        or row.message_id != message_id
        or row.subject != f"openship.{state.installation_id.hex}.v1.command.run.execute"
        or row.payload != expected_payload
    ):
        return False

    disposition = dispositions.get(message_id)
    if disposition is None:
        return True
    if (
        disposition.disposition != "deferred"
        or disposition.error_code not in _SAFE_EXISTING_HOLDS
    ):
        return False
    try:
        envelope = _record_envelope(
            row,
            state,
            uuid.UUID(int=0),
            event=None,
            run=run,
            job=job,
        )
    except (TypeError, ValueError):
        return False
    return disposition.payload_digest == hashlib.sha256(envelope.body).hexdigest()


def _classify_runs(
    db: Session,
    *,
    schema_stage: str,
    state,
    only_run_ids: list[uuid.UUID] | None = None,
    lock_rows: bool = False,
    run_dispositions: dict[uuid.UUID, str] | None = None,
) -> tuple[dict[str, int], int, tuple[str, ...], bool]:
    counts: defaultdict[str, int] = defaultdict(int)
    blocked_ids: list[str] = []
    blocked_count = 0
    last_run_id = None
    while True:
        run_query = db.query(
            Run.id,
            Run.status,
            Run.fence,
            Run.kind,
            Run.graph_version,
            Run.provider_started_at,
            Run.conversation_id,
            Run.project_id,
        )
        if last_run_id is not None:
            run_query = run_query.filter(Run.id > last_run_id)
        if only_run_ids is not None:
            run_query = run_query.filter(Run.id.in_(only_run_ids))
        run_query = run_query.order_by(Run.id).limit(_RUN_SCAN_BATCH_SIZE)
        if lock_rows:
            run_query = run_query.with_for_update()
        runs = [SimpleNamespace(**row._mapping) for row in run_query.all()]
        if not runs:
            break
        last_run_id = runs[-1].id
        run_ids = [run.id for run in runs]
        jobs_by_run: defaultdict[uuid.UUID, list[SimpleNamespace]] = defaultdict(list)
        job_columns = [
            RunJob.id,
            RunJob.run_id,
            RunJob.fence,
            RunJob.owner_id,
            RunJob.lease_until,
        ]
        if schema_stage == "phase6a":
            job_columns.append(RunJob.dispatch_generation)
        job_query = (
            db.query(*job_columns)
            .filter(RunJob.run_id.in_(run_ids))
            .order_by(RunJob.run_id, RunJob.id)
        )
        if lock_rows:
            job_query = job_query.with_for_update()
        for row in job_query.all():
            job_fields = dict(row._mapping)
            job_fields.setdefault("dispatch_generation", 1)
            job = SimpleNamespace(**job_fields)
            jobs_by_run[job.run_id].append(job)

        provider_attempts = {
            run_id
            for (run_id,) in db.query(ProviderAttempt.run_id)
            .filter(ProviderAttempt.run_id.in_(run_ids))
            .distinct()
            .all()
        }
        operation_runs = {
            run_id
            for (run_id,) in db.query(ToolOperation.run_id)
            .filter(ToolOperation.run_id.in_(run_ids))
            .distinct()
            .all()
        }
        active_operation_runs = {
            run_id
            for (run_id,) in db.query(ToolOperation.run_id)
            .filter(
                ToolOperation.run_id.in_(run_ids),
                ToolOperation.status.in_(("admitted", "dispatching", "running")),
            )
            .distinct()
            .all()
        }
        attempt_runs = {
            run_id
            for (run_id,) in db.query(ToolOperation.run_id)
            .join(ToolAttempt, ToolAttempt.operation_id == ToolOperation.id)
            .filter(ToolOperation.run_id.in_(run_ids))
            .distinct()
            .all()
        }
        started_attempt_runs = {
            run_id
            for (run_id,) in db.query(ToolOperation.run_id)
            .join(ToolAttempt, ToolAttempt.operation_id == ToolOperation.id)
            .filter(
                ToolOperation.run_id.in_(run_ids),
                ToolAttempt.outcome == "started",
            )
            .distinct()
            .all()
        }

        queued = [
            run
            for run in runs
            if run.status == "queued"
            and len(jobs_by_run[run.id]) == 1
            and jobs_by_run[run.id][0].owner_id is None
            and jobs_by_run[run.id][0].lease_until is None
            and jobs_by_run[run.id][0].fence == run.fence
            and not run.provider_started_at
            and run.id not in provider_attempts
            and run.id not in operation_runs
            and run.id not in attempt_runs
        ]
        current_commands: defaultdict[uuid.UUID, list[SimpleNamespace]] = defaultdict(
            list
        )
        dispositions: dict[uuid.UUID, SimpleNamespace] = {}
        if schema_stage == "phase6a" and queued:
            queued_ids = [run.id for run in queued]
            current_keys = {
                (run.id, jobs_by_run[run.id][0].dispatch_generation) for run in queued
            }
            expected_message_ids = {
                uuid.uuid5(
                    state.installation_id,
                    f"run.execute:{run.id}:{jobs_by_run[run.id][0].dispatch_generation}:{state.transport_epoch}",
                )
                for run in queued
            }
            command_query = db.query(
                Outbox.id,
                Outbox.conversation_id,
                Outbox.event_id,
                Outbox.type,
                Outbox.payload,
                Outbox.created_at,
                Outbox.destination,
                Outbox.schema_version,
                Outbox.message_id,
                Outbox.subject,
                Outbox.run_id,
                Outbox.dispatch_generation,
                Outbox.transport_epoch,
                Outbox.failure_id,
                Outbox.maintenance_task_id,
            ).filter(Outbox.run_id.in_(queued_ids))
            if lock_rows:
                command_query = command_query.with_for_update()
            for row in command_query.all():
                row = SimpleNamespace(**row._mapping)
                if (row.run_id, row.dispatch_generation) in current_keys:
                    current_commands[row.run_id].append(row)
            message_ids = expected_message_ids | {
                row.message_id for rows in current_commands.values() for row in rows
            }
            if message_ids:
                disposition_query = db.query(
                    MessageDisposition.message_id,
                    MessageDisposition.disposition,
                    MessageDisposition.error_code,
                    MessageDisposition.payload_digest,
                ).filter(MessageDisposition.message_id.in_(message_ids))
                if lock_rows:
                    disposition_query = disposition_query.with_for_update()
                dispositions = {
                    disposition.message_id: SimpleNamespace(**disposition._mapping)
                    for disposition in disposition_query.all()
                }

        for run in runs:
            jobs = jobs_by_run[run.id]
            blocker: str | None = None
            if not jobs:
                blocker = "blocked_missing_job"
            elif len(jobs) > 1:
                blocker = "blocked_duplicate_job"
            else:
                job = jobs[0]
                if job.fence != run.fence:
                    blocker = "blocked_fence_mismatch"
                elif run.status in _TERMINAL_RUN_STATUSES:
                    if (
                        run.id in active_operation_runs
                        or run.id in started_attempt_runs
                    ):
                        blocker = "blocked_active_execution"
                    elif job.owner_id is not None or job.lease_until is not None:
                        blocker = "terminal_owned_preserved"
                    else:
                        blocker = "terminal_preserved"
                elif job.owner_id is not None or job.lease_until is not None:
                    blocker = "blocked_owned"
                elif run.status == "queued":
                    if not _supports_run_graph(run):
                        blocker = "blocked_unsupported_graph"
                    elif (
                        run.provider_started_at is not None
                        or run.id in provider_attempts
                        or run.id in operation_runs
                        or run.id in attempt_runs
                    ):
                        blocker = "blocked_dispatch_history"
                    elif schema_stage == "phase6a" and not _exact_current_command(
                        run=run,
                        job=job,
                        rows=current_commands[run.id],
                        state=state,
                        dispositions=dispositions,
                    ):
                        blocker = "blocked_command_identity"
                    else:
                        blocker = (
                            "queued_existing_command"
                            if current_commands[run.id]
                            else "queued_eligible"
                        )
                elif run.status == "waiting_connector":
                    if (
                        run.id in active_operation_runs
                        or run.id in started_attempt_runs
                    ):
                        blocker = "blocked_active_execution"
                    else:
                        blocker = "waiting_preserved"
                elif run.status in {"created", "running"}:
                    blocker = "requires_recovery"
                else:
                    blocker = "unsupported_status"

            counts[blocker] += 1
            if run_dispositions is not None:
                run_dispositions[run.id] = blocker
            if blocker.startswith("blocked_") or blocker in {
                "requires_recovery",
                "unsupported_status",
            }:
                blocked_count += 1
                if len(blocked_ids) < _RUN_BLOCKER_PREVIEW_LIMIT:
                    blocked_ids.append(str(run.id))

        if only_run_ids is not None:
            break

    return (
        dict(sorted(counts.items())),
        blocked_count,
        tuple(blocked_ids),
        blocked_count > len(blocked_ids),
    )


def _supports_run_graph(run: SimpleNamespace) -> bool:
    """Use the worker's exact graph registry for queued replay eligibility."""
    from ..runs.dispatch import UnsupportedGraphVersion, resolve_graph

    try:
        resolve_graph(run.kind, run.graph_version)
    except UnsupportedGraphVersion:
        return False
    return True


def _exact_event_notice(
    *,
    event: SimpleNamespace,
    notices: list[SimpleNamespace],
    state,
) -> bool:
    if len(notices) != 1:
        return False
    notice = notices[0]
    expected_message_id = uuid.uuid5(state.installation_id, f"event:{event.id}")
    expected_payload = json.dumps(
        {"event_id": str(event.id)},
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_subject = (
        f"openship.{state.installation_id.hex}.v1.event.{event.conversation_id.hex}"
    )
    return (
        notice.destination == "event"
        and notice.event_id == event.id
        and notice.conversation_id == event.conversation_id
        and notice.run_id == event.run_id
        and notice.type == event.type
        and notice.schema_version == 1
        and notice.message_id == expected_message_id
        and notice.subject == expected_subject
        and notice.payload == expected_payload
        and notice.transport_epoch == state.transport_epoch
    )


def _event_coverage(
    db: Session,
    *,
    schema_stage: str,
    state,
    event_rows: int,
) -> tuple[int, int, tuple[str, ...], bool, int, int, tuple[str, ...], bool]:
    """Count retained events lacking exact publishable notices and bad refs."""
    if schema_stage == "phase5":
        missing_ids = [
            str(event_id)
            for (event_id,) in db.query(Event.id)
            .order_by(Event.id)
            .limit(_EVENT_ID_PREVIEW_LIMIT + 1)
        ]
        truncated = len(missing_ids) > _EVENT_ID_PREVIEW_LIMIT
        return (
            0,
            event_rows,
            tuple(missing_ids[:_EVENT_ID_PREVIEW_LIMIT]),
            truncated,
            0,
            0,
            (),
            False,
        )

    notification_rows = int(
        db.query(Outbox.id).filter(Outbox.destination == "event").count()
    )
    missing_count = 0
    conflict_count = 0
    missing_ids: list[str] = []
    issue_ids: list[str] = []
    seen_issue_ids: set[str] = set()

    def remember_issue_id(value: uuid.UUID) -> None:
        value_text = str(value)
        if (
            value_text not in seen_issue_ids
            and len(issue_ids) < _EVENT_ID_PREVIEW_LIMIT + 1
        ):
            seen_issue_ids.add(value_text)
            issue_ids.append(value_text)

    last_event_id = None
    while True:
        event_query = db.query(
            Event.id,
            Event.conversation_id,
            Event.run_id,
            Event.type,
        )
        if last_event_id is not None:
            event_query = event_query.filter(Event.id > last_event_id)
        events = [
            SimpleNamespace(**row._mapping)
            for row in event_query.order_by(Event.id).limit(_RUN_SCAN_BATCH_SIZE).all()
        ]
        if not events:
            break
        last_event_id = events[-1].id
        events_by_id = {event.id: event for event in events}
        notices_by_event: defaultdict[uuid.UUID, list[SimpleNamespace]] = defaultdict(
            list
        )
        notice_columns = (
            Outbox.id,
            Outbox.event_id,
            Outbox.conversation_id,
            Outbox.run_id,
            Outbox.type,
            Outbox.payload,
            Outbox.destination,
            Outbox.schema_version,
            Outbox.message_id,
            Outbox.subject,
            Outbox.transport_epoch,
        )
        for row in (
            db.query(*notice_columns)
            .filter(
                Outbox.destination == "event",
                Outbox.event_id.in_(list(events_by_id)),
            )
            .order_by(Outbox.id)
            .all()
        ):
            notice = SimpleNamespace(**row._mapping)
            notices_by_event[notice.event_id].append(notice)

        for event in events:
            notices = notices_by_event[event.id]
            if not notices:
                missing_count += 1
                if len(missing_ids) < _EVENT_ID_PREVIEW_LIMIT + 1:
                    missing_ids.append(str(event.id))
                continue

            if not _exact_event_notice(event=event, notices=notices, state=state):
                conflict_count += 1
                remember_issue_id(event.id)

    orphan_count = 0
    last_outbox_id = None
    while True:
        orphan_query = db.query(Outbox.id, Outbox.event_id)
        if last_outbox_id is not None:
            orphan_query = orphan_query.filter(Outbox.id > last_outbox_id)
        orphan_rows = (
            orphan_query.outerjoin(Event, Outbox.event_id == Event.id)
            .filter(
                Outbox.destination == "event",
                Event.id.is_(None),
            )
            .order_by(Outbox.id)
            .limit(_RUN_SCAN_BATCH_SIZE)
            .all()
        )
        if not orphan_rows:
            break
        last_outbox_id = orphan_rows[-1].id
        orphan_count += len(orphan_rows)
        for outbox_id, event_id in orphan_rows:
            remember_issue_id(event_id or outbox_id)

    missing_truncated = len(missing_ids) > _EVENT_ID_PREVIEW_LIMIT
    issues_truncated = len(issue_ids) > _EVENT_ID_PREVIEW_LIMIT
    return (
        notification_rows,
        missing_count,
        tuple(missing_ids[:_EVENT_ID_PREVIEW_LIMIT]),
        missing_truncated,
        conflict_count,
        orphan_count,
        tuple(issue_ids[:_EVENT_ID_PREVIEW_LIMIT]),
        issues_truncated,
    )


def plan_cutover(db: Session, *, cutover_id: uuid.UUID) -> CutoverReport:
    """Inventory Phase 5 or Phase 6A records without changing either store."""
    if not isinstance(cutover_id, uuid.UUID):
        raise TypeError("cutover_id must be a UUID")

    inspector = inspect(db.get_bind())
    tables = set(inspector.get_table_names())
    missing = sorted(_REQUIRED_CUTOVER_TABLES - tables)
    if missing:
        raise RuntimeError("cutover schema is missing required tables")

    schema_stage = "phase6a" if "messaging_state" in tables else "phase5"
    state = None
    if schema_stage == "phase6a":
        if not _PHASE6A_TABLES.issubset(tables):
            raise RuntimeError("cutover messaging schema is incomplete")
        state_row = (
            db.query(
                MessageTransportState.installation_id,
                MessageTransportState.transport_epoch,
            )
            .filter(MessageTransportState.id == 1)
            .one_or_none()
        )
        if state_row is None:
            raise RuntimeError("cutover messaging state is missing")
        state = SimpleNamespace(**state_row._mapping)

    run_counts = {
        str(status): int(count)
        for status, count in db.execute(
            text("SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY status")
        ).all()
    }
    (
        run_disposition_counts,
        blocked_run_groups,
        blocked_run_ids,
        blocked_run_ids_truncated,
    ) = _classify_runs(db, schema_stage=schema_stage, state=state)
    job_rows = int(db.execute(text("SELECT COUNT(*) FROM run_jobs")).scalar_one())
    duplicate_job_groups = int(
        db.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "SELECT run_id FROM run_jobs GROUP BY run_id HAVING COUNT(*) > 1"
                ") AS duplicate_jobs"
            )
        ).scalar_one()
    )
    duplicate_job_run_ids = tuple(
        str(uuid.UUID(str(run_id)))
        for (run_id,) in db.execute(
            text(
                "SELECT run_id FROM run_jobs GROUP BY run_id "
                "HAVING COUNT(*) > 1 ORDER BY run_id LIMIT :limit"
            ),
            {"limit": _DUPLICATE_ID_PREVIEW_LIMIT + 1},
        ).all()
    )
    duplicate_ids_truncated = len(duplicate_job_run_ids) > _DUPLICATE_ID_PREVIEW_LIMIT
    if duplicate_ids_truncated:
        duplicate_job_run_ids = duplicate_job_run_ids[:_DUPLICATE_ID_PREVIEW_LIMIT]

    event_rows = int(db.execute(text("SELECT COUNT(*) FROM events")).scalar_one())
    (
        event_notification_rows,
        missing_event_notification_rows,
        missing_event_ids,
        missing_event_ids_truncated,
        event_identity_conflicts,
        orphan_event_references,
        event_issue_ids,
        event_issue_ids_truncated,
    ) = _event_coverage(
        db,
        schema_stage=schema_stage,
        state=state,
        event_rows=event_rows,
    )
    outbox_rows = int(db.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one())

    return CutoverReport(
        cutover_id=cutover_id,
        schema_stage=schema_stage,
        run_counts=run_counts,
        run_disposition_counts=run_disposition_counts,
        blocked_run_groups=blocked_run_groups,
        blocked_run_ids=blocked_run_ids,
        blocked_run_ids_truncated=blocked_run_ids_truncated,
        job_rows=job_rows,
        duplicate_job_groups=duplicate_job_groups,
        duplicate_job_run_ids=duplicate_job_run_ids,
        duplicate_ids_truncated=duplicate_ids_truncated,
        event_rows=event_rows,
        event_notification_rows=event_notification_rows,
        missing_event_notification_rows=missing_event_notification_rows,
        missing_event_ids=missing_event_ids,
        missing_event_ids_truncated=missing_event_ids_truncated,
        event_identity_conflicts=event_identity_conflicts,
        orphan_event_references=orphan_event_references,
        event_issue_ids=event_issue_ids,
        event_issue_ids_truncated=event_issue_ids_truncated,
        outbox_rows=outbox_rows,
    )
