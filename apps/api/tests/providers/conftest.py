"""Synthetic provider transports shared by every adapter's conformance case.

The transport stands in for the SDK at its outermost edge: it records the
request the adapter actually made and whether the adapter closed the stream
it opened. Everything else in a case — the adapter, the contracts, the
control — is the real component under test.
"""

from src.openship.providers.contracts import (
    ChatMessage,
    ModelRequest,
    pin_profile,
)
from src.openship.providers.errors import ProviderError

#: A fixture model/endpoint pair. Not a real deployment: live qualification
#: happens only in tests/live.  # rayloc:ignore
FIXTURE_MODEL = "fixture-model"
FIXTURE_ENDPOINT = "https://fixture.provider.test/v1"


def qualified_profile(*, output_limit_parameter="max_tokens", **overrides):
    """A text-only profile an operator would declare, ready to pin."""
    spec = {
        "id": "fixture-profile",
        "label": "Fixture profile",
        "adapter": "openai",
        "model": FIXTURE_MODEL,
        "endpoint": FIXTURE_ENDPOINT,
        "api_version": "chat-completions",
        "credential_ref": "FIXTURE_KEY",  # rayloc:ignore
        "capabilities": {
            "streaming": True,
            "usage_reporting": True,
            "context_tokens": 32768,
            "max_output_tokens": 4096,
            "token_counting": False,
            "tool_calls": False,
            "structured_output": False,
            "attachment_types": [],
            "parallel_tool_calls": False,
        },
        "allowed_fallback_profile_ids": [],
        "qualified_model_aliases": [],
        "adapter_options": {
            "adapter": "openai",
            "output_limit_parameter": output_limit_parameter,
        },
    }
    spec.update(overrides)
    return pin_profile(spec)


def conversation(*turns) -> ModelRequest:
    """A ModelRequest over (role, text) pairs, in order."""
    return ModelRequest(
        messages=tuple(ChatMessage(role=role, content=text) for role, text in turns),
        max_output_tokens=128,
    )


def sdk_chunk(*, content=None, finish_reason=None, usage=None, model=FIXTURE_MODEL, choices=True):
    """One Chat Completions chunk, mirroring the SDK's full shape."""
    delta = _Delta(content=content, role=None, refusal=None, tool_calls=None)
    choice = (
        [_Choice(finish_reason=finish_reason, index=0, delta=delta, logprobs=None, message=None)]
        if choices
        else []
    )
    usage_report = None
    if usage is not None:
        input_tokens, output_tokens, total_tokens = usage
        usage_report = _Usage(
            completion_tokens=output_tokens,
            prompt_tokens=input_tokens,
            total_tokens=total_tokens,
            completion_tokens_details=None,
            prompt_tokens_details=None,
        )
    return _Chunk(
        choices=choice,
        created=0,
        id="chunk-fixture",
        model=model,
        object="chat.completion.chunk",
        system_fingerprint=None,
        usage=usage_report,
    )


class _Delta:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Chunk:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Choice:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Usage:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class StreamTransport:
    """A stream that records whether the adapter closed it."""

    def __init__(self, chunks, *, raises=None):
        self._chunks = list(chunks)
        self._raises = raises
        self.closed = False
        self.requests = 0

    async def create(self, **kwargs):
        self.requests += 1
        if self._raises is not None:
            raise self._raises
        self._seen = kwargs
        return _Stream(self._chunks, self)

    def aclose(self):
        self.closed = True

    @property
    def seen_kwargs(self):
        return getattr(self, "_seen", None)


class _Stream:
    def __init__(self, chunks, transport):
        self._remaining = list(chunks)
        self._transport = transport

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._remaining:
            raise StopAsyncIteration
        return self._remaining.pop(0)

    async def aclose(self):
        self._transport.aclose()


class ProviderClient:
    """The shape an adapter asks a client for: chat.completions.create."""

    def __init__(self, transport):
        self.chat = _Nested(completions=_Nested(create=transport.create))
        self._transport = transport

    @property
    def transport(self):
        return self._transport


class _Nested:
    def __init__(self, **fields):
        self.__dict__.update(fields)


def make_adapter(name: str, transport) -> object:
    """A real adapter wired to a synthetic transport."""
    from src.openship.providers.openai import OpenAIAdapter

    if name == "openai":
        return OpenAIAdapter(client_factory=lambda _config: ProviderClient(transport))
    raise AssertionError(f"no adapter registered for {name!r}")


def adapter_case(*, chunks, raises=None, adapter="openai", profile_kwargs=None):
    """A fresh case: a real adapter, a qualified profile, and a transport."""
    transport = StreamTransport(chunks, raises=raises)
    profile = qualified_profile(**(profile_kwargs or {}))
    return _Case(
        adapter=make_adapter(adapter, transport),
        profile=profile,
        transport=transport,
    )


class _Case:
    def __init__(self, **fields):
        self.__dict__.update(fields)


def error_fixture(name: str, kind: str = "rate_limited"):
    """A real SDK failure, built the way the SDK builds one."""
    import httpx2
    import openai

    request = httpx2.Request("POST", FIXTURE_ENDPOINT + "/chat/completions")
    if name == "openai":
        if kind == "rate_limited":
            response = httpx2.Response(
                429,
                headers={"retry-after": "7", "x-request-id": "req-fixture"},
                request=request,
            )
            return openai.RateLimitError(
                '{"error": {"message": "secret-body"}}', response=response, body=None
            )
        if kind == "unreachable":
            return openai.APIConnectionError(request=request)
        if kind == "unauthorized":
            response = httpx2.Response(401, request=request)
            return openai.AuthenticationError(
                "invalid api key", response=response, body=None
            )
    raise AssertionError(f"no error fixture {kind!r} for {name!r}")


def control(*, remaining=30.0, stopped=False):
    """A real InvocationControl with an injected clock and stop signal."""
    from src.openship.providers.control import InvocationControl

    return InvocationControl(
        deadline=remaining,
        clock=lambda: 0.0,
        stop=lambda: stopped,
    )


async def drain(adapter, request, profile, control_) -> tuple[list[object], ProviderError | None]:
    """Run one stream attempt and collect what it emitted."""
    events = []
    try:
        async for event in adapter.stream(request, profile, control_):
            events.append(event)
    except ProviderError as failure:
        return events, failure
    return events, None
