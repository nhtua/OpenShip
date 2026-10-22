"""Owner-scoped conversation lifecycle reads and commands."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..workspace.lifecycle import assert_active_project
from ..workspace.models import Project
from .models import Conversation


class ConversationLifecycleError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def get_owned_conversation_scope(
    db: Session,
    *,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID,
    for_update: bool = False,
) -> tuple[Conversation, Project]:
    """Load an owned conversation and project, locking them in that order."""
    conversation_query = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.user_id == owner_id,
    )
    if for_update:
        conversation_query = conversation_query.with_for_update().populate_existing()
    conversation = conversation_query.first()
    if conversation is None:
        raise ConversationLifecycleError("conversation_not_found")

    project_query = db.query(Project).filter(
        Project.id == conversation.project_id,
        Project.owner_user_id == owner_id,
    )
    if for_update:
        project_query = project_query.with_for_update().populate_existing()
    project = project_query.first()
    if project is None:
        raise ConversationLifecycleError("conversation_not_found")
    return conversation, project


def list_owned_conversations(
    db: Session,
    *,
    owner_id: uuid.UUID,
    state: Literal["active", "archived"] = "active",
) -> list[tuple[Conversation, Project]]:
    query = (
        db.query(Conversation, Project)
        .join(
            Project,
            and_(
                Project.id == Conversation.project_id,
                Project.owner_user_id == owner_id,
            ),
        )
        .filter(Conversation.user_id == owner_id)
    )
    if state == "active":
        query = query.filter(
            Conversation.archived_at.is_(None),
            Project.archived_at.is_(None),
        )
    else:
        query = query.filter(
            or_(
                Conversation.archived_at.is_not(None),
                Project.archived_at.is_not(None),
            )
        )
    return query.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).all()


def archive_conversation(
    db: Session,
    *,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> tuple[Conversation, Project]:
    conversation, project = get_owned_conversation_scope(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        for_update=True,
    )
    assert_active_project(project)
    if conversation.archived_at is None:
        conversation.archived_at = datetime.now(UTC)
        db.flush()
        db.refresh(conversation, attribute_names=["archived_at"])
    return conversation, project


def restore_conversation(
    db: Session,
    *,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> tuple[Conversation, Project]:
    conversation, project = get_owned_conversation_scope(
        db,
        owner_id=owner_id,
        conversation_id=conversation_id,
        for_update=True,
    )
    assert_active_project(project)
    if conversation.archived_at is not None:
        conversation.archived_at = None
        db.flush()
    return conversation, project
