import uuid

import pytest

from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    ExecutionConfig,
    ExecutionLimits,
    ToolInvocationContext,
)
from src.openship.providers.control import InvocationControl
from src.openship.tools.search.brave import search_brave
from src.openship.tools.search.contracts import SearchArguments
from src.openship.tools.search.http import BRAVE_SEARCH_ENDPOINT, SearchToolError

SENTINEL_KEY = "brave-test-secret-sentinel"


def _context(credential=SENTINEL_KEY, *, limits=None):
    return ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            action="invoke_tool",
            target_id="brave_web_search",
        ),
        execution=ExecutionConfig(
            graph_kind="tool", graph_version=1, limits=limits or ExecutionLimits()
        ),
        credential_resolver=lambda name: credential if name == "BRAVE_SEARCH_API_KEY" else None,
    )


class _FakeSearchHttp:
    def __init__(
        self,
        endpoint,
        *,
        max_response_bytes=2 * 1024 * 1024,
        timeout_seconds=30,
        payload=None,
        error=None,
        calls=None,
    ):
        assert endpoint == BRAVE_SEARCH_ENDPOINT
        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = timeout_seconds
        self.payload = payload
        self.error = error
        self.calls = calls if calls is not None else []

    async def get_json(self, *, params, headers, control):
        self.calls.append(
            {
                "params": params,
                "headers": headers,
                "control": control,
                "max_response_bytes": self.max_response_bytes,
                "timeout_seconds": self.timeout_seconds,
            }
        )
        if self.error:
            raise self.error
        return self.payload


def _payload(results):
    return {"web": {"results": results}}


@pytest.mark.asyncio
async def test_brave_uses_exact_binding_endpoint_and_maps_web_results(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.openship.tools.search.brave.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint,
            **kwargs,
            payload=_payload(
                [
                    {
                        "title": "First result",
                        "url": "https://example.com/one",
                        "description": "A <b>useful</b> snippet &amp; more.",
                    },
                    {
                        "title": "Unsafe URL",
                        "url": "http://user:pass@example.org/private",
                        "description": "drop this result",
                    },
                    {
                        "title": "Second result",
                        "url": "https://example.net/two",
                        "description": None,
                    },
                ]
            ),
            calls=calls,
        ),
    )

    result = await search_brave(
        SearchArguments(query="  deploy gVisor  ".strip(), max_results=2),
        _context(
            limits=ExecutionLimits(
                operation_timeout_seconds=9, max_http_response_bytes=4096
            )
        ),
        InvocationControl(),
    )

    expected_tool = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
    )
    assert result.tool.id == "brave_web_search"
    assert result.tool.version == expected_tool.version
    assert result.tool.manifest_digest == expected_tool.manifest_digest
    assert result.query == "deploy gVisor"
    assert result.requested_count == 2
    assert result.returned_count == 1
    assert result.items[0].title == "First result"
    assert result.items[0].snippet == "A useful snippet & more."
    assert result.items[0].rank == 1
    assert result.truncated is True
    assert len(calls) == 1
    assert calls[0]["params"] == {
        "q": "deploy gVisor",
        "count": 2,
        "text_decorations": "false",
        "result_filter": "web",
    }
    assert calls[0]["headers"] == {
        "Accept": "application/json",
        "X-Subscription-Token": SENTINEL_KEY,
    }
    assert calls[0]["max_response_bytes"] == 4096
    assert calls[0]["timeout_seconds"] == 9


@pytest.mark.asyncio
async def test_missing_brave_key_fails_before_http_and_does_not_fallback(monkeypatch):
    def unexpected_http(_endpoint, **_kwargs):
        raise AssertionError("missing credentials must fail before opening HTTP")

    monkeypatch.setattr(
        "src.openship.tools.search.brave.BoundedSearchHttp", unexpected_http
    )

    with pytest.raises(SearchToolError) as caught:
        await search_brave(
            SearchArguments(query="query"),
            _context(credential=None),
            InvocationControl(),
        )

    assert caught.value.code == "missing_credentials"
    assert SENTINEL_KEY not in str(caught.value)


@pytest.mark.asyncio
async def test_empty_success_is_valid_and_malformed_envelope_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "src.openship.tools.search.brave.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint, **kwargs, payload={"web": None}
        ),
    )
    empty = await search_brave(
        SearchArguments(query="query"), _context(), InvocationControl()
    )
    assert empty.items == ()
    assert empty.returned_count == 0
    assert empty.truncated is False

    monkeypatch.setattr(
        "src.openship.tools.search.brave.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint, **kwargs, payload={"web": {"results": "bad"}}
        ),
    )
    with pytest.raises(SearchToolError) as caught:
        await search_brave(
            SearchArguments(query="query"), _context(), InvocationControl()
        )
    assert caught.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_provider_echoed_credential_is_not_persisted_in_results(monkeypatch):
    monkeypatch.setattr(
        "src.openship.tools.search.brave.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint,
            **kwargs,
            payload=_payload(
                [
                    {
                        "title": "Leaked token " + SENTINEL_KEY,
                        "url": "https://example.com/private",
                        "description": "The service echoed a credential.",
                    }
                ]
            ),
        ),
    )

    result = await search_brave(
        SearchArguments(query="query"), _context(), InvocationControl()
    )

    assert result.items == ()
    assert result.truncated is True
    assert SENTINEL_KEY not in result.model_dump_json()


@pytest.mark.asyncio
async def test_rejected_key_is_a_fixed_error_with_no_provider_or_fallback_details(monkeypatch):
    fake_error = SearchToolError("authentication_error")
    calls = []
    monkeypatch.setattr(
        "src.openship.tools.search.brave.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint, **kwargs, error=fake_error, calls=calls
        ),
    )

    with pytest.raises(SearchToolError) as caught:
        await search_brave(
            SearchArguments(query="query"), _context(), InvocationControl()
        )

    assert caught.value.code == "authentication_error"
    assert SENTINEL_KEY not in str(caught.value)
    assert len(calls) == 1
