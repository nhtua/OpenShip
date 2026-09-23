from openship.events import WorkflowEvent

def test_event_creation():
    event = WorkflowEvent(
        type="stage_start",
        stage="generating_diagram",
        message="Starting diagram generation"
    )
    assert event.type == "stage_start"
    assert event.stage == "generating_diagram"

def test_event_to_sse():
    event = WorkflowEvent(type="stage_complete", stage="diagram", data={"diagram": "test"})
    sse = event.to_sse()
    assert "event: stage_complete" in sse
    assert "data:" in sse
