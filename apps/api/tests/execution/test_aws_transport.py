import httpcore
import httpx
import pytest

from src.openship.execution.aws_transport import AwsPinnedHttpTransport, AwsSdkError
from src.openship.execution.network import NetworkPolicyError, PinnedNetworkBackend


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://sts.us-east-1.amazonaws.com",
        "https://169.254.169.254/latest/meta-data",
        "https://sts.us-east-1.amazonaws.com.attacker.example",
        "https://sts.us-gov-west-1.amazonaws.com",
        "https://s3.cn-north-1.amazonaws.com.cn",
        "https://sts.us-east-1.amazonaws.com:444",
        "https://user@sts.us-east-1.amazonaws.com",
    ],
)
def test_aws_transport_rejects_noncommercial_or_unpinned_endpoints(endpoint):
    with pytest.raises(AwsSdkError) as caught:
        AwsPinnedHttpTransport(endpoint)

    assert caught.value.code == "aws_destination_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_request",
    [
        httpx.Request("PUT", "https://sts.us-east-1.amazonaws.com/"),
        httpx.Request("GET", "http://sts.us-east-1.amazonaws.com/"),
        httpx.Request("GET", "https://sts.us-east-1.amazonaws.com:444/"),
        httpx.Request("GET", "https://s3.us-east-1.amazonaws.com/"),
        httpx.Request("GET", "https://user@sts.us-east-1.amazonaws.com/"),
        httpx.Request("GET", "https://:password@sts.us-east-1.amazonaws.com/"),
        httpx.Request(
            "GET", "https://sts.us-east-1.amazonaws.com/",
            headers={"Host": "attacker.example"},
        ),
        httpx.Request(
            "GET",
            "https://sts.us-east-1.amazonaws.com/",
            headers={"proxy-authorization": "Basic dXNlcjpwYXNz"},
        ),
    ],
)
async def test_aws_transport_rejects_unsupported_request_targets(invalid_request):
    transport = AwsPinnedHttpTransport("https://sts.us-east-1.amazonaws.com")
    try:
        with pytest.raises(AwsSdkError) as caught:
            await transport.handle_async_request(invalid_request)
    finally:
        await transport.aclose()

    assert caught.value.code == "aws_destination_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "method", "target"),
    [
        ("https://sts.us-east-1.amazonaws.com", "POST", "/"),
        ("https://monitoring.us-east-1.amazonaws.com", "POST", "/"),
        ("https://s3.us-east-1.amazonaws.com", "GET", "/example-bucket?location"),
    ],
)
async def test_aws_transport_forwards_normal_requests_without_url_credentials(
    endpoint, method, target, monkeypatch
):
    forwarded = []

    async def capture(_pool, request):
        forwarded.append(request)
        async def body():
            yield b"ok"
        return httpcore.Response(200, content=body())

    monkeypatch.setattr(httpcore.AsyncConnectionPool, "handle_async_request", capture)
    transport = AwsPinnedHttpTransport(endpoint)
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        response = await client.request(method, endpoint + target)

    assert response.status_code == 200
    assert response.content == b"ok"
    assert len(forwarded) == 1
    assert forwarded[0].method == method.encode()
    assert forwarded[0].url.target == target.encode()


@pytest.mark.asyncio
async def test_aws_transport_pins_tcp_to_resolved_public_ip():
    class _Backend:
        def __init__(self):
            self.targets = []

        async def connect_tcp(self, host, port, **_kwargs):
            self.targets.append((host, port))
            return object()

        async def sleep(self, _seconds):
            return None

    backend = _Backend()

    async def resolve(host, port):
        assert host == "sts.us-east-1.amazonaws.com"
        assert port == 443
        return ["93.184.216.34"]

    pinned = PinnedNetworkBackend(
        "sts.us-east-1.amazonaws.com", backend=backend, resolver=resolve
    )
    await pinned.connect_tcp("sts.us-east-1.amazonaws.com", 443)

    assert backend.targets == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_aws_transport_rejects_dns_answer_for_another_destination():
    class _Backend:
        def __init__(self):
            self.targets = []

        async def connect_tcp(self, host, port, **_kwargs):
            self.targets.append((host, port))
            return object()

        async def sleep(self, _seconds):
            return None

    backend = _Backend()

    async def resolve(_host, _port):
        return ["93.184.216.34", "169.254.169.254"]

    pinned = PinnedNetworkBackend(
        "sts.us-east-1.amazonaws.com", backend=backend, resolver=resolve
    )
    with pytest.raises(NetworkPolicyError):
        await pinned.connect_tcp("sts.us-east-1.amazonaws.com", 443)

    assert backend.targets == []


def test_aws_sdk_context_rejects_noncommercial_regions():
    from src.openship.execution.aws_sdk import AwsSdkAuth, AwsSdkContext

    with pytest.raises(AwsSdkError) as caught:
        AwsSdkContext(
            auth=AwsSdkAuth("access", "secret"),
            region="us-gov-west-1",
            control=object(),
        )

    assert caught.value.code == "aws_configuration_error"
