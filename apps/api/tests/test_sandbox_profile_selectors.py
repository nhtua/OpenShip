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
