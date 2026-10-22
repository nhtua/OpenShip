"""Lazy credential-store dependencies so plain chat does not require key files."""

from src.openship.config import settings

from ..errors import CredentialStoreUnavailable
from .keys import load_encryption_keyring, load_hmac_keyring
from .store import CredentialStore


def get_credential_store() -> CredentialStore:
    if settings.connector_installation_id is None:
        raise CredentialStoreUnavailable()
    return CredentialStore(
        load_encryption_keyring(settings.connector_encryption_key_file),
        installation_id=settings.connector_installation_id,
    )


def get_idempotency_keyring():
    return load_hmac_keyring(settings.connector_idempotency_hmac_key_file)
