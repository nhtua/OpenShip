"""Own-account agent-mode preference with optimistic revision checks."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from .models import User
from .schemas import AgentPreferences, AgentPreferencesUpdate


class AgentPreferenceConflict(RuntimeError):
    """The caller's expected revision is no longer current."""

    def __init__(self, current: AgentPreferences) -> None:
        self.current = current
        super().__init__("the agent mode preference changed")


class AgentPreferenceNotFound(LookupError):
    """The authenticated account no longer exists."""


def _project(user: User) -> AgentPreferences:
    return AgentPreferences(
        agent_mode=user.agent_mode,
        revision=user.agent_mode_revision,
    )


def read_preferences(db: Session, user_id: uuid.UUID) -> AgentPreferences:
    """Read only the authenticated account's preference."""
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None:
        raise AgentPreferenceNotFound("account not found")
    return _project(user)


def update_preferences(
    db: Session,
    user_id: uuid.UUID,
    command: AgentPreferencesUpdate,
) -> AgentPreferences:
    """Compare-and-swap one preference write under the account's row lock.

    The caller owns the transaction, so the lock remains held until the
    request commits. A concurrent writer that used the same expected revision
    waits for this lock and then observes the advanced revision.
    """
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if user is None:
        raise AgentPreferenceNotFound("account not found")

    current = _project(user)
    if command.expected_revision != current.revision:
        raise AgentPreferenceConflict(current)
    if command.agent_mode == current.agent_mode:
        return current

    user.agent_mode = command.agent_mode
    user.agent_mode_revision = current.revision + 1
    db.flush()
    return _project(user)
