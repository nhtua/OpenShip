"""One bounded live call to exactly one explicitly selected search provider."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime

import pytest

from src.openship.capabilities.contracts import (
    AuthorizationContext,
    ExecutionConfig,
    ToolInvocationContext,
)
from src.openship.config import settings
from src.openship.providers.control import InvocationControl
from src.openship.providers.credentials import (
    credential_binding_present,
    credential_value,
)
from src.openship.tools.search.brave import search_brave
from src.openship.tools.search.contracts import SearchArguments
from src.openship.tools.search.tavily import search_tavily
from tests.live.qualification import require_search_tool, selected_search_tool_id

TOOL_ID_ENV = "OPENSHIP_LIVE_SEARCH_TOOL_ID"
LIVE_QUERY = "OpenShip web search provider qualification"


@pytest.mark.asyncio
@pytest.mark.provider_live
async def test_selected_search_provider_returns_one_bounded_observation(record_property):
    tool_id = selected_search_tool_id(os.environ.get(TOOL_ID_ENV))
    if tool_id is None:
        pytest.skip(
            "opt-in: set OPENSHIP_LIVE_SEARCH_TOOL_ID to exactly one enabled search tool"
        )

    manifest = require_search_tool(
        tool_id,
        enabled_ids=settings.enabled_tool_ids,
        credential_check=credential_binding_present,
    )
    handler = {
        "brave_web_search": search_brave,
        "tavily_web_search": search_tavily,
    }[tool_id]
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            action="invoke_tool",
            target_id=tool_id,
        ),
        execution=ExecutionConfig(graph_kind="tool", graph_version=1),
        credential_resolver=credential_value,
    )
    started_at = datetime.now(UTC)
    observation = await handler(
        SearchArguments(query=LIVE_QUERY, max_results=1),
        context,
        InvocationControl(deadline=time.monotonic() + 60),
    )

    assert observation.tool.id == manifest.id
    assert observation.tool.version == manifest.version
    assert observation.tool.manifest_digest == manifest.manifest_digest
    assert observation.query == LIVE_QUERY
    assert observation.requested_count == 1
    assert observation.returned_count <= 1
    assert all(item.url.startswith(("http://", "https://")) for item in observation.items)
    assert observation.observed_at.tzinfo is not None

    record_property("tool_id", manifest.id)
    record_property("tool_version", manifest.version)
    record_property("tool_manifest_digest", manifest.manifest_digest)
    record_property("tool_implementation_digest", manifest.implementation_digest)
    record_property("query", LIVE_QUERY)
    record_property("observation_id", "direct-handler:no-persisted-operation")
    record_property("observed_at", observation.observed_at.isoformat())
    record_property("qualified_at", started_at.isoformat())
    record_property("returned_count", observation.returned_count)
