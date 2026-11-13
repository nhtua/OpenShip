"""Strict, versioned contracts for bounded agentic task execution.

These contracts describe the public task state: context, plan, assessment,
action, progress, decisions and verification. Model-authored drafts remain
proposals until the server validates them against the pinned catalog.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ...artifacts.contracts import ArtifactRef
from ...capabilities.contracts import (
    SHA256_PATTERN,
    CapabilityRef,
    ConnectorRef,
    ExecutionLimits,
    FrozenModel,
    LoopLimits,
    canonical_digest,
)
from ...sandboxes.profile_schema import (
    ProfileCatalog,
    ProfileId,
    catalog_digest,
)

# A step identity is written by a model and validated by this pattern, so the
# words the stage prompt gives the model come from here too: one source, no
# prompt that asks for an id the contract refuses. Hyphens are admitted because
# that is the shape models write ("find-data-source"); ids written under the
# older grammar stay valid.
STEP_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
STEP_ID_GRAMMAR = (
    "one lowercase letter, then lowercase letters, digits, underscores or "
    "hyphens, 64 characters at most"
)
_EVIDENCE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_CONTROL_CHARACTERS = frozenset(
    character for character in map(chr, range(32)) if character not in "\n\t"
) | {"\x7f"}


def _public_text(value: str, limit: int) -> str:
    if not value or not value.strip():
        raise ValueError("public text must not be empty")
    if value != value.strip():
        raise ValueError("public text must be trimmed")
    if any(character in _CONTROL_CHARACTERS for character in value):
        raise ValueError("public text contains a control character")
    if len(value.encode("utf-8")) > limit:
        raise ValueError("public text exceeds the UTF-8 byte limit")
    return value


def _evidence_ref(value: str) -> str:
    if ".." in value or not _EVIDENCE_REF.fullmatch(value):
        raise ValueError("invalid or unsafe evidence reference")
    return value


def _step_id(value: str) -> str:
    if not re.fullmatch(STEP_ID_PATTERN, value):
        raise ValueError("invalid step identifier")
    return value


def _output_name(value: str) -> str:
    _public_text(value, 255)
    if (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(character in _CONTROL_CHARACTERS for character in value)
    ):
        raise ValueError("invalid expected output name")
    return value


class TaskLimits(FrozenModel):
    """P1-P3 defaults double as upper bounds for operator ceilings."""

    schema_version: Literal[1] = 1
    max_steps: int = Field(default=8, ge=1, le=8)
    max_tool_calls: int = Field(default=16, ge=0, le=16)
    max_model_turns: int = Field(default=20, ge=1, le=20)
    max_generation_attempts: int = Field(default=24, ge=1, le=24)
    max_output_tokens_per_request: int = Field(default=4000, ge=1, le=4000)
    max_total_output_tokens: int = Field(default=48000, ge=1, le=48000)
    max_repairs_per_step: int = Field(default=2, ge=0, le=2)
    decision_expiry_hours: int = Field(default=24, ge=1, le=24)


class TaskContext(FrozenModel):
    schema_version: Literal[1] = 1
    root_run_id: UUID
    project_id: UUID
    conversation_id: UUID
    initiator_id: UUID
    scope: Literal["conversation", "project"]
    mode: Literal["auto", "copilot"]
    graph_version: int = Field(ge=1)
    original_intent: str
    constraints: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("original_intent")
    @classmethod
    def _intent(cls, value: str) -> str:
        return _public_text(value, 10_000)

    @field_validator("constraints")
    @classmethod
    def _constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _public_text(item, 512)
        if len(set(value)) != len(value):
            raise ValueError("constraints must be unique")
        return value


class TaskStep(FrozenModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=STEP_ID_PATTERN)
    intent: str
    depends_on: tuple[str, ...] = Field(default=(), max_length=8)
    capability: CapabilityRef | None = None
    profile_id: ProfileId | None = None
    expected_outputs: tuple[str, ...] = Field(default=(), max_length=8)
    completion_checks: tuple[str, ...] = Field(min_length=1, max_length=8)

    @property
    def is_result_only(self) -> bool:
        """A result-only step commits its own result but publishes no new artifact.

        Read-only local steps (for example reading a saved conversation file) fall
        in this category: the committed operation's result is the durable evidence,
        not a newly published artifact. Producer steps must still declare at least
        one expected output so their artifact side effects are verified.
        """
        return not self.expected_outputs

    @field_validator("intent")
    @classmethod
    def _intent(cls, value: str) -> str:
        return _public_text(value, 2000)

    @field_validator("depends_on")
    @classmethod
    def _depends_on(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _step_id(item)
        if len(set(value)) != len(value):
            raise ValueError("dependencies must be unique")
        return value

    @field_validator("expected_outputs")
    @classmethod
    def _expected_outputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _output_name(item)
        if len(set(value)) != len(value):
            raise ValueError("expected outputs must be unique")
        return value

    @field_validator("completion_checks")
    @classmethod
    def _completion_checks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _public_text(item, 512)
        if len(set(value)) != len(value):
            raise ValueError("completion checks must be unique")
        return value


class TaskPlan(FrozenModel):
    """A draft plan plus the server-supplied pinned reference sets.

    ``available_capabilities`` and ``available_profiles`` must come from the
    server's pinned execution snapshot. A model-authored plan is only a
    proposal until the server rebuilds it with those sets and validates it.
    """

    schema_version: Literal[1] = 1
    revision: int = Field(ge=1)
    intent: str
    constraints: tuple[str, ...] = Field(default=(), max_length=16)
    steps: tuple[TaskStep, ...] = Field(min_length=1, max_length=8)
    available_capabilities: tuple[CapabilityRef, ...] = Field(default=(), max_length=32)
    available_profiles: tuple[ProfileId, ...] = Field(default=(), max_length=4)
    status: Literal["proposed", "validated"] = "proposed"

    @field_validator("intent")
    @classmethod
    def _intent(cls, value: str) -> str:
        return _public_text(value, 2000)

    @field_validator("constraints")
    @classmethod
    def _constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _public_text(item, 512)
        if len(set(value)) != len(value):
            raise ValueError("constraints must be unique")
        return value

    @field_validator("available_capabilities")
    @classmethod
    def _available_capabilities(
        cls, value: tuple[CapabilityRef, ...]
    ) -> tuple[CapabilityRef, ...]:
        identities = {(item.id, item.version, item.manifest_digest) for item in value}
        if len(identities) != len(value):
            raise ValueError("available capabilities must be unique")
        return value

    @field_validator("available_profiles")
    @classmethod
    def _available_profiles(cls, value: tuple[ProfileId, ...]) -> tuple[ProfileId, ...]:
        if len(set(value)) != len(value):
            raise ValueError("available profiles must be unique")
        return value

    @model_validator(mode="after")
    def _validated_plan(self) -> TaskPlan:
        capability_identities = {
            (item.id, item.version, item.manifest_digest)
            for item in self.available_capabilities
        }
        seen: set[str] = set()
        for item in self.steps:
            if item.id in seen:
                raise ValueError("step identifiers must be unique")
            for dependency in item.depends_on:
                if dependency not in seen:
                    raise ValueError("dependencies must point only to earlier steps")
            if item.capability is not None:
                identity = (
                    item.capability.id,
                    item.capability.version,
                    item.capability.manifest_digest,
                )
                if identity not in capability_identities:
                    raise ValueError("step references an unavailable capability")
            if item.profile_id is not None:
                if item.capability is None:
                    raise ValueError("a profile choice requires a capability")
                if item.profile_id not in self.available_profiles:
                    raise ValueError("step references an unavailable profile")
            seen.add(item.id)
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json", exclude={"status"}))

    def as_validated(self) -> TaskPlan:
        """Return the server-validated view of a structurally valid draft."""
        return self.model_copy(update={"status": "validated"})


class TaskAction(FrozenModel):
    schema_version: Literal[1] = 1
    kind: Literal[
        "assess",
        "execute",
        "repair",
        "revise",
        "wait",
        "verify",
        "finish",
        "stop",
    ]
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    wait_kind: Literal["decision", "configuration"] | None = None
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _public_text(value, 512)

    @model_validator(mode="after")
    def _consistent_action(self) -> TaskAction:
        step_kinds = {"execute", "repair", "verify"}
        if self.kind == "wait":
            if self.wait_kind is None:
                raise ValueError(
                    "wait actions specify a decision or configuration kind"
                )
            if self.step_id is not None:
                raise ValueError("wait actions do not target a step")
            return self
        if self.wait_kind is not None:
            raise ValueError("only wait actions specify a wait kind")
        if self.kind in step_kinds and self.step_id is None:
            raise ValueError("this action requires a step identifier")
        if self.kind not in step_kinds and self.step_id is not None:
            raise ValueError("this action does not target a step")
        return self


RecoveryAction = Literal["abort", "repeat_step", "continue_next"]


class TaskAssessment(FrozenModel):
    schema_version: Literal[1] = 1
    classification: Literal["informational", "clarification", "task", "blocked"]
    explanation: str
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)
    missing_information: tuple[str, ...] = Field(default=(), max_length=8)
    failure: Literal[
        "none", "known", "material", "uncertain", "denied", "policy", "budget"
    ] = "none"
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    proposed_action: TaskAction | None = None
    unknowns: tuple[str, ...] = Field(default=(), max_length=16)
    risks: tuple[str, ...] = Field(default=(), max_length=16)
    recommended_recovery_action: RecoveryAction | None = None

    @field_validator("explanation")
    @classmethod
    def _explanation(cls, value: str) -> str:
        return _public_text(value, 2000)

    @field_validator("unknowns", "risks")
    @classmethod
    def _diagnosis_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _public_text(item, 512)
        return value

    @field_validator("evidence_refs")
    @classmethod
    def _evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _evidence_ref(item)
        if len(set(value)) != len(value):
            raise ValueError("evidence references must be unique")
        return value

    @field_validator("missing_information")
    @classmethod
    def _missing_information(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _public_text(item, 512)
        if len(set(value)) != len(value):
            raise ValueError("missing information must be unique")
        return value

    @model_validator(mode="after")
    def _consistent_assessment(self) -> TaskAssessment:
        if self.classification == "clarification" and not self.missing_information:
            raise ValueError("clarification requires missing information")
        if self.classification == "blocked" and not (
            self.missing_information or self.evidence_refs
        ):
            raise ValueError(
                "blocked assessments require evidence or missing information"
            )
        if self.failure in {"known", "material"} and self.step_id is None:
            raise ValueError("known or material failures require a step identifier")
        if self.failure == "none" and self.step_id is not None:
            raise ValueError("a step identifier requires a failure category")
        if self.recommended_recovery_action is not None and self.failure != "uncertain":
            raise ValueError(
                "only an uncertain failure carries a recovery recommendation"
            )
        return self


class TaskAttempt(FrozenModel):
    schema_version: Literal[1] = 1
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    attempt: int = Field(ge=1, le=3)
    status: Literal["running", "succeeded", "failed", "interrupted", "uncertain"]
    operation_id: UUID | None = None
    input_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    result_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("evidence_refs")
    @classmethod
    def _evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _evidence_ref(item)
        if len(set(value)) != len(value):
            raise ValueError("evidence references must be unique")
        return value

    @model_validator(mode="after")
    def _committed_success_has_identity(self) -> TaskAttempt:
        if self.status == "succeeded" and self.operation_id is None:
            raise ValueError("a succeeded attempt requires an operation identity")
        return self


class TaskRecoveryReport(FrozenModel):
    schema_version: Literal[1] = 1
    plan_revision: int = Field(ge=1)
    plan_digest: str = Field(pattern=SHA256_PATTERN)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    attempt: int = Field(ge=1, le=3)
    operation_id: UUID | None = None
    explanation: str
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)
    unknowns: tuple[str, ...] = Field(default=(), max_length=16)
    risks: tuple[str, ...] = Field(default=(), max_length=16)
    recommended_action: RecoveryAction | None = None
    eligible_actions: tuple[RecoveryAction, ...] = Field(default=(), max_length=16)
    blockers: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("explanation")
    @classmethod
    def _explanation(cls, value: str) -> str:
        return _public_text(value, 2048)

    @field_validator("evidence_refs", "unknowns", "risks", "blockers")
    @classmethod
    def _items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _public_text(item, 512)
        return value

    @model_validator(mode="after")
    def _size(self) -> TaskRecoveryReport:
        if (
            len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode())
            > 32768
        ):
            raise ValueError("recovery report exceeds byte limit")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class TaskCallBinding(FrozenModel):
    schema_version: Literal[1] = 1
    plan_revision: int = Field(ge=1)
    step_id: str = Field(pattern=STEP_ID_PATTERN)
    attempt: int = Field(ge=1, le=3)
    logical_call_id: str = Field(pattern=r"^model:[0-9]+:call:[0-9]+$")


class ClarificationAssessment(FrozenModel):
    """Whether accumulated replies answer the open clarification.

    A model may judge sufficiency and phrase a follow-up, but only recorded
    replies and the durable request decide whether anything is resolved.
    """

    verdict: Literal["sufficient", "insufficient"]
    follow_up: str | None = None

    @model_validator(mode="after")
    def _follow_up_matches_verdict(self) -> ClarificationAssessment:
        if self.verdict == "insufficient":
            if self.follow_up is None:
                raise ValueError("an insufficient assessment requires a follow-up")
            _public_text(self.follow_up, 10_000)
        elif self.follow_up is not None:
            raise ValueError("a sufficient assessment carries no follow-up")
        return self


class TaskDecision(FrozenModel):
    schema_version: Literal[1] = 1
    request_id: UUID
    kind: Literal["plan_confirmation", "clarification", "configuration", "recovery"]
    plan_revision: int = Field(ge=0)
    expected_digest: str = Field(pattern=SHA256_PATTERN)
    expires_at: datetime
    recovery_report: TaskRecoveryReport | None = None

    @model_validator(mode="after")
    def _confirmation_requires_plan(self) -> TaskDecision:
        if self.kind == "plan_confirmation" and self.plan_revision == 0:
            raise ValueError("plan confirmation requires a plan revision")
        if self.kind == "recovery":
            if (
                self.recovery_report is None
                or self.plan_revision < 1
                or self.recovery_report.plan_revision != self.plan_revision
                or self.expected_digest != self.recovery_report.digest
            ):
                raise ValueError("recovery requires a matching report and revision")
        elif self.recovery_report is not None:
            raise ValueError("only recovery decisions carry a recovery report")
        return self


class TaskCounters(FrozenModel):
    schema_version: Literal[1] = 1
    tool_calls: int = Field(default=0, ge=0)
    model_turns: int = Field(default=0, ge=0)
    generation_attempts: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    repairs_by_step: dict[str, int] = Field(default_factory=dict, max_length=8)

    @field_validator("repairs_by_step")
    @classmethod
    def _repairs_by_step(cls, value: dict[str, int]) -> dict[str, int]:
        for step_id, attempts in value.items():
            _step_id(step_id)
            if not 0 <= attempts <= 2:
                raise ValueError("repairs per step exceed the task ceiling")
        return value


class VerificationCheck(FrozenModel):
    schema_version: Literal[1] = 1
    name: str
    passed: bool
    evidence_ref: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _public_text(value, 256)

    @field_validator("evidence_ref")
    @classmethod
    def _evidence(cls, value: str | None) -> str | None:
        return None if value is None else _evidence_ref(value)

    @model_validator(mode="after")
    def _passed_check_has_evidence(self) -> VerificationCheck:
        if self.passed and self.evidence_ref is None:
            raise ValueError("a passing check requires evidence")
        return self


class VerificationReport(FrozenModel):
    schema_version: Literal[1] = 1
    step_id: str | None = Field(default=None, pattern=STEP_ID_PATTERN)
    passed: bool
    checks: tuple[VerificationCheck, ...] = Field(min_length=1, max_length=16)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)
    missing: tuple[str, ...] = Field(default=(), max_length=16)
    explanation: str

    @field_validator("evidence_refs")
    @classmethod
    def _evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _evidence_ref(item)
        if len(set(value)) != len(value):
            raise ValueError("evidence references must be unique")
        return value

    @field_validator("missing")
    @classmethod
    def _missing(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _public_text(item, 512)
        if len(set(value)) != len(value):
            raise ValueError("missing outputs must be unique")
        return value

    @field_validator("explanation")
    @classmethod
    def _explanation(cls, value: str) -> str:
        return _public_text(value, 2000)

    @model_validator(mode="after")
    def _verification_is_evidence_backed(self) -> VerificationReport:
        if self.passed:
            if not self.evidence_refs:
                raise ValueError("passing verification requires evidence references")
            if self.missing:
                raise ValueError("passing verification cannot report missing outputs")
            if not all(check.passed for check in self.checks):
                raise ValueError("passing verification cannot contain failed checks")
        elif all(check.passed for check in self.checks) and not self.missing:
            raise ValueError("failed verification must report a failed check or gap")
        return self


class TaskProgress(FrozenModel):
    schema_version: Literal[1] = 1
    current_revision: int = Field(default=1, ge=0)
    status: Literal[
        "unknown",
        "planned",
        "running",
        "waiting",
        "verification_pending",
        "completed",
        "blocked",
        "failed",
        "cancelled",
    ] = "unknown"
    step_ids: tuple[str, ...] = Field(default=(), max_length=8)
    attempts: tuple[TaskAttempt, ...] = Field(default=(), max_length=24)
    verified_outputs: tuple[ArtifactRef, ...] = Field(default=(), max_length=64)
    verified_steps: tuple[str, ...] = Field(default=(), max_length=8)
    skipped_steps: tuple[str, ...] = Field(default=(), max_length=8)
    call_bindings: tuple[TaskCallBinding, ...] = Field(default=(), max_length=16)
    pending_decision: TaskDecision | None = None
    counters: TaskCounters = Field(default_factory=TaskCounters)
    last_disposition: Literal[
        "none",
        "known_failure",
        "material_revision",
        "uncertain",
        "denied",
        "policy_revoked",
        "budget_exhausted",
    ] = "none"

    @field_validator("step_ids", "verified_steps", "skipped_steps")
    @classmethod
    def _step_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            _step_id(item)
        if len(set(value)) != len(value):
            raise ValueError("step identifiers must be unique")
        return value

    @model_validator(mode="after")
    def _progress_is_consistent(self) -> TaskProgress:
        if self.current_revision == 0 and (
            self.step_ids
            or self.attempts
            or self.verified_steps
            or self.skipped_steps
            or self.call_bindings
        ):
            raise ValueError("pre-plan progress cannot contain plan steps or attempts")
        plan_steps = set(self.step_ids)
        if not set(self.verified_steps) <= plan_steps:
            raise ValueError("verified steps must belong to the current plan")
        if not set(self.skipped_steps) <= plan_steps or set(self.skipped_steps) & set(
            self.verified_steps
        ):
            raise ValueError(
                "skipped steps must belong to the plan and not be verified"
            )
        if len({binding.logical_call_id for binding in self.call_bindings}) != len(
            self.call_bindings
        ):
            raise ValueError("logical call IDs must be unique")
        if any(attempt.step_id not in plan_steps for attempt in self.attempts):
            raise ValueError("attempts must belong to the current plan")
        if (
            self.pending_decision is not None
            and self.pending_decision.plan_revision != self.current_revision
        ):
            raise ValueError("a pending decision must match the current revision")
        return self


class TaskDecisionCommand(FrozenModel):
    schema_version: Literal[1] = 1
    client_request_id: UUID
    request_id: UUID
    plan_revision: int = Field(ge=0)
    expected_digest: str = Field(pattern=SHA256_PATTERN)
    action: Literal[
        "confirm",
        "reject",
        "answer",
        "resume_configuration",
        "abort",
        "repeat_step",
        "continue_next",
    ]
    answer: str | None = Field(default=None, max_length=10_000)

    @field_validator("client_request_id", "request_id", mode="before")
    @classmethod
    def _parse_uuid(cls, value: object) -> object:
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError as exc:
                raise ValueError("decision identifiers must be UUIDs") from exc
        return value

    @field_validator("answer")
    @classmethod
    def _answer(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("an answer must not be blank")
        return value

    @model_validator(mode="after")
    def _action_matches_answer(self) -> TaskDecisionCommand:
        if self.plan_revision == 0 and self.action == "confirm":
            raise ValueError("plan confirmation requires a plan revision")
        if self.action == "answer":
            if self.answer is None:
                raise ValueError("answer decisions require an answer")
        elif self.answer is not None:
            raise ValueError("only answer decisions carry an answer")
        return self


class ConversationArtifact(FrozenModel):
    id: UUID
    name: str
    media_type: str
    size_bytes: int
    sha256: str


class ExecutionConfigV5(FrozenModel):
    """Task graph v5 extends the maintained v4 execution snapshot."""

    schema_version: Literal[5] = 5
    graph_kind: Literal["chat"] = "chat"
    graph_version: Literal[5] = 5
    allowed_capabilities: tuple[CapabilityRef, ...] = ()
    selected_artifacts: tuple[ArtifactRef, ...] = ()
    connector_refs: tuple[ConnectorRef, ...] = ()
    credential_binding_refs: tuple[str, ...] = ()
    admitted_context_message_ids: tuple[UUID, ...] = ()
    conversation_artifacts: tuple[ConversationArtifact, ...] = ()
    prior_task_context: tuple[str, ...] = ()
    preferred_search_kind: Literal["brave", "tavily"] | None = None
    aws_read_catalog_digest: str = Field(pattern=SHA256_PATTERN)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    loop_limits: LoopLimits = Field(default_factory=LoopLimits)
    task_context: TaskContext
    profile_catalog: ProfileCatalog
    profile_catalog_digest: str = Field(pattern=SHA256_PATTERN)
    task_limits: TaskLimits = Field(default_factory=TaskLimits)

    @model_validator(mode="after")
    def _pinned_snapshot_matches(self) -> ExecutionConfigV5:
        if self.task_context.graph_version != self.graph_version:
            raise ValueError("task context and execution graph versions must match")
        if catalog_digest(self.profile_catalog) != self.profile_catalog_digest:
            raise ValueError("profile catalog digest does not match the snapshot")
        return self
