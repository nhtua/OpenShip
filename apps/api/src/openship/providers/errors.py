"""Normalized provider errors.

Every failure an adapter or the runner can produce is one of a closed set of
codes with fixed, actionable copy. The original provider exception and its
HTTP body never reach a user, an event, or a persisted record.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from src.openship.providers.credentials import (
    credential_binding_present as _credential_binding_present,
)

SAFE_MESSAGES: Mapping[str, str] = {
    "configuration_error": (
        "The selected model profile is not available in this installation. "
        "Choose another profile, or ask the installation operator to update its configuration."
    ),
    "authentication_error": (
        "The model provider rejected the configured credentials. "
        "Rotate or re-check the credential binding for this profile."
    ),
    "invalid_request": (
        "The model provider rejected the conversation request. "
        "Rephrase the request and try again."
    ),
    "unsupported_capability": (
        "The selected model profile does not support the requested capability. "
        "Choose a profile that declares it, or ask for text only."
    ),
    "connector_unavailable": (
        "A Connector pinned to this run is unavailable. Restore that exact Connector and resume, or submit a new run with a replacement."
    ),
    "connector_expired": (
        "A Connector credential pinned to this run has expired. Replace it and submit a new run."
    ),
    "credential_store_unavailable": (
        "The encrypted Connector credential is temporarily unavailable. Restore the credential store, then resume this run."
    ),
    "connector_wait_expired": (
        "The Connector wait expired before the run was resumed. Submit a new run after restoring its Connector."
    ),
    "connector_dismissed": "The Connector wait was dismissed.",
    "context_limit_exceeded": (
        "The conversation is larger than the selected model's context limit. "
        "Shorten the conversation or choose a profile with a larger context."
    ),
    "rate_limited": (
        "The model provider is rate limiting this request. "
        "OpenShip will retry within its budget, or you can retry later."
    ),
    "provider_unavailable": (
        "The model provider could not be reached. "
        "OpenShip will retry within its budget, or you can choose another profile."
    ),
    "invalid_response": (
        "The model returned an incomplete or malformed response. Try again."
    ),
    "model_refused": (
        "The model declined to answer this request. Rephrase the request or choose another profile."
    ),
    "output_limit_exceeded": (
        "The model reached its output limit. "
        "Ask for a shorter answer, or raise the profile's output limit."
    ),
    "unexpected_model": (
        "The provider served a model this profile does not qualify. "
        "Update the profile's qualified model aliases."
    ),
    "budget_exceeded": (
        "The model turn reached its time or token budget. "
        "Narrow the request, or raise the installation's model budget."
    ),
    "tool_limit_exceeded": (
        "The model requested another tool after this run's tool allowance ended. "
        "Start another turn to continue the search."
    ),
    "model_interrupted": ("The model stopped before completing the answer. Try again."),
    "execution_interrupted": (
        "The tool execution was interrupted and will not be repeated automatically."
    ),
    "cancelled": "The run was cancelled.",
    "lease_lost": (
        "This worker no longer owns the run, so its result was not published. "
        "Another worker will finish it."
    ),
}

RETRYABLE_ERROR_CODES = frozenset({"rate_limited", "provider_unavailable"})


class ProviderError(Exception):
    """A safe, normalized provider failure. Carries a code and fixed copy only."""

    def __init__(
        self,
        code: str,
        *,
        safe_message: str | None = None,
        retry_after_seconds: float | None = None,
        cause: BaseException | None = None,
    ) -> None:
        if code not in SAFE_MESSAGES:
            raise ValueError(f"unknown provider error code: {code!r}")
        if retry_after_seconds is not None and not retry_after_seconds > 0:
            raise ValueError("retry_after_seconds must be positive when present")

        self.code = code
        self.safe_message = safe_message or SAFE_MESSAGES[code]
        self.retryable = code in RETRYABLE_ERROR_CODES
        self.retry_after_seconds = retry_after_seconds
        self.cause = cause

    @property
    def public(self) -> dict:
        """The only shape safe to persist or send to a browser."""
        payload = {"code": self.code, "message": self.safe_message}
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        return payload

    def __str__(self) -> str:
        return self.safe_message


def redact(cause: BaseException) -> str:
    """Internal logging only: the exception's type, never its arguments."""
    return f"{type(cause).__name__}"


def describe_validation(invalid: ValidationError) -> str:
    """Field names and reasons only — never the offending value."""
    parts = []
    for error in invalid.errors():
        location = ".".join(str(part) for part in error["loc"]) or "profile"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def credential_binding_present(name: str) -> bool:
    """Whether an operator-declared credential binding is configured.

    Presence only — the value is never read here, so it cannot leak into a
    catalog, an event, or a log line.
    """
    return _credential_binding_present(name)
