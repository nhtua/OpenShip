"""Authenticated task decision commands."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...auth.models import User
from ...auth.routes import require_jwt
from ...database.session import get_db
from .contracts import TaskDecisionCommand
from .decisions import apply_decision
from .projections import TaskProgressProjection

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("/{run_id}/task-decisions", response_model=TaskProgressProjection)
def decide_task(
    run_id: uuid.UUID,
    command: TaskDecisionCommand,
    db: Session = Depends(get_db),
    user: User = Depends(require_jwt),
):
    try:
        result = apply_decision(
            db,
            principal_id=user.id,
            run_id=run_id,
            command=command,
            now=datetime.now(UTC),
        )
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "decision_conflict",
                    "message": "Decision unavailable",
                }
            },
        ) from exc
    except Exception:
        db.rollback()
        raise
