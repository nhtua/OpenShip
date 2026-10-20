def test_builder_state_fields():
    from src.state import BuilderState
    state = BuilderState(workflow_path="test.md", workflow_content="test")
    assert state.workflow_path == "test.md"
    assert state.approved == False
    assert state.user_feedback == ""

def test_executor_state_fields():
    from src.state import ExecutorState
    state = ExecutorState(plan="test plan", thread_id="test-thread")
    assert state.plan == "test plan"
    assert state.graph is None