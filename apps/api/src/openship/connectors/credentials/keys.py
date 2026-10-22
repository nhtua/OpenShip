"""Protected, versioned key-file loading for credential encryption and HMACs."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ..errors import CredentialStoreUnavailable

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_KEY_FILE_BYTES = 16 * 1024
_MIN_HMAC_KEY_BYTES = 32


def _pairs_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key file property")
        result[key] = value
    return result


def _read_protected_json(path: str | Path) -> dict[str, object]:
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(os.fspath(path), flags)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size > _MAX_KEY_FILE_BYTES
        ):
            raise CredentialStoreUnavailable()
        chunks = bytearray()
        while len(chunks) <= _MAX_KEY_FILE_BYTES:
            chunk = os.read(descriptor, min(4096, _MAX_KEY_FILE_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > _MAX_KEY_FILE_BYTES:
            raise CredentialStoreUnavailable()
        value = json.loads(
            bytes(chunks).decode("utf-8"), object_pairs_hook=_pairs_no_duplicates
        )
        if not isinstance(value, dict) or set(value) != {"active_key_id", "keys"}:
            raise CredentialStoreUnavailable()
        return value
    except CredentialStoreUnavailable:
        raise
    except (
        OSError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ):
        raise CredentialStoreUnavailable() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _decode_keyring(path: str | Path, *, exact_length: bool) -> tuple[str, Mapping[str, bytes]]:
    document = _read_protected_json(path)
    active_key_id = document.get("active_key_id")
    encoded_keys = document.get("keys")
    if (
        not isinstance(active_key_id, str)
        or _KEY_ID.fullmatch(active_key_id) is None
        or not isinstance(encoded_keys, dict)
        or not encoded_keys
        or len(encoded_keys) > 16
        or active_key_id not in encoded_keys
    ):
        raise CredentialStoreUnavailable()

    keys: dict[str, bytes] = {}
    for key_id, encoded in encoded_keys.items():
        if not isinstance(key_id, str) or _KEY_ID.fullmatch(key_id) is None:
            raise CredentialStoreUnavailable()
        if not isinstance(encoded, str) or not encoded or len(encoded) > 128:
            raise CredentialStoreUnavailable()
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise CredentialStoreUnavailable() from None
        if base64.b64encode(decoded).decode("ascii") != encoded:
            raise CredentialStoreUnavailable()
        if (exact_length and len(decoded) != 32) or (
            not exact_length and len(decoded) < _MIN_HMAC_KEY_BYTES
        ):
            raise CredentialStoreUnavailable()
        keys[key_id] = decoded
    return active_key_id, MappingProxyType(keys)


@dataclass(frozen=True, slots=True, repr=False)
class EncryptionKeyRing:
    active_key_id: str
    _keys: Mapping[str, bytes] = field(repr=False)

    def key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError:
            raise CredentialStoreUnavailable() from None

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._keys))

    def __repr__(self) -> str:
        return f"EncryptionKeyRing(active_key_id={self.active_key_id!r}, key_ids={self.key_ids!r})"


@dataclass(frozen=True, slots=True, repr=False)
class HmacKeyRing:
    active_key_id: str
    _keys: Mapping[str, bytes] = field(repr=False)

    def key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError:
            raise CredentialStoreUnavailable() from None

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._keys))

    def digest(self, value: bytes, *, key_id: str | None = None) -> tuple[str, str]:
        selected = key_id or self.active_key_id
        digest = hmac.new(self.key(selected), value, "sha256").hexdigest()
        return selected, digest

    def verify(self, key_id: str, value: bytes, expected_digest: str) -> bool:
        if not isinstance(expected_digest, str) or re.fullmatch(
            r"[0-9a-f]{64}", expected_digest
        ) is None:
            return False
        try:
            _, observed = self.digest(value, key_id=key_id)
        except CredentialStoreUnavailable:
            return False
        return hmac.compare_digest(observed, expected_digest)

    def __repr__(self) -> str:
        return f"HmacKeyRing(active_key_id={self.active_key_id!r}, key_ids={self.key_ids!r})"


def load_encryption_keyring(path: str | Path) -> EncryptionKeyRing:
    """Load AES-256 keys; failures never include paths or key material."""
    active_key_id, keys = _decode_keyring(path, exact_length=True)
    return EncryptionKeyRing(active_key_id, keys)


def load_hmac_keyring(path: str | Path) -> HmacKeyRing:
    """Load versioned 256-bit-or-longer idempotency HMAC keys."""
    active_key_id, keys = _decode_keyring(path, exact_length=False)
    return HmacKeyRing(active_key_id, keys)
