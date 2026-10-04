"""Integration tests for the Phase 2 durable workspace migration.

These tests verify:
1. Migration works on an empty database
2. Migration works on a seeded Phase 1 database with existing users, conversations, and messages
3. Project ownership is enforced after migration
4. Conversation project_id is backfilled and becomes non-nullable
5. Historical messages retain null run_id

These tests use Alembic migrations directly and do not depend on the
ORM's create_all() for schema setup.
"""

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, MetaData, Table, Column, String, Text, DateTime, ForeignKey, func


def _run_alembic(alembic_ini: Path, *args: str) -> subprocess.CompletedProcess:
    """Run an alembic command against the given alembic.ini."""
    env = {
        "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
        "ALEMBIC_CONFIG": str(alembic_ini),
    }
    cmd = [sys.executable, "-m", "alembic", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).parents[2],
        timeout=60,
    )


@pytest.fixture
def alembic_ini(tmp_path):
    """Create a temporary alembic.ini pointing to a test SQLite database."""
    db_path = tmp_path / "test_phase2.db"
    ini_content = f"""[alembic]
script_location = alembic
sqlalchemy.url = sqlite:///{db_path}

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
    ini_path = tmp_path / "alembic.ini"
    ini_path.write_text(ini_content)
    return ini_path, db_path


@pytest.fixture
def seeded_phase1_db(alembic_ini):
    """Seed a Phase 1-style database with two users and conversations.

    First applies the Phase 1 migration (abc123), then seeds data.
    Uses raw SQL for dialect-agnostic UUID handling.
    """
    ini_path, db_path = alembic_ini

    # Apply Phase 1 migration
    result = _run_alembic(ini_path, "upgrade", "abc123")
    assert result.returncode == 0, f"Phase 1 migration failed: {result.stderr}"

    # Use raw SQL for seeding to avoid UUID type issues across dialects
    engine = create_engine(f"sqlite:///{db_path}")

    user1_id = str(uuid.uuid4())
    user2_id = str(uuid.uuid4())
    conv1_id = str(uuid.uuid4())
    conv2_id = str(uuid.uuid4())
    conv3_id = str(uuid.uuid4())
    msg1_id = str(uuid.uuid4())
    msg2_id = str(uuid.uuid4())
    msg3_id = str(uuid.uuid4())
    msg4_id = str(uuid.uuid4())

    with engine.connect() as conn:
        conn.execute(sa.text(
            "INSERT INTO users (id, username, email, password_hash) VALUES (:id, :username, :email, :hash)"
        ), {"id": user1_id, "username": "alice", "email": "alice@example.com", "hash": "hashed1"})
        conn.execute(sa.text(
            "INSERT INTO users (id, username, email, password_hash) VALUES (:id, :username, :email, :hash)"
        ), {"id": user2_id, "username": "bob", "email": "bob@example.com", "hash": "hashed2"})

        conn.execute(sa.text(
            "INSERT INTO conversations (id, user_id, title) VALUES (:id, :user_id, :title)"
        ), {"id": conv1_id, "user_id": user1_id, "title": "Alice's Chat 1"})
        conn.execute(sa.text(
            "INSERT INTO conversations (id, user_id, title) VALUES (:id, :user_id, :title)"
        ), {"id": conv2_id, "user_id": user1_id, "title": "Alice's Chat 2"})
        conn.execute(sa.text(
            "INSERT INTO conversations (id, user_id, title) VALUES (:id, :user_id, :title)"
        ), {"id": conv3_id, "user_id": user2_id, "title": "Bob's Chat"})

        conn.execute(sa.text(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (:id, :conv_id, :role, :content)"
        ), {"id": msg1_id, "conv_id": conv1_id, "role": "user", "content": "Hi from Alice"})
        conn.execute(sa.text(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (:id, :conv_id, :role, :content)"
        ), {"id": msg2_id, "conv_id": conv1_id, "role": "assistant", "content": "Hello Alice!"})
        conn.execute(sa.text(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (:id, :conv_id, :role, :content)"
        ), {"id": msg3_id, "conv_id": conv2_id, "role": "user", "content": "Second chat from Alice"})
        conn.execute(sa.text(
            "INSERT INTO messages (id, conversation_id, role, content) VALUES (:id, :conv_id, :role, :content)"
        ), {"id": msg4_id, "conv_id": conv3_id, "role": "user", "content": "Hi from Bob"})
        conn.commit()

    return {
        "ini_path": ini_path,
        "db_path": db_path,
        "user1_id": user1_id,
        "user2_id": user2_id,
        "conv1_id": conv1_id,
        "conv2_id": conv2_id,
        "conv3_id": conv3_id,
        "msg1_id": msg1_id,
        "msg2_id": msg2_id,
        "msg3_id": msg3_id,
        "msg4_id": msg4_id,
    }


def test_migration_on_empty_db(alembic_ini):
    """Migration should succeed on an empty database (both migrations)."""
    ini_path, db_path = alembic_ini
    result = _run_alembic(ini_path, "upgrade", "head")
    assert result.returncode == 0, f"Migration failed on empty DB: {result.stderr}"

    # Verify Phase 2 tables exist
    engine = create_engine(f"sqlite:///{db_path}")
    meta = MetaData()
    meta.reflect(engine)
    assert "projects" in meta.tables, "projects table missing"
    assert "runs" in meta.tables, "runs table missing"
    assert "run_jobs" in meta.tables, "run_jobs table missing"
    assert "events" in meta.tables, "events table missing"
    assert "outbox" in meta.tables, "outbox table missing"


def test_migration_backfills_phase1_data(seeded_phase1_db):
    """Migration should backfill project_id for all conversations and create projects."""
    result = _run_alembic(seeded_phase1_db["ini_path"], "upgrade", "head")
    assert result.returncode == 0, f"Migration failed on seeded DB: {result.stderr}"

    # Verify projects were created (one per user)
    engine = create_engine(f"sqlite:///{seeded_phase1_db['db_path']}")
    meta = MetaData()
    meta.reflect(engine)

    # Check projects table exists
    assert "projects" in meta.tables, "projects table was not created"

    # Check conversations have project_id
    assert "project_id" in meta.tables["conversations"].columns, "project_id column not added to conversations"

    # Verify project ownership: each conversation should map to its owner's project
    with engine.connect() as conn:
        projects = conn.execute(meta.tables["projects"].select()).fetchall()
        assert len(projects) == 2, f"Expected 2 projects (one per user), got {len(projects)}"

        # Build user -> project mapping
        project_by_owner = {p.owner_user_id: p for p in projects}

        conversations = conn.execute(meta.tables["conversations"].select()).fetchall()
        assert len(conversations) == 3, f"Expected 3 conversations, got {len(conversations)}"

        for conv in conversations:
            assert conv.project_id is not None, f"Conversation {conv.id} has null project_id"
            # Find the user who owns this conversation
            users = conn.execute(
                meta.tables["users"].select().where(
                    meta.tables["users"].c.id == conv.user_id
                )
            ).fetchall()
            assert users, f"User {conv.user_id} not found"
            user = users[0]
            # The conversation should be owned by the user's project
            assert user.id in project_by_owner, f"User {user.id} has no project"
            assert conv.project_id == project_by_owner[user.id].id, (
                f"Conversation {conv.id} has wrong project_id: {conv.project_id} "
                f"expected {project_by_owner[user.id].id}"
            )


def test_historical_messages_retain_ids(seeded_phase1_db):
    """Migration should preserve all original message IDs and content."""
    result = _run_alembic(seeded_phase1_db["ini_path"], "upgrade", "head")
    assert result.returncode == 0, f"Migration failed: {result.stderr}"

    engine = create_engine(f"sqlite:///{seeded_phase1_db['db_path']}")
    meta = MetaData()
    meta.reflect(engine)

    with engine.connect() as conn:
        messages = conn.execute(meta.tables["messages"].select()).fetchall()
        assert len(messages) == 4, f"Expected 4 messages, got {len(messages)}"

        # All original IDs preserved (compare as strings)
        ids = {str(m.id) for m in messages}
        expected_ids = {
            seeded_phase1_db["msg1_id"],
            seeded_phase1_db["msg2_id"],
            seeded_phase1_db["msg3_id"],
            seeded_phase1_db["msg4_id"],
        }
        assert ids == expected_ids, f"Message IDs not preserved. Expected {expected_ids}, got {ids}"

        # Historical messages should have null run_id
        for msg in messages:
            assert msg.run_id is None, f"Historical message {msg.id} should have null run_id"


def test_foreign_project_join_fails(seeded_phase1_db):
    """Conversations from one project should not be joinable to another project."""
    result = _run_alembic(seeded_phase1_db["ini_path"], "upgrade", "head")
    assert result.returncode == 0, f"Migration failed: {result.stderr}"

    engine = create_engine(f"sqlite:///{seeded_phase1_db['db_path']}")
    meta = MetaData()
    meta.reflect(engine)

    with engine.connect() as conn:
        # Get user1's conversations and user2's project
        user1_convs = conn.execute(
            meta.tables["conversations"].select().where(
                meta.tables["conversations"].c.user_id == seeded_phase1_db["user1_id"]
            )
        ).fetchall()

        projects = conn.execute(meta.tables["projects"].select()).fetchall()
        project_by_owner = {p.owner_user_id: p.id for p in projects}

        user2_project_id = project_by_owner[seeded_phase1_db["user2_id"]]

        # Try joining user1's conversation to user2's project — should fail FK constraint
        conv = user1_convs[0]
        try:
            conn.execute(
                meta.tables["conversations"].update().where(
                    meta.tables["conversations"].c.id == conv.id
                ).values(project_id=user2_project_id)
            )
            conn.commit()
            assert False, "FK constraint did not prevent foreign project join"
        except Exception as e:
            # FK violation expected
            conn.rollback()


def test_downgrade_restores_phase1_schema(seeded_phase1_db):
    """Downgrading should restore the Phase 1 schema."""
    # First upgrade
    result = _run_alembic(seeded_phase1_db["ini_path"], "upgrade", "head")
    assert result.returncode == 0, f"Migration failed: {result.stderr}"

    # Then downgrade
    result = _run_alembic(seeded_phase1_db["ini_path"], "downgrade", "abc123")
    assert result.returncode == 0, f"Downgrade failed: {result.stderr}"

    engine = create_engine(f"sqlite:///{seeded_phase1_db['db_path']}")
    meta = MetaData()
    meta.reflect(engine)

    # Phase 2 tables should be gone
    assert "projects" not in meta.tables, "projects table should be dropped on downgrade"
    assert "runs" not in meta.tables, "runs table should be dropped on downgrade"

    # Phase 1 tables should still exist
    assert "users" in meta.tables, "users table should still exist"
    assert "conversations" in meta.tables, "conversations table should still exist"
    assert "messages" in meta.tables, "messages table should still exist"

    # Conversations should not have project_id anymore
    assert "project_id" not in meta.tables["conversations"].columns, "project_id should be dropped"