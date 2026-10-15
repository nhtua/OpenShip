"""The Google adapter: the qualified Gemini Developer API baseline.

The SDK is imported only here. Everything this module hands on is a
provider-neutral contract, so the graph, the worker, and the browser never
see an SDK object. The SDK never multiplies a request on its own either:
OpenShip owns request counts, timeouts, and fallback - which is why this
adapter disables both the SDK's transport retries and the two behaviors that
would make one attempt into several requests behind the runner's back.

Where this API differs from the other adapters is stated at each place it
matters: the system turn is a field of its own, the assistant role is named
for the model, an answer is a list of parts of which only text is the answer,
the counts are repeated on every chunk rather than reported once, and the
ends name a refusal, a rejected call, and a hop.
"""

from __future__ import annotations

import httpx

try:
    import httpx2
except ImportError:  # the SDK treats the fork as optional, and so do we
    httpx2 = None

from google.genai import Client, types
from google.genai.errors import APIError

from src.openship.providers.configuration import resolve_credential
from src.openship.providers.contracts import (
    ModelRequest,
    ProviderEvent,
    TokenUsage,
    validate_request,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import register_adapter

#: The counter this adapter implements, by the name an operator declares. A
#: profile that names another one is not served by a different counter.
NATIVE_COUNTER = "google-count-tokens"

#: This API's ends, in the contract's vocabulary. An end that is not here is
#: not guessed: a new name is a change to the API's vocabulary, and claiming
#: a complete answer because one arrived would be a claim without evidence.
_END_REASON = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "refusal",
    "RECITATION": "refusal",
    "LANGUAGE": "refusal",
    "BLOCKLIST": "refusal",
    "PROHIBITED_CONTENT": "refusal",
    "SPII": "refusal",
    "IMAGE_SAFETY": "refusal",
    "IMAGE_PROHIBITED_CONTENT": "refusal",
    "IMAGE_RECITATION": "refusal",
    "OTHER": "refusal",
}

#: Ends this build does accept but does not answer: the model tried to call
#: something this adapter never runs, and a hop is a request the runner owns.
_REJECTED_END = {
    "MALFORMED_FUNCTION_CALL": "the model tried to call a tool this adapter does not run",
    "UNEXPECTED_TOOL_CALL": "the model called a tool this adapter does not run",
    "TOO_MANY_TOOL_CALLS": "the model called tools this adapter does not run",
    "CONTINUATION": "the SDK would have continued, and this adapter does not let it",
}

#: The failures this adapter maps, and only these are caught: an SDK failure
#: with no mapping here is not turned into a claim about the provider. The
#: transport's own errors are included because this SDK, unlike the others,
#: does not wrap them.
_SDK_FAILURES = (
    APIError,
    httpx.TimeoutException,
    httpx.ConnectError,
    *(
        (httpx2.TimeoutException, httpx2.ConnectError)
        if httpx2 is not None
        else ()
    ),
)


def _named(value) -> str | None:
    """A provider name, whether the SDK gave it as an enum member or as text."""
    if value is None:
        return None
    return getattr(value, "value", None) or (value if isinstance(value, str) else None)


def _usage(snapshot, *, input_tokens: int | None, output_tokens: int | None) -> TokenUsage | None:
    """Provider-reported counts, mapped without inventing anything.

    This API repeats the counts on every chunk, so a caller that summed them
    would double count; the counts a run reports are the ones the provider
    last stated. A snapshot that carries no numbers is not a set of zeros, so
    nothing is claimed from it.
    """
    reported_input = getattr(snapshot, "prompt_token_count", None)
    reported_output = getattr(snapshot, "candidates_token_count", None)
    if reported_input is not None:
        input_tokens = reported_input
    if reported_output is not None:
        output_tokens = reported_output
    total = getattr(snapshot, "total_token_count", None)
    cached = getattr(snapshot, "cached_content_token_count", None)
    reasoning = getattr(snapshot, "thoughts_token_count", None)
    if all(value is None for value in (input_tokens, output_tokens, total, cached, reasoning)):
        return None
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        cached_input_tokens=cached,
        reasoning_tokens=reasoning,
        source="provider",
    )


def _retry_after(error: BaseException) -> float | None:
    """A provider's own repair hint, read from the response it returned."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    for header in ("retry-after", "Retry-After"):
        hint = headers.get(header)
        if hint is None:
            continue
        try:
            value = float(hint)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _sdk_failure(error: BaseException) -> ProviderError:
    """The normalized error for an SDK failure. Total over what is caught.

    Both the request and the stream map through here: a provider that fails
    after it began answering is the same failure as one that refuses to start.
    This SDK reports a status code rather than an error class per condition,
    so the status is what carries the meaning - and a server-side failure is
    unreachable, not rate limited, because it offered no delay to wait for.

    A failure whose status this build cannot read is still a failure: the
    request did not complete, and the honest claim is that the provider is
    unreachable rather than no claim at all - which is what a caller that
    raised ``None`` here would have been.
    """
    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
        return ProviderError("provider_unavailable", cause=error)
    if httpx2 is not None and isinstance(
        error, (httpx2.TimeoutException, httpx2.ConnectError)
    ):
        return ProviderError("provider_unavailable", cause=error)
    status = getattr(error, "code", None)
    if status == 429:
        return ProviderError(
            "rate_limited", retry_after_seconds=_retry_after(error), cause=error
        )
    if status in (401, 403):
        return ProviderError("authentication_error", cause=error)
    if isinstance(status, int) and 400 <= status < 500:
        return ProviderError("invalid_request", cause=error)
    if isinstance(status, int) and 500 <= status < 600:
        return ProviderError("provider_unavailable", cause=error)
    return ProviderError("provider_unavailable", cause=error)


def transport_options(config) -> types.HttpOptions:
    """The transport policy a pinned profile asks for.

    One attempt, because retrying is the runner's decision and the SDK's own
    retries would be invisible to it; a bounded request, because a turn has a
    deadline. The timeout is milliseconds here and seconds everywhere else,
    which is the one place that conversion happens.
    """
    return types.HttpOptions(
        base_url=config.endpoint,
        api_version=config.adapter_options.native_api_version,
        timeout=int(config.limits.request_timeout_seconds * 1000),
        retry_options=types.HttpRetryOptions(attempts=1),
    )


def _system_and_contents(request: ModelRequest) -> tuple[str | None, list[types.Content]]:
    """The system turns, and the history as this API spells contents.

    Nothing here rewrites the conversation: the same text is put where this
    API reads it, and the one role this API names differently is named here.
    """
    system = [message.content for message in request.messages if message.role == "system"]
    contents = [
        types.Content(
            role="model" if message.role == "assistant" else message.role,
            parts=[types.Part(text=message.content)],
        )
        for message in request.messages
        if message.role != "system"
    ]
    return ("\n\n".join(system) if system else None), contents


class GoogleAdapter:
    """Streams one model turn through the Gemini Developer API as contract events."""

    def __init__(self, client_factory=None):
        self._client_factory = client_factory

    def build_client(self, config):
        """A client for one pinned profile: one transport attempt, bounded request."""
        api_key = resolve_credential(config.credential_ref)
        if api_key is None:
            raise ProviderError(
                "configuration_error",
                safe_message=(
                    f"the credential binding {config.credential_ref!r} is not configured "
                    "for this installation; choose another profile"
                ),
            )
        return Client(
            api_key=api_key,
            http_options=transport_options(config),
        )

    def _client(self, config):
        return (self._client_factory or self.build_client)(config)

    def _check_options(self, config) -> None:
        """A declared option this adapter does not perform is a gap, not a default."""
        if config.adapter_options.automatic_function_calling:
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    f"profile {config.id!r} declares automatic_function_calling, which this "
                    "adapter never performs: a call is the harness's to run"
                ),
            )

    async def stream(self, request: ModelRequest, config, control):
        """Yield text deltas, cumulative usage, and one terminal finish."""
        validate_request(request, config)
        self._check_options(config)
        client = self._client(config)
        system, contents = _system_and_contents(request)

        config_fields = {
            "max_output_tokens": request.max_output_tokens,
            # Both of these would make one attempt into several requests that
            # the runner never counted: the SDK runs a predicted call itself,
            # and it starts a follow-up request on a hop.
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
            "automatic_continuation": False,
        }
        if system is not None:
            config_fields["system_instruction"] = system
        kwargs = {
            "model": config.model,
            "contents": contents,
            "config": types.GenerateContentConfig(**config_fields),
        }

        async with client.aio as aclient:
            try:
                stream = await aclient.models.generate_content_stream(**kwargs)
            except _SDK_FAILURES as error:
                raise _sdk_failure(error) from error

            input_tokens: int | None = None
            output_tokens: int | None = None
            actual_model: str | None = None
            finish_reason: str | None = None
            produced_text = False
            blocked: str | None = None
            try:
                async for chunk in stream:
                    control.raise_if_stopped()

                    feedback = getattr(chunk, "prompt_feedback", None)
                    if feedback is not None:
                        blocked = _named(getattr(feedback, "block_reason", None))

                    served = getattr(chunk, "model_version", None)
                    if served and actual_model is None:
                        actual_model = served

                    for candidate in getattr(chunk, "candidates", None) or []:
                        content = getattr(candidate, "content", None)
                        parts = (
                            getattr(content, "parts", None) or []
                            if content is not None
                            else []
                        )
                        for part in parts:
                            # Only a text part is an answer. A thought is the
                            # model's own work, and a predicted call is the
                            # harness's to run: neither is answer text.
                            if getattr(part, "thought", False):
                                continue
                            text = getattr(part, "text", None)
                            if text:
                                produced_text = True
                                yield ProviderEvent(type="text_delta", text=text)

                        end = _named(getattr(candidate, "finish_reason", None))
                        if end is None:
                            continue
                        mapped = _END_REASON.get(end)
                        if mapped is None:
                            reason = _REJECTED_END.get(end)
                            if reason is None:
                                raise ProviderError(
                                    "invalid_response",
                                    cause=RuntimeError(
                                        f"this build has not seen the end {end!r}"
                                    ),
                                )
                            raise ProviderError(
                                "invalid_response",
                                cause=RuntimeError(f"the end {end!r}: {reason}"),
                            )
                        finish_reason = mapped

                    reported = _usage(
                        getattr(chunk, "usage_metadata", None),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                    if reported is not None:
                        input_tokens = reported.input_tokens
                        output_tokens = reported.output_tokens
                        yield ProviderEvent(type="usage_update", usage=reported)

                control.raise_if_stopped()
                if blocked is not None:
                    # A response the provider blocked before producing anything
                    # has no answer to state, and the block reason is this
                    # API's vocabulary: it stays in the cause, never the copy.
                    raise ProviderError(
                        "invalid_response",
                        cause=RuntimeError(f"the provider blocked the response: {blocked}"),
                    )
                if finish_reason is None:
                    raise ProviderError("invalid_response", cause=RuntimeError("no end marker"))
                if not produced_text and finish_reason == "stop":
                    raise ProviderError("invalid_response", cause=RuntimeError("empty answer"))
                if (
                    actual_model
                    and actual_model != config.model
                    and actual_model not in config.qualified_model_aliases
                ):
                    yield ProviderEvent(
                        type="stream_finished",
                        reason=finish_reason,
                        actual_model=actual_model,
                    )
                    raise ProviderError("unexpected_model", cause=RuntimeError("served model"))
                yield ProviderEvent(
                    type="stream_finished",
                    reason=finish_reason,
                    actual_model=actual_model,
                )
            except _SDK_FAILURES as error:
                # A provider that fails after it began answering is the same
                # failure as one that refuses to start; the stream is closed below.
                raise _sdk_failure(error) from error
            finally:
                await stream.aclose()

    async def count_input_tokens(self, request: ModelRequest, config, control) -> int:
        """Count only with the counter this profile qualifies; never estimate."""
        validate_request(request, config)
        self._check_options(config)
        if not config.capabilities.token_counting:
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    f"profile {config.id!r} declares no qualified token counter, so input "
                    "tokens cannot be counted"
                ),
            )
        declared = config.adapter_options.token_counter_id
        if declared != NATIVE_COUNTER:
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    f"profile {config.id!r} names the counter {declared!r}, which this "
                    "adapter does not implement; declare a profile that names "
                    f"{NATIVE_COUNTER!r}"
                ),
            )
        control.raise_if_stopped()
        timeout = control.request_timeout_seconds(config.limits.request_timeout_seconds)
        if timeout <= 0:
            raise ProviderError("budget_exceeded")
        client = self._client(config)
        system, contents = _system_and_contents(request)

        config_fields = {
            "http_options": types.HttpOptions(
                timeout=max(1, int(timeout * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1),
            )
        }
        if system is not None:
            config_fields["system_instruction"] = system
        kwargs = {"model": config.model, "contents": contents}
        if config_fields:
            kwargs["config"] = types.GenerateContentConfig(**config_fields)
        try:
            async with client.aio as aclient:
                counted = await aclient.models.count_tokens(**kwargs)
        except _SDK_FAILURES as error:
            raise _sdk_failure(error) from error
        reported = getattr(counted, "total_tokens", None)
        if reported is None:
            # A counter that answered without a count is not a count of zero:
            # the caller reserves against this number, and a guess would
            # reserve the wrong amount.
            raise ProviderError(
                "invalid_response",
                cause=RuntimeError("the provider's counter reported no count"),
            )
        return reported


def _build_adapter(profile) -> GoogleAdapter:
    """The adapter for a pinned profile: the SDK is imported only here."""
    return GoogleAdapter()


#: Registered by the adapter itself, so the registry never invents one and no
#: module outside this one imports the SDK.
register_adapter("google", _build_adapter)
