"""Sandbox analysis journey through the real gateway, admission and publication.

OpenSandbox transport is stubbed at its HTTP boundary only (httpx MockTransport
speaking the exact proxy routes); artifact records, the store, admission, the
active slot, attempts, staging and fenced publication are production machinery.
"""


import hashlib
import json
import uuid as _uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import reserve_artifact  # noqa: F401
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ExecutionConfig,
    ToolInvocationContext,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.execution.models import ExecutionSlot, ToolAttempt, ToolOperation
from src.openship.execution.projections import project_operation  # noqa: F401
from src.openship.providers.control import InvocationControl
from src.openship.runs.models import Event, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.sandboxes import tool as sandbox_tool
from src.openship.sandboxes.client import OpenSandboxClient
from src.openship.sandboxes.contracts import (
    ExecutionRequest,
    InputFile,
    OutputSpec,
    RuntimeRequirements,
)
from src.openship.sandboxes.sessions import get_or_create_sandbox  # noqa: F401
from src.openship.workspace.models import Project

ANALYSIS_ID = "sandbox_analysis"

REQUIREMENTS = RuntimeRequirements(
    provider_id="phase6b-lane",
    profile_id="python311",
    image="localhost/openship-python:phase6b-qualified",
    dependency_identity="stdlib",
)

PARSER_SOURCE = (
    b"import os\n"
    b"from pathlib import Path\n"
    b"data = (Path(os.environ['OPENSHIP_INPUT_DIR']) / 'log.txt').read_bytes()\n"
    b"(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'report.txt').write_bytes"
    b"(data.upper())\n"
    b"import json\n"
    b"(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'summary.json')"
    b".write_text(json.dumps({'lines': data.count(b'\\\\n')}))\n"
)


def enable_sandbox(monkeypatch, tmp_path):
    """Install the opt-in sandbox profile the analysis capability expects."""
    monkeypatch.setattr(settings, "tool_enabled_ids", ANALYSIS_ID)
    monkeypatch.setattr(settings, "sandbox_enabled", True)
    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_image", REQUIREMENTS.image)
    monkeypatch.setattr(settings, "sandbox_profile_id", REQUIREMENTS.profile_id)
    monkeypatch.setattr(settings, "sandbox_dependency_identity", REQUIREMENTS.dependency_identity)
    key_file = tmp_path / "sandbox.key"
    key_file.write_bytes(b"lane-test-key\n")
    monkeypatch.setattr(settings, "sandbox_key_file", key_file)


class Lane:
    """One seeded project/conversation/tool-run with claim and real store."""

    def __init__(self, tmp_path):
        self.store = LocalArtifactStore(tmp_path / "store", _uuid.uuid4())
        self.store.initialize()
        self.db = SessionLocal()
        self.user = User(
            username=f"sandbox_{_uuid.uuid4().hex[:12]}",
            email=f"sandbox_{_uuid.uuid4().hex}@example.test",
            password_hash="test-hash",
        )
        self.db.add(self.user)
        self.db.flush()
        self.project = Project(owner_user_id=self.user.id, name="Sandbox lane")
        self.db.add(self.project)
        self.db.flush()
        self.conversation = Conversation(
            user_id=self.user.id, project_id=self.project.id, title="Sandbox lane"
        )
        self.db.add(self.conversation)
        self.db.flush()
        manifest = self.manifest()
        self.ref = CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        self.execution = ExecutionConfig(
            graph_kind="tool",
            graph_version=1,
            allowed_capabilities=(self.ref,),
        )
        self.run = Run(
            conversation_id=self.conversation.id,
            project_id=self.project.id,
            client_request_id=str(_uuid.uuid4()),
            status="running",
            kind="tool",
            graph_version=1,
            fence=1,
            execution_configuration=self.execution.model_dump_json(),
        )
        self.db.add(self.run)
        self.db.flush()
        self.job = RunJob(
            run_id=self.run.id,
            fence=1,
            owner_id="sandbox-worker",
            lease_until=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.db.add(self.job)
        self.db.commit()
        self.claim = Claim(
            run_id=self.run.id,
            fence=1,
            lease_until=self.job.lease_until,
            worker_id="sandbox-worker",
            user_id=self.user.id,
        )

    def manifest(self):
        matches = [
            item for item in list_capability_manifests() if item.id == ANALYSIS_ID
        ]
        assert matches, "the analysis capability must be production-registered"
        return matches[0]

    def context(self) -> ToolInvocationContext:
        return ToolInvocationContext(
            authorization=AuthorizationContext(
                principal_id=self.user.id,
                project_id=self.project.id,
                conversation_id=self.conversation.id,
                action="execute_tool",
                target_id=str(self.run.id),
            ),
            execution=self.execution,
            credential_resolver=lambda _name: None,
            artifact_store=self.store,
            session_factory=SessionLocal,
            claim=self.claim,
        )

    def store_artifact(self, data: bytes, *, conversation_id=None) -> Artifact:
        artifact_id = _uuid.uuid4()
        conversation_id = conversation_id or self.conversation.id
        key = (
            f"projects/{self.project.id}/conversations/{conversation_id}"
            f"/artifacts/{artifact_id}/content"
        )
        expected = ArtifactRef(
            id=artifact_id,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            media_type="text/plain",
        )
        staged = self.store.stage(
            artifact_id,
            (data,),
            expected,
            project_id=self.project.id,
            conversation_id=conversation_id,
        )
        self.store.publish_file(staged, key)
        artifact = Artifact(
            id=artifact_id,
            project_id=self.project.id,
            conversation_id=conversation_id,
            kind="upload",
            media_type="text/plain",
            display_name="lane-input.txt",
            size_bytes=len(data),
            sha256=expected.sha256,
            relative_storage_key=key,
            status="ready",
            mutability="immutable",
            source="upload",
        )
        self.db.add(artifact)
        self.db.commit()
        return artifact

    def request(self, source, inputs, outputs, *, operation_id=None):
        return ExecutionRequest(
            root_run_id=self.run.id,
            operation_id=operation_id or _uuid.uuid4(),
            requirements=REQUIREMENTS,
            source=_ref(source),
            inputs=tuple(
                InputFile(alias=alias, artifact=_ref(artifact))
                for alias, artifact in inputs
            ),
            parameters={},
            outputs=tuple(
                OutputSpec(alias=alias, media_type=media, kind="data")
                for alias, media in outputs
            ),
        )


def _ref(artifact: Artifact) -> ArtifactRef:
    return ArtifactRef(
        id=artifact.id,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        media_type=artifact.media_type,
    )


class Provider:
    """OpenSandbox stand-in speaking the exact client routes."""

    def __init__(self):
        self.created = 0
        self.commands = []
        self.listings = []
        self.downloads = []
        self.dead = False

    def on_create(self):
        self.created += 1

    def on_command(self, body):
        self.commands.append(body)

    def on_listing(self, path):
        self.listings.append(path)

    def on_download(self, path):
        self.downloads.append(path)

    def command_response(self, body):
        raise AssertionError("test must define command_response")

    def listing(self, outputs_dir):
        raise AssertionError("test must define listing")

    def download(self, path):
        raise AssertionError("test must define download")

    def route(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.dead:
            raise httpx.TransportError("provider disappeared")
        if request.method == "POST" and path == "/v1/sandboxes":
            self.on_create()
            expires = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
            return httpx.Response(
                202,
                json={
                    "id": "sbx-lane-1",
                    "expiresAt": expires,
                    "status": {"state": "Running"},
                },
            )
        if request.method == "GET" and path == "/v1/sandboxes/sbx-lane-1":
            expires = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
            return httpx.Response(
                200,
                json={
                    "id": "sbx-lane-1",
                    "expiresAt": expires,
                    "status": {"state": "Running"},
                },
            )
        if path.endswith("/proxy/44772/ping"):
            return httpx.Response(200, json={})
        if path.endswith("/directories"):
            return httpx.Response(200, json={})
        if path.endswith("/files/upload"):
            return httpx.Response(200, json={})
        if path.endswith("/command"):
            body = json.loads(request.content)
            self.on_command(body)
            return self.command_response(body)
        if path.endswith("/directories/list"):
            params = dict(request.url.params)
            self.on_listing(params["path"])
            return httpx.Response(200, json=self.listing(params["path"]))
        if path.endswith("/files/download"):
            path = dict(request.url.params)["path"]
            self.on_download(path)
            content = self.download(path)
            if content is None:
                return httpx.Response(404)
            return httpx.Response(200, content=content)
        raise AssertionError(f"unexpected provider route: {request.method} {path}")

    def install(self, monkeypatch):
        provider = self
        monkeypatch.setattr(
            sandbox_tool,
            "sandbox_client_factory",
            lambda: OpenSandboxClient(
                "http://sandbox.test",
                "lane-test-key",
                http=httpx.AsyncClient(transport=httpx.MockTransport(provider.route)),
            ),
        )


def _declared_entries(outputs_dir, report=b"FIRST\n", summary=b'{"lines": 1}'):
    return [
        {"path": f"{outputs_dir}/report.txt", "size": len(report), "type": "file"},
        {"path": f"{outputs_dir}/summary.json", "size": len(summary), "type": "file"},
    ]


@pytest.mark.asyncio
async def test_sandbox_analysis_journey_publishes_declared_outputs(monkeypatch, tmp_path):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")
    provider = Provider()

    def command(_body):
        return httpx.Response(
            200,
            content=b'{"type":"stdout","text":"parser ok"}\n{"type":"execution_complete","exit_code":0}\n',
        )

    provider.command_response = command
    provider.listing = lambda outputs_dir: _declared_entries(outputs_dir)
    provider.download = lambda path: (
        b"FIRST\n" if path.endswith("/report.txt") else b'{"lines": 1}'
    )
    provider.install(monkeypatch)

    request = lane.request(
        source,
        [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    result = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )

    assert result.status == "succeeded", result.error
    assert len(result.artifacts) == 2
    outputs = {
        artifact.display_name: artifact
        for artifact in lane.db.query(Artifact)
        .filter(Artifact.originating_operation_id.is_not(None))
        if artifact.originating_run_id == lane.run.id
    }
    assert sorted(outputs) == ["report.txt", "summary.json"]
    for artifact in outputs.values():
        assert artifact.status == "ready"
        assert artifact.kind == "analysis_output"
    with lane.store.open_verified(outputs["report.txt"]) as handle:
        assert handle.read() == b"FIRST\n"
    with lane.store.open_verified(outputs["summary.json"]) as handle:
        assert json.loads(handle.read()) == {"lines": 1}
    operation = (
        lane.db.query(ToolOperation).filter(ToolOperation.run_id == lane.run.id).one()
    )
    assert operation.status == "succeeded"
    assert sorted(json.loads(operation.output_artifact_ids)) == sorted(
        str(item.id) for item in result.artifacts
    )
    attempt = lane.db.query(ToolAttempt).filter(
        ToolAttempt.operation_id == operation.id
    ).one()
    assert attempt.outcome == "succeeded"
    assert len(provider.commands) == 1
    assert provider.created == 1


@pytest.mark.asyncio
async def test_second_analysis_reuses_one_sandbox_with_fresh_directories(
    monkeypatch, tmp_path
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    first_log = lane.store_artifact(b"first\n")
    second_log = lane.store_artifact(b"second\n")
    provider = Provider()
    provider.phase = 0

    def command(_body):
        provider.phase += 1
        return httpx.Response(
            200,
            content=b'{"type":"stdout","text":"parser ok"}\n{"type":"execution_complete","exit_code":0}\n',
        )

    provider.command_response = command

    def listing(outputs_dir):
        report = b"FIRST\n" if provider.phase <= 1 else b"SECOND\n"
        return _declared_entries(outputs_dir, report=report)

    def download(path):
        if path.endswith("/summary.json"):
            return b'{"lines": 1}'
        return b"FIRST\n" if provider.phase <= 1 else b"SECOND\n"

    provider.listing = listing
    provider.download = download
    provider.install(monkeypatch)

    first_request = lane.request(
        source,
        [("log.txt", first_log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    second_request = lane.request(
        source,
        [("log.txt", second_log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    first = await sandbox_tool.execute_analysis(
        lane.context(), first_request, InvocationControl()
    )
    second = await sandbox_tool.execute_analysis(
        lane.context(), second_request, InvocationControl()
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert provider.created == 1
    assert len(provider.commands) == 2
    assert provider.commands[0]["cwd"] != provider.commands[1]["cwd"]
    operations = (
        lane.db.query(ToolOperation).filter(ToolOperation.run_id == lane.run.id).all()
    )
    assert len(operations) == 2
    assert {str(item.id) for item in operations} == {
        str(first.operation_id),
        str(second.operation_id),
    }
    second_operation = (
        lane.db.query(ToolOperation)
        .filter(ToolOperation.id == second.operation_id)
        .one()
    )
    assert second_operation.status == "succeeded"
    second_artifacts = {
        artifact.display_name: artifact
        for artifact in lane.db.query(Artifact)
        .filter(Artifact.originating_operation_id == second_operation.id)
        .all()
    }
    assert sorted(second_artifacts) == ["report.txt", "summary.json"]
    with lane.store.open_verified(second_artifacts["report.txt"]) as handle:
        assert handle.read() == b"SECOND\n"
    first_artifacts = {
        artifact.display_name: artifact
        for artifact in lane.db.query(Artifact)
        .filter(Artifact.originating_operation_id == first.operation_id)
        .all()
    }
    assert sorted(first_artifacts) == ["report.txt", "summary.json"]
    with lane.store.open_verified(first_artifacts["report.txt"]) as handle:
        assert handle.read() == b"FIRST\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["deleted", "foreign"])
async def test_stale_or_foreign_input_reference_refuses_before_any_provider_call(
    monkeypatch, tmp_path, case
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    if case == "deleted":
        log = lane.store_artifact(b"first\n")
        lane.db.query(Artifact).filter(Artifact.id == log.id).one().status = "deleted"
        lane.db.commit()
    else:
        other = Conversation(
            user_id=lane.user.id, project_id=lane.project.id, title="other lane"
        )
        lane.db.add(other)
        lane.db.commit()
        log = lane.store_artifact(b"first\n", conversation_id=other.id)

    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200,
        content=b'{"type":"stdout","text":"parser ok"}\n{"type":"execution_complete","exit_code":0}\n',
    )
    provider.listing = lambda outputs_dir: _declared_entries(outputs_dir)
    provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    provider.install(monkeypatch)

    request = lane.request(
        source,
        [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    try:
        result = await sandbox_tool.execute_analysis(
            lane.context(), request, InvocationControl()
        )
    except HTTPException as refused:
        assert refused.status_code in (404, 409, 422)
        result = None

    if result is not None:
        assert result.status == "failed", result.error
        assert result.error.code == "artifact_not_found"
    assert provider.created == 0
    assert provider.commands == []
    assert (
        lane.db.query(Artifact)
        .filter(
            Artifact.originating_run_id == lane.run.id,
            Artifact.originating_operation_id.is_not(None),
        )
        .count()
        == 0
    )
    assert (
        lane.db.query(ToolOperation)
        .filter(ToolOperation.run_id == lane.run.id)
        .count()
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["nonzero", "missing-completion", "error-event"],
)
async def test_nonzero_or_missing_completion_never_publishes_success(
    monkeypatch, tmp_path, case
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")

    provider = Provider()
    if case == "nonzero":
        stream = b'{"type":"stdout","text":"boom"}\n{"type":"execution_complete","exit_code":3}\n'
    elif case == "missing-completion":
        stream = b'{"type":"stdout","text":"no terminal event"}\n'
    else:
        stream = b'{"type":"stderr","text":"boom"}\n{"type":"error","message":"crash"}\n'
    provider.command_response = lambda _body: httpx.Response(200, content=stream)
    provider.listing = lambda outputs_dir: _declared_entries(outputs_dir)
    provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    provider.install(monkeypatch)

    request = lane.request(
        source,
        [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    result = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )

    assert result.status == "failed", result.error
    assert result.error.code == "provider_unavailable"
    assert len(provider.commands) == 1
    ready = (
        lane.db.query(Artifact)
        .filter(
            Artifact.originating_run_id == lane.run.id,
            Artifact.originating_operation_id.is_not(None),
            Artifact.status == "ready",
        )
        .count()
    )
    assert ready == 0
    operation = (
        lane.db.query(ToolOperation).filter(ToolOperation.run_id == lane.run.id).one()
    )
    assert operation.status == "failed"
    assert operation.output_artifact_ids == "[]"
    assert (
        lane.db.query(Event)
        .filter(Event.run_id == lane.run.id, Event.type == "tool.completed")
        .count()
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("undeclared-extra", "provider_unavailable"),
        ("oversized", "provider_unavailable"),
        ("invalid-json", "invalid_tool_output"),
    ],
)
async def test_undeclared_oversized_or_invalid_output_is_rejected(
    monkeypatch, tmp_path, case, expected_code
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")

    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200,
        content=b'{"type":"stdout","text":"parser ok"}\n{"type":"execution_complete","exit_code":0}\n',
    )
    if case == "undeclared-extra":
        provider.listing = lambda outputs_dir: _declared_entries(
            outputs_dir
        ) + [{"path": f"{outputs_dir}/stray.bin", "size": 4, "type": "file"}]
        provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    elif case == "oversized":
        provider.listing = lambda outputs_dir: _declared_entries(
            outputs_dir, report=b"x" * (4 * 1024 * 1024 + 1)
        )
        provider.download = lambda path: (
            b"x" * (4 * 1024 * 1024 + 1) if path.endswith(".txt") else b'{"lines": 1}'
        )
    else:
        provider.listing = lambda outputs_dir: _declared_entries(
            outputs_dir, summary=b"not-json"
        )
        provider.download = lambda path: (
            b"FIRST\n" if path.endswith(".txt") else b"not-json"
        )
    provider.install(monkeypatch)

    request = lane.request(
        source,
        [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    result = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )

    assert result.status == "failed", result.error
    assert result.error.code == expected_code
    assert len(provider.commands) == 1
    ready = (
        lane.db.query(Artifact)
        .filter(
            Artifact.originating_run_id == lane.run.id,
            Artifact.originating_operation_id.is_not(None),
            Artifact.status == "ready",
        )
        .count()
    )
    assert ready == 0
    operation = (
        lane.db.query(ToolOperation).filter(ToolOperation.run_id == lane.run.id).one()
    )
    assert operation.status == "failed"
    assert operation.output_artifact_ids == "[]"
    assert (
        lane.db.query(Event)
        .filter(Event.run_id == lane.run.id, Event.type == "tool.completed")
        .count()
        == 0
    )


@pytest.mark.asyncio
async def test_cancelled_invocation_stops_the_wait_without_publishing(monkeypatch, tmp_path):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")

    provider = Provider()
    stopped = [False]

    def command(_body):
        stopped[0] = True
        return httpx.Response(
            200,
            content=b'{"type":"stdout","text":"parser ok"}\n{"type":"execution_complete","exit_code":0}\n',
        )

    provider.command_response = command
    provider.listing = lambda outputs_dir: _declared_entries(outputs_dir)
    provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    provider.install(monkeypatch)

    request = lane.request(
        source,
        [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    result = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl(stop=lambda: stopped[0])
    )

    assert result.status in ("cancelled", "failed"), result.error
    assert result.error.code == "cancelled"
    assert len(provider.commands) == 1
    ready = (
        lane.db.query(Artifact)
        .filter(
            Artifact.originating_run_id == lane.run.id,
            Artifact.originating_operation_id.is_not(None),
            Artifact.status == "ready",
        )
        .count()
    )
    assert ready == 0
    assert (
        lane.db.query(Event)
        .filter(Event.run_id == lane.run.id, Event.type == "tool.completed")
        .count()
        == 0
    )


@pytest.mark.asyncio
async def test_stale_fence_during_publication_cannot_publish_sandbox_outputs(
    monkeypatch, tmp_path
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")

    provider = Provider()

    def steal_then_succeed(_body):
        with SessionLocal() as changed:
            changed.query(Run).filter(Run.id == lane.run.id).one().fence = 2
            job = changed.query(RunJob).filter(RunJob.run_id == lane.run.id).one()
            job.fence = 2
            job.owner_id = "replacement-worker"
            job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
            changed.commit()
        return httpx.Response(
            200,
            content=b'{"type":"stdout","text":"parser ok"}\n{"type":"execution_complete","exit_code":0}\n',
        )

    provider.command_response = steal_then_succeed
    provider.listing = lambda outputs_dir: _declared_entries(outputs_dir)
    provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    provider.install(monkeypatch)

    request = lane.request(
        source,
        [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    result = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )

    assert result.status == "interrupted", result.error
    assert len(provider.commands) == 1
    staged = (
        lane.db.query(Artifact)
        .filter(
            Artifact.originating_run_id == lane.run.id,
            Artifact.originating_operation_id.is_not(None),
        )
        .all()
    )
    assert all(artifact.status != "ready" for artifact in staged)
    operation = (
        lane.db.query(ToolOperation).filter(ToolOperation.run_id == lane.run.id).one()
    )
    assert operation.status == "running"
    assert operation.output_artifact_ids == "[]"
    assert (
        lane.db.query(Event)
        .filter(Event.run_id == lane.run.id, Event.type == "tool.completed")
        .count()
        == 0
    )

    with pytest.raises(HTTPException):
        await sandbox_tool.execute_analysis(
            lane.context(), request, InvocationControl()
        )
    assert len(provider.commands) == 1
    lane.db.query(ExecutionSlot).filter(ExecutionSlot.run_id == lane.run.id).delete(
        synchronize_session=False
    )
    lane.db.commit()


@pytest.mark.asyncio
async def test_duplicate_delivery_replays_the_committed_result_without_a_new_command(
    monkeypatch, tmp_path
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")

    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200,
        content=b'{"type":"stdout","text":"parser ok"}\n{"type":"execution_complete","exit_code":0}\n',
    )
    provider.listing = lambda outputs_dir: _declared_entries(outputs_dir)
    provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    provider.install(monkeypatch)

    request = lane.request(
        source,
        [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    first = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )
    replay = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )

    assert first.status == "succeeded"
    assert replay == first
    assert len(provider.commands) == 1
    assert provider.created == 1


@pytest.mark.asyncio
async def test_retained_reports_survive_the_sandbox_provider_disappearing(
    monkeypatch, tmp_path
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")

    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200,
        content=b'{"type":"stdout","text":"parser ok"}\n{"type":"execution_complete","exit_code":0}\n',
    )
    provider.listing = lambda outputs_dir: _declared_entries(outputs_dir)
    provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    provider.install(monkeypatch)

    request = lane.request(
        source,
        [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    first = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )
    assert first.status == "succeeded"

    provider.dead = True
    after = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )
    assert after == first
    assert len(provider.commands) == 1
    assert provider.created == 1

    operation = (
        lane.db.query(ToolOperation).filter(ToolOperation.run_id == lane.run.id).one()
    )
    retained = {
        artifact.display_name: artifact
        for artifact in lane.db.query(Artifact)
        .filter(Artifact.originating_operation_id == operation.id)
        .all()
    }
    assert sorted(retained) == ["report.txt", "summary.json"]
    assert all(artifact.status == "ready" for artifact in retained.values())
    with lane.store.open_verified(retained["report.txt"]) as handle:
        assert handle.read() == b"FIRST\n"
    with lane.store.open_verified(retained["summary.json"]) as handle:
        assert json.loads(handle.read()) == {"lines": 1}
