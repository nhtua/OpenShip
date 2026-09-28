from pydantic import BaseModel, Field
from typing import Dict, Any

class BuilderState(BaseModel):
    workflow_path: str
    workflow_content: str
    llm_plan: str = ""
    user_feedback: str = ""
    approved: bool = False

class ExecutorState(BaseModel):
    plan: str
    graph: Any = None
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    thread_id: str