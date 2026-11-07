"""Fixed-journey routing must not re-enter completed phases."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.openship.runs import schemas
from src.openship.runs.analysis_graph import (
    AnalysisJourney,
    build_analysis_graph,
    encode_journey,
)


def test_resume_command_has_only_explicit_modes_and_uuid_identity():
    request = getattr(schemas, "ResumeAnalysisRequest", None)
    assert request is not None, "explicit analysis resume command is missing"
    assert request(mode="last_step", client_request_id=uuid4()).mode == "last_step"
    for payload in (
        {"mode": "automatic", "client_request_id": str(uuid4())},
        {"mode": "last_step", "client_request_id": "not-a-uuid"},
        {"mode": "last_step", "client_request_id": str(uuid4()), "source": "code"},
    ):
        with pytest.raises(ValidationError):
            request(**payload)


def test_output_resume_never_prepares_or_dispatches():
    run_id = uuid4()
    ref = {
        "id": uuid4(),
        "sha256": "a" * 64,
        "size_bytes": 1,
        "media_type": "text/plain",
    }
    journey = AnalysisJourney(
        root_run_id=str(uuid4()),
        initial_refs=(ref,),
        completed_phases=("prepare", "execute"),
    )
    graph = build_analysis_graph(
        invocation_factory=lambda _state: (SimpleNamespace(run_id=run_id), None, None),
        gateway_factory=lambda: pytest.fail("completed execution must not dispatch"),
    )
    result = graph.invoke(
        {
            "run_id": str(run_id),
            "journey_json": encode_journey(journey),
            "tool_result": {
                "status": "failed",
                "operation_id": str(uuid4()),
                "error": {"code": "invalid_tool_output", "message": "Refused"},
            },
        }
    )
    assert result["tool_result"]["status"] == "failed"


def test_completed_journey_skips_every_completed_phase():
    journey = AnalysisJourney(
        root_run_id=str(uuid4()), completed_phases=("prepare", "execute", "use_outputs"),
    )
    graph = build_analysis_graph(invocation_factory=lambda _state: pytest.fail("completed phases must be skipped"))
    result = graph.invoke({"run_id": str(uuid4()), "journey_json": encode_journey(journey), "analysis_summary": {"report.txt": "Saved report"}})
    assert result["analysis_summary"] == {"report.txt": "Saved report"}
