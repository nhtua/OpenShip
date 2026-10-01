"""file.write built-in tool.

Writes content to a file, creating or overwriting as needed.
"""

from pathlib import Path
from typing import ClassVar

from src.tools.base import Tool


class FileWriteTool(Tool):
    """Write content to a file.

    Inputs:
        path (str): Path to the file to write.
        content (str): Content to write to the file.

    Outputs:
        success (bool): Whether the write succeeded.
        path (str): Path to the written file (on success).
        error (str): Error message (on failure).
    """

    name = "file.write"
    version = "1.0.0"
    description = "Write content to a file"
    inputs_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write to the file"}
        },
        "required": ["path", "content"]
    }
    outputs_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "path": {"type": "string"},
            "error": {"type": "string"}
        }
    }

    def execute(self, inputs: dict, context: dict) -> dict:
        path = inputs.get("path")
        content = inputs.get("content", "")

        if not path:
            return {"success": False, "error": "Missing required input: path"}

        file_path = Path(path)
        try:
            # Create parent directories if they don't exist
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return {"success": True, "path": path}
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}
        except OSError as e:
            return {"success": False, "error": f"Failed to write file: {e}"}
