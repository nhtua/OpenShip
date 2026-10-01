"""shell.exec built-in tool.

Executes a shell command and returns the output.
"""

import subprocess
from typing import ClassVar

from src.tools.base import Tool


class ShellExecTool(Tool):
    """Execute a shell command.

    Inputs:
        command (str): Shell command to execute.
        working_dir (str): Working directory for the command (optional).
        timeout (int): Timeout in seconds (optional).

    Outputs:
        success (bool): Whether the command succeeded (exit code 0).
        exit_code (int): Command exit code.
        output (str): Command stdout.
        error (str): Command stderr or execution error.
    """

    name = "shell.exec"
    version = "1.0.0"
    description = "Execute a shell command"
    inputs_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "working_dir": {"type": "string", "description": "Working directory (optional)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (optional)"}
        },
        "required": ["command"]
    }
    outputs_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "exit_code": {"type": "integer"},
            "output": {"type": "string"},
            "error": {"type": "string"}
        }
    }

    def execute(self, inputs: dict, context: dict) -> dict:
        command = inputs.get("command")
        working_dir = inputs.get("working_dir") or context.get("working_dir")
        timeout = inputs.get("timeout") or 60

        if not command:
            return {"success": False, "error": "Missing required input: command"}

        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "success": result.returncode == 0,
                "exit_code": result.returncode,
                "output": result.stdout,
                "error": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "error": f"Command timed out after {timeout}s",
            }
        except OSError as e:
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "error": f"Failed to execute command: {e}",
            }
