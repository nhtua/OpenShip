"""SQLite database schema definitions and initialization for OpenShip."""

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'custom',
    definition TEXT NOT NULL,
    description TEXT,
    inputs TEXT,
    outputs TEXT,
    required_tools TEXT,
    required_connectors TEXT,
    tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tools (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    origin TEXT NOT NULL,
    description TEXT,
    category TEXT,
    inputs TEXT,
    outputs TEXT,
    tags TEXT,
    permissions TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow_executions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    status TEXT NOT NULL DEFAULT 'running',
    inputs TEXT,
    outputs TEXT,
    checkpoint_data BLOB,
    sandbox_id TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    error TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES workflow_executions(id),
    step_name TEXT NOT NULL,
    state_data BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_workflows_name ON workflows(name);
CREATE INDEX IF NOT EXISTS idx_workflows_origin ON workflows(origin);
CREATE INDEX IF NOT EXISTS idx_tools_origin ON tools(origin);
CREATE INDEX IF NOT EXISTS idx_executions_workflow_id ON workflow_executions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_executions_status ON workflow_executions(status);
CREATE INDEX IF NOT EXISTS idx_checkpoints_execution_id ON checkpoints(execution_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_step_name ON checkpoints(step_name);
"""


def init_db(path: str):
    """Initialize the database with the schema.

    Args:
        path: Path to the SQLite database file, or ":memory:" for in-memory DB.

    Returns:
        sqlite3.Connection: The database connection.
    """
    if path != ":memory:":
        # Ensure parent directory exists
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def close_db(conn):
    """Close the database connection.

    Args:
        conn: The database connection to close.
    """
    if conn:
        conn.close()
