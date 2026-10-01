"""Tool implementation registry.

Maintains an in-memory mapping of tool names to their Python class implementations.
Separate from the tool definition registry (Task 3) to avoid circular dependencies.
"""

# In-memory mapping of tool name -> tool class
_tool_implementations: dict[str, type] = {}


def register_tool_implementation(name: str, tool_class: type) -> None:
    """Register a tool implementation class.

    Args:
        name: Tool name (must match registry entry).
        tool_class: Tool class implementing the Tool interface.
    """
    _tool_implementations[name] = tool_class


def get_tool_implementation(name: str) -> type | None:
    """Get a tool implementation class by name.

    Args:
        name: Tool name.

    Returns:
        The tool class, or None if not found.
    """
    return _tool_implementations.get(name)
