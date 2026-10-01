"""Tool execution framework.

Provides the Tool base class, built-in tool implementations, and tool execution
via the tool registry.
"""

from src.tools.base import Tool
from src.tools.builtin import register_builtin_tools
from src.tools.implementations import get_tool_implementation

__all__ = ["TOOL_REGISTRY", "Tool", "execute_tool", "register_builtin_tools"]


# Backward-compatible tool registry (PoC artifact, superseded by SQLite registry in Task 3)
TOOL_REGISTRY = {
    "shell.echo": {"type": "shell", "cmd": "echo"},
    "shell.date": {"type": "shell", "cmd": "date"},
    "shell.xargs": {"type": "shell", "cmd": "xargs"},
    "exec.curl": {"type": "exec", "cmd": "curl"},
    "user.ask": {"type": "interrupt", "description": "Ask user a question and pause for their response"},
}


def execute_tool(tool_name: str, inputs: dict) -> dict:
    """Execute a tool by name.

    Looks up the tool from the registry, instantiates the appropriate tool class,
    and executes it with the given inputs.

    Args:
        tool_name: Name of the tool to execute (e.g., "file.read").
        inputs: Input parameters for the tool as a dictionary.

    Returns:
        Execution result dictionary with 'success', 'output', and optionally 'error'.

    Raises:
        KeyError: If tool_name is not registered.
    """
    from src.registry.tools import get_tool

    tool_def = get_tool(tool_name)
    if tool_def is None:
        raise KeyError(f"Tool not found in registry: {tool_name}")

    # Resolve the tool class from the implementation registry
    tool_class = get_tool_implementation(tool_name)
    if tool_class is None:
        raise KeyError(f"No implementation registered for tool: {tool_name}")

    tool = tool_class()
    return tool.execute(inputs, {})
