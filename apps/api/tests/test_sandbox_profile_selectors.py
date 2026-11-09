"""Live prerequisites fail closed and managed chat selectors propagate honestly."""

import pytest

from tests.full_stack.test_sandbox_python_chat import _runtime_settings
from tests.live.test_sandbox_profiles import selected_requirements


def test_chat_runtime_selectors_keep_legacy_defaults_and_accept_logs_data():
    assert _runtime_settings({}) == {
        "SANDBOX_IMAGE": "openship-python-plot:phase6b-task4",
        "SANDBOX_PROFILE_ID": "python311-plot",
        "SANDBOX_DEPENDENCY_IDENTITY": "matplotlib-3.9.2-numpy-2.1.0",
    }
    assert _runtime_settings({
        "OPENSHIP_SANDBOX_TEST_IMAGE": "openship-sandbox-logs-data:fixture",
        "OPENSHIP_SANDBOX_TEST_PROFILE_ID": "logs-data",
        "OPENSHIP_SANDBOX_TEST_DEPENDENCY_IDENTITY": "sha256:" + "1" * 64,
    }) == {
        "SANDBOX_IMAGE": "openship-sandbox-logs-data:fixture",
        "SANDBOX_PROFILE_ID": "logs-data",
        "SANDBOX_DEPENDENCY_IDENTITY": "sha256:" + "1" * 64,
    }


def test_explicit_live_selection_requires_all_prerequisites_not_skip():
    with pytest.raises(pytest.fail.Exception):
        selected_requirements({"OPENSHIP_LIVE_SANDBOX_PROFILES": "1"})


def test_task_tool_description_exposes_only_ready_profiles_without_host_identity(monkeypatch):
    from src.openship.capabilities.definitions import task_tool_definitions
    from src.openship.config import settings
    from src.openship.sandboxes.profiles import (
        install_runtime_catalog,
        reset_runtime_catalog,
    )
    from tests.test_task_profile_selection import (
        _all_profiles,
        _catalog,
        _execution,
        _profile,
    )

    pinned_profiles = _all_profiles()
    pinned_profiles["iac-validation"]["enabled"] = False
    pinned = _catalog(pinned_profiles)

    current_profiles = _all_profiles()
    current_profiles["iac-validation"]["enabled"] = False
    current_profiles["devops-shell"] = _profile(
        "6",
        label="Changed DevOps shell",
        purpose="A rebuilt image that no longer matches the pinned selection.",
        use_for="shell tools",
    )
    current = _catalog(current_profiles)
    execution = _execution(pinned)
    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_deployment_id", "lane-deployment")
    install_runtime_catalog(current)
    try:
        definitions = task_tool_definitions(execution, frozenset({"python_execute"}))
        assert len(definitions) == 1
        tool = definitions[0]
        exposed = set(tool.parameters["properties"]["profile_id"]["enum"])
        assert exposed == {"logs-data", "standard"}
        rendered = tool.description + repr(tool.parameters["properties"])
        assert "image_id" not in rendered
        assert "image_tag" not in rendered
        assert "engine_identity" not in rendered
        assert "test-engine" not in rendered
        assert "sha256:" not in rendered
    finally:
        reset_runtime_catalog()
