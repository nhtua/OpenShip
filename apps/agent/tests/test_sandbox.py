"""Tests for sandbox management."""

import os
import tempfile

import pytest

from src.core.sandbox import (
    cleanup_sandbox,
    create_sandbox,
    execute_in_sandbox,
    get_sandbox,
)


class TestSandboxCreation:
    """Tests for sandbox creation and lifecycle."""

    def test_create_process_sandbox(self):
        """Create a process sandbox returns a sandbox_id."""
        config = {
            "type": "process",
            "working_dir": tempfile.mkdtemp(),
        }
        sandbox_id = create_sandbox(config)
        assert sandbox_id is not None
        assert len(sandbox_id) > 0
        cleanup_sandbox(sandbox_id)

    def test_create_sandbox_generates_unique_ids(self):
        """Each sandbox creation generates a unique ID."""
        config = {"type": "process", "working_dir": tempfile.mkdtemp()}
        id1 = create_sandbox(config)
        id2 = create_sandbox(config)
        assert id1 != id2
        cleanup_sandbox(id1)
        cleanup_sandbox(id2)

    def test_sandbox_state_is_tracked(self):
        """Created sandbox is retrievable and has correct state."""
        working_dir = tempfile.mkdtemp()
        config = {"type": "process", "working_dir": working_dir}
        sandbox_id = create_sandbox(config)

        sandbox = get_sandbox(sandbox_id)
        assert sandbox is not None
        assert sandbox["type"] == "process"
        assert sandbox["working_dir"] == working_dir
        assert sandbox["status"] == "active"

        cleanup_sandbox(sandbox_id)

    def test_get_nonexistent_sandbox(self):
        """Retrieving a nonexistent sandbox returns None."""
        sandbox = get_sandbox("nonexistent-sandbox-id")
        assert sandbox is None


class TestSandboxExecution:
    """Tests for tool execution within sandbox."""

    def test_execute_shell_command_in_sandbox(self):
        """Execute a shell command and capture output."""
        working_dir = tempfile.mkdtemp()
        config = {"type": "process", "working_dir": working_dir}
        sandbox_id = create_sandbox(config)

        result = execute_in_sandbox(
            sandbox_id,
            tool={"name": "shell.echo", "type": "shell", "cmd": "echo"},
            inputs={"args": "hello sandbox"},
        )
        assert result["exit_code"] == 0
        assert "hello sandbox" in result["output"]

        cleanup_sandbox(sandbox_id)

    def test_execute_command_with_working_dir(self):
        """Command executes in the sandbox's working directory."""
        working_dir = tempfile.mkdtemp()
        # Create a marker file to verify working directory
        test_file = os.path.join(working_dir, "test_file.txt")
        with open(test_file, "w") as f:
            f.write("test content")

        config = {"type": "process", "working_dir": working_dir}
        sandbox_id = create_sandbox(config)

        result = execute_in_sandbox(
            sandbox_id,
            tool={"name": "shell.cat", "type": "shell", "cmd": "cat"},
            inputs={"args": "test_file.txt"},
        )
        assert result["exit_code"] == 0
        assert "test content" in result["output"]

        cleanup_sandbox(sandbox_id)

    def test_execute_command_nonzero_exit(self):
        """Capture non-zero exit codes correctly."""
        working_dir = tempfile.mkdtemp()
        config = {"type": "process", "working_dir": working_dir}
        sandbox_id = create_sandbox(config)

        result = execute_in_sandbox(
            sandbox_id,
            tool={"name": "shell.false", "type": "shell", "cmd": "false"},
            inputs={"args": ""},
        )
        assert result["exit_code"] != 0

        cleanup_sandbox(sandbox_id)

    def test_execute_in_nonexistent_sandbox_raises(self):
        """Executing in a nonexistent sandbox raises an error."""
        with pytest.raises(KeyError):
            execute_in_sandbox(
                "nonexistent",
                tool={"name": "test", "type": "shell", "cmd": "echo"},
                inputs={"args": "test"},
            )


class TestSandboxCleanup:
    """Tests for sandbox cleanup."""

    def test_cleanup_sandbox_marks_as_cleaned(self):
        """Cleanup marks the sandbox as cleaned."""
        working_dir = tempfile.mkdtemp()
        config = {"type": "process", "working_dir": working_dir}
        sandbox_id = create_sandbox(config)

        cleanup_sandbox(sandbox_id)

        sandbox = get_sandbox(sandbox_id)
        assert sandbox is not None
        assert sandbox["status"] == "cleaned"

    def test_cleanup_removes_sandbox_from_registry(self):
        """After cleanup, sandbox is no longer accessible via get_sandbox for new operations."""
        working_dir = tempfile.mkdtemp()
        config = {"type": "process", "working_dir": working_dir}
        sandbox_id = create_sandbox(config)

        cleanup_sandbox(sandbox_id)

        # Sandbox still exists but is marked as cleaned
        sandbox = get_sandbox(sandbox_id)
        assert sandbox is not None
        assert sandbox["status"] == "cleaned"

    def test_cleanup_idempotent(self):
        """Calling cleanup multiple times is safe."""
        working_dir = tempfile.mkdtemp()
        config = {"type": "process", "working_dir": working_dir}
        sandbox_id = create_sandbox(config)

        cleanup_sandbox(sandbox_id)
        cleanup_sandbox(sandbox_id)

        sandbox = get_sandbox(sandbox_id)
        assert sandbox is not None
        assert sandbox["status"] == "cleaned"
