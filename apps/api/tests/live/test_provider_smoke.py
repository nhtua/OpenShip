"""One real provider turn, opt-in, for the profile an operator qualified.

This is the only test that contacts a provider, and it is what turns a claim
in ``docs/provider-support.md`` into evidence. It never runs as part of an
ordinary suite: it carries the ``provider_live`` marker, and it does nothing
until ``OPENSHIP_LIVE_PROFILE_ID`` names one installed profile. A real turn
can incur provider charges, so qualification is a decision the operator makes
rather than something a test run spends on its own.

The breaks these cases catch: a smoke run that invents a profile the
installation never declared, one that reaches a destination outside that
configuration, and one that reports qualification from a turn that produced
no answer.
"""

import os
import time

import pytest

from src.openship.config import settings
from src.openship.providers.contracts import ChatMessage, ModelRequest
from src.openship.providers.registry import build_adapter, load_profiles
from src.openship.providers.runner import run_model_turn
from src.openship.providers.control import InvocationControl

pytestmark = pytest.mark.provider_live

PROFILE_ID_ENV = "OPENSHIP_LIVE_PROFILE_ID"

#: The smallest turn that still exercises the whole path - a real request, a
#: real stream, a real finish. Nothing here asks a model to be useful, so a
#: qualification says the transport works, not that an answer is good.
SMOKE_PROMPT = "Reply with the single word ready."
SMOKE_OUTPUT_BOUND = 16


def _selected_profile_id() -> str:
    """The one installed profile this opt-in run qualifies, or skip."""
    profile_id = os.environ.get(PROFILE_ID_ENV, "").strip()
    if not profile_id:
        pytest.skip(
            "opt-in: set OPENSHIP_LIVE_PROFILE_ID to the installed profile "
            "you accept a real turn against (it can incur provider charges)"
        )
    return profile_id


class AttemptLog:
    """What the turn opened and closed, kept in memory.

    The durable record belongs to a run's transaction and is qualified against
    PostgreSQL in ``tests/integration``; a live turn has no run to write into,
    so this only has to be enough to say the turn made one attempt and closed
    it with a result.
    """

    def __init__(self):
        self.opened = []
        self.closed = []

    def start(self, profile, reason, reserved_cost_usd=None):
        self.opened.append((profile.id, reason, reserved_cost_usd))
        return len(self.opened)

    def finish(self, attempt_id, outcome, usage=None, error_code=None, actual_model=None):
        self.closed.append(
            {
                "outcome": outcome,
                "error_code": error_code,
                "usage": usage,
                "actual_model": actual_model,
            }
        )


def _watched_destination(adapter, seen: list) -> None:
    """Watch the one place a real turn builds its client.

    The adapter constructs a client only where a turn runs, so the only way to
    learn which destination a real turn actually reached is to watch that
    construction. ``client_factory`` is the adapter's own injection point.
    """
    build = adapter.build_client

    def client(config):
        seen.append(config.endpoint)
        return build(config)

    adapter._client_factory = client


@pytest.mark.asyncio
async def test_smoke_never_invents_a_profile():
    """The selected profile is one this installation declares and can serve.

    No request is made here: an operator who asks for qualification and has
    configured nothing gets a reason, not a silent skip and not a call to
    somewhere they never chose.
    """
    profile_id = _selected_profile_id()
    registry = load_profiles(settings)

    availability = registry.availability(profile_id)
    assert availability.available, (
        f"the installation cannot serve {profile_id} for qualification: "
        f"{availability.reason} - {availability.explanation}"
    )


@pytest.mark.asyncio
async def test_smoke_turn_answers_once_at_the_declared_destination(monkeypatch):
    """One real turn, through the configuration this installation declares."""
    profile_id = _selected_profile_id()
    registry = load_profiles(settings)
    declared = registry.get(profile_id).endpoint
    config = registry.pin_selection(profile_id, ())

    seen: list[str] = []

    def build_and_watch(profile):
        adapter = build_adapter(profile)
        _watched_destination(adapter, seen)
        return adapter

    # The runner asks the registry for an adapter per attempt; watching that
    # one call is what makes the destination a claim rather than an assumption.
    monkeypatch.setattr("src.openship.providers.runner.build_adapter", build_and_watch)

    log = AttemptLog()
    request = ModelRequest(
        messages=(ChatMessage(role="user", content=SMOKE_PROMPT),),
        max_output_tokens=min(SMOKE_OUTPUT_BOUND, config.limits.max_output_tokens),
    )
    control = InvocationControl(deadline=time.monotonic() + config.limits.run_timeout_seconds)
    result = await run_model_turn(request, config, control, log)

    assert log.opened, "a qualified turn made no request at all"
    assert len(log.opened) == 1, "a qualified turn reached for another attempt"
    assert seen == [declared], f"the turn contacted {seen}, not {declared}"

    # An answer, and an end that says so: a partial stream is not a
    # qualification, and neither is an empty one.
    assert result.finish_reason == "stop", f"the turn ended as {result.finish_reason}"
    assert not result.partial
    assert result.text, "the turn reported success without producing an answer"

    # What served is recorded. A provider that answered with another model is
    # qualified only if the profile said so - the runner rejects it otherwise,
    # so reaching here means the pair is the profile's own.
    assert result.actual_model == registry.get(profile_id).model

    # Usage is either what the provider reported or explicitly unknown; the
    # contract never invents a count, so asserting the shape is the claim.
    if result.usage is not None:
        assert result.usage.input_tokens is not None
        assert result.usage.output_tokens is not None
