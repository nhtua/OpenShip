"""Durable worker with deterministic model and search transports for chat-v2."""

from __future__ import annotations

import os
from pathlib import Path

import src.openship.providers.runner as provider_runner
from src.openship.capabilities.contracts import canonical_json
from src.openship.providers.contracts import ProviderEvent, ToolCall
from src.openship.tools.search import http as search_http
from tests.full_stack.synthetic_worker import _synthetic_transport


def _record_model_turn(profile, request, tool_name: str | None) -> None:
    record = {
        "profile_id": profile.id,
        "model": profile.model,
        "message_count": len(request.messages),
        "tool_names": [tool.name for tool in request.tools],
        "tool_result_count": sum(message.role == "tool" for message in request.messages),
        "selected_tool": tool_name,
    }
    path = Path(os.environ["SYNTHETIC_MODEL_CALL_LOG"])
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, canonical_json(record) + b"\n")
    finally:
        os.close(descriptor)


class SyntheticToolChatAdapter:
    """Emulate the one tool request and final answer without a model API."""

    async def stream(self, request, config, control):
        control.raise_if_stopped()
        prior_results = [message for message in request.messages if message.role == "tool"]
        if request.tools and not prior_results:
            tool = next(
                (item for item in request.tools if item.name == "brave_web_search"),
                request.tools[0],
            )
            _record_model_turn(config, request, tool.name)
            yield ProviderEvent(
                type="tool_call_ready",
                tool_call=ToolCall(
                    provider_call_id="synthetic-model-call-0",
                    name=tool.name,
                    arguments={
                        "query": "synthetic automatic chat search",
                        "max_results": 1,
                    },
                ),
            )
            yield ProviderEvent(
                type="stream_finished",
                reason="tool_calls",
                actual_model=config.model,
            )
            return

        _record_model_turn(config, request, None)
        yield ProviderEvent(
            type="text_delta",
            text="The synthetic search returned one result.",
        )
        yield ProviderEvent(
            type="stream_finished",
            reason="stop",
            actual_model=config.model,
        )


def _synthetic_adapter(profile):
    if profile.adapter == "openai":
        return SyntheticToolChatAdapter()
    return _original_build_adapter(profile)


_original_build_adapter = provider_runner.build_adapter


def main() -> None:
    # Only the two external boundaries are synthetic; the API, queue, worker,
    # graph, gateway, database, artifact store and transcript remain real.
    search_http.PinnedHttpTransport = _synthetic_transport
    provider_runner.build_adapter = _synthetic_adapter
    from src.openship.runs.worker import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()
