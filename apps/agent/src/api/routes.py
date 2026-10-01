"""API route definitions for the OpenShip backend service.

Exposes the agent orchestration engine, tool registry, workflow registry,
and sandbox management through a REST API.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from src.core.engine import build_graph, get_execution, resume_graph, run_graph
from src.core.sandbox import create_sandbox
from src.registry.tools import (
    find_tools,
    list_tools,
    register_tool,
)
from src.registry.workflows import (
    find_workflows,
    get_workflow,
    list_workflows,
    register_workflow,
)

logger = logging.getLogger(__name__)

# Create router with API prefix
router = APIRouter(prefix="/api")


# Pydantic models for request/response validation
class WorkflowRunRequest(BaseModel):
    """Request model for running a workflow."""
    name: str
    inputs: dict[str, Any] = {}


class ResumeWorkflowRequest(BaseModel):
    """Request model for resuming a paused workflow."""
    response: Any = None


class BuildWorkflowRequest(BaseModel):
    """Request model for building a workflow."""
    description: str
    feedback: str = ""


class RegisterWorkflowRequest(BaseModel):
    """Request model for registering a workflow."""
    name: str
    version: str
    origin: str = "custom"
    description: str = ""
    definition: str
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    tags: list[str] = []
    required_tools: list[str] = []
    required_connectors: list[str] = []


class RegisterToolRequest(BaseModel):
    """Request model for registering a tool."""
    name: str
    version: str
    origin: str = "custom"
    description: str = ""
    category: str = ""
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    tags: list[str] = []
    permissions: list[str] = []


class ToolDefinition(BaseModel):
    """Tool definition model."""
    name: str
    version: str
    origin: str = "builtin"
    description: str = ""
    category: str = ""
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    tags: list[str] = []
    permissions: list[str] = []


# Health check
@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        dict: Status information.
    """
    return {"status": "ok"}


# Workflow endpoints
@router.get("/workflows")
async def list_workflows_endpoint(origin: str | None = Query(None)) -> list[dict]:
    """List available workflows.

    Args:
        origin: Optional filter by origin (e.g., "builtin", "custom").

    Returns:
        list[dict]: List of workflow definitions.
    """
    workflows = list_workflows(origin=origin)
    return [serialize_workflow(wf) for wf in workflows]


@router.get("/workflows/search")
async def search_workflows_endpoint(q: str) -> list[dict]:
    """Search for workflows.

    Args:
        q: Search query string.

    Returns:
        list[dict]: List of matching workflow definitions.
    """
    workflows = find_workflows(q)
    return [serialize_workflow(wf) for wf in workflows]


@router.post("/workflows/run")
async def run_workflow_endpoint(request: Request, body: WorkflowRunRequest) -> dict:
    """Start a workflow execution.

    Args:
        request: FastAPI request object.
        body: Workflow run request with name and inputs.

    Returns:
        dict: Execution information including execution_id and status.

    Raises:
        HTTPException: If workflow not found or execution fails.
    """
    workflow = get_workflow(body.name)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {body.name}")

    try:
        # Parse workflow definition
        definition = json.loads(workflow["definition"])

        # Create sandbox for execution
        create_sandbox({
            "type": "process",
            "working_dir": "/tmp/openship-sandbox"
        })

        # Build and run graph
        graph = build_graph(definition)
        result = run_graph(graph, body.inputs, wait=True)

        # Return execution result
        return {
            "execution_id": result.get("execution_id"),
            "status": result.get("status", "unknown"),
            "output": result.get("output"),
            "step_outputs": result.get("step_outputs"),
            "error": result.get("error"),
        }
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid workflow definition: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {e}")


@router.get("/workflows/status/{execution_id}")
async def get_workflow_status_endpoint(execution_id: str) -> dict:
    """Get the status of a workflow execution.

    Args:
        execution_id: The execution ID to check.

    Returns:
        dict: Execution status and result information.

    Raises:
        HTTPException: If execution not found.
    """
    execution = get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"Execution not found: {execution_id}")

    return {
        "execution_id": execution_id,
        "status": execution.get("status", "unknown"),
        "inputs": execution.get("inputs"),
        "result": execution.get("result"),
    }


@router.post("/workflows/resume/{execution_id}")
async def resume_workflow_endpoint(execution_id: str, body: ResumeWorkflowRequest) -> dict:
    """Resume a paused workflow execution.

    Args:
        execution_id: The execution ID to resume.
        body: Resume request with optional response for human-in-the-loop.

    Returns:
        dict: Execution result after resuming.

    Raises:
        HTTPException: If execution not found or resume fails.
    """
    try:
        result = resume_graph(execution_id, body.response)
        if result is None:
            # Execution is still running or failed
            execution = get_execution(execution_id)
            if execution is None:
                raise HTTPException(status_code=404, detail=f"Execution not found: {execution_id}")
            return {
                "execution_id": execution_id,
                "status": execution.get("status", "unknown"),
            }
        return result
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resume execution: {e}")


@router.post("/workflows/build")
async def build_workflow_endpoint(body: BuildWorkflowRequest) -> dict:
    """Build a workflow from a natural language description.
    
    Uses the LLM to generate an execution plan from the description.
    
    Args:
        body: Build request with workflow description.
    
    Returns:
        dict: Generated plan JSON.
    """
    try:
        from src.agent import Agent
        
        agent = Agent()
        agent.workflow = body.description
        
        # Generate plan with LLM
        plan = agent.generate_plan(feedback=body.feedback)
        
        return {
            "plan": plan,
            "status": "generated"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build workflow: {e}")


@router.post("/workflows/compile")
async def compile_workflow_endpoint(body: BuildWorkflowRequest) -> dict:
    """Compile a generated plan to a LangGraph workflow.
    
    Args:
        body: Compile request with workflow description and plan.
    
    Returns:
        dict: Compiled workflow information.
    """
    try:
        from src.agent import Agent
        
        agent = Agent()
        agent.workflow = body.description
        
        # Parse the plan
        plan_json = agent.parse_plan()
        
        # Compile to LangGraph
        graph = agent.compile_graph(plan_json)
        
        return {
            "status": "compiled",
            "graph_id": id(graph)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compile workflow: {e}")


@router.post("/workflows/register")
async def register_workflow_endpoint(body: RegisterWorkflowRequest) -> dict:
    """Register a new workflow.

    Args:
        body: Workflow registration data.

    Returns:
        dict: Registration confirmation with workflow ID.
    """
    workflow_id = register_workflow({
        "name": body.name,
        "version": body.version,
        "origin": body.origin,
        "description": body.description,
        "definition": body.definition,
        "inputs": body.inputs,
        "outputs": body.outputs,
        "tags": body.tags,
        "required_tools": body.required_tools,
        "required_connectors": body.required_connectors,
    })
    return {"workflow_id": workflow_id, "name": body.name, "version": body.version}


# Tool endpoints
@router.get("/tools")
async def list_tools_endpoint(
    category: str | None = Query(None),
    origin: str | None = Query(None),
    name: str | None = Query(None),
) -> list[dict]:
    """List available tools.

    Args:
        category: Optional filter by category (e.g., "file", "shell").
        origin: Optional filter by origin (e.g., "builtin", "custom").
        name: Optional filter by tool name.

    Returns:
        list[dict]: List of tool definitions.
    """
    tools = list_tools(category=category, origin=origin)
    if name:
        tools = [t for t in tools if t.get("name") == name]
    return [serialize_tool(t) for t in tools]


@router.get("/tools/search")
async def search_tools_endpoint(q: str) -> list[dict]:
    """Search for tools.

    Args:
        q: Search query string.

    Returns:
        list[dict]: List of matching tool definitions.
    """
    tools = find_tools(q)
    return [serialize_tool(t) for t in tools]


@router.post("/tools/register")
async def register_tool_endpoint(body: RegisterToolRequest) -> dict:
    """Register a new tool.

    Args:
        body: Tool registration data.

    Returns:
        dict: Registration confirmation with tool name.
    """
    tool_name = register_tool({
        "name": body.name,
        "version": body.version,
        "origin": body.origin,
        "description": body.description,
        "category": body.category,
        "inputs": body.inputs,
        "outputs": body.outputs,
        "tags": body.tags,
        "permissions": body.permissions,
    })
    return {"tool_name": tool_name, "version": body.version}


# Helper functions
def serialize_workflow(wf: dict) -> dict:
    """Serialize workflow definition for API response.

    Args:
        wf: Workflow definition dictionary.

    Returns:
        dict: Serialized workflow definition.
    """
    return {
        "id": wf.get("id"),
        "name": wf.get("name"),
        "version": wf.get("version"),
        "origin": wf.get("origin"),
        "description": wf.get("description"),
        "tags": wf.get("tags", []),
        "required_tools": wf.get("required_tools", []),
        "updated_at": wf.get("updated_at"),
    }


def serialize_tool(tool: dict) -> dict:
    """Serialize tool definition for API response.

    Args:
        tool: Tool definition dictionary.

    Returns:
        dict: Serialized tool definition.
    """
    return {
        "name": tool.get("name"),
        "version": tool.get("version"),
        "origin": tool.get("origin"),
        "description": tool.get("description"),
        "category": tool.get("category"),
        "tags": tool.get("tags", []),
        "permissions": tool.get("permissions", []),
        "updated_at": tool.get("updated_at"),
    }
