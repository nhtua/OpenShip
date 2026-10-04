"""LangGraph chat graph with PostgreSQL checkpointing and bounded model step.

This module defines a simple chat graph with a single model step node.
State carries only serializable chat inputs/output and run ID, never
API credentials.
"""

import json
from dataclasses import dataclass

from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional

from ..chat.llm import chat
from ..config import settings


class ChatState(TypedDict):
    """Graph state schema. Carries only serializable data."""
    messages: list[dict]
    run_id: str
    conversation_id: str
    assistant_response: str
    usage: Optional[dict]


@dataclass
class ModelResult:
    """Result of executing a model turn."""
    assistant_response: str
    usage: Optional[dict] = None
    error: Optional[str] = None


class ModelError(Exception):
    """Raised when the provider returns an error."""
    pass


class ConfigurationError(Exception):
    """Raised when configuration is missing or invalid."""
    pass


def model_step(state: ChatState) -> dict:
    """Execute one model turn.

    Calls the OpenAI chat API with bounded output budget.
    Returns only the assistant response and usage info.
    """
    messages = state["messages"]
    run_id = state["run_id"]

    # Validate API key is configured
    if not settings.openai_api_key:
        raise ConfigurationError(
            "OPENAI_API_KEY is not configured. "
            "Set the OPENAI_API_KEY environment variable."
        )

    # Bounded output budget
    max_tokens = getattr(settings, "model_max_tokens", 4000)

    try:
        response = chat(messages, max_tokens=max_tokens)
    except Exception as e:
        raise ModelError(f"Provider error: {e}")

    # Collect response and capture usage from provider metadata
    chunks = []
    prompt_tokens = None
    completion_tokens = None
    total_tokens = None
    try:
        for chunk in response:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    chunks.append(delta.content)
            # Usage is provided in the final chunk of the stream
            if chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens
                completion_tokens = chunk.usage.completion_tokens
                total_tokens = chunk.usage.total_tokens
    except Exception as e:
        raise ModelError(f"Stream error: {e}")

    assistant_response = "".join(chunks).strip()

    # Build usage info from provider metadata only — never estimated
    # Values come only from what the provider returns
    usage = None
    has_any = (
        prompt_tokens is not None
        or completion_tokens is not None
        or total_tokens is not None
    )
    if has_any:
        # If any value is missing, mark as estimated (incomplete)
        all_present = (
            prompt_tokens is not None
            and completion_tokens is not None
            and total_tokens is not None
        )
        usage = {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "provider_model": None,  # Not available in stream response
            "estimated": not all_present,
        }

    return {
        "assistant_response": assistant_response,
        "usage": usage,
    }


def build_chat_graph(checkpointer=None):
    """Build the chat graph.

    Args:
        checkpointer: Optional checkpoint saver. If None, uses in-memory.

    Returns:
        CompiledStateGraph.
    """
    builder = StateGraph(ChatState)

    builder.add_node("model", model_step)
    builder.set_entry_point("model")
    builder.add_edge("model", END)

    return builder.compile(checkpointer=checkpointer)


def execute_model_turn(run_id: str, thread_id: str, fence: int, state: ChatState) -> ModelResult:
    """Execute a model turn and return the result.

    Args:
        run_id: The run ID for this turn.
        thread_id: The thread ID for checkpointing (reserved for future
            checkpointer integration; not currently used).
        fence: The fence value for optimistic locking (reserved for future
            checkpointer integration; not currently used).
        state: The input state with messages.

    Returns:
        ModelResult with assistant response, usage, and any error.
    """
    try:
        result = model_step(state)
        return ModelResult(
            assistant_response=result["assistant_response"],
            usage=result["usage"],
        )
    except (ModelError, ConfigurationError) as e:
        return ModelResult(
            assistant_response="",
            error=str(e),
        )
