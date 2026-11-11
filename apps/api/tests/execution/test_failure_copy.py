"""Failure copy names the failed step's own dependency, not another tool's.

The break this pins: dev-stack run `e8aa2b32-1507-4c3d-8114-b8606f4b0915` had
its `python_execute` step fail with `provider_unavailable`, and the operation's
recorded message read "The search service is temporarily unavailable." - the
shared execution maps applied search wording to every capability's provider
failure.
"""

from src.openship.execution.gateway import _PRE_DISPATCH_MESSAGES
from src.openship.execution.publication import _ERROR_MESSAGES
from src.openship.tools.search.http import _SAFE_MESSAGES

NEUTRAL = "The service this step depends on is temporarily unavailable."


def test_a_generic_provider_failure_does_not_name_a_service_the_step_never_asked():
    for messages in (_PRE_DISPATCH_MESSAGES, _ERROR_MESSAGES):
        assert messages["provider_unavailable"] == NEUTRAL
        assert "search" not in messages["provider_unavailable"].lower()


def test_the_search_transport_keeps_its_own_search_wording():
    assert _SAFE_MESSAGES["provider_unavailable"] == (
        "The search service is temporarily unavailable."
    )
