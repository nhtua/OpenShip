"""Explicit, previewable migration from legacy search environment bindings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from ..auth.models import User
from .service import (
    ConnectorCommandError,
    create_connector,
    require_global_operator,
    set_default,
)

_SERVICES = ("brave", "tavily")


def _validate_migration(
    *,
    services: Sequence[str],
    visibility: str,
    project_id: UUID | None,
    default_services: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    selected = tuple(services)
    defaults = tuple(default_services)
    if visibility == "project":
        raise ConnectorCommandError("connector_visibility_unsupported")
    if (
        visibility != "global"
        or not selected
        or len(set(selected)) != len(selected)
        or any(service not in _SERVICES for service in selected)
        or len(set(defaults)) != len(defaults)
        or any(service not in selected for service in defaults)
        or (visibility == "global" and project_id is not None)
        or defaults
    ):
        raise ValueError("invalid_search_migration_selection")
    return selected, defaults


def preview_legacy_search_import(
    *,
    services: Sequence[str],
    visibility: str,
    project_id: UUID | None,
    default_services: Sequence[str] = (),
    available_services: Sequence[str] = (),
) -> list[dict[str, object]]:
    """Describe only explicitly selected effects and credential presence."""
    selected, defaults = _validate_migration(
        services=services,
        visibility=visibility,
        project_id=project_id,
        default_services=default_services,
    )
    available = set(available_services)
    return [
        {
            "service": service,
            "visibility": visibility,
            "project_id": str(project_id) if project_id is not None else None,
            "will_set_default": service in defaults,
            "credential_available": service in available,
        }
        for service in selected
    ]


def import_legacy_search_connectors(
    db: Session,
    *,
    actor: User,
    visibility: str,
    project_id: UUID | None,
    services: Sequence[str],
    credentials: Mapping[str, str | None],
    default_services: Sequence[str] = (),
) -> list[dict[str, object]]:
    """Import named legacy keys into encrypted Connectors under explicit scope."""
    selected, defaults = _validate_migration(
        services=services,
        visibility=visibility,
        project_id=project_id,
        default_services=default_services,
    )
    require_global_operator(actor)

    imported: list[dict[str, object]] = []
    for service in selected:
        secret = credentials.get(service)
        if not isinstance(secret, str) or not secret:
            raise ConnectorCommandError("legacy_credential_missing")
        result = create_connector(
            db,
            actor=actor,
            visibility=visibility,
            project_id=project_id,
            kind=service,
            name=f"Migrated {service.title()} search",
            configuration_raw={"schema_version": 1, "kind": service},
            api_key=secret,
        )
        if service in defaults:
            assert project_id is not None
            set_default(
                db,
                project_id=project_id,
                actor=actor,
                service=service,
                expected_revision=0,
                connector_id=UUID(result["id"]),
            )
        imported.append(result)
    return imported
