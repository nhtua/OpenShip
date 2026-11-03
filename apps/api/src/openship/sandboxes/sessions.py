"""On-demand selection of the workflow-scoped sandbox handle for a root run."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..chat.models import Conversation
from ..runs.models import Run, RunJob
from ..runs.queue import Claim
from .contracts import RuntimeRequirements, SandboxHandle
from .models import WorkflowSandbox


class SandboxClaimUncertain(Exception):
    """The durable run claim backing this selection is no longer current."""


class SandboxCreationUncertain(Exception):
    """A sandbox create may already have been sent; never replay it automatically."""


def _utc(value):
    """Normalize DB datetimes (SQLite returns strings) to UTC-aware values."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _current_claim(db: Session, claim: Claim) -> Run | None:
    """Lock conversation → run → job and confirm this claim still owns them."""
    identity = db.query(Run.conversation_id).filter(Run.id == claim.run_id).first()
    if identity is None:
        return None
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == identity.conversation_id)
        .with_for_update()
        .first()
    )
    run = (
        db.query(Run)
        .filter(Run.id == claim.run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    job = (
        db.query(RunJob)
        .filter(RunJob.run_id == claim.run_id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if (
        conversation is None
        or run is None
        or job is None
        or run.status != "running"
        or run.fence != claim.fence
        or job.fence != claim.fence
        or job.owner_id != claim.worker_id
        or job.lease_until is None
        or claim.lease_until is None
        or _utc(job.lease_until) <= _now()
        or _utc(claim.lease_until) <= _now()
    ):
        db.rollback()
        return None
    return run


def _pointer(db: Session, claim: Claim):
    """Return this root run's saved pointer row, locking it when present."""
    return (
        db.query(WorkflowSandbox)
        .filter(WorkflowSandbox.root_run_id == claim.run_id)
        .with_for_update()
        .first()
    )


async def get_or_create_sandbox(
    session_factory,
    client,
    *,
    claim: Claim,
    root_run_id: uuid.UUID,
    requirements: RuntimeRequirements,
) -> SandboxHandle:
    """Select or create the reusable sandbox for one authorized root run.

    Provider round-trips (inspect/create) always run with no open DB
    transaction. A live compatible pointer is probed and reused; a definitive
    absence (or an incompatible runtime identity) records the request intent,
    then creates on demand. An unreachable or ambiguous provider result never
    creates. A returned ID is saved only while the same claim is still current.
    """
    if root_run_id != claim.run_id:
        raise ValueError("the sandbox scope must match the claimed root run")

    probe = None
    with session_factory() as db:
        run = _current_claim(db, claim)
        row = _pointer(db, claim)
        if row is not None and row.status == "requested":
            db.rollback()
            raise SandboxCreationUncertain("a sandbox create may already have been sent")
        if run is None:
            raise SandboxClaimUncertain("the run claim is no longer current")
        if (
            row is not None
            and row.status == "active"
            and row.requirements_key == requirements.key
            and row.sandbox_id is not None
            and row.expires_at is not None
        ):
            probe = SandboxHandle(
                provider_id=row.provider_id,
                sandbox_id=row.sandbox_id,
                requirements_key=row.requirements_key,
                expires_at=_utc(row.expires_at),
            )
        else:
            _record_request_intent(db, run, claim, requirements, row)

    if probe is not None:
        probed = await client.inspect(probe)
        if probed is not None:
            with session_factory() as db:
                run = _current_claim(db, claim)
                row = _pointer(db, claim)
                if run is None:
                    raise SandboxClaimUncertain("the run claim is no longer current")
                if (
                    row is None
                    or row.status != "active"
                    or row.sandbox_id != probed.sandbox_id
                    or row.requirements_key != probed.requirements_key
                ):
                    db.rollback()
                    raise SandboxClaimUncertain("the sandbox probe acknowledgement is uncertain")
                row.expires_at = probed.expires_at
                row.fence = claim.fence
                db.commit()
            return probed
        with session_factory() as db:
            run = _current_claim(db, claim)
            row = _pointer(db, claim)
            if run is None:
                raise SandboxClaimUncertain("the run claim is no longer current")
            if row is None or row.status != "active" or row.sandbox_id != probe.sandbox_id:
                db.rollback()
                raise SandboxClaimUncertain("the sandbox absence acknowledgement is uncertain")
            _record_request_intent(db, run, claim, requirements, row)

    created = await client.create(requirements)
    with session_factory() as db:
        run = _current_claim(db, claim)
        row = _pointer(db, claim)
        if (
            run is None
            or row is None
            or row.status != "requested"
            or row.fence != claim.fence
            or row.requirements_key != requirements.key
            or created.requirements_key != requirements.key
            or not created.sandbox_id
        ):
            db.rollback()
            raise SandboxClaimUncertain("the sandbox create acknowledgement is uncertain")
        row.status = "active"
        row.sandbox_id = created.sandbox_id
        row.expires_at = created.expires_at
        row.fence = claim.fence
        db.commit()
    return created


def _record_request_intent(db: Session, run: Run, claim: Claim,
                           requirements: RuntimeRequirements, row) -> None:
    """Commit that this claim is about to ask the provider for a sandbox."""
    if row is None:
        db.add(
            WorkflowSandbox(
                root_run_id=claim.run_id,
                project_id=run.project_id,
                conversation_id=run.conversation_id,
                provider_id=requirements.provider_id,
                requirements_key=requirements.key,
                fence=claim.fence,
                status="requested",
            )
        )
    else:
        row.provider_id = requirements.provider_id
        row.requirements_key = requirements.key
        row.fence = claim.fence
        row.status = "requested"
        row.sandbox_id = None
        row.expires_at = None
    db.commit()
