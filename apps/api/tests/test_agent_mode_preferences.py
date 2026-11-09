"""Own-account agent-mode preference API, revision CAS and migration defaults."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from src.openship.auth.models import User
from src.openship.auth.service import create_jwt
from src.openship.database.session import SessionLocal

pytestmark = pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _register(client, username: str | None = None) -> dict[str, str]:
    username = username or _unique("agentmode")
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.test",
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _alembic_ini(tmp_path: Path, db_path: Path) -> Path:
    api_root = Path(__file__).parents[1]
    path = tmp_path / "alembic.ini"
    path.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
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
    )
    return path


def _run_alembic(ini: Path, *args: str) -> subprocess.CompletedProcess:
    api_root = Path(__file__).parents[1]
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        cwd=api_root,
        env={"PYTHONPATH": str(api_root / "src"), "ALEMBIC_CONFIG": str(ini)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_existing_and_new_accounts_default_copilot(client, auth_token, authorized_client):
    registered = client.get(
        "/api/auth/preferences", headers=authorized_client
    )
    assert registered.status_code == 200
    assert registered.json() == {"agent_mode": "copilot", "revision": 1}

    # An account created directly (as an installation migration leaves it)
    # still reads Copilot from the positive column default.
    with SessionLocal() as db:
        user = User(
            username=_unique("direct"),
            email=f"{_unique('direct')}@example.test",
            password_hash="hash",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_jwt(user.id, user.username)
        user_id = user.id

    assert user_id is not None
    direct = client.get(
        "/api/auth/preferences",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert direct.status_code == 200
    assert direct.json() == {"agent_mode": "copilot", "revision": 1}


def test_preference_cas_and_cross_account_isolation(client, authorized_client):
    other_headers = _register(client)

    original = client.get(
        "/api/auth/preferences", headers=authorized_client
    )
    assert original.json() == {"agent_mode": "copilot", "revision": 1}

    # Saving the same mode is a no-op, not a revision increment.
    same = client.patch(
        "/api/auth/preferences",
        json={"agent_mode": "copilot", "expected_revision": 1},
        headers=authorized_client,
    )
    assert same.status_code == 200
    assert same.json() == {"agent_mode": "copilot", "revision": 1}

    changed = client.patch(
        "/api/auth/preferences",
        json={"agent_mode": "auto", "expected_revision": 1},
        headers=authorized_client,
    )
    assert changed.status_code == 200
    assert changed.json() == {"agent_mode": "auto", "revision": 2}

    # A second writer that still holds revision 1 must lose visibly.
    stale_patch = client.patch(
        "/api/auth/preferences",
        json={"agent_mode": "copilot", "expected_revision": 1},
        headers=authorized_client,
    )
    assert stale_patch.status_code == 409
    assert stale_patch.json()["error"]["code"] == "stale_agent_preference"

    # The account is identified by the authenticated token, never by a body ID.
    spoofed = client.patch(
        "/api/auth/preferences",
        json={
            "agent_mode": "copilot",
            "expected_revision": 2,
            "user_id": str(uuid.uuid4()),
        },
        headers=authorized_client,
    )
    assert spoofed.status_code == 422

    other = client.get("/api/auth/preferences", headers=other_headers)
    assert other.json() == {"agent_mode": "copilot", "revision": 1}
    other_update = client.patch(
        "/api/auth/preferences",
        json={"agent_mode": "auto", "expected_revision": 1},
        headers=other_headers,
    )
    assert other_update.status_code == 200
    assert other_update.json() == {"agent_mode": "auto", "revision": 2}

    unchanged = client.get(
        "/api/auth/preferences", headers=authorized_client
    )
    assert unchanged.json() == {"agent_mode": "auto", "revision": 2}


def test_migration_031_to_032_defaults_existing_accounts_to_copilot(tmp_path):
    db_path = tmp_path / "agent-mode.db"
    ini = _alembic_ini(tmp_path, db_path)
    assert _run_alembic(ini, "upgrade", "phase7a031").returncode == 0

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, 'legacy-agent-mode', 'legacy@example.test', 'hash')"
            ),
            {"id": "10000000-0000-0000-0000-0000000000a1"},
        )

    upgraded = _run_alembic(ini, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    with engine.begin() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == "phase7a032"
        legacy = connection.execute(
            text(
                "SELECT agent_mode, agent_mode_revision FROM users "
                "WHERE username='legacy-agent-mode'"
            )
        ).one()
        assert tuple(legacy) == ("copilot", 1)
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES (:id, 'new-agent-mode', 'new@example.test', 'hash')"
            ),
            {"id": "10000000-0000-0000-0000-0000000000a2"},
        )
        created = connection.execute(
            text(
                "SELECT agent_mode, agent_mode_revision FROM users "
                "WHERE username='new-agent-mode'"
            )
        ).one()
        assert tuple(created) == ("copilot", 1)

    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    assert {"agent_mode", "agent_mode_revision"} <= columns

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, email, password_hash, agent_mode, agent_mode_revision) "
                "VALUES (:id, 'bad-agent-mode', 'bad@example.test', 'hash', 'turbo', 1)"
            ),
            {"id": "10000000-0000-0000-0000-0000000000a3"},
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, email, password_hash, agent_mode, agent_mode_revision) "
                "VALUES (:id, 'bad-agent-revision', 'badrev@example.test', 'hash', 'copilot', 0)"
            ),
            {"id": "10000000-0000-0000-0000-0000000000a4"},
        )
    engine.dispose()
