"""Resumable apply and verification for the drained messaging cutover."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ..runs.models import Event, Outbox, Run, RunJob
from .migration import (
    _EVENT_ID_PREVIEW_LIMIT,
    _RUN_SCAN_BATCH_SIZE,
    CutoverReport,
    _classify_runs,
    _exact_event_notice,
    plan_cutover,
)
from .models import (
    MessageDisposition,
    MessageExhaustion,
    MessageTransportState,
    MessagingCutover,
    RetentionTask,
)
from .outbox import enqueue_event, enqueue_run

_MAX_DISPATCH_GENERATION = 2_147_483_647


@dataclass(frozen=True)
class CutoverApplyReport:
    cutover_id: uuid.UUID
    phase: str
    events_scanned: int
    events_backfilled: int
    runs_scanned: int
    commands_created: int
    commands_reused: int

    @property
    def status(self) -> str:
        return "applied"

    @property
    def gates_remain_closed(self) -> bool:
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "cutover_id": str(self.cutover_id),
            "read_only": False,
            "status": self.status,
            "gates_remain_closed": self.gates_remain_closed,
        }


@dataclass(frozen=True)
class CutoverVerificationReport:
    cutover_id: uuid.UUID
    ready: bool
    errors: tuple[str, ...]
    pending_publications: int
    preview: CutoverReport

    @property
    def status(self) -> str:
        return "verified" if self.ready else "not_ready"

    def as_dict(self) -> dict[str, object]:
        return {
            "cutover_id": str(self.cutover_id),
            "read_only": True,
            "status": self.status,
            "ready": self.ready,
            "errors": list(self.errors),
            "pending_publications": self.pending_publications,
            "preview": self.preview.as_dict(),
        }


@dataclass(frozen=True)
class CutoverGateReport:
    cutover_id: uuid.UUID
    gate: str
    status: str

    def as_dict(self) -> dict[str, object]:
        return {
            "cutover_id": str(self.cutover_id),
            "gate": self.gate,
            "status": self.status,
            "read_only": False,
        }


@dataclass(frozen=True)
class CutoverRollbackReport:
    cutover_id: uuid.UUID
    ready: bool
    errors: tuple[str, ...]
    phase: str | None
    rollback_epoch: uuid.UUID | None
    run_disposition_counts: dict[str, int]
    blocked_run_groups: int
    blocked_run_ids: tuple[str, ...]
    blocked_run_ids_truncated: bool
    event_rows: int
    event_notification_rows: int
    missing_event_notification_rows: int
    event_identity_conflicts: int
    orphan_event_references: int
    event_issue_ids: tuple[str, ...]
    event_issue_ids_truncated: bool
    pending_command_publications: int
    pending_maintenance_publications: int
    active_retention_tasks: int
    claimed_dispositions: int
    unresolved_exhaustions: int
    active_publisher_leases: int
    events_scanned: int = 0
    events_requeued: int = 0
    runs_scanned: int = 0
    generations_bumped: int = 0
    generation_exhausted_runs: int = 0
    read_only: bool = True

    @property
    def status(self) -> str:
        if self.errors:
            return "blocked"
        if self.phase == "rolled_back":
            return "rolled_back"
        if self.phase in {"rollback_events", "rollback_runs"}:
            return "resumable"
        return "preview"

    def as_dict(self) -> dict[str, object]:
        return {
            "cutover_id": str(self.cutover_id),
            "read_only": self.read_only,
            "status": self.status,
            "ready": self.ready,
            "errors": list(self.errors),
            "phase": self.phase,
            "rollback_epoch": (
                str(self.rollback_epoch) if self.rollback_epoch is not None else None
            ),
            "run_disposition_counts": self.run_disposition_counts,
            "blocked_run_groups": self.blocked_run_groups,
            "blocked_run_ids": list(self.blocked_run_ids),
            "blocked_run_ids_truncated": self.blocked_run_ids_truncated,
            "event_rows": self.event_rows,
            "event_notification_rows": self.event_notification_rows,
            "missing_event_notification_rows": self.missing_event_notification_rows,
            "event_identity_conflicts": self.event_identity_conflicts,
            "orphan_event_references": self.orphan_event_references,
            "event_issue_ids": list(self.event_issue_ids),
            "event_issue_ids_truncated": self.event_issue_ids_truncated,
            "pending_command_publications": self.pending_command_publications,
            "pending_maintenance_publications": self.pending_maintenance_publications,
            "active_retention_tasks": self.active_retention_tasks,
            "claimed_dispositions": self.claimed_dispositions,
            "unresolved_exhaustions": self.unresolved_exhaustions,
            "active_publisher_leases": self.active_publisher_leases,
            "generation_exhausted_runs": self.generation_exhausted_runs,
            "events_scanned": self.events_scanned,
            "events_requeued": self.events_requeued,
            "runs_scanned": self.runs_scanned,
            "generations_bumped": self.generations_bumped,
        }


def apply_cutover(
    db: Session,
    *,
    cutover_id: uuid.UUID,
    operator: str,
    batch_size: int = 500,
    legacy_writers_stopped: bool = False,
) -> CutoverApplyReport:
    """Backfill event notices and eligible run commands in resumable batches."""
    if not isinstance(cutover_id, uuid.UUID):
        raise TypeError("cutover_id must be a UUID")
    if (
        not isinstance(operator, str)
        or not operator.strip()
        or len(operator.strip()) > 255
    ):
        raise ValueError(
            "a non-empty operator identity of at most 255 characters is required"
        )
    operator = operator.strip()
    if type(batch_size) is not int or not 1 <= batch_size <= _RUN_SCAN_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {_RUN_SCAN_BATCH_SIZE}")
    if legacy_writers_stopped is not True:
        raise ValueError("legacy writer shutdown must be attested")

    initial = plan_cutover(db, cutover_id=cutover_id)
    if initial.schema_stage != "phase6a":
        raise RuntimeError("cutover apply requires the expanded Phase 6A schema")
    if initial.status == "blocked":
        raise RuntimeError("cutover preview is blocked; reconcile before apply")
    tables = set(inspect(db.get_bind()).get_table_names())
    if not {"messaging_cutovers", "retention_tasks"}.issubset(tables):
        raise RuntimeError("cutover progress or retention schema is missing")
    db.commit()

    now = datetime.now(UTC)
    with db.begin():
        state = (
            db.query(MessageTransportState)
            .filter(MessageTransportState.id == 1)
            .with_for_update()
            .one_or_none()
        )
        if state is None:
            raise RuntimeError("cutover messaging state is missing")
        if state.cutover_id not in (None, cutover_id):
            previous = (
                db.query(MessagingCutover)
                .filter(MessagingCutover.cutover_id == state.cutover_id)
                .with_for_update()
                .one_or_none()
            )
            if not (
                previous is not None
                and previous.status == "rolled_back"
                and previous.rollback_epoch == state.transport_epoch
                and state.active is False
                and state.admission_open is False
                and state.execution_open is False
            ):
                raise RuntimeError("another cutover owns the messaging state")
        progress = (
            db.query(MessagingCutover)
            .filter(MessagingCutover.cutover_id == cutover_id)
            .with_for_update()
            .one_or_none()
        )
        if progress is not None and progress.status == "rolled_back":
            raise RuntimeError("a rolled-back cutover requires a new cutover ID")
        if progress is not None and (
            progress.installation_id != state.installation_id
            or progress.transport_epoch != state.transport_epoch
        ):
            raise RuntimeError("cutover identity does not match persisted transport")
        if (
            progress is not None
            and progress.status == "complete"
            and (
                progress.admission_opened_at is not None
                or progress.execution_opened_at is not None
            )
        ):
            raise RuntimeError(
                "completed cutover gates were opened; refusing to reapply"
            )
        active_retention = (
            db.query(RetentionTask.id)
            .filter(RetentionTask.state.in_(("requested", "running")))
            .count()
        )
        if active_retention:
            raise RuntimeError("active retention tasks must finish before cutover")
        state.active = True
        if progress is None or progress.status != "complete":
            state.admission_open = False
            state.execution_open = False
        state.cutover_id = cutover_id
        if progress is None:
            progress = MessagingCutover(
                cutover_id=cutover_id,
                installation_id=state.installation_id,
                transport_epoch=state.transport_epoch,
                requested_by=operator,
                last_operator=operator,
            )
            db.add(progress)
        else:
            progress.last_operator = operator
        progress.updated_at = now

    closed_gate_preview = plan_cutover(db, cutover_id=cutover_id)
    db.commit()
    if closed_gate_preview.status == "blocked":
        raise RuntimeError("closed-gate cutover recheck is blocked; reconcile first")

    while True:
        progress = db.get(MessagingCutover, cutover_id)
        phase = progress.phase if progress is not None else None
        progress_status = progress.status if progress is not None else None
        db.commit()
        if progress is None:
            raise RuntimeError("cutover progress row is missing")
        if phase == "events":
            with db.begin():
                state = (
                    db.query(MessageTransportState)
                    .filter(MessageTransportState.id == 1)
                    .with_for_update()
                    .one()
                )
                progress = (
                    db.query(MessagingCutover)
                    .filter(MessagingCutover.cutover_id == cutover_id)
                    .with_for_update()
                    .one()
                )
                _assert_cutover_gates(state, progress)
                if progress.phase != "events":
                    continue
                event_query = db.query(
                    Event.id,
                    Event.conversation_id,
                    Event.run_id,
                    Event.type,
                )
                if progress.last_event_id is not None:
                    event_query = event_query.filter(Event.id > progress.last_event_id)
                event_query = event_query.order_by(Event.id).limit(batch_size)
                events = [
                    SimpleNamespace(**row._mapping)
                    for row in event_query.with_for_update().all()
                ]
                if not events:
                    progress.phase = "runs"
                    progress.last_event_id = None
                else:
                    event_ids = [event.id for event in events]
                    notice_query = db.query(
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
                    ).filter(
                        Outbox.destination == "event",
                        Outbox.event_id.in_(event_ids),
                    )
                    notices_by_event: defaultdict[uuid.UUID, list[SimpleNamespace]] = (
                        defaultdict(list)
                    )
                    for row in notice_query.with_for_update().all():
                        notice = SimpleNamespace(**row._mapping)
                        notices_by_event[notice.event_id].append(notice)

                    backfilled = 0
                    for event in events:
                        notices = notices_by_event[event.id]
                        if notices and not _exact_event_notice(
                            event=event,
                            notices=notices,
                            state=state,
                        ):
                            raise RuntimeError(
                                "event notice identity changed during apply"
                            )
                        if not notices:
                            enqueue_event(db, event)
                            backfilled += 1
                    progress.last_event_id = events[-1].id
                    progress.events_scanned += len(events)
                    progress.events_backfilled += backfilled
                progress.updated_at = datetime.now(UTC)
            continue

        if phase == "runs":
            with db.begin():
                state = (
                    db.query(MessageTransportState)
                    .filter(MessageTransportState.id == 1)
                    .with_for_update()
                    .one()
                )
                progress = (
                    db.query(MessagingCutover)
                    .filter(MessagingCutover.cutover_id == cutover_id)
                    .with_for_update()
                    .one()
                )
                _assert_cutover_gates(state, progress)
                if progress.phase != "runs":
                    continue
                run_query = db.query(Run.id)
                if progress.last_run_id is not None:
                    run_query = run_query.filter(Run.id > progress.last_run_id)
                run_ids = [
                    run_id
                    for (run_id,) in run_query.order_by(Run.id)
                    .limit(batch_size)
                    .with_for_update()
                    .all()
                ]
                if not run_ids:
                    progress.phase = "complete"
                    progress.status = "complete"
                    progress.completed_at = datetime.now(UTC)
                else:
                    locked_jobs = (
                        db.query(RunJob)
                        .filter(RunJob.run_id.in_(run_ids))
                        .with_for_update()
                        .all()
                    )
                    jobs_by_run = {job.run_id: job for job in locked_jobs}
                    per_run_dispositions: dict[uuid.UUID, str] = {}
                    dispositions, blocked_count, _blocked_ids, _truncated = (
                        _classify_runs(
                            db,
                            schema_stage="phase6a",
                            state=state,
                            only_run_ids=run_ids,
                            lock_rows=True,
                            run_dispositions=per_run_dispositions,
                        )
                    )
                    if (
                        blocked_count
                        or set(per_run_dispositions) != set(run_ids)
                        or any(
                            category
                            not in {
                                "queued_eligible",
                                "queued_existing_command",
                                "waiting_preserved",
                                "terminal_preserved",
                                "terminal_owned_preserved",
                            }
                            for category in per_run_dispositions.values()
                        )
                    ):
                        raise RuntimeError(
                            "run batch changed or became unsafe during apply"
                        )
                    commands_created = 0
                    commands_reused = dispositions.get("queued_existing_command", 0)
                    for run_id in run_ids:
                        disposition = per_run_dispositions[run_id]
                        if disposition == "terminal_owned_preserved":
                            # A terminal run cannot execute again. With writers
                            # stopped and the run/job fence plus active tool
                            # state rechecked under lock, its old lease metadata
                            # is stale queue state and can be normalized safely.
                            job = jobs_by_run[run_id]
                            job.owner_id = None
                            job.lease_until = None
                        elif disposition == "queued_eligible":
                            job = jobs_by_run[run_id]
                            enqueue_run(
                                db,
                                run_id,
                                job.dispatch_generation,
                                epoch=state.transport_epoch,
                            )
                            commands_created += 1
                    progress.last_run_id = run_ids[-1]
                    progress.runs_scanned += len(run_ids)
                    progress.commands_created += commands_created
                    progress.commands_reused += commands_reused
                progress.updated_at = datetime.now(UTC)
            continue

        if phase == "complete" and progress_status == "complete":
            break
        raise RuntimeError("cutover progress has an unsupported phase")

    progress = db.get(MessagingCutover, cutover_id)
    return CutoverApplyReport(
        cutover_id=cutover_id,
        phase=progress.phase,
        events_scanned=progress.events_scanned,
        events_backfilled=progress.events_backfilled,
        runs_scanned=progress.runs_scanned,
        commands_created=progress.commands_created,
        commands_reused=progress.commands_reused,
    )


def plan_rollback(db: Session, *, cutover_id: uuid.UUID) -> CutoverRollbackReport:
    """Preview whether the completed cutover can safely return to DB polling."""
    if not isinstance(cutover_id, uuid.UUID):
        raise TypeError("cutover_id must be a UUID")
    tables = set(inspect(db.get_bind()).get_table_names())
    required = {
        "messaging_cutovers",
        "messaging_state",
        "message_dispositions",
        "message_exhaustions",
        "retention_tasks",
    }
    missing = required - tables
    if missing:
        return _empty_rollback_report(
            cutover_id, ("rollback_schema_missing",), phase=None
        )

    state = db.get(MessageTransportState, 1)
    progress = db.get(MessagingCutover, cutover_id)
    if state is None or progress is None:
        return _empty_rollback_report(
            cutover_id, ("rollback_state_missing",), phase=None
        )

    errors: list[str] = []
    if progress.installation_id != state.installation_id:
        errors.append("installation_identity_changed")
    allowed_event_epochs = {progress.transport_epoch}
    run_epoch = progress.transport_epoch
    if progress.status == "complete":
        if progress.phase != "complete" or progress.completed_at is None:
            errors.append("cutover_apply_incomplete")
        if (
            state.cutover_id != cutover_id
            or state.transport_epoch != progress.transport_epoch
        ):
            errors.append("cutover_identity_changed")
        if state.active is not True:
            errors.append("transport_not_active")
    elif progress.status == "rolling_back":
        if (
            progress.phase not in {"rollback_events", "rollback_runs"}
            or progress.rollback_epoch is None
        ):
            errors.append("rollback_progress_invalid")
        if (
            state.cutover_id != cutover_id
            or state.transport_epoch != progress.rollback_epoch
            or state.active is not False
        ):
            errors.append("rollback_identity_changed")
        if progress.rollback_epoch is not None:
            allowed_event_epochs.add(progress.rollback_epoch)
    elif progress.status == "rolled_back":
        if progress.phase != "rolled_back" or progress.rollback_epoch is None:
            errors.append("rollback_progress_invalid")
        if progress.rollback_epoch is not None:
            allowed_event_epochs = {progress.rollback_epoch}
            if (
                state.cutover_id != cutover_id
                or state.transport_epoch != progress.rollback_epoch
                or state.active is not False
            ):
                errors.append("rollback_no_longer_current")
    else:
        errors.append("cutover_apply_incomplete")

    if state.admission_open is not False or state.execution_open is not False:
        errors.append("messaging_gates_must_be_closed")

    identity_state = SimpleNamespace(
        installation_id=progress.installation_id,
        transport_epoch=run_epoch,
    )
    run_counts, blocked_count, blocked_ids, blocked_truncated = _classify_runs(
        db,
        schema_stage="phase6a",
        state=identity_state,
    )
    safe_run_dispositions = {
        "queued_eligible",
        "queued_existing_command",
        "waiting_preserved",
        "terminal_preserved",
        "terminal_owned_preserved",
    }
    if blocked_count or any(
        disposition not in safe_run_dispositions for disposition in run_counts
    ):
        errors.append("run_dispositions_unsafe")

    event_inventory = _rollback_event_inventory(
        db,
        installation_id=progress.installation_id,
        allowed_epochs=allowed_event_epochs,
    )
    if event_inventory["event_identity_conflicts"]:
        errors.append("event_identity_conflicts")
    if event_inventory["orphan_event_references"]:
        errors.append("orphan_event_references")

    pending_commands = _count_pending_publications(db, "command")
    pending_maintenance = _count_pending_publications(db, "maintenance")
    active_retention = int(
        db.query(RetentionTask.id)
        .filter(RetentionTask.state.in_(("requested", "running")))
        .count()
    )
    claimed_dispositions = int(
        db.query(MessageDisposition.message_id)
        .filter(MessageDisposition.disposition == "claimed")
        .count()
    )
    unresolved_exhaustions = int(
        db.query(MessageExhaustion.id)
        .filter(MessageExhaustion.state != "resolved")
        .count()
    )
    active_publisher_leases = int(
        db.query(Outbox.id)
        .filter(
            Outbox.publisher_lease_until.is_not(None),
            Outbox.publisher_lease_until > datetime.now(UTC),
        )
        .count()
    )
    if progress.status == "rolled_back":
        generation_exhausted_runs = 0
    else:
        # A rollback cursor and each generation bump commit together. On resume,
        # only rows after that cursor still need a bump; earlier rows may now be
        # at INT_MAX because their required bump already completed.
        generation_query = (
            db.query(RunJob.run_id)
            .join(Run, Run.id == RunJob.run_id)
            .filter(
                Run.status == "queued",
                RunJob.dispatch_generation >= _MAX_DISPATCH_GENERATION,
            )
        )
        if (
            progress.status == "rolling_back"
            and progress.rollback_last_run_id is not None
        ):
            generation_query = generation_query.filter(
                Run.id > progress.rollback_last_run_id
            )
        generation_exhausted_runs = int(generation_query.distinct().count())
    if pending_commands:
        errors.append("command_publications_pending")
    if pending_maintenance:
        errors.append("maintenance_publications_pending")
    if active_retention:
        errors.append("retention_tasks_active")
    if claimed_dispositions:
        errors.append("message_claims_unresolved")
    if unresolved_exhaustions:
        errors.append("message_exhaustions_unresolved")
    if active_publisher_leases:
        errors.append("publisher_leases_active")
    if generation_exhausted_runs:
        errors.append("dispatch_generation_exhausted")

    return CutoverRollbackReport(
        cutover_id=cutover_id,
        ready=not errors,
        errors=tuple(sorted(set(errors))),
        phase=progress.phase,
        rollback_epoch=progress.rollback_epoch,
        run_disposition_counts=run_counts,
        blocked_run_groups=blocked_count,
        blocked_run_ids=blocked_ids,
        blocked_run_ids_truncated=blocked_truncated,
        event_rows=event_inventory["event_rows"],
        event_notification_rows=event_inventory["event_notification_rows"],
        missing_event_notification_rows=event_inventory[
            "missing_event_notification_rows"
        ],
        event_identity_conflicts=event_inventory["event_identity_conflicts"],
        orphan_event_references=event_inventory["orphan_event_references"],
        event_issue_ids=event_inventory["event_issue_ids"],
        event_issue_ids_truncated=event_inventory["event_issue_ids_truncated"],
        pending_command_publications=pending_commands,
        pending_maintenance_publications=pending_maintenance,
        active_retention_tasks=active_retention,
        claimed_dispositions=claimed_dispositions,
        unresolved_exhaustions=unresolved_exhaustions,
        active_publisher_leases=active_publisher_leases,
        generation_exhausted_runs=generation_exhausted_runs,
        events_scanned=progress.rollback_events_scanned,
        events_requeued=progress.rollback_events_requeued,
        runs_scanned=progress.rollback_runs_scanned,
        generations_bumped=progress.rollback_generations_bumped,
    )


def apply_rollback(
    db: Session,
    *,
    cutover_id: uuid.UUID,
    operator: str,
    batch_size: int = _RUN_SCAN_BATCH_SIZE,
    processes_stopped: bool = False,
    consumers_drained: bool = False,
) -> CutoverRollbackReport:
    """Fence JetStream work and prepare a resumable return to DB polling."""
    if not isinstance(cutover_id, uuid.UUID):
        raise TypeError("cutover_id must be a UUID")
    if not isinstance(operator, str) or not 1 <= len(operator.strip()) <= 255:
        raise ValueError(
            "a non-empty operator identity of at most 255 characters is required"
        )
    if type(batch_size) is not int or not 1 <= batch_size <= _RUN_SCAN_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {_RUN_SCAN_BATCH_SIZE}")
    if processes_stopped is not True or consumers_drained is not True:
        raise ValueError("process shutdown and consumer drain must be attested")
    operator = operator.strip()

    initial = plan_rollback(db, cutover_id=cutover_id)
    if initial.status == "rolled_back":
        return replace(initial, read_only=False)
    if not initial.ready:
        raise RuntimeError("rollback preview is blocked; reconcile before apply")
    db.commit()

    with db.begin():
        state = (
            db.query(MessageTransportState)
            .filter(MessageTransportState.id == 1)
            .with_for_update()
            .one_or_none()
        )
        progress = (
            db.query(MessagingCutover)
            .filter(MessagingCutover.cutover_id == cutover_id)
            .with_for_update()
            .one_or_none()
        )
        if state is None or progress is None:
            raise RuntimeError("rollback state is missing")
        fresh = plan_rollback(db, cutover_id=cutover_id)
        if not fresh.ready:
            raise RuntimeError("rollback changed after preview; reconcile before apply")
        if progress.status == "complete":
            rollback_epoch = uuid.uuid4()
            state.transport_epoch = rollback_epoch
            state.active = False
            state.admission_open = False
            state.execution_open = False
            progress.rollback_epoch = rollback_epoch
            progress.status = "rolling_back"
            progress.phase = "rollback_events"
            progress.rollback_last_event_id = None
            progress.rollback_last_run_id = None
            progress.last_operator = operator
            progress.updated_at = datetime.now(UTC)
        elif progress.status == "rolling_back":
            _assert_rollback_identity(state, progress, cutover_id)
            progress.last_operator = operator
            progress.updated_at = datetime.now(UTC)
        elif progress.status == "rolled_back":
            return replace(fresh, read_only=False)
        else:
            raise RuntimeError("cutover is not eligible for rollback")

    while True:
        progress = db.get(MessagingCutover, cutover_id)
        phase = progress.phase if progress is not None else None
        db.commit()
        if progress is None:
            raise RuntimeError("rollback progress row is missing")
        if phase == "rollback_events":
            with db.begin():
                state = (
                    db.query(MessageTransportState)
                    .filter(MessageTransportState.id == 1)
                    .with_for_update()
                    .one()
                )
                progress = (
                    db.query(MessagingCutover)
                    .filter(MessagingCutover.cutover_id == cutover_id)
                    .with_for_update()
                    .one()
                )
                _assert_rollback_identity(state, progress, cutover_id)
                if progress.phase != "rollback_events":
                    continue
                fresh = plan_rollback(db, cutover_id=cutover_id)
                if not fresh.ready:
                    raise RuntimeError("rollback became unsafe during event batches")
                query = db.query(
                    Event.id,
                    Event.conversation_id,
                    Event.run_id,
                    Event.type,
                )
                if progress.rollback_last_event_id is not None:
                    query = query.filter(Event.id > progress.rollback_last_event_id)
                events = [
                    SimpleNamespace(**row._mapping)
                    for row in query.order_by(Event.id)
                    .limit(batch_size)
                    .with_for_update()
                    .all()
                ]
                if not events:
                    progress.phase = "rollback_runs"
                    progress.rollback_last_event_id = None
                else:
                    event_ids = [event.id for event in events]
                    notices_by_event: defaultdict[uuid.UUID, list[Outbox]] = (
                        defaultdict(list)
                    )
                    notices = (
                        db.query(Outbox)
                        .filter(
                            Outbox.destination == "event",
                            Outbox.event_id.in_(event_ids),
                        )
                        .order_by(Outbox.event_id, Outbox.id)
                        .with_for_update()
                        .all()
                    )
                    for notice in notices:
                        notices_by_event[notice.event_id].append(notice)
                    allowed_epochs = {progress.transport_epoch, progress.rollback_epoch}
                    requeued = 0
                    for event in events:
                        event_notices = notices_by_event[event.id]
                        if not event_notices:
                            enqueue_event(db, event)
                        elif (
                            len(event_notices) != 1
                            or event_notices[0].transport_epoch not in allowed_epochs
                            or not _exact_event_notice(
                                event=event,
                                notices=event_notices,
                                state=SimpleNamespace(
                                    installation_id=progress.installation_id,
                                    transport_epoch=event_notices[0].transport_epoch,
                                ),
                            )
                        ):
                            raise RuntimeError("event notice changed during rollback")
                        if event_notices:
                            notice = event_notices[0]
                            notice.transport_epoch = progress.rollback_epoch
                            notice.processed_at = None
                            notice.publisher_claim_id = None
                            notice.publisher_lease_until = None
                            notice.next_attempt_at = datetime.now(UTC)
                            notice.last_error = None
                        requeued += 1
                    progress.rollback_last_event_id = events[-1].id
                    progress.rollback_events_scanned += len(events)
                    progress.rollback_events_requeued += requeued
                progress.updated_at = datetime.now(UTC)
            continue

        if phase == "rollback_runs":
            with db.begin():
                state = (
                    db.query(MessageTransportState)
                    .filter(MessageTransportState.id == 1)
                    .with_for_update()
                    .one()
                )
                progress = (
                    db.query(MessagingCutover)
                    .filter(MessagingCutover.cutover_id == cutover_id)
                    .with_for_update()
                    .one()
                )
                _assert_rollback_identity(state, progress, cutover_id)
                if progress.phase != "rollback_runs":
                    continue
                fresh = plan_rollback(db, cutover_id=cutover_id)
                if not fresh.ready:
                    raise RuntimeError("rollback became unsafe during run batches")
                query = db.query(Run.id)
                if progress.rollback_last_run_id is not None:
                    query = query.filter(Run.id > progress.rollback_last_run_id)
                run_ids = [
                    run_id
                    for (run_id,) in query.order_by(Run.id)
                    .limit(batch_size)
                    .with_for_update()
                    .all()
                ]
                if not run_ids:
                    progress.phase = "rolled_back"
                    progress.status = "rolled_back"
                    progress.rolled_back_at = datetime.now(UTC)
                else:
                    locked_jobs = (
                        db.query(RunJob)
                        .filter(RunJob.run_id.in_(run_ids))
                        .with_for_update()
                        .all()
                    )
                    jobs_by_run = {job.run_id: job for job in locked_jobs}
                    per_run: dict[uuid.UUID, str] = {}
                    # Run command rows remain immutable in their forward epoch;
                    # event notices alone are re-epoched during rollback.
                    run_identity_state = SimpleNamespace(
                        installation_id=progress.installation_id,
                        transport_epoch=progress.transport_epoch,
                    )
                    _counts, blocked, _ids, _truncated = _classify_runs(
                        db,
                        schema_stage="phase6a",
                        state=run_identity_state,
                        only_run_ids=run_ids,
                        lock_rows=True,
                        run_dispositions=per_run,
                    )
                    if (
                        blocked
                        or set(per_run) != set(run_ids)
                        or any(
                            category
                            not in {
                                "queued_eligible",
                                "queued_existing_command",
                                "waiting_preserved",
                                "terminal_preserved",
                                "terminal_owned_preserved",
                            }
                            for category in per_run.values()
                        )
                    ):
                        raise RuntimeError("run batch changed or became unsafe")
                    generations_bumped = 0
                    for run_id in run_ids:
                        if per_run[run_id] == "terminal_owned_preserved":
                            job = jobs_by_run[run_id]
                            job.owner_id = None
                            job.lease_until = None
                            continue
                        if per_run[run_id] not in {
                            "queued_eligible",
                            "queued_existing_command",
                        }:
                            continue
                        job = jobs_by_run[run_id]
                        if job.dispatch_generation >= _MAX_DISPATCH_GENERATION:
                            raise RuntimeError(
                                "queued run dispatch generation is exhausted"
                            )
                        job.dispatch_generation += 1
                        generations_bumped += 1
                    progress.rollback_last_run_id = run_ids[-1]
                    progress.rollback_runs_scanned += len(run_ids)
                    progress.rollback_generations_bumped += generations_bumped
                progress.updated_at = datetime.now(UTC)
            continue

        if phase == "rolled_back":
            break
        raise RuntimeError("rollback progress has an unsupported phase")

    return replace(plan_rollback(db, cutover_id=cutover_id), read_only=False)


def _empty_rollback_report(
    cutover_id: uuid.UUID,
    errors: tuple[str, ...],
    *,
    phase: str | None,
) -> CutoverRollbackReport:
    return CutoverRollbackReport(
        cutover_id=cutover_id,
        ready=False,
        errors=errors,
        phase=phase,
        rollback_epoch=None,
        run_disposition_counts={},
        blocked_run_groups=0,
        blocked_run_ids=(),
        blocked_run_ids_truncated=False,
        event_rows=0,
        event_notification_rows=0,
        missing_event_notification_rows=0,
        event_identity_conflicts=0,
        orphan_event_references=0,
        event_issue_ids=(),
        event_issue_ids_truncated=False,
        pending_command_publications=0,
        pending_maintenance_publications=0,
        active_retention_tasks=0,
        claimed_dispositions=0,
        unresolved_exhaustions=0,
        active_publisher_leases=0,
    )


def _count_pending_publications(db: Session, destination: str) -> int:
    return int(
        db.query(Outbox.id)
        .filter(Outbox.destination == destination, Outbox.processed_at.is_(None))
        .count()
    )


def _rollback_event_inventory(
    db: Session,
    *,
    installation_id: uuid.UUID,
    allowed_epochs: set[uuid.UUID],
) -> dict[str, object]:
    event_rows = int(db.query(Event.id).count())
    notification_rows = int(
        db.query(Outbox.id).filter(Outbox.destination == "event").count()
    )
    missing = 0
    conflicts = 0
    orphan_count = 0
    issue_ids: list[str] = []
    last_event_id = None
    while True:
        query = db.query(
            Event.id,
            Event.conversation_id,
            Event.run_id,
            Event.type,
        )
        if last_event_id is not None:
            query = query.filter(Event.id > last_event_id)
        events = [
            SimpleNamespace(**row._mapping)
            for row in query.order_by(Event.id).limit(_RUN_SCAN_BATCH_SIZE).all()
        ]
        if not events:
            break
        last_event_id = events[-1].id
        event_ids = [event.id for event in events]
        notices_by_event: defaultdict[uuid.UUID, list[SimpleNamespace]] = defaultdict(
            list
        )
        for row in (
            db.query(
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
            .filter(Outbox.destination == "event", Outbox.event_id.in_(event_ids))
            .order_by(Outbox.event_id, Outbox.id)
            .all()
        ):
            notice = SimpleNamespace(**row._mapping)
            notices_by_event[notice.event_id].append(notice)
        for event in events:
            notices = notices_by_event[event.id]
            if not notices:
                missing += 1
                if len(issue_ids) < _EVENT_ID_PREVIEW_LIMIT + 1:
                    issue_ids.append(str(event.id))
                continue
            valid_epoch = (
                len(notices) == 1 and notices[0].transport_epoch in allowed_epochs
            )
            exact = False
            if valid_epoch:
                exact = _exact_event_notice(
                    event=event,
                    notices=notices,
                    state=SimpleNamespace(
                        installation_id=installation_id,
                        transport_epoch=notices[0].transport_epoch,
                    ),
                )
            if not exact:
                conflicts += 1
                if len(issue_ids) < _EVENT_ID_PREVIEW_LIMIT + 1:
                    issue_ids.append(str(event.id))

    last_outbox_id = None
    while True:
        query = db.query(Outbox.id, Outbox.event_id)
        if last_outbox_id is not None:
            query = query.filter(Outbox.id > last_outbox_id)
        rows = (
            query.outerjoin(Event, Outbox.event_id == Event.id)
            .filter(Outbox.destination == "event", Event.id.is_(None))
            .order_by(Outbox.id)
            .limit(_RUN_SCAN_BATCH_SIZE)
            .all()
        )
        if not rows:
            break
        last_outbox_id = rows[-1].id
        orphan_count += len(rows)
        for outbox_id, event_id in rows:
            if len(issue_ids) < _EVENT_ID_PREVIEW_LIMIT + 1:
                issue_ids.append(str(event_id or outbox_id))

    truncated = len(issue_ids) > _EVENT_ID_PREVIEW_LIMIT
    return {
        "event_rows": event_rows,
        "event_notification_rows": notification_rows,
        "missing_event_notification_rows": missing,
        "event_identity_conflicts": conflicts,
        "orphan_event_references": orphan_count,
        "event_issue_ids": tuple(issue_ids[:_EVENT_ID_PREVIEW_LIMIT]),
        "event_issue_ids_truncated": truncated,
    }


def _assert_rollback_identity(
    state: MessageTransportState,
    progress: MessagingCutover,
    cutover_id: uuid.UUID,
) -> None:
    if (
        state.cutover_id != cutover_id
        or state.installation_id != progress.installation_id
        or progress.rollback_epoch is None
        or state.transport_epoch != progress.rollback_epoch
        or state.active is not False
        or state.admission_open is not False
        or state.execution_open is not False
        or progress.status != "rolling_back"
    ):
        raise RuntimeError("rollback state identity or gates changed")


def _assert_cutover_gates(state, progress: MessagingCutover) -> None:
    if (
        state.cutover_id != progress.cutover_id
        or state.installation_id != progress.installation_id
        or state.transport_epoch != progress.transport_epoch
    ):
        raise RuntimeError("cutover state identity changed")
    if (
        state.active is not True
        or state.admission_open is not False
        or state.execution_open is not False
    ):
        raise RuntimeError("cutover requires active transport with closed gates")


def verify_cutover(db: Session, *, cutover_id: uuid.UUID) -> CutoverVerificationReport:
    """Check the completed DB cutover and publication catch-up without writes."""
    report = plan_cutover(db, cutover_id=cutover_id)
    state = db.get(MessageTransportState, 1)
    progress = db.get(MessagingCutover, cutover_id)
    errors: list[str] = []
    if state is None or progress is None:
        errors.append("cutover_state_missing")
    else:
        try:
            _assert_cutover_gates(state, progress)
        except RuntimeError:
            errors.append("cutover_identity_or_gates_invalid")
        if progress.status != "complete" or progress.phase != "complete":
            errors.append("cutover_apply_incomplete")
    if report.status == "blocked":
        errors.append("cutover_preview_blocked")
    if report.missing_event_notification_rows:
        errors.append("event_notifications_missing")
    if report.run_disposition_counts.get("queued_eligible", 0):
        errors.append("eligible_run_commands_missing")
    pending_publications = int(
        db.query(Outbox.id)
        .filter(
            Outbox.destination.in_(("event", "command")),
            Outbox.processed_at.is_(None),
        )
        .count()
    )
    if pending_publications:
        errors.append("publications_pending")
    return CutoverVerificationReport(
        cutover_id=cutover_id,
        ready=not errors,
        errors=tuple(errors),
        pending_publications=pending_publications,
        preview=report,
    )


def open_execution_gate(db: Session, *, cutover_id: uuid.UUID) -> CutoverGateReport:
    """Recheck the verified cutover under the state lock, then enable execution."""
    readiness = verify_cutover(db, cutover_id=cutover_id)
    db.commit()
    if not readiness.ready:
        raise RuntimeError("cutover is not verified; execution gate remains closed")

    with db.begin():
        state = (
            db.query(MessageTransportState)
            .filter(MessageTransportState.id == 1)
            .with_for_update()
            .one_or_none()
        )
        progress = (
            db.query(MessagingCutover)
            .filter(MessagingCutover.cutover_id == cutover_id)
            .with_for_update()
            .one_or_none()
        )
        if state is None or progress is None:
            raise RuntimeError("cutover state is missing")
        _assert_cutover_gates(state, progress)
        current_readiness = verify_cutover(db, cutover_id=cutover_id)
        if not current_readiness.ready:
            raise RuntimeError("cutover changed during gate verification")
        state.execution_open = True
        progress.execution_opened_at = datetime.now(UTC)
        progress.updated_at = datetime.now(UTC)
    return CutoverGateReport(cutover_id, "execution", "opened")


def open_admission_gate(db: Session, *, cutover_id: uuid.UUID) -> CutoverGateReport:
    """Open new-work admission only after execution was explicitly opened."""
    with db.begin():
        state = (
            db.query(MessageTransportState)
            .filter(MessageTransportState.id == 1)
            .with_for_update()
            .one_or_none()
        )
        progress = (
            db.query(MessagingCutover)
            .filter(MessagingCutover.cutover_id == cutover_id)
            .with_for_update()
            .one_or_none()
        )
        if state is None or progress is None:
            raise RuntimeError("cutover state is missing")
        if (
            state.cutover_id != cutover_id
            or state.installation_id != progress.installation_id
            or state.transport_epoch != progress.transport_epoch
            or state.active is not True
            or progress.status != "complete"
            or progress.phase != "complete"
            or progress.execution_opened_at is None
        ):
            raise RuntimeError("execution gate has not been verified and opened")
        if state.admission_open is True:
            return CutoverGateReport(cutover_id, "admission", "already_open")
        if state.admission_open is not False or state.execution_open is not True:
            raise RuntimeError("gate state is inconsistent; admission remains closed")
        state.admission_open = True
        progress.admission_opened_at = datetime.now(UTC)
        progress.updated_at = datetime.now(UTC)
    return CutoverGateReport(cutover_id, "admission", "opened")


def close_admission_gate(db: Session, *, cutover_id: uuid.UUID) -> CutoverGateReport:
    """Stop new work before pausing execution or preparing a rollback."""
    with db.begin():
        state = (
            db.query(MessageTransportState)
            .filter(MessageTransportState.id == 1)
            .with_for_update()
            .one_or_none()
        )
        progress = (
            db.query(MessagingCutover)
            .filter(MessagingCutover.cutover_id == cutover_id)
            .with_for_update()
            .one_or_none()
        )
        if state is None or progress is None:
            raise RuntimeError("cutover state is missing")
        _assert_cutover_identity(state, progress, cutover_id)
        if progress.status != "complete" or progress.phase != "complete":
            raise RuntimeError("only a completed cutover can close admission")
        if state.admission_open is False:
            return CutoverGateReport(cutover_id, "admission", "already_closed")
        if state.admission_open is not True:
            raise RuntimeError("admission gate state is invalid")
        state.admission_open = False
        progress.admission_closed_at = datetime.now(UTC)
        progress.updated_at = datetime.now(UTC)
    return CutoverGateReport(cutover_id, "admission", "closed")


def close_execution_gate(db: Session, *, cutover_id: uuid.UUID) -> CutoverGateReport:
    """Stop new execution claims after admission has been closed."""
    with db.begin():
        state = (
            db.query(MessageTransportState)
            .filter(MessageTransportState.id == 1)
            .with_for_update()
            .one_or_none()
        )
        progress = (
            db.query(MessagingCutover)
            .filter(MessagingCutover.cutover_id == cutover_id)
            .with_for_update()
            .one_or_none()
        )
        if state is None or progress is None:
            raise RuntimeError("cutover state is missing")
        _assert_cutover_identity(state, progress, cutover_id)
        if progress.status != "complete" or progress.phase != "complete":
            raise RuntimeError("only a completed cutover can close execution")
        if state.execution_open is False:
            return CutoverGateReport(cutover_id, "execution", "already_closed")
        if state.execution_open is not True or state.admission_open is not False:
            raise RuntimeError("close admission before execution")
        state.execution_open = False
        progress.execution_closed_at = datetime.now(UTC)
        progress.updated_at = datetime.now(UTC)
    return CutoverGateReport(cutover_id, "execution", "closed")


def _assert_cutover_identity(
    state: MessageTransportState,
    progress: MessagingCutover,
    cutover_id: uuid.UUID,
) -> None:
    if (
        state.cutover_id != cutover_id
        or state.installation_id != progress.installation_id
        or state.transport_epoch != progress.transport_epoch
    ):
        raise RuntimeError("cutover state identity changed")
