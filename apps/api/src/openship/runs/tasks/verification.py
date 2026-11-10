"""Evidence-backed verification of committed task work.

Verification reads only durable records and only ever in one order: the
backend proves what the run actually committed - a committed attempt and the
gateway operation it bound, the artifact rows that operation recorded, their
provenance, readiness and the bytes actually stored for them - and only then
does a semantic criterion get evaluated, against references the backend has
already resolved. A model's claim about what it produced is never authority
for what it produced: a step whose chart was never published is not complete
because an answer said it was, and a step whose recorded result disagrees with
the stored bytes is not verified, because the disagreement is what the records
show.

Two things this module deliberately refuses to do:

* It never accepts an artifact identity a model named. Artifacts are looked up
  only through the operation the attempt bound, and an identity outside those
  rows - or one whose scope, readiness or stored bytes disagree - makes the
  check fail rather than pass.
* It never claims completion for work it cannot cover. A skipped step, a
  missing deliverable or an uncovered dependent input leaves the report failed
  with the gap named, so a partial outcome stays a real outcome instead of a
  renormalised success.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import ArtifactStorageError, LocalArtifactStore
from src.openship.artifacts.models import Artifact
from src.openship.config import settings
from src.openship.execution.models import ToolOperation

from .contracts import TaskAssessment, VerificationCheck, VerificationReport
from .models import TaskStepAttempt
from .state import _json, read_plan, read_progress

_OPERATION_EVIDENCE = "operation:"
_ARTIFACT_EVIDENCE = "artifact:"


class VerificationError(RuntimeError):
    """This call cannot be verified as asked; it never guesses a verdict."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _step_attempts(db, run_id: UUID, step_id: str) -> list[TaskStepAttempt]:
    """This step's committed attempts, oldest first."""
    return (
        db.query(TaskStepAttempt)
        .filter(TaskStepAttempt.run_id == run_id, TaskStepAttempt.step_id == step_id)
        .order_by(TaskStepAttempt.ordinal)
        .all()
    )


def _operation_row(db, operation_id: UUID) -> ToolOperation | None:
    return db.query(ToolOperation).filter_by(id=operation_id).one_or_none()


def _published_rows(db, operation: ToolOperation) -> tuple[Artifact, ...]:
    """The artifact rows the operation itself recorded, by identity.

    Nothing here consults a model-supplied id: the operation is the only
    authority for which artifacts it published, so an artifact that is not one
    of these rows cannot be substituted into a verdict.
    """
    recorded = _json(operation.output_artifact_ids, [])
    if not isinstance(recorded, list):
        return ()
    identities = []
    for item in recorded:
        try:
            identities.append(UUID(str(item)))
        except (TypeError, ValueError):
            continue
    if not identities:
        return ()
    return tuple(
        db.query(Artifact).filter(Artifact.id.in_(identities)).order_by(Artifact.id).all()
    )


def _stored_bytes(artifact: Artifact) -> bytes | None:
    """The bytes stored for one artifact, only if they still prove its identity.

    The store verifies the regular file and its digest before handing back a
    descriptor, and the digest here is recomputed from what is actually read,
    so bytes swapped in after publication fail instead of verifying.
    """
    store = LocalArtifactStore(settings.artifact_root, settings.artifact_storage_id)
    try:
        with store.open_verified(artifact) as handle:
            data = handle.read()
    except ArtifactStorageError:
        return None
    if data is None or len(data) != artifact.size_bytes:
        return None
    if hashlib.sha256(data).hexdigest() != artifact.sha256:
        return None
    return data


def _artifact_covers(artifact: Artifact, operation: ToolOperation) -> bool:
    """An artifact belongs to this operation's scope and is committed."""
    return (
        artifact.project_id == operation.project_id
        and artifact.conversation_id == operation.conversation_id
        and artifact.originating_run_id == operation.run_id
        and artifact.originating_operation_id == operation.id
        and artifact.status == "ready"
    )


def verify_step(
    db,
    *,
    run_id: UUID,
    step_id: str,
    assessment: TaskAssessment,
) -> VerificationReport:
    """One step's verdict from durable evidence, or the gap that keeps it failed.

    Every passing check carries an evidence reference the backend resolved.
    ``assessment`` supplies the criteria the step is judged against: each of its
    cited references has to resolve to that same durable evidence, and a
    citation that does not resolve is a failed check, never a permission to
    pass.
    """
    plan = read_plan(db, run_id)
    if plan is None:
        raise VerificationError("plan_missing", "a run with no plan has no step to verify")
    step = next((item for item in plan.steps if item.id == step_id), None)
    if step is None:
        raise VerificationError("step_unknown", "the step does not belong to the current plan")

    attempts = _step_attempts(db, run_id, step_id)
    latest = attempts[-1] if attempts else None
    operation = _operation_row(db, latest.operation_id) if latest is not None else None
    rows = _published_rows(db, operation) if operation is not None else ()
    verified_bytes = {row.display_name: _stored_bytes(row) for row in rows}
    by_name: dict[str, Artifact] = {}
    for row in rows:
        if _artifact_covers(row, operation):
            by_name.setdefault(row.display_name, row)

    checks: list[VerificationCheck] = []
    missing: list[str] = []
    evidence: list[str] = []

    if operation is not None and operation.request_digest:
        evidence.append(_OPERATION_EVIDENCE + operation.request_digest)

    outcome_reference = (
        _OPERATION_EVIDENCE + operation.request_digest
        if operation is not None and operation.request_digest
        else None
    )
    if (
        latest is not None
        and latest.status == "succeeded"
        and operation is not None
        and operation.status == "succeeded"
        and outcome_reference is not None
        and latest.result_digest == operation.result_digest
    ):
        checks.append(
            VerificationCheck(
                name="committed attempt outcome",
                passed=True,
                evidence_ref=outcome_reference,
            )
        )
    else:
        checks.append(
            VerificationCheck(name="committed attempt outcome", passed=False)
        )

    for row in rows:
        if not _artifact_covers(row, operation):
            checks.append(
                VerificationCheck(name=f"published artifact provenance {row.display_name}", passed=False)
            )
        elif verified_bytes.get(row.display_name) is None:
            checks.append(
                VerificationCheck(
                    name=f"stored bytes of {row.display_name}",
                    passed=False,
                )
            )

    for name in step.expected_outputs:
        row = by_name.get(name)
        if (
            row is None
            or row.status != "ready"
            or verified_bytes.get(name) is None
        ):
            missing.append(name)
            checks.append(
                VerificationCheck(name=f"declared output {name}", passed=False)
            )
            continue
        checks.append(
            VerificationCheck(
                name=f"declared output {name}",
                passed=True,
                evidence_ref=_ARTIFACT_EVIDENCE + row.sha256,
            )
        )

    resolved = set(evidence) | {_ARTIFACT_EVIDENCE + row.sha256 for row in by_name.values()}
    unresolved = tuple(
        reference for reference in assessment.evidence_refs if reference not in resolved
    )
    if unresolved:
        checks.append(
            VerificationCheck(
                name="cited evidence resolves in this run's records",
                passed=False,
            )
        )
    elif assessment.evidence_refs:
        checks.append(
            VerificationCheck(
                name="cited evidence resolves in this run's records",
                passed=True,
                evidence_ref=assessment.evidence_refs[0],
            )
        )

    covered = tuple(
        _ARTIFACT_EVIDENCE + row.sha256
        for row in by_name.values()
        if verified_bytes.get(row.display_name) is not None
    )
    passed = not missing and not unresolved and all(check.passed for check in checks)
    report_evidence = tuple(dict.fromkeys(evidence + list(covered)))[:16]
    unique_missing = tuple(dict.fromkeys(missing))[:16]
    explanation = (
        (
            f"Step {step_id} is covered by the committed attempt and the artifacts "
            "that operation published."
            if passed
            else (
                f"Step {step_id} is not verified: "
                + (
                    ", ".join(unique_missing) + " were not published as ready artifacts. "
                    if unique_missing
                    else ""
                )
                + ("cited evidence does not resolve to this run's records. " if unresolved else "")
                + ("the committed outcome or its evidence is missing." if not unique_missing and not unresolved else "")
            )
        ).strip()
    )
    return VerificationReport(
        step_id=step_id,
        passed=passed,
        checks=tuple(checks)[:16],
        evidence_refs=report_evidence if passed else tuple(report_evidence)[:16],
        missing=unique_missing,
        explanation=explanation,
    )


def verify_task(db, *, run_id: UUID, assessment: TaskAssessment) -> VerificationReport:
    """Task-wide coverage: every required deliverable, or the gap that remains.

    A skipped step is never a verified one, so its deliverables stay in
    ``missing`` until something already covered in this scope supplies them;
    that is what keeps a partial task partial instead of completed.
    """
    plan = read_plan(db, run_id)
    if plan is None:
        raise VerificationError("plan_missing", "a run with no plan has nothing to verify")
    progress = read_progress(db, run_id)
    verified = set(progress.verified_steps)
    covered: dict[str, ArtifactRef] = {}
    for reference in progress.verified_outputs:
        row = db.query(Artifact).filter_by(id=reference.id).one_or_none()
        if row is not None and row.status == "ready":
            covered.setdefault(row.display_name, reference)

    checks: list[VerificationCheck] = []
    missing: list[str] = []
    for step in plan.steps:
        uncovered = tuple(
            name for name in step.expected_outputs if name not in covered
        )
        if step.id in verified and not uncovered:
            checks.append(
                VerificationCheck(
                    name=f"step {step.id} verified",
                    passed=True,
                    evidence_ref=_ARTIFACT_EVIDENCE
                    + covered[step.expected_outputs[0]].sha256,
                )
            )
            continue
        missing.extend(uncovered)
        checks.append(
            VerificationCheck(
                name=f"step {step.id} deliverables covered",
                passed=False,
            )
        )

    evidence_refs = tuple(
        dict.fromkeys(
            [
                _ARTIFACT_EVIDENCE + covered[name].sha256
                for name in sorted(covered)
            ]
        )
    )
    passed = not missing and all(check.passed for check in checks)
    unique_missing = tuple(dict.fromkeys(missing))[:16]
    explanation = (
        (
            "Every plan step is covered by verified evidence."
            if passed
            else (
                "Task verification is not complete: "
                + (", ".join(unique_missing) + " remain uncovered. " if unique_missing else "")
                + ("the assessed evidence cites nothing durable." if not unique_missing else "")
            )
        ).strip()
    )
    return VerificationReport(
        step_id=None,
        passed=passed,
        checks=tuple(checks[:16]),
        evidence_refs=evidence_refs[:16],
        missing=unique_missing,
        explanation=explanation,
    )
