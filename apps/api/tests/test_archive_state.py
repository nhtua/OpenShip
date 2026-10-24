import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from src.openship.chat.models import Conversation
from src.openship.workspace.lifecycle import (
    ArchiveStateError,
    archive_http_error,
    assert_active_conversation,
    assert_active_project,
)
from src.openship.workspace.models import Project


def _project(archived_at=None) -> Project:
    project = Project(id=uuid.uuid4(), owner_user_id=uuid.uuid4(), name="Test")
    project.archived_at = archived_at
    return project


def _conversation(project: Project, archived_at=None) -> Conversation:
    conversation = Conversation(
        id=uuid.uuid4(), user_id=project.owner_user_id, project_id=project.id
    )
    conversation.archived_at = archived_at
    return conversation


def test_active_scope_error_precedence():
    archived_at = datetime(2026, 10, 22, tzinfo=UTC)

    active_project = _project()
    active_conversation = _conversation(active_project)
    assert_active_project(active_project)
    assert_active_conversation(active_conversation, active_project)

    archived_project = _project(archived_at)
    with pytest.raises(ArchiveStateError) as project_error:
        assert_active_project(archived_project)
    assert project_error.value.code == "project_archived"

    conversation = _conversation(active_project, archived_at)
    with pytest.raises(ArchiveStateError) as conversation_error:
        assert_active_conversation(conversation, active_project)
    assert conversation_error.value.code == "conversation_archived"

    with pytest.raises(ArchiveStateError) as parent_error:
        assert_active_conversation(conversation, archived_project)
    assert parent_error.value.code == "project_archived"


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("project_archived", "Restore this project before making changes."),
        ("conversation_archived", "Restore this conversation before continuing."),
    ],
)
def test_archive_http_error_uses_lifecycle_conflict_envelope(code, message):
    error = archive_http_error(ArchiveStateError(code))

    assert isinstance(error, HTTPException)
    assert error.status_code == 409
    assert error.detail == {"error": {"code": code, "message": message}}
