from src.state import BuilderState
from unittest.mock import patch, MagicMock

def test_load_workflow_node():
    from src.builder import load_workflow_node
    state = BuilderState(workflow_path="tests/fixtures/simple_workflow.md", workflow_content="")
    result = load_workflow_node(state)
    assert "Test Workflow" in result.workflow_content

def test_generate_plan_node():
    from src.builder import generate_plan_node
    state = BuilderState(workflow_path="test.md", workflow_content="test content", user_feedback="")
    with patch("src.builder.get_llm_client") as mock_get_llm:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = '{"steps": [{"order": 1, "tool": "shell.echo", "args": "hello"}]}'
        mock_get_llm.return_value = mock_llm
        result = generate_plan_node(state)
    assert result.llm_plan == '{"steps": [{"order": 1, "tool": "shell.echo", "args": "hello"}]}'

def test_show_plan_node():
    from src.builder import show_plan_node
    state = BuilderState(workflow_path="test.md", workflow_content="test", llm_plan="test plan")
    result = show_plan_node(state)
    assert result.approved == False

def test_wait_for_approval_node_approved():
    from src.builder import wait_for_approval_node
    from langgraph.types import interrupt
    state = BuilderState(workflow_path="test.md", workflow_content="test", llm_plan="plan")
    with patch("langgraph.types.interrupt", return_value="yes"):
        result = wait_for_approval_node(state)
    assert result.approved == True

def test_wait_for_approval_node_rejected():
    from src.builder import wait_for_approval_node
    state = BuilderState(workflow_path="test.md", workflow_content="test", llm_plan="plan")
    with patch("langgraph.types.interrupt", return_value="change tool"):
        result = wait_for_approval_node(state)
    assert result.approved == False
    assert result.user_feedback == "change tool"

def test_compile_builder_workflow():
    from src.builder import compile_builder_workflow
    builder = compile_builder_workflow()
    assert builder is not None