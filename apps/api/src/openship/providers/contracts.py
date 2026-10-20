"""Provider-neutral contracts.

This is the only vocabulary the adapters, the runner, the worker, and the
browser share. Everything here is frozen, serializable, and secret-free:
credentials are named by reference and resolved only where a client is built.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from src.openship.providers.errors import ProviderError, describe_validation

PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
TOOL_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

ADAPTER_NAMES = ("openai", "anthropic", "google", "openai_compatible")

_CAPABILITY_NAMES = (
    "streaming",
    "usage_reporting",
    "token_counting",
    "tool_calls",
    "structured_output",
    "parallel_tool_calls",
)

_OPTION_NAMES = (
    "output_limit_parameter",
    "token_counter_id",
    "server_side_fallback",
    "automatic_function_calling",
    "native_api_version",
    "endpoint_implementation",
    "authentication",
    "stream_usage_supported",
)


def _require_json_object(value: dict[str, Any], label: str) -> dict[str, Any]:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, UnicodeEncodeError) as invalid:
        raise ValueError(f"{label} must contain only finite JSON values") from invalid
    return value


class ToolDefinition(BaseModel):
    """One exact, server-authorized function exposed to a model request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=TOOL_ID_PATTERN, min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    parameters: dict[str, Any]

    @field_validator("parameters")
    @classmethod
    def _json_parameters(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _require_json_object(value, "tool parameters")

    @model_validator(mode="after")
    def _object_schema(self):
        if self.parameters.get("type") != "object":
            raise ValueError("tool parameters must be an object schema")
        if self.parameters.get("additionalProperties") is not False:
            raise ValueError("tool parameters must reject additional properties")
        properties = self.parameters.get("properties", {})
        required = self.parameters.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise TypeError("tool parameters must declare object properties and required fields")
        if any(not isinstance(name, str) for name in required):
            raise TypeError("tool schema required fields must be strings")
        if len(required) != len(set(required)) or not set(required).issubset(properties):
            raise ValueError("tool schema required fields must be unique declared properties")
        return self


class ToolCall(BaseModel):
    """One complete normalized call; the provider ID is never an internal ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_call_id: str = Field(min_length=1, max_length=128)
    name: str = Field(pattern=TOOL_ID_PATTERN, min_length=1, max_length=64)
    arguments: dict[str, Any]

    @field_validator("arguments")
    @classmethod
    def _json_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _require_json_object(value, "tool arguments")


class ToolCallDelta(BaseModel):
    """Transient provider stream fragments. They are never transcript entries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0)
    id_delta: str = Field(default="", max_length=256)
    name_delta: str = Field(default="", max_length=256)
    arguments_delta: str = Field(default="", max_length=65536)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: tuple[ToolCall, ...] | None = None
    name: str | None = Field(default=None, pattern=TOOL_ID_PATTERN, max_length=64)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _role_shape(self):
        if self.role == "assistant":
            if self.name is not None or self.tool_call_id is not None:
                raise ValueError("assistant messages cannot carry tool-result fields")
            if self.tool_calls is not None:
                if not self.tool_calls:
                    raise ValueError("assistant tool_calls must not be empty")
                ids = [call.provider_call_id for call in self.tool_calls]
                if len(ids) != len(set(ids)):
                    raise ValueError("assistant tool call IDs must be distinct")
        elif self.role == "tool":
            if not self.name or not self.tool_call_id:
                raise ValueError("tool messages require name and tool_call_id")
            if self.tool_calls is not None:
                raise ValueError("tool messages cannot contain tool_calls")
        elif self.name is not None or self.tool_call_id is not None or self.tool_calls is not None:
            raise ValueError("system and user messages cannot carry tool fields")
        return self


class ProviderCapabilities(BaseModel):
    """What a qualified profile can actually be asked for."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    streaming: bool
    usage_reporting: bool
    context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    token_counting: bool = False
    tool_calls: bool = False
    structured_output: bool = False
    attachment_types: tuple[str, ...] = ()
    parallel_tool_calls: bool = False


class RunLimits(BaseModel):
    """One shared budget across the initial call, retries, and fallback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_output_tokens: int = Field(default=4000, gt=0)
    max_attempts: int = Field(default=3, ge=1)
    run_timeout_seconds: float = Field(default=120.0, gt=0)
    request_timeout_seconds: float = Field(default=45.0, gt=0)
    max_context_bytes: int = Field(default=65536, gt=0)
    max_total_tokens: int | None = Field(default=None, gt=0)
    max_cost_usd: Decimal | None = Field(default=None, ge=0)


class ProfilePricing(BaseModel):
    """A conservative schedule an operator qualified for a profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: str = Field(min_length=1, max_length=64)
    currency: Literal["USD"]
    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)


class AdapterOptions(BaseModel):
    """Explicit, adapter-discriminated options. Arbitrary SDK kwargs are not."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: Literal[ADAPTER_NAMES]
    output_limit_parameter: Literal["max_tokens", "max_completion_tokens"] | None = None
    token_counter_id: str | None = Field(default=None, min_length=1, max_length=128)
    server_side_fallback: bool | None = None
    automatic_function_calling: bool | None = None
    native_api_version: str | None = Field(default=None, min_length=1, max_length=64)
    endpoint_implementation: str | None = Field(default=None, min_length=1, max_length=128)
    authentication: Literal["bearer", "none"] | None = None
    stream_usage_supported: bool | None = None

    @model_validator(mode="after")
    def _check_options(self):
        required = {
            "openai": ("output_limit_parameter",),
            "anthropic": ("server_side_fallback",),
            "google": ("native_api_version", "automatic_function_calling"),
            "openai_compatible": (
                "endpoint_implementation",
                "authentication",
                "stream_usage_supported",
                "output_limit_parameter",
            ),
        }[self.adapter]
        present = {name for name in _OPTION_NAMES if getattr(self, name) is not None}
        missing = sorted(name for name in required if name not in present)
        if missing:
            raise ValueError(
                f"adapter_options for {self.adapter} must declare " + ", ".join(missing)
            )
        allowed = set(required) | {"token_counter_id"}
        unexpected = sorted(present - allowed)
        if unexpected:
            raise ValueError(
                f"adapter_options for {self.adapter} must not declare " + ", ".join(unexpected)
            )
        return self


class _ProfileBase(BaseModel):
    """Fields every profile carries, whether declared by an operator or pinned."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=PROFILE_ID_PATTERN)
    label: str = Field(min_length=1, max_length=120)
    adapter: Literal[ADAPTER_NAMES]
    model: str = Field(min_length=1, max_length=128)
    endpoint: str
    api_version: str = Field(min_length=1, max_length=64)
    credential_ref: str = Field(default="", max_length=128)
    capabilities: ProviderCapabilities
    allowed_fallback_profile_ids: tuple[str, ...] = ()
    qualified_model_aliases: tuple[str, ...] = ()
    adapter_options: AdapterOptions
    pricing: ProfilePricing | None = None
    limits: RunLimits | None = None

    @model_validator(mode="after")
    def _claim_only_what_is_wired(self):
        """A capability may only be claimed where the mechanism is named.

        ``token_counting`` is the one claimed capability that is not a
        provider behaviour the adapter reads off a stream: it is a call this
        build makes, and which call is decided by the profile. A profile that
        claims it without naming a counter would look enforceable and only
        fail at request time - so the declaration is refused here, and the
        adapter still refuses a counter it does not implement.
        """
        if self.capabilities.token_counting and self.adapter_options.token_counter_id is None:
            raise ValueError(
                f"profile {self.id!r} claims token_counting but names no "
                "token_counter_id for its adapter to call"
            )
        return self


class ProviderProfile(_ProfileBase):
    """An operator-declared profile, before it is pinned to a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PinnedProviderConfig(_ProfileBase):
    """An immutable snapshot persisted with a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_revision: str = Field(min_length=1)


class TokenUsage(BaseModel):
    """Provider-reported counts. Missing stays missing; nothing is invented."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    source: Literal["provider", "unavailable"] = "unavailable"


class ProviderEvent(BaseModel):
    """A closed tagged union: text deltas, cumulative usage, a terminal finish."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[
        "text_delta",
        "usage_update",
        "tool_call_delta",
        "tool_call_ready",
        "stream_finished",
    ]
    text: str | None = None
    usage: TokenUsage | None = None
    tool_call_delta: ToolCallDelta | None = None
    tool_call: ToolCall | None = None
    reason: Literal["stop", "length", "refusal", "tool_calls"] | None = None
    actual_model: str | None = None

    @model_validator(mode="after")
    def _check_tag(self):
        required = {
            "text_delta": ("text",),
            "usage_update": ("usage",),
            "tool_call_delta": ("tool_call_delta",),
            "tool_call_ready": ("tool_call",),
            "stream_finished": ("reason",),
        }[self.type]
        present = {
            name
            for name in (
                "text",
                "usage",
                "tool_call_delta",
                "tool_call",
                "reason",
                "actual_model",
            )
            if getattr(self, name) is not None
        }
        missing = [name for name in required if name not in present]
        if missing:
            raise ValueError(f"{self.type} requires {', '.join(missing)}")
        allowed = {
            "text_delta": {"text"},
            "usage_update": {"usage"},
            "tool_call_delta": {"tool_call_delta"},
            "tool_call_ready": {"tool_call"},
            "stream_finished": {"reason", "actual_model"},
        }[self.type]
        unexpected = present - allowed
        if unexpected:
            raise ValueError(f"{self.type} carries only {', '.join(sorted(allowed))}")
        return self


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    tools: tuple[ToolDefinition, ...] = ()
    required_capabilities: frozenset[str] = frozenset({"streaming"})

    @model_validator(mode="after")
    def _unique_tools(self):
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool definition names must be unique")
        allowed = set(names)
        calls = {
            call.provider_call_id: call.name
            for message in self.messages
            for call in (message.tool_calls or ())
        }
        results = {}
        for message in self.messages:
            if message.role != "tool":
                continue
            expected_name = calls.get(message.tool_call_id)
            if expected_name != message.name or message.tool_call_id in results:
                raise ValueError("tool results must match one prior assistant tool call")
            results[message.tool_call_id] = message.name

        # Tool definitions authorize new model calls. Complete historical
        # call/result pairs remain in context after an operator disables a tool.
        unknown = {
            call.name
            for message in self.messages
            for call in (message.tool_calls or ())
            if call.name not in allowed and results.get(call.provider_call_id) != call.name
        }
        if unknown:
            raise ValueError(
                "tool names are not present in the request allowlist: "
                + ", ".join(sorted(unknown))
            )
        return self


class ProviderTurnResult(BaseModel):
    """What one bounded model turn produced. Partial output is never an answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage | None = None
    actual_model: str | None = None
    selected_profile_id: str = Field(min_length=1)
    attempt_count: int = Field(default=1, ge=1)
    finish_reason: str
    partial: bool = False

    @property
    def completed(self) -> bool:
        """Only a clean stop with nonempty output may publish an answer."""
        return (
            self.finish_reason == "stop"
            and bool(self.text)
            and not self.tool_calls
            and not self.partial
        )


class ProfileAvailability(BaseModel):
    """Why a profile can or cannot be used now — without naming any secret."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    available: bool
    reason: Literal["missing_credentials", "adapter_unavailable", "configuration_error"] | None = None
    explanation: str = ""


class RunModelConfig(BaseModel):
    """The immutable provider configuration a run executes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    primary: PinnedProviderConfig
    fallbacks: tuple[PinnedProviderConfig, ...] = ()
    limits: RunLimits
    binding_origin: Literal["submission", "legacy_bound_at_execution"] = "submission"


def pin_profile(profile, *, defaults: RunLimits | None = None) -> PinnedProviderConfig:
    """Validate a declared profile and freeze it with a stable digest.

    Accepts an operator profile or an already-pinned snapshot so that a
    persisted snapshot round-trips unchanged.
    """
    data = dict(profile) if isinstance(profile, dict) else profile.model_dump()
    data.pop("profile_revision", None)
    try:
        declared = ProviderProfile(**data)
    except ValidationError as invalid:
        raise ProviderError(
            "configuration_error",
            safe_message=f"invalid model profile: {describe_validation(invalid)}",
        ) from invalid

    limits = _merge_limits(defaults, declared.limits)
    if limits.max_output_tokens > declared.capabilities.max_output_tokens:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"profile {declared.id!r} declares an output limit above its "
                f"qualified maximum of {declared.capabilities.max_output_tokens} tokens"
            ),
        )

    canonical = declared.model_dump(mode="json")
    canonical["limits"] = limits.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PinnedProviderConfig(**canonical, profile_revision=digest)


def _merge_limits(*candidates: RunLimits | None) -> RunLimits:
    """The strictest of the installation defaults and the profile's own limits."""
    present = [limits for limits in candidates if limits is not None]
    if not present:
        return RunLimits()

    def field_min(name: str) -> object:
        values = [getattr(limits, name) for limits in present]
        numeric = [value for value in values if value is not None]
        if not numeric:
            return None
        return min(numeric)

    return RunLimits(
        max_output_tokens=field_min("max_output_tokens"),
        max_attempts=field_min("max_attempts"),
        run_timeout_seconds=field_min("run_timeout_seconds"),
        request_timeout_seconds=field_min("request_timeout_seconds"),
        max_context_bytes=field_min("max_context_bytes"),
        max_total_tokens=field_min("max_total_tokens"),
        max_cost_usd=field_min("max_cost_usd"),
    )


def declared_capabilities(profile: PinnedProviderConfig) -> frozenset[str]:
    """The capability names a qualified profile will accept."""
    names = {name for name in _CAPABILITY_NAMES if getattr(profile.capabilities, name)}
    if profile.capabilities.attachment_types:
        names.add("attachments")
    return frozenset(names)


def validate_request(request: ModelRequest, profile: PinnedProviderConfig) -> None:
    """Refuse capabilities the profile does not qualify, before any dispatch."""
    required = set(request.required_capabilities)
    asks_for_tools = bool(request.tools) or any(
        message.role == "tool" or message.tool_calls for message in request.messages
    )
    if asks_for_tools:
        required.add("tool_calls")
        if profile.adapter != "openai":
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    f"profile {profile.id!r} uses adapter {profile.adapter!r}, which has not "
                    "qualified tool calls; choose an OpenAI Chat Completions profile"
                ),
            )
    missing = sorted(required - declared_capabilities(profile))
    if missing:
        raise ProviderError(
            "unsupported_capability",
            safe_message=(
                "The selected model profile does not support the requested capability. "
                f"Request capabilities not declared: {', '.join(missing)}."
            ),
        )
    if request.max_output_tokens > profile.capabilities.max_output_tokens:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"the request asks for {request.max_output_tokens} output tokens, above the "
                f"{profile.capabilities.max_output_tokens} qualified for profile {profile.id!r}"
            ),
        )


def validate_limits(limits: RunLimits, profile: PinnedProviderConfig) -> None:
    """Refuse a token or cost ceiling the profile cannot enforce."""
    if limits.max_total_tokens is not None and not profile.capabilities.token_counting:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"profile {profile.id!r} has no qualified token counter, so a "
                f"{limits.max_total_tokens} token ceiling cannot be enforced"
            ),
        )
    if limits.max_cost_usd is not None and profile.pricing is None:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"profile {profile.id!r} has no qualified pricing schedule, so a "
                f"cost ceiling cannot be enforced"
            ),
        )
    if limits.max_output_tokens > profile.capabilities.max_output_tokens:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"the run's output limit of {limits.max_output_tokens} exceeds the "
                f"{profile.capabilities.max_output_tokens} qualified for profile {profile.id!r}"
            ),
        )
