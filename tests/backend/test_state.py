from openship.state import WorkflowState, create_state

def test_workflow_state_default():
    state = create_state()
    assert state["step"] == "idle"
    assert not state["done"]
    assert state["requirements"] == ""
    assert state["diagram"] == ""
    assert state["terraform"] == ""
    assert state["apply_output"] == ""
