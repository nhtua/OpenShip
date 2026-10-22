"""Owner-scoped Project lifecycle commands and safe deletion checks."""

from __future__ import annotations

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..artifacts.models import Artifact, ArtifactQuota
from ..auth.models import User
from ..chat.models import Conversation
from ..connectors.models import (
    Connector,
    ConnectorAuditEvent,
    ConnectorAvailability,
    ConnectorRequestLedger,
    ConnectorRevision,
    CredentialVersion,
    ProjectConnectorDefault,
)
from ..execution.models import ToolOperation
from ..runs.models import Event, Run
from .models import ConversationWorkspace, Project


class ProjectLifecycleError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_PROJECT_DEPENDENCIES = (
    (Conversation, Conversation.project_id),
    (ConversationWorkspace, ConversationWorkspace.project_id),
    (Run, Run.project_id),
    (Event, Event.project_id),
    (ToolOperation, ToolOperation.project_id),
    (Artifact, Artifact.project_id),
    (ArtifactQuota, ArtifactQuota.project_id),
    (Connector, Connector.project_id),
    (CredentialVersion, CredentialVersion.project_id),
    (ConnectorRevision, ConnectorRevision.project_id),
    (ProjectConnectorDefault, ProjectConnectorDefault.project_id),
    (ConnectorRequestLedger, ConnectorRequestLedger.project_id),
    (ConnectorAuditEvent, ConnectorAuditEvent.project_id),
    (ConnectorAuditEvent, ConnectorAuditEvent.consuming_project_id),
    (ConnectorAvailability, ConnectorAvailability.project_id),
)


def _validated_name(name: str) -> str:
    if not isinstance(name, str):
        raise ProjectLifecycleError("project_name_invalid")
    normalized = name.strip()
    if not normalized or len(normalized) > 255:
        raise ProjectLifecycleError("project_name_invalid")
    return normalized


def _lock_owner(db: Session, owner_id: uuid.UUID) -> User:
    owner = (
        db.query(User)
        .filter(User.id == owner_id)
        .with_for_update()
        .first()
    )
    if owner is None:
        raise ProjectLifecycleError("project_not_found")
    return owner


def list_owned_projects(db: Session, owner_id: uuid.UUID) -> list[Project]:
    return (
        db.query(Project)
        .filter(Project.owner_user_id == owner_id)
        .order_by(Project.created_at.asc(), Project.id.asc())
        .all()
    )


def get_owned_project(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    for_update: bool = False,
) -> Project:
    query = db.query(Project).filter(
        Project.id == project_id,
        Project.owner_user_id == owner_id,
    )
    if for_update:
        query = query.with_for_update()
    project = query.first()
    if project is None:
        raise ProjectLifecycleError("project_not_found")
    return project


def create_project(db: Session, *, owner_id: uuid.UUID, name: str) -> Project:
    normalized_name = _validated_name(name)
    _lock_owner(db, owner_id)
    project = Project(owner_user_id=owner_id, name=normalized_name)
    db.add(project)
    db.flush()
    return project


def rename_project(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID,
    name: str,
) -> Project:
    normalized_name = _validated_name(name)
    _lock_owner(db, owner_id)
    project = get_owned_project(
        db,
        owner_id=owner_id,
        project_id=project_id,
        for_update=True,
    )
    project.name = normalized_name
    project.updated_at = func.now()
    db.flush()
    return project


def _has_retained_data(db: Session, project_id: uuid.UUID) -> bool:
    for model, project_column in _PROJECT_DEPENDENCIES:
        if db.query(model.id).filter(project_column == project_id).limit(1).first():
            return True
    return False


def delete_project(db: Session, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
    _lock_owner(db, owner_id)
    project = get_owned_project(
        db,
        owner_id=owner_id,
        project_id=project_id,
        for_update=True,
    )
    project_count = (
        db.query(func.count(Project.id))
        .filter(Project.owner_user_id == owner_id)
        .scalar()
    )
    if project_count <= 1:
        raise ProjectLifecycleError("last_project_required")
    if _has_retained_data(db, project.id):
        raise ProjectLifecycleError("project_not_empty")
    db.delete(project)
    db.flush()


def select_project_for_conversation(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID | None,
) -> Project:
    """Lock the owner and selected project so creation cannot race deletion."""
    _lock_owner(db, owner_id)
    if project_id is not None:
        return get_owned_project(
            db,
            owner_id=owner_id,
            project_id=project_id,
            for_update=True,
        )

    candidates = (
        db.query(Project)
        .filter(Project.owner_user_id == owner_id)
        .order_by(Project.created_at.asc(), Project.id.asc())
        .limit(2)
        .with_for_update()
        .all()
    )
    if not candidates:
        raise ProjectLifecycleError("project_not_found")
    if len(candidates) > 1:
        raise ProjectLifecycleError("project_required")
    return candidates[0]
