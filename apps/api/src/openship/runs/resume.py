"""Explicit A/B commands for the fixed analysis journey, never a scheduler."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..artifacts.contracts import ArtifactRef
from ..artifacts.models import Artifact
from ..capabilities.contracts import canonical_digest
from ..events.service import append_event
from ..execution.models import ToolOperation
from ..execution.publication import project_published_result
from ..messaging.models import MessageTransportState
from ..messaging.outbox import check_submission_capacity, enqueue_run
from ..sandboxes.models import WorkflowSandbox
from .analysis_graph import AnalysisJourney, decode_journey, encode_journey
from .models import Run, RunJob
from .schemas import ResumeAnalysisRequest
from .service import (
    _lock_active_conversation_scope,
    _tool_command_error,
    get_run,
    require_owned_conversation,
)


def _require_refs(db, run, refs):
    for ref in refs:
        row = db.get(Artifact, ref.id)
        if (
            row is None
            or row.project_id != run.project_id
            or row.conversation_id != run.conversation_id
            or row.status != "ready"
            or row.sha256 != ref.sha256
            or row.size_bytes != ref.size_bytes
            or row.media_type != ref.media_type
        ):
            raise _tool_command_error(
                409,
                "analysis_inputs_unavailable",
                "A saved analysis file is missing or pruned. Select current files explicitly; earlier steps will not be recomputed.",
            )


def resume_analysis(
    db: Session,
    *,
    principal_id: uuid.UUID,
    run_id: uuid.UUID,
    command: ResumeAnalysisRequest,
) -> Run:
    """Admit one new run atomically through the ordinary queue/outbox.

    The caller commits. Request identity binds the source run AND choice. A
    committed operation is projected, never re-admitted under a new identity.
    """
    prior = get_run(db=db, user_id=principal_id, run_id=str(run_id))
    conversation = require_owned_conversation(
        db, principal_id, str(prior.conversation_id)
    )
    digest = canonical_digest({"resume_analysis": str(run_id), "mode": command.mode})
    existing = (
        db.query(Run)
        .filter_by(client_request_id=str(command.client_request_id))
        .first()
    )
    if existing is not None:
        if (
            existing.conversation_id != conversation.id
            or existing.command_digest != digest
        ):
            raise _tool_command_error(
                409,
                "client_request_id_conflict",
                "This request ID already identifies a different command.",
            )
        return existing
    conversation = _lock_active_conversation_scope(
        db, user_id=principal_id, conversation_id=conversation.id
    )
    prior = (
        db.query(Run).filter_by(id=run_id).with_for_update().populate_existing().one()
    )
    try:
        raw = json.loads(prior.tool_command_json or "{}")
        if prior.kind != "tool" or raw["capability"]["id"] != "sandbox_analysis":
            raise ValueError("not analysis")
        saved = json.loads(prior.analysis_state_json or "{}")
        journey = decode_journey(saved["journey_json"])
    except (KeyError, TypeError, ValueError):
        raise _tool_command_error(
            409,
            "analysis_state_unavailable",
            "This run has no saved analysis state. Submit a new analysis with explicitly selected files.",
        ) from None
    if (
        db.query(Run)
        .filter(
            Run.conversation_id == conversation.id,
            Run.status.in_(("created", "queued", "running", "waiting_connector")),
        )
        .first()
    ):
        raise _tool_command_error(
            409,
            "run_already_active",
            "Another run is already active on this conversation.",
        )

    retained_output_ids = list(
        dict.fromkeys(
            [
                *[str(ref.id) for ref in journey.outputs],
                *saved.get("retained_output_ids", []),
            ]
        )
    )
    retained_error_code = prior.error_code or saved.get("retained_error_code")
    new_run_id = uuid.uuid4()
    if command.mode == "from_beginning":
        _require_refs(db, prior, journey.initial_refs)
        # New root; generated source and all old outputs are deliberately absent.
        saved = {
            "journey_json": encode_journey(
                AnalysisJourney(root_run_id=str(new_run_id), initial_refs=journey.initial_refs)
            )
        }
    else:
        operation = (
            db.query(ToolOperation)
            .filter_by(run_id=prior.id, logical_call_id="analysis:0")
            .first()
        )
        if operation is not None:
            if operation.status not in ("succeeded", "failed", "cancelled"):
                raise _tool_command_error(
                    409,
                    "analysis_execution_interrupted",
                    "The earlier command may still be running or was interrupted. Wait for definite provider availability; it will not be sent again. Start from beginning only when ready.",
                )
            result = project_published_result(db, operation)
            if result.status == "interrupted" or (
                operation.status == "succeeded"
                and len(result.artifacts)
                != len(json.loads(operation.output_artifact_ids))
            ):
                raise _tool_command_error(
                    409,
                    "analysis_inputs_unavailable",
                    "The committed analysis result is no longer available; execution will not be repeated.",
                )
            saved["tool_result"] = result.model_dump(mode="json")
            if result.status == "succeeded":
                # Projection discovers ready rows, but the committed result—not
                # today's row metadata—is the authority for their exact refs.
                outputs = tuple(
                    ArtifactRef.model_validate_json(json.dumps(item))
                    for item in result.result["outputs"].values()
                )
                if {ref.id for ref in outputs} != {ref.id for ref in result.artifacts}:
                    raise _tool_command_error(409, "analysis_inputs_unavailable", "The committed analysis output set is no longer available.")
                result = result.model_copy(update={"artifacts": outputs})
                saved["tool_result"] = result.model_dump(mode="json")
            journey = journey.model_copy(
                update={
                    "completed_phases": ("prepare", "execute") + (("use_outputs",) if "use_outputs" in journey.completed_phases else ()),
                    "outputs": result.artifacts,
                }
            )
        elif "tool_result" not in saved and "execute" in journey.completed_phases:
            raise _tool_command_error(
                409,
                "analysis_state_unavailable",
                "The saved result is unavailable; execution will not be repeated.",
            )
        if "execute" in journey.completed_phases:
            _require_refs(db, prior, journey.outputs)
        else:
            _require_refs(
                db,
                prior,
                (
                    *journey.initial_refs,
                    *((journey.generated_source,) if journey.generated_source else ()),
                ),
            )
        saved["journey_json"] = encode_journey(journey)

    needs_execution = (
        command.mode == "from_beginning" or "execute" not in journey.completed_phases
    )
    if needs_execution:
        pointer = (
            db.query(WorkflowSandbox)
            .filter_by(
                project_id=prior.project_id, conversation_id=prior.conversation_id
            )
            .first()
        )
        if pointer is not None and pointer.command_uncertain_until is not None:
            until = pointer.command_uncertain_until
            until = until.replace(tzinfo=UTC) if until.tzinfo is None else until
            if until > datetime.now(UTC):
                raise _tool_command_error(
                    409,
                    "analysis_execution_interrupted",
                    "An earlier sandbox command may still be running. Wait for its provider TTL before starting another execution; no command has been sent.",
                )
    saved.update(
        prior_run_id=str(prior.id),
        retained_output_ids=retained_output_ids,
        retained_error_code=retained_error_code,
    )
    check_submission_capacity(db)
    run = Run(
        id=new_run_id,
        conversation_id=prior.conversation_id,
        project_id=prior.project_id,
        client_request_id=str(command.client_request_id),
        command_digest=digest,
        kind="tool",
        graph_version=prior.graph_version,
        status="queued",
        attempt=0,
        fence=0,
        tool_command_json=prior.tool_command_json,
        execution_configuration=prior.execution_configuration,
        model_selection=prior.model_selection,
        model_configuration=prior.model_configuration,
        tool_policy_revision=prior.tool_policy_revision,
        analysis_state_json=json.dumps(saved),
    )
    db.add(run)
    db.flush()
    job = RunJob(run_id=run.id, fence=0)
    db.add(job)
    db.flush()
    transport = db.query(MessageTransportState).filter_by(id=1).one()
    enqueue_run(db, run.id, job.dispatch_generation, epoch=transport.transport_epoch)
    append_event(
        db,
        conversation_id=run.conversation_id,
        run_id=run.id,
        event_type="tool.submitted",
        actor_id=principal_id,
        payload={
            "schema_version": 1,
            "run_id": str(run.id),
            "client_request_id": str(command.client_request_id),
            "capability": raw["capability"],
            "resumed_from": str(prior.id),
            "mode": command.mode,
        },
    )
    db.flush()
    return run
