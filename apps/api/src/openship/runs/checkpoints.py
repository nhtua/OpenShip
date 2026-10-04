"""PostgreSQL checkpoint backend for LangGraph.

Uses the maintained PostgresSaver with strict msgpack serialization.
Checkpoint setup is an install/migration step, never on a request.
"""

import os
import contextlib
from typing import Generator

# Enable strict msgpack serialization (no pickle fallback)
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")


@contextlib.contextmanager
def get_checkpointer(database_url: str) -> Generator:
    """Create a PostgreSQL checkpointer from a database URL.

    Args:
        database_url: PostgreSQL connection string.

    Yields:
        A configured PostgresSaver instance.
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    saver = PostgresSaver.from_conn_string(database_url)
    yield saver


def setup_checkpointer(database_url: str):
    """Run checkpoint table setup as an install/migration step.

    This should be called during application startup or migration,
    never during request handling.
    """
    with get_checkpointer(database_url) as saver:
        saver.setup()
