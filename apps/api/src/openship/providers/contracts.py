"""Provider-neutral contracts.

This is the only vocabulary the adapters, the runner, the worker, and the
browser share. Everything here is frozen, serializable, and secret-free:
credentials are named by reference and resolved only where a client is built.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.openship.providers.errors import ProviderError, describe_validation

PROFILE_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"

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


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


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

    type: Literal["text_delta", "usage_update", "stream_finished"]
    text: str | None = None
    usage: TokenUsage | None = None
    reason: Literal["stop", "length", "refusal", "tool_calls"] | None = None
    actual_model: str | None = None

    @model_validator(mode="after")
    def _check_tag(self):
        required = {
            "text_delta": ("text",),
            "usage_update": ("usage",),
            "stream_finished": ("reason",),
        }[self.type]
        present = {
            name
            for name in ("text", "usage", "reason", "actual_model")
            if getattr(self, name) is not None
        }
        missing = [name for name in required if name not in present]
        if missing:
            raise ValueError(f"{self.type} requires {', '.join(missing)}")
        allowed = {
            "text_delta": {"text"},
            "usage_update": {"usage"},
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
    required_capabilities: frozenset[str] = frozenset({"streaming"})


class ProviderTurnResult(BaseModel):
    """What one bounded model turn produced. Partial output is never an answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = ""
    usage: TokenUsage | None = None
    actual_model: str | None = None
    selected_profile_id: str = Field(min_length=1)
    attempt_count: int = Field(default=1, ge=1)
    finish_reason: str
    partial: bool = False

    @property
    def completed(self) -> bool:
        """Only a clean stop with nonempty output may publish an answer."""
        return self.finish_reason == "stop" and bool(self.text) and not self.partial


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
    missing = sorted(set(request.required_capabilities) - declared_capabilities(profile))
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
