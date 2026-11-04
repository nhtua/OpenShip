"""Sandbox analysis journey through the real gateway, admission and publication.

OpenSandbox transport is stubbed at its HTTP boundary only (httpx MockTransport
speaking the exact proxy routes); artifact records, the store, admission, the
active slot, attempts, staging and fenced publication are production machinery.
"""


import hashlib
import json
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
import pytest
from fastapi import HTTPException

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore
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
from src.openship.runs.analysis_graph import (
    build_analysis_graph,
    decode_journey,
    resume_phase,
)
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
from src.openship.sandboxes.tool import AnalysisArguments
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
    monkeypatch.setattr(settings, "sandbox_deployment_id", "lane-deployment")
    monkeypatch.setattr(settings, "sandbox_image", REQUIREMENTS.image)
    monkeypatch.setattr(settings, "sandbox_profile_id", REQUIREMENTS.profile_id)
    monkeypatch.setattr(settings, "sandbox_dependency_identity", REQUIREMENTS.dependency_identity)
    key_file = tmp_path / "sandbox.key"
    key_file.write_bytes(b"lane-test-key\n")
    monkeypatch.setattr(settings, "sandbox_key_file", key_file)


class Lane:
    """One seeded project/conversation/tool-run with claim and real store."""

    sessions: ClassVar[list] = []

    def __init__(self, tmp_path):
        self.store = LocalArtifactStore(tmp_path / "store", _uuid.uuid4())
        self.store.initialize()
        self.db = SessionLocal()
        self.sessions.append(self.db)
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


@pytest.fixture(autouse=True)
def close_lane_sessions():
    """Do not let journey fixtures exhaust the shared database connection pool."""
    yield
    for session in Lane.sessions:
        session.close()
    Lane.sessions.clear()


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
async def test_lost_command_reply_blocks_later_operation_on_same_sandbox(
    monkeypatch, tmp_path
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")
    provider = Provider()

    def lose_first_reply(_body):
        if len(provider.commands) == 1:
            raise httpx.ReadTimeout("response lost")
        return httpx.Response(200, content=(
            b'{"type":"execution_complete","exit_code":0}\n'
        ))

    provider.command_response = lose_first_reply
    provider.listing = lambda path: _declared_entries(path)
    provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    provider.install(monkeypatch)
    outputs = [("report.txt", "text/plain"), ("summary.json", "application/json")]
    first = lane.request(source, [("log.txt", log)], outputs)
    second = lane.request(source, [("log.txt", log)], outputs)

    failed = await sandbox_tool.execute_analysis(
        lane.context(), first, InvocationControl()
    )
    blocked = await sandbox_tool.execute_analysis(
        lane.context(), second, InvocationControl()
    )

    assert failed.status != "succeeded"
    assert blocked.status != "succeeded"
    assert provider.created == 1
    assert len(provider.commands) == 1


@pytest.mark.asyncio
async def test_execute_analysis_refuses_source_without_pinned_review_model(
    monkeypatch, tmp_path
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: [{"path": f"{path}/returned.py", "size": 9, "type": "file"}]
    provider.download = lambda _path: b"print(1)\n"
    provider.install(monkeypatch)
    request = lane.request(source, [("log.txt", log)], [("returned.py", "text/plain")])
    request = request.model_copy(update={
        "outputs": (OutputSpec(
            alias="returned.py", media_type="text/plain", kind="source",
            expectation="parse the selected log file",
        ),),
    })

    result = await sandbox_tool.execute_analysis(lane.context(), request, InvocationControl())
    assert result.status == "failed"
    assert result.error.code == "invalid_tool_output"
    assert result.artifacts == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False, None])
async def test_source_review_is_persisted_and_replayed_without_execution(monkeypatch, tmp_path, accepted):
    from src.openship.providers.contracts import ProviderTurnResult
    from src.openship.sandboxes import outputs
    from tests.providers.conftest import run_snapshot

    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    lane.run.model_configuration = run_snapshot()
    lane.db.commit()
    source = lane.store_artifact(PARSER_SOURCE)
    returned = b"raise RuntimeError('returned source must remain inert')\n"
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: [{"path": f"{path}/returned.py", "size": len(returned), "type": "file"}]
    provider.download = lambda _path: returned
    provider.install(monkeypatch)
    reviewed = []

    async def judge(request, config, control, recorder, **kwargs):
        reviewed.append(request)
        if accepted is None:
            from src.openship.providers.errors import ProviderError

            raise ProviderError("provider_unavailable")
        return ProviderTurnResult(
            text=json.dumps({"accepted": accepted, "reason": "Expectation decision only"}),
            finish_reason="stop", selected_profile_id=config.primary.id,
        )

    monkeypatch.setattr(outputs, "run_model_turn", judge)
    request = lane.request(source, [], [("returned.py", "text/plain")])
    request = request.model_copy(update={"outputs": (OutputSpec(
        alias="returned.py", media_type="text/plain", kind="source",
        expectation="Raise a RuntimeError without reading inputs.",
    ),)})
    result = await sandbox_tool.execute_analysis(lane.context(), request, InvocationControl())
    assert result.status == ("succeeded" if accepted else "failed"), result.error
    decision = result.result["reviews"]["returned.py"]
    assert decision["accepted"] is bool(accepted)
    assert decision["output_sha256"] == hashlib.sha256(returned).hexdigest()
    if accepted:
        assert len(result.artifacts) == 1
        row = lane.db.get(Artifact, result.artifacts[0].id)
        with lane.store.open_verified(row) as handle:
            assert handle.read() == returned
        assert row.sha256 == decision["output_sha256"]
    else:
        assert not result.artifacts
        assert result.result["outputs"] == {}
    replay = await sandbox_tool.execute_analysis(lane.context(), request, InvocationControl())
    assert replay == result
    assert len(provider.commands) == len(reviewed) == 1


@pytest.mark.parametrize("kind", ["source", "text", "json"])
def test_analysis_result_step_escapes_text_and_keeps_source_inert(monkeypatch, tmp_path, kind):
    from langgraph.checkpoint.memory import MemorySaver

    from src.openship.providers.contracts import ProviderTurnResult
    from src.openship.sandboxes import outputs
    from tests.providers.conftest import run_snapshot

    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    lane.run.model_configuration = run_snapshot()
    lane.db.commit()
    source = lane.store_artifact(PARSER_SOURCE)
    returned = b"raise RuntimeError('never run')\n# <script>alert(1)</script>"
    if kind == "json":
        returned = b'{"comment":"<script>alert(1)</script>"}'
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: [{"path": f"{path}/returned.py", "size": len(returned), "type": "file"}]
    provider.download = lambda _path: returned
    provider.install(monkeypatch)

    async def judge(request, config, *_args, **_kwargs):
        return ProviderTurnResult(
            text='{"accepted":true,"reason":"Matches expectation, not a safety assessment"}',
            finish_reason="stop", selected_profile_id=config.primary.id,
        )

    monkeypatch.setattr(outputs, "run_model_turn", judge)
    from src.openship.execution.gateway import ToolGateway

    graph = build_analysis_graph(
        MemorySaver(), gateway_factory=lambda: ToolGateway(SessionLocal),
        invocation_factory=lambda _state: (lane.claim, lane.context(), InvocationControl()),
    )
    final = graph.invoke({
        "run_id": str(lane.run.id),
        "execution_configuration": lane.execution.model_dump_json(),
        "model_configuration": run_snapshot(),
        "tool_command_json": json.dumps({
            "capability": lane.ref.model_dump(mode="json"),
            "arguments": {
                "source_id": str(source.id), "output_media": {
                    "returned.py": "application/json" if kind == "json" else "text/plain",
                },
                "output_expectations": (
                    {"returned.py": "Raise RuntimeError; include a script-tag comment."}
                    if kind == "source" else {}
                ),
            },
        }),
    }, config={"configurable": {"thread_id": f"review-{lane.run.id}"}})
    assert "<script>" not in final["analysis_summary"]["returned.py"]
    assert "&lt;script&gt;" in final["analysis_summary"]["returned.py"]
    if kind == "source":
        assert final["tool_result"]["result"]["reviews"]["returned.py"]["output_sha256"] == hashlib.sha256(returned).hexdigest()
    else:
        assert final["tool_result"]["result"]["reviews"] == {}
    assert resume_phase(decode_journey(final["journey_json"])) is None
    assert len(provider.commands) == 1


def test_publication_validation_rejects_missing_or_mismatched_source_review(tmp_path):
    from types import SimpleNamespace

    from src.openship.capabilities.definitions import get_tool_definition_by_id
    from src.openship.sandboxes.contracts import ReviewDecision
    from src.openship.sandboxes.tool import AnalysisObservation

    definition = get_tool_definition_by_id(ANALYSIS_ID)
    operation = SimpleNamespace(arguments_json=json.dumps({
        "source_id": str(_uuid.uuid4()), "output_media": {"returned.py": "text/plain"},
        "output_expectations": {"returned.py": "Define parse."},
    }))
    for reviews in ({}, {"returned.py": ReviewDecision(
        accepted=True, reason="matches", output_sha256=hashlib.sha256(b"different").hexdigest(),
    )}):
        with pytest.raises((ValueError, sandbox_tool.OutputValidationFailed)):
            definition.validate_handler_result(operation, AnalysisObservation(
                outputs={"returned.py": b"def parse(): pass"}, reviews=reviews,
            ))


def test_failed_execute_remains_the_first_incomplete_phase(monkeypatch, tmp_path):
    from langgraph.checkpoint.memory import MemorySaver

    from src.openship.capabilities.contracts import PublishedToolResult, ToolFailure

    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")
    arguments = {
        "source_id": str(source.id),
        "input_ids": {"log.txt": str(log.id)},
        "parameters": {},
        "output_media": {"report.txt": "text/plain"},
    }

    class FailedGateway:
        async def execute_tool(self, **_kwargs):
            return PublishedToolResult(
                operation_id=_uuid.uuid4(),
                status="failed",
                error=ToolFailure(code="provider_unavailable", message="provider unavailable"),
            )

    graph = build_analysis_graph(
        MemorySaver(),
        gateway_factory=FailedGateway,
        invocation_factory=lambda _state: (
            lane.claim, lane.context(), InvocationControl()
        ),
    )
    final = graph.invoke(
        {
            "run_id": str(lane.run.id),
            "execution_configuration": lane.execution.model_dump_json(),
            "tool_command_json": json.dumps({
                "capability": lane.ref.model_dump(mode="json"),
                "arguments": arguments,
            }),
        },
        config={"configurable": {"thread_id": f"failed-{lane.run.id}"}},
    )

    journey = decode_journey(final["journey_json"])
    assert journey.completed_phases == ("prepare",)
    assert resume_phase(journey) == "execute"


def test_generated_source_is_captured_and_exact_ref_is_handed_to_execution(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from langgraph.checkpoint.memory import MemorySaver

    from src.openship.capabilities.contracts import PublishedToolResult, ToolFailure
    from src.openship.providers.contracts import ProviderTurnResult
    from src.openship.runs import analysis_graph
    from tests.providers.conftest import run_snapshot

    lane = Lane(tmp_path)
    log = lane.store_artifact(b"warning: service unavailable\n")
    generated = "from pathlib import Path\nPath('unused')\n"
    observed = {}
    model_calls = 0

    async def fake_model_turn(request, model_config, control, recorder, **kwargs):
        nonlocal model_calls
        model_calls += 1
        observed["request"] = request
        observed["model_config"] = model_config
        observed["kwargs"] = kwargs
        return ProviderTurnResult(
            text=generated,
            selected_profile_id=model_config.primary.id,
            finish_reason="stop",
        )

    monkeypatch.setattr(analysis_graph, "run_model_turn", fake_model_turn)

    class Recorder:
        pass

    class CaptureGateway:
        async def execute_tool(self, **kwargs):
            observed["tool_arguments"] = kwargs["arguments"]
            observed["tool_inputs"] = kwargs["inputs"]
            return PublishedToolResult(
                operation_id=_uuid.uuid4(),
                status="failed",
                error=ToolFailure(code="provider_unavailable", message="test handoff"),
            )

    invocation = SimpleNamespace(
        claim=lane.claim,
        context=lane.context(),
        control=InvocationControl(),
        recorder=Recorder(),
        authorize_profile=lambda _profile: None,
    )
    arguments = AnalysisArguments(
        source_prompt="Parse the selected log and write a concise report.",
        input_ids={"log.txt": log.id},
        parameters={},
        output_media={"report.txt": "text/plain"},
        output_expectations={"report.txt": "Define a parser for the selected log format."},
    )
    state = {
        "run_id": str(lane.run.id),
        "execution_configuration": lane.execution.model_dump_json(),
        "model_configuration": run_snapshot(),
        "tool_command_json": json.dumps({
            "capability": lane.ref.model_dump(mode="json"),
            "arguments": arguments.model_dump(mode="json"),
        }),
    }
    graph = build_analysis_graph(
        MemorySaver(),
        gateway_factory=CaptureGateway,
        invocation_factory=lambda _state: invocation,
    )

    final = graph.invoke(
        state, config={"configurable": {"thread_id": f"generated-{lane.run.id}"}}
    )
    replay = graph.invoke(
        state, config={"configurable": {"thread_id": f"generated-replay-{lane.run.id}"}}
    )

    journey = decode_journey(final["journey_json"])
    source = journey.generated_source
    assert source is not None
    with lane.store.open_verified(lane.db.get(Artifact, source.id)) as file:
        assert file.read() == generated.encode()
    assert source.sha256 == hashlib.sha256(generated.encode()).hexdigest()
    assert observed["model_config"].primary.id
    assert observed["request"].tools == ()
    assert json.loads(observed["request"].messages[-1].content)["output_expectations"] == arguments.output_expectations
    assert observed["tool_arguments"]["source_id"] == str(source.id)
    assert "source_prompt" not in observed["tool_arguments"]
    assert tuple(item.id for item in observed["tool_inputs"]) == (source.id, log.id)
    assert decode_journey(replay["journey_json"]).generated_source == source
    assert model_calls == 1


def test_oversized_generated_source_fails_before_sandbox_execution(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from langgraph.checkpoint.memory import MemorySaver

    from src.openship.providers.contracts import ProviderTurnResult
    from src.openship.providers.errors import ProviderError
    from src.openship.runs import analysis_graph
    from tests.providers.conftest import run_snapshot

    lane = Lane(tmp_path)
    log = lane.store_artifact(b"log\n")
    executed = []

    async def oversized_model_turn(request, model_config, control, recorder, **kwargs):
        return ProviderTurnResult(
            text="x" * (256 * 1024 + 1),
            selected_profile_id=model_config.primary.id,
            finish_reason="stop",
        )

    monkeypatch.setattr(analysis_graph, "run_model_turn", oversized_model_turn)

    class Recorder:
        pass

    class Gateway:
        async def execute_tool(self, **kwargs):
            executed.append(kwargs)

    args = AnalysisArguments(
        source_prompt="Write a parser.",
        input_ids={"log.txt": log.id},
        output_media={"report.txt": "text/plain"},
    )
    graph = build_analysis_graph(
        MemorySaver(),
        gateway_factory=Gateway,
        invocation_factory=lambda _state: SimpleNamespace(
            claim=lane.claim,
            context=lane.context(),
            control=InvocationControl(),
            recorder=Recorder(),
            authorize_profile=lambda _profile: None,
        ),
    )
    with pytest.raises(ProviderError, match="exceeds its size limit"):
        graph.invoke(
            {
                "run_id": str(lane.run.id),
                "execution_configuration": lane.execution.model_dump_json(),
                "model_configuration": run_snapshot(),
                "tool_command_json": json.dumps({
                    "capability": lane.ref.model_dump(mode="json"),
                    "arguments": args.model_dump(mode="json"),
                }),
            },
            config={"configurable": {"thread_id": f"oversized-{lane.run.id}"}},
        )
    assert executed == []
    assert (
        lane.db.query(Artifact)
        .filter(
            Artifact.originating_run_id == lane.run.id,
            Artifact.source == "generated",
        )
        .count()
        == 0
    )

    async def unavailable_model(*_args, **_kwargs):
        raise ProviderError("provider_unavailable")

    monkeypatch.setattr(analysis_graph, "run_model_turn", unavailable_model)
    with pytest.raises(ProviderError, match="provider could not be reached"):
        graph.invoke(
            {
                "run_id": str(lane.run.id),
                "execution_configuration": lane.execution.model_dump_json(),
                "model_configuration": run_snapshot(),
                "tool_command_json": json.dumps({
                    "capability": lane.ref.model_dump(mode="json"),
                    "arguments": args.model_dump(mode="json"),
                }),
            },
            config={"configurable": {"thread_id": f"unavailable-{lane.run.id}"}},
        )
    assert executed == []


def test_runtime_identity_changes_with_provider_deployment(monkeypatch, tmp_path):
    enable_sandbox(monkeypatch, tmp_path)
    first = sandbox_tool.runtime_requirements()
    monkeypatch.setattr(settings, "sandbox_endpoint", "http://other-sandbox.test")
    second = sandbox_tool.runtime_requirements()
    assert first.provider_id != second.provider_id
    assert first.key != second.key


def test_analysis_arguments_require_exactly_one_source_origin():
    prompt = "Read log.txt and write report.txt as UTF-8 text."
    generated = AnalysisArguments(
        source_prompt=prompt,
        input_ids={"log.txt": _uuid.uuid4()},
        output_media={"report.txt": "text/plain"},
    )
    assert generated.source_prompt == prompt
    assert generated.source_id is None

    prepared = AnalysisArguments(
        source_id=_uuid.uuid4(),
        input_ids={},
        output_media={"report.txt": "text/plain"},
    )
    assert prepared.source_prompt is None

    with pytest.raises(ValueError, match="exactly one"):
        AnalysisArguments(
            source_prompt=prompt,
            source_id=_uuid.uuid4(),
            output_media={"report.txt": "text/plain"},
        )


def test_generated_source_is_a_valid_journey_without_selected_inputs():
    from src.openship.runs.analysis_graph import (
        AnalysisJourney,
        _journey_of,
        encode_journey,
    )

    generated = ArtifactRef(
        id=_uuid.uuid4(), sha256=hashlib.sha256(b"print('hello')").hexdigest(),
        size_bytes=len(b"print('hello')"), media_type="text/plain",
    )
    journey = AnalysisJourney(root_run_id=str(_uuid.uuid4()), generated_source=generated)

    assert _journey_of({"journey_json": encode_journey(journey)}) == journey


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
async def test_partial_output_staging_removes_already_installed_content(
    monkeypatch, tmp_path
):
    enable_sandbox(monkeypatch, tmp_path)
    lane = Lane(tmp_path)
    source = lane.store_artifact(PARSER_SOURCE)
    log = lane.store_artifact(b"first\n")
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda path: _declared_entries(path)
    provider.download = lambda path: b"FIRST\n" if path.endswith(".txt") else b'{"lines": 1}'
    provider.install(monkeypatch)
    original_stage = lane.store.stage
    calls = []

    def fail_second_stage(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise ArtifactStorageError("injected second-output staging failure")
        return original_stage(*args, **kwargs)

    monkeypatch.setattr(lane.store, "stage", fail_second_stage)
    request = lane.request(
        source, [("log.txt", log)],
        [("report.txt", "text/plain"), ("summary.json", "application/json")],
    )
    result = await sandbox_tool.execute_analysis(
        lane.context(), request, InvocationControl()
    )
    assert result.status != "succeeded"
    first_row = lane.db.get(Artifact, calls[0])
    assert first_row.status != "ready"
    assert not (lane.store.root / first_row.relative_storage_key).exists()


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
