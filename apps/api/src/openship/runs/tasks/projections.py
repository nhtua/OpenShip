"""Read projections for durable task state.

The public ``TaskProgress`` contract remains the authoritative shape consumed
by routing and later graph nodes. This module adds a per-step view so callers
can read attempts grouped by the stable logical step identity without
re-parsing the plan JSON.
"""

from __future__ import annotations

from typing import Literal

from ...artifacts.contracts import ArtifactRef
from ...capabilities.contracts import FrozenModel
from .contracts import TaskAttempt, TaskProgress


class TaskStepProgress(FrozenModel):
    """Projected status and attempts for one logical plan step."""

    schema_version: Literal[1] = 1
    id: str
    intent: str
    depends_on: tuple[str, ...] = ()
    status: Literal[
        "pending",
        "running",
        "succeeded",
        "failed",
        "interrupted",
        "uncertain",
        "verified",
    ] = "pending"
    attempts: tuple[TaskAttempt, ...] = ()
    verified_outputs: tuple[ArtifactRef, ...] = ()


class TaskProgressProjection(TaskProgress):
    """``TaskProgress`` plus the per-step grouping readers need."""

    steps: tuple[TaskStepProgress, ...] = ()
