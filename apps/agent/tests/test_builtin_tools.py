"""Tests for built-in tool implementations."""

import os
import tempfile

import pytest

from src.registry.tools import close_registry, init_registry, register_tool
from src.tools import execute_tool
from src.tools.builtin import register_builtin_tools as register_builtins


@pytest.fixture(autouse=True)
def setup_registry():
    """Set up tool registry and register built-in tools."""
    init_registry(":memory:")
    register_builtins()

    # Register tool definitions with the registry
    register_tool({
        "name": "file.read",
        "version": "1.0.0",
        "origin": "builtin",
        "description": "Read file contents",
        "category": "file",
        "inputs": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"]
        },
        "outputs": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "content": {"type": "string"},
                "error": {"type": "string"}
            }
        }
    })

    register_tool({
        "name": "file.write",
        "version": "1.0.0",
        "origin": "builtin",
        "description": "Write file contents",
        "category": "file",
        "inputs": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["path", "content"]
        },
        "outputs": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "path": {"type": "string"},
                "error": {"type": "string"}
            }
        }
    })

    register_tool({
        "name": "shell.exec",
        "version": "1.0.0",
        "origin": "builtin",
        "description": "Execute shell command",
        "category": "shell",
        "inputs": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "working_dir": {"type": "string"},
                "timeout": {"type": "integer"}
            },
            "required": ["command"]
        },
        "outputs": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "exit_code": {"type": "integer"},
                "output": {"type": "string"},
                "error": {"type": "string"}
            }
        }
    })

    yield

    close_registry()


class TestFileReadTool:
    """Tests for the file.read tool."""

    def test_file_read_existing_file(self):
        """Test reading an existing file."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("Hello, World!")
            path = f.name

        try:
            result = execute_tool("file.read", {"path": path})
            assert result["success"] is True
            assert result["content"] == "Hello, World!"
        finally:
            os.unlink(path)

    def test_file_read_nonexistent_file(self):
        """Test reading a file that doesn't exist."""
        result = execute_tool("file.read", {"path": "/tmp/nonexistent-file-12345.txt"})
        assert result["success"] is False
        assert "File not found" in result["error"]

    def test_file_read_missing_path(self):
        """Test reading with missing path input."""
        result = execute_tool("file.read", {})
        assert result["success"] is False
        assert "path" in result["error"]


class TestFileWriteTool:
    """Tests for the file.write tool."""

    def test_file_write_new_file(self):
        """Test writing to a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")
            result = execute_tool("file.write", {
                "path": path,
                "content": "Hello, World!"
            })
            assert result["success"] is True
            assert os.path.exists(path)

            # Verify content
            with open(path) as f:
                assert f.read() == "Hello, World!"

    def test_file_write_overwrite(self):
        """Test overwriting an existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.txt")
            with open(path, "w") as f:
                f.write("Old content")

            result = execute_tool("file.write", {
                "path": path,
                "content": "New content"
            })
            assert result["success"] is True

            with open(path) as f:
                assert f.read() == "New content"

    def test_file_write_creates_parent_dirs(self):
        """Test writing to a path with non-existent parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "nested", "sub", "test.txt")
            result = execute_tool("file.write", {
                "path": path,
                "content": "Test content"
            })
            assert result["success"] is True
            assert os.path.exists(path)

    def test_file_write_missing_inputs(self):
        """Test writing with missing required inputs."""
        result = execute_tool("file.write", {})
        assert result["success"] is False
        assert "path" in result["error"]


class TestShellExecTool:
    """Tests for the shell.exec tool."""

    def test_shell_exec_success(self):
        """Test executing a successful command."""
        result = execute_tool("shell.exec", {"command": "echo hello"})
        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "hello" in result["output"]

    def test_shell_exec_failure(self):
        """Test executing a command that fails."""
        result = execute_tool("shell.exec", {"command": "false"})
        assert result["success"] is False
        assert result["exit_code"] != 0

    def test_shell_exec_working_dir(self):
        """Test executing a command in a specific working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_tool("shell.exec", {
                "command": "pwd",
                "working_dir": tmpdir
            })
            assert result["success"] is True
            assert tmpdir in result["output"]

    def test_shell_exec_missing_command(self):
        """Test executing with missing command input."""
        result = execute_tool("shell.exec", {})
        assert result["success"] is False
        assert "command" in result["error"]


class TestToolExecution:
    """Tests for tool execution framework."""

    def test_execute_unregistered_tool(self):
        """Test executing a tool that isn't registered."""
        with pytest.raises(KeyError) as exc_info:
            execute_tool("nonexistent.tool", {})
        assert "nonexistent.tool" in str(exc_info.value)
