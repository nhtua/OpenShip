"""Operator profile configuration: parse, validate, and resolve credentials.

Profile configuration is non-secret by contract. A credential is named by a
binding reference and its value is read only where a client is constructed,
so no catalog, event, checkpoint, or log line can carry it.
"""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

from pydantic import ValidationError

from src.openship.providers.contracts import ProviderProfile
from src.openship.providers.credentials import credential_value
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
    if host in _PRIVATE_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Single-label names are commonly used by local/container services.
        # A dotted DNS name is public unless it is an explicit local alias.
        return "." not in host and ":" not in host
    return address.is_private or address.is_loopback or address.is_link_local


def destination_label(endpoint: str) -> str:
    """A safe name for a destination: host and path, never a query or a secret.

    A browser may show this; it may never show the endpoint's own shape, which
    is where an operator could have hidden something.
    """
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    path = (parts.path or "").rstrip("/")
    return f"{host}{path}"


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
    return credential_value(ref)
