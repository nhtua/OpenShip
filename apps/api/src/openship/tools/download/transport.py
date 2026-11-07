"""HTTPS downloads with public-address pinning and bounded response bodies."""

import asyncio
import ipaddress
import ssl
from urllib.parse import urljoin

import httpcore
import httpx

from src.openship.execution.network import PinnedNetworkBackend
from src.openship.tools.search.http import _HttpcoreResponseStream

_MAX_BYTES = 8 * 1024 * 1024


class DownloadError(Exception):
    """A download failed without exposing its URL or provider response."""

    def __str__(self):
        return "The requested file could not be downloaded safely."


def _url(value: str) -> httpx.URL:
    try:
        url = httpx.URL(value)
        if (url.scheme != "https" or not url.host or url.userinfo
                or url.port not in (None, 443) or url.fragment):
            raise ValueError
        try:
            address = ipaddress.ip_address(url.host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        return url
    except (ValueError, httpx.InvalidURL):
        raise DownloadError() from None


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, url: httpx.URL):
        self.host = url.host
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(), max_connections=1,
            max_keepalive_connections=0, retries=0,
            network_backend=PinnedNetworkBackend(self.host, policy_error=DownloadError),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or request.url.host != self.host or request.url.scheme != "https":
            raise DownloadError()
        response = await self.pool.handle_async_request(httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions,
        ))
        return httpx.Response(response.status, headers=response.headers,
                              stream=_HttpcoreResponseStream(response.stream, request),
                              extensions=response.extensions, request=request)

    async def aclose(self):
        await self.pool.aclose()


async def download_bytes(url: str, control, *, transport_factory=None) -> bytes:
    """Fetch one public HTTPS resource without following unchecked redirects."""
    body = bytearray()
    await download_stream(url, control, body.extend, transport_factory=transport_factory)
    return bytes(body)


async def download_stream(url: str, control, write, *, transport_factory=None) -> None:
    """Write each received chunk to the caller's sink before reading another."""
    task = asyncio.create_task(_download_stream(url, control, write, transport_factory=transport_factory))
    try:
        while not task.done():
            control.raise_if_stopped()
            await asyncio.wait((task,), timeout=0.05)
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _download_stream(url: str, control, write, *, transport_factory=None) -> None:
    try:
        async with asyncio.timeout(min(30, control.request_timeout_seconds(30))):
            current = _url(url)
            for hop in range(4):
                control.raise_if_stopped()
                transport = transport_factory(current) if transport_factory else _Transport(current)
                async with (
                    httpx.AsyncClient(transport=transport, trust_env=False,
                                      follow_redirects=False) as client,
                    client.stream("GET", current, headers={"Accept-Encoding": "identity"}) as response,
                ):
                    if response.status_code in (301, 302, 303, 307, 308):
                        if hop == 3 or not response.headers.get("location"):
                            raise DownloadError()
                        current = _url(urljoin(str(current), response.headers["location"]))
                        continue
                    if response.status_code != 200 or response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                        raise DownloadError()
                    size = 0
                    async for chunk in response.aiter_raw():
                        control.raise_if_stopped()
                        size += len(chunk)
                        if size > _MAX_BYTES:
                            raise DownloadError()
                        write(chunk)
                    return
            raise DownloadError()
    except (httpx.HTTPError, httpcore.NetworkError, TimeoutError, OSError):
        raise DownloadError() from None
