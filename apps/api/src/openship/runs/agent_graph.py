"""Bounded chat-v2 graph that alternates model turns and trusted tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from langgraph.graph import END, StateGraph
from pydantic import ValidationError
from typing_extensions import TypedDict

from ..capabilities.catalog import resolve_capability
from ..capabilities.contracts import (
    ExecutionConfigLike,
    PublishedToolResult,
    decode_execution_config,
)
from ..capabilities.definitions import get_tool_definition
from ..capabilities.policy import (
    policy_changed_for_capabilities_since,
    read_effective_tool_policy,
)
from ..execution.errors import CapabilityError
from ..providers.contracts import (
    ChatMessage,
    ModelRequest,
    ProviderTurnResult,
    ToolDefinition,
)
from ..providers.errors import ProviderError
from ..providers.runner import authorize_current_profile, run_model_turn
from .service import parse_run_model_config
from .transcript import append_transcript, read_transcript


class AgentState(TypedDict):
    """Only serializable messages and pinned, secret-free run settings."""

    messages: list[dict[str, Any]]
    run_id: str
    conversation_id: str
    model_configuration: str
    execution_configuration: str
    tool_policy_revision: int | None
    assistant_response: str
    usage: dict | None
    finish_reason: str


def _messages(state: AgentState) -> list[ChatMessage]:
    try:
        return [ChatMessage.model_validate(message) for message in state["messages"]]
    except (KeyError, TypeError, ValueError, ValidationError) as invalid:
        raise ProviderError(
            "invalid_request",
            safe_message="The conversation has messages this model cannot continue.",
            cause=invalid,
        ) from invalid


def _available_tools(
    execution: ExecutionConfigLike, enabled_ids: frozenset[str]
) -> tuple[ToolDefinition, ...]:
    """Expose only still-enabled, unchanged capabilities pinned to this run."""
    definitions = []
    for ref in execution.allowed_capabilities:
        if ref.id not in enabled_ids:
            continue
        try:
            manifest = resolve_capability(ref, enabled_ids)
        except (
            CapabilityError
        ) as refused:  # manifest changes must stop before model dispatch.
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    "A tool pinned to this run is no longer available in the installation."
                ),
                cause=refused,
            ) from None
        try:
            definition = get_tool_definition(manifest)
        except LookupError as refused:
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    "A tool pinned to this run is no longer available in the installation."
                ),
                cause=refused,
            ) from None
        if not definition.model_selectable:
            continue
        if manifest.id in {"aws_s3_bucket_storage", "aws_s3_bucket_metadata"} and not getattr(
            execution, "selected_resource_binding_refs", ()
        ):
            continue
        definitions.append(
            ToolDefinition(
                name=manifest.id,
                description=definition.description,
                parameters=manifest.input_schema,
            )
        )
    return tuple(definitions)


def _append_transcript(
    invocation, model_turn_ordinal: int, message: ChatMessage
) -> None:
    with invocation.session_factory() as db:
        append_transcript(db, invocation.claim, model_turn_ordinal, message)
        db.commit()


def _result_message(result: PublishedToolResult, max_bytes: int) -> str:
    encoded = json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ProviderError("output_limit_exceeded")
    return encoded


def _failure_for_tool(result: PublishedToolResult) -> ProviderError | None:
    if result.status == "interrupted":
        return ProviderError("execution_interrupted")
    if result.status == "cancelled":
        return ProviderError("cancelled")
    if result.error and result.error.code in ("lease_lost", "execution_interrupted"):
        return ProviderError(result.error.code)
    return None


def build_agent_graph(
    checkpointer=None,
    gateway_factory: Callable[[], Any] | Any | None = None,
    invocation_factory: Callable[[AgentState], Any] | None = None,
):
    """Build the graph for chat-v2; runtime objects never enter graph state."""
    if invocation_factory is None:
        raise ProviderError(
            "configuration_error",
            safe_message="Tool-enabled chat requires a supervised worker invocation.",
        )
    if gateway_factory is None:
        from ..database.session import SessionLocal
        from ..execution.gateway import ToolGateway

        selected_gateway_factory = lambda: ToolGateway(SessionLocal)
    else:
        selected_gateway_factory = gateway_factory

    def execute(state: AgentState) -> dict:
        invocation = invocation_factory(state)
        if str(invocation.claim.run_id) != state["run_id"]:
            raise ProviderError("lease_lost")

        try:
            execution = decode_execution_config(state["execution_configuration"])
        except (KeyError, TypeError, ValueError, ValidationError) as invalid:
            raise ProviderError("configuration_error", cause=invalid) from invalid
        if invocation.context.execution != execution:
            raise ProviderError("configuration_error")

        model_config = parse_run_model_config(
            state.get("model_configuration"), state["run_id"]
        )
        messages = _messages(state)
        # A resumed run carries its recorded transcript; continuing means
        # replaying the recorded turns' committed results, not re-deriving
        # them from conversation history alone. A fresh run has none.
        with invocation.session_factory() as transcript_db:
            recorded = read_transcript(transcript_db, invocation.claim.run_id)
            for entry in recorded:
                messages.append(entry.message)
        pending_assistant = None
        if recorded:
            last = recorded[-1].message
            if last.continuation is not None:
                from ..providers.configuration import destination_label

                continuation = last.continuation
                profile = model_config.primary
                if (continuation.profile_revision != profile.profile_revision
                        or continuation.model != profile.model
                        or continuation.destination != destination_label(profile.endpoint)):
                    raise ProviderError("unsupported_capability")
            if last.role == "assistant" and last.continuation is not None and last.tool_calls:
                pending_assistant = last
            if last.role == "assistant" and last.continuation is not None and not last.tool_calls:
                if not last.content.strip():
                    raise ProviderError("invalid_response")
                return {"assistant_response": last.content, "usage": None, "finish_reason": "stop"}
        gateway = (
            selected_gateway_factory()
            if callable(selected_gateway_factory)
            else selected_gateway_factory
        )
        usage = None

        responses_history = any(entry.message.continuation is not None for entry in recorded)
        starting_turn = (recorded[-1].model_turn_ordinal + (0 if pending_assistant else 1)
                         if responses_history else 0)
        for model_turn_ordinal in range(starting_turn, execution.loop_limits.max_model_turns):
            invocation.control.raise_if_stopped()
            with invocation.session_factory() as db:
                policy = read_effective_tool_policy(db)
                if not policy.state_available:
                    raise ProviderError("unsupported_capability")
                revision = state.get("tool_policy_revision")
                changed_ids = {
                    ref.id
                    for ref in execution.allowed_capabilities
                    if policy_changed_for_capabilities_since(
                        db,
                        frozenset({ref.id}),
                        revision,
                        current_revision=policy.revision,
                    )
                }
                enabled_ids = policy.effective_ids - changed_ids
            definitions = _available_tools(execution, enabled_ids)
            if pending_assistant is not None:
                from ..providers.openai_schemas import (
                    normalize_arguments,
                    unique_json_object,
                    wire_schema,
                )

                call = pending_assistant.tool_calls[0]
                definition = next((tool for tool in definitions if tool.name == call.name), None)
                saved = [item for item in pending_assistant.continuation.items if item["type"] == "function_call"]
                if definition is None or len(saved) != 1:
                    raise ProviderError("unsupported_capability")
                try:
                    raw = json.loads(saved[0]["arguments"], object_pairs_hook=unique_json_object,
                                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    if not isinstance(raw, dict):
                        raise TypeError("invalid saved call")
                    _, strict = wire_schema(definition.parameters)
                    left = normalize_arguments(definition.parameters, raw, strict=strict)
                    right = normalize_arguments(definition.parameters, call.arguments, strict=strict)
                    if left != right:
                        raise ValueError("saved call does not match continuation")
                except (KeyError, TypeError, ValueError) as invalid:
                    raise ProviderError("invalid_response", cause=invalid) from invalid
            request = ModelRequest(
                messages=tuple(messages),
                tools=definitions,
                max_output_tokens=min(
                    model_config.limits.max_output_tokens,
                    execution.loop_limits.max_output_tokens_per_request,
                ),
            )
            if pending_assistant is not None:
                result = ProviderTurnResult(
                    selected_profile_id=model_config.primary.id, finish_reason="tool_calls",
                    text=pending_assistant.content, tool_calls=pending_assistant.tool_calls,
                    continuation=pending_assistant.continuation,
                )
            else:
                result = asyncio.run(run_model_turn(
                    request,
                    model_config,
                    invocation.control,
                    invocation.recorder,
                    authorize_profile=(
                        invocation.authorize_profile or authorize_current_profile
                    ),
                    budget=invocation.budget,
                    model_turn_ordinal=model_turn_ordinal,
                ))
            usage = result.usage.model_dump(mode="json") if result.usage else None

            if result.tool_calls:
                if result.finish_reason != "tool_calls" or len(result.tool_calls) != 1:
                    raise ProviderError(
                        "invalid_response",
                        safe_message="The model returned an unsupported or incomplete tool request.",
                    )
                assistant = ChatMessage(
                    role="assistant",
                    content=result.text,
                    tool_calls=result.tool_calls,
                    continuation=result.continuation,
                )
                if pending_assistant is None:
                    _append_transcript(invocation, model_turn_ordinal, assistant)
                    messages.append(assistant)
                pending_assistant = None
                call = result.tool_calls[0]
                logical_call_id = f"model:{model_turn_ordinal}:call:0"
                invocation.budget.admit_tool(logical_call_id)
                ref = next(
                    (
                        item
                        for item in execution.allowed_capabilities
                        if item.id == call.name
                    ),
                    None,
                )
                if ref is None or call.name not in {tool.name for tool in definitions}:
                    raise ProviderError(
                        "unsupported_capability",
                        safe_message="The model requested a tool that is not currently authorized.",
                    )
                invocation.control.raise_if_stopped()
                try:
                    published = asyncio.run(
                        gateway.execute_tool(
                            claim=invocation.claim,
                            context=invocation.context,
                            logical_call_id=logical_call_id,
                            ref=ref,
                            arguments=call.arguments,
                            inputs=tuple(execution.selected_artifacts),
                            control=invocation.control,
                        )
                    )
                except HTTPException as refused:
                    raise ProviderError(
                        "unsupported_capability",
                        safe_message="The tool authority check rejected this model request.",
                        cause=refused,
                    ) from None
                except ProviderError:
                    raise
                except Exception as refused:  # noqa: BLE001 - keep SDK/storage details private.
                    raise ProviderError(
                        "execution_interrupted",
                        safe_message="The tool execution could not be completed safely.",
                        cause=refused,
                    ) from None

                try:
                    published = PublishedToolResult.model_validate(published)
                except (TypeError, ValueError, ValidationError) as invalid:
                    raise ProviderError(
                        "invalid_response",
                        safe_message="The tool returned a result this conversation cannot use.",
                        cause=invalid,
                    ) from invalid
                tool_message = ChatMessage(
                    role="tool",
                    name=call.name,
                    tool_call_id=call.provider_call_id,
                    content=_result_message(
                        published, execution.limits.max_model_result_bytes
                    ),
                )
                _append_transcript(invocation, model_turn_ordinal, tool_message)
                messages.append(tool_message)
                failure = _failure_for_tool(published)
                if failure is not None:
                    raise failure
                continue

            if result.completed:
                assistant = ChatMessage(role="assistant", content=result.text, continuation=result.continuation)
                _append_transcript(invocation, model_turn_ordinal, assistant)
                return {
                    "assistant_response": result.text,
                    "usage": usage,
                    "finish_reason": result.finish_reason,
                }
            if result.finish_reason == "length":
                raise ProviderError("output_limit_exceeded")
            if result.finish_reason == "refusal":
                raise ProviderError("model_refused")
            raise ProviderError(
                "invalid_response",
                safe_message="The model stopped before completing a final answer.",
            )

        raise ProviderError("budget_exceeded")

    builder = StateGraph(AgentState)
    builder.add_node("agent", execute)
    builder.set_entry_point("agent")
    builder.add_edge("agent", END)
    return builder.compile(checkpointer=checkpointer)
