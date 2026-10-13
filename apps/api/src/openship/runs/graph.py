"""LangGraph chat graph: one bounded model turn through the run's own snapshot.

State carries only serializable chat inputs, the run's pinned configuration,
and the canonical result. The runtime control, the attempt recorder, and every
provider client are injected per invocation and never reach a checkpoint.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, StateGraph

from ..providers.contracts import ChatMessage, ModelRequest, RunModelConfig
from ..providers.errors import SAFE_MESSAGES, ProviderError
from ..providers.runner import run_model_turn
from .service import parse_run_model_config


class ChatState(TypedDict):
    """Graph state schema. Carries only serializable data.

    The run's pinned configuration is part of the state because it is frozen,
    non-secret, and is what makes a resumed thread run the same model. The
    runtime control, the attempt recorder, and every provider client are
    injected per invocation and never reach a checkpoint.
    """
    messages: list[dict]
    run_id: str
    conversation_id: str
    model_configuration: str
    assistant_response: str
    usage: dict | None
    finish_reason: str


@dataclass
class ModelResult:
    """Result of executing a model turn."""
    assistant_response: str
    usage: dict | None = None
    error: str | None = None
    error_code: str | None = None
    finish_reason: str | None = None


#: A finish reason that is not an answer, and the normalized failure it shows.
FINISH_ERRORS = {
    "length": "output_limit_exceeded",
    "refusal": "model_refused",
    "tool_calls": "unsupported_capability",
}


def _request_of(state: ChatState, config: RunModelConfig) -> ModelRequest:
    """The conversation as contract messages, in the order it was sent."""
    try:
        return ModelRequest(
            messages=tuple(
                ChatMessage(role=message["role"], content=message["content"])
                for message in state["messages"]
            ),
            max_output_tokens=config.limits.max_output_tokens,
        )
    except (KeyError, TypeError, ValueError) as unmapped:
        raise ProviderError(
            "invalid_request",
            safe_message="the conversation carries messages this model cannot be sent",
        ) from unmapped


def model_step(state: ChatState, invocation=None) -> dict:
    """Execute one model turn through the configuration this run pinned.

    ``invocation`` supplies the runtime control and attempt recorder this
    invocation should use; it is never stored in state. Without one, the pair
    is resolved from the run in this installation's database, which is what a
    direct caller (a test, a one-off script) wants.
    """
    config = parse_run_model_config(state.get("model_configuration"), state["run_id"])
    request = _request_of(state, config)
    control, recorder = invocation() if invocation else _default_invocation(state)

    # The model turn is asynchronous; this node owns the event loop so the
    # worker's own thread stays synchronous.
    result = asyncio.run(run_model_turn(request, config, control, recorder))

    usage = result.usage.model_dump(mode="json") if result.usage is not None else None
    return {
        "assistant_response": result.text,
        "usage": usage,
        "finish_reason": result.finish_reason,
    }


def _invocation_for(run_id, fence: int):
    """The control and recorder one turn should use, for a run this worker owns."""
    from ..database.session import get_db
    from ..providers.attempts import DatabaseAttemptRecorder
    from ..providers.control import InvocationControl

    db = get_db()
    return InvocationControl(), DatabaseAttemptRecorder(lambda: db, _run_id(run_id), fence)


def _default_invocation(state: ChatState):
    """The control and recorder for a run, read from this installation's database.

    A worker supplies its own factory so the control carries its deadline and
    stop signal and the recorder its claimed fence; this is the fallback for a
    direct caller, which owns no supervision of its own.
    """
    return _invocation_for(state["run_id"], _current_fence(state["run_id"]))


def _run_id(raw) -> uuid.UUID:
    """A run ID this installation knows, or a safe configuration error."""
    try:
        return uuid.UUID(raw)
    except (TypeError, ValueError):
        raise ProviderError(
            "configuration_error",
            safe_message=f"run {raw} is not a run this installation knows",
        ) from None


def _current_fence(raw) -> int:
    """The fence a run holds now, for a caller that claimed no lease of its own."""
    from ..database.session import get_db
    from ..runs.models import Run

    run = get_db().get(Run, _run_id(raw))
    if run is None:
        raise ProviderError(
            "configuration_error",
            safe_message=f"run {raw} does not exist in this installation",
        )
    return run.fence


def build_chat_graph(checkpointer=None, invocation_factory=None):
    """Build the chat graph.

    Args:
        checkpointer: Optional checkpoint saver. If None, uses in-memory.
        invocation_factory: Supplies the runtime control and attempt recorder
            for one invocation. It is called per model step, so neither object
            is ever serialized into a checkpoint.

    Returns:
        CompiledStateGraph.
    """
    builder = StateGraph(ChatState)

    factory = invocation_factory or _default_invocation
    builder.add_node("model", lambda state: model_step(state, factory))
    builder.set_entry_point("model")
    builder.add_edge("model", END)

    return builder.compile(checkpointer=checkpointer)


def execute_model_turn(
    run_id: str,
    thread_id: str,
    fence: int,
    state: ChatState,
    invocation_factory=None,
) -> ModelResult:
    """Execute a model turn and return the result.

    Args:
        run_id: The run ID for this turn.
        thread_id: The thread ID for checkpointing (reserved for future
            checkpointer integration; not currently used).
        fence: The fence value for optimistic locking (reserved for future
            checkpointer integration; not currently used).
        state: The input state with messages.
        invocation_factory: Supplies the control and recorder to use; defaults
            to the run's own, read from this installation's database.

    Returns:
        ModelResult with assistant response, usage, and any error.
    """
    factory = invocation_factory or (lambda: _invocation_for(_run_id(run_id), fence))
    try:
        values = model_step(state, factory)
    except ProviderError as refused:
        return ModelResult(assistant_response="", error=refused.safe_message, error_code=refused.code)

    code = _unpublishable(values)
    if code is not None:
        return ModelResult(assistant_response="", error=SAFE_MESSAGES[code], error_code=code)

    return ModelResult(
        assistant_response=values["assistant_response"],
        usage=values["usage"],
        finish_reason=values["finish_reason"],
    )


def _unpublishable(values: dict) -> str | None:
    """The normalized failure a turn produced instead of a publishable answer.

    Only a clean stop with nonempty text may publish an assistant answer;
    partial text stays an internal result and never enters history.
    """
    if values["finish_reason"] == "stop" and values["assistant_response"]:
        return None
    return FINISH_ERRORS.get(values["finish_reason"], "invalid_response")
