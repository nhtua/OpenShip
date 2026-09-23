"""API endpoints for workflow management."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openship.workflow import (
    create_workflow,
    get_workflow,
    approve_diagram,
    approve_terraform,
)
from openship.config import config

router = APIRouter()


class WorkflowRequest(BaseModel):
    requirements: str = ""


class ApproveRequest(BaseModel):
    pass


@router.post("/workflows")
async def create_new_workflow(req: WorkflowRequest):
    """Create a new workflow run."""
    run_id, state = create_workflow(req.requirements)
    return {"run_id": run_id, "state": state}


@router.get("/workflows/{run_id}")
async def get_workflow_endpoint(run_id: str):
    """Get the current state of a workflow."""
    state = get_workflow(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"run_id": run_id, "state": state}


@router.post("/workflows/{run_id}/approve-diagram")
async def approve_diagram_endpoint(run_id: str, req: ApproveRequest):
    """Approve the diagram and continue the workflow."""
    state = approve_diagram(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"run_id": run_id, "state": state}


@router.post("/workflows/{run_id}/approve-terraform")
async def approve_terraform_endpoint(run_id: str, req: ApproveRequest):
    """Approve the terraform code and continue the workflow."""
    state = approve_terraform(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"run_id": run_id, "state": state}


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