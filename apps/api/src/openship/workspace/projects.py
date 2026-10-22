"""Owner-scoped Project lifecycle commands."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth.models import User
from .lifecycle import ArchiveStateError, assert_active_project
from .models import Project


class ProjectLifecycleError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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


def list_owned_projects(
    db: Session,
    owner_id: uuid.UUID,
    *,
    state: Literal["active", "archived"] = "active",
) -> list[Project]:
    query = db.query(Project).filter(Project.owner_user_id == owner_id)
    if state == "active":
        query = query.filter(Project.archived_at.is_(None))
    else:
        query = query.filter(Project.archived_at.is_not(None))
    return query.order_by(Project.created_at.asc(), Project.id.asc()).all()


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
        query = query.with_for_update().populate_existing()
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
    assert_active_project(project)
    project.name = normalized_name
    project.updated_at = func.now()
    db.flush()
    return project


def archive_project(db: Session, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    """Archive an owned project without changing any retained project data."""
    _lock_owner(db, owner_id)
    project = get_owned_project(
        db,
        owner_id=owner_id,
        project_id=project_id,
        for_update=True,
    )
    if project.archived_at is not None:
        return project

    active_project_count = (
        db.query(func.count(Project.id))
        .filter(Project.owner_user_id == owner_id, Project.archived_at.is_(None))
        .scalar()
    )
    if active_project_count <= 1:
        raise ProjectLifecycleError("last_project_required")

    project.archived_at = datetime.now(UTC)
    db.flush()
    db.refresh(project, attribute_names=["archived_at"])
    return project


def restore_project(db: Session, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    _lock_owner(db, owner_id)
    project = get_owned_project(
        db,
        owner_id=owner_id,
        project_id=project_id,
        for_update=True,
    )
    if project.archived_at is not None:
        project.archived_at = None
        db.flush()
    return project


def delete_project(db: Session, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Compatibility alias for project archival; project data is never deleted."""
    archive_project(db, owner_id=owner_id, project_id=project_id)


def select_project_for_conversation(
    db: Session,
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID | None,
) -> Project:
    """Select an active project while serializing creation against archive."""
    _lock_owner(db, owner_id)
    if project_id is not None:
        project = get_owned_project(
            db,
            owner_id=owner_id,
            project_id=project_id,
            for_update=True,
        )
        assert_active_project(project)
        return project

    candidates = (
        db.query(Project)
        .filter(Project.owner_user_id == owner_id, Project.archived_at.is_(None))
        .order_by(Project.created_at.asc(), Project.id.asc())
        .limit(2)
        .with_for_update()
        .populate_existing()
        .all()
    )
    if not candidates:
        has_archived_project = (
            db.query(Project.id)
            .filter(Project.owner_user_id == owner_id, Project.archived_at.is_not(None))
            .first()
        )
        if has_archived_project:
            raise ArchiveStateError("project_archived")
        raise ProjectLifecycleError("project_not_found")
    if len(candidates) > 1:
        raise ProjectLifecycleError("project_required")
    return candidates[0]
