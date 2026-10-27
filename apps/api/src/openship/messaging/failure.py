"""Content-free failure ledger records and their transactional outbox rows."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..runs.models import Outbox
from .delivery import DeliveryDisposition, PreparedDelivery
from .models import MessageFailure, MessageFailureConflict, MessageTransportState

_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_FAILURE_ACTIONS = frozenset({"quarantine", "reconcile"})
_LIVE_OWNER_CODES = frozenset({"ownership_expired", "ownership_moved"})


@dataclass(frozen=True)
class JetStreamSourceIdentity:
    """Stable source coordinates read from trusted JetStream message metadata."""

    stream: str
    sequence: int
    timestamp: datetime

    def validated(self) -> JetStreamSourceIdentity | None:
        if (
            self.stream != "OS_RUNS"
            or type(self.sequence) is not int
            or self.sequence < 1
            or not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            return None
        return JetStreamSourceIdentity(
            self.stream,
            self.sequence,
            self.timestamp.astimezone(UTC),
        )


@dataclass(frozen=True)
class FailureHandoff:
    """Identifies the durable failure and its one outbox publication row."""

    failure_id: uuid.UUID
    outbox_id: uuid.UUID
    installation_id: uuid.UUID
    source_transport_epoch: uuid.UUID
    source_identity: JetStreamSourceIdentity
    body_digest: str
    classification: str
    error_code: str


def handoff_matches_delivery(
    handoff: FailureHandoff,
    *,
    installation_id: uuid.UUID | None,
    source_identity: JetStreamSourceIdentity | None,
    disposition: DeliveryDisposition,
    body: bytes,
) -> bool:
    source = source_identity.validated() if source_identity is not None else None
    return (
        source is not None
        and installation_id is not None
        and handoff.installation_id == installation_id
        and handoff.source_identity == source
        and handoff.body_digest == hashlib.sha256(body).hexdigest()
        and handoff.classification == disposition.action
        and handoff.error_code == disposition.code
    )


def source_identity_from_message(message) -> JetStreamSourceIdentity | None:
    """Read only validated source metadata; do not infer identity from the body."""
    try:
        metadata = message.metadata
        identity = JetStreamSourceIdentity(
            stream=metadata.stream,
            sequence=metadata.sequence.stream,
            timestamp=metadata.timestamp,
        )
        return identity.validated()
    except Exception:  # noqa: BLE001 - malformed/non-JetStream metadata fails closed
        return None


def _failure_id(installation_id: uuid.UUID, epoch: uuid.UUID, source) -> uuid.UUID:
    return uuid.uuid5(
        installation_id,
        f"failure:v1:{epoch}:{source.stream}:{source.sequence}",
    )


def _outbox_id(installation_id: uuid.UUID, failure_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(installation_id, f"failure.outbox:{failure_id}")


def _message_id(installation_id: uuid.UUID, failure_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(installation_id, f"failure.notice:{failure_id}")


def _same_timestamp(left: datetime, right: datetime) -> bool:
    if left.tzinfo is None:
        left = left.replace(tzinfo=UTC)
    if right.tzinfo is None:
        right = right.replace(tzinfo=UTC)
    return left.astimezone(UTC) == right.astimezone(UTC)


def _handoff_from_rows(failure: MessageFailure, outbox: Outbox) -> FailureHandoff:
    timestamp = failure.source_timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return FailureHandoff(
        failure_id=failure.id,
        outbox_id=outbox.id,
        installation_id=failure.installation_id,
        source_transport_epoch=failure.source_transport_epoch,
        source_identity=JetStreamSourceIdentity(
            stream=failure.source_stream,
            sequence=failure.source_sequence,
            timestamp=timestamp.astimezone(UTC),
        ),
        body_digest=failure.body_digest,
        classification=failure.classification,
        error_code=failure.error_code,
    )


def _identity_query(db: Session, installation_id: uuid.UUID, epoch: uuid.UUID, source):
    return (
        db.query(MessageFailure)
        .filter_by(
            installation_id=installation_id,
            source_transport_epoch=epoch,
            source_stream=source.stream,
            source_sequence=source.sequence,
        )
        .with_for_update()
        .populate_existing()
    )


def _record_conflict(
    db: Session,
    *,
    installation_id: uuid.UUID,
    epoch: uuid.UUID,
    source: JetStreamSourceIdentity,
    observed_digest: str,
    classification: str,
    error_code: str,
    conflict_code: str,
) -> None:
    conflict_id = uuid.uuid5(
        installation_id,
        f"failure-conflict:v1:{epoch}:{source.stream}:{source.sequence}:"
        f"{observed_digest}:{classification}:{error_code}",
    )
    observation = MessageFailureConflict(
        id=conflict_id,
        installation_id=installation_id,
        source_transport_epoch=epoch,
        source_stream=source.stream,
        source_sequence=source.sequence,
        source_timestamp=source.timestamp,
        observed_digest=observed_digest,
        observed_classification=classification,
        observed_error_code=error_code,
        conflict_code=conflict_code,
    )
    try:
        with db.begin_nested():
            db.add(observation)
            db.flush()
    except IntegrityError:
        # The deterministic primary/unique identity already records this
        # observation from a concurrent delivery.
        pass


def _matching_outbox(
    db: Session,
    *,
    installation_id: uuid.UUID,
    epoch: uuid.UUID,
    failure: MessageFailure,
) -> Outbox | None:
    expected_id = _outbox_id(installation_id, failure.id)
    expected_message_id = _message_id(installation_id, failure.id)
    expected_subject = (
        f"openship.{installation_id.hex}.v1.failure.{failure.classification}"
    )
    row = (
        db.query(Outbox)
        .filter_by(id=expected_id, failure_id=failure.id, destination="failure")
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    expected_payload = json.dumps(
        {"failure_id": str(failure.id)}, separators=(",", ":")
    )
    if (
        row is None
        or row.type != failure.classification
        or row.message_id != expected_message_id
        or row.subject != expected_subject
        or row.transport_epoch != epoch
        or row.conversation_id is not None
        or row.run_id is not None
        or row.event_id is not None
        or row.dispatch_generation is not None
        or row.payload != expected_payload
    ):
        return None
    return row


def failure_handoff_for_source(
    db: Session,
    *,
    installation_id: uuid.UUID,
    source_identity: JetStreamSourceIdentity,
    epoch: uuid.UUID | None = None,
) -> FailureHandoff | None:
    """Load one previously committed handoff for an exhausted source."""
    source = source_identity.validated()
    state = db.get(MessageTransportState, 1)
    if source is None or state is None or state.installation_id != installation_id:
        return None
    epoch = epoch or state.transport_epoch
    if state.transport_epoch != epoch:
        return None
    failure = _identity_query(db, installation_id, epoch, source).one_or_none()
    if failure is None:
        return None
    outbox = _matching_outbox(
        db,
        installation_id=installation_id,
        epoch=epoch,
        failure=failure,
    )
    if outbox is None:
        return None
    return _handoff_from_rows(failure, outbox)


def failure_record_exists(
    db: Session,
    *,
    installation_id: uuid.UUID,
    source_identity: JetStreamSourceIdentity,
) -> bool:
    """Distinguish an absent handoff from a broken existing failure record."""
    source = source_identity.validated()
    state = db.get(MessageTransportState, 1)
    if source is None or state is None or state.installation_id != installation_id:
        return False
    return (
        _identity_query(
            db, installation_id, state.transport_epoch, source
        ).one_or_none()
        is not None
    )


def record_failure_source_conflict(
    db: Session,
    *,
    installation_id: uuid.UUID,
    source_identity: JetStreamSourceIdentity,
    body: bytes,
) -> bool:
    """Record a digest-only conflict when a stored handoff mismatches source."""
    source = source_identity.validated()
    if source is None or not isinstance(body, bytes):
        db.rollback()
        return False
    try:
        state = db.get(MessageTransportState, 1)
        if state is None or state.installation_id != installation_id:
            db.rollback()
            return False
        failure = _identity_query(
            db, installation_id, state.transport_epoch, source
        ).one_or_none()
        if failure is None:
            db.rollback()
            return False
        digest = hashlib.sha256(body).hexdigest()
        if failure.body_digest != digest:
            conflict_code = "source_digest_conflict"
        elif not _same_timestamp(failure.source_timestamp, source.timestamp):
            conflict_code = "failure_row_mismatch"
        elif (
            _matching_outbox(
                db,
                installation_id=installation_id,
                epoch=state.transport_epoch,
                failure=failure,
            )
            is None
        ):
            conflict_code = "outbox_missing"
        else:
            conflict_code = "failure_row_mismatch"
        _record_conflict(
            db,
            installation_id=installation_id,
            epoch=state.transport_epoch,
            source=source,
            observed_digest=digest,
            classification="quarantine",
            error_code="failure_source_conflict",
            conflict_code=conflict_code,
        )
        db.commit()
        return True
    except SQLAlchemyError:
        db.rollback()
        return False


def persist_failure_handoff(
    db: Session,
    prepared: PreparedDelivery,
    disposition: DeliveryDisposition,
    source_identity: JetStreamSourceIdentity | None,
    body: bytes,
    *,
    safe_reconcile: bool = False,
) -> FailureHandoff | None:
    """Commit one safe failure and reference outbox row, or fail closed.

    Call from a slot-owned session. A returned handoff proves the ledger and
    outbox commit completed; JetStream publication and source ACK are separate
    steps. Reconcile callers must set ``safe_reconcile`` only after applying a
    state repair in this same session. Conflict observations contain only
    digests and fixed codes.
    """
    source = source_identity.validated() if source_identity is not None else None
    action, error_code = disposition.action, disposition.code
    if (
        source is None
        or not isinstance(body, bytes)
        or action not in _FAILURE_ACTIONS
        or not isinstance(error_code, str)
        or not _SAFE_ERROR_CODE.fullmatch(error_code)
        or (action == "reconcile" and error_code in _LIVE_OWNER_CODES)
        or (action == "reconcile" and safe_reconcile is not True)
    ):
        db.rollback()
        return None

    digest = hashlib.sha256(body).hexdigest()
    try:
        state = db.get(MessageTransportState, 1)
        if state is None:
            db.rollback()
            return None
        installation_id = state.installation_id
        epoch = state.transport_epoch
        failure_id = _failure_id(installation_id, epoch, source)
        outbox_id = _outbox_id(installation_id, failure_id)
        command = prepared.command

        existing = _identity_query(db, installation_id, epoch, source).one_or_none()
        if existing is not None:
            matches = (
                existing.id == failure_id
                and _same_timestamp(existing.source_timestamp, source.timestamp)
                and existing.body_digest == digest
                and existing.classification == action
                and existing.error_code == error_code
                and existing.message_id == (command.message_id if command else None)
                and existing.run_id == (command.run_id if command else None)
                and existing.conversation_id
                == (command.conversation_id if command else None)
            )
            if not matches:
                conflict_code = (
                    "source_digest_conflict"
                    if existing.body_digest != digest
                    else "failure_row_mismatch"
                )
                _record_conflict(
                    db,
                    installation_id=installation_id,
                    epoch=epoch,
                    source=source,
                    observed_digest=digest,
                    classification=action,
                    error_code=error_code,
                    conflict_code=conflict_code,
                )
                db.commit()
                return None
            outbox = _matching_outbox(
                db,
                installation_id=installation_id,
                epoch=epoch,
                failure=existing,
            )
            if outbox is None:
                _record_conflict(
                    db,
                    installation_id=installation_id,
                    epoch=epoch,
                    source=source,
                    observed_digest=digest,
                    classification=action,
                    error_code=error_code,
                    conflict_code="outbox_missing",
                )
                db.commit()
                return None
            db.commit()
            return _handoff_from_rows(existing, outbox)

        failure = MessageFailure(
            id=failure_id,
            installation_id=installation_id,
            source_transport_epoch=epoch,
            source_stream=source.stream,
            source_sequence=source.sequence,
            source_timestamp=source.timestamp,
            body_digest=digest,
            classification=action,
            error_code=error_code,
            message_id=command.message_id if command else None,
            run_id=command.run_id if command else None,
            conversation_id=command.conversation_id if command else None,
        )
        outbox = Outbox(
            id=outbox_id,
            conversation_id=None,
            event_id=None,
            run_id=None,
            failure_id=failure_id,
            type=action,
            payload=json.dumps({"failure_id": str(failure_id)}, separators=(",", ":")),
            destination="failure",
            schema_version=1,
            message_id=_message_id(installation_id, failure_id),
            subject=f"openship.{installation_id.hex}.v1.failure.{action}",
            dispatch_generation=None,
            transport_epoch=epoch,
            next_attempt_at=datetime.now(UTC),
        )
        try:
            with db.begin_nested():
                db.add_all((failure, outbox))
                db.flush()
        except IntegrityError:
            # Another delivery may have inserted the same deterministic pair.
            db.expire_all()
            existing = _identity_query(db, installation_id, epoch, source).one_or_none()
            if existing is None:
                db.rollback()
                return None
            if (
                existing.body_digest != digest
                or existing.classification != action
                or existing.error_code != error_code
                or not _same_timestamp(existing.source_timestamp, source.timestamp)
                or existing.message_id != (command.message_id if command else None)
                or existing.run_id != (command.run_id if command else None)
                or existing.conversation_id
                != (command.conversation_id if command else None)
            ):
                _record_conflict(
                    db,
                    installation_id=installation_id,
                    epoch=epoch,
                    source=source,
                    observed_digest=digest,
                    classification=action,
                    error_code=error_code,
                    conflict_code=(
                        "source_digest_conflict"
                        if existing.body_digest != digest
                        else "failure_row_mismatch"
                    ),
                )
                db.commit()
                return None
            matching = _matching_outbox(
                db,
                installation_id=installation_id,
                epoch=epoch,
                failure=existing,
            )
            if matching is None:
                db.rollback()
                return None
            db.commit()
            return _handoff_from_rows(existing, matching)
        db.commit()
        return _handoff_from_rows(failure, outbox)
    except SQLAlchemyError:
        db.rollback()
        return None
