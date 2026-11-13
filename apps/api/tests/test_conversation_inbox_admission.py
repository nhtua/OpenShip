"""Conversation message admission is durable and idempotent."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.openship.chat.models import Message
from src.openship.database.session import SessionLocal
from src.openship.runs.models import Event


def test_message_request_rejects_whitespace_without_changing_valid_text():
    from src.openship.runs.inbox.contracts import MessageAdmissionRequest

    key = str(uuid.uuid4())
    with pytest.raises(ValidationError):
        MessageAdmissionRequest(content=" \n ", client_request_id=key)
    request = MessageAdmissionRequest(content="  keep spaces  ", client_request_id=key)
    assert request.content == "  keep spaces  "


def test_exact_replay_has_one_message_and_one_event(client, authorized_client):
    from src.openship.runs.inbox.models import ConversationInboxEntry

    conversation = client.post(
        "/api/conversations", json={"title": "Inbox"}, headers=authorized_client
    ).json()["id"]
    payload = {"content": "A queued message", "client_request_id": str(uuid.uuid4())}
    url = f"/api/runs/{conversation}/messages"
    first = client.post(url, json=payload, headers=authorized_client)
    replay = client.post(url, json=payload, headers=authorized_client)
    assert first.status_code == replay.status_code == 202
    assert first.json()["message_id"] == replay.json()["message_id"]
    with SessionLocal() as db:
        assert (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=uuid.UUID(conversation))
            .count()
            == 1
        )
        assert (
            db.query(Message).filter_by(conversation_id=uuid.UUID(conversation)).count()
            == 1
        )
        assert (
            db.query(Event)
            .filter_by(conversation_id=uuid.UUID(conversation), type="message.admitted")
            .count()
            == 1
        )


def test_blank_or_conflicting_submission(client, authorized_client):
    conversation = client.post(
        "/api/conversations", json={"title": "Inbox"}, headers=authorized_client
    ).json()["id"]
    url = f"/api/runs/{conversation}/messages"
    key = str(uuid.uuid4())
    assert (
        client.post(
            url,
            json={"content": " ", "client_request_id": key},
            headers=authorized_client,
        ).status_code
        == 422
    )
    assert (
        client.post(
            url,
            json={"content": "First", "client_request_id": key},
            headers=authorized_client,
        ).status_code
        == 202
    )
    assert (
        client.post(
            url,
            json={"content": "Changed", "client_request_id": key},
            headers=authorized_client,
        ).status_code
        == 409
    )


def test_open_clarification_admission_attaches_to_open_request():
    from src.openship.runs.inbox.admission import submit_message
    from src.openship.runs.inbox.contracts import MessageAdmissionRequest
    from src.openship.runs.tasks.decisions import park_task_wait
    from src.openship.runs.tasks.models import TaskDecisionRecord
    from tests.conftest import TestSessionLocal
    from tests.test_task_decisions import _pinned_execution
    from tests.test_task_state import _identity

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        progress = park_task_wait(
            db,
            claim,
            kind="clarification",
            prompt="Which source?",
            now=datetime.now(UTC),
        )
        result = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="The morning CSV", client_request_id=str(uuid.uuid4())
            ),
        )
        assert result.disposition == "clarification"
        assert result.status == "attached"
        assert result.request_id == progress.pending_decision.request_id
        assert result.run_id == run.id
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_queued_messages_keep_order_even_without_active_run(client, authorized_client):
    from src.openship.runs.inbox.models import ConversationInboxEntry
    from src.openship.runs.models import Run

    conversation = client.post(
        "/api/conversations", json={"title": "FIFO"}, headers=authorized_client
    ).json()["id"]
    url = f"/api/runs/{conversation}/messages"
    initial = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert initial.status_code == 202
    first = client.post(
        url,
        json={"content": "first", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    # Admitted while a run owns the slot: queued behind it, not yet promoted.
    assert first.status_code == 202 and first.json()["status"] == "queued"
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(initial.json()["id"])).status = "completed"
        db.commit()
    second = client.post(
        url,
        json={"content": "second", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    # Admitted to an idle conversation: assignment queues "second", promotion
    # picks the oldest queued entry ("first") and starts its run; "second"
    # stays queued behind it.
    assert second.status_code == 202 and second.json()["status"] == "queued"
    replies = [first, second]
    with SessionLocal() as db:
        rows = (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=uuid.UUID(conversation))
            .order_by(ConversationInboxEntry.sequence)
            .all()
        )
        assert [str(row.message_id) for row in rows] == [
            reply.json()["message_id"] for reply in replies
        ]
        # FIFO: the older entry was promoted to a run; the newer one waits.
        promoted = [row for row in rows if row.status == "promoted"]
        queued = [row for row in rows if row.status == "queued"]
        assert [row.message_id for row in promoted] == [
            uuid.UUID(first.json()["message_id"])
        ]
        assert [row.message_id for row in queued] == [
            uuid.UUID(second.json()["message_id"])
        ]


def test_non_clarification_active_run_does_not_claim_message(client, authorized_client):
    from src.openship.runs.models import Run

    conversation = client.post(
        "/api/conversations", json={"title": "Active"}, headers=authorized_client
    ).json()["id"]
    turn = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "run", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert turn.status_code == 202
    with SessionLocal() as db:
        run = db.get(Run, uuid.UUID(turn.json()["id"]))
        run.status = "waiting_plan"
        db.commit()
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "later", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert reply.status_code == 202
    assert reply.json()["disposition"] == "queued"
    assert reply.json()["status"] == "queued"
    assert reply.json()["run_id"] is None


def test_unknown_conversation_is_not_an_admission(client, authorized_client):
    reply = client.post(
        f"/api/runs/{uuid.uuid4()}/messages",
        json={"content": "hello", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert reply.status_code == 404


def test_archived_conversation_rejects_new_message(client, authorized_client):
    conversation = client.post(
        "/api/conversations", json={"title": "Archived"}, headers=authorized_client
    ).json()["id"]
    assert (
        client.post(
            f"/api/conversations/{conversation}/archive", headers=authorized_client
        ).status_code
        == 200
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "hello", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert reply.status_code == 409


def test_exact_replay_after_archive_returns_original(client, authorized_client):
    conversation = client.post(
        "/api/conversations", json={"title": "Archived"}, headers=authorized_client
    ).json()["id"]
    url = f"/api/runs/{conversation}/messages"
    payload = {"content": "previous", "client_request_id": str(uuid.uuid4())}
    first = client.post(url, json=payload, headers=authorized_client)
    assert first.status_code == 202
    assert (
        client.post(
            f"/api/conversations/{conversation}/archive", headers=authorized_client
        ).status_code
        == 200
    )
    replay = client.post(url, json=payload, headers=authorized_client)
    assert replay.status_code == 202
    assert replay.json()["message_id"] == first.json()["message_id"]


def test_other_owner_cannot_submit_to_conversation(client, authorized_client):
    from fastapi import HTTPException

    from src.openship.auth.models import User
    from src.openship.runs.inbox.admission import submit_message
    from src.openship.runs.inbox.contracts import MessageAdmissionRequest

    conversation = client.post(
        "/api/conversations", json={"title": "Private"}, headers=authorized_client
    ).json()["id"]
    with SessionLocal() as db:
        other = User(
            username=f"foreign-{uuid.uuid4()}",
            email=f"{uuid.uuid4()}@example.test",
            password_hash="unused",
        )
        db.add(other)
        db.flush()
        with pytest.raises(HTTPException) as refused:
            submit_message(
                db,
                user_id=other.id,
                conversation_id=uuid.UUID(conversation),
                request=MessageAdmissionRequest(
                    content="private", client_request_id=str(uuid.uuid4())
                ),
            )
        assert refused.value.status_code == 404


def test_parallel_exact_retries_create_one_entry(client, authorized_client):
    from src.openship.runs.inbox.models import ConversationInboxEntry

    conversation = client.post(
        "/api/conversations", json={"title": "Concurrent"}, headers=authorized_client
    ).json()["id"]
    url = f"/api/runs/{conversation}/messages"
    payload = {"content": "same message", "client_request_id": str(uuid.uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(
                lambda _: client.post(url, json=payload, headers=authorized_client),
                range(2),
            )
        )
    assert [reply.status_code for reply in replies] == [202, 202]
    assert replies[0].json()["message_id"] == replies[1].json()["message_id"]
    with SessionLocal() as db:
        assert (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=uuid.UUID(conversation))
            .count()
            == 1
        )


def test_parallel_distinct_sends_have_distinct_order(client, authorized_client):
    from src.openship.runs.inbox.models import ConversationInboxEntry

    conversation = client.post(
        "/api/conversations", json={"title": "Concurrent"}, headers=authorized_client
    ).json()["id"]
    url = f"/api/runs/{conversation}/messages"
    payloads = [
        {"content": text, "client_request_id": str(uuid.uuid4())}
        for text in ("first", "second")
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(
                lambda payload: client.post(
                    url, json=payload, headers=authorized_client
                ),
                payloads,
            )
        )
    assert [reply.status_code for reply in replies] == [202, 202]
    with SessionLocal() as db:
        entries = (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=uuid.UUID(conversation))
            .order_by(ConversationInboxEntry.sequence)
            .all()
        )
        assert len(entries) == 2
        assert entries[0].sequence < entries[1].sequence
        assert {str(entry.message_id) for entry in entries} == {
            reply.json()["message_id"] for reply in replies
        }


def test_admission_event_does_not_expose_message_or_selections(
    client, authorized_client
):
    import json

    conversation = client.post(
        "/api/conversations", json={"title": "Private"}, headers=authorized_client
    ).json()["id"]
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "private content", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert reply.status_code == 202
    with SessionLocal() as db:
        event = (
            db.query(Event)
            .filter_by(conversation_id=uuid.UUID(conversation), type="message.admitted")
            .one()
        )
        payload = json.loads(event.payload)
        assert set(payload) == {
            "schema_version",
            "message_id",
            "disposition",
            "status",
            "run_id",
            "request_id",
        }
        assert "private content" not in event.payload


def test_omitted_selections_are_captured_once(client, authorized_client, monkeypatch):
    import json

    from src.openship.auth.models import User
    from src.openship.chat.models import Conversation
    from src.openship.runs.inbox.models import ConversationInboxEntry

    conversation = client.post(
        "/api/conversations", json={"title": "Defaults"}, headers=authorized_client
    ).json()["id"]
    url = f"/api/runs/{conversation}/messages"
    payload = {"content": "queued", "client_request_id": str(uuid.uuid4())}
    first = client.post(url, json=payload, headers=authorized_client)
    assert first.status_code == 202
    with SessionLocal() as db:
        row = (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=uuid.UUID(conversation))
            .one()
        )
        captured = json.loads(row.request_json)
        assert captured["provider_profile_id"] == "primary-fixture"
        assert captured["agent_mode"] == "copilot"
        owner = db.get(User, db.get(Conversation, uuid.UUID(conversation)).user_id)
        owner.agent_mode = "auto"
        owner.agent_mode_revision += 1
        db.commit()
    replay = client.post(url, json=payload, headers=authorized_client)
    assert replay.status_code == 202
    assert replay.json()["message_id"] == first.json()["message_id"]
    with SessionLocal() as db:
        row = (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=uuid.UUID(conversation))
            .one()
        )
        assert json.loads(row.request_json) == captured
