"""Authenticated built-in tool catalog and installation policy routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.capabilities.catalog import (
    list_capabilities,
    list_installed_tool_definitions,
)
from src.openship.capabilities.contracts import (
    CapabilitySummary,
    ProjectCapabilitySummary,
)
from src.openship.capabilities.definitions import get_tool_definition
from src.openship.capabilities.policy import (
    EffectiveToolPolicy,
    ToolPolicyError,
    cutover_legacy_tool_allowlist,
    read_effective_tool_policy,
    set_tool_policy,
)
from src.openship.config import settings
from src.openship.connectors.selection import ConnectorSelectionError, resolve_connector
from src.openship.connectors.service import (
    ConnectorCommandError,
    require_global_operator,
)
from src.openship.database.session import get_db
from src.openship.execution.availability import read_tool_availability
from src.openship.resources.models import ResourceBinding
from src.openship.workspace.models import Project

router = APIRouter(tags=["capabilities"])


class _PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolPolicyUpdate(_PolicyRequest):
    enabled: StrictBool
    expected_revision: StrictInt = Field(ge=1)


class ToolPolicyCutover(_PolicyRequest):
    expected_revision: StrictInt = Field(ge=1)


class ToolPolicyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    enabled: bool
    permitted: bool
    deployment_restricted: bool
    unavailable_reason: Literal["disabled_by_operator", "deployment_restricted"] | None


class ToolPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int
    tools: list[ToolPolicyEntry]
    effective_ids: list[str]
    deployment_ceiling_active: bool
    deployment_ceiling_ids: list[str] | None
    unknown_deployment_ids: list[str]
    cutover_available: bool
    legacy_cutover_at: datetime | None


_POLICY_MESSAGES = {
    "global_management_forbidden": (
        "Only the configured installation operator can manage built-in tool availability"
    ),
    "tool_policy_unavailable": "Managed tool availability is unavailable",
    "tool_policy_revision_changed": "Tool availability changed; reload Global Settings and try again",
    "tool_not_installed": "This built-in tool is not installed",
    "legacy_cutover_complete": "The legacy tool allowlist was already adopted",
    "legacy_cutover_not_required": "There is no legacy tool allowlist to adopt",
}

_POLICY_STATUS = {
    "global_management_forbidden": 403,
    "tool_policy_unavailable": 503,
    "tool_policy_revision_changed": 409,
    "tool_not_installed": 404,
    "legacy_cutover_complete": 409,
    "legacy_cutover_not_required": 409,
}


def _policy_http_error(error: ToolPolicyError | ConnectorCommandError) -> HTTPException:
    code = error.code
    if isinstance(error, ConnectorCommandError):
        code = "global_management_forbidden"
    return HTTPException(
        status_code=_POLICY_STATUS.get(code, 503),
        detail={
            "error": {
                "code": code,
                "message": _POLICY_MESSAGES.get(code, "Tool availability could not be changed"),
            }
        },
    )


def _policy_response(snapshot: EffectiveToolPolicy) -> ToolPolicyResponse:
    if not snapshot.state_available:
        raise _policy_http_error(ToolPolicyError("tool_policy_unavailable"))
    tools = []
    for manifest in list_installed_tool_definitions():
        enabled = manifest.id in snapshot.operator_enabled_ids
        restricted = manifest.id in snapshot.deployment_restricted_ids
        reason = (
            "deployment_restricted"
            if restricted
            else "disabled_by_operator"
            if not enabled
            else None
        )
        definition = get_tool_definition(manifest)
        tools.append(
            ToolPolicyEntry(
                id=manifest.id,
                description=definition.description,
                enabled=enabled,
                permitted=manifest.id in snapshot.effective_ids,
                deployment_restricted=restricted,
                unavailable_reason=reason,
            )
        )
    ceiling = settings.tool_enabled_ceiling
    return ToolPolicyResponse(
        revision=snapshot.revision,
        tools=tools,
        effective_ids=sorted(snapshot.effective_ids),
        deployment_ceiling_active=ceiling is not None,
        deployment_ceiling_ids=sorted(ceiling) if ceiling is not None else None,
        unknown_deployment_ids=list(snapshot.unknown_deployment_ids),
        cutover_available=snapshot.cutover_available,
        legacy_cutover_at=snapshot.legacy_cutover_at,
    )


@router.get("/api/global-tool-policy", response_model=ToolPolicyResponse)
def get_global_tool_policy(
    user: User = Depends(require_jwt), db: Session = Depends(get_db)
) -> ToolPolicyResponse:
    try:
        require_global_operator(user)
        return _policy_response(read_effective_tool_policy(db))
    except (ToolPolicyError, ConnectorCommandError) as error:
        raise _policy_http_error(error) from None


@router.put(
    "/api/global-tool-policy/{capability_id}", response_model=ToolPolicyResponse
)
def update_global_tool_policy(
    capability_id: str,
    body: ToolPolicyUpdate,
    user: User = Depends(require_jwt),
    db: Session = Depends(get_db),
) -> ToolPolicyResponse:
    try:
        snapshot = set_tool_policy(
            db,
            actor=user,
            capability_id=capability_id,
            enabled=body.enabled,
            expected_revision=body.expected_revision,
        )
        db.commit()
        return _policy_response(snapshot)
    except (ToolPolicyError, ConnectorCommandError) as error:
        db.rollback()
        raise _policy_http_error(error) from None
    except SQLAlchemyError:
        db.rollback()
        raise _policy_http_error(ToolPolicyError("tool_policy_unavailable")) from None


@router.post("/api/global-tool-policy/cutover", response_model=ToolPolicyResponse)
def cutover_global_tool_policy(
    body: ToolPolicyCutover,
    user: User = Depends(require_jwt),
    db: Session = Depends(get_db),
) -> ToolPolicyResponse:
    try:
        snapshot = cutover_legacy_tool_allowlist(
            db, actor=user, expected_revision=body.expected_revision
        )
        db.commit()
        return _policy_response(snapshot)
    except (ToolPolicyError, ConnectorCommandError) as error:
        db.rollback()
        raise _policy_http_error(error) from None
    except SQLAlchemyError:
        db.rollback()
        raise _policy_http_error(ToolPolicyError("tool_policy_unavailable")) from None


@router.get("/api/capabilities", response_model=list[CapabilitySummary])
def get_capabilities(
    _user: User = Depends(require_jwt), db: Session = Depends(get_db)
) -> list[CapabilitySummary]:
    policy = read_effective_tool_policy(db)
    return list_capabilities(
        enabled_ids=policy.effective_ids,
        deployment_restricted_ids=policy.deployment_restricted_ids,
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

    policy = read_effective_tool_policy(db)
    availability = read_tool_availability(db)
    active_bindings = (
        db.query(ResourceBinding.id)
        .filter(
            ResourceBinding.project_id == project.id,
            ResourceBinding.state == "active",
        )
        .count()
    )
    result = []
    for manifest in list_installed_tool_definitions():
        connector_kinds = getattr(manifest, "connector_kinds", ())
        target_kinds = getattr(manifest, "target_kinds", ())
        readiness = {}
        for kind in connector_kinds:
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

        policy_enabled = manifest.id in policy.operator_enabled_ids
        deployment_restricted = manifest.id in policy.deployment_restricted_ids
        permitted = manifest.id in policy.effective_ids
        unavailable_reason = None
        if not policy.state_available:
            unavailable_reason = "policy_unavailable"
        elif deployment_restricted:
            unavailable_reason = "deployment_restricted"
        elif not policy_enabled:
            unavailable_reason = "disabled_by_operator"
        elif any(value == "ambiguous" for value in readiness.values()):
            unavailable_reason = "connector_ambiguous"
        elif any(value == "required" for value in readiness.values()):
            unavailable_reason = "connector_required"
        elif target_kinds and active_bindings == 0:
            unavailable_reason = "target_required"
        else:
            observed = availability.get(manifest.id)
            if observed is not None and not observed[0]:
                unavailable_reason = "runtime_unavailable"

        result.append(
            ProjectCapabilitySummary(
                id=manifest.id,
                version=manifest.version,
                manifest_digest=manifest.manifest_digest,
                input_schema=manifest.input_schema,
                output_schema=manifest.output_schema,
                effect=manifest.effect,
                permitted=permitted,
                policy_enabled=policy_enabled,
                deployment_restricted=deployment_restricted,
                unavailable_reason=unavailable_reason,
                required_connector_kinds=tuple(connector_kinds),
                connector_readiness=readiness,
            )
        )
    return result
