from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from typing import Any, Literal, TypeAlias
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


class CapabilityManifestV2(FrozenModel):
    """Version 2 replaces installation variable names with connector kinds."""

    schema_version: Literal[2] = 2
    id: str = Field(pattern=TOOL_ID_PATTERN)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    implementation_digest: str = Field(pattern=SHA256_PATTERN)
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    description: str = Field(min_length=1, max_length=1024)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect: Literal["read_only_external", "read_only_local"]
    enablement_requirements: tuple[str, ...] = ()
    limits: dict[str, int | float | str]
    outputs: tuple[OutputPolicy, ...]
    connector_kinds: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    target_kinds: tuple[str, ...] = ()
    execution_kind: Literal["maintained"] = "maintained"

    @field_validator("connector_kinds", "operations", "target_kinds")
    @classmethod
    def _unique_nonempty_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or value.strip() != value for value in values):
            raise ValueError("manifest names must be nonempty and trimmed")
        if len(values) != len(set(values)):
            raise ValueError("manifest names must be unique")
        return values

    @model_validator(mode="after")
    def _digest_matches_manifest(self) -> CapabilityManifestV2:
        if self.effect == "read_only_external" and not self.connector_kinds:
            raise ValueError("external v2 capabilities require a connector kind")
        unsigned = self.model_dump(mode="json", exclude={"manifest_digest"})
        if canonical_digest(unsigned) != self.manifest_digest:
            raise ValueError("manifest_digest does not match the canonical manifest")
        return self


CapabilityManifestLike: TypeAlias = CapabilityManifest | CapabilityManifestV2


def _json_object(
    payload: str | bytes | dict[str, Any], *, label: str
) -> dict[str, Any]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError(f"{label} must be a JSON object")
    return payload


def decode_capability_manifest(
    payload: str | bytes | dict[str, Any],
) -> CapabilityManifestLike:
    raw = _json_object(payload, label="capability manifest")
    encoded = payload if isinstance(payload, (str, bytes)) else canonical_json(raw)
    version = raw.get("schema_version")
    if version == 1:
        return CapabilityManifest.model_validate_json(encoded)
    if version == 2:
        return CapabilityManifestV2.model_validate_json(encoded)
    raise ValueError("unsupported capability manifest version")


class CapabilitySummary(FrozenModel):
    id: str = Field(pattern=TOOL_ID_PATTERN)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect: Literal["read_only_external", "read_only_local"]
    enabled: StrictBool
    deployment_restricted: StrictBool = False
    available: StrictBool
    unavailable_reason: (
        Literal[
            "disabled",
            "deployment_restricted",
            "missing_credentials",
            "runtime_unavailable",
        ]
        | None
    ) = None


class ProjectCapabilitySummary(FrozenModel):
    """Project permission and Connector readiness are separate projections."""

    id: str = Field(pattern=TOOL_ID_PATTERN)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect: Literal["read_only_external", "read_only_local"]
    permitted: StrictBool
    policy_enabled: StrictBool = True
    deployment_restricted: StrictBool = False
    unavailable_reason: (
        Literal[
            "disabled_by_operator",
            "deployment_restricted",
            "runtime_unavailable",
            "connector_required",
            "connector_ambiguous",
            "target_required",
            "policy_unavailable",
        ]
        | None
    ) = None
    required_connector_kinds: tuple[str, ...] = ()
    connector_readiness: dict[
        str, Literal["ready", "required", "ambiguous", "unavailable"]
    ] = Field(default_factory=dict)


class ExecutionLimits(FrozenModel):
    operation_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_http_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    max_observation_bytes: int = Field(default=64 * 1024, ge=1)
    max_model_result_bytes: int = Field(default=16 * 1024, ge=1)
    max_file_read_bytes: int = Field(default=16 * 1024, ge=1)


class LoopLimits(FrozenModel):
    deadline_seconds: int = Field(default=1800, ge=1, le=3600)
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


class ConnectorRef(FrozenModel):
    """A pinned connector configuration and its write-only credential version."""

    id: UUID
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    configuration_revision: int = Field(ge=1)
    visibility: Literal["global", "project"]
    credential_version_id: UUID


class ResourceBindingRef(FrozenModel):
    """An immutable owned target and the connector revision authorized for it."""

    id: UUID
    revision: int = Field(ge=1)
    connector_id: UUID
    connector_revision: int = Field(ge=1)


class ExecutionConfigV2(FrozenModel):
    schema_version: Literal[2] = 2
    graph_kind: Literal["chat", "tool"]
    graph_version: int = Field(ge=1)
    allowed_capabilities: tuple[CapabilityRef, ...] = ()
    selected_artifacts: tuple[ArtifactRef, ...] = ()
    connector_refs: tuple[ConnectorRef, ...] = ()
    resource_binding_refs: tuple[ResourceBindingRef, ...] = ()
    selected_resource_binding_refs: tuple[ResourceBindingRef, ...] = ()
    admitted_context_message_ids: tuple[UUID, ...] = ()
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    loop_limits: LoopLimits = Field(default_factory=LoopLimits)


class ExecutionConfigV3(ExecutionConfigV2):
    """Version 3 adds a pinned preferred search provider to managed chat."""

    schema_version: Literal[3] = 3
    preferred_search_kind: Literal["brave", "tavily"] | None = None


ExecutionConfigLike: TypeAlias = ExecutionConfig | ExecutionConfigV2 | ExecutionConfigV3


def decode_execution_config(
    payload: str | bytes | dict[str, Any],
) -> ExecutionConfigLike:
    """Decode only the exact persisted configuration versions this runtime supports."""
    raw = _json_object(payload, label="execution configuration")
    encoded = payload if isinstance(payload, (str, bytes)) else canonical_json(raw)
    version = raw.get("schema_version")
    if version == 1:
        return ExecutionConfig.model_validate_json(encoded)
    if version == 2:
        return ExecutionConfigV2.model_validate_json(encoded)
    if version == 3:
        return ExecutionConfigV3.model_validate_json(encoded)
    raise ValueError("unsupported execution configuration version")


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
    connector_selections: dict[Literal["brave", "tavily"], UUID] = Field(
        default_factory=dict, max_length=2
    )

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

    @field_validator("connector_selections", mode="before")
    @classmethod
    def _parse_connector_selections(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        parsed = {}
        for kind, connector_id in value.items():
            if not isinstance(kind, str) or kind not in {"brave", "tavily"}:
                raise ValueError("connector selection kind is invalid")
            if isinstance(connector_id, str):
                try:
                    connector_id = UUID(connector_id)
                except ValueError as exc:
                    raise ValueError("connector selection ID must be a UUID") from exc
            parsed[kind] = connector_id
        return parsed


class ToolInvocationContext:
    """Runtime-only authority and resolvers; never place this in graph state."""

    __slots__ = (
        "_aws_accesses",
        "artifact_store",
        "authorization",
        "aws_access_factory",
        "credential_resolver",
        "execution",
        "operation_id",
        "session_factory",
    )

    def __init__(
        self,
        authorization,
        execution,
        credential_resolver,
        artifact_store=None,
        session_factory=None,
        operation_id=None,
        aws_access_factory=None,
    ):
        self.authorization = authorization
        self.execution = execution
        self.credential_resolver = credential_resolver
        self.artifact_store = artifact_store
        self.session_factory = session_factory
        self.operation_id = operation_id
        self.aws_access_factory = aws_access_factory
        self._aws_accesses = []

    async def close_runtime_accesses(self) -> None:
        accesses, self._aws_accesses = tuple(self._aws_accesses), []
        for access in accesses:
            with suppress(Exception):
                await access.aclose()


class ToolFailure(FrozenModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=512)


class PublishedToolResult(FrozenModel):
    operation_id: UUID
    status: Literal["succeeded", "failed", "cancelled", "interrupted"]
    result: dict[str, Any] | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    error: ToolFailure | None = None
