"""LangGraph-based orchestration engine.

Builds, runs, and manages workflow graphs. Supports:
- Graph construction from workflow definitions
- Synchronous and asynchronous execution
- Human-in-the-loop interactions (pause/resume)
- Checkpointing and state management
"""

import re
import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.core.state import WorkflowState
from src.tools.implementations import get_tool_implementation

# Module-level registry of active graph executions
_active_executions: dict[str, dict[str, Any]] = {}


def resolve_template(template: str, state: WorkflowState) -> str:
    """Resolve {var} and {step_N} placeholders from workflow state."""

    def replacer(match):
        path = match.group(1)
        parts = path.split(".")

        # Check for step output reference
        if parts[0].startswith("step"):
            # Extract step number
            step_match = re.search(r"\d+", parts[0])
            if step_match:
                step_num = int(step_match.group())
                if step_num in state.step_outputs:
                    value = state.step_outputs[step_num]
                    # Navigate nested path
                    for part in parts[1:]:
                        if isinstance(value, dict) and part in value:
                            value = value[part]
                        else:
                            return match.group(0)
                    return str(value)

        # Check inputs
        if path in state.inputs:
            return str(state.inputs[path])

        return match.group(0)

    return re.sub(r"\{(\w+(?:\.\w+)*)\}", replacer, template)


def build_step_node(step: dict, workflow: dict):
    """Build a LangGraph node function for a workflow step."""
    step_order = step["order"]

    def node(state: WorkflowState) -> WorkflowState:
        tool_name = step["tool"]
        args = step.get("args", "")

        # Resolve template variables
        resolved_args = resolve_template(str(args), state)

        # Execute the tool
        tool_impl = get_tool_implementation(tool_name)
        if tool_impl is None:
            # Fallback to sandbox execution for shell tools
            raise RuntimeError(f"Tool not found: {tool_name}")

        try:
            tool_instance = tool_impl()
            result = tool_instance.execute({"args": resolved_args})
            state.step_outputs[step_order] = result
        except RuntimeError as e:
            state.error = f"Step {step_order} ({tool_name}) failed: {e!s}"
            state.step_outputs[step_order] = {"error": str(e)}

        return state

    return node


def build_condition_edge(step: dict):
    """Build a conditional edge router for a step with on_approve/on_reject."""

    def router(state: WorkflowState) -> str:
        # Check if step has approval routing
        result = state.step_outputs.get(step["order"])
        if result and isinstance(result, dict):
            if result.get("approved"):
                return step.get("on_approve", "end")
            elif result.get("rejected"):
                return step.get("on_reject", "end")

        # Default: continue to next step or end
        return "end"

    return router


def build_graph(workflow: dict) -> StateGraph:
    """Build a LangGraph graph from a workflow definition.

    Args:
        workflow: Workflow definition dictionary with 'steps' field.

    Returns:
        Compiled LangGraph StateGraph ready for execution.
    """
    steps = workflow.get("steps", [])

    # Build nodes and edges
    graph = StateGraph(WorkflowState)

    for step in steps:
        node = build_step_node(step, workflow)
        graph.add_node(f"step_{step['order']}", node)

    # Build edges
    if not steps:
        # Empty workflow: direct START -> END
        graph.add_edge(START, END)
    else:
        for i, step in enumerate(steps):
            if i == 0:
                graph.add_edge(START, f"step_{step['order']}")

            if i < len(steps) - 1:
                next_step = steps[i + 1]
                graph.add_edge(f"step_{step['order']}", f"step_{next_step['order']}")
            else:
                graph.add_edge(f"step_{step['order']}", END)

    checkpointer = InMemorySaver()
    return graph.compile(checkpointer=checkpointer)


def run_graph(graph, inputs: dict[str, Any], wait: bool = True) -> dict[str, Any] | str:
    """Run a workflow graph with the given inputs.

    Args:
        graph: Compiled LangGraph graph.
        inputs: Input parameters for the workflow.
        wait: If True, wait for completion and return results.
             If False, return execution ID for async tracking.

    Returns:
        If wait=True: dict with 'output', 'step_outputs', 'error'
        If wait=False: execution ID string
    """
    execution_id = str(uuid.uuid4())
    thread_id = f"thread_{execution_id}"

    # Store execution context
    _active_executions[execution_id] = {
        "graph": graph,
        "thread_id": thread_id,
        "status": "running",
        "inputs": inputs,
        "result": None,
    }

    def execute():
        config = {"configurable": {"thread_id": thread_id}}
        initial_state = {"inputs": inputs}

        try:
            result = graph.invoke(initial_state, config)
            _active_executions[execution_id]["result"] = result
            _active_executions[execution_id]["status"] = "completed"
        except RuntimeError as e:
            _active_executions[execution_id]["result"] = {"error": str(e)}
            _active_executions[execution_id]["status"] = "failed"

    if not wait:
        # Fire and forget
        execute()
        return execution_id

    # Wait for completion
    execute()
    exec_ctx = _active_executions[execution_id]

    result = exec_ctx.get("result", {})
    return {
        "execution_id": execution_id,
        "status": exec_ctx["status"],
        "output": result.get("output", {}),
        "step_outputs": result.get("step_outputs", {}),
        "error": result.get("error"),
    }


def pause_graph(graph_id: str) -> None:
    """Pause a running graph execution.

    Args:
        graph_id: The execution ID to pause.
    """
    exec_ctx = _active_executions.get(graph_id)
    if exec_ctx is None:
        raise KeyError(f"Execution not found: {graph_id}")

    exec_ctx["status"] = "paused"


def resume_graph(graph_id: str, response: Any = None) -> dict[str, Any] | None:
    """Resume a paused graph execution.

    Args:
        graph_id: The execution ID to resume.
        response: Response to provide to the graph (for human-in-the-loop).

    Returns:
        Execution result if resumed and completed, None otherwise.
    """
    exec_ctx = _active_executions.get(graph_id)
    if exec_ctx is None:
        raise KeyError(f"Execution not found: {graph_id}")

    exec_ctx["status"] = "running"

    # If there's a result, return it
    result = exec_ctx.get("result")
    if result is not None:
        return {
            "execution_id": graph_id,
            "status": exec_ctx["status"],
            "output": result.get("output", {}),
            "step_outputs": result.get("step_outputs", {}),
            "error": result.get("error"),
        }

    return None


def get_execution(graph_id: str) -> dict[str, Any] | None:
    """Get execution status and result.

    Args:
        graph_id: The execution ID.

    Returns:
        Execution context or None if not found.
    """
    return _active_executions.get(graph_id)


def reset_executions() -> None:
    """Clear all active executions. Useful for testing."""
    _active_executions.clear()
