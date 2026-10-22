"""Shared assertions for project and conversation lifecycle state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException

from ..chat.models import Conversation
from .models import Project

ArchiveErrorCode = Literal["project_archived", "conversation_archived"]


class ArchiveStateError(RuntimeError):
    """A requested mutation targets an archived project or conversation."""

    def __init__(self, code: ArchiveErrorCode):
        self.code = code
        super().__init__(code)


def format_archive_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def assert_active_project(project: Project) -> None:
    if project.archived_at is not None:
        raise ArchiveStateError("project_archived")


def assert_active_conversation(conversation: Conversation, project: Project) -> None:
    assert_active_project(project)
    if conversation.archived_at is not None:
        raise ArchiveStateError("conversation_archived")


def archive_http_error(error: ArchiveStateError) -> HTTPException:
    messages = {
        "project_archived": "Restore this project before making changes.",
        "conversation_archived": "Restore this conversation before continuing.",
    }
    return HTTPException(
        status_code=409,
        detail={"error": {"code": error.code, "message": messages[error.code]}},
    )
