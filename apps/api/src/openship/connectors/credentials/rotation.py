"""Exclusive key initialization and resumable credential re-encryption."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import ConnectorRequestLedger, CredentialVersion
from .keys import (
    EncryptionKeyRing,
    HmacKeyRing,
    load_encryption_keyring,
    load_hmac_keyring,
)
from .store import CredentialContext, CredentialEnvelope, CredentialStore

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_KEYS = 16


def _encode_keyring(active_key_id: str, keys: dict[str, bytes]) -> bytes:
    return (
        json.dumps(
            {
                "active_key_id": active_key_id,
                "keys": {
                    key_id: base64.b64encode(value).decode("ascii")
                    for key_id, value in sorted(keys.items())
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _create_exclusive(path: Path, contents: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as output:
        output.write(contents)
        output.flush()
        os.fsync(output.fileno())


def _atomic_replace(path: Path, contents: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".openship-keyring-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def initialize_key_files(
    encryption_path: str | Path,
    hmac_path: str | Path,
    *,
    key_id: str = "v1",
) -> None:
    """Create both protected keyrings once; never overwrite either destination."""
    encryption_file = Path(encryption_path)
    hmac_file = Path(hmac_path)
    if encryption_file.resolve() == hmac_file.resolve():
        raise ValueError("key_file_paths_must_differ")
    if _KEY_ID.fullmatch(key_id) is None:
        raise ValueError("invalid_key_id")
    encryption_bytes = _encode_keyring(key_id, {key_id: secrets.token_bytes(32)})
    hmac_bytes = _encode_keyring(key_id, {key_id: secrets.token_bytes(32)})
    created: list[Path] = []
    try:
        _create_exclusive(encryption_file, encryption_bytes)
        created.append(encryption_file)
        _create_exclusive(hmac_file, hmac_bytes)
        created.append(hmac_file)
    except Exception:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def rotate_key_file(
    path: str | Path,
    *,
    key_id: str,
    key_kind: str,
) -> None:
    """Add a fresh active key while preserving all old keys for safe migration."""
    if _KEY_ID.fullmatch(key_id) is None:
        raise ValueError("invalid_key_id")
    selected = Path(path)
    if key_kind == "encryption":
        ring: EncryptionKeyRing | HmacKeyRing = load_encryption_keyring(selected)
    elif key_kind == "hmac":
        ring = load_hmac_keyring(selected)
    else:
        raise ValueError("invalid_key_kind")
    if key_id in ring.key_ids:
        raise ValueError("key_id_already_exists")
    if len(ring.key_ids) >= _MAX_KEYS:
        raise ValueError("keyring_full")
    keys = {old_id: ring.key(old_id) for old_id in ring.key_ids}
    keys[key_id] = secrets.token_bytes(32)
    _atomic_replace(selected, _encode_keyring(key_id, keys))


def _context(record: CredentialVersion, installation_id: UUID) -> CredentialContext:
    return CredentialContext(
        installation_id=installation_id,
        visibility=record.visibility,
        project_id=record.project_id,
        connector_id=record.connector_id,
        credential_version_id=record.id,
        schema_version=record.schema_version,
        kind=record.kind,
    )


def reencrypt_credentials_batch(
    db: Session,
    store: CredentialStore,
    *,
    installation_id: UUID,
    batch_size: int = 100,
) -> int:
    """Re-encrypt one committed batch and retain each credential-version ID."""
    if not 1 <= batch_size <= 1000:
        raise ValueError("invalid_batch_size")
    query = (
        db.query(CredentialVersion)
        .filter(
            CredentialVersion.purged_at.is_(None),
            CredentialVersion.key_id != store.active_key_id,
        )
        .order_by(CredentialVersion.id)
        .limit(batch_size)
        .with_for_update()
    )
    records = query.all()
    try:
        for record in records:
            context = _context(record, installation_id)
            old = CredentialEnvelope(
                key_id=record.key_id,
                nonce=record.nonce,
                ciphertext=record.ciphertext,
            )
            payload = store.decrypt(context, old)
            rotated = store.encrypt(context, payload)
            record.key_id = rotated.key_id
            record.nonce = rotated.nonce
            record.ciphertext = rotated.ciphertext
        db.commit()
    except Exception:
        db.rollback()
        raise
    return len(records)


def verify_retained_credentials(
    db: Session,
    store: CredentialStore,
    *,
    installation_id: UUID,
) -> int:
    """Decrypt every retained row without returning or logging its payload."""
    records = (
        db.query(CredentialVersion)
        .filter(CredentialVersion.purged_at.is_(None))
        .order_by(CredentialVersion.id)
        .yield_per(100)
    )
    count = 0
    for record in records:
        store.decrypt(
            _context(record, installation_id),
            CredentialEnvelope(
                key_id=record.key_id,
                nonce=record.nonce,
                ciphertext=record.ciphertext,
            ),
        )
        count += 1
    return count


def retire_encryption_key(
    db: Session,
    path: str | Path,
    *,
    key_id: str,
    installation_id: UUID,
) -> None:
    """Remove an old encryption key only after all retained ciphertext verifies."""
    selected = Path(path)
    ring = load_encryption_keyring(selected)
    if key_id == ring.active_key_id:
        raise RuntimeError("cannot_retire_active_key")
    if key_id not in ring.key_ids:
        raise RuntimeError("key_not_found")
    if db.query(CredentialVersion.id).filter(CredentialVersion.key_id == key_id).first():
        raise RuntimeError("encryption_key_still_in_use")
    store = CredentialStore(ring, installation_id=installation_id)
    verify_retained_credentials(db, store, installation_id=installation_id)
    _remove_key(selected, ring, key_id)


def retire_hmac_key(db: Session, path: str | Path, *, key_id: str) -> None:
    """Retire an HMAC key only after every 24-hour request retry has expired."""
    selected = Path(path)
    ring = load_hmac_keyring(selected)
    if key_id == ring.active_key_id:
        raise RuntimeError("cannot_retire_active_key")
    if key_id not in ring.key_ids:
        raise RuntimeError("key_not_found")
    now = datetime.now(UTC)
    pending = (
        db.query(ConnectorRequestLedger.id)
        .filter(
            ConnectorRequestLedger.key_id == key_id,
            ConnectorRequestLedger.expires_at > now,
        )
        .first()
    )
    if pending is not None:
        raise RuntimeError("hmac_retry_window_active")
    _remove_key(selected, ring, key_id)


def _remove_key(path: Path, ring: EncryptionKeyRing | HmacKeyRing, key_id: str) -> None:
    remaining = {item: ring.key(item) for item in ring.key_ids if item != key_id}
    if not remaining:
        raise RuntimeError("keyring_cannot_be_empty")
    _atomic_replace(path, _encode_keyring(ring.active_key_id, remaining))
