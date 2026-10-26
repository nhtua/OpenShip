"""Shared NATS connection setup with file-backed credentials."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, field
from pathlib import Path

import nats
from nats.aio.client import Client

from ..config import settings


@dataclass(frozen=True, repr=False)
class NatsCredentials:
    user: str
    password: str = field(repr=False)

    def __repr__(self) -> str:
        return f"NatsCredentials(user={self.user!r}, password=<redacted>)"


def load_credentials(path: Path | None = None) -> NatsCredentials:
    """Read one role's credentials from a private JSON file."""
    auth_path = path or settings.nats_auth_file
    if auth_path is None:
        raise ValueError("NATS_AUTH_FILE must name a protected role credential file")
    try:
        metadata = auth_path.stat()
        if metadata.st_mode & 0o077:
            raise ValueError(
                "NATS_AUTH_FILE must not be accessible by group or other users"
            )
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "NATS_AUTH_FILE could not be read as credential JSON"
        ) from error
    if not isinstance(payload, dict):
        raise TypeError("NATS_AUTH_FILE must contain a JSON object")
    user = payload.get("user")
    password = payload.get("password")
    if (
        not isinstance(user, str)
        or not user
        or not isinstance(password, str)
        or not password
    ):
        raise ValueError(
            "NATS_AUTH_FILE must contain non-empty user and password values"
        )
    return NatsCredentials(user=user, password=password)  # rayloc:ignore


def parse_servers(value: str | None = None) -> list[str]:
    """Return a validated NATS server list from a comma-separated setting."""
    configured = settings.nats_servers if value is None else value
    servers = [server.strip() for server in configured.split(",") if server.strip()]
    if not servers or any(
        not server.startswith(("nats://", "tls://")) for server in servers
    ):
        raise ValueError("NATS_SERVERS must contain one or more nats:// or tls:// URLs")
    return servers


async def connect_nats(
    *,
    name: str,
    reconnected_cb=None,
    allow_reconnect: bool = True,
) -> Client:
    """Connect using the current process role's credentials without logging them."""
    credentials = load_credentials()
    tls_context = None
    if settings.nats_tls_ca_file is not None:
        try:
            tls_context = ssl.create_default_context(
                cafile=str(settings.nats_tls_ca_file)
            )
        except (OSError, ssl.SSLError) as error:
            raise ValueError(
                "NATS_TLS_CA_FILE could not initialize certificate validation"
            ) from error
    return await nats.connect(
        servers=parse_servers(),
        user=credentials.user,
        password=credentials.password,
        tls=tls_context,
        name=name,
        reconnected_cb=reconnected_cb,
        allow_reconnect=allow_reconnect,
        max_reconnect_attempts=-1 if allow_reconnect else 0,
        connect_timeout=3,
    )
