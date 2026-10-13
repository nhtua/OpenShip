# Import all models so Alembic's autogenerate can discover them.
# Order matters for FK resolution: users → projects → conversations → runs → others
from ..auth.models import Base, User  # noqa: F401
from ..workspace.models import Project  # noqa: F401
from ..chat.models import Conversation, Message  # noqa: F401
from ..runs.models import Event, Outbox, ProviderAttempt, Run, RunJob  # noqa: F401

__all__ = [
    "Base",
    "User",
    "Conversation",
    "Message",
    "Project",
    "Run",
    "RunJob",
    "Event",
    "Outbox",
    "ProviderAttempt",
]
