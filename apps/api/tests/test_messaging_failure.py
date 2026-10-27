"""Safe failure ledger and confirmed JetStream handoff contracts."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from src.openship.database.session import SessionLocal
from src.openship.messaging.delivery import DeliveryDisposition, PreparedDelivery
from src.openship.messaging.failure import (
    FailureHandoff,
    JetStreamSourceIdentity,
    MessageFailure,
    MessageFailureConflict,
    failure_record_exists,
    handoff_matches_delivery,
    persist_failure_handoff,
    record_failure_source_conflict,
    source_identity_from_message,
)
from src.openship.messaging.models import MessageTransportState
from src.openship.messaging.relay import claim_outbox_batch, publish_failure_handoff
from src.openship.runs.models import Outbox

_FAILURE_IDS: list[uuid.UUID] = []
_CONFLICT_IDS: list[uuid.UUID] = []


@pytest.fixture(autouse=True)
def cleanup_failure_fixtures():
    yield
    if not _FAILURE_IDS and not _CONFLICT_IDS:
        return
    with SessionLocal() as db:
        if _CONFLICT_IDS:
            db.query(MessageFailureConflict).filter(
                MessageFailureConflict.id.in_(_CONFLICT_IDS)
            ).delete(synchronize_session=False)
        if _FAILURE_IDS:
            db.query(Outbox).filter(Outbox.failure_id.in_(_FAILURE_IDS)).delete(
                synchronize_session=False
            )
            db.query(MessageFailure).filter(MessageFailure.id.in_(_FAILURE_IDS)).delete(
                synchronize_session=False
            )
        db.commit()
    _FAILURE_IDS.clear()
    _CONFLICT_IDS.clear()


def _source(*, stream="OS_RUNS", sequence=None, timestamp=None):
    return JetStreamSourceIdentity(
        stream=stream,
        sequence=sequence or (uuid.uuid4().int % (2**63 - 1)) + 1,
        timestamp=timestamp or datetime.now(UTC),
    )


def _prepared(code="invalid_run_command", action="quarantine"):
    return PreparedDelivery(
        command=None,
        decision=None,
        disposition=DeliveryDisposition(action, code),
    )


def _persist(
    source,
    body=b"private provider output",
    *,
    code="invalid_run_command",
    action="quarantine",
):
    with SessionLocal() as db:
        result = persist_failure_handoff(
            db,
            _prepared(code, action),
            _prepared(code, action).disposition,
            source,
            body,
        )
    if result is not None:
        _FAILURE_IDS.append(result.failure_id)
    return result


async def _inline_run_sync(function, *args, **kwargs):
    return function(*args, **kwargs)


def test_source_identity_uses_only_validated_jetstream_metadata():
    timestamp = datetime.now(UTC)
    metadata = SimpleNamespace(
        stream="OS_RUNS",
        sequence=SimpleNamespace(stream=42),
        timestamp=timestamp,
    )
    assert source_identity_from_message(SimpleNamespace(metadata=metadata)) == _source(
        sequence=42, timestamp=timestamp
    )
    assert (
        source_identity_from_message(
            SimpleNamespace(
                metadata=SimpleNamespace(
                    stream="OS_EVENTS",
                    sequence=SimpleNamespace(stream=42),
                    timestamp=timestamp,
                )
            )
        )
        is None
    )
    assert source_identity_from_message(SimpleNamespace(metadata=None)) is None


def test_failure_and_reference_outbox_commit_idempotently_without_payload():
    source = _source()
    body = b"private-value-must-never-be-stored"
    handoff = _persist(source, body)
    assert isinstance(handoff, FailureHandoff)
    duplicate = _persist(source, body)
    assert duplicate == handoff

    with SessionLocal() as db:
        failure = db.get(MessageFailure, handoff.failure_id)
        outbox = db.get(Outbox, handoff.outbox_id)
        state = db.get(MessageTransportState, 1)
        assert failure is not None
        assert failure.installation_id == state.installation_id
        assert failure.source_transport_epoch == state.transport_epoch
        assert failure.source_stream == "OS_RUNS"
        assert failure.source_sequence == source.sequence
        assert failure.source_timestamp.replace(tzinfo=UTC) == source.timestamp
        assert failure.body_digest
        assert failure.classification == "quarantine"
        assert failure.error_code == "invalid_run_command"
        assert failure.message_id is None and failure.run_id is None
        assert failure.conversation_id is None
        assert outbox is not None
        assert outbox.destination == "failure"
        assert outbox.failure_id == failure.id
        assert outbox.conversation_id is None
        assert outbox.run_id is None and outbox.event_id is None
        assert outbox.message_id == uuid.uuid5(
            state.installation_id, f"failure.notice:{failure.id}"
        )
        assert outbox.subject == (
            f"openship.{state.installation_id.hex}.v1.failure.quarantine"
        )
        assert json.loads(outbox.payload) == {"failure_id": str(failure.id)}
        assert body.decode() not in repr(failure) + repr(outbox)
        assert (
            db.query(MessageFailure)
            .filter_by(
                installation_id=state.installation_id,
                source_transport_epoch=state.transport_epoch,
                source_sequence=source.sequence,
            )
            .count()
            == 1
        )
        assert db.query(Outbox).filter_by(failure_id=handoff.failure_id).count() == 1


def test_source_identity_conflict_is_recorded_without_changing_canonical_failure():
    source = _source()
    canonical = _persist(source, b"first body")
    assert canonical is not None

    conflict = _persist(source, b"different body")
    assert conflict is None
    with SessionLocal() as db:
        failure = db.get(MessageFailure, canonical.failure_id)
        assert failure.body_digest
        observation = (
            db.query(MessageFailureConflict)
            .filter_by(source_sequence=source.sequence)
            .one()
        )
        _CONFLICT_IDS.append(observation.id)
        assert observation.source_sequence == source.sequence
        assert observation.observed_digest != failure.body_digest
        assert observation.conflict_code == "source_digest_conflict"
        assert "different body" not in repr(observation)
        assert (
            db.query(MessageFailure)
            .filter_by(
                installation_id=failure.installation_id,
                source_transport_epoch=failure.source_transport_epoch,
                source_sequence=source.sequence,
            )
            .count()
            == 1
        )
        assert db.query(Outbox).filter_by(failure_id=canonical.failure_id).count() == 1


def test_observed_failure_handoff_conflict_is_content_free_and_idempotent():
    source = _source()
    canonical = _persist(source, b"canonical body")
    assert canonical is not None
    observed = b"changed body"

    with SessionLocal() as db:
        assert record_failure_source_conflict(
            db,
            installation_id=canonical.installation_id,
            source_identity=source,
            body=observed,
        )
    with SessionLocal() as db:
        conflict = (
            db.query(MessageFailureConflict)
            .filter_by(source_sequence=source.sequence)
            .one()
        )
        _CONFLICT_IDS.append(conflict.id)
        assert conflict.observed_digest != canonical.body_digest
        assert conflict.observed_classification == "quarantine"
        assert conflict.observed_error_code == "failure_source_conflict"
        assert conflict.conflict_code == "source_digest_conflict"
        assert observed.decode() not in repr(conflict)


def test_incomplete_failure_outbox_is_observed_and_blocks_generic_recovery():
    source = _source()
    canonical = _persist(source, b"canonical body")
    assert canonical is not None
    with SessionLocal() as db:
        db.query(Outbox).filter_by(id=canonical.outbox_id).delete()
        db.commit()
    with SessionLocal() as db:
        assert failure_record_exists(
            db,
            installation_id=canonical.installation_id,
            source_identity=source,
        )
    with SessionLocal() as db:
        assert record_failure_source_conflict(
            db,
            installation_id=canonical.installation_id,
            source_identity=source,
            body=b"canonical body",
        )
    with SessionLocal() as db:
        conflict = (
            db.query(MessageFailureConflict)
            .filter_by(source_sequence=source.sequence)
            .one()
        )
        _CONFLICT_IDS.append(conflict.id)
        assert conflict.conflict_code == "outbox_missing"


def test_handoff_marker_is_bound_to_exact_source_body_and_classification():
    source = _source()
    body = b"private source body"
    prepared = _prepared()
    handoff = _persist(source, body)
    assert handoff is not None
    with SessionLocal() as db:
        installation_id = db.get(MessageTransportState, 1).installation_id

    assert handoff_matches_delivery(
        handoff,
        installation_id=installation_id,
        source_identity=source,
        disposition=prepared.disposition,
        body=body,
    )
    assert not handoff_matches_delivery(
        handoff,
        installation_id=installation_id,
        source_identity=_source(),
        disposition=prepared.disposition,
        body=body,
    )
    assert not handoff_matches_delivery(
        handoff,
        installation_id=installation_id,
        source_identity=source,
        disposition=prepared.disposition,
        body=b"different source body",
    )


def test_failure_persistence_rejects_wrong_stream_and_live_owner_reconcile():
    assert _persist(_source(stream="OS_EVENTS"), b"bad") is None
    assert (
        _persist(
            _source(),
            b"uncertain active run",
            code="ownership_moved",
            action="reconcile",
        )
        is None
    )
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        assert (
            db.query(MessageFailure)
            .filter_by(
                installation_id=state.installation_id,
                source_transport_epoch=state.transport_epoch,
            )
            .filter(MessageFailure.source_stream == "OS_EVENTS")
            .count()
            == 0
        )


class _Ack:
    stream = "OS_FAILURES"
    seq = 7
    duplicate = False


class _PublishingJetStream:
    def __init__(self, *, fail_first=False):
        self.publications = []
        self.fail_first = fail_first

    async def publish(self, subject, body, *, headers):
        self.publications.append((subject, body, headers))
        with SessionLocal() as db:
            outbox = (
                db.query(Outbox)
                .filter_by(message_id=uuid.UUID(headers["Nats-Msg-Id"]))
                .one()
            )
            assert outbox.processed_at is None
        if self.fail_first and len(self.publications) == 1:
            raise TimeoutError("publish confirmation lost")
        return _Ack()


@pytest.mark.asyncio
async def test_failure_puback_and_outbox_marker_precede_source_ack_authority():
    handoff = _persist(_source(), b"do-not-publish-this")
    js = _PublishingJetStream()

    assert await publish_failure_handoff(
        js, handoff, session_factory=SessionLocal, run_sync=_inline_run_sync
    )
    subject, body, headers = js.publications[0]
    envelope = json.loads(body)
    assert subject.endswith(".failure.quarantine")
    assert envelope["failure_id"] == str(handoff.failure_id)
    assert "conversation_id" not in envelope and "run_id" not in envelope
    assert "do-not-publish-this" not in body.decode()
    assert headers == {
        "Nats-Msg-Id": str(
            uuid.uuid5(
                envelope_uuid(envelope["installation_id"]),
                f"failure.notice:{handoff.failure_id}",
            )
        )
    }
    with SessionLocal() as db:
        assert db.get(Outbox, handoff.outbox_id).processed_at is not None

    assert await publish_failure_handoff(
        js, handoff, session_factory=SessionLocal, run_sync=_inline_run_sync
    )
    assert len(js.publications) == 1


@pytest.mark.asyncio
async def test_uncertain_puback_keeps_outbox_pending_and_reuses_stable_identity():
    handoff = _persist(_source(), b"sensitive body")
    js = _PublishingJetStream(fail_first=True)

    assert not await publish_failure_handoff(
        js, handoff, session_factory=SessionLocal, run_sync=_inline_run_sync
    )
    with SessionLocal() as db:
        row = db.get(Outbox, handoff.outbox_id)
        assert row.processed_at is None
        # Simulate a retry after JetStream's bounded 120 second dedupe window.
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=180)
        db.commit()

    assert await publish_failure_handoff(
        js, handoff, session_factory=SessionLocal, run_sync=_inline_run_sync
    )
    assert len(js.publications) == 2
    assert js.publications[0][2] == js.publications[1][2]
    assert js.publications[0][1] == js.publications[1][1]


@pytest.mark.asyncio
async def test_failure_publisher_respects_general_relay_claim_lease():
    handoff = _persist(_source(), b"pending relay lease")
    leased = claim_outbox_batch(
        session_factory=SessionLocal,
        limit=1,
        outbox_id=handoff.outbox_id,
        failure_id=handoff.failure_id,
    )
    assert len(leased) == 1
    js = _PublishingJetStream()

    assert not await publish_failure_handoff(
        js, handoff, session_factory=SessionLocal, run_sync=_inline_run_sync
    )
    assert js.publications == []

    with SessionLocal() as db:
        row = db.get(Outbox, handoff.outbox_id)
        row.publisher_claim_id = None
        row.publisher_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert await publish_failure_handoff(
        js, handoff, session_factory=SessionLocal, run_sync=_inline_run_sync
    )
    assert len(js.publications) == 1


def test_outbox_failure_scope_is_checked_by_the_database():
    with SessionLocal() as db:
        state = db.get(MessageTransportState, 1)
        db.add(
            Outbox(
                conversation_id=None,
                event_id=None,
                run_id=None,
                failure_id=None,
                type="quarantine",
                payload="{}",
                destination="failure",
                schema_version=1,
                message_id=uuid.uuid4(),
                subject=f"openship.{state.installation_id.hex}.v1.failure.quarantine",
                transport_epoch=state.transport_epoch,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def envelope_uuid(value):
    return uuid.UUID(value)
