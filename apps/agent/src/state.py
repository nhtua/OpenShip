from typing import Any

from pydantic import BaseModel, Field


class BuilderState(BaseModel):
    workflow_path: str = ""
    workflow_content: str = ""
    llm_plan: str = ""
    approved: bool = False
    user_feedback: str = ""
    standardized_workflow: str = ""
    update_source: bool = False

class ExecutorState(BaseModel):
    plan: str
    graph: Any = None
    checkpointer: Any = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    thread_id: str