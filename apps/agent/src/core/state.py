"""Workflow state management for LangGraph orchestration.

Defines the shared state schema for workflow execution, including inputs,
outputs, step results, and error tracking.
"""

from typing import Any

from pydantic import BaseModel, Field


class WorkflowState(BaseModel):
    """State for a workflow execution.

    Tracks inputs, per-step outputs, errors, and execution status.
    Used as the state schema for LangGraph StateGraph.
    """

    # Execution inputs provided by caller
    inputs: dict[str, Any] = Field(default_factory=dict)

    # Per-step outputs: {step_order: output}
    step_outputs: dict[int, Any] = Field(default_factory=dict)

    # Final aggregated output
    output: dict[str, Any] = Field(default_factory=dict)

    # Error tracking
    error: str | None = Field(default=None)

    # Execution status
    status: str = Field(default="running")
