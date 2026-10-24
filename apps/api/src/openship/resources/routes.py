"""Authenticated, owner-scoped resource setup and observation metadata."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..database.session import get_db
from .contracts import (
    ResourceObservationPage,
    ResourcePage,
)
from .service import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ResourceServiceError,
    authorize_resource_project,
    list_environments,
    list_resource_bindings,
    list_resource_observations,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["resources"])

_ERRORS = {
    "project_not_found": (404, "Project not found."),
    "resource_not_found": (404, "Resource not found."),
    "invalid_cursor": (422, "The resource cursor is invalid."),
    "invalid_page_size": (422, "The resource page size is invalid."),
    "resource_unavailable": (503, "Resource observation metadata is unavailable."),
}


def _raise_service_error(error: ResourceServiceError) -> None:
    status_code, message = _ERRORS.get(
        error.code, (503, "The resource operation is unavailable.")
    )
    raise HTTPException(
        status_code=status_code,
        detail={"error": {"code": error.code, "message": message}},
    ) from None


@router.get("/environments")
def environments_list(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        return [item.model_dump(mode="json") for item in list_environments(
            db, owner_id=user.id, project_id=project_id
        )]
    except ResourceServiceError as error:
        _raise_service_error(error)


@router.post("/environments", status_code=410)
def environments_create(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        authorize_resource_project(db, owner_id=user.id, project_id=project_id)
    except ResourceServiceError as error:
        _raise_service_error(error)
    raise HTTPException(
        status_code=410,
        detail={
            "error": {
                "code": "resource_binding_retired",
                "message": "Environment and resource binding setup is no longer supported.",
            }
        },
    )


@router.get("/resources", response_model=ResourcePage)
def resources_list(
    project_id: UUID,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=256),
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        items, next_cursor = list_resource_bindings(
            db,
            owner_id=user.id,
            project_id=project_id,
            limit=limit,
            cursor=cursor,
        )
        return ResourcePage(items=tuple(items), next_cursor=next_cursor)
    except ResourceServiceError as error:
        _raise_service_error(error)


@router.post("/resources", status_code=410)
def resources_create(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        authorize_resource_project(db, owner_id=user.id, project_id=project_id)
    except ResourceServiceError as error:
        _raise_service_error(error)
    raise HTTPException(
        status_code=410,
        detail={
            "error": {
                "code": "resource_binding_retired",
                "message": "Environment and resource binding setup is no longer supported.",
            }
        },
    )


@router.get("/resources/{resource_id}/observations", response_model=ResourceObservationPage)
def resource_observations_list(
    project_id: UUID,
    resource_id: UUID,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=256),
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        items, next_cursor = list_resource_observations(
            db,
            owner_id=user.id,
            project_id=project_id,
            resource_id=resource_id,
            limit=limit,
            cursor=cursor,
        )
        return ResourceObservationPage(items=tuple(items), next_cursor=next_cursor)
    except ResourceServiceError as error:
        _raise_service_error(error)


__all__ = ["router"]
