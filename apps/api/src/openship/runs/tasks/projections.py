"""Read projections for durable task state.

The public ``TaskProgress`` contract remains the authoritative shape consumed
by routing and later graph nodes. This module adds a per-step view so callers
can read attempts grouped by the stable logical step identity without
re-parsing the plan JSON.

``TaskSummary`` is the browser's view of the same records: the plan under
review, what each step has durably done, and the one decision that is open.
It carries no operation identity, no call bindings, no attempt digests and no
private continuation - those belong to the ledgers the task indexes, and a
reader follows the ledgers through their own reads, never through this view.
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ...artifacts.contracts import ArtifactRef
from ...artifacts.models import Artifact
from ...capabilities.contracts import SHA256_PATTERN, FrozenModel
from .contracts import RecoveryAction, TaskAttempt, TaskProgress, TaskRecoveryReport
from .models import (
    TaskDecisionRecord,
    TaskPlanRecord,
    TaskRecord,
    TaskStepAttempt,
)


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


# --- Browser-facing public projection --------------------------------------
#
# One shape for every public surface: the GET run response, the conversation
# snapshot, and the ``task.*`` presentation events. Every field states a fact
# the records already hold.

_TASK_STATUSES = Literal[
    "unknown",
    "planned",
    "running",
    "waiting",
    "verification_pending",
    "completed",
    "blocked",
    "failed",
    "cancelled",
]

_STEP_STATUSES = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "interrupted",
    "uncertain",
    "verified",
    "skipped",
]

_DISPOSITIONS = Literal[
    "none",
    "known_failure",
    "material_revision",
    "uncertain",
    "denied",
    "policy_revoked",
    "budget_exhausted",
]


class PublicView(BaseModel):
    """A shape that travels as JSON, so its payload must also validate.

    Durable contracts stay strict; a public view accepts what its own dump
    carries back, which is what the event stream and the snapshot both do.
    """

    model_config = ConfigDict(extra="forbid")


class TaskPlanStepSummary(PublicView):
    """One reviewed plan step as the browser sees it."""

    id: str
    intent: str
    depends_on: tuple[str, ...] = ()


class TaskPlanSummary(PublicView):
    """The reviewed plan revision under work and the digest that review made.

    ``review_digest`` is the digest a plan confirmation echoes back: the same
    one the decision lane requires, so the browser binds to the review it saw
    rather than to a copy it made up.
    """

    revision: int = Field(ge=1)
    review_digest: str = Field(pattern=SHA256_PATTERN)
    steps: tuple[TaskPlanStepSummary, ...] = Field(min_length=1, max_length=8)


class TaskArtifactLink(PublicView):
    """An artifact a verified step published, named for the step card."""

    id: UUID
    name: str | None = None
    media_type: str
    status: Literal["ready", "unavailable", "deleting", "deleted"]


class TaskStepSummary(PublicView):
    """What one logical step has durably done, without operation identity."""

    id: str
    status: _STEP_STATUSES = "pending"
    attempt_count: int = Field(default=0, ge=0, le=3)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)
    outputs: tuple[TaskArtifactLink, ...] = Field(default=(), max_length=16)


class TaskRecoverySummary(PublicView):
    """The diagnosis behind an open recovery request.

    Eligibility and blockers come from the records, so the browser can state
    why a choice is unavailable; the digest is the diagnosis's own identity,
    which is what a recovery command must echo.
    """

    plan_revision: int = Field(ge=1)
    plan_digest: str = Field(pattern=SHA256_PATTERN)
    step_id: str
    attempt: int = Field(ge=1, le=3)
    explanation: str
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)
    unknowns: tuple[str, ...] = Field(default=(), max_length=16)
    risks: tuple[str, ...] = Field(default=(), max_length=16)
    blockers: tuple[str, ...] = Field(default=(), max_length=16)
    eligible_actions: tuple[RecoveryAction, ...] = Field(default=(), max_length=3)
    recommended_action: RecoveryAction | None = None
    digest: str = Field(pattern=SHA256_PATTERN)


class TaskPendingDecisionSummary(PublicView):
    """The one open decision this run waits on."""

    request_id: UUID
    kind: Literal[
        "plan_confirmation", "clarification", "configuration", "recovery"
    ]
    plan_revision: int = Field(ge=0)
    expected_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    prompt: str | None = Field(default=None, max_length=10_000)
    expires_at: str | None = None
    diagnosis: TaskRecoverySummary | None = None


class TaskSummary(PublicView):
    """The public task view for one durable task."""

    schema_version: Literal[1] = 1
    run_id: UUID
    mode: Literal["auto", "copilot"] = "copilot"
    revision: int = Field(default=0, ge=0)
    plan: TaskPlanSummary | None = None
    status: _TASK_STATUSES = "unknown"
    steps: tuple[TaskStepSummary, ...] = Field(default=(), max_length=8)
    skipped_steps: tuple[str, ...] = Field(default=(), max_length=8)
    verified_outputs: tuple[TaskArtifactLink, ...] = Field(default=(), max_length=64)
    last_disposition: _DISPOSITIONS = "none"
    pending_decision: TaskPendingDecisionSummary | None = None


def _json_list(text: str | None) -> list:
    try:
        parsed = json.loads(text) if text else []
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(text: str | None) -> dict:
    try:
        parsed = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _step_status(
    step_id: str,
    attempts: tuple[tuple[str, tuple[str, ...]], ...],
    verified_steps: set[str],
    skipped_steps: set[str],
) -> _STEP_STATUSES:
    """The honest status for one step: skipped is never presented as done."""
    if step_id in skipped_steps:
        return "skipped"
    if step_id in verified_steps:
        return "verified"
    statuses = {status for status, _evidence in attempts}
    for candidate in ("succeeded", "running", "interrupted", "uncertain", "failed"):
        if candidate in statuses:
            return candidate
    return "pending" if not attempts else "uncertain"


def recovery_summary(report: TaskRecoveryReport) -> TaskRecoverySummary:
    """The public half of a recovery report.

    The report itself names the operation it diagnosed; the summary does not.
    A reader follows that identity through the ledgers the task indexes, and
    this view stays free of it.
    """
    return TaskRecoverySummary(
        plan_revision=report.plan_revision,
        plan_digest=report.plan_digest,
        step_id=report.step_id,
        attempt=report.attempt,
        explanation=report.explanation,
        evidence_refs=report.evidence_refs,
        unknowns=report.unknowns,
        risks=report.risks,
        blockers=report.blockers,
        eligible_actions=report.eligible_actions,
        recommended_action=report.recommended_action,
        digest=report.digest,
    )


def read_task_summary(db, run_id: UUID) -> TaskSummary | None:
    """The browser-facing task view, from the same records as every read.

    Attempts contribute a count and their evidence, never an operation
    identity; verified outputs contribute the artifact links the step card
    shows. A pending recovery contributes its diagnosis, never a
    continuation.
    """
    task = db.query(TaskRecord).filter_by(run_id=run_id).one_or_none()
    if task is None:
        return None

    plan_row = (
        db.query(TaskPlanRecord)
        .filter_by(task_id=task.id, revision=task.current_revision)
        .one_or_none()
    )
    plan_summary = None
    plan_steps: list[dict] = []
    if plan_row is not None:
        plan = _json_object(plan_row.plan_json)
        plan_steps = list(plan.get("steps", []))
        plan_summary = TaskPlanSummary(
            revision=plan_row.revision,
            review_digest=plan_row.digest,
            steps=tuple(
                TaskPlanStepSummary(
                    id=step["id"],
                    intent=step["intent"],
                    depends_on=tuple(step.get("depends_on", [])),
                )
                for step in plan_steps
                if isinstance(step, dict) and "id" in step and "intent" in step
            ),
        )

    verified_steps = {item for item in _json_list(task.verified_steps_json)}
    skipped_steps = {item for item in _json_list(task.skipped_steps_json)}

    attempts_by_step: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for row in (
        db.query(TaskStepAttempt)
        .filter_by(task_id=task.id)
        .order_by(TaskStepAttempt.step_id, TaskStepAttempt.ordinal)
        .all()
    ):
        attempts_by_step.setdefault(row.step_id, []).append(
            (row.status, tuple(_json_list(row.evidence_json)))
        )

    step_summaries = []
    for step in plan_steps:
        if not isinstance(step, dict) or "id" not in step:
            continue
        attempts = tuple(attempts_by_step.get(step["id"], ()))
        latest = attempts[-1] if attempts else None
        step_summaries.append(
            TaskStepSummary(
                id=step["id"],
                status=_step_status(
                    step["id"], attempts, verified_steps, skipped_steps
                ),
                attempt_count=len(attempts),
                evidence_refs=latest[1] if latest else (),
                outputs=(),
            )
        )

    output_links = []
    published: list[tuple[UUID, dict]] = []
    for item in _json_list(task.verified_outputs_json):
        if not isinstance(item, dict) or "id" not in item:
            continue
        try:
            published.append((UUID(str(item["id"])), item))
        except ValueError:
            continue
    if published:
        by_id = {
            row.id: row
            for row in db.query(Artifact).filter(
                Artifact.id.in_([artifact_id for artifact_id, _item in published])
            ).all()
        }
        for artifact_id, item in published:
            row = by_id.get(artifact_id)
            output_links.append(
                TaskArtifactLink(
                    id=artifact_id,
                    name=row.display_name if row is not None else None,
                    media_type=row.media_type
                    if row is not None
                    else str(item.get("media_type", "application/octet-stream")),
                    status=row.status
                    if row is not None
                    and row.status in ("ready", "unavailable", "deleting", "deleted")
                    else "unavailable",
                )
            )

    pending = None
    decision_row = (
        db.query(TaskDecisionRecord)
        .filter_by(task_id=task.id, state="open")
        .one_or_none()
    )
    if decision_row is not None and decision_row.plan_revision == task.current_revision:
        diagnosis = None
        request = _json_object(decision_row.request_json)
        report = request.get("recovery_report")
        if report is not None:
            diagnosis = recovery_summary(
                TaskRecoveryReport.model_validate_json(json.dumps(report))
            )
        expires_at = decision_row.expires_at
        pending = TaskPendingDecisionSummary(
            request_id=decision_row.request_id,
            kind=decision_row.kind,
            plan_revision=decision_row.plan_revision,
            expected_digest=decision_row.expected_digest,
            prompt=request.get("prompt") if isinstance(request.get("prompt"), str) else None,
            expires_at=(
                (
                    expires_at.replace(tzinfo=UTC)
                    if expires_at.tzinfo is None
                    else expires_at
                ).isoformat()
                if expires_at is not None
                else None
            ),
            diagnosis=diagnosis,
        )

    return TaskSummary(
        run_id=task.run_id,
        mode=task.mode if task.mode in ("auto", "copilot") else "copilot",
        revision=task.current_revision,
        plan=plan_summary,
        status=task.status
        if task.status
        in (
            "unknown",
            "planned",
            "running",
            "waiting",
            "verification_pending",
            "completed",
            "blocked",
            "failed",
            "cancelled",
        )
        else "unknown",
        steps=tuple(step_summaries),
        skipped_steps=tuple(sorted(skipped_steps)),
        verified_outputs=tuple(output_links),
        last_disposition=task.last_disposition
        if task.last_disposition
        in (
            "none",
            "known_failure",
            "material_revision",
            "uncertain",
            "denied",
            "policy_revoked",
            "budget_exhausted",
        )
        else "none",
        pending_decision=pending,
    )
