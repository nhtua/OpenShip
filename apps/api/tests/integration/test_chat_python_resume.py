"""Explicit durable continuation of a python-directed chat run.

The breaks these catch: a continuation that silently refetches a URL or
re-executes a sandbox command, a resume request that forks into two runs, a
restart that starts a second command while the first may still be running,
and a continuation that grants authority to today's files instead of the
captured ones.
"""

import json
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.models import Artifact
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ExecutionConfig,
    ToolInvocationContext,
)
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolOperation
from src.openship.providers.control import InvocationControl
from src.openship.runs.models import Run, RunJob, RunTranscriptRecord
from src.openship.runs.queue import Claim
from src.openship.runs.resume import resume_chat_python
from src.openship.runs.schemas import ResumeChatPythonRequest
from src.openship.sandboxes.models import WorkflowSandbox
from src.openship.tools.download import tool as download_tool
from tests.sandboxes.test_tool import Lane, Provider, enable_sandbox

PYTHON_ID = "python_execute"
DOWNLOAD_ID = "download_file"
DOWNLOAD_URL = "https://example.test/data.csv"


class ChatPythonLane(Lane):
    """A chat-v2 run pinned to the python and download capabilities."""

    def manifest(self):
        matches = [item for item in list_capability_manifests() if item.id == PYTHON_ID]
        assert matches, "the general python capability must be production-registered"
        return matches[0]

    def pin_chat(self, *, selected=None):
        """Re-pin the seeded lane to a chat graph that allows python execution."""
        manifest = next(
            item for item in list_capability_manifests() if item.id == DOWNLOAD_ID
        )
        self.download_ref = CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        self.execution = ExecutionConfig(
            graph_kind="chat",
            graph_version=2,
            allowed_capabilities=(self.ref, self.download_ref),
            selected_artifacts=tuple(selected) if selected else (),
        )
        self.run.kind = "chat"
        self.run.graph_version = 2
        self.run.execution_configuration = self.execution.model_dump_json()
        self.db.commit()

    def context_for(self, run_id, claim) -> ToolInvocationContext:
        return ToolInvocationContext(
            authorization=AuthorizationContext(
                principal_id=self.user.id,
                project_id=self.project.id,
                conversation_id=self.conversation.id,
                action="execute_tool",
                target_id=str(run_id),
            ),
            execution=self.execution,
            credential_resolver=lambda _name: None,
            artifact_store=self.store,
            session_factory=SessionLocal,
            claim=claim,
        )


def _lane(monkeypatch, tmp_path):
    """Chat lane with committed download then python ops, transcript and state."""
    enable_sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", f"{PYTHON_ID},{DOWNLOAD_ID}")
    lane = ChatPythonLane(tmp_path)
    lane.pin_chat()

    fetches: list[str] = []

    async def receive(url, control, write):
        fetches.append(url)
        write(b"same-run-input\n")

    monkeypatch.setattr(download_tool, "download_stream", receive)
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"stdout","text":"same-run-input\\n"}\n{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: []
    provider.install(monkeypatch)
    gateway = ToolGateway(lane.context().session_factory)
    return lane, gateway, provider, fetches


async def _execute_ops(lane, gateway, claim, context, *, lost=False):
    """Run the download then python tool calls the way the agent graph does."""
    download = await gateway.execute_tool(
        claim=claim,
        context=context,
        logical_call_id="model:0:call:0",
        ref=lane.download_ref,
        arguments={"url": DOWNLOAD_URL},
        inputs=(),
        control=InvocationControl(),
    )
    assert download.status == "succeeded", download.error
    python = await gateway.execute_tool(
        claim=claim,
        context=context,
        logical_call_id="model:1:call:0",
        ref=lane.ref,
        arguments={
            "source": "print(open('/inputs/data.csv').read())\n",
            "input_ids": {"data.csv": str(download.artifacts[0].id)},
            "parameters": {},
            "output_media": {},
        },
        inputs=(),
        control=InvocationControl(),
    )
    if not lost:
        assert python.status == "succeeded", python.error
    return download, python


@pytest.mark.asyncio
async def test_other_principal_cannot_resume(monkeypatch, tmp_path):
    lane, gateway, *_ = _lane(monkeypatch, tmp_path)
    await _execute_ops(lane, gateway, lane.claim, lane.context())
    _finalize(lane)
    foreign = User(
        username=f"foreign_{_uuid.uuid4().hex[:8]}",
        email=f"foreign-{_uuid.uuid4().hex}@example.test",
        password_hash="test-hash",
    )
    lane.db.add(foreign)
    lane.db.commit()
    with pytest.raises(HTTPException) as refused:
        _resume(lane, "last_step", _uuid.uuid4(), principal_id=foreign.id)
    assert refused.value.status_code == 404
    assert refused.value.detail["error"]["code"] == "run_not_found"
    assert lane.db.query(Run).filter_by(conversation_id=lane.conversation.id).count() == 1


@pytest.mark.asyncio
async def test_active_run_blocks_resume(monkeypatch, tmp_path):
    lane, gateway, *_ = _lane(monkeypatch, tmp_path)
    await _execute_ops(lane, gateway, lane.claim, lane.context())
    with pytest.raises(HTTPException) as refused:
        _resume(lane, "last_step", _uuid.uuid4())
    assert refused.value.status_code == 409
    assert refused.value.detail["error"]["code"] == "run_already_active"
    assert lane.run.status == "running"
    assert lane.db.query(Run).filter_by(conversation_id=lane.conversation.id).count() == 1


def _resume(lane, mode, request_id, *, principal_id=None):
    """Admit one explicit continuation through the ordinary queue/outbox."""
    db = SessionLocal()
    try:
        run = resume_chat_python(
            db,
            principal_id=principal_id or lane.user.id,
            run_id=lane.run.id,
            command=ResumeChatPythonRequest(mode=mode, client_request_id=request_id),
        )
        admitted = SimpleNamespace(
            id=run.id,
            kind=run.kind,
            graph_version=run.graph_version,
            status=run.status,
            client_request_id=run.client_request_id,
            command_digest=run.command_digest,
            execution_configuration=run.execution_configuration,
        )
        db.commit()
        return admitted
    finally:
        db.close()


def _transcript(db, run_id):
    return [
        (row.ordinal, json.loads(row.message_json)["role"], row.logical_call_id)
        for row in db.query(RunTranscriptRecord)
        .filter_by(run_id=run_id)
        .order_by(RunTranscriptRecord.ordinal)
        .all()
    ]


def _operations(db, run_id):
    return {
        row.logical_call_id: row
        for row in db.query(ToolOperation).filter_by(run_id=run_id).all()
    }


def _seed_transcript(lane):
    """Record model turns and tool replies the way the fenced writer would."""
    turns = (("download_file", "model:0:call:0"), ("python_execute", "model:1:call:0"))
    ordinal = 0
    for turn_ordinal, (tool_name, call_id) in enumerate(turns):
        for role in ("assistant", "tool"):
            message = {"role": role, "content": "recorded step"}
            if role == "tool":
                message["name"] = tool_name
                message["tool_call_id"] = call_id
            lane.db.add(
                RunTranscriptRecord(
                    run_id=lane.run.id,
                    ordinal=ordinal,
                    model_turn_ordinal=turn_ordinal,
                    message_json=json.dumps(message),
                    logical_call_ids_json=json.dumps(
                        [call_id] if role == "assistant" else []
                    ),
                    logical_call_id=call_id if role == "assistant" else None,
                )
            )
            ordinal += 1
    lane.db.commit()


def _finalize(lane):
    lane.run.status = "completed"
    lane.db.commit()


def _select_upload(lane, monkeypatch, tmp_path):
    """Pin a submission-selected upload into the chat execution config."""
    upload = lane.store_artifact(b"item,value\n1,2\n")
    lane.pin_chat(selected=[
        ArtifactRef(
            id=upload.id,
            sha256=upload.sha256,
            size_bytes=upload.size_bytes,
            media_type=upload.media_type,
        )
    ])
    return upload


@pytest.mark.asyncio
async def test_last_step_reuses_download_and_source(monkeypatch, tmp_path):
    lane, gateway, provider, fetches = _lane(monkeypatch, tmp_path)
    download, _python = await _execute_ops(lane, gateway, lane.claim, lane.context())
    source_artifact_id = download.artifacts[0].id
    lane.db.expire_all()
    committed = lane.db.get(Artifact, source_artifact_id)
    assert committed.status == "ready"
    _seed_transcript(lane)
    _finalize(lane)
    prior_ops = _operations(lane.db, lane.run.id)
    prior_transcript = _transcript(lane.db, lane.run.id)
    commands = len(provider.commands)
    fetch_count = len(fetches)
    assert (commands, fetch_count) == (1, 1)

    new_run = _resume(lane, "last_step", _uuid.uuid4())
    assert new_run.id != lane.run.id
    assert new_run.kind == "chat" and new_run.graph_version == 2
    prior_config = json.loads(lane.run.execution_configuration)
    new_config = json.loads(new_run.execution_configuration)
    assert new_config["graph_kind"] == prior_config["graph_kind"]
    assert new_config["graph_version"] == prior_config["graph_version"]
    assert [item["id"] for item in new_config["allowed_capabilities"]] == [
        item["id"] for item in prior_config["allowed_capabilities"]
    ]
    assert {item["id"] for item in prior_config["selected_artifacts"]} <= {
        item["id"] for item in new_config["selected_artifacts"]
    }, "the continuation never drops a pinned input"
    assert str(source_artifact_id) in new_run.execution_configuration, (
        "the committed output was re-selected instead of refetched"
    )

    # The continuation carries the recorded history and committed results.
    assert _transcript(lane.db, new_run.id) == prior_transcript
    replayed_ops = _operations(lane.db, new_run.id)
    assert set(replayed_ops) == set(prior_ops)
    for logical_call_id, row in replayed_ops.items():
        assert row.result_digest == prior_ops[logical_call_id].result_digest

    # Hand the new run a worker lease and replay exactly the same calls.
    lease = datetime.now(UTC) + timedelta(minutes=30)
    lane.db.expire_all()
    new_job = lane.db.query(RunJob).filter_by(run_id=new_run.id).one()
    new_job.fence = 1
    new_job.owner_id = "chat-worker"
    new_job.lease_until = lease
    claimed = lane.db.query(Run).filter_by(id=new_run.id).one()
    claimed.status = "running"
    claimed.fence = 1
    lane.db.commit()
    claim = Claim(
        run_id=new_run.id,
        fence=1,
        lease_until=lease,
        worker_id="chat-worker",
        user_id=lane.user.id,
    )
    context = lane.context_for(new_run.id, claim)
    replay_download, replay_python = await _execute_ops(lane, gateway, claim, context)
    assert len(fetches) == fetch_count, "the recorded URL was silently refetched"
    assert len(provider.commands) == commands, "a committed command was rerun"
    assert replay_download.status == "succeeded"
    assert replay_python.status == "succeeded"
    assert str(replay_download.artifacts[0].id) == str(source_artifact_id)


@pytest.mark.asyncio
async def test_duplicate_resume_is_one_run(monkeypatch, tmp_path):
    lane, gateway, *_ = _lane(monkeypatch, tmp_path)
    await _execute_ops(lane, gateway, lane.claim, lane.context())
    _finalize(lane)
    request_id = _uuid.uuid4()
    first = _resume(lane, "last_step", request_id)
    second = _resume(lane, "last_step", request_id)
    assert second.id == first.id
    with pytest.raises(HTTPException) as refused:
        _resume(lane, "from_beginning", request_id)
    assert refused.value.status_code == 409
    assert refused.value.detail["error"]["code"] == "client_request_id_conflict"
    runs = lane.db.query(Run).filter_by(conversation_id=lane.conversation.id).all()
    assert len(runs) == 2


@pytest.mark.asyncio
async def test_restart_does_not_redispatch_uncertain_command(monkeypatch, tmp_path):
    lane, gateway, provider, _ = _lane(monkeypatch, tmp_path)

    def lost(_body):
        raise httpx.ReadError("command response lost")

    provider.command_response = lost
    _download, python = await _execute_ops(
        lane, gateway, lane.claim, lane.context(), lost=True
    )
    assert python.status != "succeeded"
    _finalize(lane)
    pointer = lane.db.query(WorkflowSandbox).filter_by(
        project_id=lane.project.id, conversation_id=lane.conversation.id
    ).first()
    assert pointer is not None, "sandbox execution recorded its handle"
    assert pointer.command_uncertain_until is not None, (
        "a lost command keeps its dispatch reservation"
    )
    runs_before = lane.db.query(Run).count()
    with pytest.raises(HTTPException) as refused:
        _resume(lane, "last_step", _uuid.uuid4())
    assert refused.value.status_code == 409
    assert refused.value.detail["error"]["code"] == "chat_python_execution_interrupted"
    assert lane.db.query(Run).count() == runs_before, "a run was admitted anyway"
    assert len(provider.commands) == 1, "the uncertain command was redispatched"
    handles = lane.db.query(WorkflowSandbox).filter_by(
        conversation_id=lane.conversation.id
    ).count()
    assert handles == 1, "a speculative sandbox replacement was created"


@pytest.mark.asyncio
async def test_from_beginning_uses_original_captured_bytes(monkeypatch, tmp_path):
    lane, gateway, *_ = _lane(monkeypatch, tmp_path)
    upload = _select_upload(lane, monkeypatch, tmp_path)
    _download, _python = await _execute_ops(lane, gateway, lane.claim, lane.context())
    _finalize(lane)
    new_run = _resume(lane, "from_beginning", _uuid.uuid4())
    assert new_run.execution_configuration == lane.run.execution_configuration
    assert _transcript(lane.db, new_run.id) == [], "a fresh root replays no recorded steps"
    assert set(_operations(lane.db, new_run.id)) == set(), "a fresh root inherits no ops"
    assert str(upload.id) in new_run.execution_configuration


@pytest.mark.asyncio
async def test_missing_input_is_actionable(monkeypatch, tmp_path):
    lane, gateway, *_ = _lane(monkeypatch, tmp_path)
    upload = _select_upload(lane, monkeypatch, tmp_path)
    await _execute_ops(lane, gateway, lane.claim, lane.context())
    _finalize(lane)
    upload.status = "deleted"
    lane.db.commit()
    with pytest.raises(HTTPException) as refused:
        _resume(lane, "from_beginning", _uuid.uuid4())
    assert refused.value.status_code == 409
    assert refused.value.detail["error"]["code"] == "chat_python_inputs_unavailable"
    assert "select" in refused.value.detail["error"]["message"].lower()


def test_resume_rejects_chat_run_without_python_authority(monkeypatch, tmp_path):
    lane, *_ = _lane(monkeypatch, tmp_path)
    lane.run.execution_configuration = ExecutionConfig(
        graph_kind="chat",
        graph_version=2,
        allowed_capabilities=(lane.download_ref,),
    ).model_dump_json()
    lane.db.commit()
    with pytest.raises(HTTPException) as refused:
        _resume(lane, "last_step", _uuid.uuid4())
    assert refused.value.status_code == 409
    assert refused.value.detail["error"]["code"] == "chat_python_state_unavailable"


def test_resume_route_and_request_are_registered():
    from src.openship.runs.routes import router as runs_routes

    assert any(
        "resume-chat-python" in route.path for route in runs_routes.routes
    ), "the authenticated continuation route must be mounted"
    with pytest.raises(ValueError):
        ResumeChatPythonRequest.model_validate(
            {"mode": "automatic", "client_request_id": str(_uuid.uuid4())}
        )
