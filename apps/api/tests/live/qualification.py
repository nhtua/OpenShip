"""Fail-closed selectors shared by opt-in provider qualification tests."""

from src.openship.capabilities.catalog import (
    legacy_credential_binding,
    list_capability_manifests,
)

SEARCH_TOOL_IDS = frozenset({"brave_web_search", "tavily_web_search"})


def selected_search_tool_id(value: str | None) -> str | None:
    """Return the one explicit search selection, or None for ordinary runs."""
    tool_id = (value or "").strip()
    if not tool_id:
        return None
    if tool_id not in SEARCH_TOOL_IDS:
        raise AssertionError(
            "OPENSHIP_LIVE_SEARCH_TOOL_ID must name exactly one installed search capability"
        )
    return tool_id


def require_search_tool(tool_id, *, enabled_ids, credential_check):
    """Resolve a declared tool and refuse before network access if unavailable."""
    if tool_id not in SEARCH_TOOL_IDS:
        raise AssertionError("live qualification selected an unknown search capability")
    manifest = next(
        (item for item in list_capability_manifests() if item.id == tool_id), None
    )
    if manifest is None:
        raise AssertionError("live qualification capability is not installed")
    if tool_id not in enabled_ids:
        raise AssertionError("selected live search capability is not operator-enabled")
    credential_binding = legacy_credential_binding(tool_id)
    if not credential_binding or not credential_check(credential_binding):
        raise AssertionError("selected live search capability has no configured credential")
    return manifest


def require_tool_profile(profile, *, credential_check):
    """Require one available OpenAI Chat Completions profile for qualification."""
    if profile.adapter != "openai" or profile.api_version != "chat-completions":
        raise AssertionError("live tool qualification requires an OpenAI Chat Completions profile")
    if not profile.capabilities.tool_calls:
        raise AssertionError("selected live model profile does not declare tool_calls")
    if profile.capabilities.parallel_tool_calls:
        raise AssertionError("parallel tool calls are not qualified")
    if profile.credential_ref and not credential_check(profile.credential_ref):
        raise AssertionError("selected live model profile has no configured credential")
    if profile.model == "gpt-6-luna" and (
        profile.adapter_options.tool_call_reasoning_effort != "none"
    ):
        raise AssertionError(
            "GPT-6 Luna tool qualification requires tool_call_reasoning_effort=none"
        )
    return profile
