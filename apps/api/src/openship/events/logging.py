"""Structured operational logging for OpenShip.

Provides structured log entries that include request/project/conversation/run/event
IDs and state transitions. Logs must not include access tokens, model keys,
prompts, or raw responses.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def structured_log(
    event: str,
    *,
    request_id: Optional[str] = None,
    project_id: Optional[str | uuid.UUID] = None,
    conversation_id: Optional[str | uuid.UUID] = None,
    run_id: Optional[str | uuid.UUID] = None,
    event_id: Optional[str | uuid.UUID] = None,
    status: Optional[str] = None,
    **extra: Any,
) -> dict:
    """Create a structured log entry.

    Args:
        event: The event name (e.g., "run_completed", "state_transition").
        request_id: HTTP request ID.
        project_id: Project identifier.
        conversation_id: Conversation identifier.
        run_id: Run identifier.
        event_id: Event identifier.
        status: Status value (e.g., "completed", "failed").
        **extra: Additional fields to include in the log entry.

    Returns:
        A dict representing the structured log entry.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }

    if request_id is not None:
        entry["request_id"] = request_id
    if project_id is not None:
        entry["project_id"] = str(project_id)
    if conversation_id is not None:
        entry["conversation_id"] = str(conversation_id)
    if run_id is not None:
        entry["run_id"] = str(run_id)
    if event_id is not None:
        entry["event_id"] = str(event_id)
    if status is not None:
        entry["status"] = status

    entry.update(extra)

    return entry


def log_state_transition(
    run_id: str | uuid.UUID,
    from_status: str,
    to_status: str,
    *,
    conversation_id: Optional[str | uuid.UUID] = None,
    reason: Optional[str] = None,
) -> dict:
    """Log a run state transition.

    Args:
        run_id: The run that is transitioning.
        from_status: Previous status.
        to_status: New status.
        conversation_id: Associated conversation (optional).
        reason: Reason for transition (optional).

    Returns:
        The structured log entry.
    """
    entry = structured_log(
        "state_transition",
        run_id=run_id,
        conversation_id=conversation_id,
        from_status=from_status,
        to_status=to_status,
    )
    if reason:
        entry["reason"] = reason
    return entry


def log_run_completed(
    run_id: str | uuid.UUID,
    *,
    conversation_id: Optional[str | uuid.UUID] = None,
    usage: Optional[dict] = None,
    duration_ms: Optional[int] = None,
) -> dict:
    """Log a completed run.

    Args:
        run_id: The completed run.
        conversation_id: Associated conversation (optional).
        usage: Usage metrics from the provider (optional).
        duration_ms: Run duration in milliseconds (optional).

    Returns:
        The structured log entry.
    """
    entry = structured_log(
        "run_completed",
        run_id=run_id,
        conversation_id=conversation_id,
        status="completed",
    )
    if usage:
        entry["usage"] = usage
    if duration_ms:
        entry["duration_ms"] = duration_ms
    return entry


def log_run_failed(
    run_id: str | uuid.UUID,
    error_code: str,
    *,
    conversation_id: Optional[str | uuid.UUID] = None,
    error_message: Optional[str] = None,
) -> dict:
    """Log a failed run.

    Args:
        run_id: The failed run.
        error_code: Error code identifier.
        conversation_id: Associated conversation (optional).
        error_message: Error message (optional, no secrets).

    Returns:
        The structured log entry.
    """
    entry = structured_log(
        "run_failed",
        run_id=run_id,
        conversation_id=conversation_id,
        status="failed",
        error_code=error_code,
    )
    if error_message:
        entry["error_message"] = error_message
    return entry