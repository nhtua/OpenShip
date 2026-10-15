"""Operator profile configuration, registry lookup, and per-run pinning tests.

The break each test catches: a profile resolved that
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


def test_only_declared_profiles_are_available():
    """The registry offers exactly the profiles the operator configured."""
    profiles = [
        {**LOCAL_PROFILE, "id": "primary", "label": "Primary"},
        LOCAL_PROFILE,
    ]
    registry = load_profiles(
        Settings(
            _env_file=None,
            model_profiles_json=json.dumps(profiles),
            model_default_profile_id="local-llama",
        )
    )

    assert registry.default_profile_id == "local-llama"
    assert sorted(registry.profile_ids) == ["local-llama", "primary"]
    assert registry.get("local-llama").model == "qwen3-32b"


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


@pytest.mark.parametrize(
    "host,allowed",
    [
        ("10.1.2.3", True),
        ("172.16.0.4", True),
        ("172.67.1.1", False),
        ("192.168.2.3", True),
        ("192.88.99.1", False),
        ("127.1.2.3", True),
        ("::1", True),
        ("fc00::1", True),
        ("2001:4860:4860::8888", False),
        ("10.attacker.example", False),
        ("model-service", True),
    ],
)
def test_plain_http_private_host_classification_uses_ip_ranges(host, allowed):
    endpoint = f"http://[{host}]/v1" if ":" in host else f"http://{host}/v1"
    profile = {**LOCAL_PROFILE, "endpoint": endpoint}
    if allowed:
        assert parse_profiles(json.dumps([profile]))[0].endpoint == endpoint
    else:
        with pytest.raises(ProviderError) as caught:
            parse_profiles(json.dumps([profile]))
        assert caught.value.code == "configuration_error"


def test_dotenv_credentials_are_available_to_settings_catalog_and_adapter(tmp_path, monkeypatch):
    """Arbitrary credential references in the documented .env work at startup."""
    from src.openship.providers.configuration import resolve_credential
    from src.openship.providers.openai import OpenAIAdapter

    from .conftest import qualified_profile

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=test-openai-value\n"
        "ANTHROPIC_API_KEY=test-anthropic-value\n"
        "MODEL_PROFILES_JSON='[]'\n",
        encoding="utf-8",
    )
    profile = qualified_profile(credential_ref="ANTHROPIC_API_KEY")
    settings = Settings(
        _env_file=tmp_path / ".env",
        model_profiles_json=json.dumps(
            [profile.model_dump(mode="json", exclude={"limits", "profile_revision"})]
        ),
        model_default_profile_id=profile.id,
    )
    assert resolve_credential("OPENAI_API_KEY") == "test-openai-value"
    assert resolve_credential("ANTHROPIC_API_KEY") == "test-anthropic-value"
    assert load_profiles(settings).availability(profile.id).available is True
    client = OpenAIAdapter().build_client(profile)
    assert client.api_key == "test-anthropic-value"


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
    from tests.conftest import profile_spec

    registry = load_profiles(Settings(
        _env_file=None,
        model_profiles_json=json.dumps([profile_spec("primary", credential_ref="OPENAI_API_KEY")]),
        model_default_profile_id="primary",
    ))
    pinned = registry.get("primary")

    assert pinned.credential_ref == "OPENAI_API_KEY"
    assert "sentinel-credential-value" not in pinned.model_dump_json()


def test_dispatch_rechecks_current_profile_id_without_replacing_pinned_destination(monkeypatch):
    from src.openship.config import settings as live_settings
    from src.openship.providers.runner import authorize_current_profile

    pinned_spec = {**LOCAL_PROFILE, "id": "pinned", "endpoint": "http://model-service:8080/v1"}
    current_spec = {**pinned_spec, "endpoint": "http://new-service:8080/v1"}
    monkeypatch.setattr(live_settings, "model_profiles_json", json.dumps([current_spec]))
    monkeypatch.setattr(live_settings, "model_default_profile_id", "pinned")
    pinned = load_profiles(Settings(
        model_profiles_json=json.dumps([pinned_spec]),
        model_default_profile_id="pinned",
    )).get("pinned")

    authorize_current_profile(pinned)
    assert pinned.endpoint == "http://model-service:8080/v1"

    monkeypatch.setattr(live_settings, "model_profiles_json", json.dumps([LOCAL_PROFILE]))
    monkeypatch.setattr(live_settings, "model_default_profile_id", "local-llama")
    with pytest.raises(ProviderError) as revoked:
        authorize_current_profile(pinned)
    assert revoked.value.code == "configuration_error"
