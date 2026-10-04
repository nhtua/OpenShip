import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_serializer


class TurnSubmitRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    client_request_id: str = Field(..., description="UUID generated once per browser send, for idempotency")


class RunResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    created_at: datetime
    attempt: int = 0

    @field_serializer('id')
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer('conversation_id')
    def serialize_conversation_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()


class RunCancelRequest(BaseModel):
    pass
