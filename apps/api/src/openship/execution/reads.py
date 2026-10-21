"""Owned API read projections for durable tool operations."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .models import ToolOperation
from .pagination import decode_cursor, encode_cursor
from .projections import OperationSummary, project_operation


class OperationPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[OperationSummary, ...]
    next_cursor: str | None


def read_operation_page(
    db: Session,
    run_id: UUID,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> OperationPage:
    if not 1 <= limit <= 100:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_operation_limit", "message": "Operation page size must be between 1 and 100"}},
        )

    query = db.query(ToolOperation).filter(ToolOperation.run_id == run_id)
    if cursor:
        try:
            timestamp, operation_id = decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "invalid_operation_cursor", "message": "Operation cursor is invalid"}},
            ) from exc
        query = query.filter(
            or_(
                ToolOperation.created_at > timestamp,
                and_(ToolOperation.created_at == timestamp, ToolOperation.id > operation_id),
            )
        )

    rows = query.order_by(ToolOperation.created_at.asc(), ToolOperation.id.asc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return OperationPage(
        items=tuple(project_operation(db, operation) for operation in rows),
        next_cursor=encode_cursor(rows[-1].created_at, rows[-1].id) if more and rows else None,
    )
