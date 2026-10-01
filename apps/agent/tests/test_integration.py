"""Integration and end-to-end tests for the complete OpenShip system.

These tests verify that all components work together:
- Database operations
- Tool registry
- Workflow registry
- Sandbox management
- Built-in tool execution
- LangGraph orchestration engine
- FastAPI backend service
- CLI interface
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from src.core.engine import reset_executions
from src.core.sandbox import reset_sandbox_registry
from src.main import app
from src.registry.tools import close_registry as close_tool_registry
from src.registry.tools import init_registry as init_tool_registry
from src.registry.tools import register_tool
from src.registry.workflows import close_registry as close_workflow_registry
from src.registry.workflows import register_workflow
from src.registry.workflows import reset_registry as reset_workflow_registry
from src.tools.builtin import register_builtin_tools
from src.tools.implementations import register_tool_implementation


@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path):
    """Initialize all registries and reset state for each test."""
    db_path = str(tmp_path / "integration_test.db")
    os.environ["OPENSUP_DB_PATH"] = db_path

    # Initialize registries
    init_tool_registry(db_path)
    reset_workflow_registry()

    # Register built-in tool implementations
    register_builtin_tools()

    # Register an echo tool that matches the engine's expected interface
    class EchoTool:
        """Echo tool for integration tests."""
        def execute(self, inputs, context=None):
            return {"success": True, "output": inputs.get("args", "")}

    register_tool_implementation("echo", EchoTool)

    yield db_path

    # Teardown
    close_tool_registry()
    close_workflow_registry()
    reset_sandbox_registry()
    reset_executions()
    if "OPENSUP_DB_PATH" in os.environ:
        del os.environ["OPENSUP_DB_PATH"]


@pytest.fixture
def client():
    """Create test client for the FastAPI app."""
    return TestClient(app)


class TestFullWorkflowExecution:
    """Test complete workflow execution from registration to completion."""

    def test_full_workflow_execution(self):
        """Register, run, and verify a complete workflow with output.

        This test covers the entire workflow lifecycle:
        1. Register a tool
        2. Register a workflow that uses the tool
        3. Run the workflow
        4. Verify the output
        """
        # Step 1: Register the echo tool
        tool = {
            "name": "echo",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Echo back input",
            "category": "test",
            "inputs": {
                "type": "object",
                "properties": {"args": {"type": "string"}},
                "required": ["args"]
            },
            "outputs": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "output": {"type": "string"}
                }
            }
        }
        register_tool(tool)

        # Step 2: Register a workflow that uses the echo tool
        workflow = {
            "name": "integration_test_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Integration test workflow",
            "definition": json.dumps({
                "steps": [
                    {"order": 1, "tool": "echo", "args": "Hello from OpenShip integration test"}
                ]
            })
        }
        register_workflow(workflow)

        # Step 3: Run the workflow via the API
        # The API runs the workflow synchronously (wait=True) and returns the result
        with TestClient(app) as client:
            response = client.post("/api/workflows/run", json={
                "name": "integration_test_workflow",
                "inputs": {}
            })

        assert response.status_code == 200
        data = response.json()
        assert "execution_id" in data
        # The workflow should complete successfully
        assert data["status"] == "completed"
        # Verify the step output
        assert data["step_outputs"] is not None
        assert "1" in data["step_outputs"]
        step_output = data["step_outputs"]["1"]
        assert step_output["success"] is True
        assert "Hello from OpenShip integration test" in step_output["output"]

    def test_workflow_with_multiple_steps(self):
        """Test a workflow with multiple tool invocations."""
        # Register the echo tool
        echo_tool = {
            "name": "echo",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Echo back input",
            "category": "test",
            "inputs": {"type": "object", "properties": {"args": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"success": {"type": "boolean"}}}
        }
        register_tool(echo_tool)

        # Register a multi-step workflow
        workflow = {
            "name": "multi_step_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Multi-step test workflow",
            "definition": json.dumps({
                "steps": [
                    {"order": 1, "tool": "echo", "args": "step1"},
                    {"order": 2, "tool": "echo", "args": "step2"}
                ]
            })
        }
        register_workflow(workflow)

        # Run the workflow
        with TestClient(app) as client:
            response = client.post("/api/workflows/run", json={
                "name": "multi_step_workflow",
                "inputs": {}
            })

        assert response.status_code == 200
        data = response.json()
        assert "execution_id" in data
        # Verify both steps completed
        assert data["status"] == "completed"
        assert "1" in data["step_outputs"]
        assert "2" in data["step_outputs"]
        assert "step1" in data["step_outputs"]["1"]["output"]
        assert "step2" in data["step_outputs"]["2"]["output"]

        # Verify workflow is listed
        with TestClient(app) as client:
            list_response = client.get("/api/workflows")

        assert list_response.status_code == 200
        workflows = list_response.json()
        assert any(wf["name"] == "multi_step_workflow" for wf in workflows)

    def test_workflow_error_handling(self):
        """Test workflow execution with a non-existent tool."""
        # Register a workflow with a non-existent tool
        workflow = {
            "name": "error_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Error handling test",
            "definition": json.dumps({
                "steps": [
                    {"order": 1, "tool": "nonexistent.tool", "args": "test"}
                ]
            })
        }
        register_workflow(workflow)

        # Run the workflow - should fail because tool doesn't exist
        with TestClient(app) as client:
            response = client.post("/api/workflows/run", json={
                "name": "error_workflow",
                "inputs": {}
            })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "Tool not found" in data["error"] or "nonexistent.tool" in data["error"]

    def test_nonexistent_workflow(self):
        """Test running a workflow that doesn't exist."""
        with TestClient(app) as client:
            response = client.post("/api/workflows/run", json={
                "name": "nonexistent_workflow",
                "inputs": {}
            })

        assert response.status_code == 404


class TestRegistryIntegration:
    """Test integration between tool and workflow registries."""

    def test_tool_and_workflow_listing(self):
        """Test that tools and workflows can be listed together."""
        # Register a tool
        tool = {
            "name": "test.tool",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test tool",
            "category": "test",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool)

        # Register a workflow
        workflow = {
            "name": "test.workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Test workflow",
            "definition": "{}"
        }
        register_workflow(workflow)

        # List tools
        with TestClient(app) as client:
            tools_response = client.get("/api/tools")

        assert tools_response.status_code == 200
        tools = tools_response.json()
        assert any(t["name"] == "test.tool" for t in tools)

        # List workflows
        with TestClient(app) as client:
            workflows_response = client.get("/api/workflows")

        assert workflows_response.status_code == 200
        workflows = workflows_response.json()
        assert any(w["name"] == "test.workflow" for w in workflows)

    def test_tool_category_filtering(self):
        """Test tool listing with category filtering."""
        # Register tools in different categories
        file_tool = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        shell_tool = {
            "name": "shell.exec",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Execute command",
            "category": "shell",
            "inputs": {},
            "outputs": {}
        }
        register_tool(file_tool)
        register_tool(shell_tool)

        # Filter by file category
        with TestClient(app) as client:
            response = client.get("/api/tools?category=file")

        assert response.status_code == 200
        tools = response.json()
        assert len(tools) == 1
        assert tools[0]["name"] == "file.read"

        # Filter by shell category
        with TestClient(app) as client:
            response = client.get("/api/tools?category=shell")

        assert response.status_code == 200
        tools = response.json()
        assert len(tools) == 1
        assert tools[0]["name"] == "shell.exec"

    def test_workflow_origin_filtering(self):
        """Test workflow listing with origin filtering."""
        # Register workflows with different origins
        builtin_wf = {
            "name": "builtin.workflow",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Builtin workflow",
            "definition": "{}"
        }
        custom_wf = {
            "name": "custom.workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Custom workflow",
            "definition": "{}"
        }
        register_workflow(builtin_wf)
        register_workflow(custom_wf)

        # Filter by builtin origin
        with TestClient(app) as client:
            response = client.get("/api/workflows?origin=builtin")

        assert response.status_code == 200
        workflows = response.json()
        assert len(workflows) == 1
        assert workflows[0]["name"] == "builtin.workflow"

        # Filter by custom origin
        with TestClient(app) as client:
            response = client.get("/api/workflows?origin=custom")

        assert response.status_code == 200
        workflows = response.json()
        assert len(workflows) == 1
        assert workflows[0]["name"] == "custom.workflow"


class TestSandboxIntegration:
    """Test sandbox management integrated with workflow execution."""

    def test_sandbox_created_for_workflow(self):
        """Test that a sandbox is created when a workflow runs."""
        # Register the echo tool
        echo_tool = {
            "name": "echo",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Echo back input",
            "category": "test",
            "inputs": {"type": "object", "properties": {"args": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"success": {"type": "boolean"}}}
        }
        register_tool(echo_tool)

        # Register a workflow
        workflow = {
            "name": "sandbox_test_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Sandbox test workflow",
            "definition": json.dumps({
                "steps": [
                    {"order": 1, "tool": "echo", "args": "sandbox test"}
                ]
            })
        }
        register_workflow(workflow)

        # Run the workflow
        with TestClient(app) as client:
            response = client.post("/api/workflows/run", json={
                "name": "sandbox_test_workflow",
                "inputs": {}
            })

        assert response.status_code == 200
        data = response.json()
        assert "execution_id" in data
        assert data["status"] == "completed"


class TestEndToEndScenarios:
    """End-to-end user scenarios."""

    def test_user_discovers_and_runs_workflow(self):
        """Simulate a user discovering and running a workflow."""
        # Step 1: User lists available workflows
        with TestClient(app) as client:
            list_response = client.get("/api/workflows")

        assert list_response.status_code == 200

        # Step 2: User registers a new workflow
        echo_tool = {
            "name": "echo",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Echo back input",
            "category": "test",
            "inputs": {"type": "object", "properties": {"args": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"success": {"type": "boolean"}}}
        }
        register_tool(echo_tool)

        workflow = {
            "name": "user_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "User-defined workflow",
            "definition": json.dumps({
                "steps": [
                    {"order": 1, "tool": "echo", "args": "hello from user workflow"}
                ]
            })
        }
        register_workflow(workflow)

        # Step 3: User lists workflows again and sees their workflow
        with TestClient(app) as client:
            list_response = client.get("/api/workflows")

        workflows = list_response.json()
        assert any(wf["name"] == "user_workflow" for wf in workflows)

        # Step 4: User runs their workflow
        with TestClient(app) as client:
            run_response = client.post("/api/workflows/run", json={
                "name": "user_workflow",
                "inputs": {}
            })

        assert run_response.status_code == 200
        data = run_response.json()
        assert "execution_id" in data

        # Step 5: User checks workflow status
        with TestClient(app) as client:
            status_response = client.get(f"/api/workflows/status/{data['execution_id']}")

        assert status_response.status_code == 200

    def test_user_discovers_and_uses_tools(self):
        """Simulate a user discovering and using tools."""
        # Step 1: User lists available tools
        with TestClient(app) as client:
            tools_response = client.get("/api/tools")

        assert tools_response.status_code == 200

        # Step 2: User searches for a specific tool
        with TestClient(app) as client:
            search_response = client.get("/api/tools/search", params={"q": "read"})

        assert search_response.status_code == 200

        # Step 3: User describes a tool
        tool = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file contents",
            "category": "file",
            "inputs": {"type": "object", "properties": {"path": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"content": {"type": "string"}}}
        }
        register_tool(tool)

        with TestClient(app) as client:
            describe_response = client.get("/api/tools", params={"name": "file.read"})

        assert describe_response.status_code == 200
        tools = describe_response.json()
        assert any(t["name"] == "file.read" for t in tools)

    def test_health_check(self):
        """Test the health check endpoint."""
        with TestClient(app) as client:
            response = client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_root_endpoint(self):
        """Test the root endpoint."""
        with TestClient(app) as client:
            response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "OpenShip Agent API"