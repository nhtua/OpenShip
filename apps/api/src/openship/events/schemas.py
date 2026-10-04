"""Pydantic schemas for event API."""

from typing import Any, Optional
from pydantic import BaseModel, Field


class EventInput(BaseModel):
    """Input for appending an event."""
    type: str = Field(..., min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[str] = None


class EventRead(BaseModel):
    """Event as returned to client."""
    id: str
    conversation_id: str
    run_id: Optional[str]
    sequence: int
    type: str
    payload: dict[str, Any]
    created_at: str


class MessageSummary(BaseModel):
    """Summary of a chat message."""
    id: str
    role: str
    content: str
    run_id: Optional[str]
    created_at: str


class RunSummary(BaseModel):
    """Summary of a run."""
    id: str
    status: str
    attempt: int
    started_at: Optional[str]
    ended_at: Optional[str]
    error_code: Optional[str]


class SnapshotResponse(BaseModel):
    """Complete state snapshot for a conversation."""
    conversation_id: str
    sequence: int  # Max committed sequence; -1 means empty
    events: list[EventRead]
    messages: list[MessageSummary]
    runs: list[RunSummary]


class StreamResponse(BaseModel):
    """Stream of events after a sequence cursor."""
    conversation_id: str
    after_sequence: int
    events: list[EventRead]
