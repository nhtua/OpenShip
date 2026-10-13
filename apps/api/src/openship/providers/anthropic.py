"""The Anthropic adapter: the qualified Messages API baseline.

The SDK is imported only here. Everything this module hands on is a
provider-neutral contract, so the graph, the worker, and the browser never
see an SDK object. The SDK never retries on its own either: OpenShip owns
request counts, timeouts, and fallback - which is why a profile of this
adapter declares no server-side fallback.

Where this API differs from the first adapter is stated at each place it
matters: the system turn is its own parameter, an answer is a sequence of
blocks of which only text is the answer, the input count is reported only
in the snapshot that opens a stream, and the end is named differently.
"""

from __future__ import annotations

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
    BadRequestError,
    OverloadedError,
    RateLimitError,
)

from src.openship.providers.configuration import resolve_credential
from src.openship.providers.contracts import (
    ModelRequest,
    ProviderEvent,
    TokenUsage,
    validate_request,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import register_adapter

_UNAVAILABLE = "The model provider could not be reached."

#: The counter this adapter implements, by the name an operator declares. A
#: profile that names another one is not served by a different counter.
NATIVE_COUNTER = "anthropic-count-tokens"

#: This API's ends, in the contract's vocabulary. An end that is not here is
#: not guessed: a new name is a change to the API's vocabulary, and claiming
#: a complete answer because one arrived would be a claim without evidence.
_END_REASON = {
    "end_turn": "stop",
    "model_end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "content_filtered": "refusal",
}

#: The failures this adapter maps, and only these are caught: an SDK failure
#: with no mapping here is not turned into a claim about the provider.
_SDK_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OverloadedError,
    RateLimitError,
)


def _usage(*, input_tokens=None, output_tokens=None) -> TokenUsage:
    """Provider-reported counts, mapped without inventing anything.

    This API reports no total and no cached count, so neither is claimed -
    a total would be an addition the provider did not make.
    """
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=None,
        cached_input_tokens=None,
        reasoning_tokens=None,
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


def _sdk_failure(error: BaseException) -> ProviderError | None:
    """The normalized error for an SDK failure, or None when it is not one.

    Both the request and the stream map through here: a provider that fails
    after it began answering is the same failure as one that refuses to start.
    An overloaded server is unreachable, not rate limited - it offered no
    delay to wait for.
    """
    if isinstance(error, RateLimitError):
        return ProviderError(
            "rate_limited", retry_after_seconds=_retry_after(error), cause=error
        )
    if isinstance(error, (APIConnectionError, APITimeoutError, OverloadedError)):
        return ProviderError("provider_unavailable", cause=error)
    if isinstance(error, AuthenticationError):
        return ProviderError("authentication_error", cause=error)
    if isinstance(error, BadRequestError):
        return ProviderError("invalid_request", cause=error)
    return None


def _system_and_history(request: ModelRequest) -> tuple[str | None, list[dict]]:
    """The system turns, and the history without them.

    This API takes the system turn as its own parameter rather than as a
    message. Nothing here rewrites the conversation: the same text is put
    where this API reads it.
    """
    system = [message.content for message in request.messages if message.role == "system"]
    history = [
        {"role": message.role, "content": message.content}
        for message in request.messages
        if message.role != "system"
    ]
    return ("\n\n".join(system) if system else None), history


class AnthropicAdapter:
    """Streams one model turn through the Messages API as contract events."""

    def __init__(self, client_factory=None):
        self._client_factory = client_factory

    def build_client(self, config) -> AsyncAnthropic:
        """A client for one pinned profile: no SDK retries, bounded request."""
        api_key = resolve_credential(config.credential_ref)
        if api_key is None:
            raise ProviderError(
                "configuration_error",
                safe_message=(
                    f"the credential binding {config.credential_ref!r} is not configured "
                    "for this installation; choose another profile"
                ),
            )
        return AsyncAnthropic(
            api_key=api_key,
            base_url=config.endpoint,
            timeout=config.limits.request_timeout_seconds,
            max_retries=0,
        )

    def _client(self, config):
        return (self._client_factory or self.build_client)(config)

    async def stream(self, request: ModelRequest, config, control):
        """Yield text deltas, cumulative usage, and one terminal finish."""
        validate_request(request, config)
        client = self._client(config)
        system, history = _system_and_history(request)

        kwargs = {
            "model": config.model,
            "messages": history,
            "max_tokens": request.max_output_tokens,
            "stream": True,
            "timeout": control.request_timeout_seconds(config.limits.request_timeout_seconds),
        }
        if system is not None:
            kwargs["system"] = system
        try:
            stream = await client.messages.create(**kwargs)
        except _SDK_ERRORS as error:
            raise _sdk_failure(error) from error

        input_tokens: int | None = None
        output_tokens: int | None = None
        actual_model: str | None = None
        finish_reason: str | None = None
        produced_text = False
        try:
            async for event in stream:
                control.raise_if_stopped()
                kind = getattr(event, "type", None)

                if kind == "message_start":
                    # The only place this API reports the input count, so it
                    # is kept for the rest of the stream rather than re-read.
                    snapshot = getattr(event, "message", None)
                    reported = getattr(snapshot, "usage", None)
                    if reported is not None:
                        input_tokens = getattr(reported, "input_tokens", None)
                        yield ProviderEvent(
                            type="usage_update",
                            usage=_usage(
                                input_tokens=input_tokens,
                                output_tokens=getattr(reported, "output_tokens", None),
                            ),
                        )
                    served = getattr(snapshot, "model", None)
                    if served:
                        actual_model = served
                    continue

                if kind == "content_block_delta":
                    # Only a text block is an answer. Reasoning and tool
                    # blocks are the model's own work: they are not answer
                    # text, and nothing here runs them.
                    delta = getattr(event, "delta", None)
                    if delta is not None and getattr(delta, "type", None) == "text_delta":
                        text = getattr(delta, "text", "")
                        if text:
                            produced_text = True
                            yield ProviderEvent(type="text_delta", text=text)
                    continue

                if kind == "message_delta":
                    reported = getattr(event, "usage", None)
                    if reported is not None:
                        output_tokens = getattr(reported, "output_tokens", None)
                        yield ProviderEvent(
                            type="usage_update",
                            usage=_usage(
                                input_tokens=input_tokens, output_tokens=output_tokens
                            ),
                        )
                    end = getattr(event, "delta", None)
                    named = getattr(end, "stop_reason", None)
                    if named is not None:
                        finish_reason = _END_REASON.get(named)
                        if finish_reason is None:
                            raise ProviderError(
                                "invalid_response",
                                cause=RuntimeError(
                                    f"this build has not seen the end {named!r}"
                                ),
                            )
                    continue
            # A ping, a block boundary, and any event this build has not seen
            # are none of them an answer and none of them end one.

            control.raise_if_stopped()
            if finish_reason is None:
                raise ProviderError(
                    "invalid_response", cause=RuntimeError("no end marker")
                )
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
        except _SDK_ERRORS as error:
            # A provider that fails after it began answering is the same
            # failure as one that refuses to start; the stream is closed below.
            raise _sdk_failure(error) from error
        finally:
            await stream.close()

    async def count_input_tokens(self, request: ModelRequest, config, control) -> int:
        """Count only with the counter this profile qualifies; never estimate."""
        validate_request(request, config)
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
        client = self._client(config)
        system, history = _system_and_history(request)

        kwargs = {"model": config.model, "messages": history}
        if system is not None:
            kwargs["system"] = system
        try:
            counted = await client.messages.count_tokens(**kwargs)
        except _SDK_ERRORS as error:
            raise _sdk_failure(error) from error
        reported = getattr(counted, "input_tokens", None)
        if reported is None:
            # A counter that answered without a count is not a count of zero:
            # the caller reserves against this number, and a guess would
            # reserve the wrong amount.
            raise ProviderError(
                "invalid_response",
                cause=RuntimeError("the provider's counter reported no count"),
            )
        return reported


def _build_adapter(profile) -> AnthropicAdapter:
    """The adapter for a pinned profile: the SDK is imported only here."""
    return AnthropicAdapter()


#: Registered by the adapter itself, so the registry never invents one and no
#: module outside this one imports the SDK.
register_adapter("anthropic", _build_adapter)
