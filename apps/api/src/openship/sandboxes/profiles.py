"""Bounded runtime view of the mounted sandbox profile snapshot.

The container target is fixed. Host paths exist only for native setup and
tests; a host ``SANDBOX_PROFILES_FILE`` setting is never read here or forwarded
into container settings.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .contracts import RuntimeRequirements
from .profile_schema import (
    MAX_YAML_BYTES,
    ProfileCatalog,
    ProfileId,
    catalog_digest,
    decode_catalog,
)

if TYPE_CHECKING:
    from ..runs.tasks.contracts import ExecutionConfigV5

DEFAULT_PROFILE_PATH = Path("/etc/openship/sandbox-profiles.yaml")
MANAGED_PROFILE_IDS: frozenset[ProfileId] = frozenset(
    ("logs-data", "iac-validation", "standard", "devops-shell")
)

_runtime_catalog: ProfileCatalog | None = None


class ProfileSnapshotError(ValueError):
    """A mounted snapshot is missing, unsafe, oversized or invalid."""


class TaskProfileError(ValueError):
    """A selected task profile is not permitted, enabled or current."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def empty_catalog() -> ProfileCatalog:
    return ProfileCatalog(schema_version=1, profiles={})


def _read_bounded(path: Path) -> bytes:
    try:
        pre_open = os.lstat(path)
    except OSError as exc:
        raise ProfileSnapshotError("sandbox profile snapshot is not readable") from exc
    if not stat.S_ISREG(pre_open.st_mode):
        raise ProfileSnapshotError(
            "sandbox profile snapshot must be a regular file"
        )
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ProfileSnapshotError("sandbox profile snapshot is not readable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ProfileSnapshotError(
                "sandbox profile snapshot must be a regular file"
            )
        if info.st_size > MAX_YAML_BYTES:
            raise ProfileSnapshotError("sandbox profile snapshot exceeds its size limit")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_YAML_BYTES:
                raise ProfileSnapshotError(
                    "sandbox profile snapshot exceeds its size limit"
                )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def load_runtime_catalog(
    path: Path = DEFAULT_PROFILE_PATH,
) -> ProfileCatalog:
    """Load one bounded snapshot from the fixed container mount."""
    try:
        return decode_catalog(_read_bounded(Path(path)))
    except ProfileSnapshotError:
        raise
    except ValueError as exc:
        raise ProfileSnapshotError("sandbox profile snapshot is invalid") from exc


def eligible_profiles(
    catalog: ProfileCatalog, permitted: frozenset[ProfileId]
) -> tuple[ProfileId, ...]:
    """Enabled, smoke-verified profiles this runtime may offer, in stable order."""
    return tuple(
        sorted(
            profile_id
            for profile_id, entry in catalog.profiles.items()
            if profile_id in permitted
            and entry.enabled
            and entry.runtime.smoke_passed
            and entry.runtime.platform == "linux/amd64"
        )
    )


def sandbox_provider_id() -> str:
    """Derive the secret-free provider identity from installation settings."""
    from src.openship.config import settings

    try:
        endpoint = httpx.URL(settings.sandbox_endpoint)
        if (
            endpoint.scheme not in ("http", "https")
            or not endpoint.host
            or endpoint.userinfo
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("invalid endpoint identity")
    except ValueError:
        raise TaskProfileError(
            "not_ready", "sandbox provider identity is not configured"
        ) from None
    deployment = settings.sandbox_deployment_id.strip()
    if not deployment:
        raise TaskProfileError(
            "not_ready", "sandbox deployment identity is not configured"
        )
    endpoint_identity = (
        f"{endpoint.scheme}://{endpoint.host}:{endpoint.port}"
        f"{endpoint.path.rstrip('/')}"
    )
    return "opensandbox:" + hashlib.sha256(
        f"{deployment}\n{endpoint_identity}".encode()
    ).hexdigest()


def resolve_task_requirements(
    execution: ExecutionConfigV5,
    profile_id: ProfileId,
    current: ProfileCatalog,
) -> RuntimeRequirements:
    """Resolve one exact pinned profile against the mounted current snapshot.

    The pinned execution snapshot is authoritative for the binding, but the
    current mount must still contain the same exact binding, enabled. A change,
    disablement or unknown selection is a blocker; there is no fallback to a
    different profile, tag, or legacy installation setting.
    """
    if profile_id not in MANAGED_PROFILE_IDS:
        raise TaskProfileError(
            "unsupported_profile", "the selected sandbox profile is not supported"
        )
    pinned_entry = execution.profile_catalog.profiles.get(profile_id)
    if pinned_entry is None:
        raise TaskProfileError(
            "unsupported_profile", "the selected sandbox profile is not permitted"
        )
    if not pinned_entry.enabled:
        raise TaskProfileError(
            "disabled", "the selected sandbox profile is disabled"
        )
    current_entry = current.profiles.get(profile_id)
    if current_entry is None:
        raise TaskProfileError(
            "changed", "the selected sandbox profile is no longer mounted"
        )
    if not current_entry.enabled:
        raise TaskProfileError(
            "disabled", "the selected sandbox profile is disabled"
        )
    if pinned_entry.runtime != current_entry.runtime:
        raise TaskProfileError(
            "changed", "the selected sandbox image binding has changed"
        )
    if (
        current_entry.runtime.platform != "linux/amd64"
        or not current_entry.runtime.smoke_passed
    ):
        raise TaskProfileError(
            "changed", "the selected sandbox image is not qualified"
        )
    return RuntimeRequirements(
        provider_id=sandbox_provider_id(),
        profile_id=profile_id,
        image=pinned_entry.runtime.image_id,
        dependency_identity=pinned_entry.runtime.dependency_identity,
    )


def profile_readiness(
    catalog: ProfileCatalog,
    permitted: frozenset[ProfileId],
    *,
    selected: ProfileId | None = None,
) -> dict[str, object]:
    """Secret-free readiness projection: digest and eligible IDs only.

    When ``selected`` is supplied, overall readiness also requires that exact
    profile to be eligible; another eligible managed profile is not enough.
    """
    eligible = eligible_profiles(catalog, permitted)
    ready = bool(eligible) if selected is None else selected in eligible
    return {
        "digest": catalog_digest(catalog),
        "eligible": list(eligible),
        "ready": ready,
    }


def startup_catalog(
    *, settings_obj=None, path: Path = DEFAULT_PROFILE_PATH
) -> ProfileCatalog:
    """Load only when the enabled mapping path needs the snapshot.

    A disabled deployment, or one still using the legacy single-profile path,
    exposes an empty catalog and never requires the mounted file.
    """
    global _runtime_catalog
    if settings_obj is None:
        from src.openship.config import settings as settings_obj
    if not getattr(settings_obj, "sandbox_enabled", False) or getattr(
        settings_obj, "sandbox_profile_id", ""
    ) not in MANAGED_PROFILE_IDS:
        _runtime_catalog = empty_catalog()
        return _runtime_catalog
    _runtime_catalog = load_runtime_catalog(path)
    return _runtime_catalog


def install_runtime_catalog(catalog: ProfileCatalog) -> None:
    global _runtime_catalog
    _runtime_catalog = catalog


def reset_runtime_catalog() -> None:
    global _runtime_catalog
    _runtime_catalog = None


def runtime_catalog() -> ProfileCatalog:
    return _runtime_catalog if _runtime_catalog is not None else empty_catalog()
