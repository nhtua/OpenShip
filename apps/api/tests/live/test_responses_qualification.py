"""One opt-in Responses qualification: real text, one real tool call, one real result.

This lane exists to turn the OpenAI Responses claim into evidence, so it is
stricter than a smoke test: selecting it names the profile it qualifies, and a
selected lane that cannot meet its prerequisites fails rather than skipping.
It spends real requests, so nothing here runs unless an operator opts in.

The breaks these catch: a turn that reached an endpoint the profile never
declared, a turn that let the provider store the exchange, a tool call that
came back without the private state the next turn needs, and a continuation
that asked the provider to remember anything.
"""

import os
import time

import pytest

from src.openship.config import settings
from src.openship.providers.contracts import ChatMessage, ModelRequest, ToolDefinition
from src.openship.providers.control import InvocationControl
from src.openship.providers.registry import build_adapter, load_profiles
from src.openship.providers.runner import run_model_turn

PROFILE_ID_ENV = "OPENSHIP_LIVE_PROFILE_ID"
SELECTOR_ENV = "OPENSHIP_LIVE_QUALIFICATION"
SELECTOR = "responses-tool-call"

TEXT_PROMPT = "Reply with the single word ready."
TOOL_PROMPT = "Say the word horizon using the echo tool."
TOOL_NAME = "echo_word"
TOOL_OUTPUT = "horizon"
OUTPUT_BOUND = 64


def _selected_profile_id() -> str:
    """The profile this opt-in run qualifies; an unselected lane skips, a selected one never does."""
    selector = {part.strip() for part in os.environ.get(SELECTOR_ENV, "").split(",")}
    if SELECTOR not in selector:
        pytest.skip(
            f"opt-in: set {SELECTOR_ENV}={SELECTOR} and {PROFILE_ID_ENV} to qualify "
            "a Responses profile against its real destination (it can incur provider charges)"
        )
    profile_id = os.environ.get(PROFILE_ID_ENV, "").strip()
    if not profile_id:
        raise AssertionError(
            f"{SELECTOR_ENV}={SELECTOR} selected but {PROFILE_ID_ENV} names no profile"
        )
    return profile_id


def _pinned_profile(monkeypatch):
    """The selected profile as a pinned config, or a reason this lane cannot run.

    The shared test installation declares synthetic profiles; an operator
    selecting this lane names the real one in ``MODEL_PROFILES_JSON``, so the
    lane re-declares exactly that and nothing else.
    """
    profile_id = _selected_profile_id()
    declared = os.environ.get("MODEL_PROFILES_JSON", "")
    assert declared, (
        f"{SELECTOR_ENV}={SELECTOR} names {profile_id} but MODEL_PROFILES_JSON declares nothing"
    )
    monkeypatch.setattr("src.openship.config.settings.model_profiles_json", declared)
    monkeypatch.setattr("src.openship.config.settings.model_default_profile_id", profile_id)
    registry = load_profiles(settings)
    availability = registry.availability(profile_id)
    assert availability.available, (
        f"the installation cannot serve {profile_id}: {availability.reason} - "
        f"{availability.explanation}"
    )
    profile = registry.get(profile_id)
    assert profile.api_version == "responses", (
        f"this lane qualifies Responses; the selected profile is pinned to {profile.api_version}"
    )
    return profile, registry.pin_selection(profile_id, ())


def _watch(adapter, seen, sent):
    """Watch the one place a real turn builds its client and issues its call."""
    factory = adapter.build_client

    def build(config):
        seen.append(config.endpoint)
        client = factory(config)
        create = client.responses.create

        def record_and_create(**kwargs):
            sent.append(kwargs)
            return create(**kwargs)

        client.responses.create = record_and_create
        return client

    adapter._client_factory = build


class AttemptLog:
    """What the qualified turns opened and closed, kept in memory.

    The durable record belongs to a run's transaction and is qualified against
    PostgreSQL in ``tests/integration``; a live turn has no run to write into.
    """

    def __init__(self):
        self.opened = []
        self.closed = []

    def start(self, profile, reason, reserved_cost_usd=None):
        self.opened.append(profile.id)
        return len(self.opened)

    def finish(self, attempt_id, outcome, usage=None, error_code=None, actual_model=None):
        self.closed.append(actual_model)


@pytest.mark.asyncio
@pytest.mark.provider_live
async def test_responses_text_turn_is_private_at_the_declared_destination(monkeypatch):
    """One real text turn, pinned to Responses, with nothing stored upstream."""
    profile, config = _pinned_profile(monkeypatch)
    declared = profile.endpoint

    seen: list[str] = []
    sent: list[dict] = []

    def build_and_watch(profile):
        adapter = build_adapter(profile)
        _watch(adapter, seen, sent)
        return adapter

    monkeypatch.setattr("src.openship.providers.runner.build_adapter", build_and_watch)

    log = AttemptLog()
    control = InvocationControl(deadline=time.monotonic() + config.limits.run_timeout_seconds)
    result = await run_model_turn(
        ModelRequest(
            messages=(ChatMessage(role="user", content=TEXT_PROMPT),),
            max_output_tokens=min(OUTPUT_BOUND, config.limits.max_output_tokens),
        ),
        config,
        control,
        log,
    )

    assert len(seen) == 1 and seen[0] == declared, f"the turn reached {seen}, not {declared}"
    assert len(sent) == 1, "the qualification issued more than one request"
    request = sent[0]
    assert request["store"] is False, "a qualified Responses turn may not store the exchange"
    assert request["parallel_tool_calls"] is False
    assert request["include"] == ["reasoning.encrypted_content"]
    assert request["reasoning"] == {"effort": profile.adapter_options.reasoning_effort}
    assert request["stream"] is True

    assert result.finish_reason == "stop", f"the turn ended as {result.finish_reason}"
    assert result.text, "the turn claimed success without an answer"
    assert result.actual_model == profile.model or result.actual_model in profile.qualified_model_aliases
    if result.usage is not None:
        assert result.usage.input_tokens is not None
        assert result.usage.output_tokens is not None


@pytest.mark.asyncio
@pytest.mark.provider_live
async def test_responses_tool_call_continues_statelessly(monkeypatch):
    """A real function call, a local result, and a real second turn from saved state."""
    profile, config = _pinned_profile(monkeypatch)
    declared = profile.endpoint
    if not profile.capabilities.tool_calls:
        raise AssertionError(f"the selected profile {profile.id} declares no tool calls")

    definition = ToolDefinition(
        name=TOOL_NAME,
        description="Return the fixed word this test accepts.",
        parameters={
            "type": "object",
            "properties": {"word": {"type": "string"}},
            "required": ["word"],
            "additionalProperties": False,
        },
    )
    seen: list[str] = []
    sent: list[dict] = []

    def build_and_watch(profile):
        adapter = build_adapter(profile)
        _watch(adapter, seen, sent)
        return adapter

    monkeypatch.setattr("src.openship.providers.runner.build_adapter", build_and_watch)

    log = AttemptLog()
    control = InvocationControl(deadline=time.monotonic() + config.limits.run_timeout_seconds)
    first = await run_model_turn(
        ModelRequest(
            messages=(ChatMessage(role="user", content=TOOL_PROMPT),),
            tools=(definition,),
            max_output_tokens=min(256, config.limits.max_output_tokens),
        ),
        config,
        control,
        log,
    )
    assert first.finish_reason == "tool_calls", f"the first turn ended as {first.finish_reason}"
    assert len(first.tool_calls) == 1, "the qualification accepts one call per turn"
    call = first.tool_calls[0]
    assert call.name == TOOL_NAME, f"the model asked for {call.name}"
    assert first.continuation is not None, "a Responses turn must carry its private state"
    assert any(item["type"] == "function_call" for item in first.continuation.items)

    second = await run_model_turn(
        ModelRequest(
            messages=(
                ChatMessage(role="user", content=TOOL_PROMPT),
                ChatMessage(
                    role="assistant",
                    content=first.text,
                    tool_calls=first.tool_calls,
                    continuation=first.continuation,
                ),
                ChatMessage(
                    role="tool",
                    name=TOOL_NAME,
                    tool_call_id=call.provider_call_id,
                    content=TOOL_OUTPUT,
                ),
            ),
            tools=(definition,),
            max_output_tokens=min(256, config.limits.max_output_tokens),
        ),
        config,
        control,
        log,
    )
    assert second.finish_reason == "stop", f"the continued turn ended as {second.finish_reason}"
    assert second.text, "the continued turn produced no answer"

    assert seen == [declared, declared], f"the journey reached {seen}"
    assert len(sent) == 2, "the journey issued more than two requests"
    replay = sent[1]
    assert replay["store"] is False
    assert "previous_response_id" not in replay, "the continuation leaned on provider-stored state"
    assert any(
        item.get("type") == "function_call" and item.get("call_id") == call.provider_call_id
        for item in replay["input"]
    ), "the saved provider call was not replayed"
    assert any(
        item.get("type") == "function_call_output" and item.get("call_id") == call.provider_call_id
        for item in replay["input"]
    ), "the tool result was not replayed with the saved state"
