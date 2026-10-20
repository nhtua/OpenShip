"""Bounded, read-only tools over verified snapshots in one conversation."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import func, or_

from src.openship.artifacts.filesystem import ArtifactStorageError
from src.openship.artifacts.models import Artifact
from src.openship.artifacts.service import mark_artifact_unavailable
from src.openship.auth.models import User
from src.openship.capabilities.contracts import canonical_json
from src.openship.chat.models import Conversation

from .schemas import (
    WorkspaceFileSummary,
    WorkspaceListArguments,
    WorkspaceListResult,
    WorkspaceReadArguments,
    WorkspaceReadResult,
)
from .service import ensure_workspace


class WorkspaceToolError(Exception):
    """Closed, safe error vocabulary for local workspace tools."""

    _MESSAGES: ClassVar[dict[str, str]] = {
        "workspace_unavailable": "The conversation workspace is unavailable.",
        "artifact_not_found": "The requested file is not available in this conversation.",
        "invalid_cursor": "The workspace file cursor is invalid.",
        "invalid_offset": "The requested byte offset is invalid.",
        "invalid_utf8_boundary": "The requested byte offset is not on a UTF-8 boundary.",
        "artifact_unavailable": "The saved file could not be verified in storage.",
        "cancelled": "The workspace read was cancelled.",
        "execution_interrupted": "The workspace operation was interrupted.",
    }

    def __init__(self, code: str):
        if code not in self._MESSAGES:
            raise ValueError("unknown workspace tool error code")
        self.code = code
        self.safe_message = self._MESSAGES[code]
        super().__init__(self.safe_message)


def _cursor_encode(created_at: datetime, artifact_id: uuid.UUID) -> str:
    value = f"{created_at.isoformat()}|{artifact_id}".encode()
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _cursor_decode(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        timestamp, artifact_id = raw.decode("utf-8").rsplit("|", 1)
        parsed_time = datetime.fromisoformat(timestamp)
        parsed_id = uuid.UUID(artifact_id)
        return parsed_time, parsed_id
    except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise WorkspaceToolError("invalid_cursor") from exc


def _runtime(db_factory, context, store):
    if (
        db_factory is None
        or store is None
        or context.authorization.action != "execute_tool"
    ):
        raise WorkspaceToolError("workspace_unavailable")
    return db_factory, store


def _authorize_conversation(db, context):
    authorization = context.authorization
    conversation = (
        db.query(Conversation)
        .join(User, User.id == Conversation.user_id)
        .filter(
            Conversation.id == authorization.conversation_id,
            Conversation.project_id == authorization.project_id,
            Conversation.user_id == authorization.principal_id,
        )
        .first()
    )
    if conversation is None:
        raise WorkspaceToolError("workspace_unavailable")
    return conversation


async def list_workspace_files(
    arguments: WorkspaceListArguments | dict,
    context,
    control,
) -> WorkspaceListResult:
    """List ready, immutable snapshots in the caller's pinned conversation."""
    try:
        normalized = (
            arguments
            if isinstance(arguments, WorkspaceListArguments)
            else WorkspaceListArguments.model_validate(arguments)
        )
    except ValueError as exc:
        raise WorkspaceToolError("invalid_cursor") from exc
    db_factory, store = _runtime(context.session_factory, context, context.artifact_store)
    control.raise_if_stopped()
    with db_factory() as db:
        conversation = _authorize_conversation(db, context)
        summary = ensure_workspace(
            db,
            context.authorization.principal_id,
            conversation.id,
            store,
        )
        db.commit()
        if summary.status != "available":
            raise WorkspaceToolError("workspace_unavailable")

        query = db.query(Artifact).filter(
            Artifact.conversation_id == conversation.id,
            Artifact.project_id == conversation.project_id,
            Artifact.status == "ready",
            Artifact.mutability == "immutable",
        )
        if normalized.cursor:
            created_at, artifact_id = _cursor_decode(normalized.cursor)
            if db.get_bind().dialect.name == "sqlite":
                artifact_time = func.strftime("%Y-%m-%d %H:%M:%f", Artifact.created_at)
                cursor_time = func.strftime("%Y-%m-%d %H:%M:%f", created_at)
            else:
                artifact_time = Artifact.created_at
                cursor_time = created_at
            query = query.filter(
                or_(
                    artifact_time < cursor_time,
                    (artifact_time == cursor_time) & (Artifact.id < artifact_id),
                )
            )
        rows = query.order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(
            normalized.limit + 1
        ).all()
        page = rows[: normalized.limit]
        if not page:
            control.raise_if_stopped()
            return WorkspaceListResult(
                workspace_id=summary.id,
                items=(),
                next_cursor=None,
            )
        for count in range(len(page), 0, -1):
            selected = page[:count]
            has_more = len(rows) > count
            items = tuple(
                WorkspaceFileSummary(
                    id=item.id,
                    version_id=item.id,
                    name=item.display_name,
                    media_type=item.media_type,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                    source=item.source,
                    created_at=item.created_at,
                    originating_run_id=item.originating_run_id,
                    originating_operation_id=item.originating_operation_id,
                )
                for item in selected
            )
            next_cursor = (
                _cursor_encode(selected[-1].created_at, selected[-1].id)
                if has_more
                else None
            )
            result = WorkspaceListResult(
                workspace_id=summary.id,
                items=items,
                next_cursor=next_cursor,
            )
            if len(canonical_json(result.model_dump(mode="json"))) <= 16 * 1024:
                control.raise_if_stopped()
                return result
        raise WorkspaceToolError("workspace_unavailable")


async def read_workspace_file(
    arguments: WorkspaceReadArguments | dict,
    context,
    control,
) -> WorkspaceReadResult:
    """Read a small exact byte range from one verified immutable snapshot."""
    try:
        normalized = (
            arguments
            if isinstance(arguments, WorkspaceReadArguments)
            else WorkspaceReadArguments.model_validate(arguments)
        )
    except ValueError as exc:
        raise WorkspaceToolError("artifact_not_found") from exc
    db_factory, store = _runtime(context.session_factory, context, context.artifact_store)
    control.raise_if_stopped()
    with db_factory() as db:
        conversation = _authorize_conversation(db, context)
        workspace = ensure_workspace(
            db,
            context.authorization.principal_id,
            conversation.id,
            store,
        )
        db.commit()
        if workspace.status != "available":
            raise WorkspaceToolError("workspace_unavailable")
        artifact = (
            db.query(Artifact)
            .filter(
                Artifact.id == normalized.artifact_id,
                Artifact.conversation_id == conversation.id,
                Artifact.project_id == conversation.project_id,
                Artifact.status == "ready",
                Artifact.mutability == "immutable",
            )
            .first()
        )
        if artifact is None:
            raise WorkspaceToolError("artifact_not_found")
        if artifact.media_type not in ("text/plain", "application/json"):
            raise WorkspaceToolError("artifact_unavailable")
        if normalized.offset > artifact.size_bytes:
            raise WorkspaceToolError("invalid_offset")

        try:
            control.raise_if_stopped()
            stream = store.open_verified(artifact)
            try:
                stream.seek(normalized.offset)
                current = stream.read(1)
                if current and current[0] & 0xC0 == 0x80:
                    raise WorkspaceToolError("invalid_utf8_boundary")
                stream.seek(normalized.offset)
                raw = stream.read(normalized.max_bytes + 3)
            finally:
                stream.close()
            chosen = None
            for length in range(min(normalized.max_bytes, len(raw)), -1, -1):
                try:
                    chosen = raw[:length].decode("utf-8", "strict")
                    break
                except UnicodeDecodeError:
                    continue
            if chosen is None:
                raise WorkspaceToolError("artifact_unavailable")
        except WorkspaceToolError:
            raise
        except ArtifactStorageError as exc:
            try:
                mark_artifact_unavailable(db, artifact.id)
                db.commit()
            except Exception:  # noqa: BLE001 - failure stays a fixed unavailable result.
                db.rollback()
            raise WorkspaceToolError("artifact_unavailable") from exc

        control.raise_if_stopped()
        while True:
            next_offset = normalized.offset + len(chosen.encode("utf-8"))
            result = WorkspaceReadResult(
                workspace_id=workspace.id,
                artifact_id=artifact.id,
                version_id=artifact.id,
                sha256=artifact.sha256,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes,
                offset=normalized.offset,
                content=chosen,
                next_offset=next_offset if next_offset < artifact.size_bytes else None,
            )
            if len(canonical_json(result.model_dump(mode="json"))) <= 16 * 1024:
                return result
            if not chosen:
                raise WorkspaceToolError("workspace_unavailable")
            chosen = chosen[:-1]
