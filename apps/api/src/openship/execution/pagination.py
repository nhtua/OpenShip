"""Stable opaque cursors for time-ordered execution records."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime


def encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    value = f"{created_at.isoformat()}|{item_id}".encode()
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        timestamp, item_id = decoded.rsplit("|", 1)
        return datetime.fromisoformat(timestamp), uuid.UUID(item_id)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
        raise ValueError("cursor is invalid") from exc
