"""The Phase 3 provider migration's durability, on every database available.

The revision must keep what a Phase 2 installation already had, invent no
provider for historical runs, and refuse to erase pinned configuration
silently. SQLite is always available, so these checks run in the unit suite
anywhere; the PostgreSQL lane adds the same checks against a real database
when ``TEST_POSTGRES_URL`` names a scratch one.

These tests drive Alembic directly and do not depend on the ORM's schema.
"""

import os
import subprocess
import sys
import uuid as uuidlib
from pathlib import Path

import pytest
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.pool import NullPool

from tests.integration.postgres import bounded_postgres_url

PHASE2_HEAD = "ghi789"


@pytest.fixture(params=[
    pytest.param("sqlite", id="sqlite"),
    pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
])
def database_url(request, tmp_path):
    """One database to run the migration against.

    SQLite is built fresh for each test. PostgreSQL is opt-in: it is the same
    scratch database across tests, and the ``alembic_ini`` fixture returns it
    to a clean Phase 2 schema before each one runs.
    """
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'phase3.db'}"
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip(
            "no TEST_POSTGRES_URL; the PostgreSQL lane is opt-in and SQLite "
            "is not substituted for it"
        )
    return bounded_postgres_url(url)


def _engine(url: str):
    """Release each connection rather than leaving a per-helper pool behind."""
    return create_engine(url, poolclass=NullPool)


def _alembic(ini_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run one alembic command against the given configuration."""
    env = {
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
        "ALEMBIC_CONFIG": str(ini_path),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini_path), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).parents[2],
        timeout=120,
        check=False,
    )


def _upgrade(ini_path: Path, revision: str) -> None:
    """Run one upgrade, and name the revision that could not be reached."""
    result = _alembic(ini_path, "upgrade", revision)
    assert result.returncode == 0, (
        f"upgrade to {revision} failed: {result.stderr}\n"
        "Every revision must declare ids with the type the ORM declares "
        "(sa.Uuid): SQLAlchemy maps it to a string column on SQLite and to "
        "the native uuid on PostgreSQL, so a revision that declares varchar "
        "cannot take a foreign key to an earlier one on PostgreSQL. SQLite "
        "accepts the mismatch, which is why this only fails in the PostgreSQL "
        "lane."
    )


@pytest.fixture
def alembic_ini(database_url, tmp_path):
    """An alembic configuration naming the database under test."""
    ini_path = tmp_path / "alembic.ini"
    script_dir = Path(__file__).parents[2] / "alembic"
    ini_path.write_text(
        f"""[alembic]
script_location = {script_dir}
sqlalchemy.url = {database_url.replace('%', '%%')}

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
    )
    return ini_path


@pytest.fixture
def phase2_db(database_url, alembic_ini):
    """A database at Phase 2, empty, ready for one test to fill."""
    _upgrade(alembic_ini, "head")
    with _engine(database_url).connect() as conn:
        for table in ("provider_attempts", "messages", "runs", "conversations", "projects", "users"):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.commit()
    result = _alembic(alembic_ini, "downgrade", PHASE2_HEAD)
    assert result.returncode == 0, f"cannot return the lane to Phase 2: {result.stderr}"
    return database_url


def _tables(url: str):
    """The database's current tables, by name."""
    metadata = MetaData()
    metadata.reflect(bind=_engine(url))
    return metadata.tables


def _seed_phase2_rows(url: str) -> dict:
    """Users, projects, conversations, and runs as a Phase 2 installation had.

    Raw SQL keeps the seeding dialect-agnostic: UUID columns are strings on
    SQLite and native on PostgreSQL. One run per conversation, as the schema
    requires.
    """
    ids = {name: str(uuidlib.uuid4()) for name in ("user", "project")}
    turns = {status: str(uuidlib.uuid4()) for status in ("completed", "queued", "running")}

    with _engine(url).connect() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, :username, :email, :hash)"
            ),
            {"id": ids["user"], "username": "phase2-user", "email": "phase2@example.com", "hash": "phase2-hash"},
        )
        conn.execute(
            text(
                "INSERT INTO projects (id, owner_user_id, name) VALUES (:id, :owner, :name)"
            ),
            {"id": ids["project"], "owner": ids["user"], "name": "phase2-project"},
        )
        for status, run_id in turns.items():
            conversation_id = str(uuidlib.uuid4())
            conn.execute(
                text(
                    "INSERT INTO conversations (id, user_id, project_id, title) "
                    "VALUES (:id, :user, :project, :title)"
                ),
                {
                    "id": conversation_id,
                    "user": ids["user"],
                    "project": ids["project"],
                    "title": f"Phase 2 {status}",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO runs (id, conversation_id, project_id, status, attempt, fence) "
                    "VALUES (:id, :conversation, :project, :status, :attempt, :fence)"
                ),
                {
                    "id": run_id,
                    "conversation": conversation_id,
                    "project": ids["project"],
                    "status": status,
                    "attempt": 1 if status == "completed" else 0,
                    "fence": 1 if status == "running" else 0,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO messages (id, conversation_id, role, content) "
                    "VALUES (:id, :conversation, :role, :content)"
                ),
                {
                    "id": str(uuidlib.uuid4()),
                    "conversation": conversation_id,
                    "role": "user",
                    "content": f"a {status} turn",
                },
            )
        conn.commit()
    return {"ids": ids, "turns": turns}


def _record_an_attempt(url: str, run_id: str, ordinal: int) -> None:
    """One attempt, as a worker would have recorded it."""
    with _engine(url).connect() as conn:
        conn.execute(
            text(
                "INSERT INTO provider_attempts (id, run_id, ordinal, fence, profile_id, "
                "profile_revision, adapter, requested_model, destination_label, outcome) "
                "VALUES (:id, :run, :ordinal, :fence, :profile, :revision, :adapter, "
                ":model, :destination, :outcome)"
            ),
            {
                "id": str(uuidlib.uuid4()),
                "run": run_id,
                "ordinal": ordinal,
                "fence": 0,
                "profile": "primary-fixture",
                "revision": "0" * 64,
                "adapter": "openai",
                "model": "fixture-model",
                "destination": "fixture.provider.test",
                "outcome": "started",
            },
        )
        conn.commit()


def test_upgrade_preserves_phase2_rows(phase2_db, alembic_ini):
    """Upgrading to Phase 3 keeps every Phase 2 row and its values."""
    seeded = _seed_phase2_rows(phase2_db)

    _upgrade(alembic_ini, "head")

    tables = _tables(phase2_db)
    with _engine(phase2_db).connect() as conn:
        runs = conn.execute(tables["runs"].select()).fetchall()
        assert len(runs) == 3
        assert {run.status for run in runs} == {"completed", "queued", "running"}
        assert len(conn.execute(tables["messages"].select()).fetchall()) == 3
        assert {str(run.id) for run in runs} == set(seeded["turns"].values())


def test_upgrade_invents_no_provider_for_historical_runs(phase2_db, alembic_ini):
    """The new columns exist, and old runs keep a null snapshot."""
    _seed_phase2_rows(phase2_db)

    _upgrade(alembic_ini, "head")

    tables = _tables(phase2_db)
    assert "provider_attempts" in tables, "the attempts table was not created"
    for column in ("model_selection", "model_configuration"):
        assert column in tables["runs"].columns, f"{column} was not added to runs"

    with _engine(phase2_db).connect() as conn:
        for run in conn.execute(tables["runs"].select()):
            assert run.model_configuration is None, "a historical run was given a provider"
        assert len(conn.execute(tables["provider_attempts"].select()).fetchall()) == 0


def test_downgrade_refuses_while_phase3_data_exists(phase2_db, alembic_ini):
    """Erasing a recorded snapshot is a decision, not a default."""
    seeded = _seed_phase2_rows(phase2_db)
    _upgrade(alembic_ini, "head")

    run_id = next(iter(seeded["turns"].values()))
    _record_an_attempt(phase2_db, run_id, ordinal=1)

    result = _alembic(alembic_ini, "downgrade", PHASE2_HEAD)
    assert result.returncode != 0, "a non-empty Phase 3 database must not downgrade silently"
    assert "refusing to downgrade" in result.stderr

    tables = _tables(phase2_db)
    with _engine(phase2_db).connect() as conn:
        assert len(conn.execute(tables["runs"].select()).fetchall()) == 3
        assert len(conn.execute(tables["provider_attempts"].select()).fetchall()) == 1


def test_an_empty_database_downgrades_cleanly(phase2_db, alembic_ini):
    """With nothing to protect, the reversal is clean."""
    _upgrade(alembic_ini, "head")

    result = _alembic(alembic_ini, "downgrade", PHASE2_HEAD)
    assert result.returncode == 0, f"empty downgrade failed: {result.stderr}"

    tables = _tables(phase2_db)
    assert "provider_attempts" not in tables, "the attempts table should be dropped"
    assert "model_configuration" not in tables["runs"].columns, "the snapshot column should be dropped"


def test_an_attempt_ordinal_is_unique_per_run(phase2_db, alembic_ini):
    """One run cannot hold two attempts with the same ordinal."""
    from sqlalchemy.exc import IntegrityError

    seeded = _seed_phase2_rows(phase2_db)
    _upgrade(alembic_ini, "head")

    run_id = next(iter(seeded["turns"].values()))
    _record_an_attempt(phase2_db, run_id, ordinal=1)
    with pytest.raises(IntegrityError):
        _record_an_attempt(phase2_db, run_id, ordinal=1)
