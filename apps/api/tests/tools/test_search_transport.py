import asyncio
import gzip

import httpx
import pytest
from httpx import AsyncByteStream

from src.openship.providers.errors import ProviderError
from src.openship.tools.search.http import (
    BRAVE_SEARCH_ENDPOINT,
    BoundedSearchHttp,
    PinnedHttpTransport,
    PinnedNetworkBackend,
    SearchToolError,
)


class _Control:
    def __init__(self, timeout=1.0, stop=None):
        self.timeout = timeout
        self.stop = stop or (lambda: False)

    def request_timeout_seconds(self, limit):
        return min(limit, self.timeout)

    def raise_if_stopped(self):
        if self.stop():
            raise ProviderError("cancelled")


class _BodyStream(AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        yield self.body

    async def aclose(self):
        return None


def _http(transport, *, max_bytes=1024, client_factory=None):
    return BoundedSearchHttp(
        BRAVE_SEARCH_ENDPOINT,
        max_response_bytes=max_bytes,
        transport_factory=lambda _endpoint: transport,
        client_factory=client_factory,
    )


@pytest.mark.asyncio
async def test_fixed_origin_sends_one_request_without_environment_proxy(monkeypatch):
    requests = []
    client_options = {}
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:54321")

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_BodyStream(b'{"ok":true}'),
        )

    def client_factory(**kwargs):
        client_options.update(kwargs)
        return httpx.AsyncClient(**kwargs)

    bounded = _http(httpx.MockTransport(respond), client_factory=client_factory)
    result = await bounded.get_json(
        params={"q": "hello world", "count": 3},
        headers={"X-Subscription-Token": "brave-test-key"},
        control=_Control(),
    )

    assert result == {"ok": True}
    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://api.search.brave.com/res/v1/web/search?q=hello+world&count=3"
    )
    assert requests[0].headers["x-subscription-token"] == "brave-test-key"
    assert client_options["trust_env"] is False
    assert client_options["follow_redirects"] is False


@pytest.mark.asyncio
async def test_redirect_is_not_followed():
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/"})

    with pytest.raises(SearchToolError) as caught:
        await _http(httpx.MockTransport(respond)).get_json(
            params={"q": "query"}, headers={}, control=_Control()
        )

    assert caught.value.code == "invalid_response"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_decompressed_response_limit_is_enforced():
    async def respond(_request):
        body = gzip.compress(b'{"body":"a"}' * 20)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
            stream=_BodyStream(body),
        )

    with pytest.raises(SearchToolError) as caught:
        await _http(httpx.MockTransport(respond), max_bytes=128).get_json(
            params={"q": "query"}, headers={}, control=_Control()
        )

    assert caught.value.code == "output_limit_exceeded"


@pytest.mark.asyncio
async def test_authentication_error_is_fixed_and_never_retried():
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(401, content=b"rejected sentinel-provider-key")

    with pytest.raises(SearchToolError) as caught:
        await _http(httpx.MockTransport(respond)).get_json(
            params={"q": "query"},
            headers={"X-Subscription-Token": "sentinel-provider-key"},
            control=_Control(),
        )

    assert caught.value.code == "authentication_error"
    assert "sentinel-provider-key" not in str(caught.value)
    assert "rejected sentinel-provider-key" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_malformed_compressed_response_is_safe():
    async def respond(_request):
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
            stream=_BodyStream(b"not gzip"),
        )

    with pytest.raises(SearchToolError) as caught:
        await _http(httpx.MockTransport(respond)).get_json(
            params={"q": "query"}, headers={}, control=_Control()
        )

    assert caught.value.code == "invalid_response"
    assert "not gzip" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b'{"x":1,"x":2}', b'{"x":NaN}'])
async def test_duplicate_or_nonfinite_json_is_rejected(body):
    async def respond(_request):
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_BodyStream(body),
        )

    with pytest.raises(SearchToolError) as caught:
        await _http(httpx.MockTransport(respond)).get_json(
            params={"q": "query"}, headers={}, control=_Control()
        )

    assert caught.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_rate_limit_and_timeout_map_to_fixed_errors():
    async def limited(_request):
        return httpx.Response(429, content=b"provider details")

    with pytest.raises(SearchToolError) as rate_limited:
        await _http(httpx.MockTransport(limited)).get_json(
            params={"q": "query"}, headers={}, control=_Control()
        )
    assert rate_limited.value.code == "rate_limited"
    assert "provider details" not in str(rate_limited.value)

    async def slow(_request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    with pytest.raises(SearchToolError) as timed_out:
        await _http(httpx.MockTransport(slow)).get_json(
            params={"q": "query"}, headers={}, control=_Control(timeout=0.01)
        )
    assert timed_out.value.code == "provider_timeout"


class _Chunks(AsyncByteStream):
    def __init__(self, stop):
        self.stop = stop
        self.closed = False

    async def __aiter__(self):
        yield b"{"
        self.stop[0] = True
        yield b"\"ok\":true}"

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_cancellation_during_response_closes_stream():
    stop = [False]
    stream = _Chunks(stop)

    async def respond(_request):
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)

    with pytest.raises(SearchToolError) as caught:
        await _http(httpx.MockTransport(respond)).get_json(
            params={"q": "query"}, headers={}, control=_Control(stop=lambda: stop[0])
        )

    assert caught.value.code == "cancelled"
    assert stream.closed


class _RecordingBackend:
    def __init__(self):
        self.hosts = []

    async def connect_tcp(self, host, port, **kwargs):
        self.hosts.append((host, port))
        return object()

    async def connect_unix_socket(self, *args, **kwargs):
        raise AssertionError("fixed HTTPS endpoints never use Unix sockets")

    async def sleep(self, _seconds):
        return None


@pytest.mark.asyncio
async def test_dns_resolution_is_pinned_to_a_public_address():
    backend = _RecordingBackend()

    async def resolve(_host, _port):
        return ["93.184.216.34"]

    pinned = PinnedNetworkBackend(
        "api.search.brave.com", backend=backend, resolver=resolve
    )
    stream = await pinned.connect_tcp("api.search.brave.com", 443)

    assert stream is not None
    assert backend.hosts == [("93.184.216.34", 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["93.184.216.34", "10.0.0.1"], ["169.254.1.1"]],
)
async def test_dns_resolution_rejects_private_mixed_or_link_local_peers(addresses):
    backend = _RecordingBackend()

    async def resolve(_host, _port):
        return addresses

    pinned = PinnedNetworkBackend(
        "api.search.brave.com", backend=backend, resolver=resolve
    )
    with pytest.raises(SearchToolError) as caught:
        await pinned.connect_tcp("api.search.brave.com", 443)

    assert caught.value.code == "provider_unavailable"
    assert backend.hosts == []


@pytest.mark.asyncio
async def test_dns_backend_rejects_an_unapproved_origin_before_resolving():
    backend = _RecordingBackend()
    resolved = []

    async def resolve(host, port):
        resolved.append((host, port))
        return ["93.184.216.34"]

    pinned = PinnedNetworkBackend(
        "api.search.brave.com", backend=backend, resolver=resolve
    )
    with pytest.raises(SearchToolError):
        await pinned.connect_tcp("127.0.0.1", 443)

    assert resolved == []
    assert backend.hosts == []


@pytest.mark.asyncio
async def test_pinned_transport_rejects_host_override_and_unapproved_url():
    transport = PinnedHttpTransport(BRAVE_SEARCH_ENDPOINT)
    request = httpx.Request(
        "GET",
        BRAVE_SEARCH_ENDPOINT,
        headers={"Host": "evil.example"},
    )
    try:
        with pytest.raises(httpx.UnsupportedProtocol):
            await transport.handle_async_request(request)
    finally:
        await transport.aclose()

    with pytest.raises(SearchToolError):
        BoundedSearchHttp("https://api.search.brave.com/unrestricted")
