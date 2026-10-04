from pydantic import BaseModel, Field


class MessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True
