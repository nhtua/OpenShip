"""Tests for the JetStream-backed event subscription contract."""

import asyncio
import uuid

import pytest
from fastapi import HTTPException
from nats.js.api import AckPolicy, DeliverPolicy

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.database.session import SessionLocal
from src.openship.events.fanout import (
    BrokerUnavailable,
    EventFanout,
    EventNotice,
    SnapshotRequired,
    decode_event_notice,
    event_consumer_config,
)
from src.openship.events.routes import _resolve_authorized_sse_cursor
from src.openship.events.service import AuthorizedScope, resolve_event_cursor
from src.openship.runs.models import Event
from src.openship.workspace.models import Project


def _conversation_with_events(count: int = 3):
    db = SessionLocal()
    try:
        user = User(
            username=f"sse_{uuid.uuid4().hex[:8]}",
            email=f"sse_{uuid.uuid4().hex[:8]}@example.com",
            password_hash="hash",
        )
        db.add(user)
        db.flush()

        project = Project(owner_user_id=user.id, name="SSE Test Project")
        db.add(project)
        db.flush()

        conversation = Conversation(
            user_id=user.id,
            project_id=project.id,
            title="SSE Test",
            next_event_sequence=count,
        )
        db.add(conversation)
        db.flush()

        events = [
            Event(
                conversation_id=conversation.id,
                sequence=sequence,
                type="test.event",
                payload="{}",
            )
            for sequence in range(count)
        ]
        db.add_all(events)
        db.commit()

        scope = AuthorizedScope(
            user_id=user.id,
            conversation_id=conversation.id,
        )
        return db, scope, events
    except Exception:
        db.rollback()
        db.close()
        raise


async def _run_inline(function, *args):
    return function(*args)


def test_numeric_last_event_id_takes_precedence_over_query_cursor():
    db, scope, _events = _conversation_with_events()
    try:
        decision = resolve_event_cursor(db, scope, header="1", query=-1)

        assert decision.sequence == 1
        assert decision.snapshot_required is False
    finally:
        db.close()


def test_route_resolves_uuid_cursor_after_authorizing_conversation():
    db, scope, events = _conversation_with_events()
    event_id = events[1].id
    db.close()

    resolved_scope, decision = _resolve_authorized_sse_cursor(
        scope.user_id,
        str(scope.conversation_id),
        str(event_id),
        -1,
    )

    assert resolved_scope == scope
    assert decision.sequence == 1
    assert decision.snapshot_required is False


def test_route_scope_rejects_foreign_user_before_subscribing():
    db, scope, _events = _conversation_with_events()
    db.close()

    with pytest.raises(HTTPException) as error:
        _resolve_authorized_sse_cursor(
            uuid.uuid4(),
            str(scope.conversation_id),
            None,
            -1,
        )

    assert error.value.status_code == 404


def test_uuid_last_event_id_resolves_inside_authorized_conversation():
    db, scope, events = _conversation_with_events()
    try:
        decision = resolve_event_cursor(db, scope, header=str(events[1].id), query=-1)

        assert decision.sequence == 1
        assert decision.snapshot_required is False
    finally:
        db.close()


def test_unknown_uuid_last_event_id_requires_snapshot_instead_of_replaying_from_query():
    db, scope, _events = _conversation_with_events()
    try:
        decision = resolve_event_cursor(db, scope, header=str(uuid.uuid4()), query=-1)

        assert decision.snapshot_required is True
    finally:
        db.close()


def test_foreign_conversation_uuid_last_event_id_requires_snapshot():
    db, scope, _events = _conversation_with_events()
    foreign_db, _foreign_scope, foreign_events = _conversation_with_events()
    try:
        decision = resolve_event_cursor(
            db,
            scope,
            header=str(foreign_events[1].id),
            query=-1,
        )

        assert decision.snapshot_required is True
    finally:
        db.close()
        foreign_db.close()


def test_malformed_last_event_id_falls_back_to_query_cursor():
    db, scope, _events = _conversation_with_events()
    try:
        decision = resolve_event_cursor(db, scope, header="not-an-event-id", query=1)

        assert decision.sequence == 1
        assert decision.snapshot_required is False
    finally:
        db.close()


def test_pruned_numeric_cursor_requires_snapshot():
    db, scope, _events = _conversation_with_events()
    try:
        db.query(Event).filter(
            Event.conversation_id == scope.conversation_id,
            Event.sequence < 2,
        ).delete()
        db.commit()

        decision = resolve_event_cursor(db, scope, header=None, query=-1)

        assert decision.snapshot_required is True
    finally:
        db.close()


def test_event_notice_decoder_checks_installation_subject_and_event_reference():
    installation_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    event_id = uuid.uuid4()
    subject = f"openship.{installation_id.hex}.v1.event.{conversation_id.hex}"
    payload = f'{{"event_id":"{event_id}"}}'.encode()

    notice = decode_event_notice(installation_id, subject, payload)

    assert notice == EventNotice(conversation_id=conversation_id, event_id=event_id)


def test_each_api_event_consumer_is_ephemeral_and_starts_with_new_notices():
    installation_id = uuid.uuid4()
    consumer_name = f"api-events-{uuid.uuid4().hex}"

    subject, config = event_consumer_config(installation_id, name=consumer_name)

    assert subject == f"openship.{installation_id.hex}.v1.event.>"
    assert config.filter_subject == subject
    assert config.name == consumer_name
    assert config.durable_name is None
    assert config.ack_policy == AckPolicy.NONE
    assert config.deliver_policy == DeliverPolicy.NEW


@pytest.mark.asyncio
async def test_subscriber_replays_then_repairs_out_of_order_live_notices():
    db, scope, _events = _conversation_with_events(count=2)
    db.close()
    fanout = EventFanout(
        session_factory=SessionLocal,
        installation_id=uuid.uuid4(),
        run_sync=_run_inline,
    )
    stream = fanout.subscribe_conversation(scope, after_sequence=-1)

    received = [await anext(stream)]
    new_event_ids = await _run_inline(
        _append_events,
        scope.conversation_id,
        (2, 3),
    )
    fanout._publish_notice(EventNotice(scope.conversation_id, new_event_ids[1]))
    fanout._publish_notice(EventNotice(scope.conversation_id, new_event_ids[0]))
    received.append(await anext(stream))
    received.append(await anext(stream))
    received.append(await asyncio.wait_for(anext(stream), timeout=1))

    assert [event.sequence for event in received] == [0, 1, 2, 3]
    await stream.aclose()


@pytest.mark.asyncio
async def test_late_old_notice_does_not_duplicate_or_block_following_events():
    db, scope, initial_events = _conversation_with_events(count=2)
    stale_event_id = initial_events[0].id
    db.close()
    fanout = EventFanout(
        session_factory=SessionLocal,
        installation_id=uuid.uuid4(),
        run_sync=_run_inline,
    )
    stream = fanout.subscribe_conversation(scope, after_sequence=-1)
    pending = None
    try:
        received = [await anext(stream), await anext(stream)]
        first_live_id = _append_events(scope.conversation_id, (2,))[0]
        fanout._publish_notice(EventNotice(scope.conversation_id, first_live_id))
        received.append(await asyncio.wait_for(anext(stream), timeout=1))

        pending = asyncio.create_task(anext(stream))
        fanout._publish_notice(EventNotice(scope.conversation_id, stale_event_id))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending), timeout=0.05)

        next_live_id = _append_events(scope.conversation_id, (3,))[0]
        fanout._publish_notice(EventNotice(scope.conversation_id, next_live_id))
        following = await asyncio.wait_for(pending, timeout=1)

        assert [event.sequence for event in received] == [0, 1, 2]
        assert following.sequence == 3
        assert following.id == str(next_live_id)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()


@pytest.mark.asyncio
async def test_bounded_client_queue_requires_snapshot_on_overflow():
    db, scope, _events = _conversation_with_events(count=1)
    db.close()
    fanout = EventFanout(
        session_factory=SessionLocal,
        installation_id=uuid.uuid4(),
        run_sync=_run_inline,
    )
    stream = fanout.subscribe_conversation(scope, after_sequence=-1)

    assert (await anext(stream)).sequence == 0
    for _ in range(257):
        fanout._publish_notice(EventNotice(scope.conversation_id, uuid.uuid4()))

    with pytest.raises(SnapshotRequired):
        await anext(stream)
    await stream.aclose()


@pytest.mark.asyncio
async def test_broker_loss_closes_existing_subscriber_for_browser_recovery():
    db, scope, _events = _conversation_with_events(count=1)
    db.close()
    fanout = EventFanout(
        session_factory=SessionLocal,
        installation_id=uuid.uuid4(),
        run_sync=_run_inline,
    )
    stream = fanout.subscribe_conversation(scope, after_sequence=-1)

    assert (await anext(stream)).sequence == 0
    fanout._close_subscribers()

    with pytest.raises(BrokerUnavailable):
        await anext(stream)
    await stream.aclose()


def _append_events(conversation_id: uuid.UUID, sequences: tuple[int, ...]):
    db = SessionLocal()
    try:
        conversation = db.get(Conversation, conversation_id)
        for sequence in sequences:
            db.add(
                Event(
                    conversation_id=conversation_id,
                    sequence=sequence,
                    type="test.event",
                    payload="{}",
                )
            )
        conversation.next_event_sequence = max(sequences) + 1
        db.commit()
        return tuple(
            row[0]
            for row in db.query(Event.id)
            .filter(
                Event.conversation_id == conversation_id,
                Event.sequence.in_(sequences),
            )
            .order_by(Event.sequence.asc())
            .all()
        )
    finally:
        db.close()
