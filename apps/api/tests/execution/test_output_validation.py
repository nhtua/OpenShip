import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef
from src.openship.execution.projections import operation_summary, tool_attempt_summary
from src.openship.execution.publication import (
    ToolPublicationError,
    build_model_result,
    validate_search_observation,
)
from src.openship.tools.search.contracts import SearchObservation


def _ref():
    manifest = next(
        item for item in list_capability_manifests() if item.id == "brave_web_search"
    )
    return CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )


def _operation(ref, query="bounded output"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        capability_id=ref.id,
        capability_version=ref.version,
        manifest_digest=ref.manifest_digest,
        arguments_json=f'{{"max_results":10,"query":"{query}"}}',
    )


def _observation(ref, *, query="bounded output", item_count=1):
    return SearchObservation(
        tool=ref,
        query=query,
        observed_at=datetime.now(UTC),
        requested_count=10,
        returned_count=item_count,
        items=tuple(
            {
                "title": f"Result {index}",
                "url": f"https://example{index}.com/result",
                "snippet": "x" * 2048,
                "rank": index,
            }
            for index in range(1, item_count + 1)
        ),
        truncated=False,
        provider_metadata={},
    )


@pytest.mark.parametrize(
    "observation",
    [
        lambda ref: _observation(ref, query="another query"),
        lambda _ref: _observation(CapabilityRef(
            id="tavily_web_search",
            version="1.0.0",
            manifest_digest=next(
                item.manifest_digest
                for item in list_capability_manifests()
                if item.id == "tavily_web_search"
            ),
        )),
    ],
)
def test_observation_must_match_the_admitted_tool_and_query(observation):
    ref = _ref()

    with pytest.raises(ToolPublicationError) as caught:
        validate_search_observation(_operation(ref), observation(ref))

    assert caught.value.code == "invalid_tool_output"


def test_model_facing_excerpt_drops_lowest_ranked_tail_to_fit_limit():
    ref = _ref()
    operation = _operation(ref)
    artifact = ArtifactRef(
        id=uuid.uuid4(),
        sha256="a" * 64,
        size_bytes=64 * 1024,
        media_type="application/json",
    )

    result = build_model_result(operation, _observation(ref, item_count=10), artifact)

    assert result.truncated is True
    assert 1 <= len(result.items) < 10
    assert [item.rank for item in result.items] == list(range(1, len(result.items) + 1))
    assert len(result.model_dump_json().encode("utf-8")) <= 16 * 1024


def test_public_operation_exposes_only_bounded_search_query_and_omits_credentials():
    ref = _ref()
    operation = SimpleNamespace(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        logical_call_id="manual:0",
        capability_id=ref.id,
        capability_version=ref.version,
        manifest_digest=ref.manifest_digest,
        status="failed",
        error_code="provider_timeout",
        output_artifact_ids="[]",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        arguments_json='{"query":"private test query"}',
        credential_binding_ref="BRAVE_SEARCH_API_KEY",
    )
    attempt = SimpleNamespace(
        id=uuid.uuid4(),
        operation_id=operation.id,
        ordinal=1,
        fence=4,
        dispatch_started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        outcome="failed",
        error_code="provider_timeout",
        request_digest="b" * 64,
        response_digest="c" * 64,
    )

    operation_json = operation_summary(operation).model_dump_json()
    attempt_json = tool_attempt_summary(attempt).model_dump_json()

    assert '"query":"private test query"' in operation_json
    assert "max_results" not in operation_json
    assert "BRAVE_SEARCH_API_KEY" not in operation_json
    assert "request_digest" not in attempt_json
    assert "response_digest" not in attempt_json
