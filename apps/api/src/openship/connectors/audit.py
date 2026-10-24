"""Append-only, secret-free Connector configuration audit records."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from .contracts import canonical_json
from .models import ConnectorAuditEvent


def record_audit(
    db: Session,
    *,
    visibility: str,
    project_id: uuid.UUID | None,
    actor_user_id: uuid.UUID,
    action: str,
    connector_id: uuid.UUID | None = None,
    connector_revision: int | None = None,
    details: dict[str, Any] | None = None,
) -> ConnectorAuditEvent:
    """Write only bounded identifiers and non-secret operation metadata."""
    encoded = canonical_json(details or {}).decode("utf-8")
    if len(encoded.encode("utf-8")) > 16 * 1024:
        encoded = "{}"
    event = ConnectorAuditEvent(
        id=uuid.uuid4(),
        visibility=visibility,
        project_id=project_id,
        connector_id=connector_id,
        connector_revision=connector_revision,
        actor_user_id=actor_user_id,
        action=action[:48],
        outcome="accepted",
        details_json=encoded,
    )
    db.add(event)
    return event
