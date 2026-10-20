"""Bounded HTTPS transport shared by the maintained search functions."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
import zlib
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from src.openship.providers.errors import ProviderError

BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
_ENDPOINT_PATHS = {
    "api.search.brave.com": "/res/v1/web/search",
    "api.tavily.com": "/search",
}
_ENDPOINT_METHODS = {
    "api.search.brave.com": "GET",
    "api.tavily.com": "POST",
}
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024

_SAFE_MESSAGES = {
    "missing_credentials": "The selected search tool is missing its operator credential.",
    "authentication_error": "The search service rejected the configured credential.",
    "rate_limited": "The search service is rate limiting this request.",
    "quota_exhausted": "The search service account has exhausted its configured quota.",
    "provider_timeout": "The search service did not respond within the allowed time.",
    "provider_unavailable": "The search service is temporarily unavailable.",
    "invalid_response": "The search service returned an invalid response.",
    "output_limit_exceeded": "The search response exceeded the allowed size.",
    "cancelled": "The search was cancelled before it completed.",
}


class SearchToolError(Exception):
    """A search failure with a closed code and fixed, secret-safe public text."""

    def __init__(self, code: str):
        if code not in _SAFE_MESSAGES:
            raise ValueError("unknown search error code")
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)

    def __str__(self) -> str:
        return self.safe_message


def _endpoint_parts(endpoint: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname.lower() if parsed.hostname else ""
        port = parsed.port
    except ValueError:
        raise SearchToolError("provider_unavailable") from None
    if (
        parsed.scheme != "https"
        or host not in _ENDPOINT_PATHS
        or port not in (None, 443)
        or parsed.path != _ENDPOINT_PATHS[host]
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SearchToolError("provider_unavailable")
    return host, parsed.path


async def _resolve_addresses(host: str, port: int) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    )
    return [record[4][0] for record in records]


def _host_text(host: str | bytes) -> str:
    if isinstance(host, bytes):
        try:
            return host.decode("ascii").lower().rstrip(".")
        except UnicodeDecodeError:
            return ""
    return host.lower().rstrip(".")


class PinnedNetworkBackend:
    """Resolve one approved host, reject non-public answers, and connect by IP.

    The HTTP core still receives the original URL hostname and therefore uses
    it for TLS SNI/certificate validation and the HTTP Host header. Only the
    socket's connect address is pinned to the validated resolver answer.
    """

    def __init__(self, allowed_host: str, *, backend=None, resolver=None):
        self.allowed_host = allowed_host.lower()
        self._backend = backend or AutoBackend()
        self._resolver = resolver or _resolve_addresses

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple] | None = None,
    ):
        if _host_text(host) != self.allowed_host or port != 443:
            raise SearchToolError("provider_unavailable")
        try:
            resolved = self._resolver(self.allowed_host, port)
            if hasattr(resolved, "__await__"):
                resolved = await resolved
            addresses = list(dict.fromkeys(resolved))
            parsed = [ipaddress.ip_address(address) for address in addresses]
        except (OSError, TypeError, ValueError):
            raise SearchToolError("provider_unavailable") from None
        if not parsed or any(not address.is_global for address in parsed):
            raise SearchToolError("provider_unavailable")

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
            raise httpcore.ConnectTimeout("search connection timed out") from None
        raise httpcore.ConnectError("search connection failed") from None

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise SearchToolError("provider_unavailable")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _HttpcoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream, request: httpx.Request):
        self._stream = stream
        self._request = request

    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                yield chunk
        except httpcore.TimeoutException:
            raise httpx.ReadTimeout("search response timed out", request=self._request) from None
        except httpcore.NetworkError:
            raise httpx.ReadError("search response could not be read", request=self._request) from None

    async def aclose(self) -> None:
        await self._stream.aclose()


class PinnedHttpTransport(httpx.AsyncBaseTransport):
    """HTTPX transport backed by HTTP core with a validated pinned connector."""

    def __init__(self, endpoint: str, *, resolver=None):
        host, _path = _endpoint_parts(endpoint)
        self._host = host
        self._endpoint_path = _path
        self._method = _ENDPOINT_METHODS[host]
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
            http1=True,
            http2=False,
            network_backend=PinnedNetworkBackend(host, resolver=resolver),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host_header = request.headers.get("host", "").lower()
        if (
            request.method != self._method
            or request.url.scheme != "https"
            or request.url.host.lower() != self._host
            or request.url.port not in (None, 443)
            or request.url.path != self._endpoint_path
            or (self._method == "POST" and request.url.query)
            or host_header not in (self._host, f"{self._host}:443")
            or "proxy-authorization" in request.headers
        ):
            raise httpx.UnsupportedProtocol("search transport only supports its fixed endpoint")
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await self._pool.handle_async_request(core_request)
        except httpcore.TimeoutException:
            raise httpx.ConnectTimeout("search connection timed out", request=request) from None
        except httpcore.NetworkError:
            raise httpx.ConnectError("search connection failed", request=request) from None
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_HttpcoreResponseStream(response.stream, request),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class BoundedSearchHttp:
    """Issue one bounded JSON request to a fixed maintained search endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        timeout_seconds: int = 30,
        transport_factory: Callable[[str], httpx.AsyncBaseTransport] | None = None,
        client_factory=None,
    ):
        _endpoint_parts(endpoint)
        if not 1 <= max_response_bytes <= _MAX_RESPONSE_BYTES:
            raise ValueError("search response limit is outside the supported range")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("search timeout is outside the supported range")
        self.endpoint = endpoint
        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = timeout_seconds
        self._transport_factory = transport_factory or PinnedHttpTransport
        self._client_factory = client_factory or httpx.AsyncClient

    async def get_json(self, *, params: dict, headers: dict, control) -> dict:
        return await self._request_json(
            "GET", params=params, json_body=None, headers=headers, control=control
        )

    async def post_json(self, *, json_body: dict, headers: dict, control) -> dict:
        return await self._request_json(
            "POST", params=None, json_body=json_body, headers=headers, control=control
        )

    async def _request_json(
        self, method: str, *, params: dict | None, json_body: dict | None, headers: dict, control
    ) -> dict:
        host, _path = _endpoint_parts(self.endpoint)
        if method != _ENDPOINT_METHODS[host]:
            raise SearchToolError("provider_unavailable")
        timeout = control.request_timeout_seconds(self.timeout_seconds)
        if timeout <= 0:
            raise SearchToolError("provider_timeout")
        request_headers = dict(headers)
        request_headers["Accept"] = "application/json"
        request_headers["Accept-Encoding"] = "identity"
        try:
            control.raise_if_stopped()
        except ProviderError:
            raise SearchToolError("cancelled") from None

        transport = self._transport_factory(self.endpoint)
        try:
            async with self._client_factory(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=timeout,
            ) as client:
                async with asyncio.timeout(timeout):
                    request_options = {}
                    if params is not None:
                        request_options["params"] = params
                    if json_body is not None:
                        request_options["json"] = json_body
                    async with client.stream(
                        method,
                        self.endpoint,
                        headers=request_headers,
                        follow_redirects=False,
                        timeout=timeout,
                        **request_options,
                    ) as response:
                        if response.status_code in (401, 403):
                            raise SearchToolError("authentication_error")
                        if response.status_code == 429:
                            raise SearchToolError("rate_limited")
                        if response.status_code in (402, 432, 433):
                            raise SearchToolError("quota_exhausted")
                        if response.status_code in (408, 504):
                            raise SearchToolError("provider_timeout")
                        if 300 <= response.status_code < 400:
                            raise SearchToolError("invalid_response")
                        if response.status_code >= 500:
                            raise SearchToolError("provider_unavailable")
                        if response.status_code >= 400:
                            raise SearchToolError("invalid_response")
                        content_type = response.headers.get("content-type", "")
                        if content_type.split(";", 1)[0].strip().lower() != "application/json":
                            raise SearchToolError("invalid_response")
                        content_length = response.headers.get("content-length")
                        if content_length is not None:
                            try:
                                declared_length = int(content_length)
                                if declared_length < 0:
                                    raise SearchToolError("invalid_response")
                                if declared_length > self.max_response_bytes:
                                    raise SearchToolError("output_limit_exceeded")
                            except ValueError:
                                raise SearchToolError("invalid_response") from None
                        body = bytearray()
                        content_encoding = response.headers.get("content-encoding", "")
                        content_encoding = content_encoding.strip().lower()
                        if content_encoding in ("", "identity"):
                            decoder = None
                        elif content_encoding == "gzip":
                            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                        else:
                            raise SearchToolError("invalid_response")
                        wire_size = 0
                        async for chunk in response.aiter_raw(chunk_size=_CHUNK_BYTES):
                            try:
                                control.raise_if_stopped()
                            except ProviderError:
                                raise SearchToolError("cancelled") from None
                            wire_size += len(chunk)
                            if wire_size > self.max_response_bytes:
                                raise SearchToolError("output_limit_exceeded")
                            if decoder is None:
                                if len(body) + len(chunk) > self.max_response_bytes:
                                    raise SearchToolError("output_limit_exceeded")
                                body.extend(chunk)
                                continue
                            pending = chunk
                            while pending:
                                allowance = self.max_response_bytes - len(body) + 1
                                decoded = decoder.decompress(pending, max(1, allowance))
                                pending = decoder.unconsumed_tail
                                if len(body) + len(decoded) > self.max_response_bytes:
                                    raise SearchToolError("output_limit_exceeded")
                                body.extend(decoded)
                        if decoder is not None:
                            allowance = self.max_response_bytes - len(body) + 1
                            flushed = decoder.flush(max(1, allowance))
                            if len(body) + len(flushed) > self.max_response_bytes:
                                raise SearchToolError("output_limit_exceeded")
                            body.extend(flushed)
                            if not decoder.eof or decoder.unused_data:
                                raise SearchToolError("invalid_response")
        except SearchToolError:
            raise
        except httpx.DecodingError:
            raise SearchToolError("invalid_response") from None
        except zlib.error:
            raise SearchToolError("invalid_response") from None
        except (httpx.TimeoutException, TimeoutError):
            raise SearchToolError("provider_timeout") from None
        except httpx.RequestError:
            raise SearchToolError("provider_unavailable") from None

        try:
            payload = json.loads(
                body.decode("utf-8", "strict"),
                object_pairs_hook=_unique_json_pairs,
                parse_constant=_reject_nonfinite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise SearchToolError("invalid_response") from None
        if not isinstance(payload, dict):
            raise SearchToolError("invalid_response")
        return payload


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(_value):
    raise ValueError("non-finite JSON number")
