"""Clarification replies remain attached until an assessment resolves them."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.openship.chat.models import Message
from src.openship.providers.contracts import ProviderTurnResult
from src.openship.runs.inbox.admission import submit_message
from src.openship.runs.inbox.contracts import MessageAdmissionRequest
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.tasks.clarification import (
    apply_clarification_assessment,
    clarification_replies,
)
from src.openship.runs.tasks.contracts import (
    ClarificationAssessment,
    TaskDecisionCommand,
)
from src.openship.runs.tasks.decisions import (
    apply_decision,
    expire_task_decisions,
    park_task_wait,
)
from src.openship.runs.tasks.models import (
    TaskDecisionCommandRecord,
    TaskDecisionRecord,
    TaskRecord,
)
from src.openship.runs.tasks.state import read_progress
from tests.conftest import TestSessionLocal
from tests.test_task_decisions import _pinned_execution
from tests.test_task_state import _identity


def test_multiple_replies_keep_request_open():
    from src.openship.runs.tasks.clarification import clarification_replies

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        decision = park_task_wait(
            db,
            claim,
            kind="clarification",
            prompt="Which source?",
            now=datetime.now(UTC),
        ).pending_decision
        db.commit()
        original_generation = (
            db.query(RunJob).filter_by(run_id=run.id).one().dispatch_generation
        )
        requests = [
            MessageAdmissionRequest(content=text, client_request_id=str(uuid.uuid4()))
            for text in ("Not sure", "Use the morning CSV")
        ]
        first = submit_message(
            db, user_id=user.id, conversation_id=conversation.id, request=requests[0]
        )
        db.commit()
        second = submit_message(
            db, user_id=user.id, conversation_id=conversation.id, request=requests[1]
        )
        replay = submit_message(
            db, user_id=user.id, conversation_id=conversation.id, request=requests[1]
        )
        db.commit()
        row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        assert first.request_id == second.request_id == decision.request_id
        assert replay.message_id == second.message_id
        assert row.state == "open"
        assert len(clarification_replies(db, decision_id=row.id)) == 2
        assert (
            db.query(RunJob).filter_by(run_id=run.id).one().dispatch_generation
            == original_generation + 1
        )
        assert (
            db.query(Outbox).filter_by(run_id=run.id, destination="command").count()
            == 1
        )


def test_reply_during_worker_claim_remains_bound():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        decision = park_task_wait(
            db, claim, kind="clarification", prompt="Which source?", now=now
        ).pending_decision
        db.commit()
        first = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="first", client_request_id=str(uuid.uuid4())
            ),
        )
        db.commit()
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        run.fence += 1
        run.status = "running"
        job.fence = run.fence
        job.owner_id = "worker"
        job.lease_until = now + timedelta(minutes=5)
        db.commit()
        second = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="second", client_request_id=str(uuid.uuid4())
            ),
        )
        assert first.request_id == second.request_id == decision.request_id
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_fresh_legacy_answer_cannot_bypass_assessment():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        decision = park_task_wait(
            db, claim, kind="clarification", prompt="Which source?", now=now
        ).pending_decision
        db.commit()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=decision.request_id,
            plan_revision=decision.plan_revision,
            expected_digest=decision.expected_digest,
            action="answer",
            answer="morning CSV",
        )
        with pytest.raises(HTTPException) as refused:
            apply_decision(
                db, principal_id=user.id, run_id=run.id, command=command, now=now
            )
        assert refused.value.status_code == 409
        assert "message" in str(refused.value.detail).lower()
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_historical_answer_replay_remains_readable():
    from src.openship.runs.tasks.decisions import _project

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        request = park_task_wait(
            db, claim, kind="clarification", prompt="Which source?", now=now
        ).pending_decision
        record = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        command = TaskDecisionCommand(
            client_request_id=uuid.uuid4(),
            request_id=request.request_id,
            plan_revision=request.plan_revision,
            expected_digest=request.expected_digest,
            action="answer",
            answer="morning CSV",
        )
        projection = _project(db, task)
        record.state = "answered"
        db.add(
            TaskDecisionCommandRecord(
                task_id=task.id,
                run_id=run.id,
                decision_id=record.id,
                client_request_id=command.client_request_id,
                request_id=command.request_id,
                action=command.action,
                plan_revision=command.plan_revision,
                expected_digest=command.expected_digest,
                answer=command.answer,
                result_json=projection.model_dump_json(),
            )
        )
        db.commit()
        replay = apply_decision(
            db, principal_id=user.id, run_id=run.id, command=command, now=now
        )
        assert replay == projection
        assert record.state == "answered"


def test_typed_yes_does_not_confirm_plan():
    from src.openship.runs.tasks.state import save_plan
    from tests.test_task_state import _plan

    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        park_task_wait(
            db,
            claim,
            kind="plan_confirmation",
            prompt="Approve?",
            now=datetime.now(UTC),
        )
        db.commit()
        reply = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="yes", client_request_id=str(uuid.uuid4())
            ),
        )
        assert reply.disposition == "queued"
        assert run.status == "waiting_plan"
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state == "open"
        )


def test_expired_clarification_does_not_claim_new_message():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        park_task_wait(db, claim, kind="clarification", prompt="Which source?", now=now)
        decision = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        decision.expires_at = now - timedelta(seconds=1)
        db.commit()
        reply = submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="CSV", client_request_id=str(uuid.uuid4())
            ),
        )
        assert reply.disposition == "queued"
        assert reply.request_id is None


def test_primary_message_uniqueness_survives_clarification_support():
    with TestSessionLocal() as db:
        _, _, conversation, run, _ = _identity(db)
        db.add(
            Message(
                conversation_id=conversation.id,
                run_id=run.id,
                role="assistant",
                content="one",
            )
        )
        db.flush()
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                Message(
                    conversation_id=conversation.id,
                    run_id=run.id,
                    role="assistant",
                    content="duplicate",
                )
            )
            db.flush()


@pytest.mark.parametrize("state", ["queued", "running"])
def test_expiry_fences_clarification_assessment_in_flight(state):
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        now = datetime.now(UTC)
        park_task_wait(db, claim, kind="clarification", prompt="Which source?", now=now)
        db.commit()
        submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="CSV", client_request_id=str(uuid.uuid4())
            ),
        )
        db.commit()
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        if state == "running":
            run.status = "running"
            job.owner_id = "worker"
            job.lease_until = now + timedelta(days=3)
        fence = run.fence
        db.commit()
        assert expire_task_decisions(db, now=now + timedelta(days=2)) >= 1
        assert run.status == "timed_out"
        assert run.fence > fence
        assert (
            db.query(TaskDecisionRecord).filter_by(run_id=run.id).one().state
            == "expired"
        )


def _claim_exact(db, run, worker: str):
    from src.openship.messaging.models import MessageTransportState
    from src.openship.runs.queue import claim_run

    job = db.query(RunJob).filter_by(run_id=run.id).one()
    transport = db.query(MessageTransportState).filter_by(id=1).one()
    transport.active = transport.execution_open = transport.admission_open = True
    db.flush()
    decision = claim_run(
        db,
        run_id=run.id,
        generation=job.dispatch_generation,
        epoch=transport.transport_epoch,
        worker_id=worker,
        lease_seconds=300,
    )
    assert decision.kind == "claimed", decision
    return decision.claim


def _planned_clarification(db, *, with_plan: bool):
    from src.openship.runs.tasks.state import save_plan
    from tests.test_task_state import _plan

    user, project, conversation, run, claim = _identity(db)
    _pinned_execution(run, user, project, conversation)
    db.flush()
    if with_plan:
        save_plan(db, claim, _plan(), expected_revision=0)
    db.commit()
    decision = park_task_wait(
        db,
        claim,
        kind="clarification",
        prompt="Which source?",
        now=datetime.now(UTC),
    ).pending_decision
    db.commit()
    submit_message(
        db,
        user_id=user.id,
        conversation_id=conversation.id,
        request=MessageAdmissionRequest(
            content="not sure yet", client_request_id=str(uuid.uuid4())
        ),
    )
    db.commit()
    row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
    assessor = _claim_exact(db, run, "assessor")
    return user, conversation, run, row, assessor, decision


def _assess(db, claim, row, assessment):
    entries = clarification_replies(db, decision_id=row.id)
    return apply_clarification_assessment(
        db,
        claim,
        decision_id=row.id,
        included_sequence=entries[-1].sequence,
        assessment=assessment,
        now=datetime.now(UTC),
    )


@pytest.mark.parametrize("with_plan", [False, True], ids=["revision-zero", "plan"])
def test_insufficient_then_sufficient(with_plan):
    with TestSessionLocal() as db:
        user, conversation, run, row, assessor, decision = _planned_clarification(
            db, with_plan=with_plan
        )
        assert _assess(
            db,
            assessor,
            row,
            ClarificationAssessment(verdict="insufficient", follow_up="Which file?"),
        )
        db.commit()
        assert row.state == "open"
        assert json.loads(row.request_json)["prompt"] == "Which file?"
        follow_up = (
            db.query(Message)
            .filter_by(run_id=run.id, message_kind="clarification_follow_up")
            .one()
        )
        assert follow_up.content == "Which file?"
        assert run.status == "waiting_input"
        from src.openship.runs.models import Event

        updated = (
            db.query(Event).filter_by(run_id=run.id, type="task.decision_updated").one()
        )
        payload = json.loads(updated.payload)
        assert payload["request_id"] == str(row.request_id)
        assert payload["message_id"] == str(follow_up.id)
        assert payload["prompt"] == "Which file?"
        from src.openship.runs.tasks.projections import read_task_summary

        assert read_task_summary(db, run.id).pending_decision.prompt == "Which file?"

        submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="the morning CSV", client_request_id=str(uuid.uuid4())
            ),
        )
        db.commit()
        second = _claim_exact(db, run, "assessor-2")
        assert len(clarification_replies(db, decision_id=row.id)) == 2, (
            "both replies stay bound to the same request"
        )
        assert _assess(db, second, row, ClarificationAssessment(verdict="sufficient"))
        db.commit()
        assert row.state == "answered"
        task = db.query(TaskRecord).filter_by(run_id=run.id).one()
        assert task.status == ("planned" if with_plan else "unknown")
        assert run.status == "running"
        assert db.query(Run).filter_by(conversation_id=conversation.id).count() == 1
        assert str(second.run_id) == str(run.id)
        assert decision.request_id == row.request_id


def test_reply_during_assessment_is_not_lost():
    with TestSessionLocal() as db:
        user, conversation, _run, row, assessor, _ = _planned_clarification(
            db, with_plan=False
        )
        first_sequence = clarification_replies(db, decision_id=row.id)[-1].sequence
        submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="actually use the afternoon CSV",
                client_request_id=str(uuid.uuid4()),
            ),
        )
        db.commit()
        assert (
            apply_clarification_assessment(
                db,
                assessor,
                decision_id=row.id,
                included_sequence=first_sequence,
                assessment=ClarificationAssessment(verdict="sufficient"),
                now=datetime.now(UTC),
            )
            is False
        )
        assert row.state == "open"
        assert len(clarification_replies(db, decision_id=row.id)) == 2


def test_expired_clarification_assessment_is_not_applied():
    with TestSessionLocal() as db:
        _user, _conversation, _run, row, assessor, _ = _planned_clarification(
            db, with_plan=False
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        assert (
            _assess(db, assessor, row, ClarificationAssessment(verdict="sufficient"))
            is False
        )
        assert row.state == "open"


def test_assessment_contract_requires_bounded_follow_up():
    with pytest.raises(ValueError):
        ClarificationAssessment(verdict="insufficient")
    with pytest.raises(ValueError):
        ClarificationAssessment(verdict="insufficient", follow_up="   ")
    with pytest.raises(ValueError):
        ClarificationAssessment(verdict="sufficient", follow_up="Which file?")


def test_assess_clarification_is_one_budgeted_turn_without_tools(monkeypatch):
    from src.openship.capabilities.contracts import decode_execution_config
    from src.openship.runs.tasks import inference
    from tests.test_provider_worker import _pinned_config

    seen = {}

    async def fake_turn(request, config, control, recorder, **kwargs):
        seen.update(request=request, config=config, **kwargs)
        return ProviderTurnResult(
            text='{"clarification": {"verdict": "sufficient"}}',
            selected_profile_id="primary-fixture",
            finish_reason="stop",
        )

    monkeypatch.setattr(inference, "run_model_turn", fake_turn)
    with TestSessionLocal() as db:
        _user, _conversation, _run, row, assessor, _ = _planned_clarification(
            db, with_plan=False
        )
        run = db.get(Run, row.run_id)
        stage = SimpleNamespace(
            claim=assessor,
            session_factory=TestSessionLocal,
            model_config=_pinned_config(),
            execution=decode_execution_config(run.execution_configuration),
            control=object(),
            recorder=object(),
            budget=object(),
            authorize_profile=None,
        )
        assessment = inference.assess_clarification(
            stage,
            read_progress(db, run.id),
            question="Which source?",
            replies=("not sure yet",),
        )
    assert assessment.verdict == "sufficient"
    assert seen["request"].tools == ()
    assert seen["request"].tool_choice == "none"
    assert seen["config"] is stage.model_config
    assert seen["budget"] is stage.budget


def test_provider_budget_error_leaves_the_request_open(monkeypatch):
    from src.openship.capabilities.contracts import decode_execution_config
    from src.openship.providers.errors import ProviderError
    from src.openship.runs.tasks import inference
    from tests.test_provider_worker import _pinned_config

    async def exhausted(*_args, **_kwargs):
        raise ProviderError("budget_exceeded")

    monkeypatch.setattr(inference, "run_model_turn", exhausted)
    with TestSessionLocal() as db:
        _user, _conversation, _run, row, assessor, _ = _planned_clarification(
            db, with_plan=False
        )
        run = db.get(Run, row.run_id)
        stage = SimpleNamespace(
            claim=assessor,
            session_factory=TestSessionLocal,
            model_config=_pinned_config(),
            execution=decode_execution_config(run.execution_configuration),
            control=object(),
            recorder=object(),
            budget=object(),
            authorize_profile=None,
        )
        with pytest.raises(ProviderError) as refused:
            inference.assess_clarification(
                stage,
                read_progress(db, run.id),
                question="Which source?",
                replies=("not sure yet",),
            )
        assert refused.value.code == "budget_exceeded"
        db.rollback()
        assert row.state == "open"
        assert len(clarification_replies(db, decision_id=row.id)) == 1


def _graph_with_open_clarification(db, monkeypatch, assess):
    """A claimed v5 run whose one open clarification already has a reply."""
    from src.openship.capabilities.contracts import decode_execution_config
    from src.openship.runs.task_graph import build_task_graph
    from tests.test_provider_worker import _pinned_config

    monkeypatch.setattr("src.openship.runs.task_graph.assess_clarification", assess)
    user, project, conversation, run, claim = _identity(db)
    _pinned_execution(run, user, project, conversation)
    db.commit()
    park_task_wait(
        db,
        claim,
        kind="clarification",
        prompt="Which source?",
        now=datetime.now(UTC),
    )
    db.commit()
    execution = decode_execution_config(run.execution_configuration)
    submit_message(
        db,
        user_id=user.id,
        conversation_id=conversation.id,
        request=MessageAdmissionRequest(
            content="not sure", client_request_id=str(uuid.uuid4())
        ),
    )
    db.commit()

    holder = {}

    def invocation_factory(_state):
        return holder["invocation"]

    graph = build_task_graph(invocation_factory=invocation_factory)
    claimed = _claim_exact(db, run, "graph")
    holder["invocation"] = SimpleNamespace(
        claim=claimed,
        session_factory=TestSessionLocal,
        control=object(),
        recorder=object(),
        budget=object(),
        authorize_profile=None,
    )
    state = {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": execution.task_context.original_intent},
        ],
        "run_id": str(run.id),
        "conversation_id": str(conversation.id),
        "model_configuration": _pinned_config().model_dump_json(),
        "execution_configuration": run.execution_configuration,
        "tool_policy_revision": run.tool_policy_revision,
    }
    return graph, state, run, conversation


def test_transient_clarification_failure_releases_claim_and_keeps_replies(monkeypatch):
    from src.openship.providers.errors import ProviderError

    def unavailable(*_args, **_kwargs):
        raise ProviderError("provider_unavailable")

    with TestSessionLocal() as db:
        graph, state, run, _conversation = _graph_with_open_clarification(
            db, monkeypatch, unavailable
        )
        fence = db.get(Run, run.id).fence
        parked = graph.invoke(state)
        assert parked["parked"] == "clarification"
        db.expire_all()
        row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        assert row.state == "open"
        assert len(clarification_replies(db, decision_id=row.id)) == 1
        run = db.get(Run, run.id)
        assert run.status == "waiting_input"
        assert run.fence > fence
        job = db.query(RunJob).filter_by(run_id=run.id).one()
        assert job.owner_id is None and job.lease_until is None


def test_hard_clarification_failure_ends_the_run_visibly(monkeypatch):
    from src.openship.providers.errors import ProviderError

    def exhausted(*_args, **_kwargs):
        raise ProviderError("budget_exceeded")

    with TestSessionLocal() as db:
        graph, state, run, _conversation = _graph_with_open_clarification(
            db, monkeypatch, exhausted
        )
        with pytest.raises(ProviderError) as refused:
            graph.invoke(state)
        assert refused.value.code == "budget_exceeded"
        db.expire_all()
        row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        assert row.state == "open"
        assert len(clarification_replies(db, decision_id=row.id)) == 1


def test_graph_assesses_clarification_before_planning(monkeypatch):
    from src.openship.capabilities.contracts import decode_execution_config
    from src.openship.runs.task_graph import build_task_graph
    from src.openship.runs.tasks.contracts import TaskAssessment
    from tests.test_provider_worker import _pinned_config

    verdicts = [
        ClarificationAssessment(verdict="insufficient", follow_up="Which file?"),
        ClarificationAssessment(verdict="sufficient"),
    ]
    seen_replies = []

    def fake_assessment(stage, progress, *, question, replies):
        seen_replies.append(replies)
        return verdicts.pop(0)

    monkeypatch.setattr(
        "src.openship.runs.task_graph.assess_clarification", fake_assessment
    )
    monkeypatch.setattr(
        "src.openship.runs.task_graph.assess_task",
        lambda *args, **kwargs: TaskAssessment(
            classification="informational",
            explanation="Answered from the conversation so far.",
        ),
    )
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.commit()
        park_task_wait(
            db,
            claim,
            kind="clarification",
            prompt="Which source?",
            now=datetime.now(UTC),
        )
        db.commit()
        execution = decode_execution_config(run.execution_configuration)
        submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="not sure", client_request_id=str(uuid.uuid4())
            ),
        )
        db.commit()

        holder = {}

        class _Invocation(SimpleNamespace):
            pass

        def invocation_factory(_state):
            return holder["invocation"]

        graph = build_task_graph(invocation_factory=invocation_factory)
        state = {
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": execution.task_context.original_intent},
            ],
            "run_id": str(run.id),
            "conversation_id": str(conversation.id),
            "model_configuration": _pinned_config().model_dump_json(),
            "execution_configuration": run.execution_configuration,
            "tool_policy_revision": run.tool_policy_revision,
        }

        first = _claim_exact(db, run, "graph-1")
        holder["invocation"] = _Invocation(
            claim=first,
            session_factory=TestSessionLocal,
            control=object(),
            recorder=object(),
            budget=object(),
            authorize_profile=None,
        )
        parked = graph.invoke(state)
        assert parked["parked"] == "clarification"
        db.expire_all()
        row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        assert row.state == "open"
        assert json.loads(row.request_json)["prompt"] == "Which file?"

        submit_message(
            db,
            user_id=user.id,
            conversation_id=conversation.id,
            request=MessageAdmissionRequest(
                content="the morning CSV", client_request_id=str(uuid.uuid4())
            ),
        )
        db.commit()
        second = _claim_exact(db, run, "graph-2")
        holder["invocation"] = _Invocation(
            claim=second,
            session_factory=TestSessionLocal,
            control=object(),
            recorder=object(),
            budget=object(),
            authorize_profile=None,
        )
        finished = graph.invoke(state)
        assert finished["finish_reason"] == "stop"
        db.expire_all()
        row = db.query(TaskDecisionRecord).filter_by(run_id=run.id).one()
        assert row.state == "answered"
        assert seen_replies == [
            ("not sure",),
            ("not sure", "the morning CSV"),
        ]
        assert db.query(Run).filter_by(conversation_id=conversation.id).count() == 1
