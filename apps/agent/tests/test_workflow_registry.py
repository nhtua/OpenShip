"""Tests for workflow registry operations."""

import pytest

from src.registry.workflows import (
    find_workflows,
    get_workflow,
    list_workflows,
    register_workflow,
    reset_registry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure a fresh registry state for each test."""
    reset_registry()
    yield
    reset_registry()


class TestWorkflowRegistry:
    """Tests for the workflow registry layer."""

    def test_register_and_get_workflow(self):
        """Test registering a workflow and retrieving it by name."""
        workflow = {
            "name": "test_workflow",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Test workflow",
            "definition": "# Test\nThis is a test workflow."
        }
        register_workflow(workflow)
        retrieved = get_workflow("test_workflow")
        assert retrieved["name"] == "test_workflow"
        assert retrieved["origin"] == "builtin"
        assert retrieved["version"] == "1.0.0"

    def test_register_workflow_with_json_fields(self):
        """Test that JSON fields are properly serialized/deserialized."""
        workflow = {
            "name": "json_workflow",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Workflow with JSON fields",
            "definition": "# JSON Test",
            "inputs": {"type": "object", "properties": {"name": {"type": "string"}}},
            "outputs": {"type": "object", "properties": {"result": {"type": "string"}}},
            "tags": ["test", "json"],
            "required_tools": ["shell.exec"],
            "required_connectors": ["aws"]
        }
        register_workflow(workflow)
        retrieved = get_workflow("json_workflow")
        assert retrieved["inputs"]["type"] == "object"
        assert retrieved["tags"] == ["test", "json"]
        assert retrieved["required_tools"] == ["shell.exec"]
        assert retrieved["required_connectors"] == ["aws"]

    def test_list_workflows(self):
        """Test listing workflows with and without origin filter."""
        # Register builtin workflows
        for i in range(3):
            register_workflow({
                "name": f"builtin_{i}",
                "version": "1.0.0",
                "origin": "builtin",
                "description": f"Builtin workflow {i}",
                "definition": "# Builtin"
            })

        # Register custom workflow
        register_workflow({
            "name": "custom_0",
            "version": "1.0.0",
            "origin": "custom",
            "description": "Custom workflow",
            "definition": "# Custom"
        })

        # List all workflows
        all_workflows = list_workflows()
        assert len(all_workflows) == 4

        # List builtin only
        builtin_workflows = list_workflows(origin="builtin")
        assert len(builtin_workflows) == 3
        for wf in builtin_workflows:
            assert wf["origin"] == "builtin"

        # List custom only
        custom_workflows = list_workflows(origin="custom")
        assert len(custom_workflows) == 1
        assert custom_workflows[0]["origin"] == "custom"

    def test_get_nonexistent_workflow(self):
        """Test getting a workflow that doesn't exist."""
        retrieved = get_workflow("nonexistent_workflow")
        assert retrieved is None

    def test_find_workflows_by_name(self):
        """Test finding workflows by name."""
        register_workflow({
            "name": "deploy_app",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Deploy an application",
            "definition": "# Deploy"
        })

        register_workflow({
            "name": "build_app",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Build an application",
            "definition": "# Build"
        })

        results = find_workflows("deploy")
        assert len(results) == 1
        assert results[0]["name"] == "deploy_app"

    def test_find_workflows_by_description(self):
        """Test finding workflows by description."""
        register_workflow({
            "name": "test_wf",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Automated testing pipeline",
            "definition": "# Test"
        })

        results = find_workflows("testing")
        assert len(results) == 1
        assert results[0]["description"] == "Automated testing pipeline"

    def test_find_workflows_no_match(self):
        """Test finding workflows with no matches."""
        results = find_workflows("nonexistent_query")
        assert len(results) == 0

    def test_register_same_workflow_multiple_times(self):
        """Test registering a workflow multiple times updates it."""
        workflow1 = {
            "name": "update_test",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Original description",
            "definition": "# Original"
        }
        register_workflow(workflow1)

        workflow2 = {
            "name": "update_test",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Updated description",
            "definition": "# Updated"
        }
        register_workflow(workflow2)

        retrieved = get_workflow("update_test")
        assert retrieved["description"] == "Updated description"
        assert retrieved["definition"] == "# Updated"