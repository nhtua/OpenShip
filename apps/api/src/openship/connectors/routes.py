"""Authenticated, origin-checked, write-only Connector setup APIs."""

# Route boundaries catch validation and persistence failures to emit fixed,
# secret-safe responses without exposing Pydantic input or driver messages.
# ruff: noqa: BLE001

from __future__ import annotations

import json
import uuid
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..database.session import get_db
from ..tools.search.health import check_search_api_key
from ..workspace.lifecycle import ArchiveStateError, archive_http_error
from .contracts import ConnectorUsage
from .errors import CredentialStoreUnavailable
from .selection import ConnectorSelectionError
from .service import (
    ConnectorCommandError,
    connector_api_key,
    create_connector,
    delete_connector,
    detail,
    finalize_request,
    global_connector,
    list_global_connectors,
    list_project_connectors,
    lock_active_project,
    owned_connector,
    owned_project,
    replace_credential,
    request_status,
    require_global_operator,
    reserve_request,
    set_default,
    update_connector,
)

router = APIRouter(tags=["connectors"])

_ERROR_MESSAGES = {
    "connector_not_found": "Connector or request not found",
    "connector_scope_denied": "Connector is not available in this project",
    "project_not_found": "Project not found",
    "connector_revision_changed": "Connector settings changed; reload and try again",
    "connector_request_conflict": "Request ID was already used for a different command",
    "connector_request_expired": "Request ID has expired",
    "connector_write_rate_limited": "Connector changes are temporarily rate limited",
    "connector_limit_reached": "Connector limit reached for this scope",
    "connector_required": "Choose an eligible Connector before using this service",
    "connector_ambiguous": "Choose one of the eligible Connectors explicitly",
    "connector_expired": "Connector credential has expired",
    "connector_revoked": "Connector has been revoked",
    "connector_unavailable": "Connector is currently unavailable",
    "credential_store_unavailable": "Connector credential storage is unavailable",
    "connector_origin_forbidden": "This browser origin is not allowed to configure Connectors",
    "global_management_forbidden": "Global Connector management is not available to this account",
    "connector_visibility_unsupported": "Brave and Tavily Connectors must be configured globally",
    "invalid_connector_request": "Connector request is invalid",
}


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SetupContext(BaseModel):
    can_manage_global: bool
    credential_store_ready: bool
    unavailable_reason: (
        Literal[
            "installation_id_missing",
            "encryption_key_unavailable",
            "idempotency_key_unavailable",
        ]
        | None
    )


class _CreateConnector(_RequestModel):
    kind: Literal["brave", "tavily"]
    name: str = Field(min_length=1, max_length=255)
    configuration: dict[str, Any] = Field(default_factory=dict)
    api_key: str = Field(min_length=8, max_length=4096, repr=False)


class _ReplaceCredential(_RequestModel):
    expected_revision: int = Field(ge=1)
    api_key: str = Field(min_length=8, max_length=4096, repr=False)


class _UpdateConnector(_RequestModel):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    configuration: dict[str, Any] | None = None
    enabled: bool | None = None
    revoke: bool = False

    @model_validator(mode="after")
    def _has_change(self) -> _UpdateConnector:
        if (
            self.name is None
            and self.configuration is None
            and self.enabled is None
            and not self.revoke
        ):
            raise ValueError("Connector update is empty")
        return self


class _SetDefault(_RequestModel):
    expected_revision: int = Field(ge=0)
    connector_id: uuid.UUID | None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON property")
        value[key] = item
    return value


def _reject_json_constant(_value: str):
    raise ValueError("non-finite JSON number")


def _http_error(code: str, status_code: int | None = None) -> HTTPException:
    status = status_code or ConnectorCommandError(code).status_code
    return HTTPException(
        status_code=status,
        detail={
            "error": {
                "code": code,
                "message": _ERROR_MESSAGES.get(code, "Connector operation failed"),
            }
        },
    )


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ArchiveStateError):
        return archive_http_error(exc)
    if isinstance(exc, ConnectorCommandError):
        return _http_error(exc.code, exc.status_code)
    if isinstance(exc, (CredentialStoreUnavailable,)):
        return _http_error("credential_store_unavailable", 503)
    if isinstance(exc, ConnectorSelectionError):
        return _http_error(exc.code)
    return _http_error("connector_unavailable", 503)


def _normalized_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    except ValueError:
        return None


def _require_browser_origin(request: Request) -> None:
    origin = _normalized_origin(request.headers.get("origin", ""))
    from ..config import settings

    configured = {
        normalized
        for raw in settings.connector_browser_origins.split(",")
        if (normalized := _normalized_origin(raw.strip())) is not None
    }
    if origin is None or origin not in configured:
        raise _http_error("connector_origin_forbidden", 403)


async def _read_bounded_json(request: Request) -> dict[str, Any]:
    from ..config import settings

    limit = settings.connector_setup_max_body_bytes
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > limit:
                raise _http_error("invalid_connector_request", 413)
        except ValueError:
            raise _http_error("invalid_connector_request", 400) from None
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > limit:
                raise _http_error("invalid_connector_request", 413)
            body.extend(chunk)
        value = json.loads(
            body,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except HTTPException:
        raise
    except Exception:
        raise _http_error("invalid_connector_request", 400) from None
    if not isinstance(value, dict):
        raise _http_error("invalid_connector_request", 400)
    return value


async def _validated_body(
    request: Request, model: type[_RequestModel]
) -> _RequestModel:
    value = await _read_bounded_json(request)
    try:
        return model.model_validate(value)
    except ValidationError:
        # Pydantic's validation errors contain rejected input. Never serialize,
        # log, or chain that exception for a secret-bearing request.
        raise _http_error("invalid_connector_request", 422) from None


def _request_id(request: Request) -> uuid.UUID:
    value = request.headers.get("idempotency-key", "")
    try:
        if len(value) > 36:
            raise ValueError
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise _http_error("invalid_connector_request", 400) from None
    if parsed.version != 4:
        raise _http_error("invalid_connector_request", 400)
    return parsed


def _safe_payload(model: BaseModel) -> dict[str, Any]:
    # The returned bytes are immediately keyed-HMACed by reserve_request and
    # are never logged or persisted. This includes secret fields by design.
    return model.model_dump(mode="json", exclude_none=False)


@router.get("/api/connectors/setup-context", response_model=_SetupContext)
def connector_setup_context(user: User = Depends(require_jwt)):
    """Return only fixed setup readiness and the caller's global authority."""
    from ..config import settings
    from .credentials.dependencies import get_credential_store, get_idempotency_keyring

    reason = None
    if settings.connector_installation_id is None:
        reason = "installation_id_missing"
    else:
        try:
            get_credential_store()
        except CredentialStoreUnavailable:
            reason = "encryption_key_unavailable"
        if reason is None:
            try:
                get_idempotency_keyring()
            except CredentialStoreUnavailable:
                reason = "idempotency_key_unavailable"

    return {
        "can_manage_global": (
            settings.connector_operator_user_id is not None
            and user.id == settings.connector_operator_user_id
        ),
        "credential_store_ready": reason is None,
        "unavailable_reason": reason,
    }


def _commit_or_safe_error(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise _http_error("connector_unavailable", 503) from None


def _begin_request(
    db: Session,
    *,
    user: User,
    visibility: str,
    project_id: uuid.UUID | None,
    request_id: uuid.UUID,
    command: str,
    payload: dict[str, Any],
):
    try:
        return reserve_request(
            db,
            actor_user_id=user.id,
            visibility=visibility,
            project_id=project_id,
            request_id=request_id,
            command=command,
            payload=payload,
        )
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.get(
    "/api/projects/{project_id}/connectors", response_model=list[ConnectorUsage]
)
def project_connector_list(
    project_id: uuid.UUID,
    service: str | None = Query(default=None, pattern="^(brave|tavily|aws)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        owned_project(db, project_id=project_id, user_id=user.id)
        return list_project_connectors(db, project_id=project_id, service=service)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.post("/api/projects/{project_id}/connectors", status_code=201)
async def project_connector_create(
    project_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        lock_active_project(db, project_id=project_id, user_id=user.id)
        _require_browser_origin(request)
        raise ConnectorCommandError("connector_visibility_unsupported")
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.get("/api/projects/{project_id}/connectors/{connector_id}")
def project_connector_detail(
    project_id: uuid.UUID,
    connector_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        owned_project(db, project_id=project_id, user_id=user.id)
        connector = owned_connector(
            db, connector_id=connector_id, project_id=project_id
        )
        return detail(db, connector)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.patch("/api/projects/{project_id}/connectors/{connector_id}")
async def project_connector_update(
    project_id: uuid.UUID,
    connector_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        lock_active_project(db, project_id=project_id, user_id=user.id)
        _require_browser_origin(request)
        request_id = _request_id(request)
        body = await _validated_body(request, _UpdateConnector)
        reservation = _begin_request(
            db,
            user=user,
            visibility="project",
            project_id=project_id,
            request_id=request_id,
            command=f"project_connector_update:{connector_id}",
            payload=_safe_payload(body),
        )
        if reservation.replay_result is not None:
            return JSONResponse(status_code=200, content=reservation.replay_result)
        # reserve_request can roll the transaction back after a concurrent
        # duplicate key race, so reacquire the Project before changing rows.
        lock_active_project(db, project_id=project_id, user_id=user.id)
        connector = owned_connector(
            db, connector_id=connector_id, project_id=project_id
        )
        if body.revoke:
            result = delete_connector(
                db,
                connector=connector,
                actor=user,
                expected_revision=body.expected_revision,
            )
        else:
            result = update_connector(
                db,
                connector=connector,
                actor=user,
                expected_revision=body.expected_revision,
                name=body.name,
                configuration_raw=body.configuration,
                enabled=body.enabled,
            )
        finalize_request(
            reservation, result, connector_id=None if body.revoke else connector_id
        )
        _commit_or_safe_error(db)
        return result
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.post("/api/projects/{project_id}/connectors/{connector_id}/credentials")
async def project_connector_replace_credential(
    project_id: uuid.UUID,
    connector_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        lock_active_project(db, project_id=project_id, user_id=user.id)
        connector = owned_connector(
            db, connector_id=connector_id, project_id=project_id
        )
        _require_browser_origin(request)
        request_id = _request_id(request)
        body = await _validated_body(request, _ReplaceCredential)
        reservation = _begin_request(
            db,
            user=user,
            visibility="project",
            project_id=project_id,
            request_id=request_id,
            command=f"project_connector_credential:{connector_id}",
            payload=_safe_payload(body),
        )
        if reservation.replay_result is not None:
            return JSONResponse(status_code=200, content=reservation.replay_result)
        lock_active_project(db, project_id=project_id, user_id=user.id)
        result = replace_credential(
            db,
            connector=connector,
            actor=user,
            expected_revision=body.expected_revision,
            api_key=body.api_key,
        )
        finalize_request(reservation, result, connector_id=connector_id)
        _commit_or_safe_error(db)
        return result
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.post("/api/projects/{project_id}/connectors/{connector_id}/check")
async def project_connector_check(
    project_id: uuid.UUID,
    connector_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        owned_project(db, project_id=project_id, user_id=user.id)
        connector = owned_connector(
            db, connector_id=connector_id, project_id=project_id
        )
        _require_browser_origin(request)
        api_key = connector_api_key(db, connector)
        status = await check_search_api_key(connector.kind, api_key)
        provider = "Brave Search" if connector.kind == "brave" else "Tavily Search"
        message = {
            "valid": f"{provider} accepted this API key.",
            "invalid": f"{provider} rejected this API key.",
            "unavailable": f"{provider} could not verify this key right now. Try again later.",
        }[status]
        return {
            "status": status,
            "message": message,
            "revision": connector.current_revision,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.get("/api/projects/{project_id}/connector-requests/{request_id}")
def project_connector_request_status(
    project_id: uuid.UUID,
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        owned_project(db, project_id=project_id, user_id=user.id)
        return request_status(
            db,
            actor_user_id=user.id,
            visibility="project",
            project_id=project_id,
            request_id=request_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from None


@router.get("/api/projects/{project_id}/connector-defaults/{service}")
def project_connector_default_get(
    project_id: uuid.UUID,
    service: Literal["brave", "tavily"],
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        owned_project(db, project_id=project_id, user_id=user.id)
        from .models import ProjectConnectorDefault

        row = (
            db.query(ProjectConnectorDefault)
            .filter(
                ProjectConnectorDefault.project_id == project_id,
            )
            .first()
        )
        return {
            "service": service,
            "connector_id": (
                str(row.connector_id)
                if row and row.service == service and row.connector_id
                else None
            ),
            "revision": row.settings_revision if row else 0,
        }
    except Exception as exc:
        raise _translate_error(exc) from None


@router.put("/api/projects/{project_id}/connector-defaults/{service}")
async def project_connector_default_set(
    project_id: uuid.UUID,
    service: Literal["brave", "tavily"],
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        lock_active_project(db, project_id=project_id, user_id=user.id)
        _require_browser_origin(request)
        request_id = _request_id(request)
        body = await _validated_body(request, _SetDefault)
        reservation = _begin_request(
            db,
            user=user,
            visibility="project",
            project_id=project_id,
            request_id=request_id,
            command=f"project_connector_default:{service}",
            payload=_safe_payload(body),
        )
        if reservation.replay_result is not None:
            return JSONResponse(status_code=200, content=reservation.replay_result)
        lock_active_project(db, project_id=project_id, user_id=user.id)
        result = set_default(
            db,
            project_id=project_id,
            actor=user,
            service=service,
            expected_revision=body.expected_revision,
            connector_id=body.connector_id,
        )
        finalize_request(
            reservation,
            result,
            connector_id=body.connector_id,
        )
        _commit_or_safe_error(db)
        return result
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.get("/api/global-connectors", response_model=list[ConnectorUsage])
def global_connector_list(
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        require_global_operator(user)
        return list_global_connectors(db)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.post("/api/global-connectors", status_code=201)
async def global_connector_create(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        require_global_operator(user)
        _require_browser_origin(request)
        request_id = _request_id(request)
        body = await _validated_body(request, _CreateConnector)
        reservation = _begin_request(
            db,
            user=user,
            visibility="global",
            project_id=None,
            request_id=request_id,
            command="global_connector_create",
            payload=_safe_payload(body),
        )
        if reservation.replay_result is not None:
            return JSONResponse(status_code=200, content=reservation.replay_result)
        result = create_connector(
            db,
            actor=user,
            visibility="global",
            project_id=None,
            kind=body.kind,
            name=body.name,
            configuration_raw=body.configuration,
            api_key=body.api_key,
        )
        finalize_request(reservation, result, connector_id=uuid.UUID(result["id"]))
        _commit_or_safe_error(db)
        return JSONResponse(status_code=201, content=result)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.get("/api/global-connectors/{connector_id}")
def global_connector_detail(
    connector_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        require_global_operator(user)
        return detail(db, global_connector(db, connector_id=connector_id))
    except Exception as exc:
        raise _translate_error(exc) from None


@router.patch("/api/global-connectors/{connector_id}")
async def global_connector_update(
    connector_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        require_global_operator(user)
        _require_browser_origin(request)
        request_id = _request_id(request)
        body = await _validated_body(request, _UpdateConnector)
        reservation = _begin_request(
            db,
            user=user,
            visibility="global",
            project_id=None,
            request_id=request_id,
            command=f"global_connector_update:{connector_id}",
            payload=_safe_payload(body),
        )
        if reservation.replay_result is not None:
            return JSONResponse(status_code=200, content=reservation.replay_result)
        connector = global_connector(db, connector_id=connector_id)
        if body.revoke:
            result = delete_connector(
                db,
                connector=connector,
                actor=user,
                expected_revision=body.expected_revision,
            )
        else:
            result = update_connector(
                db,
                connector=connector,
                actor=user,
                expected_revision=body.expected_revision,
                name=body.name,
                configuration_raw=body.configuration,
                enabled=body.enabled,
            )
        finalize_request(
            reservation, result, connector_id=None if body.revoke else connector_id
        )
        _commit_or_safe_error(db)
        return result
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.post("/api/global-connectors/{connector_id}/credentials")
async def global_connector_replace_credential(
    connector_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        require_global_operator(user)
        connector = global_connector(db, connector_id=connector_id)
        _require_browser_origin(request)
        request_id = _request_id(request)
        body = await _validated_body(request, _ReplaceCredential)
        reservation = _begin_request(
            db,
            user=user,
            visibility="global",
            project_id=None,
            request_id=request_id,
            command=f"global_connector_credential:{connector_id}",
            payload=_safe_payload(body),
        )
        if reservation.replay_result is not None:
            return JSONResponse(status_code=200, content=reservation.replay_result)
        result = replace_credential(
            db,
            connector=connector,
            actor=user,
            expected_revision=body.expected_revision,
            api_key=body.api_key,
        )
        finalize_request(reservation, result, connector_id=connector_id)
        _commit_or_safe_error(db)
        return result
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.post("/api/global-connectors/{connector_id}/check")
async def global_connector_check(
    connector_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        require_global_operator(user)
        connector = global_connector(db, connector_id=connector_id)
        _require_browser_origin(request)
        api_key = connector_api_key(db, connector)
        status = await check_search_api_key(connector.kind, api_key)
        provider = "Brave Search" if connector.kind == "brave" else "Tavily Search"
        message = {
            "valid": f"{provider} accepted this API key.",
            "invalid": f"{provider} rejected this API key.",
            "unavailable": f"{provider} could not verify this key right now. Try again later.",
        }[status]
        return {
            "status": status,
            "message": message,
            "revision": connector.current_revision,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise _translate_error(exc) from None


@router.get("/api/global-connectors/requests/{request_id}")
def global_connector_request_status(
    request_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        require_global_operator(user)
        return request_status(
            db,
            actor_user_id=user.id,
            visibility="global",
            project_id=None,
            request_id=request_id,
        )
    except Exception as exc:
        raise _translate_error(exc) from None
