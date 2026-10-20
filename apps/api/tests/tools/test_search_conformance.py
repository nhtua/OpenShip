import uuid

import pytest

from src.openship.capabilities.catalog import list_capabilities
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    ExecutionConfig,
    ToolInvocationContext,
)
from src.openship.providers.control import InvocationControl
from src.openship.tools.search.brave import search_brave
from src.openship.tools.search.contracts import SearchArguments
from src.openship.tools.search.http import BRAVE_SEARCH_ENDPOINT, TAVILY_SEARCH_ENDPOINT
from src.openship.tools.search.tavily import search_tavily


class _FakeSearchHttp:
    def __init__(self, endpoint, *, payload):
        self.endpoint = endpoint
        self.payload = payload

    async def get_json(self, *, params, headers, control):
        assert self.endpoint == BRAVE_SEARCH_ENDPOINT
        assert params["q"] == "shared search contract"
        return self.payload

    async def post_json(self, *, json_body, headers, control):
        assert self.endpoint == TAVILY_SEARCH_ENDPOINT
        assert json_body["query"] == "shared search contract"
        return self.payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function", "module", "tool_id", "binding", "endpoint", "payload"),
    [
        (
            search_brave,
            "src.openship.tools.search.brave",
            "brave_web_search",
            "BRAVE_SEARCH_API_KEY",
            BRAVE_SEARCH_ENDPOINT,
            {
                "web": {
                    "results": [
                        {
                            "title": "First",
                            "url": "https://example.com/one",
                            "description": "first snippet",
                        },
                        {
                            "title": "Unsafe",
                            "url": "javascript:alert(1)",
                            "description": "discard",
                        },
                        {"title": "Partial", "url": "https://example.net/three"},
                    ]
                }
            },
        ),
        (
            search_tavily,
            "src.openship.tools.search.tavily",
            "tavily_web_search",
            "TAVILY_API_KEY",
            TAVILY_SEARCH_ENDPOINT,
            {
                "results": [
                    {"title": "First", "url": "https://example.com/one", "content": "first snippet"},
                    {"title": "Unsafe", "url": "javascript:alert(1)", "content": "discard"},
                    {"title": "Partial", "url": "https://example.net/three"},
                ],
                "usage": {"credits": 1},
                "answer": "provider-generated answer is ignored",
            },
        ),
    ],
)
async def test_search_tools_share_normalized_observation_contract(
    monkeypatch, function, module, tool_id, binding, endpoint, payload
):
    credentials = {binding: "provider-specific-secret"}
    resolved = []

    def resolve(name):
        resolved.append(name)
        return credentials.get(name)

    monkeypatch.setattr(
        f"{module}.BoundedSearchHttp",
        lambda actual_endpoint, **_kwargs: _FakeSearchHttp(
            actual_endpoint, payload=payload
        ),
    )
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            action="invoke_tool",
            target_id=tool_id,
        ),
        execution=ExecutionConfig(graph_kind="tool", graph_version=1),
        credential_resolver=resolve,
    )

    observation = await function(
        SearchArguments(query="shared search contract", max_results=3),
        context,
        InvocationControl(),
    )

    assert observation.tool.id == tool_id
    assert observation.query == "shared search contract"
    assert observation.returned_count == 2
    assert [item.rank for item in observation.items] == [1, 3]
    assert observation.items[0].snippet == "first snippet"
    assert observation.items[1].snippet == ""
    assert observation.truncated is True
    assert observation.provider_metadata == {}
    assert observation.observed_at.tzinfo is not None
    assert resolved == [binding]
    assert endpoint in {BRAVE_SEARCH_ENDPOINT, TAVILY_SEARCH_ENDPOINT}


@pytest.mark.parametrize(
    ("enabled_ids", "expected_enabled"),
    [
        (frozenset({"brave_web_search"}), "brave_web_search"),
        (frozenset({"tavily_web_search"}), "tavily_web_search"),
    ],
)
def test_search_tools_are_independently_enableable(enabled_ids, expected_enabled):
    summaries = {
        item.id: item
        for item in list_capabilities(
            enabled_ids=enabled_ids, credential_check=lambda _binding: True
        )
    }

    assert summaries[expected_enabled].enabled is True
    other_id = (
        "tavily_web_search"
        if expected_enabled == "brave_web_search"
        else "brave_web_search"
    )
    assert summaries[other_id].enabled is False
    assert summaries[other_id].unavailable_reason == "disabled"
