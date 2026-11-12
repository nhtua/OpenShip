"""Validated requests and responses for conversation message admission."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, field_validator

from ..schemas import TurnSubmitRequest


class MessageAdmissionRequest(TurnSubmitRequest):
    @field_validator("content")
    @classmethod
    def _nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class MessageAdmissionResponse(BaseModel):
    message_id: UUID
    disposition: Literal["clarification", "queued"]
    status: Literal["attached", "queued", "promoted", "failed"]
    run_id: UUID | None = None
    request_id: UUID | None = None
