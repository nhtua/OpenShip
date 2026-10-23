import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.capabilities.catalog import (
    list_capabilities,
    list_capability_manifests,
)
from src.openship.capabilities.contracts import (
    CapabilitySummary,
    ProjectCapabilitySummary,
)
from src.openship.config import settings
from src.openship.connectors.selection import ConnectorSelectionError, resolve_connector
from src.openship.connectors.service import list_project_connectors
from src.openship.database.session import get_db
from src.openship.execution.availability import read_tool_availability
from src.openship.workspace.models import Project

router = APIRouter(tags=["capabilities"])


@router.get("/api/capabilities", response_model=list[CapabilitySummary])
def get_capabilities(
    _user: User = Depends(require_jwt), db: Session = Depends(get_db)
) -> list[CapabilitySummary]:
    return list_capabilities(
        availability=read_tool_availability(db),
        credential_check=lambda _binding: True,
    )


@router.get(
    "/api/projects/{project_id}/capabilities",
    response_model=list[ProjectCapabilitySummary],
)
def get_project_capabilities(
    project_id: uuid.UUID,
    user: User = Depends(require_jwt),
    db: Session = Depends(get_db),
) -> list[ProjectCapabilitySummary]:
    project = db.query(Project).filter(
        Project.id == project_id,
        Project.owner_user_id == user.id,
    ).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    result = []
    for manifest in list_capability_manifests():
        connector_kinds = getattr(manifest, "connector_kinds", ())
        readiness = {}
        for kind in connector_kinds:
            if kind == "aws":
                readiness[kind] = (
                    "ready"
                    if any(
                        item.status == "configured"
                        for item in list_project_connectors(
                            db, project_id=project.id, service="aws"
                        )
                    )
                    else "required"
                )
                continue
            try:
                resolve_connector(db, project_id=project.id, service=kind)
            except ConnectorSelectionError as refused:
                readiness[kind] = (
                    "ambiguous"
                    if refused.code == "connector_ambiguous"
                    else "required"
                    if refused.code == "connector_required"
                    else "unavailable"
                )
            else:
                readiness[kind] = "ready"
        result.append(
            ProjectCapabilitySummary(
                id=manifest.id,
                version=manifest.version,
                manifest_digest=manifest.manifest_digest,
                input_schema=manifest.input_schema,
                output_schema=manifest.output_schema,
                effect=manifest.effect,
                permitted=manifest.id in settings.enabled_tool_ids,
                required_connector_kinds=tuple(connector_kinds),
                connector_readiness=readiness,
            )
        )
    return result
