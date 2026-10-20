"""Installation settings reach pinned model and tool execution budgets."""

import json

import pytest
from pydantic import ValidationError

from src.openship.capabilities.contracts import ExecutionConfig, LoopLimits
from src.openship.config import Settings
from src.openship.providers.contracts import RunLimits
from src.openship.providers.registry import load_profiles
from src.openship.runs.service import _tool_chat_execution
from tests.conftest import profile_spec


def _settings(**overrides):
    profile = profile_spec("primary")
    # Operators can configure context without editing the profile JSON.
    profile["capabilities"].pop("context_tokens")
    profile["capabilities"]["tool_calls"] = True
    return Settings(
        _env_file=None,
        model_profiles_json=json.dumps([profile]),
        model_default_profile_id="primary",
        **overrides,
    )


def test_default_runtime_budgets_are_thirty_minutes_and_128k():
    installed = _settings()
    pinned = load_profiles(installed).pin_selection(None)
    assert installed.workflow_run_timeout_seconds == 1800
    assert installed.model_context_tokens == 128000
    assert pinned.limits.run_timeout_seconds == 1800
    assert pinned.limits.max_context_bytes == 2 * 1024 * 1024
    assert pinned.primary.capabilities.context_tokens == 128000
    assert LoopLimits().deadline_seconds == 1800
    assert RunLimits().run_timeout_seconds == 1800


def test_environment_settings_override_defaults_and_change_registry_cache(monkeypatch):
    initial = load_profiles(_settings()).pin_selection(None)
    monkeypatch.setenv("WORKFLOW_RUN_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("MODEL_CONTEXT_TOKENS", "64000")
    monkeypatch.setenv("MODEL_MAX_CONTEXT_BYTES", "1048576")
    changed = load_profiles(_settings()).pin_selection(None)
    assert changed.limits.run_timeout_seconds == 900
    assert changed.limits.max_context_bytes == 1048576
    assert changed.primary.capabilities.context_tokens == 64000
    # Existing runs retain their original configuration.
    assert initial.limits.run_timeout_seconds == 1800
    assert initial.primary.capabilities.context_tokens == 128000


def test_explicit_profile_limits_remain_stricter_than_installation():
    installed = _settings()
    profile = json.loads(installed.model_profiles_json)[0]
    profile["capabilities"]["context_tokens"] = 32000
    profile["limits"] = {"run_timeout_seconds": 300, "max_context_bytes": 32768}
    installed.model_profiles_json = json.dumps([profile])
    pinned = load_profiles(installed).pin_selection(None)
    assert pinned.primary.capabilities.context_tokens == 32000
    assert pinned.limits.run_timeout_seconds == 300
    assert pinned.limits.max_context_bytes == 32768


def test_partial_profile_limits_do_not_reintroduce_old_default_ceilings():
    installed = _settings(
        workflow_run_timeout_seconds=3600, model_max_context_bytes=4 * 1024 * 1024,
    )
    profile = json.loads(installed.model_profiles_json)[0]
    profile["limits"] = {"max_attempts": 1}
    installed.model_profiles_json = json.dumps([profile])
    pinned = load_profiles(installed).pin_selection(None)
    assert pinned.limits.max_attempts == 1
    assert pinned.limits.run_timeout_seconds == 3600
    assert pinned.limits.max_context_bytes == 4 * 1024 * 1024


def test_installation_context_cap_does_not_inflate_provider_capability():
    installed = _settings(model_context_tokens=16000)
    profile = json.loads(installed.model_profiles_json)[0]
    profile["capabilities"]["context_tokens"] = 32000
    installed.model_profiles_json = json.dumps([profile])
    pinned = load_profiles(installed).pin_selection(None)
    assert pinned.primary.capabilities.context_tokens == 16000


def test_tool_chat_pins_configured_deadline_and_preserves_saved_run(monkeypatch):
    from src.openship.runs import service

    installed = _settings(workflow_run_timeout_seconds=900)
    installed.tool_enabled_ids = "brave_web_search"
    monkeypatch.setattr(service, "settings", installed)
    pinned = load_profiles(installed).pin_selection(None)
    execution = _tool_chat_execution(pinned, ())
    saved = execution.model_dump_json()
    assert execution.loop_limits.deadline_seconds == 900
    installed.workflow_run_timeout_seconds = 1800
    assert ExecutionConfig.model_validate_json(saved).loop_limits.deadline_seconds == 900
    repinned = load_profiles(installed).pin_selection(None)
    assert _tool_chat_execution(repinned, ()).loop_limits.deadline_seconds == 1800


def test_tool_chat_honors_a_shorter_profile_deadline(monkeypatch):
    from src.openship.runs import service

    installed = _settings()
    profile = json.loads(installed.model_profiles_json)[0]
    profile["limits"] = {"run_timeout_seconds": 300}
    installed.model_profiles_json = json.dumps([profile])
    installed.tool_enabled_ids = "brave_web_search"
    monkeypatch.setattr(service, "settings", installed)
    pinned = load_profiles(installed).pin_selection(None)
    assert _tool_chat_execution(pinned, ()).loop_limits.deadline_seconds == 300


@pytest.mark.asyncio
async def test_large_context_reaches_provider_and_byte_guard_still_applies(monkeypatch):
    from src.openship.providers.errors import ProviderError
    from src.openship.providers.runner import run_model_turn
    from tests.providers.conftest import (
        FakeRecorder,
        control,
        conversation,
        sdk_chunk,
        serve_requests,
    )

    # This exceeds the old 64-KiB guard by several times. This tests bytes,
    # without claiming a local tokenizer or a measured provider context limit.
    request = conversation(("user", "infrastructure notes " * 24000))
    pinned = load_profiles(_settings()).pin_selection(None)
    transport = serve_requests(monkeypatch, [{
        "chunks": [sdk_chunk(content="Reviewed.", finish_reason="stop")],
    }])
    result = await run_model_turn(request, pinned, control(), FakeRecorder())
    assert result.text == "Reviewed."
    assert transport.requests == 1

    too_large = conversation(("user", "x" * (2 * 1024 * 1024)))
    with pytest.raises(ProviderError) as refused:
        await run_model_turn(too_large, pinned, control(), FakeRecorder())
    assert refused.value.code == "context_limit_exceeded"
    assert transport.requests == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"workflow_run_timeout_seconds": 0},
        {"workflow_run_timeout_seconds": 3601},
        {"model_context_tokens": 0},
        {"model_max_context_bytes": 0},
    ],
)
def test_invalid_installation_budgets_fail_at_startup(invalid):
    with pytest.raises(ValidationError):
        _settings(**invalid)
