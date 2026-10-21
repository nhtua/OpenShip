from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtifactContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactRef(ArtifactContract):
    id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=127)


class ArtifactSummary(ArtifactRef):
    conversation_id: UUID
    project_id: UUID
    name: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1, max_length=32)
    status: Literal["staging", "ready", "deleting", "deleted", "unavailable"]
    created_at: datetime
    mutability: Literal["immutable", "mutable"] = "immutable"


class StagedArtifact(ArtifactContract):
    artifact_id: UUID
    relative_staging_key: str = Field(min_length=1, max_length=512)
    observed: ArtifactRef

    @field_validator("relative_staging_key")
    @classmethod
    def _safe_relative_key(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise ValueError("staging key must be a normalized relative path")
        if "\\" in value or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("staging key contains an invalid character")
        return value
