"""API endpoints for workflow management."""
from fastapi import APIRouter
from pydantic import BaseModel

from openship.workflow import create_workflow, approve_diagram, approve_terraform
from openship.config import config

router = APIRouter()


class WorkflowRequest(BaseModel):
    requirements: str = ""


class ApproveRequest(BaseModel):
    pass


@router.post("/workflows")
async def create_new_workflow(req: WorkflowRequest):
    """Create a new workflow and generate diagram."""
    run_id, diagram = await create_workflow(req.requirements)
    return {
        "run_id": run_id,
        "step": "diagram_generated",
        "diagram": diagram
    }


@router.post("/workflows/{run_id}/approve-diagram")
async def approve_diagram_endpoint(run_id: str, req: ApproveRequest):
    """Approve the diagram and generate Terraform code."""
    terraform = await approve_diagram(run_id)
    return {
        "run_id": run_id,
        "step": "terraform_generated",
        "terraform": terraform
    }


@router.post("/workflows/{run_id}/approve-terraform")
async def approve_terraform_endpoint(run_id: str, req: ApproveRequest):
    """Approve the Terraform code and apply."""
    output = await approve_terraform(run_id)
    return {
        "run_id": run_id,
        "step": "done",
        "apply_output": output
    }


@router.get("/config")
async def get_config():
    """Get the current configuration."""
    return {
        "cloud_provider": config.CLOUD_PROVIDER,
        "cloud_region": config.CLOUD_REGION,
        "cloud_mock": config.CLOUD_MOCK,
        "llm_provider": config.LLM_PROVIDER,
        "llm_model": config.LLM_MODEL,
        "llm_base_url": config.LLM_BASE_URL,
        "terraform_backend": config.TERRAFORM_BACKEND,
        "terraform_apply": config.TERRAFORM_APPLY,
    }
