import re
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field
from typing import Dict, Any
from .executor import execute_tool


class WorkflowState(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[int, Any] = Field(default_factory=dict)


def resolve_template(template: str, state: WorkflowState) -> str:
    """Resolve {var}, {step_N}, and {step_N.field} placeholders from state."""
    def replacer(match):
        path = match.group(1)
        parts = path.split(".")
        base = parts[0]
        
        # Check if it's a step output reference (step_N or stepN)
        step_num = None
        if base.startswith("step_"):
            # Format: step_1
            try:
                step_num = int(base.split("_")[1])
            except (ValueError, IndexError):
                pass
        elif base.startswith("step"):
            # Format: step1 (no underscore)
            digits = re.sub(r"step", "", base, flags=re.IGNORECASE)
            if digits.isdigit():
                step_num = int(digits)
        
        if step_num is not None:
            if step_num not in state.outputs:
                return match.group(0)  # Return unchanged if step hasn't run
            value = state.outputs[step_num]
            
            # Navigate remaining path if any (e.g., .name)
            for part in parts[1:]:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return match.group(0)
            return str(value)
        
        # Regular input variable
        if path in state.inputs:
            return str(state.inputs[path])
        
        return match.group(0)  # Return unchanged if not found

    return re.sub(r"\{(\w+(?:\.\w+)*)\}", replacer, template)


def compile_to_langgraph(workflow, checkpointer=None):
    # Handle both object with "steps" field and plain list
    if isinstance(workflow, dict):
        steps = workflow.get("steps", [])
    else:
        steps = workflow

    # Add order numbers if missing
    for i, step in enumerate(steps):
        if "order" not in step:
            step["order"] = i + 1

    def build_node(step):
        def node(state: WorkflowState) -> WorkflowState:
            # Resolve template variables from state
            args = step["args"] or ""
            resolved_args = resolve_template(args, state)
            
            result = execute_tool(step["tool"], resolved_args)
            state.outputs[step["order"]] = result
            return state
        return node

    graph = StateGraph(WorkflowState)
    for step in steps:
        graph.add_node(f"step_{step['order']}", build_node(step))

    for i, step in enumerate(steps):
        if i == 0:
            graph.add_edge(START, f"step_{step['order']}")
        if i < len(steps) - 1:
            next_step = steps[i + 1]
            graph.add_edge(f"step_{step['order']}", f"step_{next_step['order']}")
        else:
            graph.add_edge(f"step_{step['order']}", END)

    return graph.compile(checkpointer=checkpointer)