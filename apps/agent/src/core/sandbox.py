"""Sandbox lifecycle management for isolated tool execution.

Supports process-level sandboxing initially. Container sandboxing via Docker
is deferred to a future implementation.
"""

import shlex
import subprocess
import uuid
from typing import Any

# Module-level registry of active sandboxes
_sandboxes: dict[str, dict[str, Any]] = {}


def create_sandbox(config: dict[str, Any]) -> str:
    """Create a new sandbox with the given configuration.

    Args:
        config: Sandbox configuration dict. Must include 'type' and 'working_dir'.
            Supported types: 'process' (local subprocess isolation).

    Returns:
        sandbox_id: A unique identifier for the sandbox.

    Raises:
        ValueError: If config is missing required fields or has invalid type.
    """
    sandbox_type = config.get("type")
    working_dir = config.get("working_dir")

    if sandbox_type is None:
        raise ValueError("Sandbox config must include 'type'")
    if working_dir is None:
        raise ValueError("Sandbox config must include 'working_dir'")
    if sandbox_type not in ("process",):
        raise ValueError(f"Unsupported sandbox type: {sandbox_type}")

    sandbox_id = str(uuid.uuid4())

    _sandboxes[sandbox_id] = {
        "id": sandbox_id,
        "type": sandbox_type,
        "working_dir": working_dir,
        "config": config,
        "status": "active",
    }

    return sandbox_id


def get_sandbox(sandbox_id: str) -> dict[str, Any] | None:
    """Retrieve sandbox configuration by ID.

    Args:
        sandbox_id: The sandbox identifier.

    Returns:
        Sandbox configuration dict, or None if not found.
    """
    return _sandboxes.get(sandbox_id)


def execute_in_sandbox(
    sandbox_id: str,
    tool: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Execute a tool within the sandbox's isolated environment.

    Args:
        sandbox_id: The sandbox identifier.
        tool: Tool definition dict with 'name', 'type', and 'cmd'.
        inputs: Input parameters for the tool.

    Returns:
        Execution result dict with 'exit_code', 'output', 'error', and 'working_dir'.

    Raises:
        KeyError: If sandbox_id is not found.
    """
    sandbox = _sandboxes.get(sandbox_id)
    if sandbox is None:
        raise KeyError(f"Sandbox not found: {sandbox_id}")

    if sandbox["status"] != "active":
        raise ValueError(f"Sandbox {sandbox_id} is not active (status: {sandbox['status']})")

    tool_type = tool.get("type")
    cmd = tool.get("cmd")
    args = inputs.get("args", "")

    if tool_type == "shell":
        # Build shell command safely
        if args:
            command = f"{cmd} {shlex.quote(args)}"
        else:
            command = cmd

        result = subprocess.run(
            ["bash", "-c", command],
            cwd=sandbox["working_dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    elif tool_type == "exec":
        # Execute binary directly
        result = subprocess.run(
            [cmd] + ([args] if args else []),
            cwd=sandbox["working_dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        raise ValueError(f"Unknown tool type: {tool_type}")

    return {
        "exit_code": result.returncode,
        "output": result.stdout,
        "error": result.stderr,
        "working_dir": sandbox["working_dir"],
    }


def cleanup_sandbox(sandbox_id: str) -> None:
    """Clean up a sandbox, marking it as cleaned.

    Args:
        sandbox_id: The sandbox identifier.
    """
    sandbox = _sandboxes.get(sandbox_id)
    if sandbox is None:
        return

    sandbox["status"] = "cleaned"


def list_sandboxes() -> list[dict[str, Any]]:
    """List all sandboxes (active and cleaned).

    Returns:
        List of sandbox configuration dicts.
    """
    return list(_sandboxes.values())


def reset_sandbox_registry() -> None:
    """Clear all sandboxes from the registry. Useful for testing."""
    _sandboxes.clear()
