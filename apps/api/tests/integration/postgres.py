"""Connection limits shared by the real PostgreSQL test lanes."""

from sqlalchemy.engine import make_url


def bounded_postgres_url(url: str) -> str:
    """Bound connection and SQL waits, including Alembic subprocesses."""
    parsed = make_url(url)
    options = parsed.query.get("options", "")
    return parsed.update_query_dict({
        "connect_timeout": "5",
        "options": f"{options} -c lock_timeout=5000 -c statement_timeout=30000".strip(),
    }).render_as_string(hide_password=False)
