"""Operator profile configuration: parse, validate, and resolve credentials.

Profile configuration is non-secret by contract. A credential is named by a
binding reference and its value is read only where a client is constructed,
so no catalog, event, checkpoint, or log line can carry it.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

from pydantic import ValidationError

from src.openship.providers.contracts import ProviderProfile
from src.openship.providers.errors import ProviderError, describe_validation

_PRIVATE_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"}
)


def validate_endpoint(profile: ProviderProfile) -> None:
    """A destination is an origin and a path, never a place to hide a secret."""
    parts = urlsplit(profile.endpoint)
    host = (parts.hostname or "").lower()

    if parts.scheme not in ("http", "https"):
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"profile {profile.id!r} endpoint must be http(s); "
                f"got scheme {parts.scheme or 'none'!r}"
            ),
        )
    if not host:
        raise ProviderError(
            "configuration_error",
            safe_message=f"profile {profile.id!r} endpoint has no host",
        )
    if parts.username or parts.password:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"profile {profile.id!r} endpoint must not carry userinfo; "
                "declare a credential_ref instead"
            ),
        )
    if parts.query:
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"profile {profile.id!r} endpoint must not carry a query string; "
                "credentials belong in a credential_ref"
            ),
        )
    if parts.fragment:
        raise ProviderError(
            "configuration_error",
            safe_message=f"profile {profile.id!r} endpoint must not carry a fragment",
        )
    if parts.scheme == "http" and not _is_private_host(host):
        raise ProviderError(
            "configuration_error",
            safe_message=(
                f"profile {profile.id!r} uses insecure http for a public host; "
                "use https, or declare a local/private endpoint"
            ),
        )


def _is_private_host(host: str) -> bool:
    """Local or private: a bare container name, a loopback, or a private IP."""
    if host in _PRIVATE_HOSTS or "." not in host:
        return True
    if host.startswith(("10.", "127.", "0.")):
        return True
    if host.startswith("192."):
        return True
    return host.startswith("172.")


def parse_profiles(raw: str) -> tuple[ProviderProfile, ...]:
    """Parse and validate the operator's declared profiles, in declared order."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as invalid:
        raise ProviderError(
            "configuration_error", safe_message="model profile configuration is not valid JSON"
        ) from invalid

    if not isinstance(decoded, list):
        raise ProviderError(
            "configuration_error",
            safe_message="model profile configuration must be a JSON array of profiles",
        )

    profiles: list[ProviderProfile] = []
    seen: set[str] = set()
    for index, item in enumerate(decoded):
        if not isinstance(item, dict):
            raise ProviderError(
                "configuration_error",
                safe_message=f"profile at position {index} is not an object",
            )
        try:
            profile = ProviderProfile(**item)
        except ValidationError as invalid:
            raise ProviderError(
                "configuration_error",
                safe_message=f"invalid model profile at position {index}: {describe_validation(invalid)}",
            ) from invalid
        if profile.id in seen:
            raise ProviderError(
                "configuration_error",
                safe_message=f"profile id {profile.id!r} is declared more than once",
            )
        seen.add(profile.id)
        validate_endpoint(profile)
        profiles.append(profile)

    return tuple(profiles)


def resolve_credential(ref: str) -> str | None:
    """Read one operator-declared binding. Called only where a client is built."""
    if not ref:
        return None
    return os.environ.get(ref) or None


# An installation that predates profiles keeps working through the legacy
# OPENAI_* settings. Only what the operator already declared is claimed here:
# the output limit comes from MODEL_MAX_TOKENS, and no token counter or
# context window is qualified, so ceilings stay unavailable until a profile
# declares them.
LEGACY_PROFILE_ID = "openai-default"
LEGACY_QUALIFIED_CONTEXT_TOKENS = 8192


def legacy_profile_spec(settings) -> ProviderProfile:
    """The synthetic profile built from the legacy OPENAI_* settings."""
    return ProviderProfile(
        id=LEGACY_PROFILE_ID,
        label="OpenAI default",
        adapter="openai",
        model=settings.openai_model,
        endpoint=settings.openai_base_url,
        api_version="chat-completions",
        credential_ref="OPENAI_API_KEY",
        capabilities={
            "streaming": True,
            "usage_reporting": True,
            "context_tokens": LEGACY_QUALIFIED_CONTEXT_TOKENS,
            "max_output_tokens": settings.model_max_tokens,
            "token_counting": False,
            "tool_calls": False,
            "structured_output": False,
            "attachment_types": [],
            "parallel_tool_calls": False,
        },
        adapter_options={
            "adapter": "openai",
            "output_limit_parameter": "max_tokens",
        },
    )
