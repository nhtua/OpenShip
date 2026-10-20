"""One live OpenAI tool-call loop, opt-in and destination-pinned."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime

import pytest

from src.openship.capabilities.contracts import (
    AuthorizationContext,
    ExecutionConfig,
    ToolInvocationContext,
)
from src.openship.config import settings
from src.openship.providers.contracts import (
    ChatMessage,
    ModelRequest,
    ToolDefinition,
)
from src.openship.providers.control import InvocationControl
from src.openship.providers.credentials import (
    credential_binding_present,
    credential_value,
)
from src.openship.providers.registry import load_profiles
from src.openship.providers.runner import run_model_turn
from src.openship.tools.search.brave import search_brave
from src.openship.tools.search.contracts import SearchArguments
from src.openship.tools.search.tavily import search_tavily
from tests.live.qualification import (
    require_search_tool,
    require_tool_profile,
    selected_search_tool_id,
)

PROFILE_ID_ENV = "OPENSHIP_LIVE_TOOL_PROFILE_ID"
SEARCH_TOOL_ID_ENV = "OPENSHIP_LIVE_SEARCH_TOOL_ID"
LIVE_QUERY = "OpenShip web search provider qualification"


class _AttemptLog:
    def __init__(self):
        self.opened = []
        self.closed = []

    def start(self, profile, reason, reserved_cost_usd=None):
        self.opened.append((profile.id, reason))
        return len(self.opened)

    def finish(self, attempt_id, outcome, usage=None, error_code=None, actual_model=None):
        self.closed.append((attempt_id, outcome, error_code, actual_model))


@pytest.fixture(autouse=True)
def configured_model_profiles():
    """Use the explicitly selected installed profile, not test fixtures."""
    yield


def _one_attempt(config):
    """Bound each provider turn to one attempt and no fallback destination."""
    assert not config.fallbacks
    return config.model_copy(
        update={
            "limits": config.limits.model_copy(
                update={
                    "max_attempts": 1,
                    "run_timeout_seconds": 60.0,
                    "request_timeout_seconds": min(
                        45.0, config.limits.request_timeout_seconds
                    ),
                }
            )
        }
    )


def _check_served_model(profile, actual_model):
    assert actual_model, "qualified model turn did not report its served model"
    assert actual_model == profile.model or actual_model in profile.qualified_model_aliases, (
        "provider served a model alias that the selected profile did not qualify"
    )


@pytest.mark.asyncio
@pytest.mark.provider_live
async def test_openai_profile_calls_one_exact_search_tool_and_finishes(
    record_property, monkeypatch
):
    profile_id = os.environ.get(PROFILE_ID_ENV, "").strip()
    tool_id = selected_search_tool_id(os.environ.get(SEARCH_TOOL_ID_ENV))
    if not profile_id and tool_id is None:
        pytest.skip(
            "opt-in: set OPENSHIP_LIVE_TOOL_PROFILE_ID and "
            "OPENSHIP_LIVE_SEARCH_TOOL_ID for one real model/tool turn"
        )
    assert profile_id, "OPENSHIP_LIVE_TOOL_PROFILE_ID is required for model/tool qualification"
    assert tool_id, "OPENSHIP_LIVE_SEARCH_TOOL_ID is required for model/tool qualification"

    registry = load_profiles(settings)
    availability = registry.availability(profile_id)
    assert availability.available, (
        f"selected live profile is unavailable: {availability.reason or 'unknown'}"
    )
    profile = require_tool_profile(
        registry.get(profile_id), credential_check=credential_binding_present
    )
    manifest = require_search_tool(
        tool_id,
        enabled_ids=settings.enabled_tool_ids,
        credential_check=credential_binding_present,
    )
    tool_definition = ToolDefinition(
        name=manifest.id,
        description=f"Search the web using {manifest.id}.",
        parameters=manifest.input_schema,
    )
    config = _one_attempt(registry.pin_selection(profile_id, ()))
    attempt_log = _AttemptLog()
    contacted: list[str] = []
    from src.openship.providers import runner

    original_build_adapter = runner.build_adapter

    def build_and_watch(selected_profile):
        contacted.append(selected_profile.endpoint)
        return original_build_adapter(selected_profile)

    monkeypatch.setattr(runner, "build_adapter", build_and_watch)
    qualified_at = datetime.now(UTC)
    deadline = time.monotonic() + 120
    control = InvocationControl(deadline=deadline)
    first = await run_model_turn(
        ModelRequest(
            messages=(
                ChatMessage(
                    role="user",
                    content=(
                        "Call the provided tool exactly once with query "
                        f"{LIVE_QUERY!r} and max_results 1. Then report the result count."
                    ),
                ),
            ),
            tools=(tool_definition,),
            max_output_tokens=min(96, profile.capabilities.max_output_tokens),
        ),
        config,
        control,
        attempt_log,
    )
    assert first.finish_reason == "tool_calls"
    assert len(first.tool_calls) == 1, "the selected model did not make exactly one tool call"
    call = first.tool_calls[0]
    assert call.name == tool_id, "the selected model requested a tool outside the allowlist"
    arguments = SearchArguments.model_validate(call.arguments)
    assert arguments.query == LIVE_QUERY
    assert arguments.max_results == 1

    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            action="invoke_tool",
            target_id=tool_id,
        ),
        execution=ExecutionConfig(graph_kind="tool", graph_version=1),
        credential_resolver=credential_value,
    )
    handler = {
        "brave_web_search": search_brave,
        "tavily_web_search": search_tavily,
    }[tool_id]
    observation = await handler(
        arguments,
        context,
        InvocationControl(deadline=deadline),
    )
    assert observation.tool.id == manifest.id
    assert observation.tool.manifest_digest == manifest.manifest_digest
    assert observation.returned_count <= 1

    final = await run_model_turn(
        ModelRequest(
            messages=(
                ChatMessage(
                    role="user",
                    content=(
                        "Call the provided tool exactly once with query "
                        f"{LIVE_QUERY!r} and max_results 1. Then report the result count."
                    ),
                ),
                ChatMessage(
                    role="assistant",
                    content=first.text,
                    tool_calls=first.tool_calls,
                ),
                ChatMessage(
                    role="tool",
                    name=call.name,
                    tool_call_id=call.provider_call_id,
                    content=json.dumps(
                        observation.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            # The paired transcript result keeps the original call valid,
            # while omitting tools prevents a second dispatch.
            max_output_tokens=min(96, profile.capabilities.max_output_tokens),
        ),
        config,
        control,
        attempt_log,
        model_turn_ordinal=1,
    )
    _check_served_model(profile, first.actual_model)
    _check_served_model(profile, final.actual_model)
    assert final.finish_reason == "stop"
    assert final.text.strip()
    assert contacted == [profile.endpoint, profile.endpoint]
    assert len(attempt_log.opened) == 2
    assert len(attempt_log.closed) == 2
    assert [entry[1] for entry in attempt_log.closed] == ["succeeded", "succeeded"]

    record_property("profile_id", profile.id)
    record_property("adapter", profile.adapter)
    record_property("api_version", profile.api_version)
    record_property("requested_model", profile.model)
    record_property("served_model", final.actual_model or "")
    record_property("endpoint", profile.endpoint)
    record_property("fallback_destinations", 0)
    record_property("provider_attempts", len(attempt_log.opened))
    record_property("tool_id", manifest.id)
    record_property("tool_version", manifest.version)
    record_property("tool_manifest_digest", manifest.manifest_digest)
    record_property("tool_implementation_digest", manifest.implementation_digest)
    record_property("search_observation_id", "direct-handler:no-persisted-operation")
    record_property("qualified_at", qualified_at.isoformat())
