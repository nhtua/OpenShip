"""Safe startup decisions for activating the JetStream transport."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .cutover import CutoverApplyReport, apply_cutover
from .migration import CutoverReport, plan_cutover
from .models import MessageTransportState, MessagingCutover


class StartupCutoverBlocked(RuntimeError):
    """The last read-only startup preview rejected the cutover before apply."""

    def __init__(self, report: StartupReport) -> None:
        self.report = report
        super().__init__("startup cutover preview is blocked; reconcile manually")


@dataclass(frozen=True)
class StartupReport:
    """Read-only view used by the deployment wrapper before starting services."""

    status: str
    cutover_id: uuid.UUID
    transport_active: bool
    admission_open: bool
    execution_open: bool
    progress_status: str | None
    progress_phase: str | None
    previous_cutover_id: uuid.UUID | None
    errors: tuple[str, ...]
    preview: CutoverReport | None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "cutover_id": str(self.cutover_id),
            "transport_active": self.transport_active,
            "admission_open": self.admission_open,
            "execution_open": self.execution_open,
            "progress_status": self.progress_status,
            "progress_phase": self.progress_phase,
            "previous_cutover_id": (
                str(self.previous_cutover_id)
                if self.previous_cutover_id is not None
                else None
            ),
            "errors": list(self.errors),
            "preview": self.preview.as_dict() if self.preview is not None else None,
            "read_only": True,
        }


def startup_cutover_id(db: Session) -> uuid.UUID:
    """Reuse forward progress, otherwise return a stable ID for this installation."""
    state = _transport_state(db)
    progress = (
        db.get(MessagingCutover, state.cutover_id)
        if state.cutover_id is not None
        else None
    )
    if progress is not None and progress.status in {"applying", "complete"}:
        return progress.cutover_id
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"openship:phase6a:startup-cutover:{state.installation_id}",
    )


def startup_report(db: Session) -> StartupReport:
    """Classify the current transport state without changing DB or broker state."""
    state = _transport_state(db)
    previous = state.cutover_id
    progress = db.get(MessagingCutover, previous) if previous is not None else None
    cutover_id = startup_cutover_id(db)
    base = {
        "cutover_id": cutover_id,
        "transport_active": bool(state.active),
        "admission_open": bool(state.admission_open),
        "execution_open": bool(state.execution_open),
        "progress_status": progress.status if progress is not None else None,
        "progress_phase": progress.phase if progress is not None else None,
        "previous_cutover_id": previous if progress is not None else None,
    }

    if progress is not None and progress.status in {"rolling_back", "rolled_back"}:
        return StartupReport(
            status="manual_required",
            errors=("rollback_history_requires_manual_forward_cutover",),
            preview=None,
            **base,
        )

    if progress is not None and progress.status == "applying":
        if state.active is not True:
            return StartupReport(
                status="manual_required",
                errors=("incomplete_cutover_transport_state_mismatch",),
                preview=None,
                **base,
            )
        preview = plan_cutover(db, cutover_id=cutover_id)
        return StartupReport(
            status="blocked" if preview.status == "blocked" else "resume_apply",
            errors=("cutover_preview_blocked",) if preview.status == "blocked" else (),
            preview=preview,
            **base,
        )

    if progress is not None and progress.status == "complete":
        if state.active is not True:
            return StartupReport(
                status="manual_required",
                errors=("completed_cutover_transport_state_mismatch",),
                preview=None,
                **base,
            )
        if state.admission_open is True and state.execution_open is True:
            return StartupReport(
                status="active_ready",
                errors=(),
                preview=None,
                **base,
            )
        preview = plan_cutover(db, cutover_id=cutover_id)
        if preview.status == "blocked":
            return StartupReport(
                status="blocked",
                errors=("cutover_preview_blocked",),
                preview=preview,
                **base,
            )
        return StartupReport(
            status="finish_apply",
            errors=(),
            preview=preview,
            **base,
        )

    if state.active is True:
        return StartupReport(
            status="manual_required",
            errors=("active_transport_cutover_progress_missing",),
            preview=None,
            **base,
        )

    preview = plan_cutover(db, cutover_id=cutover_id)
    blocked = preview.status == "blocked"
    return StartupReport(
        status="blocked" if blocked else "confirmation_required",
        errors=("cutover_preview_blocked",) if blocked else (),
        preview=preview,
        **base,
    )


def apply_startup_cutover(
    db: Session,
    *,
    cutover_id: uuid.UUID,
    writers_stopped: bool,
    development_confirmed: bool,
) -> CutoverApplyReport:
    """Recheck startup state and apply only a clear, explicitly confirmed cutover."""
    if not isinstance(cutover_id, uuid.UUID):
        raise TypeError("cutover_id must be a UUID")
    if development_confirmed is not True:
        raise ValueError("startup cutover automation requires dev confirmation")
    if writers_stopped is not True:
        raise ValueError("legacy writer shutdown must be attested")
    report = startup_report(db)
    if report.cutover_id != cutover_id:
        raise RuntimeError("startup cutover identity changed; refusing apply")
    if report.status == "blocked":
        raise StartupCutoverBlocked(report)
    if report.status not in {"confirmation_required", "resume_apply"}:
        raise RuntimeError(f"startup cutover cannot apply from {report.status}")
    try:
        return apply_cutover(
            db,
            cutover_id=cutover_id,
            operator="dev-startup-automation",
            legacy_writers_stopped=True,
        )
    except RuntimeError as error:
        db.rollback()
        latest = startup_report(db)
        if latest.status == "blocked" and latest.progress_status is None:
            raise StartupCutoverBlocked(latest) from error
        raise


def _transport_state(db: Session) -> MessageTransportState:
    state = db.get(MessageTransportState, 1)
    if state is None:
        raise RuntimeError("messaging state is missing")
    return state
