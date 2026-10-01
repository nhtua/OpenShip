"""Tests for the FastAPI backend service."""

import json

import pytest
from fastapi.testclient import TestClient

from src.core.engine import reset_executions
from src.core.sandbox import reset_sandbox_registry
from src.main import app
from src.registry.tools import close_registry as close_tool_registry
from src.registry.tools import init_registry as init_tool_registry
from src.registry.workflows import close_registry as close_workflow_registry
from src.registry.workflows import reset_registry as reset_workflow_registry


@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path):
    """Initialize registries and reset state for each test."""
    db_path = str(tmp_path / "api_test.db")
    # Initialize tool registry
    init_tool_registry(db_path)
    # Initialize workflow registry (uses same DB via env var)
    import os
    os.environ["OPENSUP_DB_PATH"] = db_path
    # Force workflow registry to re-init with new DB path
    reset_workflow_registry()
    
    yield db_path
    
    # Teardown
    close_tool_registry()
    close_workflow_registry()
    reset_sandbox_registry()
    reset_executions()


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestWorkflowsList:
    def test_list_workflows_empty(self, client):
        """Test listing workflows when registry is empty."""
        response = client.get("/api/workflows")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_workflows_with_entries(self, client):
        """Test listing workflows after registration."""
        from src.registry.workflows import register_workflow
        
        # Register a workflow
        workflow = {
            "name": "test_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Test workflow",
            "definition": "# Test\nThis is a test workflow."
        }
        register_workflow(workflow)
        
        response = client.get("/api/workflows")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "test_workflow"

    def test_list_workflows_with_origin_filter(self, client):
        """Test listing workflows filtered by origin."""
        from src.registry.workflows import register_workflow
        
        # Register workflows with different origins
        builtin_wf = {
            "name": "builtin_workflow",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Built-in workflow",
            "definition": "# Built-in workflow"
        }
        custom_wf = {
            "name": "custom_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Custom workflow",
            "definition": "# Custom workflow"
        }
        register_workflow(builtin_wf)
        register_workflow(custom_wf)
        
        response = client.get("/api/workflows?origin=builtin")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "builtin_workflow"


class TestToolsList:
    def test_list_tools_empty(self, client):
        """Test listing tools when registry is empty."""
        response = client.get("/api/tools")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_tools_with_entries(self, client):
        """Test listing tools after registration."""
        from src.registry.tools import register_tool
        
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
        
        response = client.get("/api/tools")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "test.tool"

    def test_list_tools_with_category_filter(self, client):
        """Test listing tools filtered by category."""
        from src.registry.tools import register_tool
        
        # Register tools with different categories
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
            "description": "Execute shell command",
            "category": "shell",
            "inputs": {},
            "outputs": {}
        }
        register_tool(file_tool)
        register_tool(shell_tool)
        
        response = client.get("/api/tools?category=file")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "file.read"

    def test_list_tools_with_origin_filter(self, client):
        """Test listing tools filtered by origin."""
        from src.registry.tools import register_tool
        
        # Register tools with different origins
        builtin_tool = {
            "name": "builtin.tool",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Builtin tool",
            "category": "test",
            "inputs": {},
            "outputs": {}
        }
        custom_tool = {
            "name": "custom.tool",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Custom tool",
            "category": "test",
            "inputs": {},
            "outputs": {}
        }
        register_tool(builtin_tool)
        register_tool(custom_tool)
        
        response = client.get("/api/tools?origin=builtin")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "builtin.tool"

    def test_list_tools_with_name_filter(self, client):
        """Test listing tools with name filter."""
        from src.registry.tools import register_tool
        tool1 = {
            "name": "test.tool1",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test tool 1",
            "category": "test",
            "inputs": {},
            "outputs": {}
        }
        tool2 = {
            "name": "test.tool2",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test tool 2",
            "category": "test",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool1)
        register_tool(tool2)

        response = client.get("/api/tools?name=test.tool1")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "test.tool1"

        # Test non-matching name
        response = client.get("/api/tools?name=test.nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0


class TestWorkflowRun:
    def test_run_workflow_invalid_name(self, client):
        """Test running a non-existent workflow."""
        response = client.post("/api/workflows/run", json={
            "name": "nonexistent_workflow",
            "inputs": {}
        })
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    def test_run_workflow_success(self, client):
        """Test running a registered workflow."""
        # Create a test file for file.read tool

        from src.registry.tools import register_tool
        from src.registry.workflows import register_workflow
        test_file_path = "/tmp/openship-test-api.txt"
        with open(test_file_path, "w") as f:
            f.write("Test content for API test")
        
        # Register a simple tool
        tool = {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read file",
            "category": "file",
            "inputs": {},
            "outputs": {}
        }
        register_tool(tool)
        
        # Register a simple workflow
        workflow = {
            "name": "simple_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Simple workflow",
            "definition": json.dumps({
                "steps": [
                    {"order": 1, "tool": "file.read", "args": test_file_path}
                ]
            })
        }
        register_workflow(workflow)
        
        response = client.post("/api/workflows/run", json={
            "name": "simple_workflow",
            "inputs": {}
        })
        assert response.status_code == 200
        data = response.json()
        assert "execution_id" in data
        assert data["status"] in ["running", "completed", "paused", "failed"]


class TestWorkflowStatus:
    def test_get_status_nonexistent(self, client):
        """Test getting status of non-existent execution."""
        response = client.get("/api/workflows/status/nonexistent-id")
        assert response.status_code == 404


class TestHealth:
    def test_health_endpoint(self, client):
        """Test health check endpoint."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
