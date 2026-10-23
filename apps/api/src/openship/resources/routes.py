"""Authenticated, owner-scoped resource setup and observation metadata."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..database.session import get_db
from .contracts import (
    EnvironmentCreate,
    EnvironmentSummary,
    ResourceBindingCreate,
    ResourceObservationPage,
    ResourcePage,
)
from .service import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ResourceServiceError,
    create_environment,
    create_resource_binding,
    get_resource_binding_summary,
    list_environments,
    list_resource_bindings,
    list_resource_observations,
)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["resources"])

_ERRORS = {
    "project_not_found": (404, "Project not found."),
    "project_archived": (409, "Restore this project before adding resources."),
    "environment_not_found": (404, "Environment not found."),
    "environment_conflict": (409, "An environment with this name already exists."),
    "environment_limit_exceeded": (409, "This project has reached its environment limit."),
    "resource_not_found": (404, "Resource not found."),
    "resource_scope_denied": (404, "The selected environment or AWS Connector is not available."),
    "resource_binding_conflict": (409, "This bucket is already bound in this environment."),
    "resource_limit_exceeded": (409, "This project has reached its resource binding limit."),
    "connector_revoked": (409, "The selected AWS Connector has been revoked."),
    "connector_unavailable": (409, "The selected AWS Connector is unavailable."),
    "connector_revision_changed": (409, "Select the current AWS Connector revision."),
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


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "resource_conflict",
                    "message": "The resource conflicts with an existing project binding.",
                }
            },
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "resource_unavailable",
                    "message": "The resource operation is unavailable.",
                }
            },
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


@router.post("/environments", status_code=201, response_model=EnvironmentSummary)
def environments_create(
    project_id: UUID,
    request: EnvironmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        environment = create_environment(
            db,
            owner_id=user.id,
            project_id=project_id,
            request=request,
        )
        _commit(db)
        db.refresh(environment)
        return EnvironmentSummary(
            id=environment.id,
            project_id=environment.project_id,
            name=environment.name,
            display_name=environment.display_name,
            created_at=environment.created_at,
        )
    except ResourceServiceError as error:
        db.rollback()
        _raise_service_error(error)


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


@router.post("/resources", status_code=201)
def resources_create(
    project_id: UUID,
    request: ResourceBindingCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        _resource, binding = create_resource_binding(
            db,
            owner_id=user.id,
            project_id=project_id,
            request=request,
        )
        summary = get_resource_binding_summary(
            db,
            owner_id=user.id,
            project_id=project_id,
            binding_id=binding.id,
        )
        _commit(db)
        return summary.model_dump(mode="json")
    except ResourceServiceError as error:
        db.rollback()
        _raise_service_error(error)


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
