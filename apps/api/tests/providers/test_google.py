"""Google-specific behaviour: the Gemini Developer API's own shapes.

The shared suite proves what every adapter must do. These are the claims only
this API can make or break: the system turn is a field of its own rather than
a message, the assistant role is named for the model, an answer is a list of
parts of which only text is the answer - thought and predicted calls sit
between the ones that are - the counts are repeated on every chunk, this API
does report a total, and its ends name a refusal, a rejected call, and a hop
the SDK would otherwise have continued.

The breaks these catch: an adapter that puts the system turn in the history,
that reads a thought part as the answer, that lets the SDK run a call or
continue a hop behind the runner's back, that sums the counts it was handed
twice, or that guesses the meaning of an end it has not seen.
"""

import os

import pytest
from google.genai.types import BlockedReason, FinishReason

from src.openship.providers.errors import ProviderError
from src.openship.providers.google import GoogleAdapter, transport_options

from .conftest import (
    GoogleCountingClient,
    adapter_case,
    control,
    conversation,
    drain,
    error_fixture,
    google_candidate,
    google_function_call,
    google_part,
    google_response,
    google_thought,
    provider_stream,
    qualified_profile,
)

FIXTURE_KEY = "FIXTURE_KEY"  # rayloc:ignore

#: A counter this provider qualifies. Only its name is committed; the count it
#: would return is a fixture's own number, never a measurement.
COUNTING = "google-count-tokens"  # rayloc:ignore

#: The ends this build reads, and what each means. An end that is not here is
#: not guessed: a new name is a change to the API's vocabulary, and claiming
#: a complete answer because one arrived would be a claim without evidence.
READ_ENDS = (
    (FinishReason.STOP, "stop"),
    (FinishReason.MAX_TOKENS, "length"),
    (FinishReason.SAFETY, "refusal"),
    (FinishReason.BLOCKLIST, "refusal"),
    (FinishReason.PROHIBITED_CONTENT, "refusal"),
)


def _counting_profile():
    """The same destination, qualified for the provider's own counter."""
    return qualified_profile(
        adapter="google",
        capabilities={
            "streaming": True,
            "usage_reporting": True,
            "context_tokens": 32768,
            "max_output_tokens": 4096,
            "token_counting": True,
            "tool_calls": False,
            "structured_output": False,
            "attachment_types": [],
            "parallel_tool_calls": False,
        },
        adapter_options={
            "adapter": "google",
            "native_api_version": "v1",
            "automatic_function_calling": False,
            "token_counter_id": COUNTING,
        },
    )


@pytest.mark.asyncio
async def test_the_system_instruction_is_given_apart_from_the_history():
    """The system turn is a field of its own, and never part of the contents."""
    case = adapter_case(adapter="google", chunks=provider_stream("google", text=["ok"]))
    request = conversation(
        ("system", "Be brief."), ("user", "What failed?"), ("assistant", "A deploy.")
    )

    _events, failure = await drain(case.adapter, request, case.profile, control())

    assert failure is None
    asked = case.transport.seen_kwargs
    assert asked["config"].system_instruction == "Be brief."
    assert [content.role for content in asked["contents"]] == ["user", "model"]


@pytest.mark.asyncio
async def test_the_adapter_asks_only_for_what_openship_decides():
    """No SDK default is left to decide something OpenShip owns."""
    case = adapter_case(adapter="google", chunks=provider_stream("google", text=["ok"]))

    await drain(
        case.adapter,
        conversation(("system", "Be brief."), ("user", "hi")),
        case.profile,
        control(),
    )

    asked = case.transport.seen_kwargs
    assert set(asked) == {"model", "contents", "config"}
    assert asked["config"].max_output_tokens == 128


@pytest.mark.asyncio
async def test_the_sdk_is_never_left_to_multiply_a_request():
    """A predicted call is run by the harness, and a hop is the runner's."""
    case = adapter_case(adapter="google", chunks=provider_stream("google", text=["ok"]))

    await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    config = case.transport.seen_kwargs["config"]
    assert config.automatic_function_calling.disable is True
    assert config.automatic_continuation is False


@pytest.mark.asyncio
async def test_only_text_parts_become_the_answer():
    """A thought and a predicted call are neither answer text nor an action."""
    case = adapter_case(
        adapter="google",
        chunks=[
            google_response(
                candidates=[
                    google_candidate(
                        parts=[
                            google_thought("checking the deploy log"),
                            google_function_call("check_ci", {"job": "build"}),
                        ]
                    )
                ]
            ),
            google_response(candidates=[google_candidate(parts=[google_part("The build failed.")])]),
            google_response(candidates=[google_candidate(finish=FinishReason.STOP)]),
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "what broke?")), case.profile, control()
    )

    assert failure is None
    text = "".join(e.text for e in events if e.type == "text_delta")
    assert text == "The build failed."
    assert events[-1].reason == "stop"
    assert case.transport.requests == 1


@pytest.mark.asyncio
async def test_actual_google_thought_part_text_is_not_answer_text():
    """SDK Parts carry thought text in `text` as well as `thought=True`."""
    from google.genai import types

    case = adapter_case(
        adapter="google",
        chunks=[
            google_response(
                candidates=[
                    google_candidate(
                        parts=[
                            types.Part(text="private deliberation", thought=True),
                            types.Part(text="public answer"),
                        ]
                    )
                ]
            ),
            google_response(candidates=[google_candidate(finish=FinishReason.STOP)]),
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )
    assert failure is None
    assert "".join(e.text for e in events if e.type == "text_delta") == "public answer"


@pytest.mark.asyncio
async def test_the_counts_are_the_ones_the_provider_last_stated():
    """Repeated snapshots are replaced; this API's own total is kept."""
    case = adapter_case(
        adapter="google",
        chunks=provider_stream("google", text=["hi"], usage=[(10, 5), (10, 8)]),
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    updates = [e.usage for e in events if e.type == "usage_update"]
    assert (updates[-1].input_tokens, updates[-1].output_tokens) == (10, 8)
    assert updates[-1].total_tokens == 18


@pytest.mark.asyncio
async def test_no_usage_is_claimed_from_a_snapshot_that_carries_no_numbers():
    """Every chunk carries a snapshot; an empty one is not a set of zeros."""
    case = adapter_case(adapter="google", chunks=provider_stream("google", text=["hi"]))

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    assert [e for e in events if e.type == "usage_update"] == []


@pytest.mark.asyncio
async def test_a_response_blocked_before_anything_is_not_an_answer():
    """A block reason is not an empty answer, and its name is not leaked."""
    case = adapter_case(
        adapter="google",
        chunks=[google_response(blocked=BlockedReason.PROHIBITED_CONTENT)],
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "invalid_response"
    assert "PROHIBITED_CONTENT" not in failure.safe_message
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_the_provider_ends_are_mapped_not_passed_through():
    """Each end this API names means one thing in the contract."""
    for provider_end, contract_reason in READ_ENDS:
        case = adapter_case(
            adapter="google",
            chunks=[
                google_response(candidates=[google_candidate(parts=[google_part("a")])]),
                google_response(candidates=[google_candidate(finish=provider_end)]),
            ],
        )

        events, failure = await drain(
            case.adapter, conversation(("user", "hi")), case.profile, control()
        )

        assert failure is None
        assert events[-1].reason == contract_reason


@pytest.mark.asyncio
async def test_a_refusal_keeps_the_answer_it_had_given():
    """Withholding is not deleting: what arrived is still what was said."""
    case = adapter_case(
        adapter="google",
        chunks=[
            google_response(candidates=[google_candidate(parts=[google_part("most of it")])]),
            google_response(candidates=[google_candidate(finish=FinishReason.SAFETY)]),
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure is None
    assert "".join(e.text for e in events if e.type == "text_delta") == "most of it"
    assert events[-1].reason == "refusal"


@pytest.mark.asyncio
async def test_a_rejected_call_is_a_failure_not_a_refusal():
    """The model tried to call something this adapter never runs."""
    for provider_end in (FinishReason.MALFORMED_FUNCTION_CALL, FinishReason.UNEXPECTED_TOOL_CALL):
        case = adapter_case(
            adapter="google",
            chunks=[
                google_response(candidates=[google_candidate(parts=[google_part("let me check")])]),
                google_response(candidates=[google_candidate(finish=provider_end)]),
            ],
        )

        _events, failure = await drain(
            case.adapter, conversation(("user", "hi")), case.profile, control()
        )

        assert failure.code == "invalid_response"


@pytest.mark.asyncio
async def test_a_hop_the_sdk_would_continue_is_not_read_as_finished():
    """Continuation is disabled, so a hop is a request the runner would own."""
    case = adapter_case(
        adapter="google",
        chunks=[
            google_response(candidates=[google_candidate(parts=[google_part("part one")])]),
            google_response(candidates=[google_candidate(finish=FinishReason.CONTINUATION)]),
        ],
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "invalid_response"
    assert "CONTINUATION" in str(failure.cause)


@pytest.mark.asyncio
async def test_an_end_this_build_has_not_seen_is_not_guessed():
    """A new vocabulary is a failure, not a claim of a complete answer."""
    case = adapter_case(
        adapter="google",
        chunks=[
            google_response(candidates=[google_candidate(parts=[google_part("a")])]),
            google_response(candidates=[google_candidate(finish="MODEL_WRAPPED_UP")]),
        ],
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "invalid_response"
    assert "MODEL_WRAPPED_UP" in str(failure.cause)


@pytest.mark.asyncio
async def test_a_stream_that_fails_after_answering_is_the_same_failure():
    """A server-side failure after HTTP success is normalized and closed."""
    case = adapter_case(
        adapter="google",
        chunks=[
            google_response(candidates=[google_candidate(parts=[google_part("part of an answer")])]),
            error_fixture("google", "server"),
        ],
    )

    events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "provider_unavailable"
    assert "".join(e.text for e in events if e.type == "text_delta") == "part of an answer"
    assert case.transport.closed is True


@pytest.mark.asyncio
async def test_a_rate_limit_keeps_the_repair_hint():
    """The provider's own delay survives; its body does not."""
    case = adapter_case(adapter="google", chunks=[], raises=error_fixture("google"))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "rate_limited"
    assert failure.retry_after_seconds == 7.0
    assert "secret-body" not in failure.safe_message


@pytest.mark.asyncio
async def test_a_failure_without_a_readable_status_is_still_a_failure():
    """The mapping is total: a provider that answered nothing is not no claim."""
    case = adapter_case(
        adapter="google", chunks=[], raises=error_fixture("google", "unreadable")
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "provider_unavailable"


@pytest.mark.asyncio
async def test_the_sdk_never_multiplies_our_requests():
    """One failed attempt is one request; retrying is the runner's decision."""
    case = adapter_case(adapter="google", chunks=[], raises=error_fixture("google"))

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert case.transport.requests == 1
    assert failure.code == "rate_limited"


def test_the_transport_policy_asks_for_one_attempt_and_a_bounded_request():
    """OpenShip owns retries and request timeouts, not the transport."""
    profile = qualified_profile(adapter="google")
    options = transport_options(profile)

    assert options.retry_options.attempts == 1
    assert options.timeout == profile.limits.request_timeout_seconds * 1000
    assert options.base_url == profile.endpoint
    assert options.api_version == profile.adapter_options.native_api_version


def test_a_missing_credential_refuses_before_any_request():
    """An unbound binding is a configuration failure, not a silent default."""
    os.environ.pop(FIXTURE_KEY, None)
    adapter = GoogleAdapter()

    with pytest.raises(ProviderError) as caught:
        adapter.build_client(qualified_profile(adapter="google"))
    assert caught.value.code == "configuration_error"
    assert "FIXTURE_KEY" in caught.value.safe_message


@pytest.mark.asyncio
async def test_counting_asks_the_providers_own_counter():
    """A qualified counter is called with the same conversation, and its own."""
    client = GoogleCountingClient(123)
    adapter = GoogleAdapter(client_factory=lambda _config: client)
    request = conversation(("system", "Be brief."), ("user", "count this"))

    counted = await adapter.count_input_tokens(
        request, _counting_profile(), control(remaining=5.0)
    )

    assert counted == 123
    assert client.asked[0]["config"].system_instruction == "Be brief."
    assert [content.role for content in client.asked[0]["contents"]] == ["user"]
    assert client.asked[0]["config"].http_options.timeout == 5000
    assert client.asked[0]["config"].http_options.retry_options.attempts == 1


@pytest.mark.asyncio
async def test_counting_is_refused_when_the_profile_has_no_counter():
    """No qualified counter means no token counting, not an estimate."""
    case = adapter_case(adapter="google", chunks=[])
    request = conversation(("user", "count this"))

    with pytest.raises(ProviderError) as caught:
        await case.adapter.count_input_tokens(request, case.profile, control())

    assert caught.value.code == "unsupported_capability"


@pytest.mark.asyncio
async def test_a_counter_this_adapter_does_not_implement_is_not_substituted():
    """Naming a counter is a request for that one, not for any counter."""
    profile = _counting_profile().model_dump()
    profile["adapter_options"]["token_counter_id"] = "some-other-counter"

    with pytest.raises(ProviderError) as caught:
        await GoogleAdapter().count_input_tokens(
            conversation(("user", "count this")),
            qualified_profile(**profile),
            control(),
        )

    assert caught.value.code == "unsupported_capability"
    assert "some-other-counter" in caught.value.safe_message


@pytest.mark.asyncio
async def test_a_profile_that_asks_the_sdk_to_run_calls_is_not_served():
    """The declared option is what this adapter does, not what it may do."""
    profile = _counting_profile().model_dump()
    profile["adapter_options"]["automatic_function_calling"] = True

    with pytest.raises(ProviderError) as caught:
        await GoogleAdapter().count_input_tokens(
            conversation(("user", "count this")),
            qualified_profile(**profile),
            control(),
        )

    assert caught.value.code == "unsupported_capability"
    assert "automatic_function_calling" in caught.value.safe_message


@pytest.mark.asyncio
async def test_an_unqualified_model_alias_is_reported():
    """A provider that serves a different model is not silently accepted."""
    case = adapter_case(
        adapter="google",
        chunks=[
            google_response(model="other-model"),
            google_response(candidates=[google_candidate(parts=[google_part("hi")])]),
            google_response(candidates=[google_candidate(finish=FinishReason.STOP)]),
        ],
    )

    _events, failure = await drain(
        case.adapter, conversation(("user", "hi")), case.profile, control()
    )

    assert failure.code == "unexpected_model"
    assert _events[-1].actual_model == "other-model"


@pytest.mark.asyncio
async def test_a_stalled_read_is_stopped_and_closed():
    """Stopping part-way through a blocked read completes nothing."""
    case = adapter_case(
        adapter="google",
        chunks=provider_stream("google", text=["a", "b", "c"], usage=[(10, 1)]),
    )
    seen = {"calls": 0}

    def stopped_after_the_first_part():
        seen["calls"] += 1
        return seen["calls"] > 2

    _events, failure = await drain(
        case.adapter,
        conversation(("user", "hi")),
        case.profile,
        control(stop=stopped_after_the_first_part),
    )

    text = "".join(e.text for e in _events if e.type == "text_delta")
    assert failure.code == "cancelled"
    assert text.startswith("a")
    assert text != "abc"
    assert case.transport.closed is True
