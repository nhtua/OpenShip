"""Fixed analysis journey: prepare source/inputs, execute, use accepted outputs.

The journey is a record inside the ordinary durable run graph, not a second
queue or scheduler. Its sandbox-backed middle step dispatches through the
shared tool gateway, and the step after it consumes only the published
artifact refs. Nothing here reads residual sandbox paths, revives a process
or trusts a sandbox that may since have expired.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from uuid import UUID

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict
from typing_extensions import TypedDict

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.artifacts.filesystem import ArtifactStorageError
from src.openship.artifacts.models import Artifact
from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfigLike,
    PublishedToolResult,
    decode_execution_config,
)
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Run
from src.openship.sandboxes.tool import ANALYSIS_ID, AnalysisArguments

PREPARE = "prepare"
EXECUTE = "execute"
USE_OUTPUTS = "use_outputs"
PHASES = (PREPARE, EXECUTE, USE_OUTPUTS)
_PREVIEW_BYTES = 4096


class AnalysisJourney(BaseModel):
    """Durable phase records: where the journey is and which refs it holds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root_run_id: str
    initial_refs: tuple[ArtifactRef, ...] = ()
    outputs: tuple[ArtifactRef, ...] = ()
    completed_phases: tuple[str, ...] = ()


def decode_journey(payload: str) -> AnalysisJourney:
    """Read one persisted journey record, or fail visibly."""
    if not payload:
        return AnalysisJourney(root_run_id="")
    return AnalysisJourney.model_validate_json(payload)


def encode_journey(journey: AnalysisJourney) -> str:
    return journey.model_dump_json()


def resume_phase(journey: AnalysisJourney) -> str | None:
    """First fixed phase that has not completed, or ``None`` when used."""
    for phase in PHASES:
        if phase not in journey.completed_phases:
            return phase
    return None


class AnalysisState(TypedDict):
    """Serializable journey and result; runtime authority stays injected."""

    run_id: str
    tool_command_json: str
    execution_configuration: str
    journey_json: str
    tool_result: dict[str, Any]
    analysis_summary: dict[str, str]


def _command_from(state: AnalysisState) -> tuple[CapabilityRef, dict, AnalysisArguments]:
    try:
        raw = json.loads(state["tool_command_json"])
        if not isinstance(raw, dict) or set(raw) != {"capability", "arguments"}:
            raise ValueError("tool command shape is invalid")
        arguments = raw["arguments"]
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments shape is invalid")
        ref = CapabilityRef.model_validate(raw["capability"])
        validated = AnalysisArguments.model_validate(arguments)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderError(
            "configuration_error",
            safe_message="The stored analysis command cannot be read by this runtime.",
            cause=exc,
        ) from None
    return ref, arguments, validated


def _execution_from(state: AnalysisState) -> ExecutionConfigLike:
    try:
        return decode_execution_config(state["execution_configuration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(
            "configuration_error",
            safe_message="The stored analysis execution settings cannot be read by this runtime.",
            cause=exc,
        ) from None


def _selected_refs(
    db, run, arguments: AnalysisArguments
) -> tuple[ArtifactRef, ...]:
    """Pin the currently ready selected files, or refuse before any dispatch."""
    refs = []
    for artifact_id in (arguments.source_id, *sorted(arguments.input_ids.values())):
        row = db.query(Artifact).filter(Artifact.id == artifact_id).first()
        if (
            row is None
            or row.conversation_id != run.conversation_id
            or row.project_id != run.project_id
            or row.status != "ready"
        ):
            raise ProviderError(
                "configuration_error",
                safe_message=(
                    "A selected analysis file is not available in this conversation. "
                    "Choose a current file and submit the analysis again."
                ),
            )
        refs.append(
            ArtifactRef(
                id=row.id,
                sha256=row.sha256,
                size_bytes=row.size_bytes,
                media_type=row.media_type,
            )
        )
    return tuple(refs)


def _journey_of(state: AnalysisState) -> AnalysisJourney:
    journey = decode_journey(state.get("journey_json") or "")
    if not journey.initial_refs:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                "The analysis preparation step did not record its selected files."
            ),
        )
    return journey


def _advance(journey: AnalysisJourney, *, phase: str) -> str:
    return encode_journey(
        AnalysisJourney(
            root_run_id=journey.root_run_id,
            initial_refs=journey.initial_refs,
            outputs=journey.outputs,
            completed_phases=journey.completed_phases + (phase,),
        )
    )


def build_analysis_graph(
    checkpointer=None,
    gateway_factory: Any = None,
    invocation_factory: Any = None,
):
    """Build the three fixed phases for one authorized analysis run.

    ``invocation_factory`` returns the live claim, runtime-only context and
    control for this invocation. None of them enter the checkpointed state.
    """
    factory = invocation_factory or _default_invocation
    selected_gateway_factory = gateway_factory

    def _select_gateway():
        if callable(selected_gateway_factory):
            return selected_gateway_factory()
        return selected_gateway_factory

    def prepare(state: AnalysisState) -> dict:
        claim, context, _control = factory(state)
        if str(claim.run_id) != state["run_id"]:
            raise ProviderError(
                "lease_lost",
                safe_message="This worker no longer owns the active run.",
            )
        execution = _execution_from(state)
        if context.execution != execution:
            raise ProviderError(
                "configuration_error",
                safe_message="The runtime context does not match the pinned analysis settings.",
            )
        ref, _arguments, validated = _command_from(state)
        if ref.id != ANALYSIS_ID:
            raise ProviderError(
                "configuration_error",
                safe_message="This run is not an authorized sandbox analysis.",
            )
        with context.session_factory() as db:
            run = db.query(Run).filter(Run.id == claim.run_id).first()
            if run is None:
                raise ProviderError(
                    "lease_lost",
                    safe_message="This worker no longer owns the active run.",
                )
            journey = AnalysisJourney(
                root_run_id=str(claim.run_id),
                initial_refs=_selected_refs(db, run, validated),
                completed_phases=(PREPARE,),
            )
        return {"journey_json": encode_journey(journey)}

    def execute(state: AnalysisState) -> dict:
        claim, context, control = factory(state)
        if str(claim.run_id) != state["run_id"]:
            raise ProviderError(
                "lease_lost",
                safe_message="This worker no longer owns the active run.",
            )
        journey = _journey_of(state)
        ref, arguments, _validated = _command_from(state)
        gateway = _select_gateway()
        result = asyncio.run(
            gateway.execute_tool(
                claim=claim,
                context=context,
                logical_call_id="analysis:0",
                ref=ref,
                arguments=arguments,
                inputs=journey.initial_refs,
                control=control,
            )
        )
        published = result.model_dump(mode="json")
        outputs = journey.outputs
        completed = journey.completed_phases + (EXECUTE,)
        if result.status == "succeeded":
            outputs = tuple(
                ArtifactRef(
                    id=UUID(str(item["id"])),
                    sha256=item["sha256"],
                    size_bytes=item["size_bytes"],
                    media_type=item["media_type"],
                )
                for item in published["artifacts"]
            )
        return {
            "journey_json": encode_journey(
                AnalysisJourney(
                    root_run_id=journey.root_run_id,
                    initial_refs=journey.initial_refs,
                    outputs=outputs,
                    completed_phases=completed,
                )
            ),
            "tool_result": published,
        }

    def use_outputs(state: AnalysisState) -> dict:
        claim, context, _control = factory(state)
        if str(claim.run_id) != state["run_id"]:
            raise ProviderError(
                "lease_lost",
                safe_message="This worker no longer owns the active run.",
            )
        journey = _journey_of(state)
        result = PublishedToolResult.model_validate_json(
            json.dumps(state["tool_result"])
        )
        if result.status != "succeeded" or not journey.outputs:
            return {
                "journey_json": state.get("journey_json") or "",
            }
        store = context.artifact_store
        if store is None:
            raise ProviderError(
                "configuration_error",
                safe_message="Conversation artifact storage is unavailable.",
            )
        summaries: dict[str, str] = {}
        with context.session_factory() as db:
            run = db.query(Run).filter(Run.id == claim.run_id).first()
            if run is None:
                raise ProviderError(
                    "lease_lost",
                    safe_message="This worker no longer owns the active run.",
                )
            for ref in journey.outputs:
                row = db.query(Artifact).filter(Artifact.id == ref.id).first()
                if (
                    row is None
                    or row.conversation_id != run.conversation_id
                    or row.project_id != run.project_id
                    or row.status != "ready"
                    or row.sha256 != ref.sha256
                    or row.size_bytes != ref.size_bytes
                ):
                    raise ProviderError(
                        "invalid_response",
                        safe_message="A saved analysis output is no longer available.",
                    )
                try:
                    with store.open_verified(row) as handle:
                        data = handle.read()
                except ArtifactStorageError:
                    raise ProviderError(
                        "invalid_response",
                        safe_message="A saved analysis output could not be verified.",
                    ) from None
                if (
                    len(data) != row.size_bytes
                    or hashlib.sha256(data).hexdigest() != row.sha256
                ):
                    raise ProviderError(
                        "invalid_response",
                        safe_message="A saved analysis output could not be verified.",
                    ) from None
                summaries[row.display_name or str(row.id)] = data[
                    :_PREVIEW_BYTES
                ].decode("utf-8", errors="replace")
        return {
            "analysis_summary": summaries,
            "journey_json": _advance(journey, phase=USE_OUTPUTS),
        }

    builder = StateGraph(AnalysisState)
    builder.add_node(PREPARE, prepare)
    builder.add_node(EXECUTE, execute)
    builder.add_node(USE_OUTPUTS, use_outputs)
    builder.set_entry_point(PREPARE)
    builder.add_edge(PREPARE, EXECUTE)
    builder.add_edge(EXECUTE, USE_OUTPUTS)
    builder.add_edge(USE_OUTPUTS, END)
    return builder.compile(checkpointer=checkpointer)


def _default_invocation(_state: AnalysisState):
    raise ProviderError(
        "configuration_error",
        safe_message="Analysis execution requires a supervised worker invocation.",
    )
