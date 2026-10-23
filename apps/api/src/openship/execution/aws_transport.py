"""Bounded Botocore HTTP-session bridge using the shared pinned network path."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
import ssl
import threading
import zlib
from collections.abc import Callable
from urllib.parse import urlsplit

import httpcore
import httpx
from botocore.awsrequest import AWSResponse
from httpcore._backends.auto import AutoBackend

from src.openship.providers.errors import ProviderError

from .network import PinnedNetworkBackend

AWS_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
AWS_MAX_REQUEST_BYTES = 64 * 1024
AWS_MAX_REQUESTS = 8
AWS_MAX_TIMEOUT_SECONDS = 30.0
_CHUNK_BYTES = 64 * 1024
_AWS_HOST = re.compile(
    r"^(?:sts|monitoring|s3)\."
    r"(?:(?:af|ap|ca|eu|il|me|mx|sa)-[a-z0-9-]+-\d|"
    r"us-(?!gov-|iso-|isob-|isof-|isoeast-)[a-z0-9-]+-\d)\.amazonaws\.com$"
)

_SAFE_MESSAGES = {
    "aws_configuration_error": "The AWS connector configuration is unavailable.",
    "aws_destination_denied": "The AWS destination was rejected by network policy.",
    "aws_request_limit_exceeded": "The AWS request limit was exceeded.",
    "aws_timeout": "The AWS request exceeded its execution deadline.",
    "aws_cancelled": "The AWS request was cancelled.",
    "aws_access_denied": "AWS denied access to this operation.",
    "aws_trust_denied": "AWS denied the configured role assumption.",
    "aws_credentials_expired": "The AWS source credentials have expired.",
    "aws_credentials_invalid": "The AWS source credentials were rejected.",
    "aws_output_limit_exceeded": "The AWS response exceeded the allowed size.",
    "aws_redirect": "The AWS service returned an unsupported redirect.",
    "aws_invalid_response": "The AWS service returned an invalid response.",
    "aws_unavailable": "The AWS service is temporarily unavailable.",
    "aws_operation_not_supported": "The AWS operation is not supported by this runtime.",
}


class AwsSdkError(Exception):
    """A fixed, secret-safe AWS execution error."""

    def __init__(self, code: str):
        if code not in _SAFE_MESSAGES:
            raise ValueError("unknown AWS error code")
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)

    def __str__(self) -> str:
        return self.safe_message


class _BoundedRawBody:
    def __init__(self, body: bytes):
        self._body = body
        self._offset = 0

    def stream(self, amt: int = _CHUNK_BYTES, decode_content: bool = False):
        del decode_content
        while self._offset < len(self._body):
            chunk = self._body[self._offset : self._offset + amt]
            self._offset += len(chunk)
            yield chunk

    def read(self, amount: int | None = None) -> bytes:
        if amount is None or amount < 0:
            amount = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self._offset = len(self._body)


class _HttpcoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream, request: httpx.Request):
        self._stream = stream
        self._request = request

    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                yield chunk
        except httpcore.TimeoutException:
            raise httpx.ReadTimeout("AWS response timed out", request=self._request) from None
        except httpcore.NetworkError:
            raise httpx.ReadError("AWS response could not be read", request=self._request) from None

    async def aclose(self) -> None:
        await self._stream.aclose()


class AwsPinnedHttpTransport(httpx.AsyncBaseTransport):
    """HTTPS transport restricted to regional STS, CloudWatch and S3 endpoints."""

    def __init__(self, endpoint: str, *, resolver=None, backend=None):
        try:
            parsed = urlsplit(endpoint)
            host = parsed.hostname.lower() if parsed.hostname else ""
            port = parsed.port
        except ValueError:
            raise AwsSdkError("aws_destination_denied") from None
        if (
            parsed.scheme != "https"
            or port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or not _AWS_HOST.fullmatch(host)
        ):
            raise AwsSdkError("aws_destination_denied")
        self._host = host
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
            http1=True,
            http2=False,
            network_backend=PinnedNetworkBackend(
                host,
                backend=backend or AutoBackend(),
                resolver=resolver,
                policy_error=lambda: AwsSdkError("aws_destination_denied"),
            ),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host_header = request.headers.get("host", "").lower()
        if (
            request.method not in {"GET", "POST"}
            or request.url.scheme != "https"
            or request.url.host.lower() != self._host
            or request.url.port not in (None, 443)
            or request.url.username is not None
            or request.url.password is not None
            or request.url.fragment
            or host_header not in (self._host, f"{self._host}:443")
            or "proxy-authorization" in request.headers
        ):
            raise AwsSdkError("aws_destination_denied")
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
            raise httpx.ConnectTimeout("AWS connection timed out", request=request) from None
        except httpcore.NetworkError:
            raise httpx.ConnectError("AWS connection failed", request=request) from None
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_HttpcoreResponseStream(response.stream, request),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class BoundedAwsHttpSession:
    """Implement Botocore's synchronous HTTP-session seam with bounded async I/O."""

    def __init__(
        self,
        *,
        control,
        transport_factory: Callable[[str], httpx.AsyncBaseTransport] | None = None,
        request_limit: int = AWS_MAX_REQUESTS,
        max_response_bytes: int = AWS_MAX_RESPONSE_BYTES,
        timeout_seconds: float = AWS_MAX_TIMEOUT_SECONDS,
    ):
        if not 1 <= request_limit <= AWS_MAX_REQUESTS:
            raise ValueError("AWS request limit is outside the supported range")
        if not 1 <= max_response_bytes <= AWS_MAX_RESPONSE_BYTES:
            raise ValueError("AWS response limit is outside the supported range")
        if not 1 <= timeout_seconds <= AWS_MAX_TIMEOUT_SECONDS:
            raise ValueError("AWS timeout is outside the supported range")
        self._control = control
        self._transport_factory = transport_factory or AwsPinnedHttpTransport
        self._request_limit = request_limit
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._request_count = 0
        self._request_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.local()
        self._active: dict[str, set[concurrent.futures.Future]] = {}
        self._active_lock = threading.Lock()
        self._closed = False

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._loop is not None and self._loop is not loop:
            raise AwsSdkError("aws_configuration_error")
        self._loop = loop

    def bind_call(self, call_id: str) -> None:
        self._thread.call_id = call_id

    def clear_call(self) -> None:
        self._thread.call_id = None

    def cancel_call(self, call_id: str) -> None:
        with self._active_lock:
            futures = tuple(self._active.get(call_id, ()))
        for future in futures:
            future.cancel()

    def send(self, request):
        if self._closed:
            raise AwsSdkError("aws_cancelled")
        if getattr(request, "stream_output", False):
            raise AwsSdkError("aws_operation_not_supported")
        if request.method not in {"GET", "POST"}:
            raise AwsSdkError("aws_operation_not_supported")
        body = request.body
        if body is None:
            request_body = None
        elif isinstance(body, str):
            request_body = body.encode("utf-8")
        elif isinstance(body, bytes):
            request_body = body
        else:
            raise AwsSdkError("aws_operation_not_supported")
        if len(request_body or b"") > AWS_MAX_REQUEST_BYTES:
            raise AwsSdkError("aws_operation_not_supported")
        call_id = getattr(self._thread, "call_id", None)
        loop = self._loop
        if call_id is None or loop is None or loop.is_closed():
            raise AwsSdkError("aws_configuration_error")
        try:
            self._control.raise_if_stopped()
        except ProviderError:
            raise AwsSdkError("aws_cancelled") from None
        timeout = self._control.request_timeout_seconds(self._timeout_seconds)
        if timeout <= 0:
            raise AwsSdkError("aws_timeout")
        with self._request_lock:
            if self._request_count >= self._request_limit:
                raise AwsSdkError("aws_request_limit_exceeded")
            self._request_count += 1

        future = asyncio.run_coroutine_threadsafe(
            self._request(request, request_body, timeout), loop
        )
        with self._active_lock:
            self._active.setdefault(call_id, set()).add(future)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise AwsSdkError("aws_timeout") from None
        except concurrent.futures.CancelledError:
            raise AwsSdkError("aws_cancelled") from None
        except AwsSdkError:
            raise
        except Exception:  # noqa: BLE001 - redact unexpected transport errors
            raise AwsSdkError("aws_unavailable") from None
        finally:
            with self._active_lock:
                active = self._active.get(call_id)
                if active is not None:
                    active.discard(future)
                    if not active:
                        self._active.pop(call_id, None)

    async def _request(self, sdk_request, body: bytes | None, timeout: float) -> AWSResponse:
        try:
            self._control.raise_if_stopped()
        except ProviderError:
            raise AwsSdkError("aws_cancelled") from None
        if timeout <= 0:
            raise AwsSdkError("aws_timeout")
        headers = dict(sdk_request.headers.items())
        headers["Accept-Encoding"] = "identity"
        transport = self._transport_factory(sdk_request.url)
        try:
            async with httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=timeout,
            ) as client:
                async with asyncio.timeout(timeout):
                    async with client.stream(
                        sdk_request.method,
                        sdk_request.url,
                        headers=headers,
                        content=body,
                        follow_redirects=False,
                        timeout=timeout,
                    ) as response:
                        if 300 <= response.status_code < 400:
                            raise AwsSdkError("aws_redirect")
                        payload = await self._read_bounded(response)
                        response_headers = {
                            key: value
                            for key, value in response.headers.items()
                            if key.lower() not in {"content-length", "content-encoding"}
                        }
                        response_headers["content-length"] = str(len(payload))
                        return AWSResponse(
                            sdk_request.url,
                            response.status_code,
                            response_headers,
                            _BoundedRawBody(payload),
                        )
        except AwsSdkError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise AwsSdkError("aws_timeout") from None
        except (httpx.RequestError, httpcore.NetworkError):
            raise AwsSdkError("aws_unavailable") from None
        except zlib.error:
            raise AwsSdkError("aws_invalid_response") from None

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                raise AwsSdkError("aws_invalid_response") from None
            if length < 0:
                raise AwsSdkError("aws_invalid_response")
            if length > self._max_response_bytes:
                raise AwsSdkError("aws_output_limit_exceeded")
        content_encoding = response.headers.get("content-encoding", "").strip().lower()
        if content_encoding in {"", "identity"}:
            decoder = None
        elif content_encoding == "gzip":
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        else:
            raise AwsSdkError("aws_invalid_response")

        body = bytearray()
        wire_size = 0
        async for chunk in response.aiter_raw(chunk_size=_CHUNK_BYTES):
            try:
                self._control.raise_if_stopped()
            except ProviderError:
                raise AwsSdkError("aws_cancelled") from None
            wire_size += len(chunk)
            if wire_size > self._max_response_bytes:
                raise AwsSdkError("aws_output_limit_exceeded")
            if decoder is None:
                if len(body) + len(chunk) > self._max_response_bytes:
                    raise AwsSdkError("aws_output_limit_exceeded")
                body.extend(chunk)
                continue
            pending = chunk
            while pending:
                allowance = self._max_response_bytes - len(body) + 1
                decoded = decoder.decompress(pending, max(1, allowance))
                pending = decoder.unconsumed_tail
                if len(body) + len(decoded) > self._max_response_bytes:
                    raise AwsSdkError("aws_output_limit_exceeded")
                body.extend(decoded)
        if decoder is not None:
            allowance = self._max_response_bytes - len(body) + 1
            flushed = decoder.flush(max(1, allowance))
            if len(body) + len(flushed) > self._max_response_bytes:
                raise AwsSdkError("aws_output_limit_exceeded")
            body.extend(flushed)
            if not decoder.eof or decoder.unused_data:
                raise AwsSdkError("aws_invalid_response")
        return bytes(body)

    def close(self) -> None:
        self._closed = True
        with self._active_lock:
            call_ids = tuple(self._active)
        for call_id in call_ids:
            self.cancel_call(call_id)
