"""AES-256-GCM credential envelopes with Connector-bound authenticated data."""

from __future__ import annotations

import json
import os
from typing import Any, Literal
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...capabilities.contracts import canonical_json
from ..errors import CredentialStoreUnavailable
from .keys import EncryptionKeyRing

MAX_CREDENTIAL_PAYLOAD_BYTES = 16 * 1024
_NONCE_BYTES = 12
_TAG_BYTES = 16


class CredentialContext(BaseModel):
    """Non-secret identity fields authenticated with every encrypted payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    installation_id: UUID
    visibility: Literal["global", "project"]
    project_id: UUID | None
    connector_id: UUID
    credential_version_id: UUID
    schema_version: Literal[1]
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @model_validator(mode="after")
    def _scope_shape(self) -> CredentialContext:
        if (self.visibility == "global") != (self.project_id is None):
            raise ValueError("credential scope is invalid")
        if self.kind == "aws_bootstrap" and self.visibility != "project":
            raise ValueError("AWS credentials must be project scoped")
        return self

    def associated_data(self) -> bytes:
        owner_scope = (
            "installation-global" if self.visibility == "global" else str(self.project_id)
        )
        return canonical_json(
            {
                "installation_id": str(self.installation_id),
                "visibility": self.visibility,
                "owner_scope": owner_scope,
                "project_id": str(self.project_id) if self.project_id is not None else None,
                "connector_id": str(self.connector_id),
                "credential_version_id": str(self.credential_version_id),
                "schema_version": self.schema_version,
                "kind": self.kind,
            }
        )


class CredentialEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    key_id: str = Field(min_length=1, max_length=64)
    nonce: bytes
    ciphertext: bytes

    def __repr__(self) -> str:
        return (
            f"CredentialEnvelope(key_id={self.key_id!r}, nonce_bytes={len(self.nonce)}, "
            f"ciphertext_bytes={len(self.ciphertext)})"
        )

    __str__ = __repr__


class CredentialStore:
    """Small crypto boundary; key loading remains explicit and lazy at call sites."""

    __slots__ = ("_installation_id", "_keyring")

    def __init__(self, keyring: EncryptionKeyRing, *, installation_id: UUID) -> None:
        self._keyring = keyring
        self._installation_id = installation_id

    def __repr__(self) -> str:
        return f"CredentialStore(key_ids={self._keyring.key_ids!r})"

    def encrypt(self, context: CredentialContext, payload: dict[str, Any]) -> CredentialEnvelope:
        try:
            if not isinstance(context, CredentialContext):
                raise TypeError
            if context.installation_id != self._installation_id:
                raise CredentialStoreUnavailable()
            if not isinstance(payload, dict):
                raise TypeError
            plaintext = canonical_json(payload)
            if not plaintext or len(plaintext) > MAX_CREDENTIAL_PAYLOAD_BYTES:
                raise ValueError
            nonce = os.urandom(_NONCE_BYTES)
            if not isinstance(nonce, bytes) or len(nonce) != _NONCE_BYTES:
                raise ValueError
            key_id = self._keyring.active_key_id
            ciphertext = AESGCM(self._keyring.key(key_id)).encrypt(
                nonce, plaintext, context.associated_data()
            )
            return CredentialEnvelope(key_id=key_id, nonce=nonce, ciphertext=ciphertext)
        except CredentialStoreUnavailable:
            raise
        except (OSError, RecursionError, TypeError, ValueError):
            raise CredentialStoreUnavailable() from None

    def decrypt(
        self, context: CredentialContext, envelope: CredentialEnvelope
    ) -> dict[str, Any]:
        try:
            if not isinstance(context, CredentialContext) or not isinstance(
                envelope, CredentialEnvelope
            ):
                raise TypeError
            if context.installation_id != self._installation_id:
                raise CredentialStoreUnavailable()
            if (
                len(envelope.nonce) != _NONCE_BYTES
                or len(envelope.ciphertext) < _TAG_BYTES
                or len(envelope.ciphertext) > MAX_CREDENTIAL_PAYLOAD_BYTES + _TAG_BYTES
            ):
                raise ValueError
            plaintext = AESGCM(self._keyring.key(envelope.key_id)).decrypt(
                envelope.nonce,
                envelope.ciphertext,
                context.associated_data(),
            )
            if not plaintext or len(plaintext) > MAX_CREDENTIAL_PAYLOAD_BYTES:
                raise ValueError
            payload = json.loads(plaintext.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError
            return payload
        except (CredentialStoreUnavailable, InvalidTag):
            raise CredentialStoreUnavailable() from None
        except (OSError, RecursionError, TypeError, ValueError, UnicodeDecodeError):
            raise CredentialStoreUnavailable() from None
