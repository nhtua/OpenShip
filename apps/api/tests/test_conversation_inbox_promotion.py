"""Queued messages promote once, in durable conversation order."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from src.openship.database.session import SessionLocal
from src.openship.runs.models import ACTIVE_RUN_STATUSES, Run


@pytest.mark.parametrize("resolution", ["rejected", "expired"])
def test_recovery_promotes_after_decision_rejection_or_expiry(resolution):
    from src.openship.runs.inbox.admission import submit_message
    from src.openship.runs.inbox.contracts import MessageAdmissionRequest
    from src.openship.runs.inbox.promotion import reconcile_queued_messages
    from src.openship.runs.tasks.contracts import TaskDecisionCommand
    from src.openship.runs.tasks.decisions import (
        apply_decision,
        expire_task_decisions,
        park_task_wait,
    )
    from src.openship.runs.tasks.state import save_plan
    from tests.test_task_decisions import _pinned_execution
    from tests.test_task_state import _identity, _plan

    with SessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        now = datetime.now(UTC)
        decision = park_task_wait(
            db, claim, kind="plan_confirmation", prompt="Approve?", now=now
        ).pending_decision
        db.commit()
        reply = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="queued", client_request_id=str(uuid.uuid4())
            ),
        )
        assert reply.status == "queued"
        db.commit()
        if resolution == "rejected":
            apply_decision(
                db,
                principal_id=user.id,
                run_id=run.id,
                command=TaskDecisionCommand(
                    client_request_id=uuid.uuid4(),
                    request_id=decision.request_id,
                    plan_revision=decision.plan_revision,
                    expected_digest=decision.expected_digest,
                    action="reject",
                ),
                now=now,
            )
        else:
            assert expire_task_decisions(db, now=now + timedelta(days=2)) >= 1
        db.commit()
        reconcile_queued_messages(db)
        db.commit()
        assert (
            db.query(Run)
            .filter_by(conversation_id=conversation.id, status="queued")
            .count()
            == 1
        )


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled", "timed_out"])
def test_terminal_run_releases_queue(client, authorized_client, terminal):
    from src.openship.runs.inbox.promotion import promote_next_message

    conversation = client.post(
        "/api/conversations", json={"title": terminal}, headers=authorized_client
    ).json()["id"]
    initial = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "first", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    queued = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "second", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert initial.status_code == queued.status_code == 202
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(initial.json()["id"])).status = terminal
        db.commit()
        assert (
            promote_next_message(db, conversation_id=uuid.UUID(conversation))
            is not None
        )


def test_archive_pauses_and_restore_allows_promotion(client, authorized_client):
    from src.openship.runs.inbox.promotion import promote_next_message

    conversation = client.post(
        "/api/conversations", json={"title": "Archive"}, headers=authorized_client
    ).json()["id"]
    initial = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "first", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    queued = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "second", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert initial.status_code == queued.status_code == 202
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(initial.json()["id"])).status = "completed"
        db.commit()
    assert (
        client.post(
            f"/api/conversations/{conversation}/archive", headers=authorized_client
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        assert promote_next_message(db, conversation_id=uuid.UUID(conversation)) is None
        db.rollback()
    assert (
        client.post(
            f"/api/conversations/{conversation}/restore", headers=authorized_client
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        assert (
            db.query(Run)
            .filter_by(conversation_id=uuid.UUID(conversation), status="queued")
            .count()
            == 1
        )


def test_idle_message_is_promoted_in_admission_transaction(client, authorized_client):
    conversation = client.post(
        "/api/conversations", json={"title": "Idle"}, headers=authorized_client
    ).json()["id"]
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "start", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert reply.status_code == 202
    assert reply.json()["status"] == "promoted"
    assert reply.json()["run_id"] is not None


def test_direct_turn_cannot_jump_older_inbox_message(client, authorized_client):
    from tests.test_tool_run_commands import _command

    conversation = client.post(
        "/api/conversations", json={"title": "FIFO"}, headers=authorized_client
    ).json()["id"]
    first = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "older", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first.status_code == reply.status_code == 202
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(first.json()["id"])).status = "completed"
        db.commit()
    direct = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "newer", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert direct.status_code == 409
    tool = client.post(
        f"/api/runs/{conversation}/tools",
        json=_command().model_dump(mode="json"),
        headers=authorized_client,
    )
    assert tool.status_code == 409


def test_promotion_preserves_captured_mode_when_preferences_change(
    client, authorized_client
):
    import json

    from src.openship.auth.models import User
    from src.openship.chat.models import Conversation
    from src.openship.runs.inbox.promotion import promote_next_message

    conversation = client.post(
        "/api/conversations", json={"title": "Mode"}, headers=authorized_client
    ).json()["id"]
    first = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "queued", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first.status_code == reply.status_code == 202
    with SessionLocal() as db:
        conv = db.get(Conversation, uuid.UUID(conversation))
        user = db.get(User, conv.user_id)
        user.agent_mode = "auto"
        user.agent_mode_revision += 1
        db.get(Run, uuid.UUID(first.json()["id"])).status = "completed"
        db.commit()
        run = promote_next_message(db, conversation_id=conv.id)
        assert run is not None
        assert json.loads(run.model_selection)["agent_mode"] == "copilot"


@pytest.mark.parametrize(
    "waiting", ["waiting_plan", "waiting_input", "waiting_config", "waiting_connector"]
)
def test_open_wait_holds_the_conversation_slot(client, authorized_client, waiting):
    from src.openship.runs.inbox.promotion import promote_next_message

    conversation = client.post(
        "/api/conversations", json={"title": "Wait"}, headers=authorized_client
    ).json()["id"]
    first = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "queued", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first.status_code == reply.status_code == 202
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(first.json()["id"])).status = waiting
        db.commit()
        assert promote_next_message(db, conversation_id=uuid.UUID(conversation)) is None


def test_queued_message_does_not_enter_active_run_context(
    client, authorized_client, monkeypatch
):
    from tests.test_provider_worker import (
        _claim,
        _process,
        _queued_turn,
        _serve,
        sdk_chunk,
    )

    run = _queued_turn(client, authorized_client, monkeypatch)
    reply = client.post(
        f"/api/runs/{run.conversation_id}/messages",
        json={"content": "FUTURE MESSAGE", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert reply.status_code == 202 and reply.json()["status"] == "queued"
    transport = _serve(
        monkeypatch, chunks=[sdk_chunk(content="Answer", finish_reason="stop")]
    )
    _process(_claim())
    assert all(
        "FUTURE MESSAGE" not in (message.get("content") or "")
        for message in transport.seen_kwargs["messages"]
    )
    with SessionLocal() as db:
        from src.openship.runs.models import RunJob

        promoted = (
            db.query(Run)
            .filter_by(conversation_id=run.conversation_id, status="queued")
            .one()
        )
        assert promoted.id != run.id
        db.query(RunJob).filter_by(run_id=promoted.id).one().available_at = datetime(
            1990, 1, 1, tzinfo=UTC
        )
        db.commit()
    next_transport = _serve(
        monkeypatch, chunks=[sdk_chunk(content="Next answer", finish_reason="stop")]
    )
    _process(_claim())
    messages = next_transport.seen_kwargs["messages"]
    assert any(message.get("content") == "Answer" for message in messages)
    assert messages[-1]["content"] == "FUTURE MESSAGE"


def test_queued_message_does_not_prevent_first_turn_title(monkeypatch):
    from src.openship.chat.models import Conversation
    from tests.test_provider_worker import (
        _claim,
        _process,
        _queued_title_turn,
        _serve,
        sdk_chunk,
    )

    _, conversation_id = _queued_title_turn(monkeypatch, "First question")
    # The direct fixture uses its own user, so admit its queued message through
    # the service as that owner rather than the unrelated HTTP client.
    from src.openship.runs.inbox.admission import submit_message
    from src.openship.runs.inbox.contracts import MessageAdmissionRequest

    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        submit_message(
            db,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
            request=MessageAdmissionRequest(
                content="follow-up", client_request_id=str(uuid.uuid4())
            ),
        )
        db.commit()
    _serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="Answer.\n\n## Title: My title", finish_reason="stop")
        ],
    )
    _process(_claim())
    with SessionLocal() as db:
        assert db.get(Conversation, conversation_id).title == "My title"


def test_fifo_promotion_after_completion(client, authorized_client):
    from src.openship.runs.inbox.promotion import promote_next_message

    conversation = client.post(
        "/api/conversations", json={"title": "Queue"}, headers=authorized_client
    ).json()["id"]
    first_run = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first_run.status_code == 202
    url = f"/api/runs/{conversation}/messages"
    messages = []
    for content in ("first", "second", "third"):
        result = client.post(
            url,
            json={"content": content, "client_request_id": str(uuid.uuid4())},
            headers=authorized_client,
        )
        assert result.status_code == 202
        messages.append(result.json()["message_id"])

    from src.openship.runs.inbox.models import ConversationInboxEntry

    promoted = []
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(first_run.json()["id"])).status = "completed"
        db.commit()
        for _ in messages:
            run = promote_next_message(db, conversation_id=uuid.UUID(conversation))
            assert run is not None
            entry = db.query(ConversationInboxEntry).filter_by(run_id=run.id).one()
            promoted.append(str(entry.message_id))
            assert (
                db.query(Run)
                .filter(
                    Run.conversation_id == uuid.UUID(conversation),
                    Run.status.in_(ACTIVE_RUN_STATUSES),
                )
                .count()
                == 1
            )
            assert (
                promote_next_message(db, conversation_id=uuid.UUID(conversation))
                is None
            )
            run.status = "completed"
            db.commit()
    assert promoted == messages


def test_recovery_does_not_duplicate_run(client, authorized_client):
    from src.openship.runs.inbox.promotion import reconcile_queued_messages

    conversation = client.post(
        "/api/conversations", json={"title": "Recovery"}, headers=authorized_client
    ).json()["id"]
    first_run = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first_run.status_code == 202
    message = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "recover", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert message.status_code == 202
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(first_run.json()["id"])).status = "completed"
        db.commit()
        reconcile_queued_messages(db)
        db.commit()
        run = (
            db.query(Run)
            .filter_by(conversation_id=uuid.UUID(conversation), status="queued")
            .one()
        )
        original = run.id
        reconcile_queued_messages(db)
        db.commit()
        assert (
            db.query(Run)
            .filter_by(conversation_id=uuid.UUID(conversation), status="queued")
            .one()
            .id
            == original
        )


def test_recovery_batch_skips_busy_conversations(client, authorized_client):
    from src.openship.runs.inbox.promotion import reconcile_queued_messages

    conversations = []
    for title in ("busy", "eligible"):
        conversation = client.post(
            "/api/conversations", json={"title": title}, headers=authorized_client
        ).json()["id"]
        first = client.post(
            f"/api/runs/{conversation}/turns",
            json={"content": "running", "client_request_id": str(uuid.uuid4())},
            headers=authorized_client,
        )
        queued = client.post(
            f"/api/runs/{conversation}/messages",
            json={"content": "queued", "client_request_id": str(uuid.uuid4())},
            headers=authorized_client,
        )
        assert first.status_code == queued.status_code == 202
        conversations.append((conversation, first.json()["id"]))
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(conversations[1][1])).status = "completed"
        db.commit()
        assert reconcile_queued_messages(db, batch_size=1) == 1
        db.commit()
        assert (
            db.query(Run)
            .filter_by(conversation_id=uuid.UUID(conversations[1][0]), status="queued")
            .count()
            == 1
        )


def test_installed_recovery_promotes_orphaned_queue(client, authorized_client):
    from src.openship.messaging.recovery import _RecoveryResource

    conversation = client.post(
        "/api/conversations", json={"title": "Recovery"}, headers=authorized_client
    ).json()["id"]
    first = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "queued", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first.status_code == reply.status_code == 202
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(first.json()["id"])).status = "completed"
        db.commit()
    _RecoveryResource("recovery-test", SessionLocal).reconcile_expired_leases()
    with SessionLocal() as db:
        assert (
            db.query(Run)
            .filter_by(conversation_id=uuid.UUID(conversation), status="queued")
            .count()
            == 1
        )


def test_cancellation_promotes_next_message(client, authorized_client):
    conversation = client.post(
        "/api/conversations", json={"title": "Cancellation"}, headers=authorized_client
    ).json()["id"]
    first = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "queued", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first.status_code == reply.status_code == 202
    assert (
        client.post(
            f"/api/runs/{first.json()['id']}/cancel", headers=authorized_client
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        assert (
            db.query(Run)
            .filter_by(conversation_id=uuid.UUID(conversation), status="queued")
            .count()
            == 1
        )


def test_competing_promoters_cannot_create_duplicate_runs(client, authorized_client):
    from src.openship.runs.inbox.promotion import promote_next_message

    conversation = client.post(
        "/api/conversations", json={"title": "Racing"}, headers=authorized_client
    ).json()["id"]
    first = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={"content": "queued", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first.status_code == reply.status_code == 202
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(first.json()["id"])).status = "completed"
        db.commit()

    def promote(_):
        with SessionLocal() as db:
            run = promote_next_message(db, conversation_id=uuid.UUID(conversation))
            db.commit()
            return run.id if run else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(promote, range(2)))
    assert sum(result is not None for result in results) == 1
    with SessionLocal() as db:
        assert (
            db.query(Run)
            .filter_by(conversation_id=uuid.UUID(conversation), status="queued")
            .count()
            == 1
        )


def test_unavailable_profile_is_failed_without_substituting(
    client, authorized_client, monkeypatch
):
    from src.openship.runs.inbox.models import ConversationInboxEntry
    from src.openship.runs.inbox.promotion import promote_next_message
    from tests.conftest import configure_profiles, profile_spec

    conversation = client.post(
        "/api/conversations", json={"title": "Unavailable"}, headers=authorized_client
    ).json()["id"]
    first_run = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    assert first_run.status_code == 202
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={
            "content": "queued",
            "client_request_id": str(uuid.uuid4()),
            "provider_profile_id": "primary-fixture",
        },
        headers=authorized_client,
    )
    assert reply.status_code == 202
    configure_profiles(monkeypatch, [profile_spec("replacement")], "replacement")
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(first_run.json()["id"])).status = "completed"
        db.commit()
        assert promote_next_message(db, conversation_id=uuid.UUID(conversation)) is None
        entry = (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=uuid.UUID(conversation))
            .one()
        )
        assert entry.status == "failed"
        assert entry.failure_code
        assert (
            db.query(Run).filter_by(conversation_id=uuid.UUID(conversation)).count()
            == 1
        )


def test_unavailable_selected_connector_fails_without_fallback(
    client, authorized_client
):
    from src.openship.runs.inbox.models import ConversationInboxEntry
    from src.openship.runs.inbox.promotion import promote_next_message

    conversation = client.post(
        "/api/conversations", json={"title": "Connector"}, headers=authorized_client
    ).json()["id"]
    first = client.post(
        f"/api/runs/{conversation}/turns",
        json={"content": "running", "client_request_id": str(uuid.uuid4())},
        headers=authorized_client,
    )
    reply = client.post(
        f"/api/runs/{conversation}/messages",
        json={
            "content": "queued",
            "client_request_id": str(uuid.uuid4()),
            "enable_tools": True,
            "connector_selections": {"brave": str(uuid.uuid4())},
        },
        headers=authorized_client,
    )
    assert first.status_code == reply.status_code == 202
    with SessionLocal() as db:
        db.get(Run, uuid.UUID(first.json()["id"])).status = "completed"
        db.commit()
        assert promote_next_message(db, conversation_id=uuid.UUID(conversation)) is None
        entry = (
            db.query(ConversationInboxEntry)
            .filter_by(conversation_id=uuid.UUID(conversation))
            .one()
        )
        assert entry.status == "failed"
        assert entry.failure_code
