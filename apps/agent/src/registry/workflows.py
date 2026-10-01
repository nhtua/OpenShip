"""Workflow registry operations.

Provides a clean interface for registering, retrieving, listing, and searching
workflows. Uses the underlying database operations for persistence.
"""

import hashlib
import os
import sqlite3

from src.database import operations as db_ops
from src.database.schema import close_db, init_db

# Default database path
DB_PATH = os.environ.get("OPENSUP_DB_PATH", ":memory:")

# Module-level database connection (lazy initialization)
_conn: sqlite3.Connection | None = None


def _get_connection() -> sqlite3.Connection:
    """Get or create the database connection.

    Returns:
        sqlite3.Connection: The database connection.
    """
    global _conn
    if _conn is None:
        _conn = init_db(DB_PATH)
    return _conn


def register_workflow(workflow: dict) -> str:
    """Register a workflow in the registry.

    Uses a consistent ID derived from name and version so that re-registering
    the same workflow updates the existing record.

    Args:
        workflow: Workflow definition dictionary with fields:
            - name (str): Workflow name (required)
            - version (str): Version string (required)
            - origin (str): Origin (builtin/custom) (optional, defaults to custom)
            - description (str): Workflow description (optional)
            - definition (str): Workflow definition content (required)
            - inputs (dict): Input schema (optional)
            - outputs (dict): Output schema (optional)
            - tags (list): Tags for the workflow (optional)
            - required_tools (list): Required tools (optional)
            - required_connectors (list): Required connectors (optional)

    Returns:
        str: The workflow ID.
    """
    # Generate consistent ID from name and version for upsert support
    id_input = f"{workflow['name']}:{workflow['version']}"
    workflow_id = hashlib.sha256(id_input.encode()).hexdigest()[:32]
    workflow["id"] = workflow_id

    conn = _get_connection()
    return db_ops.register_workflow(conn, workflow)


def get_workflow(name: str, version: str | None = None) -> dict | None:
    """Get a workflow by name.

    If version is not specified, returns the latest version.

    Args:
        name: Workflow name.
        version: Optional version to match.

    Returns:
        Optional[dict]: The workflow definition, or None if not found.
    """
    conn = _get_connection()
    return db_ops.get_workflow(conn, name, version)


def list_workflows(origin: str | None = None) -> list[dict]:
    """List workflows, optionally filtered by origin.

    Args:
        origin: Optional origin filter (e.g., "builtin", "custom").

    Returns:
        list[dict]: List of workflow definitions.
    """
    conn = _get_connection()
    return db_ops.list_workflows(conn, origin)


def find_workflows(query: str) -> list[dict]:
    """Find workflows matching a query string.

    Searches workflow names and descriptions for the query term.

    Args:
        query: Query string to search for.

    Returns:
        list[dict]: List of matching workflow definitions.
    """
    conn = _get_connection()
    return db_ops.find_workflows(conn, query)


def reset_registry() -> None:
    """Reset the registry by closing and clearing the database connection.

    Useful for testing to ensure a fresh database state.
    """
    global _conn
    if _conn is not None:
        close_db(_conn)
    _conn = None


def close_registry() -> None:
    """Close the registry database connection."""
    global _conn
    if _conn is not None:
        close_db(_conn)
        _conn = None
