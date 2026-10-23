import asyncio
import gzip
import importlib

import httpx
import pytest
from httpx import AsyncByteStream

from src.openship.providers.errors import ProviderError


class _BodyStream(AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        yield self.body

    async def aclose(self):
        return None


def _sdk_types():
    try:
        module = importlib.import_module("src.openship.execution.aws_sdk")
    except ModuleNotFoundError:
        pytest.fail("the managed AWS SDK context has not been implemented")
    return module.AwsSdkContext, module.AwsSdkAuth, module.AwsSdkError


class _Control:
    def __init__(self):
        self.stopped = False

    def request_timeout_seconds(self, limit):
        return limit

    def raise_if_stopped(self):
        if self.stopped:
            raise ProviderError("cancelled")


def _identity_xml():
    return b"""<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
      <GetCallerIdentityResult>
        <UserId>synthetic-user</UserId>
        <Account>123456789012</Account>
        <Arn>arn:aws:iam::123456789012:user/synthetic</Arn>
      </GetCallerIdentityResult>
      <ResponseMetadata><RequestId>synthetic-request</RequestId></ResponseMetadata>
    </GetCallerIdentityResponse>"""


def _access_denied_xml():
    return b"""<ErrorResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/">
      <Error><Type>Sender</Type><Code>AccessDenied</Code>
      <Message>provider-error-message-sentinel</Message></Error>
      <RequestId>synthetic-request</RequestId>
    </ErrorResponse>"""


def _context(handler, *, control=None, max_workers=2, max_requests=8):
    AwsSdkContext, AwsSdkAuth, _AwsSdkError = _sdk_types()
    return AwsSdkContext(
        auth=AwsSdkAuth(
            access_key_id="explicit-access-key",
            secret_access_key="explicit-secret-key",
            session_token="explicit-session-token",
        ),
        region="us-east-1",
        control=control or _Control(),
        max_workers=max_workers,
        max_requests=max_requests,
        transport_factory=lambda _endpoint: httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_real_botocore_client_signs_and_parses_identity_through_adapter():
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/xml"},
            stream=_BodyStream(_identity_xml()),
        )

    async with _context(respond) as aws:
        identity = await aws.run(aws.client("sts").get_caller_identity)

    assert identity["Account"] == "123456789012"
    assert identity["Arn"].endswith("/synthetic")
    assert len(requests) == 1
    assert requests[0].url.host == "sts.us-east-1.amazonaws.com"
    assert requests[0].method == "POST"
    assert b"Action=GetCallerIdentity" in requests[0].content
    assert "explicit-access-key" in requests[0].headers["authorization"]
    assert "explicit-secret-key" not in repr(requests[0].headers)


@pytest.mark.asyncio
async def test_sdk_context_closes_after_the_last_call_finishes():
    async def respond(_request):
        return httpx.Response(200, stream=_BodyStream(_identity_xml()))

    aws = _context(respond)
    await aws.run(aws.client("sts").get_caller_identity)
    try:
        await asyncio.wait_for(aws.aclose(), timeout=0.5)
    except TimeoutError:
        # Keep the test process clean if the shutdown regression returns.
        aws._executor.shutdown(wait=False, cancel_futures=True)
        raise


@pytest.mark.asyncio
async def test_client_ignores_ambient_credentials_endpoints_and_proxy(monkeypatch):
    requests = []
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret-key")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://metadata.google.internal")
    monkeypatch.setenv("AWS_PROFILE", "missing-profile")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    monkeypatch.setenv("AWS_CA_BUNDLE", "/missing/operator-ca.pem")

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, stream=_BodyStream(_identity_xml()))

    async with _context(respond) as aws:
        await aws.run(aws.client("sts").get_caller_identity)

    assert len(requests) == 1
    assert requests[0].url.host == "sts.us-east-1.amazonaws.com"
    assert "explicit-access-key" in requests[0].headers["authorization"]
    assert "ambient-access-key" not in requests[0].headers["authorization"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 403])
async def test_oversized_sdk_responses_are_rejected_without_body_leaks(status_code):
    _AwsSdkContext, _AwsSdkAuth, AwsSdkError = _sdk_types()
    sentinel = b"sts-body-sentinel"
    body = b"<Response>" + sentinel + b"x" * (2 * 1024 * 1024) + b"</Response>"

    async def respond(_request):
        return httpx.Response(
            status_code,
            headers={"content-type": "text/xml"},
            stream=_BodyStream(body),
        )

    async with _context(respond) as aws:
        with pytest.raises(AwsSdkError) as caught:
            await aws.run(aws.client("sts").get_caller_identity)

    assert caught.value.code == "aws_output_limit_exceeded"
    assert sentinel.decode() not in str(caught.value)
    assert sentinel.decode() not in repr(caught.value)


@pytest.mark.asyncio
async def test_compressed_expansion_is_limited_before_botocore_parses_it():
    _AwsSdkContext, _AwsSdkAuth, AwsSdkError = _sdk_types()
    sentinel = b"compressed-sts-sentinel"
    body = gzip.compress(b"<Response>" + sentinel + b"x" * (2 * 1024 * 1024) + b"</Response>")

    async def respond(_request):
        return httpx.Response(
            200,
            headers={"content-type": "text/xml", "content-encoding": "gzip"},
            stream=_BodyStream(body),
        )

    async with _context(respond) as aws:
        with pytest.raises(AwsSdkError) as caught:
            await aws.run(aws.client("sts").get_caller_identity)

    assert caught.value.code == "aws_output_limit_exceeded"
    assert sentinel.decode() not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code, expected_code", [(302, "aws_redirect"), (503, "aws_unavailable")])
async def test_redirect_and_service_errors_are_safe_and_never_retried(status_code, expected_code):
    _AwsSdkContext, _AwsSdkAuth, AwsSdkError = _sdk_types()
    requests = []
    sentinel = "signed-header-and-error-body-sentinel"

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            status_code,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
            stream=_BodyStream(f"<Error>{sentinel}</Error>".encode()),
        )

    async with _context(respond) as aws:
        with pytest.raises(AwsSdkError) as caught:
            await aws.run(aws.client("sts").get_caller_identity)

    assert caught.value.code == expected_code
    assert len(requests) == 1
    assert sentinel not in str(caught.value)
    assert "explicit-access-key" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation, expected_code",
    [
        ("get_caller_identity", "aws_access_denied"),
        ("assume_role", "aws_trust_denied"),
    ],
)
async def test_access_denial_is_classified_without_returning_provider_message(
    operation, expected_code
):
    _AwsSdkContext, _AwsSdkAuth, AwsSdkError = _sdk_types()

    async def respond(_request):
        return httpx.Response(
            403,
            headers={"content-type": "text/xml"},
            stream=_BodyStream(_access_denied_xml()),
        )

    async with _context(respond) as aws:
        sts = aws.client("sts")
        method = getattr(sts, operation)
        parameters = (
            {"RoleArn": "arn:aws:iam::123456789012:role/readonly", "RoleSessionName": "test"}
            if operation == "assume_role"
            else {}
        )
        with pytest.raises(AwsSdkError) as caught:
            await aws.run(method, **parameters)

    assert caught.value.code == expected_code
    assert "provider-error-message-sentinel" not in str(caught.value)
    assert "provider-error-message-sentinel" not in repr(caught.value)


@pytest.mark.asyncio
async def test_physical_request_budget_is_shared_across_sdk_calls():
    _AwsSdkContext, _AwsSdkAuth, AwsSdkError = _sdk_types()
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, stream=_BodyStream(_identity_xml()))

    async with _context(respond, max_requests=1) as aws:
        client = aws.client("sts")
        await aws.run(client.get_caller_identity)
        with pytest.raises(AwsSdkError) as caught:
            await aws.run(client.get_caller_identity)

    assert caught.value.code == "aws_request_limit_exceeded"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_executor_slot_is_held_until_the_first_sdk_call_finishes():
    requests = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def respond(request):
        requests.append(request)
        if len(requests) == 1:
            entered.set()
            await release.wait()
        return httpx.Response(200, stream=_BodyStream(_identity_xml()))

    async with _context(respond, max_workers=1) as aws:
        client = aws.client("sts")
        first = asyncio.create_task(aws.run(client.get_caller_identity))
        await asyncio.wait_for(entered.wait(), timeout=1)
        second = asyncio.create_task(aws.run(client.get_caller_identity))
        await asyncio.sleep(0.05)
        assert len(requests) == 1
        release.set()
        await asyncio.gather(first, second)

    assert len(requests) == 2


@pytest.mark.asyncio
async def test_closing_context_rejects_a_call_waiting_for_executor_capacity():
    _AwsSdkContext, _AwsSdkAuth, AwsSdkError = _sdk_types()
    requests = []
    entered = asyncio.Event()

    async def respond(request):
        requests.append(request)
        entered.set()
        await asyncio.Event().wait()

    aws = _context(respond, max_workers=1)
    client = aws.client("sts")
    first = asyncio.create_task(aws.run(client.get_caller_identity))
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(aws.run(client.get_caller_identity))
    await asyncio.sleep(0.05)
    closing = asyncio.create_task(aws.aclose())

    results = await asyncio.wait_for(
        asyncio.gather(first, second, closing, return_exceptions=True), timeout=1
    )

    assert isinstance(results[0], AwsSdkError)
    assert isinstance(results[1], AwsSdkError)
    assert results[1].code == "aws_cancelled"
    assert len(requests) == 1


class _BlockingBody(AsyncByteStream):
    def __init__(self, started, closed):
        self.started = started
        self.closed = closed

    async def __aiter__(self):
        self.started.set()
        await asyncio.Event().wait()
        yield b"never"

    async def aclose(self):
        self.closed.set()


@pytest.mark.asyncio
async def test_stopping_a_call_cancels_its_active_transport_and_closes_stream():
    _AwsSdkContext, _AwsSdkAuth, AwsSdkError = _sdk_types()
    control = _Control()
    started = asyncio.Event()
    closed = asyncio.Event()

    async def respond(_request):
        return httpx.Response(
            200,
            headers={"content-type": "text/xml"},
            stream=_BlockingBody(started, closed),
        )

    async with _context(respond, control=control, max_workers=1) as aws:
        task = asyncio.create_task(aws.run(aws.client("sts").get_caller_identity))
        await asyncio.wait_for(started.wait(), timeout=1)
        control.stopped = True
        with pytest.raises(AwsSdkError) as caught:
            await asyncio.wait_for(task, timeout=1)
        await asyncio.wait_for(closed.wait(), timeout=1)

    assert caught.value.code == "aws_cancelled"


def test_pinned_network_backend_rejects_mixed_public_and_private_dns_answers():
    try:
        module = importlib.import_module("src.openship.execution.network")
    except ModuleNotFoundError:
        pytest.fail("shared pinned-network backend has not been extracted")

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
        return ["93.184.216.34", "10.0.0.8"]

    pinned = module.PinnedNetworkBackend(
        "sts.us-east-1.amazonaws.com",
        backend=backend,
        resolver=resolve,
    )
    with pytest.raises(module.NetworkPolicyError):
        import asyncio

        asyncio.run(pinned.connect_tcp("sts.us-east-1.amazonaws.com", 443))

    assert backend.targets == []


def test_aws_sdk_auth_repr_hides_values():
    _AwsSdkContext, AwsSdkAuth, _AwsSdkError = _sdk_types()
    auth = AwsSdkAuth(
        access_key_id="access-key-sentinel",
        secret_access_key="secret-key-sentinel",
        session_token="session-token-sentinel",
    )

    assert "access-key-sentinel" not in repr(auth)
    assert "secret-key-sentinel" not in repr(auth)
    assert "session-token-sentinel" not in repr(auth)
