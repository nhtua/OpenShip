"""Contracts for general, operator-provisioned Python sandbox execution."""

import hashlib
import json
import uuid
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import ArtifactStorageError
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import (
    mark_artifact_ready,
    release_artifact_reservation,
    reserve_artifact,
)
from src.openship.capabilities.contracts import canonical_json
from src.openship.execution.admission import require_live_claim
from src.openship.execution.models import ToolOperation
from src.openship.providers.errors import ProviderError
from src.openship.sandboxes.contracts import (
    ExecutionRequest,
    InputFile,
    OutputSpec,
    _alias,
)
from src.openship.sandboxes.outputs import OutputValidationFailed, validate_outputs


class PythonArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: str | None = None
    source_id: UUID | None = None
    input_ids: dict[str, UUID] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_media: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_id", mode="before")
    @classmethod
    def parse_source_id(cls, value):
        return UUID(value) if isinstance(value, str) else value

    @field_validator("input_ids", mode="before")
    @classmethod
    def parse_input_ids(cls, value):
        if not isinstance(value, dict):
            return value
        return {key: UUID(item) if isinstance(item, str) else item for key, item in value.items()}

    @model_validator(mode="after")
    def validate_request(self):
        if (self.source is None) == (self.source_id is None):
            raise ValueError("exactly one source is required")
        if self.source is not None and (not self.source.strip() or len(self.source.encode()) > 256 * 1024):
            raise ValueError("source is empty or too large")
        if len(self.input_ids) > 8 or len(self.output_media) > 8:
            raise ValueError("too many files")
        for alias in (*self.input_ids, *self.output_media):
            _alias(alias)
        if any(media not in ("text/plain", "application/json") for media in self.output_media.values()):
            raise ValueError("unsupported output media")
        if len(canonical_json(self.parameters)) > 16 * 1024:
            raise ValueError("parameters exceed limit")
        return self


class PythonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: ArtifactRef
    outputs: dict[str, ArtifactRef]
    exit_code: int
    stdout: str
    stderr: str


class PythonObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: ArtifactRef
    outputs: dict[str, bytes]
    exit_code: int
    stdout: str
    stderr: str


def validate_python_observation(operation, value) -> PythonObservation:
    observation = PythonObservation.model_validate(value)
    arguments = PythonArguments.model_validate_json(operation.arguments_json)
    if (set(observation.outputs) != set(arguments.output_media)
            or observation.exit_code != 0
            or len(observation.stdout.encode()) + len(observation.stderr.encode()) > 256 * 1024):
        raise OutputValidationFailed("invalid Python result")
    return observation


_SOURCE_NAMESPACE = uuid.UUID("b16569c2-80c8-43ae-ab92-f1e8d29ca43a")


def capture_python_source(context, claim, operation_id: UUID, source: bytes) -> ArtifactRef:
    """Capture exact source once per authorized operation, before sandbox dispatch."""
    if not source or len(source) > 256 * 1024:
        raise ProviderError("invalid_response", safe_message="Python source is empty or too large.")
    digest = hashlib.sha256(source).hexdigest()
    request_key = str(uuid.uuid5(_SOURCE_NAMESPACE, str(operation_id)))
    with context.session_factory() as db:
        run = require_live_claim(db, claim, lock=True)
        operation = db.query(ToolOperation).filter(
            ToolOperation.id == operation_id, ToolOperation.run_id == run.id,
            ToolOperation.project_id == run.project_id,
            ToolOperation.conversation_id == run.conversation_id,
            ToolOperation.status == "running", ToolOperation.capability_id == "python_execute",
        ).with_for_update().first()
        if operation is None:
            raise ProviderError("lease_lost")
        row = db.query(Artifact).filter(
            Artifact.conversation_id == run.conversation_id, Artifact.request_key == request_key,
        ).with_for_update().first()
        if row is not None:
            if (row.status != "ready" or row.sha256 != digest or row.size_bytes != len(source)
                    or row.originating_run_id != run.id or row.originating_operation_id != operation_id):
                raise ProviderError("invalid_response", safe_message="Saved Python source cannot be reused safely.")
            with context.artifact_store.open_verified(row) as existing:
                if existing.read() != source:
                    raise ProviderError("invalid_response", safe_message="Saved Python source cannot be verified.")
            return ArtifactRef(id=row.id, sha256=row.sha256, size_bytes=row.size_bytes,
                               media_type=row.media_type)
        row = reserve_artifact(
            db, conversation_id=run.conversation_id, project_id=run.project_id,
            max_bytes=len(source), kind="upload", request_key=request_key,
            display_name="generated-python.py", media_type="text/plain", source="generated",
            originating_run_id=run.id, originating_operation_id=operation_id,
        )
        db.commit()
        artifact_id, storage_key = row.id, row.relative_storage_key
        project_id, conversation_id = run.project_id, run.conversation_id
    ref = ArtifactRef(id=artifact_id, sha256=digest, size_bytes=len(source), media_type="text/plain")
    store = context.artifact_store
    commit_started = False
    try:
        staged = store.stage(artifact_id, (source,), ref, project_id=project_id,
                             conversation_id=conversation_id)
        with context.session_factory() as db:
            require_live_claim(db, claim, lock=True)
            operation = db.query(ToolOperation).filter(
                ToolOperation.id == operation_id, ToolOperation.run_id == claim.run_id,
                ToolOperation.status == "running", ToolOperation.capability_id == "python_execute",
            ).with_for_update().first()
            if operation is None:
                raise ProviderError("lease_lost")
            store.publish_file(staged, storage_key)
            mark_artifact_ready(db, artifact_id, ref)
            commit_started = True
            db.commit()
            commit_started = False
        return ref
    except BaseException:
        if not commit_started:
            try:
                store.remove_staging(artifact_id, project_id, conversation_id)
                store.remove_unpublished(storage_key, expected_size=ref.size_bytes,
                                         expected_sha256=ref.sha256)
            except ArtifactStorageError:
                pass
            with context.session_factory() as db:
                try:
                    release_artifact_reservation(db, artifact_id)
                    db.commit()
                except (HTTPException, ArtifactStorageError, SQLAlchemyError, OSError):
                    db.rollback()
        raise


async def python_execute(arguments: PythonArguments, invocation, control) -> PythonObservation:
    """Execute a single fenced request through the configured sandbox client."""
    from src.openship.sandboxes import tool as sandbox_tool
    from src.openship.sandboxes.sessions import (
        clear_command_uncertain,
        get_or_create_sandbox,
        mark_command_uncertain,
    )

    claim = invocation.claim
    if claim is None or invocation.artifact_store is None:
        raise ProviderError("execution_interrupted")
    with invocation.session_factory() as db:
        require_live_claim(db, claim, lock=True)
        operation = db.query(ToolOperation).filter(
            ToolOperation.id == invocation.operation_id, ToolOperation.run_id == claim.run_id,
            ToolOperation.status == "running", ToolOperation.capability_id == "python_execute",
        ).first()
        if operation is None:
            raise ProviderError("lease_lost")
        try:
            refs = tuple(ArtifactRef.model_validate_json(json.dumps(item))
                         for item in json.loads(operation.input_artifact_refs))
        except (TypeError, ValueError):
            raise ProviderError("invalid_response") from None
    by_id = {ref.id: ref for ref in refs}
    if arguments.source is not None:
        source = capture_python_source(invocation, claim, invocation.operation_id,
                                       arguments.source.encode("utf-8"))
    else:
        source = by_id.get(arguments.source_id)
        if source is None:
            raise ProviderError("invalid_response")
    try:
        inputs = tuple(InputFile(alias=alias, artifact=by_id[artifact_id])
                       for alias, artifact_id in sorted(arguments.input_ids.items()))
    except KeyError:
        raise ProviderError("invalid_response") from None
    request = ExecutionRequest(
        root_run_id=claim.run_id, operation_id=invocation.operation_id,
        requirements=sandbox_tool.runtime_requirements(), source=source, inputs=inputs,
        parameters=dict(arguments.parameters),
        outputs=tuple(OutputSpec(alias=alias, media_type=media, kind="data")
                      for alias, media in sorted(arguments.output_media.items())),
    )
    with invocation.session_factory() as db:
        files = sandbox_tool.prepare_execution(db, claim=claim, request=request,
                                               store=invocation.artifact_store)
    client = sandbox_tool.sandbox_client_factory()
    try:
        handle = await get_or_create_sandbox(
            invocation.session_factory, client, claim=claim, root_run_id=claim.run_id,
            requirements=request.requirements,
        )
        mark_command_uncertain(invocation.session_factory, claim=claim,
                               sandbox_id=handle.sandbox_id, operation_id=invocation.operation_id)
        try:
            result = await client.execute(handle, request, files, control)
        except BaseException:
            if not client.command_uncertain:
                clear_command_uncertain(invocation.session_factory, claim=claim,
                                        sandbox_id=handle.sandbox_id, operation_id=invocation.operation_id)
            raise
        clear_command_uncertain(invocation.session_factory, claim=claim,
                                sandbox_id=handle.sandbox_id, operation_id=invocation.operation_id)
        return PythonObservation(source=source, outputs=validate_outputs(request, result),
                                 exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)
    finally:
        await client.aclose()
