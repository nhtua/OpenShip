"""Routes for durable run commands."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth.models import User
from ..auth.routes import require_jwt
from ..database.session import get_db
from .schemas import RunResponse, RunCancelRequest, TurnSubmitRequest
from .service import request_cancel, submit_turn, get_run

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
        return run
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "get_run_failed", "message": str(e)}},
        )


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
        )
        logger.info(f"submit_chat_turn success: run={run.id}")
        return run
    except HTTPException as e:
        logger.warning(f"submit_chat_turn HTTPException: {e.status_code} - {e.detail}")
        raise
    except Exception as e:
        logger.exception(f"submit_chat_turn failed with exception: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "submit_failed", "message": str(e)}},
        )


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
        return run
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "cancel_failed", "message": str(e)}},
        )
