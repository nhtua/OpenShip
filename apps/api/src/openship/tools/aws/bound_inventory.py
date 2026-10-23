"""Local inventory of owned S3 bucket bindings."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy.orm import Session

from src.openship.capabilities.contracts import canonical_json
from src.openship.chat.models import Conversation
from src.openship.providers.control import InvocationControl
from src.openship.resources.contracts import ResourceBindingSummary
from src.openship.resources.service import (
    ResourceServiceError,
    list_resource_bindings,
)
from src.openship.workspace.lifecycle import ArchiveStateError, assert_active_project
from src.openship.workspace.projects import (
    ProjectLifecycleError,
    get_owned_project,
)

MAX_MODEL_RESULT_BYTES = 16 * 1024
_CURSOR_SCOPE = "active_s3_bucket_bindings"


class BoundInventoryError(RuntimeError):
    """Closed error vocabulary for local resource inventory."""

    _CODES = frozenset({"invalid_page_size", "invalid_cursor", "inventory_unavailable"})

    def __init__(self, code: str):
        if code not in self._CODES:
            raise ValueError("unknown bound inventory error code")
        self.code = code
        super().__init__(code)


class BoundInventoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    limit: StrictInt = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, max_length=512)


class BoundInventoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: Literal["stored_bindings"] = "stored_bindings"
    items: tuple[ResourceBindingSummary, ...]
    next_cursor: str | None = None


def _encode_cursor(cursor: str, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> str:
    payload = {
        "version": 1,
        "scope": _CURSOR_SCOPE,
        "owner_id": str(owner_id),
        "project_id": str(project_id),
        "cursor": cursor,
    }
    return base64.urlsafe_b64encode(canonical_json(payload)).decode("ascii").rstrip("=")


def _decode_cursor(value: str, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> str:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "scope", "owner_id", "project_id", "cursor"}
            or payload["version"] != 1
            or payload["scope"] != _CURSOR_SCOPE
            or payload["owner_id"] != str(owner_id)
            or payload["project_id"] != str(project_id)
            or not isinstance(payload["cursor"], str)
            or not payload["cursor"]
            or len(payload["cursor"]) > 256
        ):
            raise ValueError("cursor scope does not match")
        return payload["cursor"]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundInventoryError("invalid_cursor") from exc


def _authorize_inventory(db: Session, context) -> tuple[uuid.UUID, uuid.UUID]:
    authorization = context.authorization
    if (
        authorization.action != "execute_tool"
        or authorization.project_id is None
        or authorization.conversation_id is None
    ):
        raise BoundInventoryError("inventory_unavailable")
    try:
        project = get_owned_project(
            db,
            owner_id=authorization.principal_id,
            project_id=authorization.project_id,
        )
        assert_active_project(project)
    except (ArchiveStateError, ProjectLifecycleError):
        raise BoundInventoryError("inventory_unavailable") from None
    conversation = (
        db.query(Conversation.id)
        .filter(
            Conversation.id == authorization.conversation_id,
            Conversation.project_id == project.id,
            Conversation.user_id == authorization.principal_id,
            Conversation.archived_at.is_(None),
        )
        .first()
    )
    if conversation is None:
        raise BoundInventoryError("inventory_unavailable")
    return authorization.principal_id, project.id


async def list_bound_buckets(
    arguments: BoundInventoryArguments | dict,
    context,
    control: InvocationControl,
) -> BoundInventoryResult:
    """Return active, locally stored bucket bindings without checking live AWS state."""
    try:
        normalized = (
            arguments
            if isinstance(arguments, BoundInventoryArguments)
            else BoundInventoryArguments.model_validate(arguments)
        )
    except ValidationError as exc:
        invalid_fields = {error["loc"][0] for error in exc.errors() if error["loc"]}
        code = "invalid_cursor" if "cursor" in invalid_fields else "invalid_page_size"
        raise BoundInventoryError(code) from exc

    if context.session_factory is None:
        raise BoundInventoryError("inventory_unavailable")
    control.raise_if_stopped()
    with context.session_factory() as db:
        owner_id, project_id = _authorize_inventory(db, context)
        service_cursor = (
            _decode_cursor(
                normalized.cursor,
                owner_id=owner_id,
                project_id=project_id,
            )
            if normalized.cursor is not None
            else None
        )
        page_size = normalized.limit
        while page_size >= 1:
            try:
                items, next_service_cursor = list_resource_bindings(
                    db,
                    owner_id=owner_id,
                    project_id=project_id,
                    limit=page_size,
                    cursor=service_cursor,
                )
            except ResourceServiceError as exc:
                code = (
                    exc.code
                    if exc.code in {"invalid_page_size", "invalid_cursor"}
                    else "inventory_unavailable"
                )
                raise BoundInventoryError(code) from None

            result = BoundInventoryResult(
                items=tuple(items),
                next_cursor=(
                    _encode_cursor(
                        next_service_cursor,
                        owner_id=owner_id,
                        project_id=project_id,
                    )
                    if next_service_cursor is not None
                    else None
                ),
            )
            if (
                len(canonical_json(result.model_dump(mode="json")))
                <= MAX_MODEL_RESULT_BYTES
            ):
                control.raise_if_stopped()
                return result
            if page_size == 1:
                raise BoundInventoryError("inventory_unavailable")
            page_size = max(1, page_size // 2)

    raise BoundInventoryError("inventory_unavailable")
