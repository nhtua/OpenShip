"""The OpenAI adapter: the qualified Chat Completions baseline.

The SDK is imported only here. Everything this module hands on is a
provider-neutral contract, so the graph, the worker, and the browser never
see an SDK object. The SDK never retries on its own either: OpenShip owns
request counts, timeouts, and fallback.
"""

from __future__ import annotations

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
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


def _usage_from(provider) -> TokenUsage:
    """Provider-reported counts, mapped without inventing anything."""
    details = getattr(provider, "prompt_tokens_details", None)
    completion_details = getattr(provider, "completion_tokens_details", None)
    return TokenUsage(
        input_tokens=getattr(provider, "prompt_tokens", None),
        output_tokens=getattr(provider, "completion_tokens", None),
        total_tokens=getattr(provider, "total_tokens", None),
        cached_input_tokens=getattr(details, "cached_tokens", None),
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", None),
        source="provider",
    )


def _retry_after(error: BaseException) -> float | None:
    """A provider's own repair hint, read from the response it returned."""
    response = getattr(error, "response", None)
    headers = getattr(error, "response_headers", None)
    if headers is None:
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


class OpenAIAdapter:
    """Streams one model turn through Chat Completions as contract events."""

    def __init__(self, client_factory=None):
        self._client_factory = client_factory

    def build_client(self, config) -> AsyncOpenAI:
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
        return AsyncOpenAI(
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

        kwargs = {
            "model": config.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            config.adapter_options.output_limit_parameter: request.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "timeout": control.request_timeout_seconds(config.limits.request_timeout_seconds),
        }
        try:
            stream = await client.chat.completions.create(**kwargs)
        except RateLimitError as error:
            raise ProviderError(
                "rate_limited", retry_after_seconds=_retry_after(error), cause=error
            ) from error
        except (APIConnectionError, APITimeoutError) as error:
            raise ProviderError("provider_unavailable", cause=error) from error
        except AuthenticationError as error:
            raise ProviderError("authentication_error", cause=error) from error
        except BadRequestError as error:
            raise ProviderError("invalid_request", cause=error) from error

        finish_reason: str | None = None
        actual_model: str | None = None
        produced_text = False
        try:
            async for chunk in stream:
                control.raise_if_stopped()
                if chunk.usage is not None:
                    yield ProviderEvent(
                        type="usage_update", usage=_usage_from(chunk.usage)
                    )
                for choice in chunk.choices or []:
                    delta = getattr(choice, "delta", None)
                    if delta and delta.content:
                        produced_text = True
                        yield ProviderEvent(type="text_delta", text=delta.content)
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                if chunk.model:
                    actual_model = chunk.model

            control.raise_if_stopped()
            if finish_reason is None:
                raise ProviderError("invalid_response", cause=RuntimeError("no finish marker"))
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
        finally:
            await stream.aclose()

    async def count_input_tokens(self, request: ModelRequest, config, control) -> int:
        """Count only with a qualified counter; never estimate one."""
        validate_request(request, config)
        if not config.capabilities.token_counting:
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    f"profile {config.id!r} declares no qualified token counter, so input "
                    "tokens cannot be counted"
                ),
            )
        control.raise_if_stopped()
        raise ProviderError(
            "unsupported_capability",
            safe_message=(
                f"no qualified counter is implemented for profile {config.id!r}; "
                "declare a profile that qualifies one"
            ),
        )


def _build_adapter(profile) -> OpenAIAdapter:
    """The adapter for a pinned profile: the SDK is imported only here."""
    return OpenAIAdapter()


#: Registered by the adapter itself, so the registry never invents one and no
#: module outside this one imports the SDK.
register_adapter("openai", _build_adapter)
