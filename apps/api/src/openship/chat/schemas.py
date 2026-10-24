from pydantic import BaseModel

from ..workspace.lifecycle import format_archive_timestamp
from ..workspace.models import Project
from .models import Conversation


class ConversationResponse(BaseModel):
    id: str
    project_id: str
    project_name: str
    project_archived_at: str | None
    title: str
    created_at: str
    updated_at: str
    archived_at: str | None

    class Config:
        from_attributes = True


def conversation_response(
    conversation: Conversation, project: Project
) -> ConversationResponse:
    return ConversationResponse(
        id=str(conversation.id),
        project_id=str(conversation.project_id),
        project_name=project.name,
        project_archived_at=format_archive_timestamp(project.archived_at),
        title=conversation.title,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        archived_at=format_archive_timestamp(conversation.archived_at),
    )
