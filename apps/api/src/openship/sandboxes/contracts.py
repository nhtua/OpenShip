"""Bounded, immutable contracts for sandbox requests and results."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.capabilities.contracts import canonical_digest, canonical_json


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeRequirements(Contract):
    provider_id: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(min_length=1, max_length=128)
    image: str = Field(min_length=1, max_length=512)
    dependency_identity: str = Field(min_length=1, max_length=128)
    cpu: str = "1"
    memory: str = "512Mi"

    @property
    def key(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


def _alias(value: str) -> str:
    if (not value or value in {".", ".."} or len(value.encode()) > 128
            or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in value)):
        raise ValueError("invalid file alias")
    return value


class InputFile(Contract):
    alias: str
    artifact: ArtifactRef

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, value: str) -> str:
        return _alias(value)


class OutputSpec(Contract):
    alias: str
    media_type: Literal["text/plain", "application/json", "image/png", "image/jpeg"]
    kind: Literal["data", "source"]
    expectation: str | None = None

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, value: str) -> str:
        return _alias(value)

    @model_validator(mode="after")
    def validate_expectation(self):
        if self.kind == "source" and (self.media_type != "text/plain" or not self.expectation or not self.expectation.strip()):
            raise ValueError("source output requires a text expectation")
        return self


class ExecutionRequest(Contract):
    root_run_id: UUID
    operation_id: UUID
    requirements: RuntimeRequirements
    source: ArtifactRef
    inputs: tuple[InputFile, ...] = Field(max_length=8)
    parameters: dict[str, Any]
    outputs: tuple[OutputSpec, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.source.size_bytes > 256 * 1024:
            raise ValueError("source too large")
        if any(item.artifact.size_bytes > 8 * 1024 * 1024 for item in self.inputs):
            raise ValueError("input too large")
        if sum(item.artifact.size_bytes for item in self.inputs) > 16 * 1024 * 1024:
            raise ValueError("inputs too large")
        for items in (self.inputs, self.outputs):
            if len({item.alias for item in items}) != len(items):
                raise ValueError("duplicate file alias")
        try:
            if len(canonical_json(self.parameters)) > 16 * 1024:
                raise ValueError("parameters too large")
        except (TypeError, RecursionError) as exc:
            raise ValueError("invalid parameters") from exc
        return self


class SandboxHandle(Contract):
    provider_id: str
    sandbox_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    requirements_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime


class PreparedFiles(Contract):
    source: bytes
    inputs: dict[str, bytes]


class ExecutionResult(Contract):
    exit_code: int
    stdout: str
    stderr: str
    outputs: dict[str, bytes]


class ReviewDecision(Contract):
    accepted: bool
    reason: str = Field(min_length=1, max_length=1024)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("reason")
    @classmethod
    def bounded_reason_bytes(cls, value: str) -> str:
        # Count JSON escaping as well as multibyte UTF-8. Eight decisions plus
        # maximal aliases/artifact refs must fit the 16-KiB public result bound.
        if len(canonical_json(value)) > 1024:
            raise ValueError("review reason exceeds its serialized byte bound")
        return value
