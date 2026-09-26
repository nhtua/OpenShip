from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field
from typing import Dict, Any
from .executor import execute_tool


class WorkflowState(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[int, str] = Field(default_factory=dict)


def compile_to_langgraph(workflow):
    steps = workflow["steps"]

    def build_node(step):
        def node(state: WorkflowState) -> WorkflowState:
            result = execute_tool(step["tool"], step["args"] or "")
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

    return graph.compile()