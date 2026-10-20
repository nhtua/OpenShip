from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from src.openship.artifacts.contracts import ArtifactRef

TOOL_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


def canonical_json(value: Any) -> bytes:
    """Serialize JSON deterministically and reject values JSON cannot represent."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("value is not finite, serializable JSON") from exc


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CapabilityRef(FrozenModel):
    id: str = Field(pattern=TOOL_ID_PATTERN)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manifest_digest: str = Field(pattern=SHA256_PATTERN)


class OutputPolicy(FrozenModel):
    name: str = Field(min_length=1, max_length=255)
    mutability: Literal["immutable", "mutable"]


class CapabilityManifest(FrozenModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=TOOL_ID_PATTERN)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    implementation_digest: str = Field(pattern=SHA256_PATTERN)
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect: Literal["read_only_external", "read_only_local"]
    enablement_requirements: tuple[str, ...] = ()
    limits: dict[str, int | float | str]
    outputs: tuple[OutputPolicy, ...]
    execution_kind: Literal["maintained"] = "maintained"
    credential_binding_ref: str | None = Field(
        default=None, min_length=1, max_length=128
    )

    @model_validator(mode="after")
    def _digest_matches_manifest(self) -> CapabilityManifest:
        unsigned = self.model_dump(mode="json", exclude={"manifest_digest"})
        if canonical_digest(unsigned) != self.manifest_digest:
            raise ValueError("manifest_digest does not match the canonical manifest")
        return self


class CapabilitySummary(FrozenModel):
    id: str = Field(pattern=TOOL_ID_PATTERN)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect: Literal["read_only_external", "read_only_local"]
    enabled: StrictBool
    available: StrictBool
    unavailable_reason: (
        Literal["disabled", "missing_credentials", "runtime_unavailable"] | None
    ) = None


class ExecutionLimits(FrozenModel):
    operation_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_http_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    max_observation_bytes: int = Field(default=64 * 1024, ge=1)
    max_model_result_bytes: int = Field(default=16 * 1024, ge=1)
    max_file_read_bytes: int = Field(default=16 * 1024, ge=1)


class LoopLimits(FrozenModel):
    deadline_seconds: int = Field(default=120, ge=1)
    max_tool_calls: int = Field(default=2, ge=0)
    max_model_turns: int = Field(default=3, ge=1)
    max_generation_attempts: int = Field(default=3, ge=1)
    max_output_tokens_per_request: int = Field(default=4000, ge=1)
    max_total_output_tokens: int = Field(default=12000, ge=1)


class ExecutionConfig(FrozenModel):
    schema_version: Literal[1] = 1
    graph_kind: Literal["chat", "tool"]
    graph_version: int = Field(ge=1)
    allowed_capabilities: tuple[CapabilityRef, ...] = ()
    selected_artifacts: tuple[ArtifactRef, ...] = ()
    credential_binding_refs: tuple[str, ...] = ()
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    loop_limits: LoopLimits = Field(default_factory=LoopLimits)
    settings: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class AuthorizationContext(FrozenModel):
    principal_id: UUID
    project_id: UUID
    conversation_id: UUID
    action: str = Field(min_length=1, max_length=64)
    target_id: str = Field(min_length=1, max_length=128)


class ToolCommandRequest(FrozenModel):
    client_request_id: UUID
    capability: CapabilityRef
    arguments: dict[str, Any]

    @field_validator("client_request_id", mode="before")
    @classmethod
    def _parse_request_id(cls, value: Any) -> UUID:
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as exc:
                raise ValueError("client_request_id must be a UUID") from exc
        return value


class ToolInvocationContext:
    """Runtime-only authority and resolvers; never place this in graph state."""

    __slots__ = (
        "artifact_store",
        "authorization",
        "credential_resolver",
        "execution",
        "session_factory",
    )

    def __init__(
        self,
        authorization,
        execution,
        credential_resolver,
        artifact_store=None,
        session_factory=None,
    ):
        self.authorization = authorization
        self.execution = execution
        self.credential_resolver = credential_resolver
        self.artifact_store = artifact_store
        self.session_factory = session_factory


class ToolFailure(FrozenModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=512)


class PublishedToolResult(FrozenModel):
    operation_id: UUID
    status: Literal["succeeded", "failed", "cancelled", "interrupted"]
    result: dict[str, Any] | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    error: ToolFailure | None = None
