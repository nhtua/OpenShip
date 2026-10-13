"""Operator profile configuration, registry lookup, and per-run pinning tests.

The break each test catches: legacy settings ignored, a profile resolved that
the operator never allowed, an endpoint that smuggles a secret in its URL,
or a fallback the user did not authorize.
"""

import json

import pytest

from src.openship.config import Settings
from src.openship.providers.configuration import parse_profiles
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import load_profiles

LOCAL_PROFILE = {
    "id": "local-llama",
    "label": "Local llama server",
    "adapter": "openai_compatible",
    "model": "qwen3-32b",
    "endpoint": "http://localhost:8080/v1",
    "api_version": "chat-completions",
    "credential_ref": "",
    "capabilities": {
        "streaming": True,
        "usage_reporting": False,
        "context_tokens": 32768,
        "max_output_tokens": 4096,
        "token_counting": False,
        "tool_calls": False,
        "structured_output": False,
        "attachment_types": [],
        "parallel_tool_calls": False,
    },
    "allowed_fallback_profile_ids": [],
    "qualified_model_aliases": ["Qwen3-32B"],
    "adapter_options": {
        "adapter": "openai_compatible",
        "endpoint_implementation": "llama-server:b4000",
        "authentication": "none",
        "stream_usage_supported": False,
        "output_limit_parameter": "max_tokens",
    },
}


def legacy_settings():
    return Settings(
        openai_api_key="test-key-not-a-secret",
        openai_model="gpt-4o",
        openai_base_url="https://api.openai.com/v1",
        model_max_tokens=4000,
        model_profiles_json="",
        model_default_profile_id="",
    )


def test_legacy_settings_still_produce_a_working_default_profile():
    """An installation that predates profiles keeps working unchanged."""
    registry = load_profiles(legacy_settings())

    assert registry.default_profile_id == "openai-default"
    pinned = registry.get("openai-default")
    assert pinned.adapter == "openai"
    assert pinned.model == "gpt-4o"
    assert pinned.endpoint == "https://api.openai.com/v1"
    assert pinned.credential_ref == "OPENAI_API_KEY"
    assert pinned.limits.max_output_tokens == 4000


def test_declaring_profiles_replaces_the_legacy_default():
    """Explicit configuration never blends legacy values into a new profile."""
    profiles = [
        {**LOCAL_PROFILE, "id": "primary", "label": "Primary"},
        LOCAL_PROFILE,
    ]
    registry = load_profiles(
        Settings(
            openai_model="gpt-4o",
            model_profiles_json=json.dumps(profiles),
            model_default_profile_id="local-llama",
        )
    )

    assert registry.default_profile_id == "local-llama"
    assert sorted(registry.profile_ids) == ["local-llama", "primary"]
    assert registry.get("local-llama").model == "qwen3-32b"
    assert "openai-default" not in registry.profile_ids


def test_declared_profile_must_name_its_own_model():
    """No current model ID is guessed on the operator's behalf."""
    without_model = {k: v for k, v in LOCAL_PROFILE.items() if k != "model"}

    with pytest.raises(ProviderError) as caught:
        parse_profiles(json.dumps([without_model]))
    assert caught.value.code == "configuration_error"
    assert "model" in caught.value.safe_message


def test_operator_configuration_mistakes_are_all_configuration_errors():
    """Extra keys, duplicate IDs, and unknown adapters fail before use."""
    with pytest.raises(ProviderError) as caught:
        parse_profiles(json.dumps([{**LOCAL_PROFILE, "api_key": "not-a-secret"}]))
    assert "api_key" in caught.value.safe_message

    with pytest.raises(ProviderError):
        parse_profiles(json.dumps([LOCAL_PROFILE, LOCAL_PROFILE]))

    with pytest.raises(ProviderError) as caught:
        parse_profiles(json.dumps([{**LOCAL_PROFILE, "adapter": "mistral"}]))
    assert "adapter" in caught.value.safe_message

    with pytest.raises(ProviderError):
        parse_profiles(json.dumps([{**LOCAL_PROFILE, "id": "a b"}]))

    with pytest.raises(ProviderError):
        parse_profiles("not json")


def test_a_claimed_capability_needs_the_mechanism_that_implements_it():
    """A profile cannot claim a token ceiling it has no counter to enforce."""
    unbacked = {
        **LOCAL_PROFILE,
        "capabilities": {**LOCAL_PROFILE["capabilities"], "token_counting": True},
    }
    with pytest.raises(ProviderError) as caught:
        parse_profiles(json.dumps([unbacked]))
    assert caught.value.code == "configuration_error", (
        "the declaration is refused, not carried into a run"
    )
    assert "token_counter_id" in caught.value.safe_message, (
        "the operator is told which half of the claim was missing"
    )

    backed = {
        **unbacked,
        "adapter_options": {
            **unbacked["adapter_options"],
            "token_counter_id": "llama-server:tokenize",
        },
    }
    assert parse_profiles(json.dumps([backed]))[0].capabilities.token_counting is True, (
        "naming the counter is what makes the claim legal"
    )


def test_endpoints_cannot_smuggle_credentials_or_use_insecure_remote_hosts():
    """A URL is a destination; secrets and query strings are not."""
    for bad in (
        "https://user:passwd@api.example.com/v1",
        "https://api.example.com/v1?api_key=query-secret",
        "https://api.example.com/v1#frag",
        "ftp://api.example.com/v1",
        "http://api.example.com/v1",
    ):
        with pytest.raises(ProviderError) as caught:
            parse_profiles(json.dumps([{**LOCAL_PROFILE, "endpoint": bad}]))
        assert caught.value.code == "configuration_error"

    profile = parse_profiles(json.dumps([LOCAL_PROFILE]))[0]
    assert profile.endpoint == "http://localhost:8080/v1"


def test_fallback_requires_an_explicit_allowed_list():
    """A user cannot reach a destination the operator did not authorize."""
    primary = {
        **LOCAL_PROFILE,
        "id": "primary",
        "label": "Primary",
        "allowed_fallback_profile_ids": ["local-llama"],
    }
    registry = load_profiles(
        Settings(
            model_profiles_json=json.dumps([primary, LOCAL_PROFILE]),
            model_default_profile_id="primary",
        )
    )

    chosen = registry.pin_selection("primary", ["local-llama"])
    assert chosen.primary.id == "primary"
    assert [p.id for p in chosen.fallbacks] == ["local-llama"]

    with pytest.raises(ProviderError) as caught:
        registry.pin_selection("primary", ["never-declared"])
    assert caught.value.code == "configuration_error"

    unselected = registry.pin_selection(None, [])
    assert unselected.binding_origin == "submission"
    assert unselected.primary.id == "primary"
    assert unselected.fallbacks == ()


def test_keyless_local_profile_is_available_and_bearer_profile_is_not():
    """Availability explains itself without ever exposing a credential value."""
    registry = load_profiles(
        Settings(model_profiles_json=json.dumps([LOCAL_PROFILE]),
                 model_default_profile_id="local-llama")
    )
    assert registry.availability("local-llama").available is True

    bearer = {**LOCAL_PROFILE, "credential_ref": "LOCAL_LLM_KEY"}
    other = load_profiles(
        Settings(model_profiles_json=json.dumps([bearer]),
                 model_default_profile_id="local-llama")
    )
    report = other.availability("local-llama")
    assert report.available is False
    assert report.reason == "missing_credentials"
    assert "LOCAL_LLM_KEY" in report.explanation


def test_availability_never_leaks_a_bound_credential(monkeypatch):
    """A configured binding stays out of every public projection."""
    monkeypatch.setenv("LOCAL_LLM_KEY", "sentinel-credential-value")
    bearer = {**LOCAL_PROFILE, "credential_ref": "LOCAL_LLM_KEY"}
    registry = load_profiles(
        Settings(model_profiles_json=json.dumps([bearer]),
                 model_default_profile_id="local-llama")
    )

    report = registry.availability("local-llama")
    assert report.available is True
    assert "sentinel-credential-value" not in report.explanation


def test_registry_never_resolves_a_credential_value_at_load(monkeypatch):
    """Configuration is secret-free; only adapters bind a value, at dispatch."""
    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-credential-value")
    pinned = load_profiles(legacy_settings()).get("openai-default")

    assert pinned.credential_ref == "OPENAI_API_KEY"
    assert "sentinel-credential-value" not in pinned.model_dump_json()
