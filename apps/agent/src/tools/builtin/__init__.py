"""Built-in tool implementations.

Registers all built-in tools with the tool implementation registry.
"""

from src.tools.builtin.file_read import FileReadTool
from src.tools.builtin.file_write import FileWriteTool
from src.tools.builtin.shell_exec import ShellExecTool
from src.tools.implementations import register_tool_implementation


def register_builtin_tools() -> None:
    """Register all built-in tool implementations."""
    register_tool_implementation("file.read", FileReadTool)
    register_tool_implementation("file.write", FileWriteTool)
    register_tool_implementation("shell.exec", ShellExecTool)
