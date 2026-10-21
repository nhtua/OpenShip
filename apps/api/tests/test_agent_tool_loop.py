"""The durable chat graph runs authorized tools between bounded model turns."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ExecutionConfig,
    LoopLimits,
    PublishedToolResult,
    ToolFailure,
    ToolInvocationContext,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.execution.admission import admit_operation
from src.openship.providers.attempts import DatabaseAttemptRecorder
from src.openship.providers.contracts import RunLimits, RunModelConfig
from src.openship.providers.control import InvocationControl
from src.openship.runs.dispatch import resolve_graph
from src.openship.runs.models import ProviderAttempt, Run, RunJob, RunTranscriptRecord
from src.openship.runs.queue import Claim, begin_execution_window
from src.openship.workspace.models import Project
from tests.conftest import TestSessionLocal
from tests.providers.conftest import (
    FIXTURE_MODEL,
    error_fixture,
    qualified_profile,
    sdk_chunk,
    sdk_tool_chunk,
    serve_requests,
)


def _tool_profile(**overrides):
    capabilities = {
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
    return qualified_profile(capabilities=capabilities, **overrides)


def _execution(*, loop_limits=None):
    manifests = list_capability_manifests()
    refs = tuple(
        CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        for manifest in manifests
    )
    return ExecutionConfig(
        graph_kind="chat",
        graph_version=2,
        allowed_capabilities=refs,
        loop_limits=loop_limits or LoopLimits(),
    )


def _running_chat_tool_run(execution):
    suffix = uuid.uuid4().hex
    with TestSessionLocal() as db:
        user = User(
            username=f"loop_{suffix[:12]}",
            email=f"loop_{suffix}@example.test",
            password_hash="test-hash",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Tool loop")
        db.add(project)
        db.flush()
        conversation = Conversation(user_id=user.id, project_id=project.id, title="Loop")
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
                owner_id="loop-worker",
                lease_until=lease_until,
            )
        )
        db.commit()
        return run.id, user.id, project.id, conversation.id, lease_until


class _CountingGateway:
    def __init__(self, *, result=None, after_execute=None):
        self.calls = []
        self.result = result
        self.after_execute = after_execute

    async def execute_tool(self, **request):
        self.calls.append(request)
        result = self.result or PublishedToolResult(
            operation_id=uuid.uuid4(),
            status="succeeded",
            result={
                "query": request["arguments"]["query"],
                "results": [{"title": "Fixture result", "url": "https://example.test/"}],
            },
        )
        if self.after_execute:
            self.after_execute(request)
        return result


def _tool_turn(call_id, query, *, name="brave_web_search", model=FIXTURE_MODEL):
    return sdk_tool_chunk(
        fragments=[
            {
                "index": 0,
                "id": call_id,
                "name": name,
                "arguments": json.dumps({"query": query, "max_results": None}, ensure_ascii=False),
            }
        ],
        finish_reason="tool_calls",
        model=model,
    )


def _invocation(
    run_id,
    user_id,
    project_id,
    conversation_id,
    lease_until,
    execution,
    profile,
    *,
    control=None,
):
    claim = Claim(
        run_id=run_id,
        fence=1,
        lease_until=lease_until,
        worker_id="loop-worker",
        user_id=user_id,
    )
    model_config = RunModelConfig(
        schema_version=1,
        primary=profile,
        limits=RunLimits(max_output_tokens=4000, max_attempts=3),
        binding_origin="submission",
    )
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            action="execute_tool",
            target_id=str(run_id),
        ),
        execution=execution,
        credential_resolver=lambda _binding: "synthetic-tool-credential",
    )
    from src.openship.runs.budget import RunBudget

    return SimpleNamespace(
        claim=claim,
        context=context,
        control=control or InvocationControl(deadline=time.monotonic() + 60),
        recorder=DatabaseAttemptRecorder(TestSessionLocal, run_id, 1, commit_each=True),
        budget=RunBudget(TestSessionLocal, claim, model_config.limits, execution),
        session_factory=TestSessionLocal,
        authorize_profile=None,
    )


class _StoppableControl(InvocationControl):
    def __init__(self):
        self.stopped = False
        super().__init__(deadline=time.monotonic() + 60, stop=lambda: self.stopped)


def _agent_case(
    monkeypatch,
    behaviors,
    *,
    execution=None,
    gateway=None,
    control=None,
    enabled_tools="brave_web_search,tavily_web_search",
):
    execution = execution or _execution()
    run_id, user_id, project_id, conversation_id, lease_until = _running_chat_tool_run(execution)
    profile = _tool_profile()
    raw_profile = profile.model_dump(mode="json", exclude={"limits", "profile_revision"})
    monkeypatch.setattr(settings, "model_profiles_json", json.dumps([raw_profile]))
    monkeypatch.setattr(settings, "model_default_profile_id", profile.id)
    monkeypatch.setenv(profile.credential_ref, "synthetic-model-credential")
    monkeypatch.setattr(settings, "tool_enabled_ids", enabled_tools)
    model_transport = serve_requests(monkeypatch, behaviors)
    selected_gateway = gateway or _CountingGateway()
    invocation = _invocation(
        run_id,
        user_id,
        project_id,
        conversation_id,
        lease_until,
        execution,
        profile,
        control=control,
    )
    graph = resolve_graph("chat", 2)(
        checkpointer=None,
        gateway_factory=lambda: selected_gateway,
        invocation_factory=lambda _state: invocation,
    )
    state = {
        "run_id": str(run_id),
        "conversation_id": str(conversation_id),
        "messages": [
            {"role": "system", "content": "Use authorized search tools when useful."},
            {"role": "user", "content": "Search and explain."},
        ],
        "model_configuration": RunModelConfig(
            schema_version=1,
            primary=profile,
            limits=RunLimits(max_output_tokens=4000, max_attempts=3),
            binding_origin="submission",
        ).model_dump_json(),
        "execution_configuration": execution.model_dump_json(),
    }
    return graph, state, model_transport, selected_gateway, run_id


def test_chat_v2_runs_two_sequential_tool_calls_and_publishes_only_final_text(monkeypatch):
    execution = _execution()
    run_id, user_id, project_id, conversation_id, lease_until = _running_chat_tool_run(execution)
    profile = _tool_profile()
    raw_profile = profile.model_dump(mode="json", exclude={"limits", "profile_revision"})
    monkeypatch.setattr(settings, "model_profiles_json", json.dumps([raw_profile]))
    monkeypatch.setattr(settings, "model_default_profile_id", profile.id)
    monkeypatch.setenv(profile.credential_ref, "synthetic-model-credential")
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search,tavily_web_search")

    model_transport = serve_requests(
        monkeypatch,
        [
            {"chunks": [_tool_turn("provider-call-1", "first query")]},
            {"chunks": [_tool_turn("provider-call-2", "second query", name="tavily_web_search")]},
            {"chunks": [sdk_chunk(content="Here are the findings.", finish_reason="stop")]},
        ],
    )
    gateway = _CountingGateway()
    invocation = _invocation(
        run_id, user_id, project_id, conversation_id, lease_until, execution, profile
    )
    graph = resolve_graph("chat", 2)(
        checkpointer=None,
        gateway_factory=lambda: gateway,
        invocation_factory=lambda _state: invocation,
    )
    state = {
        "run_id": str(run_id),
        "conversation_id": str(conversation_id),
        "messages": [
            {"role": "system", "content": "Use the authorized search tools when useful."},
            {"role": "user", "content": "Search twice and explain."},
        ],
        "model_configuration": RunModelConfig(
            schema_version=1,
            primary=profile,
            limits=RunLimits(max_output_tokens=4000, max_attempts=3),
            binding_origin="submission",
        ).model_dump_json(),
        "execution_configuration": execution.model_dump_json(),
    }

    result = graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert result["assistant_response"] == "Here are the findings."
    assert result["finish_reason"] == "stop"
    assert model_transport.requests == 3
    assert [request.get("tool_choice", "auto") for request in model_transport.seen] == [
        "auto", "auto", "none",
    ]
    assert model_transport.seen[2]["tools"] == model_transport.seen[1]["tools"]
    assert [call["logical_call_id"] for call in gateway.calls] == [
        "model:0:call:0",
        "model:1:call:0",
    ]
    assert [call["arguments"]["query"] for call in gateway.calls] == [
        "first query",
        "second query",
    ]
    with TestSessionLocal() as db:
        rows = db.query(RunTranscriptRecord).filter_by(run_id=run_id).order_by(
            RunTranscriptRecord.ordinal
        ).all()
        attempts = db.query(ProviderAttempt).filter_by(run_id=run_id).order_by(
            ProviderAttempt.ordinal
        ).all()
    assert [(row.model_turn_ordinal, json.loads(row.message_json)["role"]) for row in rows] == [
        (0, "assistant"),
        (0, "tool"),
        (1, "assistant"),
        (1, "tool"),
        (2, "assistant"),
    ]
    assert len(attempts) == 3
    assert [attempt.model_turn_ordinal for attempt in attempts] == [0, 1, 2]
    assert [attempt.reserved_output_tokens for attempt in attempts] == [4000, 4000, 4000]


def test_chat_v2_graph_is_registered_before_worker_dispatch():
    assert callable(resolve_graph("chat", 2))


def test_chat_v2_can_choose_a_workspace_read_tool_without_search_credentials(monkeypatch):
    arguments = {"limit": 10}
    local_turn = sdk_tool_chunk(
        fragments=[
            {
                "index": 0,
                "id": "workspace-call-1",
                "name": "workspace_list_files",
                "arguments": json.dumps(arguments),
            }
        ],
        finish_reason="tool_calls",
        model=FIXTURE_MODEL,
    )
    gateway = _CountingGateway(
        result=PublishedToolResult(
            operation_id=uuid.uuid4(),
            status="succeeded",
            result={"workspace_id": str(uuid.uuid4()), "items": [], "next_cursor": None},
        )
    )
    graph, state, transport, gateway, run_id = _agent_case(
        monkeypatch,
        [
            {"chunks": [local_turn]},
            {"chunks": [sdk_chunk(content="There are no saved files yet.", finish_reason="stop")]},
        ],
        gateway=gateway,
        enabled_tools="workspace_list_files,workspace_read_file",
    )

    result = graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert result["assistant_response"] == "There are no saved files yet."
    assert transport.requests == 2
    assert {tool["function"]["name"] for tool in transport.seen[0]["tools"]} == {
        "workspace_list_files",
        "workspace_read_file",
    }
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["ref"].id == "workspace_list_files"
    assert gateway.calls[0]["arguments"] == {"limit": 10, "cursor": None}
    assert {tool["function"]["name"] for tool in transport.seen[1]["tools"]} == {
        "workspace_list_files",
        "workspace_read_file",
    }


@pytest.mark.parametrize("deadline_seconds", [120, 1800])
def test_chat_v2_uses_a_persisted_execution_deadline(deadline_seconds):
    execution = _execution()
    run_id, user_id, _project_id, _conversation_id, lease_until = _running_chat_tool_run(execution)
    claim = Claim(run_id, 1, lease_until, "loop-worker", user_id)

    with TestSessionLocal() as db:
        window = begin_execution_window(
            db,
            claim,
            deadline_seconds=deadline_seconds,
            graph_thread_id=f"{run_id}-attempt-1",
        )
        run = db.get(Run, run_id)

    assert window is not None
    assert window[1] - window[0] == timedelta(seconds=deadline_seconds)
    assert run.execution_started_at is not None
    assert run.graph_thread_id == f"{run_id}-attempt-1"


def test_third_tool_call_is_refused_before_gateway_dispatch(monkeypatch):
    graph, state, transport, gateway, run_id = _agent_case(
        monkeypatch,
        [
            {"chunks": [_tool_turn("call-1", "first")]},
            {"chunks": [_tool_turn("call-2", "second")]},
            {"chunks": [_tool_turn("call-3", "third")]},
        ],
    )

    with pytest.raises(Exception) as refused:
        graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert getattr(refused.value, "code", None) == "tool_limit_exceeded"
    assert transport.requests == 3
    assert len(gateway.calls) == 2
    with TestSessionLocal() as db:
        roles = [
            json.loads(row.message_json)["role"]
            for row in db.query(RunTranscriptRecord)
            .filter_by(run_id=run_id)
            .order_by(RunTranscriptRecord.ordinal)
            .all()
        ]
    assert roles == ["assistant", "tool", "assistant", "tool"]


def test_last_model_turn_is_reserved_for_answer_even_with_more_tool_allowance(monkeypatch):
    execution = _execution(loop_limits=LoopLimits(max_tool_calls=3, max_model_turns=3))
    graph, state, transport, gateway, run_id = _agent_case(
        monkeypatch,
        [
            {"chunks": [_tool_turn("call-1", "first")]},
            {"chunks": [_tool_turn("call-2", "second")]},
            {"chunks": [_tool_turn("call-3", "third")]},
        ],
        execution=execution,
    )

    with pytest.raises(Exception) as refused:
        graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert getattr(refused.value, "code", None) == "tool_limit_exceeded"
    assert transport.requests == 3
    assert transport.seen[-1]["tool_choice"] == "none"
    assert len(gateway.calls) == 2


@pytest.mark.parametrize(
    "limits",
    [
        LoopLimits(max_tool_calls=0),
        LoopLimits(max_model_turns=1),
        LoopLimits(max_generation_attempts=1),
        LoopLimits(max_total_output_tokens=4000),
    ],
)
def test_single_remaining_request_is_used_for_an_answer(monkeypatch, limits):
    graph, state, transport, gateway, run_id = _agent_case(
        monkeypatch,
        [{"chunks": [sdk_chunk(content="Here is what I can establish.", finish_reason="stop")]}],
        execution=_execution(loop_limits=limits),
    )
    result = graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})
    assert result["assistant_response"] == "Here is what I can establish."
    assert transport.seen[0]["tool_choice"] == "none"
    assert not gateway.calls


@pytest.mark.parametrize("failures", [1, 2])
def test_retries_reserve_the_last_generation_attempt_for_an_answer(monkeypatch, failures):
    behaviors = [
        {"raises": error_fixture("openai", "unreachable")} for _ in range(failures)
    ]
    if failures == 1:
        behaviors.append({"chunks": [_tool_turn("call-1", "first")]})
    behaviors.append({"chunks": [sdk_chunk(content="Here are the findings.", finish_reason="stop")]})
    graph, state, transport, gateway, run_id = _agent_case(monkeypatch, behaviors)
    # Retry policy is real, but backoff does not need wall-clock delay here.
    monkeypatch.setattr("src.openship.providers.runner._delay", lambda *_args: 0)
    result = graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})
    assert result["assistant_response"] == "Here are the findings."
    assert transport.requests == 3
    assert transport.seen[-1]["tool_choice"] == "none"
    assert len(gateway.calls) == (1 if failures == 1 else 0)


def test_cancellation_after_published_tool_keeps_transcript_without_final_answer(monkeypatch):
    control = _StoppableControl()
    gateway = _CountingGateway(
        after_execute=lambda request: setattr(request["control"], "stopped", True)
    )
    graph, state, transport, _gateway, run_id = _agent_case(
        monkeypatch,
        [{"chunks": [_tool_turn("call-1", "first")]},
         {"chunks": [sdk_chunk(content="must not appear", finish_reason="stop")] }],
        gateway=gateway,
        control=control,
    )

    with pytest.raises(Exception) as refused:
        graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert getattr(refused.value, "code", None) == "cancelled"
    assert transport.requests == 1
    with TestSessionLocal() as db:
        entries = db.query(RunTranscriptRecord).filter_by(run_id=run_id).order_by(
            RunTranscriptRecord.ordinal
        ).all()
    assert [json.loads(row.message_json)["role"] for row in entries] == ["assistant", "tool"]


def test_disabled_tool_is_not_reoffered_after_a_completed_call(monkeypatch):
    def disable_after_call(_request):
        monkeypatch.setattr(settings, "tool_enabled_ids", "")

    gateway = _CountingGateway(after_execute=disable_after_call)
    graph, state, transport, _gateway, run_id = _agent_case(
        monkeypatch,
        [
            {"chunks": [_tool_turn("call-1", "first")]},
            {"chunks": [sdk_chunk(content="Search is complete.", finish_reason="stop")]},
        ],
        gateway=gateway,
    )

    result = graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert result["assistant_response"] == "Search is complete."
    assert transport.requests == 2
    assert "tools" not in transport.seen[-1]


def test_failed_tool_result_is_saved_for_model_explanation(monkeypatch):
    gateway = _CountingGateway(
        result=PublishedToolResult(
            operation_id=uuid.uuid4(),
            status="failed",
            error=ToolFailure(code="provider_unavailable", message="Search is temporarily unavailable."),
        )
    )
    graph, state, _transport, _gateway, run_id = _agent_case(
        monkeypatch,
        [
            {"chunks": [_tool_turn("call-1", "first")]},
            {"chunks": [sdk_chunk(content="Search is unavailable, so I could not verify that.", finish_reason="stop")]},
        ],
        gateway=gateway,
    )

    result = graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert result["assistant_response"].startswith("Search is unavailable")
    with TestSessionLocal() as db:
        saved = db.query(RunTranscriptRecord).filter_by(run_id=run_id).order_by(
            RunTranscriptRecord.ordinal
        ).all()
    assert json.loads(saved[1].message_json)["role"] == "tool"
    assert '"provider_unavailable"' in json.loads(saved[1].message_json)["content"]


def test_partial_tool_json_disconnect_is_never_dispatched_or_saved(monkeypatch):
    graph, state, transport, gateway, run_id = _agent_case(
        monkeypatch,
        [
            {
                "chunks": [
                    sdk_tool_chunk(
                        fragments=[
                            {"index": 0, "id": "call-1", "name": "brave_web_search", "arguments": '{"query":'}
                        ]
                    ),
                    error_fixture("openai", "unreachable"),
                ]
            }
        ],
    )

    with pytest.raises(Exception) as refused:
        graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert getattr(refused.value, "code", None) == "provider_unavailable"
    assert transport.requests == 1
    assert gateway.calls == []
    with TestSessionLocal() as db:
        assert db.query(RunTranscriptRecord).filter_by(run_id=run_id).count() == 0


def test_model_cannot_dispatch_a_tool_outside_the_current_allowlist(monkeypatch):
    graph, state, transport, gateway, run_id = _agent_case(
        monkeypatch,
        [{"chunks": [_tool_turn("call-1", "unauthorized", name="workspace_read_file")]}],
    )

    with pytest.raises(Exception) as refused:
        graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    assert getattr(refused.value, "code", None) == "invalid_response"
    assert transport.requests == 1
    assert gateway.calls == []
    with TestSessionLocal() as db:
        assert db.query(RunTranscriptRecord).filter_by(run_id=run_id).count() == 0


def test_gateway_admission_rejects_capability_outside_chat_snapshot():
    execution = _execution()
    run_id, user_id, project_id, conversation_id, lease_until = _running_chat_tool_run(execution)
    claim = Claim(run_id, 1, lease_until, "loop-worker", user_id)
    context = AuthorizationContext(
        principal_id=user_id,
        project_id=project_id,
        conversation_id=conversation_id,
        action="execute_tool",
        target_id=str(run_id),
    )
    outside = CapabilityRef(
        id="workspace_read_file",
        version="1.0.0",
        manifest_digest="0" * 64,
    )

    with TestSessionLocal() as db, pytest.raises(HTTPException) as refused:
        admit_operation(
            db,
            claim,
            context,
            "model:0:call:0",
            outside,
            {"query": "should be denied"},
            (),
        )

    assert refused.value.status_code == 403
    assert refused.value.detail["error"]["code"] == "unsupported_capability"
