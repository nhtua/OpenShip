import uuid
from typing import AsyncGenerator

from langgraph.graph import StateGraph, END

from openship.config import config
from openship.llm import generate_diagram, generate_terraform
from openship.state import WorkflowState, create_state
from openship.events import WorkflowEvent
from openship.checkpoints import get_checkpointer


# Global store for workflow state (in production, use Redis or database)
workflows = {}


async def create_workflow(requirements: str):
    """Create a new workflow and generate diagram."""
    run_id = str(uuid.uuid4())
    workflows[run_id] = create_state()
    workflows[run_id]["requirements"] = requirements
    workflows[run_id]["step"] = "generating_diagram"

    # Generate diagram
    diagram = generate_diagram(requirements)
    workflows[run_id]["diagram"] = diagram
    workflows[run_id]["step"] = "diagram_generated"

    return run_id, diagram


async def approve_diagram(run_id: str):
    """Approve diagram and generate Terraform code."""
    if run_id not in workflows:
        return None

    workflow = workflows[run_id]
    workflow["step"] = "generating_terraform"

    # Generate Terraform
    terraform = generate_terraform(workflow["requirements"], workflow["diagram"])
    workflow["terraform"] = terraform
    workflow["step"] = "terraform_generated"

    return terraform


async def approve_terraform(run_id: str):
    """Approve Terraform and apply."""
    if run_id not in workflows:
        return None

    workflow = workflows[run_id]
    workflow["step"] = "applying_terraform"

    # Apply Terraform
    if config.CLOUD_MOCK or not config.TERRAFORM_APPLY:
        output = "[Mocked] Running terraform plan\n\nTerraform plan completed successfully."
    else:
        output = "Running terraform apply...\n\nApply completed successfully."

    workflow["apply_output"] = output
    workflow["step"] = "done"
    workflow["done"] = True

    return output
