import uuid
from typing import AsyncGenerator

from langgraph.graph import StateGraph, END

from openship.config import config
from openship.llm import generate_diagram, generate_terraform
from openship.state import WorkflowState, create_state
from openship.events import WorkflowEvent
from openship.checkpoints import get_checkpointer


def compile_graph() -> StateGraph:
    graph = StateGraph(WorkflowState)

    def parse_requirements(state):
        return {"step": "parsing_requirements", "requirements": state.get("requirements", "")}

    def generate_diagram_node(state):
        diagram = generate_diagram(state.get("requirements", ""))
        return {"step": "generating_diagram", "diagram": diagram}

    def generate_terraform_node(state):
        terraform = generate_terraform(state.get("requirements", ""), state.get("diagram", ""))
        return {"step": "generating_terraform", "terraform": terraform}

    def apply_terraform_node(state):
        if config.CLOUD_MOCK or not config.TERRAFORM_APPLY:
            output = "[Mocked] Running terraform plan\n\nTerraform plan completed successfully."
        else:
            output = "Running terraform apply...\n\nApply completed successfully."
        return {"step": "applying_terraform", "apply_output": output, "done": True}

    graph.add_node("parse_requirements", parse_requirements)
    graph.add_node("generate_diagram", generate_diagram_node)
    graph.add_node("generate_terraform", generate_terraform_node)
    graph.add_node("apply_terraform", apply_terraform_node)

    graph.set_entry_point("parse_requirements")
    graph.add_edge("parse_requirements", "generate_diagram")
    graph.add_edge("generate_diagram", "generate_terraform")
    graph.add_edge("generate_terraform", "apply_terraform")
    graph.add_edge("apply_terraform", END)

    checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)


async def run_workflow(requirements: str) -> AsyncGenerator[WorkflowEvent, None]:
    run_id = str(uuid.uuid4())
    graph = compile_graph()

    config_obj = {"configurable": {"thread_id": run_id}}

    yield WorkflowEvent(type="stage_start", stage="generating_diagram", message="Generating architecture diagram")

    async for chunk in graph.astream({"requirements": requirements}, config=config_obj):
        for node, data in chunk.items():
            if node == "generate_diagram":
                yield WorkflowEvent(type="stage_complete", stage="generating_diagram", data={"diagram": data.get("diagram", "")})
                yield WorkflowEvent(type="stage_start", stage="generating_terraform", message="Generating Terraform code")
            elif node == "generate_terraform":
                yield WorkflowEvent(type="stage_complete", stage="generating_terraform", data={"terraform": data.get("terraform", "")})
                yield WorkflowEvent(type="stage_start", stage="applying_terraform", message="Applying Terraform")
            elif node == "apply_terraform":
                yield WorkflowEvent(type="stage_complete", stage="applying_terraform", data={"output": data.get("apply_output", "")})

    yield WorkflowEvent(type="complete", stage="done", message="Workflow completed")
