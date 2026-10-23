"""Shared public-address pinning for fixed outbound HTTPS destinations."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Iterable

import httpcore
from httpcore._backends.auto import AutoBackend


class NetworkPolicyError(Exception):
    """A destination was outside the configured network policy."""

    def __str__(self) -> str:
        return "The remote destination is unavailable."


async def resolve_addresses(host: str, port: int) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return [record[4][0] for record in records]


def host_text(host: str | bytes) -> str:
    if isinstance(host, bytes):
        try:
            return host.decode("ascii").lower().rstrip(".")
        except UnicodeDecodeError:
            return ""
    return host.lower().rstrip(".")


class PinnedNetworkBackend:
    """Resolve one approved hostname, reject non-public results, and connect by IP."""

    def __init__(
        self,
        allowed_host: str,
        *,
        backend=None,
        resolver: Callable | None = None,
        policy_error: Callable[[], Exception] | None = None,
    ):
        self.allowed_host = allowed_host.lower()
        self._backend = backend or AutoBackend()
        self._resolver = resolver or resolve_addresses
        self._policy_error = policy_error or NetworkPolicyError

    def _deny(self):
        raise self._policy_error()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple] | None = None,
    ):
        if host_text(host) != self.allowed_host or port != 443:
            self._deny()
        try:
            resolved = self._resolver(self.allowed_host, port)
            if hasattr(resolved, "__await__"):
                resolved = await resolved
            addresses = list(dict.fromkeys(resolved))
            parsed = [ipaddress.ip_address(address) for address in addresses]
        except (OSError, TypeError, ValueError):
            self._deny()
        if not parsed or any(not address.is_global for address in parsed):
            self._deny()

        last_error = None
        for address in parsed:
            try:
                return await self._backend.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.TimeoutException, OSError) as exc:
                last_error = exc
        if isinstance(last_error, httpcore.TimeoutException):
            raise httpcore.ConnectTimeout("connection timed out") from None
        raise httpcore.ConnectError("connection failed") from None

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        self._deny()

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)
