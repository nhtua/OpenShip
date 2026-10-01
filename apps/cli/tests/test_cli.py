"""Tests for the OpenShip CLI interface."""

import json
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from src.main import app, api_get, api_post, get_api_url

runner = CliRunner()


class TestApiHelperFunctions:
    """Test the API helper functions."""

    def test_get_api_url_default(self):
        """Test default API URL."""
        with patch.dict("os.environ", {}, clear=True):
            assert get_api_url() == "http://localhost:8000"

    def test_get_api_url_from_env(self):
        """Test API URL from environment variable."""
        with patch.dict("os.environ", {"OPENSHIP_API_URL": "http://custom:9000"}):
            assert get_api_url() == "http://custom:9000"


class TestWorkflowCommands:
    """Test workflow CLI commands."""

    @patch("src.main.api_get")
    def test_workflow_list(self, mock_api_get):
        """Test workflow list command."""
        mock_api_get.return_value = []
        result = runner.invoke(app, ["workflow", "list"])
        assert result.exit_code == 0
        assert "No workflows found" in result.output

    @patch("src.main.api_get")
    def test_workflow_list_with_workflows(self, mock_api_get):
        """Test workflow list command with workflows."""
        mock_api_get.return_value = [
            {"name": "test-workflow", "version": "1.0.0", "origin": "builtin", "description": "Test", "tags": []}
        ]
        result = runner.invoke(app, ["workflow", "list"])
        assert result.exit_code == 0
        assert "test-workflow" in result.output

    @patch("src.main.api_get")
    def test_workflow_list_with_origin_filter(self, mock_api_get):
        """Test workflow list command with origin filter."""
        mock_api_get.return_value = []
        result = runner.invoke(app, ["workflow", "list", "--origin", "custom"])
        assert result.exit_code == 0
        mock_api_get.assert_called_once_with("/api/workflows", params={"origin": "custom"})

    @patch("src.main.api_post")
    def test_workflow_run(self, mock_api_post):
        """Test workflow run command."""
        mock_api_post.return_value = {"execution_id": "exec-123", "status": "completed"}
        result = runner.invoke(app, ["workflow", "run", "test-workflow"])
        assert result.exit_code == 0
        assert "Running workflow" in result.output
        mock_api_post.assert_called_once_with(
            "/api/workflows/run", {"name": "test-workflow", "inputs": {}}
        )

    @patch("src.main.api_post")
    def test_workflow_run_with_inputs(self, mock_api_post):
        """Test workflow run command with inputs."""
        mock_api_post.return_value = {"execution_id": "exec-123", "status": "completed"}
        inputs_json = '{"key": "value"}'
        result = runner.invoke(app, ["workflow", "run", "test-workflow", "--inputs", inputs_json])
        assert result.exit_code == 0
        mock_api_post.assert_called_once_with(
            "/api/workflows/run", {"name": "test-workflow", "inputs": {"key": "value"}}
        )

    @patch("src.main.api_get")
    def test_workflow_status(self, mock_api_get):
        """Test workflow status command."""
        mock_api_get.return_value = {"execution_id": "exec-123", "status": "completed"}
        result = runner.invoke(app, ["workflow", "status", "exec-123"])
        assert result.exit_code == 0
        mock_api_get.assert_called_once_with("/api/workflows/status/exec-123")

    @patch("src.main.api_post")
    def test_workflow_resume(self, mock_api_post):
        """Test workflow resume command."""
        mock_api_post.return_value = {"execution_id": "exec-123", "status": "completed"}
        result = runner.invoke(app, ["workflow", "resume", "exec-123"])
        assert result.exit_code == 0
        mock_api_post.assert_called_once_with(
            "/api/workflows/resume/exec-123", {"response": None}
        )

    @patch("src.main.api_get")
    def test_workflow_search(self, mock_api_get):
        """Test workflow search command."""
        mock_api_get.return_value = []
        result = runner.invoke(app, ["workflow", "search", "test"])
        assert result.exit_code == 0
        mock_api_get.assert_called_once_with("/api/workflows/search", params={"q": "test"})


class TestToolCommands:
    """Test tool CLI commands."""

    @patch("src.main.api_get")
    def test_tool_list(self, mock_api_get):
        """Test tool list command."""
        mock_api_get.return_value = []
        result = runner.invoke(app, ["tool", "list"])
        assert result.exit_code == 0
        assert "No tools found" in result.output

    @patch("src.main.api_get")
    def test_tool_list_with_tools(self, mock_api_get):
        """Test tool list command with tools."""
        mock_api_get.return_value = [
            {"name": "test-tool", "version": "1.0.0", "category": "test", "origin": "builtin", "description": "Test"}
        ]
        result = runner.invoke(app, ["tool", "list"])
        assert result.exit_code == 0
        assert "test-tool" in result.output

    @patch("src.main.api_get")
    def test_tool_list_with_category_filter(self, mock_api_get):
        """Test tool list command with category filter."""
        mock_api_get.return_value = []
        result = runner.invoke(app, ["tool", "list", "--category", "shell"])
        assert result.exit_code == 0
        mock_api_get.assert_called_once_with("/api/tools", params={"category": "shell"})

    @patch("src.main.api_get")
    def test_tool_describe_found(self, mock_api_get):
        """Test tool describe command - tool found."""
        mock_api_get.return_value = [
            {"name": "test-tool", "version": "1.0.0", "category": "test", "origin": "builtin", "description": "Test"}
        ]
        result = runner.invoke(app, ["tool", "describe", "test-tool"])
        assert result.exit_code == 0
        assert "test-tool" in result.output
        mock_api_get.assert_called_once_with("/api/tools", params={"name": "test-tool"})

    @patch("src.main.api_get")
    def test_tool_describe_not_found(self, mock_api_get):
        """Test tool describe command - tool not found."""
        mock_api_get.return_value = []
        result = runner.invoke(app, ["tool", "describe", "test-tool"])
        assert result.exit_code == 1
        assert "Tool not found" in result.output
        mock_api_get.assert_called_once_with("/api/tools", params={"name": "test-tool"})

    @patch("src.main.api_get")
    def test_tool_search(self, mock_api_get):
        """Test tool search command."""
        mock_api_get.return_value = []
        result = runner.invoke(app, ["tool", "search", "test"])
        assert result.exit_code == 0
        mock_api_get.assert_called_once_with("/api/tools/search", params={"q": "test"})


class TestSessionCommands:
    """Test session CLI commands."""

    def test_session_list(self):
        """Test session list command."""
        result = runner.invoke(app, ["session", "list"])
        assert result.exit_code == 0


class TestHealthCommand:
    """Test health check command."""

    @patch("src.main.api_get")
    def test_health_success(self, mock_api_get):
        """Test health check success."""
        mock_api_get.return_value = {"status": "ok"}
        result = runner.invoke(app, ["health"])
        assert result.exit_code == 0
        assert "healthy" in result.output


class TestMainApp:
    """Test main app configuration."""

    def test_app_exists(self):
        """Test that the Typer app exists."""
        assert app is not None
