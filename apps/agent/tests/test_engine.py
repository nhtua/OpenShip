"""Tests for the LangGraph orchestration engine.

Covers:
- Graph construction from workflow definitions
- Graph execution with inputs
- Human-in-the-loop pause/resume via LangGraph's interrupt mechanism
- Conditional routing with build_condition_edge
- Async execution with wait=False
- Error handling during execution
"""

import time

from src.core.engine import (
    build_condition_edge,
    build_graph,
    get_execution,
    reset_executions,
    resume_graph,
    run_graph,
)
from src.core.state import WorkflowState
from src.tools.implementations import register_tool_implementation


# Register test tools for engine tests
class EchoTool:
    """Test tool that echoes back input."""

    def execute(self, inputs):
        return {"output": inputs.get("args", "")}


class FailTool:
    """Test tool that always fails."""

    def execute(self, inputs):
        raise RuntimeError("Intentional failure")


register_tool_implementation("echo", EchoTool)
register_tool_implementation("fail", FailTool)


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
    assert "step_outputs" in result
    assert result["step_outputs"] is not None
    assert result["status"] == "completed"


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
    assert result["status"] == "completed"


def test_pause_and_resume():
    """Test pausing and resuming graph execution with interrupt mechanism."""
    reset_executions()

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
                "tool": "echo",
                "args": "decision needed",
                "on_approve": 3,
                "on_reject": 4,
            },
            {"order": 3, "tool": "echo", "args": "approved"},
            {"order": 4, "tool": "echo", "args": "rejected"},
        ],
    }

    graph = build_graph(workflow)

    # Run the graph - it should pause at step 2 due to interrupt
    graph_id = run_graph(graph, inputs={}, wait=False)
    assert graph_id is not None

    # Wait briefly for the graph to reach the interrupt point
    time.sleep(0.1)

    # Check that the execution is paused
    exec_ctx = get_execution(graph_id)
    assert exec_ctx is not None
    assert exec_ctx["status"] == "paused"

    # Resume with "approve" decision
    result = resume_graph(graph_id, response="approve")

    # Should have completed
    assert result is not None
    assert result["status"] == "completed"

    # Verify step 3 (approved path) was executed
    assert 3 in result["step_outputs"]


def test_pause_and_resume_reject():
    """Test pausing and resuming with reject decision."""
    reset_executions()

    workflow = {
        "name": "reject_test",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Reject test",
        "steps": [
            {"order": 1, "tool": "echo", "args": "before"},
            {
                "order": 2,
                "tool": "echo",
                "args": "decision needed",
                "on_approve": 3,
                "on_reject": 4,
            },
            {"order": 3, "tool": "echo", "args": "approved"},
            {"order": 4, "tool": "echo", "args": "rejected"},
        ],
    }

    graph = build_graph(workflow)

    graph_id = run_graph(graph, inputs={}, wait=False)
    time.sleep(0.1)

    # Resume with "reject" decision
    result = resume_graph(graph_id, response="reject")

    assert result is not None
    assert result["status"] == "completed"

    # Verify step 4 (rejected path) was executed
    assert 4 in result["step_outputs"]


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
    assert result["status"] == "failed"
    assert "error" in result
    assert result["error"] is not None


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
    """Test conditional branching in graph using build_condition_edge."""
    reset_executions()

    # Test approve path
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
                "args": "decision",
                "on_approve": 3,
                "on_reject": 4,
            },
            {"order": 3, "tool": "echo", "args": "approved_path"},
            {"order": 4, "tool": "echo", "args": "rejected_path"},
        ],
    }

    graph = build_graph(workflow)

    # Run and approve
    graph_id = run_graph(graph, inputs={}, wait=False)
    time.sleep(0.1)
    result = resume_graph(graph_id, response="approve")

    assert result is not None
    assert result["status"] == "completed"
    # Verify approve path was taken
    assert 3 in result["step_outputs"]
    assert 4 not in result["step_outputs"]


def test_build_condition_edge():
    """Test that build_condition_edge produces a working router function."""
    step = {"order": 2, "on_approve": 3, "on_reject": 4}
    router = build_condition_edge(step)

    # Test approve routing
    state_approved = WorkflowState(
        step_outputs={2: {"approved": True, "rejected": False}}
    )
    assert router(state_approved) == "step_3"

    # Test reject routing
    state_rejected = WorkflowState(
        step_outputs={2: {"approved": False, "rejected": True}}
    )
    assert router(state_rejected) == "step_4"

    # Test default routing
    state_default = WorkflowState(step_outputs={2: {}})
    assert router(state_default) == "end"


def test_async_execution():
    """Test that wait=False actually runs in background."""
    reset_executions()

    workflow = {
        "name": "async_test",
        "version": "1.0.0",
        "origin": "custom",
        "definition": "Async test",
        "steps": [
            {"order": 1, "tool": "echo", "args": "async"},
        ],
    }

    graph = build_graph(workflow)

    # Start async execution
    graph_id = run_graph(graph, inputs={}, wait=False)
    assert graph_id is not None

    # Wait for completion
    time.sleep(0.1)

    # Check status
    exec_ctx = get_execution(graph_id)
    assert exec_ctx is not None
    assert exec_ctx["status"] == "completed"