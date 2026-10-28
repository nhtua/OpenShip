# Import all models so Alembic's autogenerate can discover them.
# Order matters for FK resolution: users → projects → conversations → runs → others
from ..artifacts.models import Artifact, ArtifactQuota
from ..auth.models import Base, User
from ..capabilities.models import (
    ToolPolicyAuditEvent,
    ToolPolicyOverride,
    ToolPolicyState,
)
from ..chat.models import Conversation, Message
from ..connectors.models import (
    Connector,
    ConnectorAuditEvent,
    ConnectorAvailability,
    ConnectorRequestLedger,
    ConnectorRevision,
    CredentialVersion,
    ProjectConnectorDefault,
)
from ..execution.models import (
    ExecutionSlot,
    ToolAttempt,
    ToolAvailability,
    ToolOperation,
)
from ..messaging.models import (
    MessageDisposition,
    MessageExhaustion,
    MessageFailure,
    MessageFailureConflict,
    MessageTransportState,
    MessagingCutover,
    RetentionTask,
)
from ..resources.models import (
    ConnectorBlocker,
    Environment,
    ResourceBinding,
    ResourceObject,
    ResourceObservation,
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
    "Connector",
    "ConnectorAuditEvent",
    "ConnectorAvailability",
    "ConnectorBlocker",
    "ConnectorRequestLedger",
    "ConnectorRevision",
    "Conversation",
    "ConversationWorkspace",
    "CredentialVersion",
    "Environment",
    "Event",
    "ExecutionSlot",
    "Message",
    "MessageDisposition",
    "MessageExhaustion",
    "MessageFailure",
    "MessageFailureConflict",
    "MessageTransportState",
    "MessagingCutover",
    "Outbox",
    "Project",
    "ProjectConnectorDefault",
    "ProviderAttempt",
    "ResourceBinding",
    "ResourceObject",
    "ResourceObservation",
    "RetentionTask",
    "Run",
    "RunJob",
    "RunTranscriptRecord",
    "ToolAttempt",
    "ToolAvailability",
    "ToolOperation",
    "ToolPolicyAuditEvent",
    "ToolPolicyOverride",
    "ToolPolicyState",
    "User",
]
