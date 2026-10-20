"""Model-free version 1 graph for one persisted maintained tool command."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph
from pydantic import ValidationError
from typing_extensions import TypedDict

from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfig,
)
from src.openship.execution.gateway import ToolGateway
from src.openship.providers.errors import ProviderError


class ToolState(TypedDict):
    """Serializable command and result; runtime authority stays injected."""

    run_id: str
    tool_command_json: str
    execution_configuration: str
    tool_result: dict[str, Any]


def _default_gateway():
    from src.openship.database.session import SessionLocal

    return ToolGateway(SessionLocal)


def _command_from(state: ToolState):
    try:
        raw = json.loads(state["tool_command_json"])
        if not isinstance(raw, dict) or set(raw) != {"capability", "arguments"}:
            raise ValueError("tool command shape is invalid")
        arguments = raw["arguments"]
        if not isinstance(arguments, dict):
            raise TypeError("tool arguments shape is invalid")
        return CapabilityRef.model_validate(raw["capability"]), arguments
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise ProviderError(
            "configuration_error",
            safe_message="The stored tool command cannot be read by this runtime.",
            cause=exc,
        ) from None


def _execution_from(state: ToolState) -> ExecutionConfig:
    try:
        return ExecutionConfig.model_validate_json(state["execution_configuration"])
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ProviderError(
            "configuration_error",
            safe_message="The stored tool execution settings cannot be read by this runtime.",
            cause=exc,
        ) from None


def build_tool_graph(
    checkpointer=None,
    gateway_factory: Callable[[], Any] | Any | None = None,
    invocation_factory: Callable[[ToolState], tuple] | None = None,
):
    """Build a graph that dispatches ``manual:0`` through the shared gateway.

    ``invocation_factory`` returns the live claim, runtime-only context and
    control for this invocation. None of them enter the checkpointed state.
    """
    factory = invocation_factory or _default_invocation
    selected_gateway_factory = gateway_factory or _default_gateway

    def execute(state: ToolState) -> dict:
        claim, context, control = factory(state)
        if str(claim.run_id) != state["run_id"]:
            raise ProviderError(
                "lease_lost",
                safe_message="This worker no longer owns the active run.",
            )
        execution = _execution_from(state)
        if context.execution != execution:
            raise ProviderError(
                "configuration_error",
                safe_message="The runtime context does not match the pinned tool settings.",
            )
        ref, arguments = _command_from(state)
        gateway = (
            selected_gateway_factory()
            if callable(selected_gateway_factory)
            else selected_gateway_factory
        )
        inputs = tuple(execution.selected_artifacts)
        result = asyncio.run(
            gateway.execute_tool(
                claim=claim,
                context=context,
                logical_call_id="manual:0",
                ref=ref,
                arguments=arguments,
                inputs=inputs,
                control=control,
            )
        )
        return {"tool_result": result.model_dump(mode="json")}

    builder = StateGraph(ToolState)
    builder.add_node("tool", execute)
    builder.set_entry_point("tool")
    builder.add_edge("tool", END)
    return builder.compile(checkpointer=checkpointer)


def _default_invocation(_state: ToolState):
    raise ProviderError(
        "configuration_error",
        safe_message="Tool execution requires a supervised worker invocation.",
    )
