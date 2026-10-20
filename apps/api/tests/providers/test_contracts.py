"""Provider-neutral contract tests.

Each test names the production break it catches: a missing validation, a
wrong default, a mutable snapshot, a fabricated usage number, or an error
that leaks the raw provider response.
"""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.openship.providers.contracts import (
    ChatMessage,
    ModelRequest,
    ProviderCapabilities,
    ProviderEvent,
    ProviderProfile,
    ProviderTurnResult,
    RunLimits,
    RunModelConfig,
    TokenUsage,
    pin_profile,
    validate_limits,
    validate_request,
)
from src.openship.providers.errors import ProviderError

OPENAI_PROFILE = {
    "id": "openai-default",
    "label": "OpenAI default",
    "adapter": "openai",
    "model": "gpt-4o",
    "endpoint": "https://api.openai.com/v1",
    "api_version": "chat-completions",
    "credential_ref": "OPENAI_API_KEY",
    "capabilities": {
        "streaming": True,
        "usage_reporting": True,
        "context_tokens": 128000,
        "max_output_tokens": 8192,
        "token_counting": True,
        "tool_calls": False,
        "structured_output": False,
        "attachment_types": [],
        "parallel_tool_calls": False,
    },
    "allowed_fallback_profile_ids": [],
    "qualified_model_aliases": [],
    "adapter_options": {
        "adapter": "openai",
        "output_limit_parameter": "max_tokens",
        "token_counter_id": "tiktoken:o200k_base",
    },
}


def test_contracts_reject_unknown_fields():
    """A stray key in a persisted snapshot must not be silently dropped."""
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content="hi", temperature=0.2)
    with pytest.raises(ValidationError):
        ModelRequest(messages=[], max_output_tokens=10, stream=True)
    with pytest.raises(ValidationError):
        ProviderProfile(**OPENAI_PROFILE, api_key="not-a-secret-here")


def test_model_specific_tool_reasoning_option_is_scoped_to_openai():
    """Only the OpenAI adapter accepts this declared Chat Completions option."""
    from src.openship.providers.contracts import AdapterOptions

    options = AdapterOptions(
        adapter="openai",
        output_limit_parameter="max_completion_tokens",
        tool_call_reasoning_effort="none",
    )
    assert options.tool_call_reasoning_effort == "none"

    with pytest.raises(ValidationError):
        AdapterOptions(
            adapter="openai_compatible",
            endpoint_implementation="llama-server",
            authentication="none",
            stream_usage_supported=False,
            output_limit_parameter="max_tokens",
            tool_call_reasoning_effort="none",
        )


def test_message_order_and_content_survive_a_json_round_trip():
    """Conversation turns keep role, text, and order — the input to every adapter."""
    messages = (
        ChatMessage(role="system", content="answer briefly"),
        ChatMessage(role="user", content="héllo — 日本語"),
        ChatMessage(role="assistant", content="ok"),
    )
    request = ModelRequest(messages=messages, max_output_tokens=100)

    restored = ModelRequest(**json.loads(request.model_dump_json()))

    assert [m.role for m in restored.messages] == ["system", "user", "assistant"]
    assert [m.content for m in restored.messages] == [
        "answer briefly",
        "héllo — 日本語",
        "ok",
    ]


def test_shared_budget_defaults_exist_before_any_adapter_runs():
    """One time/attempt/token budget is shared across attempts and fallback."""
    limits = RunLimits()

    assert limits.max_output_tokens == 4000
    assert limits.max_attempts == 3
    assert limits.run_timeout_seconds == 120.0
    assert limits.request_timeout_seconds == 45.0
    assert limits.max_context_bytes == 65536
    assert limits.max_total_tokens is None
    assert limits.max_cost_usd is None

    with pytest.raises(ValidationError):
        RunLimits(max_attempts=0)
    with pytest.raises(ValidationError):
        RunLimits(run_timeout_seconds=0)


def test_advanced_capabilities_are_declared_unavailable():
    """Phase 3 is text-only: advanced fields stay false/empty, never guessed."""
    caps = ProviderCapabilities(
        streaming=True, usage_reporting=True, context_tokens=1000, max_output_tokens=100
    )

    assert caps.tool_calls is False
    assert caps.structured_output is False
    assert caps.parallel_tool_calls is False
    assert caps.attachment_types == ()


def test_unsupported_capability_is_refused_before_the_network():
    """A tool-requiring request against a text-only profile never dispatches."""
    profile = pin_profile(ProviderProfile(**OPENAI_PROFILE))
    request = ModelRequest(
        messages=(ChatMessage(role="user", content="use a tool"),),
        max_output_tokens=10,
        required_capabilities=frozenset({"streaming", "tool_calls"}),
    )

    with pytest.raises(ProviderError) as caught:
        validate_request(request, profile)

    assert caught.value.code == "unsupported_capability"
    assert "tool_calls" in caught.value.safe_message


def test_token_ceiling_without_a_counter_is_refused():
    """An unenforceable ceiling is an error, not a silently ignored limit."""
    uncounted = dict(OPENAI_PROFILE)
    uncounted["capabilities"] = {**OPENAI_PROFILE["capabilities"], "token_counting": False}
    profile = pin_profile(ProviderProfile(**uncounted))

    with pytest.raises(ProviderError) as caught:
        validate_limits(RunLimits(max_total_tokens=8000), profile)

    assert caught.value.code == "configuration_error"


def test_cost_ceiling_without_pricing_is_refused():
    """Unknown pricing is never represented as zero cost."""
    profile = pin_profile(ProviderProfile(**OPENAI_PROFILE))

    with pytest.raises(ProviderError) as caught:
        validate_limits(RunLimits(max_cost_usd=Decimal("0.50")), profile)

    assert caught.value.code == "configuration_error"


def test_pinned_snapshot_is_frozen_digest_bearing_and_secret_free(monkeypatch):
    """The snapshot a run persists survives JSON, refuses edits, holds no key."""
    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-credential-value")
    pinned = pin_profile(ProviderProfile(**OPENAI_PROFILE))

    run_config = RunModelConfig(
        schema_version=1,
        primary=pin_profile(json.loads(pinned.model_dump_json())),
        fallbacks=(),
        limits=RunLimits(),
        binding_origin="submission",
    )
    again = RunModelConfig(**json.loads(run_config.model_dump_json()))

    assert again.model_dump_json() == run_config.model_dump_json()
    assert again.primary.profile_revision == pinned.profile_revision

    with pytest.raises(ValidationError):
        pinned.model = "gpt-5"

    dumped = json.dumps(run_config.model_dump_json())
    assert "OPENAI_API_KEY" in dumped
    assert "sentinel-credential-value" not in dumped


def test_missing_usage_stays_unknown_and_zero_stays_zero():
    """Nothing is invented for a provider that reports no counts."""
    unknown = TokenUsage()
    assert unknown.source == "unavailable"
    assert (unknown.input_tokens, unknown.output_tokens, unknown.total_tokens) == (
        None,
        None,
        None,
    )

    real_zero = TokenUsage(input_tokens=0, source="provider")
    assert real_zero.input_tokens == 0

    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1)


def test_stream_events_are_a_closed_tagged_union():
    """Only text deltas, cumulative usage snapshots, and a finish exist."""
    for payload in (
        {"type": "text_delta", "text": "x"},
        {"type": "usage_update", "usage": {"source": "provider", "output_tokens": 3}},
        {"type": "stream_finished", "reason": "stop", "actual_model": "gpt-4o"},
    ):
        ProviderEvent(**payload)

    with pytest.raises(ValidationError):
        ProviderEvent(type="text_delta", text="x", tool_calls=[])
    with pytest.raises(ValidationError):
        ProviderEvent(type="stream_finished", reason="unknown")


def test_only_a_clean_stop_with_text_is_a_completed_answer():
    """Partial, truncated, and refused output never publish as an answer."""
    def turn(**overrides):
        return ProviderTurnResult(
            selected_profile_id="openai-default", **overrides
        )

    assert turn(text="hi", finish_reason="stop", partial=False).completed
    assert not turn(text="", finish_reason="stop", partial=False).completed
    assert not turn(text="cut", finish_reason="length", partial=True).completed
    assert not turn(text="no", finish_reason="refusal", partial=False).completed


def test_provider_error_carries_fixed_copy_not_the_raw_response():
    """The raw provider body never reaches the user or the persisted record."""
    try:
        raise ValueError('{"error": {"secret": "super-secret-value"}}')
    except ValueError as raw:
        safe = ProviderError("provider_unavailable", cause=raw)

    assert safe.code == "provider_unavailable"
    assert safe.retryable is True
    assert "super-secret" not in safe.safe_message
    assert "super-secret" not in str(safe)
    assert safe.retry_after_seconds is None

    with pytest.raises(ValueError):
        ProviderError("not_a_code")
    with pytest.raises(ValueError):
        ProviderError("rate_limited", retry_after_seconds=-1)
