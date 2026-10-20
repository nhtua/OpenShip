"""Durable worker with deterministic model and search transports for chat-v2."""

from __future__ import annotations

import json
import os
from pathlib import Path

import src.openship.providers.runner as provider_runner
from src.openship.capabilities.contracts import canonical_json
from src.openship.providers.contracts import ProviderEvent, ToolCall
from src.openship.tools.search import http as search_http
from tests.full_stack.synthetic_worker import _synthetic_transport


def _record_model_turn(profile, request, tool_name: str | None, arguments: dict | None = None) -> None:
    record = {
        "profile_id": profile.id,
        "model": profile.model,
        "message_count": len(request.messages),
        "tool_names": [tool.name for tool in request.tools],
        "tool_result_count": sum(message.role == "tool" for message in request.messages),
        "selected_tool": tool_name,
        "arguments": arguments,
    }
    path = Path(os.environ["SYNTHETIC_MODEL_CALL_LOG"])
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, canonical_json(record) + b"\n")
    finally:
        os.close(descriptor)


class SyntheticToolChatAdapter:
    """Emulate search and exact saved-file continuation without a model API."""

    async def stream(self, request, config, control):
        control.raise_if_stopped()
        prior_results = [message for message in request.messages if message.role == "tool"]
        latest_user = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        selected = None
        arguments = None
        if request.tools and "read the saved artifact" in latest_user.lower() and not prior_results:
            selected = next((item for item in request.tools if item.name == "workspace_list_files"), None)
            arguments = {"limit": 20}
        elif prior_results and prior_results[-1].name == "workspace_list_files":
            listing = json.loads(prior_results[-1].content)["result"]
            items = listing.get("items", [])
            if items:
                selected = next((item for item in request.tools if item.name == "workspace_read_file"), None)
                arguments = {"artifact_id": items[0]["id"], "offset": 0, "max_bytes": 16384}
            else:
                _record_model_turn(config, request, None)
                yield ProviderEvent(
                    type="text_delta",
                    text="There are no saved files in this conversation.",
                )
                yield ProviderEvent(type="stream_finished", reason="stop", actual_model=config.model)
                return
        elif request.tools and not prior_results:
            selected = next(
                (item for item in request.tools if item.name == "brave_web_search"),
                request.tools[0],
            )
            arguments = {"query": "synthetic automatic chat search", "max_results": 1}

        if selected is not None:
            _record_model_turn(config, request, selected.name, arguments)
            yield ProviderEvent(
                type="tool_call_ready",
                tool_call=ToolCall(
                    provider_call_id=f"synthetic-model-call-{len(prior_results)}",
                    name=selected.name,
                    arguments=arguments or {},
                ),
            )
            yield ProviderEvent(
                type="stream_finished",
                reason="tool_calls",
                actual_model=config.model,
            )
            return

        _record_model_turn(config, request, None)
        if prior_results and prior_results[-1].name == "workspace_read_file":
            read_result = json.loads(prior_results[-1].content)["result"]
            text = (
                f"Read exact saved version {read_result['version_id']} with "
                f"SHA-256 {read_result['sha256']}: {read_result['content']}"
            )
        else:
            text = "The synthetic search returned one result."
        yield ProviderEvent(
            type="text_delta",
            text=text,
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
