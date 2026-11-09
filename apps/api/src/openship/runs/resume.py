"""Explicit A/B commands for the fixed analysis journey, never a scheduler."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..artifacts.contracts import ArtifactRef
from ..artifacts.models import Artifact
from ..capabilities.contracts import (
    PublishedToolResult,
    canonical_digest,
    decode_execution_config,
)
from ..chat.models import Message
from ..events.service import append_event
from ..execution.models import ToolOperation
from ..execution.publication import project_published_result
from ..messaging.models import MessageTransportState
from ..messaging.outbox import check_submission_capacity, enqueue_run
from ..sandboxes.models import WorkflowSandbox
from .analysis_graph import AnalysisJourney, decode_journey, encode_journey
from .models import ACTIVE_RUN_STATUSES, Run, RunJob, RunTranscriptRecord
from .schemas import ResumeAnalysisRequest, ResumeChatPythonRequest
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


def _committed_analysis_result(db, operation) -> PublishedToolResult:
    """Refuse corrupt terminal data without granting another execution."""
    try:
        artifact_ids = json.loads(operation.output_artifact_ids)
        if not isinstance(artifact_ids, list) or not all(
            isinstance(item, str) for item in artifact_ids
        ):
            raise ValueError("invalid committed artifact ID list")
        result = project_published_result(db, operation)
        if result.status == "interrupted" or (
            result.status == "succeeded" and len(result.artifacts) != len(artifact_ids)
        ):
            raise ValueError("committed outputs are unavailable")
        if result.status == "succeeded":
            data = result.result
            if not isinstance(data, dict) or not isinstance(data.get("outputs"), dict):
                raise ValueError("invalid committed output map")
            # Projection discovers ready rows, but the committed result—not
            # today's row metadata—is the authority for their exact refs.
            outputs = tuple(
                ArtifactRef.model_validate_json(json.dumps(item))
                for item in data["outputs"].values()
            )
            if {ref.id for ref in outputs} != {ref.id for ref in result.artifacts}:
                raise ValueError("committed output identities do not match")
            result = result.model_copy(update={"artifacts": outputs})
        return result
    except (KeyError, TypeError, ValueError):
        raise _tool_command_error(
            409,
            "analysis_inputs_unavailable",
            "The committed analysis result is no longer available; execution will not be repeated.",
        ) from None


def _resume_replay(db, *, conversation_id, request_id, digest) -> Run | None:
    existing = db.query(Run).filter_by(client_request_id=str(request_id)).first()
    if existing is not None and (
        existing.conversation_id != conversation_id or existing.command_digest != digest
    ):
        raise _tool_command_error(
            409,
            "client_request_id_conflict",
            "This request ID already identifies a different command.",
        )
    return existing


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
    existing = _resume_replay(
        db,
        conversation_id=conversation.id,
        request_id=command.client_request_id,
        digest=digest,
    )
    if existing is not None:
        return existing
    conversation = _lock_active_conversation_scope(
        db, user_id=principal_id, conversation_id=conversation.id
    )
    # A competing admission may have committed while this request waited for
    # the scope lock. Replay its result before checking active work or queuing.
    existing = _resume_replay(
        db,
        conversation_id=conversation.id,
        request_id=command.client_request_id,
        digest=digest,
    )
    if existing is not None:
        return existing
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
            Run.status.in_(ACTIVE_RUN_STATUSES),
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
            result = _committed_analysis_result(db, operation)
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


def resume_chat_python(
    db: Session,
    *,
    principal_id: uuid.UUID,
    run_id: uuid.UUID,
    command: ResumeChatPythonRequest,
) -> Run:
    """Admit one explicit continuation of a python-directed chat run.

    The caller commits. Request identity binds the source run AND the mode.
    Recorded transcript rows and committed operations are copied under the new
    run so the ordinary replay reuses committed results; no URL is silently
    refetched and no captured source is regenerated. An uncertain sandbox
    command gates either mode before any new work is admitted.
    """
    prior = get_run(db=db, user_id=principal_id, run_id=str(run_id))
    conversation = require_owned_conversation(
        db, principal_id, str(prior.conversation_id)
    )
    digest = canonical_digest({"resume_chat_python": str(run_id), "mode": command.mode})
    existing = _resume_replay(
        db,
        conversation_id=conversation.id,
        request_id=command.client_request_id,
        digest=digest,
    )
    if existing is not None:
        return existing
    conversation = _lock_active_conversation_scope(
        db, user_id=principal_id, conversation_id=conversation.id
    )
    # A competing admission may have committed while this request waited for
    # the scope lock. Replay its result before checking active work or queuing.
    existing = _resume_replay(
        db,
        conversation_id=conversation.id,
        request_id=command.client_request_id,
        digest=digest,
    )
    if existing is not None:
        return existing
    prior = (
        db.query(Run).filter_by(id=run_id).with_for_update().populate_existing().one()
    )
    try:
        execution = decode_execution_config(prior.execution_configuration or "")
        if prior.kind != "chat" or prior.graph_version not in (2, 3, 4):
            raise ValueError("not a tool-enabled chat run")
        if "python_execute" not in {ref.id for ref in execution.allowed_capabilities}:
            raise ValueError("no python authority pinned to this chat run")
    except (KeyError, TypeError, ValueError):
        raise _tool_command_error(
            409,
            "chat_python_state_unavailable",
            "This chat run has no pinned python execution to continue. Start a new chat turn with explicitly selected files.",
        ) from None
    if (
        db.query(Run)
        .filter(
            Run.conversation_id == conversation.id,
            Run.status.in_(ACTIVE_RUN_STATUSES),
        )
        .first()
    ):
        raise _tool_command_error(
            409,
            "run_already_active",
            "This conversation already has active work; finish or cancel it before resuming.",
        )
    operations = db.query(ToolOperation).filter_by(run_id=prior.id).all()
    if command.mode == "last_step":
        for operation in operations:
            if operation.status not in ("succeeded", "failed", "cancelled"):
                raise _tool_command_error(
                    409,
                    "chat_python_execution_interrupted",
                    "A recorded step did not reach a terminal status. Wait for its provider TTL, then resume from the beginning explicitly.",
                )

    def _refused_uncertain():
        raise _tool_command_error(
            409,
            "chat_python_execution_interrupted",
            "An earlier sandbox command may still be running. Wait for its provider TTL before continuing; no command has been sent.",
        )

    pointer = (
        db.query(WorkflowSandbox)
        .filter_by(project_id=prior.project_id, conversation_id=prior.conversation_id)
        .first()
    )
    if pointer is not None and pointer.command_uncertain_until is not None:
        until = pointer.command_uncertain_until
        until = until.replace(tzinfo=UTC) if until.tzinfo is None else until
        if until > datetime.now(UTC):
            _refused_uncertain()

    def _require_chat_ref(ref: ArtifactRef) -> None:
        row = db.query(Artifact).filter(
            Artifact.id == ref.id,
            Artifact.project_id == prior.project_id,
            Artifact.conversation_id == prior.conversation_id,
            Artifact.status == "ready",
        ).first()
        if row is None or (
            row.sha256,
            row.size_bytes,
            row.media_type,
        ) != (ref.sha256, ref.size_bytes, ref.media_type):
            raise _tool_command_error(
                409,
                "chat_python_inputs_unavailable",
                "A saved chat file is missing or changed. Select the current files explicitly to start a new chat turn.",
            )

    for ref in execution.selected_artifacts:
        _require_chat_ref(ref)

    pinned_execution = execution
    if command.mode == "last_step":
        # The continuation re-selects the prior run's committed outputs so the
        # replay can hand them forward without a silent refetch: the resume
        # itself is the explicit selection, and only exact committed refs
        # carry authority.
        selected_refs = list(execution.selected_artifacts)
        for operation in operations:
            if operation.status != "succeeded":
                continue
            try:
                output_ids = json.loads(operation.output_artifact_ids or "[]")
                result_data = json.loads(operation.result_json or "")
                outputs = result_data.get("outputs")
                single = result_data.get("artifact")
                source = result_data.get("source")
                if isinstance(outputs, dict):
                    committed = tuple(
                        ArtifactRef.model_validate_json(json.dumps(item))
                        for item in outputs.values()
                    )
                elif isinstance(single, dict):
                    committed = (ArtifactRef.model_validate_json(json.dumps(single)),)
                else:
                    committed = ()
                # A python step also retains its captured source; that is
                # execution material, not a hand-forward data selection.
                declared = {str(ref.id) for ref in committed}
                if isinstance(source, dict):
                    declared.add(str(ArtifactRef.model_validate_json(json.dumps(source)).id))
                if not isinstance(output_ids, list) or set(map(str, output_ids)) != declared:
                    raise ValueError("committed output identities do not match")
            except (KeyError, TypeError, ValueError, AttributeError):
                raise _tool_command_error(
                    409,
                    "chat_python_record_unavailable",
                    "A recorded step has no usable output list. Start a new chat turn with explicitly selected files.",
                ) from None
            for ref in committed:
                if ref.id not in {item.id for item in selected_refs}:
                    _require_chat_ref(ref)
                    selected_refs.append(ref)
        pinned_execution = execution.model_copy(
            update={"selected_artifacts": tuple(selected_refs)}
        )

    new_run_id = uuid.uuid4()
    run = Run(
        id=new_run_id,
        conversation_id=prior.conversation_id,
        project_id=prior.project_id,
        client_request_id=str(command.client_request_id),
        command_digest=digest,
        kind="chat",
        graph_version=prior.graph_version,
        status="queued",
        attempt=0,
        fence=0,
        execution_configuration=(
            pinned_execution.model_dump_json()
            if command.mode == "last_step"
            else prior.execution_configuration
        ),
        model_selection=prior.model_selection,
        model_configuration=prior.model_configuration,
        tool_policy_revision=prior.tool_policy_revision,
    )
    db.add(run)
    db.flush()
    job = RunJob(run_id=run.id, fence=0)
    db.add(job)
    db.flush()
    transport = db.query(MessageTransportState).filter_by(id=1).one()
    enqueue_run(db, run.id, job.dispatch_generation, epoch=transport.transport_epoch)
    # The replay re-runs the recorded request, so the new run carries the
    # same user turn the durable worker reads to build model context.
    for row in (
        db.query(Message)
        .filter_by(run_id=prior.id, role="user")
        .order_by(Message.created_at)
        .all()
    ):
        db.add(
            Message(
                run_id=run.id,
                **{
                    column.key: getattr(row, column.key)
                    for column in Message.__table__.columns
                    if column.key not in ("id", "run_id", "created_at")
                },
            )
        )
    if command.mode == "last_step":
        # Copy recorded history and committed operations so replay reuses
        # their digests under the new run; the provider sees no new dispatch.
        for row in (
            db.query(RunTranscriptRecord)
            .filter_by(run_id=prior.id)
            .order_by(RunTranscriptRecord.ordinal)
            .all()
        ):
            db.add(
                RunTranscriptRecord(
                    run_id=run.id,
                    **{
                        column.key: getattr(row, column.key)
                        for column in RunTranscriptRecord.__table__.columns
                        if column.key not in ("id", "run_id")
                    },
                )
            )
        for row in operations:
            db.add(
                ToolOperation(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    **{
                        column.key: getattr(row, column.key)
                        for column in ToolOperation.__table__.columns
                        if column.key not in ("id", "run_id")
                    },
                )
            )
        db.flush()
    append_event(
        db,
        conversation_id=run.conversation_id,
        run_id=run.id,
        event_type="turn.submitted",
        actor_id=principal_id,
        payload={
            "schema_version": 1,
            "run_id": str(run.id),
            "client_request_id": str(command.client_request_id),
            "capability": "python_execute",
            "resumed_from": str(prior.id),
            "mode": command.mode,
        },
    )
    db.flush()
    return run
