"""Runtime-only AWS source and temporary-session contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...execution.aws_sdk import AwsSdkAuth


class AwsBootstrapAuth(BaseModel):
    """Validated encrypted source material; secret fields never appear in repr."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    access_key_id: str = Field(min_length=8, max_length=128, repr=False)
    secret_access_key: str = Field(min_length=16, max_length=256, repr=False)
    session_token: str | None = Field(default=None, max_length=8192, repr=False)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_material(self) -> AwsBootstrapAuth:
        values = (self.access_key_id, self.secret_access_key)
        if any(
            value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            for value in values
        ):
            raise ValueError("AWS source material is invalid")
        if self.session_token is not None:
            if (
                not self.session_token
                or self.session_token != self.session_token.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in self.session_token)
                or self.expires_at is None
            ):
                raise ValueError("temporary AWS source material requires an expiry")
        elif self.expires_at is not None:
            raise ValueError("AWS source expiry requires a session token")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("AWS source expiry must include a timezone")
        return self

    def to_payload(self) -> dict[str, str | None]:
        return {
            "access_key_id": self.access_key_id,
            "secret_access_key": self.secret_access_key,
            "session_token": self.session_token,
            "expires_at": (
                self.expires_at.astimezone(UTC).isoformat()
                if self.expires_at is not None
                else None
            ),
        }

    def to_sdk_auth(self) -> AwsSdkAuth:
        return AwsSdkAuth(
            access_key_id=self.access_key_id,
            secret_access_key=self.secret_access_key,
            session_token=self.session_token,
        )


@dataclass(frozen=True, slots=True)
class AwsAssumedRole:
    """Verified temporary role material kept only in process memory."""

    auth: AwsSdkAuth = field(repr=False)
    expires_at: datetime
    account_id: str
    role_name: str


@dataclass(frozen=True, slots=True)
class AwsRoleCacheKey:
    project_id: UUID
    connector_id: UUID
    connector_revision: int
    auth_version_id: UUID
    role_arn: str
    region: str
    visibility: str
