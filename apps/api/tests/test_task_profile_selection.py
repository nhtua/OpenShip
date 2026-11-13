"""Task-v5 Python execution selects only a pinned, ready sandbox profile."""

from __future__ import annotations

import json
from typing import ClassVar
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    CapabilityRef,
    canonical_digest,
    canonical_json,
    decode_execution_config,
)
from src.openship.capabilities.definitions import task_tool_definitions
from src.openship.config import settings
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.models import ToolOperation
from src.openship.execution.projections import project_operation
from src.openship.providers.control import InvocationControl
from src.openship.runs.tasks.contracts import ExecutionConfigV5, TaskContext
from src.openship.runs.tasks.inference import _stage_prompt
from src.openship.sandboxes import tool as sandbox_tool
from src.openship.sandboxes.profile_schema import ProfileCatalog, catalog_digest
from src.openship.sandboxes.profiles import (
    TaskProfileError,
    install_runtime_catalog,
    reset_runtime_catalog,
    resolve_task_requirements,
)
from tests.sandboxes.test_tool import Lane, Provider, enable_sandbox

IDENTIFIERS = ("logs-data", "iac-validation", "standard", "devops-shell")


def _metadata(label: str, purpose: str, use_for: str) -> dict:
    return {
        "label": label,
        "description": purpose,
        "use_for": [use_for],
        "highlights": ["Python"],
        "limitations": ["offline"],
    }


def _profile(
    seed: str,
    *,
    label: str = "Profile",
    purpose: str = "Purpose metadata",
    use_for: str = "general work",
    enabled: bool = True,
    image_id: str | None = None,
    dependency_identity: str | None = None,
) -> dict:
    image = image_id or "sha256:" + seed * 64
    dependency = dependency_identity or "sha256:" + seed * 64
    metadata = _metadata(label, purpose, use_for)
    return {
        "enabled": enabled,
        "extensions": {},
        "metadata": metadata,
        "metadata_digest": canonical_digest(metadata),
        "runtime": {
            "image_tag": f"hidden/example:{seed}",
            "image_id": image,
            "dependency_identity": dependency,
            "platform": "linux/amd64",
            "fingerprint": seed * 64,
            "engine_identity": f"test-engine-{seed}",
            "smoke_image_id": image,
            "smoke_fingerprint": seed * 64,
            "smoke_passed": True,
            "smoke_evidence_digest": "f" * 64,
        },
    }


def _catalog(entries: dict[str, dict]) -> ProfileCatalog:
    return ProfileCatalog.model_validate_json(
        json.dumps({"schema_version": 1, "profiles": entries})
    )


def _python_ref() -> CapabilityRef:
    manifest = next(
        item for item in list_capability_manifests() if item.id == "python_execute"
    )
    return CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )


def _execution(catalog: ProfileCatalog) -> ExecutionConfigV5:
    return ExecutionConfigV5(
        allowed_capabilities=(_python_ref(),),
        aws_read_catalog_digest="0" * 64,
        task_context=TaskContext(
            root_run_id=UUID("11111111-1111-1111-1111-111111111111"),
            project_id=UUID("22222222-2222-2222-2222-222222222222"),
            conversation_id=UUID("33333333-3333-3333-3333-333333333333"),
            initiator_id=UUID("44444444-4444-4444-4444-444444444444"),
            scope="conversation",
            mode="copilot",
            graph_version=5,
            original_intent="Produce the requested report.",
        ),
        profile_catalog=catalog,
        profile_catalog_digest=catalog_digest(catalog),
    )


def _all_profiles() -> dict[str, dict]:
    return {
        "logs-data": _profile(
            "1",
            label="Logs and data",
            purpose="Chart and aggregate selected data files.",
            use_for="charts",
        ),
        "iac-validation": _profile(
            "2",
            label="IaC validation",
            purpose="Validate infrastructure configuration.",
            use_for="terraform",
        ),
        "standard": _profile(
            "3",
            label="Standard",
            purpose="Run general Python analysis.",
            use_for="general analysis",
        ),
        "devops-shell": _profile(
            "4",
            label="DevOps shell",
            purpose="Inspect installed command-line tooling.",
            use_for="shell tools",
        ),
    }


class TaskLane(Lane):
    sessions: ClassVar[list] = []

    def manifest(self):
        return next(
            item for item in list_capability_manifests() if item.id == "python_execute"
        )


@pytest.fixture(autouse=True)
def close_task_lanes():
    reset_runtime_catalog()
    yield
    for session in TaskLane.sessions:
        session.close()
    TaskLane.sessions.clear()
    reset_runtime_catalog()


def _task_lane(monkeypatch, tmp_path, execution: ExecutionConfigV5) -> TaskLane:
    enable_sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "python_execute")
    lane = TaskLane(tmp_path)
    lane.execution = execution
    lane.run.kind = "chat"
    lane.run.graph_version = 5
    lane.run.execution_configuration = execution.model_dump_json()
    lane.db.commit()
    install_runtime_catalog(execution.profile_catalog)
    return lane


def _provider(monkeypatch) -> Provider:
    provider = Provider()
    provider.command_response = lambda _body: httpx.Response(
        200, content=b'{"type":"execution_complete","exit_code":0}\n'
    )
    provider.listing = lambda _path: []
    provider.install(monkeypatch)
    return provider


def test_planning_prompt_exposes_enabled_profile_purposes_not_runtime_bindings():
    entries = _all_profiles()
    entries["iac-validation"] = _profile(
        "2", purpose="Disabled unique purpose", enabled=False
    )
    execution = _execution(_catalog(entries))

    prompt = _stage_prompt("assessment_and_plan", execution, ())

    assert "Chart and aggregate selected data files." in prompt
    assert "charts" in prompt
    assert "Run general Python analysis." in prompt
    assert "Disabled unique purpose" not in prompt
    assert "hidden/example" not in prompt
    assert "test-engine" not in prompt


def test_each_maintained_profile_is_selected_by_purpose(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_deployment_id", "lane-deployment")
    catalog = _catalog(_all_profiles())
    execution = _execution(catalog)
    install_runtime_catalog(catalog)

    for profile_id in IDENTIFIERS:
        pinned_entry = catalog.profiles[profile_id]
        requirements = resolve_task_requirements(execution, profile_id, catalog)
        assert requirements.image == pinned_entry.runtime.image_id
        assert requirements.image != pinned_entry.runtime.image_tag
        assert requirements.profile_id == profile_id
        assert (
            requirements.dependency_identity
            == pinned_entry.runtime.dependency_identity
        )

    definitions = task_tool_definitions(execution, frozenset({"python_execute"}))
    assert len(definitions) == 1
    tool = definitions[0]
    assert tool.name == "python_execute"
    assert set(tool.parameters["properties"]["profile_id"]["enum"]) == set(IDENTIFIERS)
    assert "profile_id" in tool.parameters["required"]
    for profile_id in IDENTIFIERS:
        assert profile_id in tool.description
    assert "image_id" not in tool.description
    assert "engine_identity" not in tool.description
    assert "image_id" not in tool.parameters["properties"]
    assert len(tool.description.encode("utf-8")) <= 1024


@pytest.mark.asyncio
async def test_hidden_disabled_changed_and_unknown_profiles_never_dispatch(monkeypatch, tmp_path):
    pinned_profiles = _all_profiles()
    pinned_profiles["iac-validation"]["enabled"] = False
    pinned = _catalog(pinned_profiles)
    execution = _execution(pinned)

    current_profiles = _all_profiles()
    current_profiles["iac-validation"]["enabled"] = False
    current_profiles["devops-shell"] = _profile(
        "5",
        label="Stale DevOps shell",
        purpose="A rebuilt image that no longer matches the pinned selection.",
        use_for="shell tools",
    )
    current = _catalog(current_profiles)

    lane = _task_lane(monkeypatch, tmp_path, execution)
    install_runtime_catalog(current)
    definitions = task_tool_definitions(execution, frozenset({"python_execute"}))
    exposed = set(definitions[0].parameters["properties"]["profile_id"]["enum"])
    assert exposed == {"logs-data", "standard"}

    for profile_id in ("iac-validation", "devops-shell", "unknown"):
        with pytest.raises(TaskProfileError):
            resolve_task_requirements(execution, profile_id, current)

    provider = _provider(monkeypatch)
    with pytest.raises(HTTPException):
        await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim,
            context=lane.context(),
            logical_call_id="python:stale",
            ref=lane.ref,
            arguments={
                "source": "print(42)\n",
                "profile_id": "devops-shell",
                "input_ids": {},
                "parameters": {},
                "output_media": {},
            },
            inputs=(),
            control=InvocationControl(),
        )
    assert provider.created == 0
    assert provider.commands == []


@pytest.mark.asyncio
async def test_model_cannot_override_image_or_runtime(monkeypatch, tmp_path):
    from src.openship.sandboxes.python_tool import PythonArguments

    for field in (
        "image",
        "image_id",
        "image_tag",
        "engine_identity",
        "settings",
        "dependency_identity",
        "platform",
        "runtime",
        "profile_catalog",
    ):
        with pytest.raises(ValidationError):
            PythonArguments(
                source="print(42)",
                profile_id="logs-data",
                input_ids={},
                parameters={},
                output_media={},
                **{field: "attacker-value"},
            )

    catalog = _catalog(_all_profiles())
    execution = _execution(catalog)
    lane = _task_lane(monkeypatch, tmp_path, execution)
    definitions = task_tool_definitions(execution, frozenset({"python_execute"}))
    properties = definitions[0].parameters["properties"]
    assert not {
        "image",
        "image_id",
        "image_tag",
        "engine_identity",
        "settings",
        "dependency_identity",
        "platform",
        "runtime",
    } & set(properties)

    provider = _provider(monkeypatch)
    with pytest.raises(HTTPException):
        await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim,
            context=lane.context(),
            logical_call_id="python:override",
            ref=lane.ref,
            arguments={
                "source": "print(42)\n",
                "profile_id": "logs-data",
                "image": "attacker/image",
                "input_ids": {},
                "parameters": {},
                "output_media": {},
            },
            inputs=(),
            control=InvocationControl(),
        )
    assert provider.created == 0


@pytest.mark.asyncio
async def test_profile_change_prevents_incompatible_sandbox_reuse(monkeypatch, tmp_path):
    catalog = _catalog(_all_profiles())
    execution = _execution(catalog)
    lane = _task_lane(monkeypatch, tmp_path, execution)
    provider = _provider(monkeypatch)
    gateway = ToolGateway(lane.context().session_factory)

    logs = resolve_task_requirements(execution, "logs-data", catalog)
    standard = resolve_task_requirements(execution, "standard", catalog)
    assert logs.key != standard.key

    async def run(profile_id: str, name: str):
        return await gateway.execute_tool(
            claim=lane.claim,
            context=lane.context(),
            logical_call_id=f"python:{name}",
            ref=lane.ref,
            arguments={
                "source": "print(42)\n",
                "profile_id": profile_id,
                "input_ids": {},
                "parameters": {},
                "output_media": {},
            },
            inputs=(),
            control=InvocationControl(),
        )

    first = await run("logs-data", "logs")
    assert first.status == "succeeded", first.error
    operation = lane.db.get(ToolOperation, first.operation_id)
    summary = project_operation(lane.db, operation)
    assert summary.profile_id == "logs-data"
    projected = canonical_json(summary.model_dump(mode="json")).decode("utf-8")
    assert "image_id" not in projected
    assert "image_tag" not in projected
    assert "engine_identity" not in projected
    assert "sha256:" not in projected
    second = await run("standard", "standard")
    assert second.status == "succeeded", second.error
    assert provider.created == 2
    third = await run("standard", "standard-reuse")
    assert third.status == "succeeded", third.error
    assert provider.created == 2


@pytest.mark.asyncio
async def test_legacy_no_profile_request_stays_legacy(monkeypatch, tmp_path):
    enable_sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "python_execute")
    lane = TaskLane(tmp_path)
    provider = _provider(monkeypatch)
    assert lane.execution.schema_version == 1
    assert decode_execution_config(lane.run.execution_configuration or "{}").schema_version == 1

    result = await ToolGateway(lane.context().session_factory).execute_tool(
        claim=lane.claim,
        context=lane.context(),
        logical_call_id="python:legacy",
        ref=lane.ref,
        arguments={
            "source": "print(42)\n",
            "input_ids": {},
            "parameters": {},
            "output_media": {},
        },
        inputs=(),
        control=InvocationControl(),
    )
    assert result.status == "succeeded", result.error
    assert provider.created == 1
    assert sandbox_tool.runtime_requirements().image == settings.sandbox_image


@pytest.mark.asyncio
async def test_v5_missing_selector_fails_closed(monkeypatch, tmp_path):
    catalog = _catalog(_all_profiles())
    execution = _execution(catalog)
    lane = _task_lane(monkeypatch, tmp_path, execution)
    provider = _provider(monkeypatch)

    with pytest.raises(HTTPException):
        await ToolGateway(lane.context().session_factory).execute_tool(
            claim=lane.claim,
            context=lane.context(),
            logical_call_id="python:missing-profile",
            ref=lane.ref,
            arguments={
                "source": "print(42)\n",
                "input_ids": {},
                "parameters": {},
                "output_media": {},
            },
            inputs=(),
            control=InvocationControl(),
        )
    assert provider.created == 0
    assert provider.commands == []
