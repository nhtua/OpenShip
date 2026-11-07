"""Network boundary tests for the model-selected download capability."""

import asyncio

import httpx
import pytest

from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError


class _Stream(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        yield self.content


@pytest.mark.asyncio
async def test_rejects_private_redirect_without_contacting_it():
    from src.openship.tools.download.transport import DownloadError, download_bytes

    contacted = []

    def respond(request):
        contacted.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})

    with pytest.raises(DownloadError):
        await download_bytes(
            "https://example.com/data.csv?secret=hidden",
            InvocationControl(),
            transport_factory=lambda _: httpx.MockTransport(respond),
        )
    assert contacted == ["https://example.com/data.csv?secret=hidden"]


@pytest.mark.asyncio
async def test_rejects_oversized_response():
    from src.openship.tools.download.transport import DownloadError, download_bytes

    def respond(request):
        return httpx.Response(200, stream=_Stream(b"x" * (8 * 1024 * 1024 + 1)))

    with pytest.raises(DownloadError):
        await download_bytes(
            "https://example.com/data.csv", InvocationControl(),
            transport_factory=lambda _: httpx.MockTransport(respond),
        )


@pytest.mark.asyncio
async def test_rejects_compressed_response():
    from src.openship.tools.download.transport import DownloadError, download_bytes

    with pytest.raises(DownloadError):
        await download_bytes(
            "https://example.com/data.csv", InvocationControl(),
            transport_factory=lambda _: httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-encoding": "gzip"}, stream=_Stream(b"gzip")
                )
            ),
        )


@pytest.mark.asyncio
async def test_stops_after_three_redirects():
    from src.openship.tools.download.transport import DownloadError, download_bytes

    contacted = []

    def respond(request):
        contacted.append(request.url.path)
        return httpx.Response(302, headers={"location": f"/{len(contacted)}.csv"})

    with pytest.raises(DownloadError):
        await download_bytes(
            "https://example.com/data.csv", InvocationControl(),
            transport_factory=lambda _: httpx.MockTransport(respond),
        )
    assert contacted == ["/data.csv", "/1.csv", "/2.csv", "/3.csv"]


@pytest.mark.asyncio
async def test_transport_disables_proxy_environment(monkeypatch):
    from src.openship.tools.download import transport

    original = httpx.AsyncClient
    configurations = []

    def client(*args, **kwargs):
        configurations.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setattr(transport.httpx, "AsyncClient", client)
    result = await transport.download_bytes(
        "https://example.com/data.csv", InvocationControl(),
        transport_factory=lambda _: httpx.MockTransport(
            lambda request: httpx.Response(200, stream=_Stream(b"x\n"))
        ),
    )
    assert result == b"x\n"
    assert configurations[0]["trust_env"] is False


def test_pinned_transport_enables_tls_certificate_and_hostname_verification(monkeypatch):
    import ssl

    from src.openship.tools.download import transport

    configuration = {}

    def pool(**kwargs):
        configuration.update(kwargs)
        return object()

    monkeypatch.setattr(transport.httpcore, "AsyncConnectionPool", pool)
    transport._Transport(httpx.URL("https://example.com/data.csv"))
    context = configuration["ssl_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.asyncio
async def test_success_returns_exact_downloaded_bytes():
    from src.openship.tools.download.transport import download_bytes

    def respond(request):
        return httpx.Response(200, stream=_Stream(b"name,value\na,1\n"))

    result = await download_bytes(
        "https://example.com/data.csv", InvocationControl(),
        transport_factory=lambda _: httpx.MockTransport(respond),
    )
    assert result == b"name,value\na,1\n"


@pytest.mark.asyncio
async def test_stream_writes_chunks_before_reading_next_chunk():
    from src.openship.tools.download.transport import download_stream

    received = []

    class SequencedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            assert received == [b"first"]
            yield b"second"

    await download_stream(
        "https://example.com/data.csv", InvocationControl(), received.append,
        transport_factory=lambda _: httpx.MockTransport(
            lambda request: httpx.Response(200, stream=SequencedStream())
        ),
    )
    assert received == [b"first", b"second"]


@pytest.mark.asyncio
async def test_cancellation_interrupts_stalled_body():
    from src.openship.tools.download.transport import download_bytes

    started = asyncio.Event()
    stopped = asyncio.Event()

    class StalledStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

    async def run():
        return await download_bytes(
            "https://example.com/data.csv", InvocationControl(stop=stopped.is_set),
            transport_factory=lambda _: httpx.MockTransport(
                lambda request: httpx.Response(200, stream=StalledStream())
            ),
        )

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 1)
    stopped.set()
    with pytest.raises(ProviderError) as error:
        await asyncio.wait_for(task, 1)
    assert error.value.code == "cancelled"


@pytest.mark.asyncio
async def test_total_deadline_interrupts_stalled_body_without_leaking_url():
    import time

    from src.openship.tools.download.transport import DownloadError, download_bytes

    class StalledStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"unreachable"

    control = InvocationControl(deadline=time.monotonic() + 0.05)
    with pytest.raises(DownloadError) as error:
        await download_bytes(
            "https://example.com/data.csv?private=secret", control,
            transport_factory=lambda _: httpx.MockTransport(
                lambda request: httpx.Response(200, stream=StalledStream())
            ),
        )
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("dataset.csv", b"name,value\na,1\n", "csv"),
        ("dataset.json", b'{"value":1}', "json"),
    ],
)
def test_validates_supported_data_formats(name, content, expected):
    from src.openship.tools.download.contracts import validate_download

    assert validate_download(name, content) == expected


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("dataset.zip", b"PK\x03\x04file"),
        ("dataset.json", b"{invalid"),
        ("dataset.csv", b"\xff\x00"),
    ],
)
def test_rejects_unsupported_or_invalid_data(name, content):
    from src.openship.tools.download.contracts import validate_download

    with pytest.raises(ValueError):
        validate_download(name, content)


@pytest.mark.asyncio
async def test_dns_mixed_public_private_addresses_are_rejected_before_connect():
    from src.openship.execution.network import PinnedNetworkBackend
    from src.openship.tools.download.transport import DownloadError

    class Backend:
        async def connect_tcp(self, *args, **kwargs):
            pytest.fail("a private DNS response must not be connected")

    backend = PinnedNetworkBackend(
        "example.com", backend=Backend(),
        resolver=lambda host, port: ["8.8.8.8", "127.0.0.1"],
        policy_error=DownloadError,
    )
    with pytest.raises(DownloadError):
        await backend.connect_tcp("example.com", 443)


@pytest.mark.asyncio
async def test_dns_connection_uses_verified_address_not_original_hostname():
    from src.openship.execution.network import PinnedNetworkBackend

    connected = []

    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            connected.append((host, port))
            return object()

    backend = PinnedNetworkBackend(
        "example.com", backend=Backend(), resolver=lambda host, port: ["8.8.8.8"]
    )
    await backend.connect_tcp("example.com", 443)
    assert connected == [("8.8.8.8", 443)]


@pytest.mark.asyncio
async def test_handler_returns_data_without_query_or_url_in_observation(monkeypatch):
    from src.openship.tools.download import tool
    from src.openship.tools.download.contracts import DownloadArguments

    async def fetch(url, control):
        return b"name,value\na,1\n"

    monkeypatch.setattr(tool, "download_bytes", fetch)
    result = await tool.download_file(
        DownloadArguments(url="https://example.com/data.csv?token=private"),
        None, InvocationControl(),
    )
    assert result.content == b"name,value\na,1\n"
    assert result.name == "data.csv"
    assert result.format == "csv"
    assert "private" not in result.model_dump_json()
