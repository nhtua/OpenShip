"""Provider execution under worker supervision: the run's own snapshot, cancellation, and fenced publication.

The breaks these catch: a worker that follows today's settings instead of the
run's snapshot, a stream that keeps a blocked read after the lease moved, and
a completed status that lands without its message.
"""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.openship.chat.models import Conversation, Message
from src.openship.providers.contracts import (
    ChatMessage,
    ModelRequest,
    RunLimits,
    RunModelConfig,
    pin_profile,
)
from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import _adapter_factories
from src.openship.providers.runner import run_model_turn
from src.openship.runs.models import ProviderAttempt, Run, RunJob
from src.openship.runs.queue import claim_next, finalize
from src.openship.runs.service import bind_run_model_config, load_run_model_config
from src.openship.runs.worker import Worker

from .conftest import (
    FIXTURE_MODEL,
    TestSessionLocal,
    configure_profiles,
    declare_two_profiles,
    profile_spec,
)
from .providers.conftest import StreamTransport, make_adapter, sdk_chunk

# --- The installation, a queued run, and one worker job ----------------------------

#: A queue order the fixture controls. A case's job is always the oldest
#: available one, so the claim that follows is this case's own run and a case
#: never depends on another case's leftovers.
_QUEUE_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def _two_destinations(monkeypatch) -> None:
    """Two profiles that serve different models, so following today's default is visible.

    The installation's default is the second one; runs pin the first. Anything
    that reads today's settings instead of a run's snapshot serves the wrong model.
    """
    configure_profiles(
        monkeypatch,
        [
            profile_spec("primary-fixture", allowed_fallbacks=["secondary-fixture"]),
            {**profile_spec("secondary-fixture"), "model": "changed-after-submission"},
        ],
        "secondary-fixture",
    )


def _conversation(client: TestClient, authorized_client: dict) -> str:
    """An owned conversation to submit turns into."""
    response = client.post("/api/conversations", json={"title": "Fixture"}, headers=authorized_client)
    assert response.status_code == 201, f"conversation creation failed: {response.text}"
    return response.json()["id"]


def _queued_turn(client: TestClient, authorized_client: dict, monkeypatch, order: int = 0) -> Run:
    """A queued run pinned to the first profile, on its own conversation.

    Its job is made the oldest available one, so the claim that follows is
    this run's: a case tests the worker, not another case's leftovers.
    """
    _two_destinations(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    response = client.post(
        f"/api/runs/{conversation_id}/turns",
        json={
            "content": "What ran?",
            "client_request_id": str(uuid.uuid4()),
            "provider_profile_id": "primary-fixture",
        },
        headers=authorized_client,
    )
    assert response.status_code == 202, f"submit failed: {response.text}"

    run = _run_on(conversation_id)
    queue = TestSessionLocal()
    queue.query(RunJob).filter(RunJob.run_id == run.id).one().available_at = (
        _QUEUE_EPOCH + timedelta(seconds=order)
    )
    queue.commit()
    return run


def _run_on(conversation_id: str) -> Run:
    session = TestSessionLocal()
    return session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).one()


def _claim(worker_id: str = "worker-1"):
    """Claim the queued job, the way a worker does."""
    return claim_next(db=TestSessionLocal(), worker_id=worker_id, lease_seconds=30)


def _process(claim, session_factory=None) -> None:
    """One worker job against the test installation."""
    sessions = session_factory or (lambda: TestSessionLocal())
    worker = Worker(worker_id=claim.worker_id, session_factory=sessions, checkpointer=None)
    worker.process_job(claim)


def _serve(monkeypatch, chunks=(), raises=None) -> StreamTransport:
    """The installation's OpenAI adapter answers from a synthetic transport."""
    transport = StreamTransport(list(chunks), raises=raises)
    monkeypatch.setitem(_adapter_factories, "openai", lambda _profile: make_adapter("openai", transport))
    return transport


def _ask(*turns) -> ModelRequest:
    """A conversation the runner would map, as contract messages."""
    return ModelRequest(
        messages=tuple(ChatMessage(role=role, content=text) for role, text in turns),
        max_output_tokens=128,
    )


def _pinned_config(profile_id: str = "primary-fixture") -> RunModelConfig:
    """A snapshot the runner can execute, without a database."""
    return RunModelConfig(
        schema_version=1,
        primary=pin_profile(profile_spec(profile_id)),
        limits=RunLimits(),
        binding_origin="submission",
    )


class RecordingRecorder:
    """A recorder that keeps what it was asked, without a database."""

    def __init__(self):
        self.started = []
        self.finished = []

    def start(self, profile, reason, reserved_cost_usd=None):
        self.started.append((profile.id, reason))
        return uuid.uuid4()

    def finish(self, attempt_id, outcome, usage=None, error_code=None, actual_model=None):
        self.finished.append({"outcome": outcome, "error_code": error_code, "usage": usage})


class BlockingTransport:
    """A stream whose first read never returns; only cancellation can end it."""

    def __init__(self):
        self.closed = False
        self.requests = 0

    async def create(self, **kwargs):
        self.requests += 1
        return _BlockingStream(self)

    def aclose(self):
        self.closed = True


class _BlockingStream:
    def __init__(self, transport):
        self._transport = transport

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(30)
        raise StopAsyncIteration

    async def aclose(self):
        self._transport.aclose()


def _answers(session, run_id) -> list[ProviderAttempt]:
    return session.query(ProviderAttempt).filter(ProviderAttempt.run_id == run_id).all()


def _assistant(session, run_id) -> list[Message]:
    return session.query(Message).filter(
        Message.run_id == run_id, Message.role == "assistant"
    ).all()


# --- The worker runs the run's own snapshot ---------------------------------------


def test_worker_uses_snapshot_not_global_settings(client, authorized_client, monkeypatch):
    """A queued run asks its own model, after today's default moved away from it."""
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    transport = _serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="From the pinned model.", finish_reason="stop"),
            sdk_chunk(choices=False, usage=(3, 4, 7)),
        ],
    )
    monkeypatch.setattr("src.openship.config.settings.openai_model", "changed-after-submission")

    _process(claim)

    assert transport.seen_kwargs["model"] == FIXTURE_MODEL, (
        "the worker served today's model, not the one the run pinned"
    )
    recorded = _answers(TestSessionLocal(), run.id)
    assert [attempt.profile_id for attempt in recorded] == ["primary-fixture"]
    assert recorded[0].requested_model == FIXTURE_MODEL


def test_legacy_queued_run_binds_once_under_fence(client, authorized_client, monkeypatch):
    """A run from before profiles gets one snapshot, under the fence it holds."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    session = TestSessionLocal()
    conversation = session.query(Conversation).filter(
        Conversation.id == uuid.UUID(conversation_id)
    ).one()

    legacy = Run(
        id=uuid.uuid4(),
        conversation_id=conversation.id,
        project_id=conversation.project_id,
        status="queued",
        fence=0,
        attempt=0,
    )
    session.add(legacy)
    session.add(
        Message(
            conversation_id=conversation.id,
            run_id=legacy.id,
            role="user",
            content="A turn from before profiles existed",
        )
    )
    session.add(RunJob(run_id=legacy.id, fence=0, available_at=_QUEUE_EPOCH))
    session.commit()

    claim = _claim()
    _serve(
        monkeypatch,
        chunks=[sdk_chunk(content="Bound at execution.", finish_reason="stop")],
    )

    _process(claim)

    run = _run_on(conversation_id)
    assert run.model_configuration, "a legacy run must record what it actually ran"
    config = load_run_model_config(run)
    assert config.binding_origin == "legacy_bound_at_execution", (
        "binding at execution is not submission-time pinning; say which it was"
    )
    assert config.primary.id == "primary-fixture"

    recorded = _answers(TestSessionLocal(), legacy.id)
    assert len(recorded) == 1, "one generation request, recorded once"
    assert recorded[0].fence == claim.fence

    # A second binding attempt cannot re-bind a run that already recorded one.
    again = bind_run_model_config(TestSessionLocal(), run, run.fence)
    assert again.model_dump_json() == run.model_configuration


# --- Cancellation, lease loss, and supervision ------------------------------------


def test_cancel_interrupts_blocked_read(monkeypatch):
    """A stop signal ends a read that never returned, and closes the stream."""
    transport = BlockingTransport()
    monkeypatch.setitem(_adapter_factories, "openai", lambda _profile: make_adapter("openai", transport))

    config = _pinned_config()
    request = _ask(("user", "Hello!"))
    started = time.monotonic()
    control = InvocationControl(stop=lambda: time.monotonic() - started > 0.2)
    recorder = RecordingRecorder()

    with pytest.raises(ProviderError) as refused:
        asyncio.run(run_model_turn(request, config, control, recorder))

    assert refused.value.code == "cancelled"
    assert transport.closed is True, "a cancelled turn must close the stream it opened"
    assert time.monotonic() - started < 2, "cancellation must not wait for a chunk"
    assert transport.requests == 1, "cancelling interrupts the read, it does not restart it"
    assert recorder.started == [(config.primary.id, "initial")]
    assert recorder.finished[-1]["outcome"] == "cancelled"
    assert recorder.finished[-1]["error_code"] == "cancelled"


def test_lease_loss_stops_dispatch_and_publication(client, authorized_client, monkeypatch):
    """A worker whose fence moved asks nothing and publishes nothing."""
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    moved = TestSessionLocal()
    moved.get(Run, run.id).fence += 1
    moved.commit()

    transport = _serve(
        monkeypatch,
        chunks=[sdk_chunk(content="Should never be asked.", finish_reason="stop")],
    )

    _process(claim)

    assert transport.requests == 0, "a stale worker must not dispatch another request"
    session = TestSessionLocal()
    assert _assistant(session, run.id) == []
    assert _answers(session, run.id) == []
    assert session.get(Run, run.id).status == "running", (
        "a worker that no longer owns the run cannot finalize it"
    )


def test_heartbeat_has_independent_session(client, authorized_client, monkeypatch):
    """The heartbeat renews on its own session, and cannot commit the worker's work."""
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    created = []

    def _sessions():
        created.append(TestSessionLocal())
        return created[-1]

    renewed = []

    def _denied(db, run_id, fence, lease_seconds=None):
        renewed.append(db)
        return False

    monkeypatch.setattr("src.openship.runs.worker.renew", _denied)
    monkeypatch.setattr("src.openship.config.settings.worker_heartbeat_interval", 0.05)

    transport = BlockingTransport()
    monkeypatch.setitem(
        _adapter_factories, "openai", lambda _profile: make_adapter("openai", transport)
    )

    _process(claim, session_factory=_sessions)

    assert renewed, "a long turn must be watched by the heartbeat"
    assert renewed[0] is not created[0], (
        "the heartbeat thread must not share the worker's session"
    )
    assert transport.closed is True, "a denied lease must interrupt the blocked read"
    assert _answers(TestSessionLocal(), run.id) == [], (
        "the heartbeat must not commit the worker's pending attempt"
    )
    assert _assistant(TestSessionLocal(), run.id) == []


def test_snapshot_schema_mismatch_fails_safely(client, authorized_client, monkeypatch):
    """A snapshot this installation cannot read fails safely, with no dispatch."""
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    unreadable = TestSessionLocal()
    unreadable.get(Run, run.id).model_configuration = json.dumps(
        {"schema_version": 2, "primary": {"id": "primary-fixture"}}
    )
    unreadable.commit()

    transport = _serve(
        monkeypatch,
        chunks=[sdk_chunk(content="Never asked.", finish_reason="stop")],
    )

    _process(claim)

    assert transport.requests == 0, "an unreadable snapshot must not reach a provider"
    session = TestSessionLocal()
    assert _assistant(session, run.id) == []
    assert _answers(session, run.id) == []
    finalized = session.get(Run, run.id)
    assert finalized.status == "failed"
    assert finalized.error_code == "configuration_error"
    assert finalized.usage is None


# --- Publication is one transaction ----------------------------------------------


def test_completion_message_and_event_commit_together(client, authorized_client, monkeypatch):
    """A completed status, its message, and its event land in one transaction."""
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    _serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="A complete answer.", finish_reason="stop"),
            sdk_chunk(choices=False, usage=(5, 2, 7)),
        ],
    )

    _process(claim)

    session = TestSessionLocal()
    completed = session.get(Run, run.id)
    assert completed.status == "completed"
    assert json.loads(completed.usage)["total_tokens"] == 7
    assert len(_assistant(session, run.id)) == 1
    assert len(_answers(session, run.id)) == 1

    # A publication that fails after the status is written leaves neither half.
    second = _queued_turn(client, authorized_client, monkeypatch, order=1)
    second_claim = _claim()

    session = TestSessionLocal()
    finalized = finalize(
        db=session,
        run_id=second.id,
        fence=second_claim.fence,
        result={"status": "success", "output": "A second answer"},
        commit=False,
    )
    assert finalized is True
    session.add(
        Message(
            conversation_id=second.conversation_id,
            run_id=second.id,
            role="assistant",
            content="A second answer",
        )
    )
    try:
        raise_if_unbalanced()
    except ValueError:
        session.rollback()

    session = TestSessionLocal()
    assert session.get(Run, second.id).status == "running", (
        "a rolled-back publication must not have completed the run"
    )
    assert _assistant(session, second.id) == [], (
        "a completed status must never exist without its message"
    )


def raise_if_unbalanced() -> None:
    """A publication step that fails after the status was written."""
    raise ValueError("the command failed after recording")
