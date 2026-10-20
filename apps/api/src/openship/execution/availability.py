"""Safe runtime-owned observations of maintained tool readiness."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.openship.artifacts.filesystem import ArtifactStorageError
from src.openship.artifacts.maintenance import configured_store
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.execution.models import ToolAvailability
from src.openship.providers.credentials import credential_binding_present

AvailabilityReason = Literal[
    "missing_credentials", "disabled", "runtime_unavailable", "configuration_error"
]


@dataclass(frozen=True, slots=True)
class ToolAvailabilityObservation:
    """One secret-free observation written by an execution runtime."""

    capability_id: str
    manifest_digest: str
    available: bool
    reason: AvailabilityReason | None
    observed_at: datetime


def observe_tool_availability(
    *,
    enabled_ids: frozenset[str],
    credential_check=credential_binding_present,
    observed_at: datetime | None = None,
) -> tuple[ToolAvailabilityObservation, ...]:
    """Check each maintained binding independently without returning values."""
    now = _utc(observed_at or datetime.now(UTC))
    observations = []
    for manifest in list_capability_manifests():
        if manifest.id not in enabled_ids:
            available, reason = False, "disabled"
        elif manifest.credential_binding_ref is None:
            if manifest.effect == "read_only_local":
                try:
                    configured_store().verify()
                    available, reason = True, None
                except ArtifactStorageError:
                    available, reason = False, "runtime_unavailable"
            else:
                available, reason = True, None
        else:
            try:
                available = bool(credential_check(manifest.credential_binding_ref))
                reason = None if available else "missing_credentials"
            except Exception:  # noqa: BLE001 - never expose resolver details in readiness.
                available, reason = False, "configuration_error"
        observations.append(
            ToolAvailabilityObservation(
                capability_id=manifest.id,
                manifest_digest=manifest.manifest_digest,
                available=available,
                reason=reason,
                observed_at=now,
            )
        )
    return tuple(observations)


def publish_tool_availability(
    db: Session,
    worker_id: str,
    observations: tuple[ToolAvailabilityObservation, ...],
) -> None:
    """Upsert safe readiness rows in the caller's transaction."""
    if not worker_id or len(worker_id) > 128:
        raise ValueError("availability worker identity is invalid")
    current = {item.id: item for item in list_capability_manifests()}
    rows = []
    for observation in observations:
        manifest = current.get(observation.capability_id)
        if manifest is None or manifest.manifest_digest != observation.manifest_digest:
            continue
        if observation.available and observation.reason is not None:
            raise ValueError("available observation cannot have an unavailable reason")
        rows.append(
            {
                "id": uuid.uuid4(),
                "capability_id": observation.capability_id,
                "manifest_digest": observation.manifest_digest,
                "worker_id": worker_id,
                "available": observation.available,
                "reason": observation.reason,
                "observed_at": _utc(observation.observed_at),
            }
        )
    if not rows:
        return

    bind = db.get_bind()
    dialect = bind.dialect.name
    values = {
        "manifest_digest": ToolAvailability.manifest_digest,
        "available": ToolAvailability.available,
        "reason": ToolAvailability.reason,
        "observed_at": ToolAvailability.observed_at,
    }
    if dialect == "postgresql":
        insert = pg_insert(ToolAvailability).values(rows)
        db.execute(
            insert.on_conflict_do_update(
                index_elements=["capability_id", "worker_id"],
                set_={key: getattr(insert.excluded, key) for key in values},
            )
        )
    elif dialect == "sqlite":
        insert = sqlite_insert(ToolAvailability).values(rows)
        db.execute(
            insert.on_conflict_do_update(
                index_elements=["capability_id", "worker_id"],
                set_={key: getattr(insert.excluded, key) for key in values},
            )
        )
    else:
        for row in rows:
            existing = (
                db.query(ToolAvailability)
                .filter(
                    ToolAvailability.capability_id == row["capability_id"],
                    ToolAvailability.worker_id == worker_id,
                )
                .with_for_update()
                .first()
            )
            if existing is None:
                db.add(ToolAvailability(**row))
            else:
                for key, value in row.items():
                    if key != "id":
                        setattr(existing, key, value)
    db.flush()


def read_tool_availability(
    db: Session,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 30,
) -> dict[str, tuple[bool, AvailabilityReason | None]]:
    """Project fresh runtime observations; absent/stale rows remain unknown."""
    if stale_after_seconds < 1:
        raise ValueError("availability freshness window must be positive")
    current_time = _utc(now or datetime.now(UTC))
    cutoff = current_time - timedelta(seconds=stale_after_seconds)
    manifests = {item.id: item for item in list_capability_manifests()}
    records: dict[str, list[ToolAvailability]] = {key: [] for key in manifests}
    for row in db.query(ToolAvailability).all():
        manifest = manifests.get(row.capability_id)
        observed_at = _utc(row.observed_at)
        if (
            manifest is not None
            and row.manifest_digest == manifest.manifest_digest
            and cutoff <= observed_at <= current_time
        ):
            records[row.capability_id].append(row)

    projection = {}
    for capability_id, rows in records.items():
        fresh_available = [row for row in rows if row.available]
        if fresh_available:
            projection[capability_id] = (True, None)
            continue
        if rows:
            latest = max(rows, key=lambda item: _utc(item.observed_at))
            reason = latest.reason if latest.reason in (
                "missing_credentials", "disabled", "runtime_unavailable"
            ) else "runtime_unavailable"
            projection[capability_id] = (False, reason)
        else:
            projection[capability_id] = (False, "runtime_unavailable")
    return projection


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
