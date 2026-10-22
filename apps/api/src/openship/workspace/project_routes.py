"""Authenticated owner-only Project lifecycle endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..database.session import get_db
from .models import Project
from .projects import (
    ProjectLifecycleError,
    create_project,
    delete_project,
    get_owned_project,
    list_owned_projects,
    rename_project,
)

router = APIRouter(prefix="/api", tags=["projects"])

_ERROR_MESSAGES = {
    "project_not_found": "Project not found",
    "project_name_invalid": "Project name must be 1 to 255 characters after trimming",
    "project_not_empty": "Project has retained data and cannot be deleted",
    "last_project_required": "Every account must keep at least one project",
    "project_required": "Choose a project before creating a conversation",
}


class _ProjectName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


def _summary(project: Project) -> dict[str, str]:
    return {"id": str(project.id), "name": project.name}


def _raise_project_error(error: ProjectLifecycleError) -> None:
    status_code = {
        "project_not_found": 404,
        "project_name_invalid": 422,
        "project_not_empty": 409,
        "last_project_required": 409,
        "project_required": 409,
    }.get(error.code, 503)
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": error.code,
                "message": _ERROR_MESSAGES.get(error.code, "Project operation is unavailable"),
            }
        },
    ) from None


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "project_unavailable",
                    "message": "Project operation is unavailable",
                }
            },
        ) from None


@router.get("/projects")
def project_list(
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    return [_summary(project) for project in list_owned_projects(db, user.id)]


@router.post("/projects", status_code=201)
def project_create(
    request: _ProjectName,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        project = create_project(db, owner_id=user.id, name=request.name)
        _commit(db)
        return _summary(project)
    except ProjectLifecycleError as error:
        db.rollback()
        _raise_project_error(error)


@router.get("/projects/{project_id}")
def project_get(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        return _summary(get_owned_project(db, owner_id=user.id, project_id=project_id))
    except ProjectLifecycleError as error:
        _raise_project_error(error)


@router.patch("/projects/{project_id}")
def project_rename(
    project_id: uuid.UUID,
    request: _ProjectName,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        project = rename_project(
            db,
            owner_id=user.id,
            project_id=project_id,
            name=request.name,
        )
        _commit(db)
        return _summary(project)
    except ProjectLifecycleError as error:
        db.rollback()
        _raise_project_error(error)


@router.delete("/projects/{project_id}", status_code=204)
def project_delete(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        delete_project(db, owner_id=user.id, project_id=project_id)
        _commit(db)
        return Response(status_code=204)
    except ProjectLifecycleError as error:
        db.rollback()
        _raise_project_error(error)
