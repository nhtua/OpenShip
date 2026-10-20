"""Network-free checks that live qualification cannot guess a destination."""

from types import SimpleNamespace

import pytest

from tests.live.qualification import (
    require_search_tool,
    require_tool_profile,
    selected_search_tool_id,
)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_search_qualification_is_opt_in(value):
    assert selected_search_tool_id(value) is None


@pytest.mark.parametrize("value", ["unknown", "brave_web_search,tavily_web_search"])
def test_search_qualification_rejects_guessed_or_multiple_tools(value):
    with pytest.raises(AssertionError, match="exactly one"):
        selected_search_tool_id(value)


@pytest.mark.parametrize(
    ("enabled_ids", "credential_check", "message"),
    [
        (frozenset(), lambda _name: True, "not operator-enabled"),
        (frozenset({"brave_web_search"}), lambda _name: False, "no configured credential"),
    ],
)
def test_search_preflight_fails_before_network(enabled_ids, credential_check, message):
    with pytest.raises(AssertionError, match=message):
        require_search_tool(
            "brave_web_search",
            enabled_ids=enabled_ids,
            credential_check=credential_check,
        )


def test_model_preflight_refuses_text_only_profiles():
    profile = SimpleNamespace(
        adapter="openai",
        api_version="chat-completions",
        capabilities=SimpleNamespace(tool_calls=False, parallel_tool_calls=False),
        credential_ref="OPENAI_API_KEY",
        model="gpt-6-luna",
        adapter_options=SimpleNamespace(tool_call_reasoning_effort="none"),
    )

    with pytest.raises(AssertionError, match="does not declare tool_calls"):
        require_tool_profile(profile, credential_check=lambda _name: True)


def test_model_preflight_requires_gpt6_tool_reasoning_qualification():
    profile = SimpleNamespace(
        adapter="openai",
        api_version="chat-completions",
        capabilities=SimpleNamespace(tool_calls=True, parallel_tool_calls=False),
        credential_ref="OPENAI_API_KEY",
        model="gpt-6-luna",
        adapter_options=SimpleNamespace(tool_call_reasoning_effort=None),
    )

    with pytest.raises(AssertionError, match="reasoning_effort=none"):
        require_tool_profile(profile, credential_check=lambda _name: True)
