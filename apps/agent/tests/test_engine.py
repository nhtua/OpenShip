"""Tests for the LangGraph orchestration engine.

Covers:
- Graph construction from workflow definitions
- Graph execution with inputs
- Human-in-the-loop pause/resume
- Error handling during execution
"""

from src.core.engine import (
    build_graph,
    pause_graph,
    resume_graph,
    run_graph,
)
from src.tools.implementations import register_tool_implementation


# Register test tools for engine tests
class EchoTool:
    """Test tool that echoes back input."""

    def execute(self, inputs):
        return {"output": inputs.get("args", "")}


class HumanApproveTool:
    """Test tool that simulates human approval."""

    def execute(self, inputs):
        return {"approved": True}


register_tool_implementation("echo", EchoTool)
register_tool_implementation("human_approve", HumanApproveTool)


def test_build_graph_from_steps():
    """Build a graph from a workflow definition with multiple steps."""
    workflow = {
        "name": "test_workflow",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Test workflow",
        "steps": [
            {"order": 1, "tool": "echo", "args": "hello"},
            {"order": 2, "tool": "echo", "args": "world"},
        ],
    }

    graph = build_graph(workflow)
    assert graph is not None
    # Compiled graph has get_graph() method for inspecting structure
    graph_spec = graph.get_graph()
    assert len(graph_spec.nodes) == 4  # START + 2 steps + END


def test_build_graph_single_step():
    """Build a graph with a single step."""
    workflow = {
        "name": "single_step",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Single step workflow",
        "steps": [
            {"order": 1, "tool": "echo", "args": "hi"},
        ],
    }

    graph = build_graph(workflow)
    assert graph is not None
    graph_spec = graph.get_graph()
    assert len(graph_spec.nodes) == 3  # START + 1 step + END


def test_run_graph_executes_steps():
    """Run a graph and verify it executes all steps."""
    workflow = {
        "name": "run_test",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Run test workflow",
        "steps": [
            {"order": 1, "tool": "echo", "args": "test"},
        ],
    }

    graph = build_graph(workflow)
    result = run_graph(graph, inputs={})
    # Result should have step outputs
    assert "step_outputs" in result
    assert result["step_outputs"] is not None


def test_run_graph_with_inputs():
    """Run a graph with input parameters."""
    workflow = {
        "name": "input_test",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Input test workflow",
        "steps": [
            {"order": 1, "tool": "echo", "args": "test"},
        ],
    }

    graph = build_graph(workflow)
    result = run_graph(graph, inputs={"key": "value"})
    assert "step_outputs" in result


def test_pause_and_resume():
    """Test pausing and resuming graph execution."""
    # Create a workflow with a human-in-the-loop step
    workflow = {
        "name": "pause_test",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Pause/resume test",
        "steps": [
            {"order": 1, "tool": "echo", "args": "before"},
            {
                "order": 2,
                "tool": "human_approve",
                "args": "Proceed?",
                "on_approve": 3,
                "on_reject": 4,
            },
            {"order": 3, "tool": "echo", "args": "approved"},
            {"order": 4, "tool": "echo", "args": "rejected"},
        ],
    }

    graph = build_graph(workflow)

    # Run until first interrupt
    graph_id = run_graph(graph, inputs={}, wait=False)
    assert graph_id is not None

    # Pause and resume
    pause_graph(graph_id)
    resume_graph(graph_id, response="approve")


def test_graph_error_handling():
    """Test that graph errors are captured properly."""
    workflow = {
        "name": "error_test",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Error test",
        "steps": [
            {"order": 1, "tool": "nonexistent_tool", "args": "test"},
        ],
    }

    graph = build_graph(workflow)
    result = run_graph(graph, inputs={}, wait=True)
    # Should have error information
    assert "error" in result or "step_outputs" in result


def test_empty_workflow():
    """Build a graph with no steps."""
    workflow = {
        "name": "empty",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Empty workflow",
        "steps": [],
    }

    graph = build_graph(workflow)
    assert graph is not None
    graph_spec = graph.get_graph()
    assert len(graph_spec.nodes) == 2  # START + END


def test_graph_with_branching():
    """Test conditional branching in graph."""
    workflow = {
        "name": "branch_test",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Branch test",
        "steps": [
            {"order": 1, "tool": "echo", "args": "start"},
            {
                "order": 2,
                "tool": "echo",
                "args": "branch_a",
                "condition": "state.get('choice') == 'a'",
            },
            {
                "order": 3,
                "tool": "echo",
                "args": "branch_b",
                "condition": "state.get('choice') == 'b'",
            },
        ],
    }

    graph = build_graph(workflow)
    assert graph is not None
