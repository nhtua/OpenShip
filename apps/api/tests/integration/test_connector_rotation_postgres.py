"""PostgreSQL verifies resumable credential-key rotation across commits."""

from __future__ import annotations

import base64
import json
import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from src.openship.auth.models import User
from src.openship.config import settings
from src.openship.connectors.credentials.dependencies import get_credential_store
from src.openship.connectors.credentials.keys import load_encryption_keyring
from src.openship.connectors.credentials.rotation import (
    reencrypt_credentials_batch,
    retire_encryption_key,
    rotate_key_file,
    verify_retained_credentials,
)
from src.openship.connectors.models import CredentialVersion
from src.openship.connectors.service import create_connector
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]


def _write_keyring(path: Path, key_id: str, key: bytes) -> None:
    path.write_text(
        json.dumps(
            {
                "active_key_id": key_id,
                "keys": {key_id: base64.b64encode(key).decode("ascii")},
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


@pytest.fixture
def pg_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; credential rotation requires PostgreSQL")
    bounded = bounded_postgres_url(service_url)
    parsed = make_url(bounded)
    database = f"connector_rotation_{uuid.uuid4().hex[:12]}"
    database_url = parsed.set(database=database).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    config = Config()
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")

    engine = create_engine(database_url, pool_size=4, max_overflow=2)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    encryption_path = tmp_path / "encryption-keys.json"
    hmac_path = tmp_path / "hmac-keys.json"
    _write_keyring(encryption_path, "enc-v1", b"e" * 32)
    _write_keyring(hmac_path, "hmac-v1", b"h" * 32)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_path)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)
    try:
        yield sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(
            isolation_level="AUTOCOMMIT"
        )
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        finally:
            admin.dispose()


def test_rotation_resumes_across_postgres_commits_and_retires_only_after_verification(
    pg_sessions, monkeypatch,
):
    with pg_sessions() as db:
        user = User(
            username=f"rotation-{uuid.uuid4().hex[:12]}",
            email=f"{uuid.uuid4().hex}@example.test",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Rotation integration")
        db.add(project)
        db.flush()
        monkeypatch.setattr(settings, "connector_operator_username", user.username)
        connectors = []
        for kind, secret in (
            ("brave", "synthetic-brave-rotation-sentinel"),
            ("tavily", "synthetic-tavily-rotation-sentinel"),
        ):
            result = create_connector(
                db,
                actor=user,
                visibility="global",
                project_id=None,
                kind=kind,
                name=f"{kind} integration key",
                configuration_raw={"schema_version": 1, "kind": kind},
                api_key=secret,
            )
            connectors.append(uuid.UUID(result["id"]))
        db.commit()

        original = {
            row.connector_id: (row.id, row.nonce)
            for row in db.query(CredentialVersion)
            .filter(CredentialVersion.connector_id.in_(connectors))
            .all()
        }
        assert len(original) == 2

    encryption_path = settings.connector_encryption_key_file
    rotate_key_file(encryption_path, key_id="enc-v2", key_kind="encryption")
    store = get_credential_store()

    with pg_sessions() as first_batch:
        assert reencrypt_credentials_batch(
            first_batch,
            store,
            installation_id=settings.connector_installation_id,
            batch_size=1,
        ) == 1
    # A fresh session models process/command restart. The remaining old row is
    # selected by its key ID, not an in-memory cursor.
    with pg_sessions() as restarted:
        rows = (
            restarted.query(CredentialVersion)
            .filter(CredentialVersion.connector_id.in_(connectors))
            .all()
        )
        assert sorted(row.key_id for row in rows) == ["enc-v1", "enc-v2"]
        assert reencrypt_credentials_batch(
            restarted,
            store,
            installation_id=settings.connector_installation_id,
            batch_size=1,
        ) == 1
        rows = (
            restarted.query(CredentialVersion)
            .filter(CredentialVersion.connector_id.in_(connectors))
            .all()
        )
        assert {row.key_id for row in rows} == {"enc-v2"}
        assert all(row.id == original[row.connector_id][0] for row in rows)
        assert all(row.nonce != original[row.connector_id][1] for row in rows)
        assert verify_retained_credentials(
            restarted, store, installation_id=settings.connector_installation_id
        ) == 2
        retire_encryption_key(
            restarted,
            encryption_path,
            key_id="enc-v1",
            installation_id=settings.connector_installation_id,
        )
        assert load_encryption_keyring(encryption_path).key_ids == ("enc-v2",)
