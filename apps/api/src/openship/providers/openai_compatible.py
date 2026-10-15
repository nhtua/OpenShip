"""The OpenAI-compatible adapter: a destination the operator pointed at.

No SDK is imported here, and that is the point. A separately configured
endpoint is an HTTP server, and the OpenAI SDK's own conventions are exactly
the parts that stop being proven at one: it attaches the installation's
credential to a destination the profile named, it retries on its own, and it
parses the stream on terms this adapter cannot see. What is reused is the
mapping - the Chat Completions request body and the chunk-to-event reading
the first adapter does - and nothing else.

The differences are declared profile options rather than inferred from
exceptions: which output-limit parameter the endpoint accepts, whether it
reports counts at all, which endpoint implementation it speaks, and whether
it needs a credential. An option this build has not qualified is a gap
stated before the network.
"""

from __future__ import annotations

import json

from httpx import AsyncClient, HTTPStatusError, TransportError

from src.openship.providers.configuration import resolve_credential
from src.openship.providers.contracts import (
    ModelRequest,
    ProviderEvent,
    TokenUsage,
    validate_request,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import register_adapter

CHAT_COMPLETIONS_PATH = "/chat/completions"

#: The one endpoint implementation this adapter has qualified. A profile that
#: names another speaks a different API, and the gap is stated before the
#: network rather than guessed at.
QUALIFIED_IMPLEMENTATION = "chat-completions"

#: The end names Chat Completions uses, mapped to the contract's. An end this
#: build has not seen is not guessed at.
_END_REASON = {
    "stop": "stop",
    "length": "length",
    "content_filter": "refusal",
    # This build never runs a tool, but the end is a fact the provider stated,
    # and claiming the answer was malformed would be a different claim.
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
}


def request_body(request: ModelRequest, config) -> dict:
    """The Chat Completions body this profile asks for, stated in one place.

    Public because the claim that a compatible endpoint is asked the way
    Chat Completions is, is a claim about this body - and about the one
    option an endpoint that will not report counts is never asked for.
    """
    body = {
        "model": config.model,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        config.adapter_options.output_limit_parameter: request.max_output_tokens,
        "stream": True,
    }
    if config.adapter_options.stream_usage_supported:
        body["stream_options"] = {"include_usage": True}
    return body


def _usage_from(payload: dict) -> TokenUsage:
    """Provider-reported counts, mapped without inventing anything.

    The same claim the first adapter makes, read where an HTTP server spells
    them: as JSON rather than as an SDK's objects. A count the endpoint did
    not state stays unknown rather than zero.
    """
    details = payload.get("prompt_tokens_details") or {}
    completion_details = payload.get("completion_tokens_details") or {}
    return TokenUsage(
        input_tokens=payload.get("prompt_tokens"),
        output_tokens=payload.get("completion_tokens"),
        total_tokens=payload.get("total_tokens"),
        cached_input_tokens=details.get("cached_tokens"),
        reasoning_tokens=completion_details.get("reasoning_tokens"),
        source="provider",
    )


def _retry_after(response) -> float | None:
    """A provider's own repair hint, read from the response that returned it."""
    hint = response.headers.get("retry-after")
    if hint is None:
        return None
    try:
        value = float(hint)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _http_failure(error: BaseException) -> ProviderError:
    """The normalized error for an HTTP failure. Total over what is caught.

    A server that answered nothing and a server that answered 500 are the
    same failure; a server that refused the request this build sent is not
    retried, because resending it would be a guess about a parameter the
    profile never declared.
    """
    if not isinstance(error, HTTPStatusError):
        return ProviderError("provider_unavailable", cause=error)
    status = error.response.status_code
    if status == 429:
        return ProviderError(
            "rate_limited", retry_after_seconds=_retry_after(error.response), cause=error
        )
    if status in (401, 403):
        return ProviderError("authentication_error", cause=error)
    if 400 <= status < 500:
        return ProviderError("invalid_request", cause=error)
    return ProviderError("provider_unavailable", cause=error)


class OpenAICompatibleAdapter:
    """Serves one declared Chat Completions-shaped endpoint."""

    def __init__(self, client_factory=None):
        self._client_factory = client_factory

    def build_client(self, config) -> AsyncClient:
        """A client for one pinned profile: the credential is the profile's own."""
        headers = {"Content-Type": "application/json"}
        if config.adapter_options.authentication == "bearer":
            credential = resolve_credential(config.credential_ref)
            if credential is None:
                raise ProviderError(
                    "configuration_error",
                    safe_message=(
                        f"the credential binding {config.credential_ref!r} is not configured "
                        "for this installation; choose another profile"
                    ),
                )
            headers["Authorization"] = f"Bearer {credential}"
        # A profile that declared no authentication sends nothing: the
        # installation's own environment is never read for a destination the
        # operator named separately.
        return AsyncClient(headers=headers)

    def _client(self, config):
        return (self._client_factory or self.build_client)(config)

    async def stream(self, request: ModelRequest, config, control):
        """Yield text deltas, cumulative usage, and one terminal finish."""
        validate_request(request, config)
        declared = config.adapter_options.endpoint_implementation
        if declared != QUALIFIED_IMPLEMENTATION:
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    f"profile {config.id!r} declares the {declared!r} endpoint "
                    f"implementation, which this build has not qualified; declare "
                    f"{QUALIFIED_IMPLEMENTATION!r} or choose another profile"
                ),
            )
        client = self._client(config)

        finish_reason: str | None = None
        actual_model: str | None = None
        produced_text = False
        ended = False
        try:
            async with client.stream(
                "POST",
                f"{config.endpoint}{CHAT_COMPLETIONS_PATH}",
                json=request_body(request, config),
                timeout=control.request_timeout_seconds(
                    config.limits.request_timeout_seconds
                ),
                follow_redirects=False,
            ) as response:
                if 300 <= response.status_code < 400:
                    # The profile named a destination; this one answered by
                    # pointing somewhere else. Following it would send the
                    # conversation somewhere the operator never declared.
                    raise ProviderError(
                        "configuration_error",
                        cause=RuntimeError("redirected destination"),
                    )
                response.raise_for_status()
                try:
                    async for line in response.aiter_lines():
                        control.raise_if_stopped()
                        if not line or line.startswith(":"):
                            # Framing that carries no event: part of the
                            # protocol, never answer text.
                            continue
                        if not line.startswith("data:"):
                            # An event name this build does not read.
                            continue
                        payload = line[len("data:") :].strip()
                        if payload == "[DONE]":
                            ended = True
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            raise ProviderError(
                                "invalid_response",
                                cause=RuntimeError("malformed chunk"),
                            ) from None
                        if not isinstance(chunk, dict):
                            raise ProviderError(
                                "invalid_response",
                                cause=RuntimeError("chunk without fields"),
                            )
                        usage = chunk.get("usage")
                        if usage is not None:
                            yield ProviderEvent(
                                type="usage_update", usage=_usage_from(usage)
                            )
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                produced_text = True
                                yield ProviderEvent(
                                    type="text_delta", text=delta["content"]
                                )
                            if choice.get("finish_reason"):
                                finish_reason = choice["finish_reason"]
                        if chunk.get("model"):
                            actual_model = chunk["model"]
                except TransportError as error:
                    # A server that fails after it began answering is the
                    # same failure as one that refuses to start.
                    raise _http_failure(error) from error

            control.raise_if_stopped()
            if not ended:
                raise ProviderError(
                    "invalid_response", cause=RuntimeError("no terminal marker")
                )
            if finish_reason is None:
                raise ProviderError(
                    "invalid_response", cause=RuntimeError("no finish marker")
                )
            if not produced_text and finish_reason == "stop":
                raise ProviderError("invalid_response", cause=RuntimeError("empty answer"))
            reason = _END_REASON.get(finish_reason)
            if reason is None:
                raise ProviderError(
                    "invalid_response",
                    cause=RuntimeError(f"this build has not seen the end {finish_reason!r}"),
                )
            if (
                actual_model
                and actual_model != config.model
                and actual_model not in config.qualified_model_aliases
            ):
                yield ProviderEvent(
                    type="stream_finished", reason=reason, actual_model=actual_model
                )
                raise ProviderError("unexpected_model", cause=RuntimeError("served model"))
            yield ProviderEvent(type="stream_finished", reason=reason, actual_model=actual_model)
        except (TransportError, HTTPStatusError) as error:
            raise _http_failure(error) from error

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


def _build_adapter(profile) -> OpenAICompatibleAdapter:
    """The adapter for a pinned profile: no SDK, so nothing to import here."""
    return OpenAICompatibleAdapter()


#: Registered by the adapter itself, so the registry never invents one.
register_adapter("openai_compatible", _build_adapter)
