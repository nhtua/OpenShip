"""Secret-safe access to operator-declared credential bindings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


def credential_value(name: str) -> str | None:
    """Resolve an arbitrary binding from exported variables or the dotenv file.

    Process environment takes precedence. Values are returned only to the
    caller that constructs an authenticated client; callers that report
    availability use :func:`credential_binding_present` instead.
    """
    if not name:
        return None
    value = os.environ.get(name)
    if value:
        return value
    value = dotenv_values(Path.cwd() / ".env").get(name)
    return value if isinstance(value, str) and value else None


def credential_binding_present(name: str) -> bool:
    """Check presence without returning or reporting a credential value."""
    return credential_value(name) is not None
