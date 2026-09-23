import os
import subprocess
import tempfile
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

    terraform_code = workflow.get("terraform", "")

    try:
        # Create temporary directory for terraform
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write terraform code to file
            tf_file = os.path.join(tmpdir, "main.tf")
            with open(tf_file, "w") as f:
                f.write(terraform_code)

            # Run terraform init
            init_result = subprocess.run(
                ["terraform", "init", "-backend=false"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=60
            )

            # Run terraform plan
            plan_result = subprocess.run(
                ["terraform", "plan", "-no-color"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=60
            )

            if config.CLOUD_MOCK or not config.TERRAFORM_APPLY:
                # Just show plan
                output = init_result.stdout + "\n"
                output += plan_result.stdout
                if plan_result.returncode != 0:
                    output += "\n" + plan_result.stderr
            else:
                # Run terraform apply
                apply_result = subprocess.run(
                    ["terraform", "apply", "-auto-approve", "-no-color"],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                output = init_result.stdout + "\n"
                output += apply_result.stdout
                if apply_result.returncode != 0:
                    output += "\n" + apply_result.stderr

    except FileNotFoundError:
        output = "Error: terraform CLI not found. Please install Terraform."
    except subprocess.TimeoutExpired as e:
        output = f"Error: Terraform command timed out: {e.cmd}"
    except Exception as e:
        output = f"Error running Terraform: {str(e)}"

    workflow["apply_output"] = output
    workflow["step"] = "done"
    workflow["done"] = True

    return output
