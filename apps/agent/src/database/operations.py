"""Database CRUD operations for OpenShip workflows, tools, executions, and checkpoints."""

import json
import sqlite3
import uuid
from typing import Any


def _serialize(obj: Any) -> str:
    """Serialize an object to JSON string."""
    if obj is None:
        return None
    return json.dumps(obj)


def _deserialize(value: str) -> Any:
    """Deserialize a JSON string to an object."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a dictionary with deserialized JSON fields."""
    if row is None:
        return None
    result = dict(row)
    # All JSON-serializable fields across all tables
    for field in ["inputs", "outputs", "required_tools", "required_connectors",
                  "tags", "permissions", "state_data"]:
        if field in result and result[field] is not None:
            result[field] = _deserialize(result[field])
    return result


# Workflow operations

def register_workflow(conn: sqlite3.Connection, workflow: dict) -> str:
    """Register a workflow in the database.

    Args:
        conn: Database connection.
        workflow: Workflow definition dictionary.

    Returns:
        str: The workflow ID.
    """
    workflow_id = workflow.get("id") or str(uuid.uuid4())
    conn.execute(
        """INSERT OR REPLACE INTO workflows (id, name, version, origin, definition, description, inputs, outputs, required_tools, required_connectors, tags, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (
            workflow_id,
            workflow["name"],
            workflow["version"],
            workflow.get("origin", "custom"),
            workflow["definition"],
            workflow.get("description"),
            _serialize(workflow.get("inputs")),
            _serialize(workflow.get("outputs")),
            _serialize(workflow.get("required_tools")),
            _serialize(workflow.get("required_connectors")),
            _serialize(workflow.get("tags")),
        ),
    )
    conn.commit()
    return workflow_id


def get_workflow(conn: sqlite3.Connection, name: str, version: str | None = None) -> dict | None:
    """Get a workflow by name (and optionally version).

    Args:
        conn: Database connection.
        name: Workflow name.
        version: Optional version to match.

    Returns:
        Optional[dict]: The workflow definition or None.
    """
    if version:
        row = conn.execute(
            "SELECT * FROM workflows WHERE name = ? AND version = ?",
            (name, version),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM workflows WHERE name = ? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
    return _row_to_dict(row)


def list_workflows(conn: sqlite3.Connection, origin: str | None = None) -> list[dict]:
    """List workflows, optionally filtered by origin.

    Args:
        conn: Database connection.
        origin: Optional origin filter (e.g., "builtin", "custom").

    Returns:
        list[dict]: List of workflow definitions.
    """
    if origin:
        rows = conn.execute(
            "SELECT * FROM workflows WHERE origin = ? ORDER BY name",
            (origin,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM workflows ORDER BY name").fetchall()
    return [_row_to_dict(row) for row in rows]


# Tool operations

def register_tool(conn: sqlite3.Connection, tool: dict) -> str:
    """Register a tool in the database.

    Args:
        conn: Database connection.
        tool: Tool definition dictionary.

    Returns:
        str: The tool name.
    """
    conn.execute(
        """INSERT OR REPLACE INTO tools (name, version, origin, description, category, inputs, outputs, tags, permissions, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (
            tool["name"],
            tool["version"],
            tool.get("origin", "custom"),
            tool.get("description"),
            tool.get("category"),
            _serialize(tool.get("inputs")),
            _serialize(tool.get("outputs")),
            _serialize(tool.get("tags")),
            _serialize(tool.get("permissions")),
        ),
    )
    conn.commit()
    return tool["name"]


def get_tool(conn: sqlite3.Connection, name: str, version: str | None = None) -> dict | None:
    """Get a tool by name (and optionally version).

    Args:
        conn: Database connection.
        name: Tool name.
        version: Optional version to match.

    Returns:
        Optional[dict]: The tool definition or None.
    """
    if version:
        row = conn.execute(
            "SELECT * FROM tools WHERE name = ? AND version = ?",
            (name, version),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM tools WHERE name = ? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
    return _row_to_dict(row)


def list_tools(conn: sqlite3.Connection, origin: str | None = None) -> list[dict]:
    """List tools, optionally filtered by origin.

    Args:
        conn: Database connection.
        origin: Optional origin filter (e.g., "builtin", "custom").

    Returns:
        list[dict]: List of tool definitions.
    """
    if origin:
        rows = conn.execute(
            "SELECT * FROM tools WHERE origin = ? ORDER BY name",
            (origin,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tools ORDER BY name").fetchall()
    return [_row_to_dict(row) for row in rows]


# Execution operations

def create_execution(conn: sqlite3.Connection, workflow_id: str, inputs: dict) -> dict:
    """Create a new workflow execution record.

    Args:
        conn: Database connection.
        workflow_id: The workflow ID being executed.
        inputs: Input parameters for the execution.

    Returns:
        dict: The created execution record.
    """
    execution_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO workflow_executions (id, workflow_id, status, inputs, started_at)
           VALUES (?, ?, 'running', ?, CURRENT_TIMESTAMP)""",
        (execution_id, workflow_id, _serialize(inputs)),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM workflow_executions WHERE id = ?",
        (execution_id,),
    ).fetchone()
    return _row_to_dict(row)


def get_execution(conn: sqlite3.Connection, execution_id: str) -> dict | None:
    """Get a workflow execution by ID.

    Args:
        conn: Database connection.
        execution_id: The execution ID.

    Returns:
        Optional[dict]: The execution record or None.
    """
    row = conn.execute(
        "SELECT * FROM workflow_executions WHERE id = ?",
        (execution_id,),
    ).fetchone()
    return _row_to_dict(row)


def update_execution_status(
    conn: sqlite3.Connection,
    execution_id: str,
    status: str,
    outputs: dict | None = None,
    error: str | None = None,
) -> None:
    """Update the status of a workflow execution.

    Args:
        conn: Database connection.
        execution_id: The execution ID.
        status: New status (e.g., "running", "paused", "completed", "failed").
        outputs: Optional output data to store.
        error: Optional error message.
    """
    completed_at = "CURRENT_TIMESTAMP" if status in ("completed", "failed") else None

    if outputs is not None and completed_at:
        conn.execute(
            """UPDATE workflow_executions
               SET status = ?, outputs = ?, completed_at = CURRENT_TIMESTAMP, error = ?
               WHERE id = ?""",
            (status, _serialize(outputs), error, execution_id),
        )
    elif completed_at:
        conn.execute(
            """UPDATE workflow_executions
               SET status = ?, completed_at = CURRENT_TIMESTAMP, error = ?
               WHERE id = ?""",
            (status, error, execution_id),
        )
    else:
        conn.execute(
            "UPDATE workflow_executions SET status = ? WHERE id = ?",
            (status, execution_id),
        )
    conn.commit()


# Checkpoint operations

def save_checkpoint(
    conn: sqlite3.Connection,
    execution_id: str,
    step_name: str,
    state_data: dict,
) -> str:
    """Save a workflow checkpoint.

    Args:
        conn: Database connection.
        execution_id: The execution ID.
        step_name: The name of the step being checkpointed.
        state_data: The state data to checkpoint.

    Returns:
        str: The checkpoint ID.
    """
    checkpoint_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO checkpoints (id, execution_id, step_name, state_data, created_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (checkpoint_id, execution_id, step_name, _serialize(state_data)),
    )
    conn.commit()
    return checkpoint_id


def get_latest_checkpoint(
    conn: sqlite3.Connection,
    execution_id: str,
    step_name: str,
) -> dict | None:
    """Get the latest checkpoint for a given execution and step.

    Args:
        conn: Database connection.
        execution_id: The execution ID.
        step_name: The step name.

    Returns:
        Optional[dict]: The latest checkpoint or None.
    """
    row = conn.execute(
        """SELECT * FROM checkpoints
           WHERE execution_id = ? AND step_name = ?
           ORDER BY rowid DESC LIMIT 1""",
        (execution_id, step_name),
    ).fetchone()
    return _row_to_dict(row)
