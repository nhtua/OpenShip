"""Synthetic provider transports shared by every adapter's conformance case.

The transport stands in for the SDK at its outermost edge: it records the
request the adapter actually made and whether the adapter closed the stream
it opened. Everything else in a case — the adapter, the contracts, the
control — is the real component under test.
"""

import uuid

#: Imported before any case replaces a factory: a lazy import inside a case
#: would re-register the real adapter over the case's own, so a second
#: request in the same turn would build a client instead of using the fixture.
import src.openship.providers.openai  # noqa: F401
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
#: The credential binding a fixture profile names. Only its name is ever used.
CREDENTIAL_REF = "FIXTURE_KEY"  # rayloc:ignore


#: What each API is called and the options its contract requires. A fixture
#: profile that declared the wrong adapter would not be testing the adapter
#: the case drives.
_API_VERSION = {"openai": "chat-completions", "anthropic": "messages"}
_REQUIRED_OPTIONS = {
    "openai": {"output_limit_parameter": "max_tokens"},
    "anthropic": {"server_side_fallback": False},
}


def qualified_profile(*, adapter: str = "openai", output_limit_parameter=None, **overrides):
    """A text-only profile an operator would declare, ready to pin."""
    options = {"adapter": adapter, **_REQUIRED_OPTIONS[adapter]}
    if output_limit_parameter is not None:
        options["output_limit_parameter"] = output_limit_parameter
    spec = {
        "id": "fixture-profile",
        "label": "Fixture profile",
        "adapter": adapter,
        "model": FIXTURE_MODEL,
        "endpoint": FIXTURE_ENDPOINT,
        "api_version": _API_VERSION[adapter],
        "credential_ref": CREDENTIAL_REF,  # rayloc:ignore
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
        "adapter_options": options,
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
        item = self._remaining.pop(0)
        if isinstance(item, BaseException):
            # A stream that fails after it began: the case's own failure fixture.
            raise item
        return item

    async def aclose(self):
        self._transport.aclose()

    async def close(self):
        """The name the Messages SDK uses for the same cleanup."""
        self._transport.aclose()


class SequenceTransport:
    """A transport that answers differently per request, so a case is a sequence.

    Each request takes the next behavior — chunks to stream, or an error to
    raise — and the last behavior repeats, so a case states only the requests
    it cares about. Every request is recorded, which is how a case shows what
    the runner actually asked for and how many times it asked.
    """

    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self._current = 0
        self.closed = False
        self.requests = 0
        self.seen = []

    async def create(self, **kwargs):
        self.requests += 1
        behavior = self._behaviors[min(self._current, len(self._behaviors) - 1)]
        self._current += 1
        self.seen.append(kwargs)
        if behavior.get("raises") is not None:
            raise behavior["raises"]
        return _Stream(list(behavior.get("chunks", ())), self)

    def aclose(self):
        self.closed = True

    @property
    def seen_kwargs(self):
        return self.seen[-1] if self.seen else None


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


class AnthropicClient:
    """The shape an adapter asks a client for: messages.create."""

    def __init__(self, transport):
        self.messages = _Nested(create=transport.create)
        self._transport = transport

    @property
    def transport(self):
        return self._transport


class CountingClient:
    """A Messages client whose counter answers, so a case can read the ask.

    The counter is the provider's own: a case that asserts a number here is
    asserting what the provider reported, never what the adapter measured.
    """

    def __init__(self, counted: int):
        self.asked = []
        self._counted = counted
        self.messages = _Nested(count_tokens=self._count)

    async def _count(self, **kwargs):
        self.asked.append(kwargs)
        return _Nested(input_tokens=self._counted)


def _event(**fields):
    """One parsed Messages-stream event, named as the SDK names its fields."""
    return _Nested(**fields)


def anthropic_message_start(*, model=FIXTURE_MODEL, input_tokens=None, content=()):
    """``message_start``: the snapshot the API opens a stream with.

    The input count is the only place this API reports it, so a fixture that
    leaves it out is a stream whose input stays unknown.
    """
    return _event(
        type="message_start",
        message=_Nested(
            content=list(content),
            id="msg-fixture",
            model=model,
            role="assistant",
            stop_reason=None,
            usage=_Nested(input_tokens=input_tokens, output_tokens=0),
        ),
    )


def anthropic_text(part):
    """A text delta - the only event that is answer text."""
    return _event(type="content_block_delta", index=0, delta=_Nested(type="text_delta", text=part))


def anthropic_thinking(part):
    """A reasoning delta. It is not answer text, and nothing here runs it."""
    return _event(
        type="content_block_delta",
        index=0,
        delta=_Nested(type="thinking_delta", thinking=part),
    )


def anthropic_tool_json(part):
    """A tool block's partial JSON. Not answer text; not executed here."""
    return _event(
        type="content_block_delta",
        index=0,
        delta=_Nested(type="input_json_delta", partial_json=part),
    )


def anthropic_block_start(kind, index=0):
    """``content_block_start``: the kind of block the API is opening."""
    return _event(type="content_block_start", index=index, content_block=_Nested(type=kind))


def anthropic_block_stop(index=0):
    return _event(type="content_block_stop", index=index)


def anthropic_finish(reason=None, *, output_tokens=0):
    """``message_delta``: the end and the cumulative output count, as one event.

    This event never carries the input count, which is why the adapter keeps
    the one it already saw rather than reading a new one here.
    """
    return _event(
        type="message_delta",
        delta=_Nested(stop_reason=reason, stop_sequence=None),
        usage=_Nested(output_tokens=output_tokens),
    )


def anthropic_stop():
    return _event(type="message_stop")


def anthropic_ping():
    """A non-content event the API sends. The adapter has no use for it."""
    return _event(type="ping", error_message="pong")


#: The same end, named as each API names it.
_OPENAI_REASON = {"stop": "stop", "length": "length", "content_filter": "content_filter"}
_ANTHROPIC_REASON = {"stop": "end_turn", "length": "max_tokens", "stop_sequence": "stop_sequence"}


def provider_stream(adapter, *, text=(), finish="stop", usage=(), model=FIXTURE_MODEL):
    """The provider-shaped stream that means: these parts, this end, these counts.

    A shared case states one claim; what that claim looks like on the wire
    belongs to the provider. This is the only place either shape is written,
    so a shared case cannot quietly stop meaning something for one of them.

    ``usage`` is a sequence of (input, output) counts as the provider reports
    them. This API reports the input count once, at the start, and the output
    count once, cumulatively, at the end - which is why the two spellings
    differ and the claim does not.
    """
    if adapter == "openai":
        chunks = [sdk_chunk(content=part) for part in text]
        chunks += [sdk_chunk(choices=False, usage=(counted, output, counted + output))
                   for counted, output in usage]
        if finish:
            chunks.append(sdk_chunk(finish_reason=_OPENAI_REASON[finish], model=model))
        return chunks

    if adapter == "anthropic":
        events = [
            anthropic_message_start(
                model=model,
                input_tokens=usage[0][0] if usage else None,
            )
        ]
        events.append(anthropic_ping())
        events += [anthropic_text(part) for part in text]
        if finish:
            events.append(
                anthropic_finish(
                    _ANTHROPIC_REASON[finish],
                    output_tokens=usage[-1][1] if usage else 0,
                )
            )
        events.append(anthropic_stop())
        return events

    raise AssertionError(f"no stream shape for adapter {adapter!r}")


def asked_turns(adapter, kwargs) -> list[tuple[str, str]]:
    """What the provider was asked for, back as (role, text) pairs.

    Each API spells a conversation differently: one puts the system turn in
    the message list, the other takes it as its own parameter. The shared
    claim is about the conversation, so this is where the spelling stops and
    the claim continues.
    """
    if adapter == "openai":
        return [(turn["role"], turn["content"]) for turn in kwargs["messages"]]
    if adapter == "anthropic":
        system = kwargs.get("system")
        opening = [("system", system)] if system else []
        return opening + [(turn["role"], turn["content"]) for turn in kwargs["messages"]]
    raise AssertionError(f"no request shape for adapter {adapter!r}")


def make_adapter(name: str, transport) -> object:
    """A real adapter wired to a synthetic transport."""
    if name == "openai":
        from src.openship.providers.openai import OpenAIAdapter

        return OpenAIAdapter(client_factory=lambda _config: ProviderClient(transport))
    if name == "anthropic":
        from src.openship.providers.anthropic import AnthropicAdapter

        return AnthropicAdapter(client_factory=lambda _config: AnthropicClient(transport))
    raise AssertionError(f"no adapter registered for {name!r}")


class CountingAdapter:
    """A token-aware adapter stand-in: it counts, and it streams through a transport.

    The OpenAI adapter deliberately never counts, so a case about the runner's
    reservation supplies the counter a qualified profile declares.
    """

    def __init__(self, transport, counted: int):
        self._adapter = make_adapter("openai", transport)
        self._counted = counted

    async def stream(self, request, profile, control_):
        async for event in self._adapter.stream(request, profile, control_):
            yield event

    async def count_input_tokens(self, request, profile, control_):
        control_.raise_if_stopped()
        return self._counted


def adapter_case(*, chunks, raises=None, adapter="openai", profile_kwargs=None):
    """A fresh case: a real adapter, a qualified profile, and a transport."""
    transport = StreamTransport(chunks, raises=raises)
    profile = qualified_profile(adapter=adapter, **(profile_kwargs or {}))
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

    path = "/chat/completions" if name == "openai" else "/v1/messages"
    request = httpx2.Request("POST", FIXTURE_ENDPOINT + path)
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
    if name == "anthropic":
        import anthropic

        if kind == "rate_limited":
            response = httpx2.Response(
                429,
                headers={"retry-after": "7", "request-id": "req-fixture"},
                request=request,
            )
            return anthropic.RateLimitError(
                '{"type": "error", "error": {"type": "rate_limit_error", "message": "secret-body"}}',
                response=response,
                body=None,
            )
        if kind == "unreachable":
            return anthropic.APIConnectionError(request=request)
        if kind == "unauthorized":
            response = httpx2.Response(401, request=request)
            return anthropic.AuthenticationError(
                "invalid api key", response=response, body=None
            )
        if kind == "overloaded":
            response = httpx2.Response(529, request=request)
            return anthropic.OverloadedError(
                '{"type": "error", "error": {"type": "overloaded_error", "message": "secret-body"}}',
                response=response,
                body=None,
            )
    raise AssertionError(f"no error fixture {kind!r} for {name!r}")


def control(*, remaining=30.0, stopped=False, stop=None):
    """A real InvocationControl with an injected clock and stop signal."""
    from src.openship.providers.control import InvocationControl

    return InvocationControl(
        deadline=remaining,
        clock=lambda: 0.0,
        stop=stop if stop is not None else (lambda: stopped),
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


class FakeRecorder:
    """A recorder that keeps what it was asked, so a case can assert on it.

    The database recorder belongs to the run's transaction; a unit case only
    needs to know that an attempt was opened and closed, and with which result.
    """

    def __init__(self):
        self.started = []
        self.finished = []
        self.reservations = []

    def start(self, profile, reason, reserved_cost_usd=None):
        self.started.append((profile.id, reason))
        self.reservations.append(reserved_cost_usd)
        return uuid.uuid4()

    def finish(self, attempt_id, outcome, usage=None, error_code=None, actual_model=None):
        self.finished.append(
            {"outcome": outcome, "error_code": error_code, "usage": usage}
        )


def invocation(*, control_=None, recorder=None):
    """The pair a model step asks an invocation to supply, per call."""
    from src.openship.providers.control import InvocationControl

    return lambda: (
        control_ if control_ is not None else InvocationControl(),
        recorder if recorder is not None else FakeRecorder(),
    )


def run_snapshot(**overrides) -> str:
    """A model configuration a run would carry, as the graph reads it."""
    from src.openship.providers.contracts import RunLimits, RunModelConfig

    config = RunModelConfig(
        schema_version=1,
        primary=qualified_profile(**overrides),
        limits=RunLimits(),
        binding_origin="submission",
    )
    return config.model_dump_json()


def run_state(*turns) -> dict:
    """A graph state carrying the configuration this run pinned."""
    return {
        "messages": [{"role": role, "content": text} for role, text in turns],
        "run_id": "test-run-model-step",
        "conversation_id": "test-conv-model-step",
        "model_configuration": run_snapshot(),
    }


def serve(monkeypatch, chunks=(), raises=None, adapter="openai") -> StreamTransport:
    """The installation's provider answers from a synthetic transport."""
    from src.openship.providers.registry import _adapter_factories

    transport = StreamTransport(list(chunks), raises=raises)
    monkeypatch.setitem(
        _adapter_factories, adapter, lambda _profile: make_adapter(adapter, transport)
    )
    return transport


def serve_requests(monkeypatch, behaviors) -> SequenceTransport:
    """The installation's OpenAI adapter answers a sequence of requests.

    A case that is about attempts, retries, or fallback states what each
    request answers with, so the runner's policy is what is under test.
    """
    from src.openship.providers.registry import _adapter_factories

    transport = SequenceTransport(list(behaviors))
    monkeypatch.setitem(
        _adapter_factories, "openai", lambda _profile: make_adapter("openai", transport)
    )
    return transport


def serve_counted(monkeypatch, behaviors, counted: int) -> SequenceTransport:
    """The same sequence, through an adapter that qualifies a token counter."""
    from src.openship.providers.registry import _adapter_factories

    transport = SequenceTransport(list(behaviors))
    monkeypatch.setitem(
        _adapter_factories,
        "openai",
        lambda _profile: CountingAdapter(transport, counted),
    )
    return transport


def answered(monkeypatch, *parts, adapter="openai") -> StreamTransport:
    """The installation's provider answers with these text parts, in order.

    A case about the durable pipeline needs an answer that arrives whole; the
    parts are only what the caller expects to read back.
    """
    return serve(monkeypatch, provider_stream(adapter, text=parts), adapter=adapter)
