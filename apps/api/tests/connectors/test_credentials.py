from __future__ import annotations

import base64
import json
import os
import uuid

import pytest

from src.openship.connectors.credentials.keys import (
    load_encryption_keyring,
    load_hmac_keyring,
)
from src.openship.connectors.credentials.store import (
    CredentialContext,
    CredentialStore,
)
from src.openship.connectors.errors import CredentialStoreUnavailable


def _write_key_file(path, key: bytes, *, active_key_id: str = "key-1"):
    path.write_text(
        json.dumps(
            {
                "active_key_id": active_key_id,
                "keys": {active_key_id: base64.b64encode(key).decode("ascii")},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _context(**updates) -> CredentialContext:
    values = {
        "installation_id": uuid.uuid4(),
        "visibility": "project",
        "project_id": uuid.uuid4(),
        "connector_id": uuid.uuid4(),
        "credential_version_id": uuid.uuid4(),
        "schema_version": 1,
        "kind": "brave_api_key",
    }
    values.update(updates)
    return CredentialContext(**values)


def test_versioned_keyrings_load_protected_files_without_exposing_key_material(tmp_path):
    encryption_path = tmp_path / "encryption.json"
    hmac_path = tmp_path / "idempotency.json"
    encryption_key = os.urandom(32)
    hmac_key = os.urandom(32)
    _write_key_file(encryption_path, encryption_key)
    _write_key_file(hmac_path, hmac_key)

    encryption = load_encryption_keyring(encryption_path)
    idempotency = load_hmac_keyring(hmac_path)

    assert encryption.active_key_id == "key-1"
    assert encryption.key("key-1") == encryption_key
    assert idempotency.active_key_id == "key-1"
    assert idempotency.key("key-1") == hmac_key
    assert encryption_key.hex() not in repr(encryption)
    assert hmac_key.hex() not in repr(idempotency)


@pytest.mark.parametrize("bad_bytes", [b"short", b"x" * 31, b"x" * 33])
def test_bad_or_unprotected_key_files_fail_with_fixed_safe_error(tmp_path, bad_bytes):
    path = tmp_path / "untrusted-keys.json"
    _write_key_file(path, bad_bytes)

    with pytest.raises(CredentialStoreUnavailable) as refused:
        load_encryption_keyring(path)

    assert str(refused.value) == "credential_store_unavailable"
    assert str(path) not in str(refused.value)
    assert base64.b64encode(bad_bytes).decode("ascii") not in repr(refused.value)

    path.chmod(0o644)
    with pytest.raises(CredentialStoreUnavailable, match="credential_store_unavailable"):
        load_encryption_keyring(path)


def test_missing_key_file_does_not_create_a_replacement(tmp_path):
    path = tmp_path / "missing-keys.json"

    with pytest.raises(CredentialStoreUnavailable, match="credential_store_unavailable"):
        load_encryption_keyring(path)

    assert not path.exists()


def test_encryption_uses_fresh_nonces_round_trips_and_redacts_secret_payload(tmp_path):
    key_path = tmp_path / "keys.json"
    _write_key_file(key_path, os.urandom(32))
    context = _context()
    store = CredentialStore(
        load_encryption_keyring(key_path), installation_id=context.installation_id
    )
    payload = {"api_key": "synthetic-secret-sentinel"}

    first = store.encrypt(context, payload)
    second = store.encrypt(context, payload)

    assert len(first.nonce) == 12
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert b"synthetic-secret-sentinel" not in first.ciphertext
    assert store.decrypt(context, first) == payload
    assert "synthetic-secret-sentinel" not in repr(first)
    assert str(first) == repr(first)
    with pytest.raises(CredentialStoreUnavailable, match="credential_store_unavailable"):
        store.decrypt(context, object())


@pytest.mark.parametrize(
    "field, replacement",
    [
        ("installation_id", uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        ("visibility", "global"),
        ("project_id", uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")),
        ("connector_id", uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")),
        (
            "credential_version_id",
            uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        ),
        ("kind", "tavily_api_key"),
        ("schema_version", 2),
    ],
)
def test_associated_data_prevents_ciphertext_substitution(tmp_path, field, replacement):
    key_path = tmp_path / "keys.json"
    _write_key_file(key_path, os.urandom(32))
    context = _context()
    store = CredentialStore(
        load_encryption_keyring(key_path), installation_id=context.installation_id
    )
    envelope = store.encrypt(context, {"api_key": "bound-secret"})
    changed_context = context.model_copy(update={field: replacement})

    with pytest.raises(CredentialStoreUnavailable, match="credential_store_unavailable"):
        store.decrypt(changed_context, envelope)


def test_nonce_and_ciphertext_tampering_fail_closed(tmp_path):
    key_path = tmp_path / "keys.json"
    _write_key_file(key_path, os.urandom(32))
    context = _context()
    store = CredentialStore(
        load_encryption_keyring(key_path), installation_id=context.installation_id
    )
    envelope = store.encrypt(context, {"api_key": "bound-secret"})

    changed_nonce = bytes([envelope.nonce[0] ^ 1]) + envelope.nonce[1:]
    changed_ciphertext = envelope.ciphertext[:-1] + bytes([envelope.ciphertext[-1] ^ 1])
    for changed in (
        envelope.model_copy(update={"nonce": changed_nonce}),
        envelope.model_copy(update={"ciphertext": changed_ciphertext}),
    ):
        with pytest.raises(
            CredentialStoreUnavailable, match="credential_store_unavailable"
        ):
            store.decrypt(context, changed)


def test_payload_limit_and_scope_shape_are_enforced_without_echoing_values(tmp_path):
    key_path = tmp_path / "keys.json"
    _write_key_file(key_path, os.urandom(32))
    context = _context()
    store = CredentialStore(
        load_encryption_keyring(key_path), installation_id=context.installation_id
    )

    at_limit = {"x": "a" * (16 * 1024 - 8)}
    assert len(json.dumps(at_limit, separators=(",", ":")).encode()) == 16 * 1024
    assert store.decrypt(context, store.encrypt(context, at_limit)) == at_limit

    with pytest.raises(CredentialStoreUnavailable, match="credential_store_unavailable"):
        store.encrypt(context, {"api_key": "x" * (16 * 1024)})

    with pytest.raises(ValueError):
        CredentialContext(
            installation_id=uuid.uuid4(),
            visibility="global",
            project_id=uuid.uuid4(),
            connector_id=uuid.uuid4(),
            credential_version_id=uuid.uuid4(),
            schema_version=1,
            kind="brave_api_key",
        )
    with pytest.raises(ValueError):
        CredentialContext(
            installation_id=uuid.uuid4(),
            visibility="global",
            project_id=None,
            connector_id=uuid.uuid4(),
            credential_version_id=uuid.uuid4(),
            schema_version=1,
            kind="aws_bootstrap",
        )


def test_idempotency_hmac_is_keyed_versioned_and_one_way(tmp_path):
    path = tmp_path / "idempotency.json"
    key = os.urandom(32)
    _write_key_file(path, key)
    keyring = load_hmac_keyring(path)

    key_id, digest = keyring.digest(b"submitted secret request")

    assert key_id == "key-1"
    assert digest != "submitted secret request"
    assert len(digest) == 64
    assert keyring.verify(key_id, b"submitted secret request", digest)
    assert not keyring.verify(key_id, b"different request", digest)
    assert key.hex() not in repr(keyring)
