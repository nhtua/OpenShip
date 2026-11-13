"""The model-visible copy of a tool result fits its budget, durably.

The break these pin: a workspace read whose committed row filled its own page
left no room for the envelope `_result_message` adds, so `dispatch` raised
`output_limit_exceeded` instead of leaving a step outcome - the run ended with
no diagnosis while the durable result stayed committed and exact.
"""

import json
import uuid

import pytest

from src.openship.capabilities.contracts import PublishedToolResult
from src.openship.providers.errors import ProviderError
from src.openship.runs.agent_graph import _result_message

BUDGET = 16 * 1024


def _canonical(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_at_the_ceiling() -> tuple[str, PublishedToolResult]:
    """The dev-stack QA row: the committed `result_json` was exactly one page.

    `max_read_bytes` and `max_model_result_bytes` are the same 16 KiB
    (`capabilities/catalog.py:522-524`), so a row that legitimately fills the
    page leaves no room at all for the envelope the model message adds.
    """
    fields = {
        "artifact_id": str(uuid.uuid4()),
        "media_type": "text/plain",
        "next_offset": BUDGET,
        "offset": 0,
        "sha256": "a" * 64,
        "size_bytes": 49916,
        "version_id": str(uuid.uuid4()),
        "workspace_id": str(uuid.uuid4()),
    }
    page = "heathrow monthly means | " * 4000
    room = BUDGET - len(_canonical({"content": "", **fields}).encode("utf-8"))
    content = page[:room]
    result = {"content": content, **fields}
    assert len(_canonical(result).encode("utf-8")) == BUDGET, (
        "the row the gateway committed filled its page - the ceiling the tool allows"
    )
    return content, PublishedToolResult(
        operation_id=uuid.uuid4(),
        status="succeeded",
        result=result,
    )


def test_a_result_at_the_tool_ceiling_is_rendered_within_the_budget():
    """A full page stays readable: the model gets a named, bounded excerpt."""
    content, published = _read_at_the_ceiling()
    rendered = _result_message(published, BUDGET)
    assert len(rendered.encode("utf-8")) <= BUDGET, (
        "the message the model gets fits the budget it was handed"
    )
    decoded = json.loads(rendered)
    assert decoded["status"] == "succeeded", "the outcome still reads"
    assert decoded["result"]["sha256"] == "a" * 64
    assert decoded["result"]["next_offset"] == BUDGET, (
        "and the page pointer the model needs to continue survives"
    )
    excerpt = decoded["result"]["content"]
    assert excerpt.startswith(content[:100]), "the model still sees the real start"
    assert "OpenShip truncation" in excerpt, "the model is told the copy is partial"
    assert f"of {len(content)} bytes" in excerpt, "and how large the durable value is"
    assert f"next_offset={BUDGET}" in excerpt, "and where the next page starts"
    assert content in published.model_dump_json(), (
        "bounding the rendering never touches the durable result"
    )


def test_a_result_under_the_budget_renders_exactly():
    """Ordinary results are unaffected: the model sees the whole contract."""
    published = PublishedToolResult(
        operation_id=uuid.uuid4(),
        status="failed",
        result={"reason": "unsupported file"},
    )
    rendered = _result_message(published, BUDGET)
    assert json.loads(rendered) == json.loads(published.model_dump_json())


def test_an_envelope_too_large_to_shorten_still_refuses():
    """Bounding the copy cannot hide a result with nothing left to bound."""
    published = PublishedToolResult(
        operation_id=uuid.uuid4(),
        status="succeeded",
        result={f"key{i}": i for i in range(9000)},
    )
    with pytest.raises(ProviderError) as over:
        _result_message(published, 512)
    assert over.value.code == "output_limit_exceeded"
