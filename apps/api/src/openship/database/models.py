# Import all models so Alembic's autogenerate can discover them.
# Order matters for FK resolution: users → projects → conversations → runs → others
from ..artifacts.models import Artifact, ArtifactQuota
from ..auth.models import Base, User
from ..chat.models import Conversation, Message
from ..execution.models import (
    ExecutionSlot,
    ToolAttempt,
    ToolAvailability,
    ToolOperation,
)
from ..runs.models import (
    Event,
    Outbox,
    ProviderAttempt,
    Run,
    RunJob,
    RunTranscriptRecord,
)
from ..workspace.models import ConversationWorkspace, Project

__all__ = [
    "Artifact",
    "ArtifactQuota",
    "Base",
    "Conversation",
    "ConversationWorkspace",
    "Event",
    "ExecutionSlot",
    "Message",
    "Outbox",
    "Project",
    "ProviderAttempt",
    "Run",
    "RunJob",
    "RunTranscriptRecord",
    "ToolAttempt",
    "ToolAvailability",
    "ToolOperation",
    "User",
]
