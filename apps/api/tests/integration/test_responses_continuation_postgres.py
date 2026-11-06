"""PostgreSQL proves a pinned Responses turn survives a crash exactly once.

The durable breaks these catch: a restart that re-asks the model for a turn it
already answered, a restart that dispatches an already-committed tool call a
second time, a saved continuation that silently belongs to another profile, and
opaque provider state leaking into the public projections.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ExecutionConfig,
    LoopLimits,
    PublishedToolResult,
    ToolInvocationContext,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.providers.attempts import DatabaseAttemptRecorder
from src.openship.providers.contracts import (
    ChatMessage,
    ProviderTurnResult,
    ResponsesContinuation,
    RunLimits,
    RunModelConfig,
    ToolCall,
    pin_profile,
)
from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError
from src.openship.runs import agent_graph
from src.openship.runs.budget import RunBudget
from src.openship.runs.dispatch import resolve_graph
from src.openship.runs.models import (
    Event,
    Outbox,
    Run,
    RunJob,
    RunTranscriptRecord,
)
from src.openship.runs.queue import Claim
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url
from tests.providers.conftest import qualified_profile

pytestmark = [pytest.mark.postgres]

LANE_DATABASE = "phase6b_responses_lane"
FIXTURE_DESTINATION = "fixture.provider.test/v1"
SEARCH_ID = "brave_web_search"


@pytest.fixture(scope="module")
def pg_sessions(tmp_path_factory):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("durable continuation needs the isolated PostgreSQL lane")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
            connection.execute(text(f'CREATE DATABASE "{LANE_DATABASE}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    ini = tmp_path_factory.mktemp("responses-continuation") / "alembic.ini"
    ini.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
sqlalchemy.url = {parsed.set(database=LANE_DATABASE).render_as_string(hide_password=False).replace('%', '%%')}
[loggers]
keys = root,sqlalchemy,alembic
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine
[logger_alembic]
level = WARN
handlers =
qualname = alembic
[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
    )
    built = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), "upgrade", "head"],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    lane_url = parsed.set(database=LANE_DATABASE).render_as_string(hide_password=False)
    engine = create_engine(lane_url, pool_size=6, max_overflow=2)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{LANE_DATABASE}"'))
        finally:
            admin.dispose()


def _responses_profile():
    return pin_profile(
        qualified_profile(
            api_version="responses",
            adapter_options={
                "adapter": "openai",
                "output_limit_parameter": "max_output_tokens",
                "reasoning_effort": "low",
            },
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
            },
        )
    )


def _execution():
    manifest = next(item for item in list_capability_manifests() if item.id == SEARCH_ID)
    execution = ExecutionConfig(
        graph_kind="chat",
        graph_version=2,
        allowed_capabilities=(
            CapabilityRef(
                id=manifest.id,
                version=manifest.version,
                manifest_digest=manifest.manifest_digest,
            ),
        ),
        loop_limits=LoopLimits(
            max_model_turns=3,
            max_tool_calls=2,
            max_generation_attempts=4,
            max_output_tokens_per_request=1024,
            max_total_output_tokens=2048,
        ),
    )
    return execution


class _CountingGateway:
    """A gateway that records dispatches and can die mid-crash."""

    def __init__(self, *, die=False):
        self.calls = []
        self.die = die

    async def execute_tool(self, **request):
        self.calls.append(request)
        if self.die:
            raise RuntimeError("the worker died while the call was in flight")
        return PublishedToolResult(
            operation_id=uuid.uuid4(),
            status="succeeded",
            result={"query": request["arguments"]["query"], "results": []},
        )


def _seed(pg_sessions, profile, execution):
    """A running chat-v2 tool run under a live worker lease."""
    suffix = uuid.uuid4().hex
    with pg_sessions() as db:
        user = User(
            username=f"responses_{suffix[:12]}",
            email=f"responses_{suffix}@example.test",
            password_hash="test-hash",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Responses continuation")
        db.add(project)
        db.flush()
        conversation = Conversation(user_id=user.id, project_id=project.id, title="Continuation")
        db.add(conversation)
        db.flush()
        run = Run(
            conversation_id=conversation.id,
            project_id=project.id,
            client_request_id=str(uuid.uuid4()),
            status="running",
            kind="chat",
            graph_version=execution.graph_version,
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
                owner_id="responses-worker",
                lease_until=lease_until,
            )
        )
        db.commit()
        claim = Claim(
            run_id=run.id,
            fence=1,
            lease_until=lease_until,
            worker_id="responses-worker",
            user_id=user.id,
        )
        return run.id, user.id, project.id, conversation.id, claim, lease_until


def _invocation(pg_sessions, claim, execution, profile, model_config, *, project_id, conversation_id):
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=claim.user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            action="execute_tool",
            target_id=str(claim.run_id),
        ),
        execution=execution,
        credential_resolver=lambda _binding: "synthetic-tool-credential",
    )
    return SimpleNamespace(
        claim=claim,
        context=context,
        control=InvocationControl(deadline=time.monotonic() + 60),
        recorder=DatabaseAttemptRecorder(pg_sessions, claim.run_id, 1, commit_each=True),
        budget=RunBudget(pg_sessions, claim, model_config.limits, execution),
        session_factory=pg_sessions,
        authorize_profile=None,
    )


def _lane(pg_sessions, monkeypatch):
    """A pinned Responses chat-v2 lane: graph, state, and one fresh restart."""
    profile = _responses_profile()
    execution = _execution()
    raw = profile.model_dump(mode="json", exclude={"limits", "profile_revision"})
    monkeypatch.setattr(settings, "model_profiles_json", json.dumps([raw]))
    monkeypatch.setattr(settings, "model_default_profile_id", profile.id)
    monkeypatch.setattr(settings, "tool_enabled_ids", SEARCH_ID)
    monkeypatch.setenv(profile.credential_ref, "synthetic-model-credential")
    run_id, _user_id, project_id, conversation_id, claim, _lease_until = _seed(
        pg_sessions, profile, execution
    )
    model_config = RunModelConfig(
        schema_version=1,
        primary=profile,
        limits=RunLimits(max_output_tokens=1024, max_attempts=4),
        binding_origin="submission",
    )
    state = {
        "run_id": str(run_id),
        "conversation_id": str(conversation_id),
        "messages": [{"role": "user", "content": "Search and explain."}],
        "model_configuration": model_config.model_dump_json(),
        "execution_configuration": execution.model_dump_json(),
    }

    def invoke(items):
        """Run the graph once: the first item is the gateway, the rest are turns."""
        gateway, *turns = items
        queue = iter(turns)

        async def fake_turn(*args, **kwargs):
            outcome = next(queue)
            invoke.log.append(kwargs["model_turn_ordinal"])
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(agent_graph, "run_model_turn", fake_turn)
        graph = resolve_graph("chat", execution.graph_version)(
            checkpointer=None,
            gateway_factory=lambda: gateway,
            invocation_factory=lambda _state: _invocation(
                pg_sessions,
                claim,
                execution,
                profile,
                model_config,
                project_id=project_id,
                conversation_id=conversation_id,
            ),
        )
        return graph.invoke(state, config={"configurable": {"thread_id": str(run_id)}})

    invoke.log = []
    return profile, model_config, state, invoke, run_id


def _saved_turn(profile, *, query="first", token="opaque-reasoning"):
    """One model turn whose answer is a Responses call plus private state."""
    item = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call-1",
        "name": SEARCH_ID,
        "arguments": json.dumps({"query": query}),
        "status": "completed",
    }
    continuation = ResponsesContinuation(
        version=1,
        protocol="openai.responses",
        profile_revision=profile.profile_revision,
        model=profile.model,
        destination=FIXTURE_DESTINATION,
        items=[{"type": "reasoning", "id": "rs_1", "encrypted_content": token}, item],
    )
    call = ToolCall(provider_call_id="call-1", name=SEARCH_ID, arguments={"query": query})
    return ProviderTurnResult(
        selected_profile_id=profile.id,
        finish_reason="tool_calls",
        tool_calls=(call,),
        continuation=continuation,
    )


def _final_turn(profile, text="done"):
    return ProviderTurnResult(
        selected_profile_id=profile.id, finish_reason="stop", text=text
    )


def test_continuation_is_persisted_before_dispatch_on_postgres(pg_sessions, monkeypatch):
    profile, _config, _state, invoke, run_id = _lane(pg_sessions, monkeypatch)
    gateway = _CountingGateway()
    invoke([gateway, _saved_turn(profile), _final_turn(profile)])
    with pg_sessions() as db:
        rows = db.query(RunTranscriptRecord).filter_by(run_id=run_id).order_by(
            RunTranscriptRecord.ordinal
        ).all()
        assert [row.ordinal for row in rows] == [0, 1, 2]
        saved = json.loads(rows[0].message_json)
        assert saved["continuation"]["items"][0]["encrypted_content"] == "opaque-reasoning"
        assert saved["continuation"]["items"][1]["call_id"] == "call-1"
        assert rows[0].logical_call_ids_json == json.dumps(["model:0:call:0"], separators=(",", ":"))
    assert invoke.log == [0, 1]


def test_restart_after_a_saved_call_reuses_it_without_reasking_the_model(pg_sessions, monkeypatch):
    profile, _config, _state, invoke, run_id = _lane(pg_sessions, monkeypatch)
    crashed = _CountingGateway(die=True)
    with pytest.raises(ProviderError):
        invoke([crashed, _saved_turn(profile)])
    restarted = _CountingGateway()
    result = invoke([restarted, _final_turn(profile, "recovered")])
    assert result["assistant_response"] == "recovered"
    assert len(restarted.calls) == 1
    assert restarted.calls[0]["logical_call_id"] == "model:0:call:0"
    with pg_sessions() as db:
        rows = db.query(RunTranscriptRecord).filter_by(run_id=run_id).order_by(
            RunTranscriptRecord.ordinal
        ).all()
        assert [row.ordinal for row in rows] == [0, 1, 2]
        assert json.loads(rows[0].message_json)["continuation"] is not None
    assert invoke.log == [0, 1]


def test_restart_after_a_committed_result_continues_once(pg_sessions, monkeypatch):
    profile, _config, _state, invoke, run_id = _lane(pg_sessions, monkeypatch)
    gateway = _CountingGateway()
    with pytest.raises(RuntimeError):
        invoke([gateway, _saved_turn(profile), RuntimeError("died after the result")])
    assert len(gateway.calls) == 1
    result = invoke([gateway, _final_turn(profile, "continued")])
    assert result["assistant_response"] == "continued"
    assert len(gateway.calls) == 1
    with pg_sessions() as db:
        rows = db.query(RunTranscriptRecord).filter_by(run_id=run_id).order_by(
            RunTranscriptRecord.ordinal
        ).all()
        assert [row.ordinal for row in rows] == [0, 1, 2]
        assert json.loads(rows[2].message_json)["role"] == "assistant"
    assert invoke.log == [0, 1, 1]


def test_saved_final_answer_is_published_without_a_model_request(pg_sessions, monkeypatch):
    profile, _config, _state, invoke, run_id = _lane(pg_sessions, monkeypatch)
    continuation = ResponsesContinuation(
        version=1,
        protocol="openai.responses",
        profile_revision=profile.profile_revision,
        model=profile.model,
        destination=FIXTURE_DESTINATION,
        items=[
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "phase": "final_answer",
                "status": "completed",
                "content": [{"type": "output_text", "text": "already answered", "annotations": []}],
            }
        ],
    )
    with pg_sessions() as db:
        db.add(
            RunTranscriptRecord(
                run_id=run_id,
                ordinal=0,
                model_turn_ordinal=0,
                message_json=ChatMessage(
                    role="assistant", content="already answered", continuation=continuation
                ).model_dump_json(),
            )
        )
        db.commit()
    gateway = _CountingGateway()
    result = invoke([gateway])
    assert result["assistant_response"] == "already answered"
    assert not gateway.calls
    assert invoke.log == []


def test_saved_continuation_from_another_profile_is_refused(pg_sessions, monkeypatch):
    profile, _config, _state, invoke, run_id = _lane(pg_sessions, monkeypatch)
    continuation = ResponsesContinuation(
        version=1,
        protocol="openai.responses",
        profile_revision="0" * 64,
        model=profile.model,
        destination=FIXTURE_DESTINATION,
        items=[
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call-1",
                "name": SEARCH_ID,
                "arguments": json.dumps({"query": "first"}),
                "status": "completed",
            }
        ],
    )
    with pg_sessions() as db:
        db.add(
            RunTranscriptRecord(
                run_id=run_id,
                ordinal=0,
                model_turn_ordinal=0,
                message_json=ChatMessage(
                    role="assistant",
                    content="",
                    continuation=continuation,
                    tool_calls=(ToolCall(provider_call_id="call-1", name=SEARCH_ID,
                                         arguments={"query": "first"}),),
                ).model_dump_json(),
                logical_call_ids_json=json.dumps(["model:0:call:0"], separators=(",", ":")),
            )
        )
        db.commit()
    gateway = _CountingGateway()
    with pytest.raises(ProviderError):
        invoke([gateway])
    assert not gateway.calls
    assert invoke.log == []


def test_legacy_transcript_without_continuation_still_decodes(pg_sessions, monkeypatch):
    profile, _config, _state, invoke, run_id = _lane(pg_sessions, monkeypatch)
    with pg_sessions() as db:
        db.add(
            RunTranscriptRecord(
                run_id=run_id,
                ordinal=0,
                model_turn_ordinal=0,
                message_json=json.dumps(
                    {"role": "assistant", "content": "fresh answer", "tool_calls": None}
                ),
            )
        )
        db.commit()
    gateway = _CountingGateway()
    result = invoke([gateway, _final_turn(profile, "fresh answer")])
    assert result["assistant_response"] == "fresh answer"
    assert not gateway.calls
    with pg_sessions() as db:
        rows = db.query(RunTranscriptRecord).filter_by(run_id=run_id).all()
    assert len(rows) == 1
    assert invoke.log == [0]


def test_private_continuation_never_reaches_public_projections(pg_sessions, monkeypatch):
    profile, _config, _state, invoke, run_id = _lane(pg_sessions, monkeypatch)
    gateway = _CountingGateway()
    result = invoke([gateway, _saved_turn(profile), _final_turn(profile, "published")])
    assert result["assistant_response"] == "published"
    with pg_sessions() as db:
        events = db.query(Event).filter_by(run_id=run_id).all()
        outgoing = db.query(Outbox).filter_by(run_id=run_id).all()
    public = json.dumps(
        [{"type": item.type, "payload": item.payload} for item in events]
        + [{"type": item.type, "payload": item.payload} for item in outgoing]
    )
    assert "opaque-reasoning" not in public
    assert "encrypted_content" not in public
    assert invoke.log == [0, 1]
