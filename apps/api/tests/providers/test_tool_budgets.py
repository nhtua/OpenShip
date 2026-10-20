"""Model and tool work consume one durable per-run budget."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfig,
    LoopLimits,
)
from src.openship.chat.models import Conversation
from src.openship.providers.attempts import DatabaseAttemptRecorder
from src.openship.providers.contracts import (
    ChatMessage,
    RunLimits,
    ToolCall,
    pin_profile,
)
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Event, ProviderAttempt, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.transcript import append_transcript
from src.openship.workspace.models import Project
from tests.conftest import TestSessionLocal
from tests.providers.conftest import qualified_profile


def _profile():
    return pin_profile(
        qualified_profile(
            capabilities={
                "streaming": True,
                "usage_reporting": True,
                "context_tokens": 32768,
                "max_output_tokens": 4096,
                "token_counting": False,
                "tool_calls": True,
                "structured_output": False,
                "attachment_types": [],
                "parallel_tool_calls": False,
            }
        )
    )


def _execution(*, tool_limit=2, model_turn_limit=3, attempt_limit=3, output_limit=12000):
    refs = tuple(
        CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        for manifest in list_capability_manifests()
    )
    return ExecutionConfig(
        graph_kind="chat",
        graph_version=2,
        allowed_capabilities=refs,
        loop_limits=LoopLimits(
            deadline_seconds=120,
            max_tool_calls=tool_limit,
            max_model_turns=model_turn_limit,
            max_generation_attempts=attempt_limit,
            max_output_tokens_per_request=4000,
            max_total_output_tokens=output_limit,
        ),
    )


def _running_run(execution):
    suffix = uuid.uuid4().hex
    with TestSessionLocal() as db:
        user = User(
            username=f"budget_{suffix[:12]}",
            email=f"budget_{suffix}@example.test",
            password_hash="test-hash",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Budget")
        db.add(project)
        db.flush()
        conversation = Conversation(user_id=user.id, project_id=project.id, title="Budget")
        db.add(conversation)
        db.flush()
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            client_request_id=str(uuid.uuid4()),
            status="running",
            kind="chat",
            graph_version=2,
            fence=1,
            execution_configuration=execution.model_dump_json(),
        )
        db.add(run)
        db.flush()
        lease_until = datetime.now(UTC) + timedelta(minutes=5)
        db.add(
            RunJob(
                run_id=run.id,
                fence=1,
                owner_id="budget-worker",
                lease_until=lease_until,
            )
        )
        db.commit()
        return run.id, user.id, project.id, conversation.id, lease_until


def _budget(execution, run_id, lease_until, user_id, *, limits=None):
    from src.openship.runs.budget import RunBudget

    claim = Claim(
        run_id=run_id,
        fence=1,
        lease_until=lease_until,
        worker_id="budget-worker",
        user_id=user_id,
    )
    return claim, RunBudget(
        TestSessionLocal, claim, limits or RunLimits(max_attempts=5), execution
    )


def test_generation_reservations_are_durable_and_share_attempt_output_limits():
    execution = _execution(attempt_limit=3)
    run_id, user_id, _project_id, _conversation_id, lease_until = _running_run(execution)
    _claim, budget = _budget(execution, run_id, lease_until, user_id)
    profile = _profile()

    reservations = [
        budget.reserve_generation(
            profile,
            reason="initial" if turn == 0 else "retry",
            model_turn_ordinal=turn,
            requested_output_tokens=4000,
            reserved_tokens=None,
            reserved_cost=None,
        )
        for turn in range(3)
    ]

    assert len({item.attempt_id for item in reservations}) == 3
    with TestSessionLocal() as db:
        attempts = db.query(ProviderAttempt).filter_by(run_id=run_id).order_by(
            ProviderAttempt.ordinal
        ).all()
        started = db.query(Event).filter_by(run_id=run_id, type="model.attempt.started").count()
    assert [(row.model_turn_ordinal, row.reserved_output_tokens) for row in attempts] == [
        (0, 4000),
        (1, 4000),
        (2, 4000),
    ]
    assert started == 3

    with pytest.raises(ProviderError) as refused:
        budget.reserve_generation(
            profile,
            reason="initial",
            model_turn_ordinal=3,
            requested_output_tokens=1,
            reserved_tokens=None,
            reserved_cost=None,
        )
    assert refused.value.code == "budget_exceeded"


def test_total_token_and_cost_reservations_are_cumulative_across_turns():
    execution = _execution(attempt_limit=4)
    profile = _profile()

    token_run_id, token_user_id, _project_id, _conversation_id, token_lease = _running_run(execution)
    _token_claim, token_budget = _budget(
        execution,
        token_run_id,
        token_lease,
        token_user_id,
        limits=RunLimits(max_attempts=4, max_total_tokens=100),
    )
    token_budget.reserve_generation(
        profile,
        reason="initial",
        model_turn_ordinal=0,
        requested_output_tokens=40,
        reserved_tokens=60,
        reserved_cost=None,
    )
    with pytest.raises(ProviderError) as token_refused:
        token_budget.reserve_generation(
            profile,
            reason="initial",
            model_turn_ordinal=1,
            requested_output_tokens=40,
            reserved_tokens=50,
            reserved_cost=None,
        )
    assert token_refused.value.code == "budget_exceeded"

    cost_run_id, cost_user_id, _project_id, _conversation_id, cost_lease = _running_run(execution)
    _cost_claim, cost_budget = _budget(
        execution,
        cost_run_id,
        cost_lease,
        cost_user_id,
        limits=RunLimits(max_attempts=4, max_cost_usd=Decimal("1.00")),
    )
    cost_budget.reserve_generation(
        profile,
        reason="initial",
        model_turn_ordinal=0,
        requested_output_tokens=40,
        reserved_tokens=None,
        reserved_cost=Decimal("0.60"),
    )
    with pytest.raises(ProviderError) as cost_refused:
        cost_budget.reserve_generation(
            profile,
            reason="initial",
            model_turn_ordinal=1,
            requested_output_tokens=40,
            reserved_tokens=None,
            reserved_cost=Decimal("0.50"),
        )
    assert cost_refused.value.code == "budget_exceeded"


def test_missing_usage_does_not_refund_reserved_output():
    execution = _execution(attempt_limit=4, output_limit=8000)
    run_id, user_id, _project_id, _conversation_id, lease_until = _running_run(execution)
    _claim, budget = _budget(execution, run_id, lease_until, user_id)
    recorder = DatabaseAttemptRecorder(TestSessionLocal, run_id, 1, commit_each=True)
    profile = _profile()

    first = budget.reserve_generation(
        profile,
        reason="initial",
        model_turn_ordinal=0,
        requested_output_tokens=4000,
        reserved_tokens=None,
        reserved_cost=None,
    )
    recorder.finish(first.attempt_id, "failed", usage=None, error_code="provider_unavailable")
    second = budget.reserve_generation(
        profile,
        reason="retry",
        model_turn_ordinal=0,
        requested_output_tokens=4000,
        reserved_tokens=None,
        reserved_cost=None,
    )
    recorder.finish(second.attempt_id, "failed", usage=None, error_code="provider_unavailable")

    with pytest.raises(ProviderError) as refused:
        budget.reserve_generation(
            profile,
            reason="initial",
            model_turn_ordinal=1,
            requested_output_tokens=1,
            reserved_tokens=None,
            reserved_cost=None,
        )
    assert refused.value.code == "budget_exceeded"


def test_execution_deadline_is_created_once_and_never_extended_between_turns():
    execution = _execution()
    run_id, user_id, _project_id, _conversation_id, lease_until = _running_run(execution)
    _claim, budget = _budget(execution, run_id, lease_until, user_id)
    profile = _profile()

    budget.reserve_generation(
        profile,
        reason="initial",
        model_turn_ordinal=0,
        requested_output_tokens=4000,
        reserved_tokens=None,
        reserved_cost=None,
    )
    with TestSessionLocal() as db:
        first_deadline = db.get(Run, run_id).execution_deadline_at
    budget.reserve_generation(
        profile,
        reason="initial",
        model_turn_ordinal=1,
        requested_output_tokens=4000,
        reserved_tokens=None,
        reserved_cost=None,
    )

    with TestSessionLocal() as db:
        run = db.get(Run, run_id)
        assert run.execution_deadline_at == first_deadline
        assert run.execution_started_at is not None
        assert run.execution_deadline_at - run.execution_started_at == timedelta(seconds=120)


def test_tool_admission_counts_transcript_ids_across_sequential_turns():
    execution = _execution(tool_limit=1)
    run_id, user_id, _project_id, _conversation_id, lease_until = _running_run(execution)
    claim, budget = _budget(execution, run_id, lease_until, user_id)
    first_ref = execution.allowed_capabilities[0]
    first = ToolCall(
        provider_call_id="provider-call-0",
        name=first_ref.id,
        arguments={"query": "first query"},
    )
    second_ref = execution.allowed_capabilities[1]
    second = ToolCall(
        provider_call_id="provider-call-1",
        name=second_ref.id,
        arguments={"query": "second query"},
    )
    with TestSessionLocal() as db:
        append_transcript(
            db,
            claim,
            0,
            ChatMessage(role="assistant", content="", tool_calls=(first,)),
        )
        db.commit()
    logical_id = "model:0:call:0"
    budget.admit_tool(logical_id)
    budget.admit_tool(logical_id)

    with TestSessionLocal() as db:
        append_transcript(
            db,
            claim,
            0,
            ChatMessage(
                role="tool",
                name=first.name,
                tool_call_id=first.provider_call_id,
                content='{"status":"succeeded"}',
            ),
        )
        db.commit()
        append_transcript(
            db,
            claim,
            1,
            ChatMessage(role="assistant", content="", tool_calls=(second,)),
        )
        db.commit()

    with pytest.raises(ProviderError) as refused:
        budget.admit_tool("model:1:call:0")
    assert refused.value.code == "budget_exceeded"


def test_context_admission_counts_function_schemas_and_tool_results(monkeypatch):
    from src.openship.providers.contracts import (
        ChatMessage,
        ModelRequest,
        RunModelConfig,
        ToolDefinition,
    )
    from src.openship.providers.runner import run_model_turn
    from tests.providers.conftest import (
        FakeRecorder,
        control,
        serve_requests,
    )

    profile = _profile()
    manifest = list_capability_manifests()[0]
    definition = ToolDefinition(
        name=manifest.id,
        description="Search the web.",
        parameters=manifest.input_schema,
    )
    request = ModelRequest(
        messages=(
            ChatMessage(role="user", content="search"),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=(ToolCall(
                    provider_call_id="provider-call",
                    name=manifest.id,
                    arguments={"query": "search"},
                ),),
            ),
            ChatMessage(role="tool", name=manifest.id, tool_call_id="provider-call", content="result"),
        ),
        tools=(definition,),
        max_output_tokens=16,
    )
    transport = serve_requests(monkeypatch, [{"chunks": []}])
    config = RunModelConfig(
        schema_version=1,
        primary=profile,
        limits=RunLimits(max_output_tokens=16, max_context_bytes=180),
        binding_origin="submission",
    )

    with pytest.raises(ProviderError) as refused:
        import asyncio

        asyncio.run(run_model_turn(request, config, control(), FakeRecorder()))

    assert refused.value.code == "context_limit_exceeded"
    assert transport.requests == 0
