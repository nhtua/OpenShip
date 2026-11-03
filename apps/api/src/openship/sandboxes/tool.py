"""Sandbox-backed analysis tool: capture, execute and hand validated data off.

OpenShip prepares the source program and selected input files from current
artifact bytes and validates what the run returns; OpenSandbox owns the
lifecycle, expiration and cleanup of its sandbox. Nothing here waits for a
sandbox to be deleted, and provider round-trips never share a database
transaction with admission or publication.
"""

import hashlib
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import ArtifactStorageError
from src.openship.artifacts.models import Artifact
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef, PublishedToolResult
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.execution.gateway import ToolGateway
from src.openship.execution.publication import ToolPublicationError
from src.openship.runs.models import Run
from src.openship.sandboxes.client import OpenSandboxClient, SandboxUnavailable
from src.openship.sandboxes.contracts import (
    ExecutionRequest,
    InputFile,
    OutputSpec,
    PreparedFiles,
    RuntimeRequirements,
    _alias,
)
from src.openship.sandboxes.sessions import get_or_create_sandbox

from .outputs import OutputValidationFailed, validate_outputs

ANALYSIS_ID = "sandbox_analysis"
_TEXT_MEDIA = "text/plain"
_JSON_MEDIA = "application/json"
_MEDIA_TYPES = (_TEXT_MEDIA, _JSON_MEDIA)


class AnalysisArguments(BaseModel):
    """Declared source, selected inputs and expected data outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_id: UUID
    input_ids: dict[str, UUID] = {}
    parameters: dict[str, Any] = {}
    output_media: dict[str, str]

    @field_validator("source_id", mode="before")
    def _parse_source_id(cls, value):
        if isinstance(value, str):
            return UUID(value)
        return value

    @field_validator("input_ids", mode="before")
    def _parse_input_ids(cls, value):
        if not isinstance(value, dict):
            return value
        return {
            alias: UUID(artifact_id) if isinstance(artifact_id, str) else artifact_id
            for alias, artifact_id in value.items()
        }

    @field_validator("input_ids")
    def _valid_input_aliases(cls, value: dict[str, UUID]) -> dict[str, UUID]:
        if len(value) > 8:
            raise ValueError("too many selected inputs")
        for alias in value:
            _alias(alias)
        return value

    @field_validator("output_media")
    def _valid_output_specs(cls, value: dict[str, str]) -> dict[str, str]:
        if not value or len(value) > 8:
            raise ValueError("outputs must be between one and eight declared files")
        for alias, media in value.items():
            _alias(alias)
            if media not in _MEDIA_TYPES:
                raise ValueError("output media type is not a declared data type")
        return value


class AnalysisObservation(BaseModel):
    """Validated output bytes handed to the gateway's publication transaction."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outputs: dict[str, bytes]


class AnalysisResult(BaseModel):
    """Public result: one published artifact reference per declared output."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outputs: dict[str, ArtifactRef]


def analysis_evidence(observation: AnalysisObservation) -> dict[str, dict[str, Any]]:
    """Digest-and-size evidence per alias; the bytes themselves stay unstored."""
    return {
        alias: {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
        for alias, data in observation.outputs.items()
    }


def analysis_error(error: Exception) -> str | None:
    """Map private failures onto the closed publication vocabulary."""
    if isinstance(error, OutputValidationFailed):
        return "invalid_tool_output"
    return None


def runtime_requirements() -> RuntimeRequirements:
    """Derive the sandbox runtime identity from installation settings."""
    if not settings.sandbox_image:
        raise SandboxUnavailable("sandbox runtime image is not configured")
    return RuntimeRequirements(
        provider_id="openship-phase6b",
        profile_id=settings.sandbox_profile_id,
        image=settings.sandbox_image,
        dependency_identity=settings.sandbox_dependency_identity,
    )


def sandbox_client_factory() -> OpenSandboxClient:
    """Build one client from the opt-in profile; refuse an empty key."""
    if not settings.sandbox_enabled or not settings.sandbox_endpoint:
        raise SandboxUnavailable("sandbox profile is not configured")
    key_file = settings.sandbox_key_file
    try:
        key = key_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise SandboxUnavailable("sandbox credential is not available") from None
    if not key:
        raise SandboxUnavailable("sandbox credential manager refuses an empty key")
    return OpenSandboxClient(
        settings.sandbox_endpoint,
        key,
        http=httpx.AsyncClient(trust_env=False, follow_redirects=False),
    )


def analysis_capability_ref() -> CapabilityRef:
    """Pin the installed analysis capability identity."""
    manifest = next(item for item in list_capability_manifests() if item.id == ANALYSIS_ID)
    return CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )


def _execution_request(
    db, claim, operation_id: UUID, arguments: AnalysisArguments
) -> ExecutionRequest:
    """Resolve one execution request against current, scope-checked artifact rows."""
    run = db.query(Run).filter(Run.id == claim.run_id).first()
    if run is None:
        raise ToolPublicationError("execution_interrupted")

    def current(artifact_id: UUID) -> ArtifactRef:
        row = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if (
            row is None
            or row.conversation_id != run.conversation_id
            or row.project_id != run.project_id
            or row.status != "ready"
        ):
            raise ToolPublicationError("artifact_not_found")
        return ArtifactRef(
            id=row.id,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            media_type=row.media_type,
        )

    return ExecutionRequest(
        root_run_id=claim.run_id,
        operation_id=operation_id,
        requirements=runtime_requirements(),
        source=current(arguments.source_id),
        inputs=tuple(
            InputFile(alias=alias, artifact=current(artifact_id))
            for alias, artifact_id in sorted(arguments.input_ids.items())
        ),
        parameters=dict(arguments.parameters),
        outputs=tuple(
            OutputSpec(alias=alias, media_type=media, kind="data")
            for alias, media in sorted(arguments.output_media.items())
        ),
    )


def prepare_execution(db, *, claim, request: ExecutionRequest, store) -> PreparedFiles:
    """Read the pinned current bytes for one execution request."""
    run = db.query(Run).filter(Run.id == request.root_run_id).first()
    if run is None:
        raise ToolPublicationError("execution_interrupted")

    def load(ref: ArtifactRef) -> bytes:
        row = db.query(Artifact).filter(Artifact.id == ref.id).first()
        if (
            row is None
            or row.conversation_id != run.conversation_id
            or row.project_id != run.project_id
            or row.status != "ready"
            or row.sha256 != ref.sha256
            or row.size_bytes != ref.size_bytes
        ):
            raise ToolPublicationError("artifact_not_found")
        try:
            with store.open_verified(row) as handle:
                data = handle.read()
        except ArtifactStorageError:
            raise ToolPublicationError("artifact_unavailable") from None
        if (
            len(data) != row.size_bytes
            or hashlib.sha256(data).hexdigest() != row.sha256
        ):
            raise ToolPublicationError("artifact_unavailable")
        return data

    return PreparedFiles(
        source=load(request.source),
        inputs={item.alias: load(item.artifact) for item in request.inputs},
    )


async def analysis_execute(arguments, invocation, control):
    """Run one prepared sandbox analysis and return only validated output bytes."""
    claim = getattr(invocation, "claim", None)
    if claim is None:
        raise ToolPublicationError("execution_interrupted")
    if invocation.artifact_store is None:
        raise ToolPublicationError("artifact_storage_unavailable")
    with invocation.session_factory() as db:
        request = _execution_request(db, claim, invocation.operation_id, arguments)
        files = prepare_execution(
            db, claim=claim, request=request, store=invocation.artifact_store
        )

    client = sandbox_client_factory()
    handle = await get_or_create_sandbox(
        invocation.session_factory,
        client,
        claim=claim,
        root_run_id=claim.run_id,
        requirements=request.requirements,
    )
    result = await client.execute(handle, request, files, control)
    outputs = validate_outputs(request, result)
    return AnalysisObservation(outputs=outputs)


async def execute_analysis(
    context, request: ExecutionRequest, control
) -> PublishedToolResult:
    """Submit one authorized analysis through the normal gateway transaction."""
    arguments = {
        "source_id": str(request.source.id),
        "input_ids": {item.alias: str(item.artifact.id) for item in request.inputs},
        "parameters": request.parameters,
        "output_media": {item.alias: item.media_type for item in request.outputs},
    }
    gateway = ToolGateway(context.session_factory or SessionLocal)
    return await gateway.execute_tool(
        claim=context.claim,
        context=context,
        logical_call_id=f"sandbox:{request.operation_id}",
        ref=analysis_capability_ref(),
        arguments=arguments,
        inputs=(request.source, *(item.artifact for item in request.inputs)),
        control=control,
    )
