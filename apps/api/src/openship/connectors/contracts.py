"""Bounded, secret-free contracts for managed Connector records."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONNECTOR_KINDS = ("brave", "tavily", "aws")
SEARCH_CONNECTOR_KINDS = ("brave", "tavily")
CONNECTOR_VISIBILITIES = ("global", "project")
MAX_CONNECTOR_CONFIG_BYTES = 16 * 1024


def canonical_json(value: Any) -> bytes:
    """Serialize JSON deterministically, rejecting non-finite or invalid data."""
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


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ConnectorPublicRef(FrozenContract):
    id: UUID
    kind: Literal["brave", "tavily", "aws"]
    visibility: Literal["global", "project"]
    revision: int = Field(ge=1)


class ConnectorUsage(FrozenContract):
    """Non-secret Connector metadata safe for an eligible project owner."""

    id: UUID
    kind: Literal["brave", "tavily", "aws"]
    visibility: Literal["global", "project"]
    name: str = Field(min_length=1, max_length=255)
    status: Literal["configured", "disabled", "expired", "revoked", "unavailable"]
    revision: int = Field(ge=1)
    is_default: bool = False


class ConnectorCredentialStatus(FrozenContract):
    """Write-only secret state; the payload and encrypted bytes are never exposed."""

    credential_version_id: UUID
    status: Literal["configured", "expired", "revoked", "unavailable"]
    expires_at: str | None = None


class ConnectorConfiguration(FrozenContract):
    schema_version: Literal[1] = 1
    kind: Literal["brave", "tavily", "aws"]
    region: str | None = Field(default=None, min_length=1, max_length=64)
    expected_account_id: str | None = Field(default=None, pattern=r"^\d{12}$")
    basic_role_arn: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _configuration_matches_kind(self) -> ConnectorConfiguration:
        if self.kind == "aws":
            if not self.region or not self.expected_account_id or not self.basic_role_arn:
                raise ValueError("AWS Connector configuration is incomplete")
        elif any(
            value is not None
            for value in (self.region, self.expected_account_id, self.basic_role_arn)
        ):
            raise ValueError("search Connector configuration contains AWS fields")
        return self

    def canonical_bytes(self) -> bytes:
        encoded = canonical_json(self.model_dump(mode="json"))
        if len(encoded) > MAX_CONNECTOR_CONFIG_BYTES:
            raise ValueError("Connector configuration is too large")
        return encoded

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()
