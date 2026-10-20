"""The OpenAI adapter: the qualified Chat Completions baseline.

The SDK is imported only here. Everything this module hands on is a
provider-neutral contract, so the graph, the worker, and the browser never
see an SDK object. The SDK never retries on its own either: OpenShip owns
request counts, timeouts, and fallback.
"""

from __future__ import annotations

import copy
import json

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from pydantic import ValidationError

from src.openship.providers.configuration import resolve_credential
from src.openship.providers.contracts import (
    ModelRequest,
    ProviderEvent,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
    validate_request,
)
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import register_adapter

_UNAVAILABLE = "The model provider could not be reached."
_MAX_TOOL_ARGUMENT_BYTES = 64 * 1024


def _strict_schema(schema: dict, *, optional: bool = False) -> dict:
    """Map a canonical JSON schema to Chat Completions strict function schema.

    The maintained capability schema remains the source of truth. Strict mode
    requires optional fields to appear as nullable fields and every object to
    list all of its properties as required, so this creates a wire-only copy.
    """
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    result = {}
    for key, value in schema.items():
        if key in {"default", "examples", "title"}:
            continue
        if key == "properties" and isinstance(value, dict):
            original_required = set(schema.get("required", ()))
            result[key] = {
                name: _strict_schema(child, optional=name not in original_required)
                for name, child in value.items()
            }
        elif key == "required" and isinstance(value, list):
            # The provider schema requires every declared property. Nullable
            # types below preserve the canonical distinction between required
            # and optional inputs.
            continue
        elif key == "items" and isinstance(value, dict):
            result[key] = _strict_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            result[key] = [_strict_schema(item) for item in value]
        elif key in {"$defs", "definitions"} and isinstance(value, dict):
            result[key] = {name: _strict_schema(child) for name, child in value.items()}
        else:
            result[key] = copy.deepcopy(value)

    if result.get("type") == "object":
        properties = result.get("properties", {})
        result["required"] = list(properties)
        result["additionalProperties"] = False

    if optional and not ("anyOf" in result and any(
        item.get("type") == "null" for item in result["anyOf"] if isinstance(item, dict)
    )):
        result = {"anyOf": [result, {"type": "null"}]}
    return result


def _normalize_arguments(schema: dict, arguments: dict) -> dict:
    """Restore canonical optional defaults after strict-schema nulls arrive."""
    if schema.get("type") != "object":
        return arguments
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    if set(arguments) - set(properties):
        raise ValueError("tool arguments contain undeclared properties")
    if required - set(arguments):
        raise ValueError("tool arguments omit required properties")

    normalized = dict(arguments)
    for name, child_schema in properties.items():
        if name not in normalized or normalized[name] is None:
            if name in required:
                raise ValueError("required tool argument is null")
            if "default" in child_schema:
                normalized[name] = copy.deepcopy(child_schema["default"])
            else:
                normalized.pop(name, None)
        elif isinstance(normalized[name], dict) and isinstance(child_schema, dict):
            normalized[name] = _normalize_arguments(child_schema, normalized[name])
    return normalized


def _wire_messages(request: ModelRequest) -> list[dict]:
    """Serialize normalized chat and tool turns to Chat Completions messages."""
    messages = []
    for message in request.messages:
        if message.role == "assistant" and message.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.provider_call_id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(
                                    call.arguments,
                                    ensure_ascii=False,
                                    allow_nan=False,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                        for call in message.tool_calls
                    ],
                }
            )
        elif message.role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "name": message.name,
                    "content": message.content,
                }
            )
        else:
            messages.append({"role": message.role, "content": message.content})
    return messages


def _wire_tools(request: ModelRequest) -> list[dict]:
    """Serialize the exact registered function IDs and strict wire schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": _strict_schema(tool.parameters),
                "strict": True,
            },
        }
        for tool in request.tools
    ]


def _invalid_tool_response(cause: BaseException | str) -> ProviderError:
    """A provider returned a malformed or unauthorized function call."""
    return ProviderError("invalid_response", cause=RuntimeError("invalid tool call response"))


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


def _sdk_failure(error: BaseException) -> ProviderError | None:
    """The normalized error for an SDK failure, or None when it is not one.

    Both the request and the stream map through here: a provider that fails
    after it began answering is the same failure as one that refuses to start.
    """
    if isinstance(error, RateLimitError):
        return ProviderError("rate_limited", retry_after_seconds=_retry_after(error), cause=error)
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return ProviderError("provider_unavailable", cause=error)
    if isinstance(error, AuthenticationError):
        return ProviderError("authentication_error", cause=error)
    if isinstance(error, BadRequestError):
        return ProviderError("invalid_request", cause=error)
    if isinstance(error, APIStatusError):
        status = error.status_code
        if 300 <= status < 400:
            return ProviderError(
                "configuration_error",
                safe_message="the model endpoint redirected to another destination; update its configured endpoint",
                cause=error,
            )
        if 500 <= status < 600:
            return ProviderError("provider_unavailable", cause=error)
        if 400 <= status < 500:
            return ProviderError("invalid_request", cause=error)
    return None


_SDK_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    APIStatusError,
)


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
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=config.endpoint,
            timeout=config.limits.request_timeout_seconds,
            max_retries=0,
        )
        client._client.follow_redirects = False
        return client

    def _client(self, config):
        return (self._client_factory or self.build_client)(config)

    async def stream(self, request: ModelRequest, config, control):
        """Yield text deltas, cumulative usage, and one terminal finish."""
        validate_request(request, config)
        client = self._client(config)

        kwargs = {
            "model": config.model,
            "messages": _wire_messages(request),
            config.adapter_options.output_limit_parameter: request.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "timeout": control.request_timeout_seconds(config.limits.request_timeout_seconds),
        }
        if request.tools:
            kwargs["tools"] = _wire_tools(request)
            # This profile is qualified for one function call per model turn.
            kwargs["parallel_tool_calls"] = False
        try:
            stream = await client.chat.completions.create(**kwargs)
        except _SDK_ERRORS as error:
            raise _sdk_failure(error) from error

        response = getattr(stream, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is not None and 300 <= status_code < 400:
            await response.aclose()
            raise ProviderError(
                "configuration_error",
                safe_message="the model endpoint redirected to another destination; update its configured endpoint",
            )

        finish_reason: str | None = None
        actual_model: str | None = None
        produced_text = False
        tool_fragments: dict[int, dict[str, str]] = {}
        try:
            async for chunk in stream:
                control.raise_if_stopped()
                if chunk.usage is not None:
                    yield ProviderEvent(
                        type="usage_update", usage=_usage_from(chunk.usage)
                    )
                for choice in chunk.choices or []:
                    delta = getattr(choice, "delta", None)
                    refusal = getattr(delta, "refusal", None) if delta else None
                    if refusal:
                        finish_reason = "refusal"
                    if delta and delta.content:
                        produced_text = True
                        yield ProviderEvent(type="text_delta", text=delta.content)
                    for fragment in (getattr(delta, "tool_calls", None) or ()):
                        index = getattr(fragment, "index", None)
                        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                            raise _invalid_tool_response("invalid tool call index")
                        values = tool_fragments.get(index)
                        if values is None:
                            if tool_fragments:
                                raise _invalid_tool_response("parallel tool calls are not qualified")
                            values = {"id": "", "name": "", "arguments": ""}
                            tool_fragments[index] = values

                        fragment_type = getattr(fragment, "type", None)
                        if fragment_type not in (None, "function"):
                            raise _invalid_tool_response("unsupported tool call type")
                        function = getattr(fragment, "function", None)
                        if function is None:
                            raise _invalid_tool_response("tool call fragment has no function")
                        pieces = {
                            "id": getattr(fragment, "id", None),
                            "name": getattr(function, "name", None),
                            "arguments": getattr(function, "arguments", None),
                        }
                        if any(value is not None and not isinstance(value, str) for value in pieces.values()):
                            raise _invalid_tool_response("tool call fragment fields must be text")
                        pieces = {key: value or "" for key, value in pieces.items()}
                        limits = {"id": 128, "name": 64}
                        for key, piece in pieces.items():
                            combined = values[key] + piece
                            if key in limits and len(combined) > limits[key]:
                                raise _invalid_tool_response("tool call identifier is too long")
                            if key == "arguments" and len(combined.encode("utf-8")) > _MAX_TOOL_ARGUMENT_BYTES:
                                raise _invalid_tool_response("tool arguments exceed the size limit")
                            values[key] = combined

                        if any(pieces.values()):
                            yield ProviderEvent(
                                type="tool_call_delta",
                                tool_call_delta=ToolCallDelta(
                                    index=index,
                                    id_delta=pieces["id"],
                                    name_delta=pieces["name"],
                                    arguments_delta=pieces["arguments"],
                                ),
                            )
                    if choice.finish_reason:
                        if choice.finish_reason not in {
                            "stop",
                            "length",
                            "refusal",
                            "content_filter",
                            "tool_calls",
                        }:
                            raise ProviderError(
                                "invalid_response", cause=RuntimeError("unknown finish reason")
                            )
                        if finish_reason != "refusal":
                            finish_reason = (
                                "refusal"
                                if choice.finish_reason == "content_filter"
                                else choice.finish_reason
                            )
                if chunk.model:
                    actual_model = chunk.model

            control.raise_if_stopped()
            if finish_reason is None:
                raise ProviderError("invalid_response", cause=RuntimeError("no finish marker"))
            if bool(tool_fragments) != (finish_reason == "tool_calls"):
                raise _invalid_tool_response("tool call content and finish reason do not match")
            if not produced_text and not tool_fragments and finish_reason == "stop":
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

            if tool_fragments:
                allowed = {tool.name: tool for tool in request.tools}
                call_ids = set()
                ready_calls = []
                for index, fragments in sorted(tool_fragments.items()):
                    provider_call_id = fragments["id"]
                    name = fragments["name"]
                    if not provider_call_id or not name:
                        raise _invalid_tool_response("tool call is missing its ID or function name")
                    if provider_call_id in call_ids:
                        raise _invalid_tool_response("provider returned duplicate tool call IDs")
                    call_ids.add(provider_call_id)
                    definition = allowed.get(name)
                    if definition is None:
                        raise _invalid_tool_response("provider returned a function outside the allowlist")
                    try:
                        arguments = json.loads(
                            fragments["arguments"],
                            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
                        )
                        if not isinstance(arguments, dict):
                            raise TypeError("tool arguments must be a JSON object")
                        arguments = _normalize_arguments(definition.parameters, arguments)
                        ready_calls.append(
                            ToolCall(
                                provider_call_id=provider_call_id,
                                name=name,
                                arguments=arguments,
                            )
                        )
                    except (json.JSONDecodeError, TypeError, ValueError) as error:
                        raise _invalid_tool_response(error) from error
                    except ValidationError as error:
                        # Pydantic rejects invalid contract fields with its own
                        # exception type; keep that provider detail contained.
                        raise _invalid_tool_response(error) from error

                for call in ready_calls:
                    yield ProviderEvent(type="tool_call_ready", tool_call=call)
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
