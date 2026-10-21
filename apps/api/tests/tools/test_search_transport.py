import asyncio
import gzip

import httpcore
import httpx
import pytest
from httpx import AsyncByteStream

from src.openship.providers.errors import ProviderError
from src.openship.tools.search.http import (
    BRAVE_SEARCH_ENDPOINT,
    TAVILY_SEARCH_ENDPOINT,
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
async def test_fixed_tavily_post_uses_bearer_json_body_without_query_url():
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_BodyStream(b'{"results":[]}'),
        )

    bounded = BoundedSearchHttp(
        TAVILY_SEARCH_ENDPOINT,
        transport_factory=lambda _endpoint: httpx.MockTransport(respond),
    )
    result = await bounded.post_json(
        json_body={"query": "private query", "search_depth": "basic"},
        headers={
            "Authorization": "Bearer tavily-sentinel-key",
            "Content-Type": "application/json",
        },
        control=_Control(),
    )

    assert result == {"results": []}
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == TAVILY_SEARCH_ENDPOINT
    assert requests[0].headers["authorization"] == "Bearer tavily-sentinel-key"
    assert requests[0].headers["content-type"] == "application/json"
    assert requests[0].read() == b'{"query":"private query","search_depth":"basic"}'


@pytest.mark.asyncio
async def test_pinned_transport_allows_only_the_method_bound_to_each_origin():
    transport = PinnedHttpTransport(TAVILY_SEARCH_ENDPOINT)
    forwarded = []

    async def capture(request):
        forwarded.append(request)
        raise httpcore.ConnectError("transport capture")

    transport._pool.handle_async_request = capture
    try:
        get_request = httpx.Request("GET", TAVILY_SEARCH_ENDPOINT)
        with pytest.raises(httpx.UnsupportedProtocol):
            await transport.handle_async_request(get_request)

        post_request = httpx.Request(
            "POST",
            TAVILY_SEARCH_ENDPOINT,
            headers={"Authorization": "Bearer sentinel", "Content-Type": "application/json"},
            json={"query": "query"},
        )
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(post_request)
    finally:
        await transport.aclose()

    assert len(forwarded) == 1
    assert forwarded[0].method == b"POST"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [402, 432, 433])
async def test_account_quota_statuses_map_to_a_fixed_error(status_code):
    async def quota(_request):
        return httpx.Response(status_code, content=b"account detail and sentinel")

    with pytest.raises(SearchToolError) as caught:
        await _http(httpx.MockTransport(quota)).get_json(
            params={"q": "query"}, headers={}, control=_Control()
        )

    assert caught.value.code == "quota_exhausted"
    assert "sentinel" not in str(caught.value)


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


@pytest.mark.asyncio
async def test_cancellation_while_waiting_for_response_headers_closes_request():
    stop = [False]
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def respond(_request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(
        _http(httpx.MockTransport(respond)).get_json(
            params={"q": "query"},
            headers={},
            control=_Control(stop=lambda: stop[0]),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    stop[0] = True

    with pytest.raises(SearchToolError) as caught:
        await asyncio.wait_for(task, timeout=0.5)

    assert caught.value.code == "cancelled"
    assert cancelled.is_set()


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
