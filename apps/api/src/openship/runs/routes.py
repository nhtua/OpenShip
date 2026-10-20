# ruff: noqa: BLE001
# A request that cannot be served becomes a 500 with a code the client can
# act on; which exception reached here is part of the message, not the answer.
"""Routes for durable run commands."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..capabilities.contracts import ToolCommandRequest
from ..database.session import get_db
from ..execution.reads import OperationPage, read_operation_page
from .models import ProviderAttempt
from .schemas import (
    RunResponse,
    TurnSubmitRequest,
    project_run_response,
)
from .service import get_run, request_cancel, submit_tool_run, submit_turn

logger = logging.getLogger("runs.routes")
router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("/{run_id}", response_model=RunResponse)
async def get_run_status(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Get the status of a run."""
    try:
        run = get_run(
            db=db,
            user_id=user.id,
            run_id=run_id,
        )
        attempts = db.query(ProviderAttempt).filter(
            ProviderAttempt.run_id == run.id
        ).order_by(ProviderAttempt.ordinal).all()
        operations = read_operation_page(db, run.id)
        return project_run_response(run, attempts, operations)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "get_run_failed", "message": str(e)}},
        )


@router.get("/{run_id}/operations", response_model=OperationPage)
async def get_run_operations(
    run_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Read one bounded page of operations for an owned run."""
    run = get_run(db=db, user_id=user.id, run_id=run_id)
    return read_operation_page(db, run.id, limit=limit, cursor=cursor)


@router.post("/{conversation_id}/turns", status_code=202, response_model=RunResponse)
async def submit_chat_turn(
    conversation_id: str,
    req: TurnSubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Submit a chat turn as a durable, queued run."""
    logger.info(f"submit_chat_turn called: conversation={conversation_id}, user={user.id}, content={req.content[:50]}...")
    try:
        run = submit_turn(
            db=db,
            user_id=user.id,
            conversation_id=conversation_id,
            content=req.content,
            client_request_id=req.client_request_id,
            provider_profile_id=req.provider_profile_id,
            fallback_profile_ids=req.fallback_profile_ids,
        )
        logger.info(f"submit_chat_turn success: run={run.id}")
        return project_run_response(run)
    except HTTPException as e:
        logger.warning(f"submit_chat_turn HTTPException: {e.status_code} - {e.detail}")
        raise
    except Exception as e:
        logger.exception("submit_chat_turn failed")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "submit_failed", "message": str(e)}},
        )


@router.post("/{conversation_id}/tools", status_code=202, response_model=RunResponse)
async def submit_tool_command(
    conversation_id: str,
    req: ToolCommandRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Queue a model-free maintained tool command."""
    try:
        run = submit_tool_run(
            db=db,
            user_id=user.id,
            conversation_id=conversation_id,
            command=req,
        )
        db.commit()
        db.refresh(run)
        return project_run_response(run)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as conflict:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "run_admission_conflict",
                    "message": "Another run or command with this request ID was admitted concurrently",
                }
            },
        ) from conflict
    except Exception as exc:
        db.rollback()
        logger.exception("submit_tool_command failed")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "submit_tool_failed", "message": "Tool command could not be submitted"}},
        ) from exc


@router.post("/{run_id}/cancel", status_code=200, response_model=RunResponse)
async def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    """Request cancellation of a run."""
    try:
        run = request_cancel(
            db=db,
            user_id=user.id,
            run_id=run_id,
        )
        return project_run_response(run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "cancel_failed", "message": str(e)}},
        )
