from src.state import BuilderState

def test_load_workflow_node():
    from src.builder import load_workflow_node
    state = BuilderState(workflow_path="tests/fixtures/simple_workflow.md", workflow_content="")
    result = load_workflow_node(state)
    assert "Test Workflow" in result.workflow_content