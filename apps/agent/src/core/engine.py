"""LangGraph-based orchestration engine.

Builds, runs, and manages workflow graphs. Supports:
- Graph construction from workflow definitions
- Synchronous and asynchronous execution
- Human-in-the-loop interactions (pause/resume) via LangGraph's interrupt mechanism
- Checkpointing and state management
"""

import re
import threading
import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

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
    """Build a LangGraph node function for a workflow step.

    If the step has on_approve/on_reject routing, the node will use
    LangGraph's interrupt mechanism to pause for a human decision.
    """
    step_order = step["order"]
    has_routing = "on_approve" in step or "on_reject" in step

    def node(state: WorkflowState) -> WorkflowState:
        tool_name = step["tool"]
        args = step.get("args", "")

        # If this step has routing logic, pause for human decision via interrupt
        if has_routing:
            decision = interrupt(
                f"Step {step_order} requires a decision. Approve or reject?",
                response_schema={"type": "string", "enum": ["approve", "reject"]},
            )

            # Store the decision in step outputs for the router to use
            if decision == "approve":
                state.step_outputs[step_order] = {"approved": True, "rejected": False}
            else:
                state.step_outputs[step_order] = {"approved": False, "rejected": True}

            return state

        # Resolve template variables
        resolved_args = resolve_template(str(args), state)

        # Execute the tool
        tool_impl = get_tool_implementation(tool_name)
        if tool_impl is None:
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
    """Build a conditional edge router for a step with on_approve/on_reject.

    The router examines the step's output for approved/rejected flags
    and routes to the appropriate next step or END.
    """

    def router(state: WorkflowState) -> str:
        result = state.step_outputs.get(step["order"])
        if result and isinstance(result, dict):
            if result.get("approved"):
                target = step.get("on_approve")
                if target is not None:
                    return f"step_{target}"
            elif result.get("rejected"):
                target = step.get("on_reject")
                if target is not None:
                    return f"step_{target}"

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

    # Collect step orders that are targets of conditional edges
    conditional_targets = set()
    for step in steps:
        if step.get("on_approve"):
            conditional_targets.add(step["on_approve"])
        if step.get("on_reject"):
            conditional_targets.add(step["on_reject"])

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
                # Check if this step has conditional routing
                if "on_approve" in step or "on_reject" in step:
                    # Use conditional edge with routing logic
                    router = build_condition_edge(step)

                    # Build path map from routing config
                    path_map = {"end": END}
                    if step.get("on_approve"):
                        path_map[f"step_{step['on_approve']}"] = f"step_{step['on_approve']}"
                    if step.get("on_reject"):
                        path_map[f"step_{step['on_reject']}"] = f"step_{step['on_reject']}"

                    graph.add_conditional_edges(
                        f"step_{step['order']}",
                        router,
                        path_map,
                    )
                else:
                    # Linear edge to next step, but only if this step is not
                    # a target of conditional routing (those are branch endpoints)
                    next_step = steps[i + 1]
                    if step["order"] not in conditional_targets:
                        graph.add_edge(f"step_{step['order']}", f"step_{next_step['order']}")
            else:
                # Last step: edge to END, but only if this step is not a
                # conditional target (those end at the branch itself)
                if step["order"] not in conditional_targets:
                    graph.add_edge(f"step_{step['order']}", END)

    checkpointer = InMemorySaver()
    return graph.compile(checkpointer=checkpointer)


def _is_interrupted(result: Any) -> bool:
    """Check if graph result contains an interrupt."""
    return isinstance(result, dict) and "__interrupt__" in result


def run_graph(graph, inputs: dict[str, Any], wait: bool = True) -> dict[str, Any] | str:
    """Run a workflow graph with the given inputs.

    Args:
        graph: Compiled LangGraph graph.
        inputs: Input parameters for the workflow.
        wait: If True, wait for completion and return results.
             If False, run in background thread and return execution ID.

    Returns:
        If wait=True: dict with 'output', 'step_outputs', 'error', 'status'
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

    if not wait:
        # Fire and forget - run in a background thread
        t = threading.Thread(
            target=_execute_graph,
            args=(execution_id, graph, thread_id, inputs),
            daemon=True,
        )
        t.start()
        return execution_id

    # Wait for completion
    _execute_graph(execution_id, graph, thread_id, inputs)
    return _get_execution_result(execution_id)


def _execute_graph(execution_id: str, graph, thread_id: str, inputs: dict[str, Any]):
    """Execute the graph and update execution context."""
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {"inputs": inputs}

    try:
        result = graph.invoke(initial_state, config)

        # Check if graph was interrupted
        if _is_interrupted(result):
            _active_executions[execution_id]["status"] = "paused"
            _active_executions[execution_id]["result"] = result
        else:
            _active_executions[execution_id]["result"] = result
            _active_executions[execution_id]["status"] = "completed"
    except RuntimeError as e:
        _active_executions[execution_id]["result"] = {"error": str(e)}
        _active_executions[execution_id]["status"] = "failed"


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

    # If not paused, just return current result if available
    if exec_ctx["status"] != "paused":
        if exec_ctx["status"] == "completed":
            return _get_execution_result(graph_id)
        return None

    exec_ctx["status"] = "running"

    # Resume the graph with the provided response
    try:
        graph = exec_ctx["graph"]
        thread_id = exec_ctx["thread_id"]
        config = {"configurable": {"thread_id": thread_id}}

        result = graph.invoke(Command(resume=response), config)

        if _is_interrupted(result):
            # Still paused (multiple interrupts)
            exec_ctx["status"] = "paused"
            exec_ctx["result"] = result
        else:
            exec_ctx["result"] = result
            exec_ctx["status"] = "completed"
            return _get_execution_result(graph_id)
    except RuntimeError as e:
        exec_ctx["result"] = {"error": str(e)}
        exec_ctx["status"] = "failed"

    return None


def _get_execution_result(execution_id: str) -> dict[str, Any]:
    """Get formatted execution result."""
    exec_ctx = _active_executions[execution_id]
    result = exec_ctx.get("result", {})

    if isinstance(result, dict) and "__interrupt__" in result:
        # Return interrupt info
        return {
            "execution_id": execution_id,
            "status": "paused",
            "interrupt": result["__interrupt__"],
        }

    return {
        "execution_id": execution_id,
        "status": exec_ctx["status"],
        "output": result.get("output", {}),
        "step_outputs": result.get("step_outputs", {}),
        "error": result.get("error"),
    }


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