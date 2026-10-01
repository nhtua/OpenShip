"""Tool registry operations.

Manages tool registration, retrieval, listing, and search.
Uses a persistent SQLite database connection managed internally.
"""

import sqlite3

from src.database.operations import (
    _row_to_dict,
)
from src.database.operations import (
    get_tool as db_get_tool,
)
from src.database.operations import (
    list_tools as db_list_tools,
)
from src.database.operations import (
    register_tool as db_register_tool,
)
from src.database.schema import close_db, init_db

# Module-level database connection
_conn: sqlite3.Connection | None = None


def init_registry(path: str) -> None:
    """Initialize the tool registry with a database connection.

    Args:
        path: Path to the SQLite database file, or ":memory:" for in-memory DB.
    """
    global _conn
    _conn = init_db(path)


def close_registry() -> None:
    """Close the tool registry database connection."""
    global _conn
    if _conn:
        close_db(_conn)
        _conn = None


def register_tool(tool: dict) -> str:
    """Register a tool in the registry.

    Args:
        tool: Tool definition dictionary with fields:
            - name (str): Unique tool name
            - version (str): Semantic version
            - origin (str): Tool origin (e.g., "builtin", "custom")
            - description (str): Tool description
            - category (str): Tool category
            - inputs (dict): JSON Schema for inputs
            - outputs (dict): JSON Schema for outputs
            - tags (list): Optional tags
            - permissions (list): Optional permissions

    Returns:
        str: The tool name.
    """
    return db_register_tool(_conn, tool)


def get_tool(name: str, version: str | None = None) -> dict | None:
    """Get a tool by name (and optionally version).

    Args:
        name: Tool name.
        version: Optional version to match. If not provided, returns the latest version.

    Returns:
        Optional[dict]: The tool definition or None if not found.
    """
    return db_get_tool(_conn, name, version)


def list_tools(category: str | None = None, origin: str | None = None) -> list[dict]:
    """List tools, optionally filtered by category and/or origin.

    Args:
        category: Optional category filter (e.g., "file", "shell").
        origin: Optional origin filter (e.g., "builtin", "custom").

    Returns:
        list[dict]: List of tool definitions.
    """
    if category and origin:
        # Both filters: query directly
        rows = _conn.execute(
            "SELECT * FROM tools WHERE category = ? AND origin = ? ORDER BY name",
            (category, origin),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    elif category:
        # Category filter only
        rows = _conn.execute(
            "SELECT * FROM tools WHERE category = ? ORDER BY name",
            (category,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    else:
        # Use db_list_tools for origin-only or no filter
        return db_list_tools(_conn, origin)


def find_tools(query: str) -> list[dict]:
    """Search for tools matching a query string.

    Searches across name, description, and category fields using SQLite LIKE matching.

    Args:
        query: Search query string.

    Returns:
        list[dict]: List of matching tool definitions.
    """
    like_query = f"%{query}%"
    rows = _conn.execute(
        """SELECT * FROM tools
           WHERE name LIKE ? OR description LIKE ? OR category LIKE ?
           ORDER BY name""",
        (like_query, like_query, like_query),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]
