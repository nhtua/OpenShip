"""Bounded chat-v2 graph that alternates model turns and trusted tools."""

from __future__ import annotations

import asyncio
import copy
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
    """Render a tool result for the model, inside `max_bytes`.

    The durable operation result keeps every byte; only this model-visible
    copy is bounded. A tool may legitimately commit a payload at its own
    ceiling - the workspace read pairs ``max_read_bytes`` with
    ``max_model_result_bytes`` (`capabilities/catalog.py:522-524`) - so the
    envelope added here can overflow a page the gateway already allowed.
    Aborting there lost the step's outcome and left the model no way to
    continue, so instead the largest strings of the tool's own payload are
    shortened, each saying so and pointing at the ``next_offset`` the result
    already carries.
    """
    payload = result.model_dump(mode="json")
    encoded = _canonical_result(payload)
    if len(encoded.encode("utf-8")) <= max_bytes:
        return encoded
    bounded = _bounded_result_payload(payload, max_bytes)
    if bounded is None:
        raise ProviderError("output_limit_exceeded")
    return bounded


def _canonical_result(payload: dict) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _page_pointer(payload: dict) -> str | None:
    """Where the tool says to read next, when its contract carries one."""
    inner = payload.get("result")
    if not isinstance(inner, dict):
        return None
    offset = inner.get("next_offset")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset <= 0:
        return None
    return f"read the next page at next_offset={offset}"


def _truncation_note(total_bytes: int, kept_bytes: int, pointer: str | None) -> str:
    guide = pointer if pointer is not None else "ask again for a smaller page"
    return (
        f" [… OpenShip truncation: {kept_bytes} of {total_bytes} bytes kept;"
        f" {guide}; the whole result stays recorded for this operation …]"
    )


def _string_leaves(node: Any, path: tuple[str | int, ...] = ()) -> list[tuple[tuple[str | int, ...], str]]:
    if isinstance(node, dict):
        found = []
        for key, value in node.items():
            found.extend(_string_leaves(value, (*path, key)))
        return found
    if isinstance(node, list):
        found = []
        for index, value in enumerate(node):
            found.extend(_string_leaves(value, (*path, index)))
        return found
    if isinstance(node, str):
        return [(path, node)]
    return []


def _leaf_at(node: Any, path: tuple[str | int, ...]) -> Any:
    for part in path[:-1]:
        node = node[part]
    return node[path[-1]]


def _replace_leaf(node: Any, path: tuple[str | int, ...], value: str) -> None:
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value


def _bounded_result_payload(payload: dict, max_bytes: int) -> str | None:
    """Shorten the tool payload's largest strings until the message fits.

    The envelope fields (status, IDs, artifact refs) stay intact: only the
    tool's own payload is shortened, so the model never loses an outcome or a
    pointer it needs to continue. Each pass is a binary search for the longest
    excerpt that still fits, so the copy is as large as the budget allows.
    """
    data = copy.deepcopy(payload)
    inner = data.get("result")
    if not isinstance(inner, dict):
        return None
    pointer = _page_pointer(payload)
    leaves = sorted(
        _string_leaves(inner),
        key=lambda item: (-len(item[1].encode("utf-8")), [str(part) for part in item[0]]),
    )
    for path, _original in leaves:
        current = _leaf_at(data["result"], path)
        if not isinstance(current, str):
            continue
        total = len(current.encode("utf-8"))
        _replace_leaf(data["result"], path, _truncation_note(total, 0, pointer))
        if _encoded_bytes(data) > max_bytes:
            continue
        low, high = 0, total
        while low < high:
            middle = (low + high + 1) // 2
            prefix = _clip_to_bytes(current, middle)
            _replace_leaf(
                data["result"],
                path,
                prefix + _truncation_note(total, len(prefix.encode("utf-8")), pointer),
            )
            if _encoded_bytes(data) <= max_bytes:
                low = middle
            else:
                high = middle - 1
        prefix = _clip_to_bytes(current, low)
        _replace_leaf(
            data["result"],
            path,
            prefix + _truncation_note(total, len(prefix.encode("utf-8")), pointer),
        )
        encoded = _canonical_result(data)
        if len(encoded.encode("utf-8")) <= max_bytes:
            return encoded
    return None


def _encoded_bytes(data: dict) -> int:
    return len(_canonical_result(data).encode("utf-8"))


def _clip_to_bytes(value: str, budget: int) -> str:
    """The longest whole-character prefix that costs at most `budget` bytes."""
    used = 0
    for index, char in enumerate(value):
        cost = len(char.encode("utf-8"))
        if used + cost > budget:
            return value[:index]
        used += cost
    return value


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
