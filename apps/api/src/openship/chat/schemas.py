from pydantic import BaseModel


class ConversationResponse(BaseModel):
    id: str
    project_id: str
    title: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True
