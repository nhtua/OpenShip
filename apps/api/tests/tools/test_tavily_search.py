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
from src.openship.tools.search.contracts import SearchArguments
from src.openship.tools.search.http import TAVILY_SEARCH_ENDPOINT, SearchToolError
from src.openship.tools.search.tavily import search_tavily

SENTINEL_KEY = "tavily-test-secret-sentinel"


def _context(credentials=None, *, limits=None):
    credentials = credentials or {"TAVILY_API_KEY": SENTINEL_KEY}
    requested_bindings = []

    def resolve(binding):
        requested_bindings.append(binding)
        return credentials.get(binding)

    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            action="invoke_tool",
            target_id="tavily_web_search",
        ),
        execution=ExecutionConfig(
            graph_kind="tool", graph_version=1, limits=limits or ExecutionLimits()
        ),
        credential_resolver=resolve,
    )
    return context, requested_bindings


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
        assert endpoint == TAVILY_SEARCH_ENDPOINT
        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = timeout_seconds
        self.payload = payload
        self.error = error
        self.calls = calls if calls is not None else []

    async def post_json(self, *, json_body, headers, control):
        self.calls.append(
            {
                "json_body": json_body,
                "headers": headers,
                "control": control,
                "max_response_bytes": self.max_response_bytes,
                "timeout_seconds": self.timeout_seconds,
            }
        )
        if self.error:
            raise self.error
        return self.payload


@pytest.mark.asyncio
async def test_tavily_uses_its_key_and_fixed_basic_general_request(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.openship.tools.search.tavily.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint,
            **kwargs,
            calls=calls,
            payload={
                "query": "terraform module",
                "answer": "must not be retained",
                "usage": {"credits": 1},
                "results": [
                    {
                        "title": "Terraform module",
                        "url": "https://example.com/module",
                        "content": "A <b>useful</b> result.",
                        "score": 0.97,
                        "raw_content": "must not be retained",
                    },
                    {
                        "title": "Unsafe result",
                        "url": "https://user:pass@example.org/private",
                        "content": "discard",
                        "score": 0.5,
                    },
                    {
                        "title": "A title without optional fields",
                        "url": "https://example.net/partial",
                    },
                ],
            },
        ),
    )
    context, requested_bindings = _context(
        limits=ExecutionLimits(
            operation_timeout_seconds=11, max_http_response_bytes=8192
        )
    )

    result = await search_tavily(
        SearchArguments(query="terraform module", max_results=3),
        context,
        InvocationControl(),
    )

    expected_tool = next(
        item for item in list_capability_manifests() if item.id == "tavily_web_search"
    )
    assert result.tool.id == "tavily_web_search"
    assert result.tool.version == expected_tool.version
    assert result.tool.manifest_digest == expected_tool.manifest_digest
    assert result.query == "terraform module"
    assert result.returned_count == 2
    assert result.items[0].title == "Terraform module"
    assert result.items[0].snippet == "A useful result."
    assert result.items[0].rank == 1
    assert result.items[1].snippet == ""
    assert result.items[1].rank == 3
    assert result.truncated is True
    assert result.provider_metadata == {}
    serialized = result.model_dump_json()
    assert "must not be retained" not in serialized
    assert "0.97" not in serialized
    assert requested_bindings == ["TAVILY_API_KEY"]
    assert len(calls) == 1
    assert calls[0]["json_body"] == {
        "query": "terraform module",
        "search_depth": "basic",
        "topic": "general",
        "max_results": 3,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_image_descriptions": False,
        "include_favicon": False,
        "auto_parameters": False,
        "include_usage": False,
    }
    assert calls[0]["headers"] == {
        "Accept": "application/json",
        "Authorization": f"Bearer {SENTINEL_KEY}",
        "Content-Type": "application/json",
    }
    assert calls[0]["max_response_bytes"] == 8192
    assert calls[0]["timeout_seconds"] == 11


@pytest.mark.asyncio
async def test_missing_tavily_key_fails_before_http_without_using_brave(monkeypatch):
    def unexpected_http(_endpoint, **_kwargs):
        raise AssertionError("missing Tavily key must fail before opening HTTP")

    monkeypatch.setattr(
        "src.openship.tools.search.tavily.BoundedSearchHttp", unexpected_http
    )
    context, requested_bindings = _context(
        {"BRAVE_SEARCH_API_KEY": "brave-key-must-not-be-used"}
    )

    with pytest.raises(SearchToolError) as caught:
        await search_tavily(
            SearchArguments(query="query"), context, InvocationControl()
        )

    assert caught.value.code == "missing_credentials"
    assert requested_bindings == ["TAVILY_API_KEY"]
    assert "brave-key-must-not-be-used" not in str(caught.value)


@pytest.mark.asyncio
async def test_empty_success_and_rejected_tavily_key_have_fixed_outcomes(monkeypatch):
    monkeypatch.setattr(
        "src.openship.tools.search.tavily.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint, **kwargs, payload={"results": []}
        ),
    )
    context, _ = _context()
    empty = await search_tavily(
        SearchArguments(query="query"), context, InvocationControl()
    )
    assert empty.items == ()
    assert empty.truncated is False

    monkeypatch.setattr(
        "src.openship.tools.search.tavily.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint, **kwargs, error=SearchToolError("authentication_error")
        ),
    )
    with pytest.raises(SearchToolError) as caught:
        await search_tavily(
            SearchArguments(query="query"), context, InvocationControl()
        )
    assert caught.value.code == "authentication_error"
    assert SENTINEL_KEY not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{"results": "bad"}, {"results": [{"title": 2, "url": "https://example.com"}]}]
)
async def test_malformed_tavily_envelope_or_result_is_rejected(monkeypatch, payload):
    monkeypatch.setattr(
        "src.openship.tools.search.tavily.BoundedSearchHttp",
        lambda endpoint, **kwargs: _FakeSearchHttp(
            endpoint, **kwargs, payload=payload
        ),
    )

    with pytest.raises(SearchToolError) as caught:
        await search_tavily(
            SearchArguments(query="query"), _context()[0], InvocationControl()
        )
    assert caught.value.code == "invalid_response"
