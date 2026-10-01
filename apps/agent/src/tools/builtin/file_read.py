"""file.read built-in tool.

Reads the contents of a file and returns them as a string.
"""

from pathlib import Path
from typing import ClassVar

from src.tools.base import Tool


class FileReadTool(Tool):
    """Read file contents.

    Inputs:
        path (str): Path to the file to read.

    Outputs:
        success (bool): Whether the read succeeded.
        content (str): File contents (on success).
        error (str): Error message (on failure).
    """

    name = "file.read"
    version = "1.0.0"
    description = "Read the contents of a file"
    inputs_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read"}
        },
        "required": ["path"]
    }
    outputs_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "content": {"type": "string"},
            "error": {"type": "string"}
        }
    }

    def execute(self, inputs: dict, context: dict) -> dict:
        path = inputs.get("path")
        if not path:
            return {"success": False, "error": "Missing required input: path"}

        file_path = Path(path)
        try:
            content = file_path.read_text(encoding="utf-8")
            return {"success": True, "content": content}
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {path}"}
        except IsADirectoryError:
            return {"success": False, "error": f"Path is a directory, not a file: {path}"}
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {path}"}
        except OSError as e:
            return {"success": False, "error": f"Failed to read file: {e}"}
