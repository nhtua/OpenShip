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
from src.openship.events.retention import prune_events
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
from src.openship.runs.models import Event, ProviderAttempt, Run, RunJob
from src.openship.runs.queue import claim_next, finalize, renew
from src.openship.runs.schemas import project_run_response
from src.openship.runs.service import bind_run_model_config, load_run_model_config
from src.openship.runs.worker import Worker

from .conftest import (
    FIXTURE_MODEL,
    SENTINEL_CREDENTIAL,
    TestSessionLocal,
    configure_profiles,
    declare_two_profiles,
    profile_spec,
)
from .providers.conftest import (
    BlockingTransport,
    StreamTransport,
    error_fixture,
    make_adapter,
    sdk_chunk,
)

# --- The installation, a queued run, and one worker job ----------------------------

#: A queue order the fixture controls. A case's job is always the oldest
#: available one, so the claim that follows is this case's own run and a case
#: never depends on another case's leftovers.
_QUEUE_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def _two_destinations(monkeypatch, aliases=()) -> None:
    """Two profiles that serve different models, so following today's default is visible.

    The installation's default is the second one; runs pin the first. Anything
    that reads today's settings instead of a run's snapshot serves the wrong
    model. The primary can also qualify an alias, which is what a provider
    that answers under a name the operator vouched for looks like.
    """
    configure_profiles(
        monkeypatch,
        [
            profile_spec(
                "primary-fixture", allowed_fallbacks=["secondary-fixture"], aliases=aliases
            ),
            {**profile_spec("secondary-fixture"), "model": "changed-after-submission"},
        ],
        "secondary-fixture",
    )


def _conversation(client: TestClient, authorized_client: dict) -> str:
    """An owned conversation to submit turns into."""
    response = client.post("/api/conversations", json={"title": "Fixture"}, headers=authorized_client)
    assert response.status_code == 201, f"conversation creation failed: {response.text}"
    return response.json()["id"]


def _queued_turn(
    client: TestClient,
    authorized_client: dict,
    monkeypatch,
    order: int = 0,
    aliases: tuple = (),
    fallbacks: tuple = (),
) -> Run:
    """A queued run pinned to the first profile, on its own conversation.

    Its job is made the oldest available one, so the claim that follows is
    this run's: a case tests the worker, not another case's leftovers. A run
    can also be asked with fallbacks it may move to - a fallback is chosen
    only from the list the request named, so a case about one has to name it.
    """
    _two_destinations(monkeypatch, aliases=aliases)
    conversation_id = _conversation(client, authorized_client)
    request = {
        "content": "What ran?",
        "client_request_id": str(uuid.uuid4()),
        "provider_profile_id": "primary-fixture",
    }
    if fallbacks:
        request["fallback_profile_ids"] = list(fallbacks)
    response = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=request,
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


class _RefusedThenAnswering(StreamTransport):
    """The first request is refused; the next answers.

    A fallback is only chosen after a request that did not answer, so a case
    about the move needs a destination that fails once and then does. The
    refusal is the SDK's own rate-limit failure, not a stand-in for one.
    """

    def __init__(self, chunks) -> None:
        super().__init__(list(chunks))
        self._refusals = 1

    async def create(self, **kwargs):
        if self._refusals:
            self._refusals -= 1
            raise error_fixture("openai", "rate_limited")
        return await super().create(**kwargs)


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


def test_a_cancel_moves_the_fence_a_worker_renews_against(
    client, authorized_client, monkeypatch
):
    """The ask takes ownership away from a worker that would be mid-request.

    The break this catches: a cancel that moves only the run's fence. A
    running worker renews against its job's, so the desync would leave the
    blocked provider read consuming until the run deadline - and the two
    claims this repo makes about cancellation would both be unmet. The
    composition is settled by the cases beside this one: a fence that moved
    stops a worker and publishes nothing, and a stop signal closes a blocked
    read. What is shown here is that the ask moves that fence.
    """
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    response = client.post(f"/api/runs/{run.id}/cancel", headers=authorized_client)
    assert response.status_code == 200, f"the owner's ask must be accepted: {response.text}"

    session = TestSessionLocal()
    assert session.get(Run, run.id).status == "cancelled", "the ask is the run's end"
    job = session.query(RunJob).filter(RunJob.run_id == run.id).one()
    assert job.fence != claim.fence, (
        "a worker mid-request must not still hold a lease it can renew"
    )
    assert not renew(session, run.id, claim.fence, lease_seconds=30), (
        "the lease the worker was about to renew is no longer its own"
    )


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
    assert [attempt.outcome for attempt in _answers(TestSessionLocal(), run.id)] == ["cancelled"], (
        "the admitted request's interrupted outcome remains auditable after the worker loses its lease"
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
    response = client.get(f"/api/runs/{run.id}", headers=authorized_client)
    assert response.status_code == 200
    assert response.json()["model"] is None
    snapshot = client.get(
        f"/api/conversations/{run.conversation_id}/snapshot", headers=authorized_client
    )
    assert snapshot.status_code == 200
    projected = next(item for item in snapshot.json()["runs"] if item["id"] == str(run.id))
    assert projected["model"] is None
    assert projected["error"]["code"] == "configuration_error"
    assert any(
        event["type"] == "run.failed" and event["payload"]["error_code"] == "configuration_error"
        for event in snapshot.json()["events"]
    ), "an unreadable snapshot still gets a terminal failure event"


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


def test_failure_publication_rolls_back_success_before_recording_failure(
    client, authorized_client, monkeypatch
):
    """A failure while constructing success publication cannot commit half a success."""
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()
    _serve(monkeypatch, chunks=[sdk_chunk(content="Answer", finish_reason="stop")])

    from src.openship.events import service as event_service

    original_append = event_service.append_event

    def reject_success(db, conversation_id, run_id, event_type, payload, actor_id=None):
        if event_type == "run.succeeded":
            raise RuntimeError("terminal success event construction failed")
        return original_append(db, conversation_id, run_id, event_type, payload, actor_id)

    monkeypatch.setattr(event_service, "append_event", reject_success)
    _process(claim)

    session = TestSessionLocal()
    failed = session.get(Run, run.id)
    assert failed.status == "failed"
    assert failed.error_code == "internal_error"
    assert _assistant(session, run.id) == []
    event_types = [event.type for event in session.query(Event).filter(Event.run_id == run.id)]
    assert "run.failed" in event_types
    assert "run.succeeded" not in event_types


def test_worker_attempt_admission_survives_process_exit(client, authorized_client, monkeypatch):
    """The actual worker commits admission before graph/provider work begins."""
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()
    from src.openship.runs import worker as worker_module

    class ProcessExitGraph:
        def __init__(self, invocation_factory):
            self.invocation_factory = invocation_factory

        def invoke(self, _state, config=None):
            _control, recorder = self.invocation_factory()
            recorder.start(pin_profile(profile_spec("primary-fixture")), reason="initial")
            raise SystemExit("simulated process exit after request admission")

    monkeypatch.setattr(
        worker_module,
        "build_chat_graph",
        lambda _saver, invocation_factory: ProcessExitGraph(invocation_factory),
    )
    monkeypatch.setattr(Worker, "_supervise", lambda *_args: None)

    with pytest.raises(SystemExit, match="simulated process exit"):
        _process(claim)

    session = TestSessionLocal()
    attempt = _answers(session, run.id)
    assert len(attempt) == 1
    assert attempt[0].outcome == "started"
    assert session.get(Run, run.id).provider_started_at is not None


@pytest.mark.parametrize(
    ("finish_reason", "error_code"),
    [("length", "output_limit_exceeded"), ("refusal", "model_refused")],
    ids=("cut_off_at_the_limit", "refused_by_the_model"),
)
def test_an_incomplete_answer_is_not_a_completed_one(
    client, authorized_client, monkeypatch, finish_reason, error_code
):
    """A turn that did not end cleanly publishes a failure, never an answer.

    The break this catches: a worker that reads the graph's own result and
    publishes whatever text came back, so an answer cut off at the output
    limit or refused by the model enters history as a completed assistant
    message. The plan says partial text stays an internal result and never
    enters history - which is why the guard sits in the model step itself,
    where no caller can bypass it.
    """
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    _serve(
        monkeypatch,
        chunks=[sdk_chunk(content="Text that never finished.", finish_reason=finish_reason)],
    )

    _process(claim)

    session = TestSessionLocal()
    stopped = session.get(Run, run.id)
    assert stopped.status == "failed", (
        f"an answer ending {finish_reason!r} is not a completed run"
    )
    assert stopped.error_code == error_code, (
        "the failure names why the answer is not there, in the same words "
        "the browser reads"
    )
    assert _assistant(session, run.id) == [], (
        "partial text never enters history as a completed assistant answer"
    )
    assert _answers(session, run.id), (
        "the request was made, so its attempt is still recorded"
    )


def test_the_requests_a_turn_made_are_stated_in_the_record(
    client, authorized_client, monkeypatch
):
    """The bounded requests a run made are part of the durable record.

    The break this catches: an attempt that exists only as a row. A reader
    following a run that asked more than once could not tell which request
    opened and which closed, or that a degraded answer came from a move at
    all - and the row is not what a reader reads.
    """
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    _serve(
        monkeypatch,
        chunks=[sdk_chunk(content="An answer.", finish_reason="stop")],
    )

    _process(claim)

    session = TestSessionLocal()
    stated = {event.type: event for event in session.query(Event).filter(Event.run_id == run.id)}
    assert {"model.attempt.started", "model.attempt.finished"} <= set(stated), (
        "a run that asked is readable as having asked"
    )
    assert "run.succeeded" in stated

    opened = json.loads(stated["model.attempt.started"].payload)
    assert opened["profile_id"] == "primary-fixture"
    assert opened["adapter"] == "openai"
    assert opened["requested_model"] == FIXTURE_MODEL
    assert opened["reason"] == "initial"

    closed = json.loads(stated["model.attempt.finished"].payload)
    assert closed["outcome"] == "succeeded"
    assert closed["ordinal"] == opened["ordinal"], "the pair states one request"

    for facts in (opened, closed):
        text = json.dumps(facts)
        assert SENTINEL_CREDENTIAL not in text, "a record never carries a credential value"
        assert "credential_ref" not in text, "not even the binding it used"
        assert "content" not in facts, "no prompt and no answer belongs here"

    terminal = json.loads(stated["run.succeeded"].payload)
    assert terminal["model"]["profile_id"] == "primary-fixture", (
        "a terminal event can be read on its own, after the events beside it "
        "were pruned"
    )


def test_a_pruned_record_still_says_what_the_run_was(
    client, authorized_client, monkeypatch
):
    """Retention takes events; it cannot take a run's own record."""
    run = _queued_turn(client, authorized_client, monkeypatch)
    claim = _claim()

    _serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="An answer.", finish_reason="stop"),
            sdk_chunk(choices=False, usage=(5, 2, 7)),
        ],
    )

    _process(claim)

    session = TestSessionLocal()
    conversation = session.query(Conversation).filter(
        Conversation.id == run.conversation_id
    ).one()
    pruned = prune_events(session, conversation.id, keep_count=1)
    assert pruned > 0, "this turn wrote more than the keep window allows"

    read = project_run_response(session.get(Run, run.id), _answers(session, run.id))
    assert read.model.profile_id == "primary-fixture", (
        "the run still says what it was pinned to"
    )
    assert read.model.actual_model == FIXTURE_MODEL, (
        "and what answered, from the record it keeps - not from an event"
    )
    assert read.usage.total_tokens == 7, "the counts a provider reported survive too"


def test_a_fallback_states_the_move_it_made(client, authorized_client, monkeypatch):
    """Choosing another profile is stated as its own fact, not inferred."""
    run = _queued_turn(
        client, authorized_client, monkeypatch, fallbacks=("secondary-fixture",)
    )
    claim = _claim()

    transport = _RefusedThenAnswering(
        [sdk_chunk(
            content="Answered elsewhere.",
            finish_reason="stop",
            model="changed-after-submission",
        )]
    )
    monkeypatch.setitem(
        _adapter_factories, "openai", lambda _profile: make_adapter("openai", transport)
    )

    _process(claim)

    session = TestSessionLocal()
    selected = [
        event
        for event in session.query(Event).filter(Event.run_id == run.id)
        if event.type == "model.fallback.selected"
    ]
    assert len(selected) == 1, "one move, stated once"
    facts = json.loads(selected[0].payload)
    assert facts["from_profile_id"] == "primary-fixture"
    assert facts["to_profile_id"] == "secondary-fixture", (
        "the pair a reader needs is which profile stopped and which began"
    )
    assert facts["to_model"] == "changed-after-submission"

    run_read = client.get(f"/api/runs/{run.id}", headers=authorized_client).json()
    assert run_read["model"]["profile_id"] == "primary-fixture"
    assert run_read["model"]["serving_profile_id"] == "secondary-fixture"
    assert run_read["model"]["serving_adapter"] == "openai"
    snapshot = client.get(
        f"/api/conversations/{run.conversation_id}/snapshot", headers=authorized_client
    ).json()
    summarized = next(item for item in snapshot["runs"] if item["id"] == str(run.id))
    assert summarized["model"]["serving_profile_id"] == "secondary-fixture"


def test_a_served_alias_is_reported_as_the_model_that_answered(
    client, authorized_client, monkeypatch
):
    """Requested and actual stay separate in what a browser reads.

    The break this catches: a run summary that reports only the pinned name,
    so a provider that answered under a name the operator qualified is
    indistinguishable from one that answered as asked. The name that is
    reported comes from the attempt the provider made, never from the
    snapshot - which is where only the requested name is knowable.
    """
    alias = "fixture-qualified-alias"
    run = _queued_turn(client, authorized_client, monkeypatch, aliases=(alias,))
    claim = _claim()

    _serve(
        monkeypatch,
        chunks=[sdk_chunk(content="An answer.", finish_reason="stop", model=alias)],
    )

    _process(claim)

    session = TestSessionLocal()
    read = project_run_response(session.get(Run, run.id), _answers(session, run.id))
    assert read.model.requested_model == FIXTURE_MODEL, (
        "the name a run asked for is still the name it asked for"
    )
    assert read.model.actual_model == alias, (
        "the name the provider stated is what is reported, not the pinned one"
    )


def raise_if_unbalanced() -> None:
    """A publication step that fails after the status was written."""
    raise ValueError("the command failed after recording")
