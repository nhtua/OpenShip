"""Shared, revisioned installation policy for distribution-owned tools."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_installed_tool_definitions
from src.openship.capabilities.models import (
    ToolPolicyAuditEvent,
    ToolPolicyOverride,
    ToolPolicyState,
)
from src.openship.config import settings
from src.openship.connectors.service import (
    ConnectorCommandError,
    require_global_operator,
)

# AWS catalog discovery is opt-in at the operator policy layer. The deployment
# ceiling still acts only as a ceiling and cannot enable either capability.
_DEFAULT_DISABLED_IDS = frozenset({"aws_read_catalog", "aws_read"})


class ToolPolicyError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class EffectiveToolPolicy:
    revision: int
    installed_ids: frozenset[str]
    operator_enabled_ids: frozenset[str]
    effective_ids: frozenset[str]
    deployment_restricted_ids: frozenset[str]
    unknown_deployment_ids: tuple[str, ...]
    state_available: bool
    legacy_cutover_at: datetime | None

    @property
    def deployment_ceiling_active(self) -> bool:
        return settings.tool_enabled_ceiling is not None

    @property
    def cutover_available(self) -> bool:
        return (
            self.state_available
            and self.deployment_ceiling_active
            and self.legacy_cutover_at is None
        )


def read_effective_tool_policy(
    db: Session, *, lock: bool = False
) -> EffectiveToolPolicy:
    definitions = list_installed_tool_definitions()
    installed_ids = frozenset(item.id for item in definitions)
    state_query = db.query(ToolPolicyState).filter(ToolPolicyState.id == 1)
    if lock:
        state_query = state_query.with_for_update()
    state = state_query.first()
    if state is None:
        return EffectiveToolPolicy(
            revision=0,
            installed_ids=installed_ids,
            operator_enabled_ids=frozenset(),
            effective_ids=frozenset(),
            deployment_restricted_ids=installed_ids,
            unknown_deployment_ids=(),
            state_available=False,
            legacy_cutover_at=None,
        )

    overrides = {
        item.capability_id: item.enabled
        for item in db.query(ToolPolicyOverride).all()
        if item.capability_id in installed_ids
    }
    operator_enabled_ids = frozenset(
        capability_id
        for capability_id in installed_ids
        if overrides.get(capability_id, capability_id not in _DEFAULT_DISABLED_IDS)
    )
    ceiling = settings.tool_enabled_ceiling
    unknown = tuple(sorted(ceiling - installed_ids)) if ceiling is not None else ()
    restricted = installed_ids - ceiling if ceiling is not None else frozenset()
    effective_ids = (
        operator_enabled_ids & ceiling if ceiling is not None else operator_enabled_ids
    )
    return EffectiveToolPolicy(
        revision=state.revision,
        installed_ids=installed_ids,
        operator_enabled_ids=operator_enabled_ids,
        effective_ids=frozenset(effective_ids),
        deployment_restricted_ids=frozenset(restricted),
        unknown_deployment_ids=unknown,
        state_available=True,
        legacy_cutover_at=state.legacy_cutover_at,
    )


def policy_changed_for_capabilities_since(
    db: Session,
    capability_ids: frozenset[str],
    revision: int | None,
    *,
    current_revision: int | None = None,
) -> bool:
    """Whether a pinned run's tools were explicitly changed after it queued."""
    if revision is None or not capability_ids:
        return False
    if current_revision is not None and current_revision < revision:
        return True
    changed = (
        db.query(ToolPolicyAuditEvent.id)
        .filter(
            ToolPolicyAuditEvent.revision > revision,
            or_(
                ToolPolicyAuditEvent.capability_id.is_(None),
                ToolPolicyAuditEvent.capability_id.in_(capability_ids),
            ),
        )
        .first()
    )
    return changed is not None


def require_current_tool_policy(
    db: Session,
    capability_ids: frozenset[str],
    *,
    pinned_revision: int | None = None,
    lock: bool = False,
) -> EffectiveToolPolicy:
    snapshot = read_effective_tool_policy(db, lock=lock)
    if not snapshot.state_available:
        raise ToolPolicyError("tool_policy_unavailable")
    if (
        not capability_ids.issubset(snapshot.effective_ids)
        or policy_changed_for_capabilities_since(
            db,
            capability_ids,
            pinned_revision,
            current_revision=snapshot.revision,
        )
    ):
        raise ToolPolicyError("disabled")
    return snapshot


def _require_operator(actor: User) -> None:
    try:
        require_global_operator(actor)
    except ConnectorCommandError as error:
        raise ToolPolicyError("global_management_forbidden") from error


def _state(db: Session) -> ToolPolicyState:
    state = (
        db.query(ToolPolicyState)
        .filter(ToolPolicyState.id == 1)
        .with_for_update()
        .first()
    )
    if state is None:
        raise ToolPolicyError("tool_policy_unavailable")
    return state


def _advance_revision(
    db: Session,
    *,
    state: ToolPolicyState,
    actor: User,
    expected_revision: int,
) -> int:
    if state.revision != expected_revision:
        raise ToolPolicyError("tool_policy_revision_changed")
    next_revision = state.revision + 1
    result = db.execute(
        update(ToolPolicyState)
        .where(
            ToolPolicyState.id == 1,
            ToolPolicyState.revision == expected_revision,
        )
        .values(
            revision=next_revision,
            updated_by_user_id=actor.id,
            updated_at=datetime.now(UTC),
        )
    )
    if result.rowcount != 1:
        raise ToolPolicyError("tool_policy_revision_changed")
    return next_revision


def _upsert_override(
    db: Session,
    *,
    capability_id: str,
    enabled: bool,
    actor: User,
) -> None:
    values = {
        "capability_id": capability_id,
        "enabled": enabled,
        "updated_by_user_id": actor.id,
        "updated_at": datetime.now(UTC),
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = pg_insert(ToolPolicyOverride).values(**values)
        db.execute(
            statement.on_conflict_do_update(
                index_elements=["capability_id"],
                set_={
                    "enabled": statement.excluded.enabled,
                    "updated_by_user_id": statement.excluded.updated_by_user_id,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(ToolPolicyOverride).values(**values)
        db.execute(
            statement.on_conflict_do_update(
                index_elements=["capability_id"],
                set_={
                    "enabled": statement.excluded.enabled,
                    "updated_by_user_id": statement.excluded.updated_by_user_id,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
    else:
        existing = (
            db.query(ToolPolicyOverride)
            .filter(ToolPolicyOverride.capability_id == capability_id)
            .with_for_update()
            .first()
        )
        if existing is None:
            db.add(ToolPolicyOverride(**values))
        else:
            existing.enabled = enabled
            existing.updated_by_user_id = actor.id
            existing.updated_at = values["updated_at"]


def set_tool_policy(
    db: Session,
    actor: User,
    capability_id: str,
    enabled: bool,
    expected_revision: int,
) -> EffectiveToolPolicy:
    _require_operator(actor)
    if capability_id not in {item.id for item in list_installed_tool_definitions()}:
        raise ToolPolicyError("tool_not_installed")
    state = _state(db)
    if state.revision != expected_revision:
        raise ToolPolicyError("tool_policy_revision_changed")
    existing = (
        db.query(ToolPolicyOverride)
        .filter(ToolPolicyOverride.capability_id == capability_id)
        .first()
    )
    current_enabled = (
        existing.enabled
        if existing is not None
        else capability_id not in _DEFAULT_DISABLED_IDS
    )
    if current_enabled == enabled:
        return read_effective_tool_policy(db)

    revision = _advance_revision(
        db, state=state, actor=actor, expected_revision=expected_revision
    )
    _upsert_override(
        db, capability_id=capability_id, enabled=enabled, actor=actor
    )
    db.add(
        ToolPolicyAuditEvent(
            id=uuid.uuid4(),
            revision=revision,
            actor_user_id=actor.id,
            action="enabled" if enabled else "disabled",
            capability_id=capability_id,
            enabled=enabled,
        )
    )
    db.flush()
    return read_effective_tool_policy(db)


def cutover_legacy_tool_allowlist(
    db: Session, actor: User, expected_revision: int
) -> EffectiveToolPolicy:
    _require_operator(actor)
    state = _state(db)
    if state.revision != expected_revision:
        raise ToolPolicyError("tool_policy_revision_changed")
    if state.legacy_cutover_at is not None:
        raise ToolPolicyError("legacy_cutover_complete")
    if settings.tool_enabled_ceiling is None:
        raise ToolPolicyError("legacy_cutover_not_required")

    snapshot = read_effective_tool_policy(db)
    revision = _advance_revision(
        db, state=state, actor=actor, expected_revision=expected_revision
    )
    for capability_id in snapshot.installed_ids:
        _upsert_override(
            db,
            capability_id=capability_id,
            enabled=capability_id in snapshot.effective_ids,
            actor=actor,
        )
    state.legacy_cutover_at = datetime.now(UTC)
    state.legacy_cutover_by_user_id = actor.id
    db.add(
        ToolPolicyAuditEvent(
            id=uuid.uuid4(),
            revision=revision,
            actor_user_id=actor.id,
            action="legacy_cutover",
            capability_id=None,
            enabled=None,
        )
    )
    db.flush()
    return read_effective_tool_policy(db)
