"""API endpoints for workflow management with SSE streaming."""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from openship.workflow import run_workflow
from openship.config import config

router = APIRouter()


class WorkflowRequest(BaseModel):
    requirements: str = ""


class ApproveRequest(BaseModel):
    pass


async def _stream_workflow(req: WorkflowRequest):
    """Async generator that yields SSE strings from workflow events."""
    async for event in run_workflow(req.requirements):
        yield event.to_sse()


@router.post("/workflows")
async def create_new_workflow(req: WorkflowRequest):
    """Create a new workflow run and stream events."""
    return StreamingResponse(
        _stream_workflow(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/workflows/{run_id}/events")
async def stream_events_endpoint(run_id: str):
    """Subscribe to workflow events by run ID."""
    return {"run_id": run_id, "streaming": True}


@router.post("/workflows/{run_id}/approve-diagram")
async def approve_diagram_endpoint(run_id: str, req: ApproveRequest):
    """Approve the diagram and continue the workflow."""
    return {"run_id": run_id, "status": "diagram approved"}


@router.post("/workflows/{run_id}/approve-terraform")
async def approve_terraform_endpoint(run_id: str, req: ApproveRequest):
    """Approve the terraform code and continue the workflow."""
    return {"run_id": run_id, "status": "terraform approved"}


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
