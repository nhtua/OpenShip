"""PostgreSQL scope and encrypted credential checks for managed search."""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfigV2,
    ToolCommandRequest,
    decode_execution_config,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.connectors.credentials.broker import (
    ConnectorCredentialError,
    SearchCredentialBroker,
)
from src.openship.connectors.models import Connector
from src.openship.connectors.service import create_connector
from src.openship.events.service import Event
from src.openship.runs.models import Outbox, Run
from src.openship.runs.service import submit_tool_run
from src.openship.workspace.models import Project
from tests.integration.postgres import bounded_postgres_url

pytestmark = [pytest.mark.postgres]

API_KEY = "synthetic-managed-search-postgres-key"  # rayloc:ignore


@pytest.fixture(scope="module")
def pg_sessions():
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip("no TEST_POSTGRES_URL; managed search PostgreSQL checks are opt-in")

    bounded_url = bounded_postgres_url(service_url)
    parsed = make_url(bounded_url)
    lane_name = "phase5_managed_search_lane"
    lane_url = parsed.set(database=lane_name).render_as_string(hide_password=False)
    admin = create_engine(service_url).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{lane_name}"'))
            connection.execute(text(f'CREATE DATABASE "{lane_name}"'))
    finally:
        admin.dispose()

    api_root = Path(__file__).parents[2]
    config = Config()
    config.set_main_option("script_location", str(api_root / "alembic"))
    config.set_main_option("sqlalchemy.url", lane_url.replace("%", "%%"))
    command.upgrade(config, "head")

    engine = create_engine(lane_url, pool_size=4, max_overflow=2)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield sessions
    finally:
        engine.dispose()
        admin = create_engine(service_url).execution_options(
            isolation_level="AUTOCOMMIT"
        )
        try:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{lane_name}"'))
        finally:
            admin.dispose()


def _install_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    encryption_path = tmp_path / "managed-search-encryption.json"
    hmac_path = tmp_path / "managed-search-hmac.json"
    for path, key in ((encryption_path, b"e" * 32), (hmac_path, b"h" * 32)):
        path.write_text(
            json.dumps(
                {
                    "active_key_id": "managed-search-pg-v1",
                    "keys": {
                        "managed-search-pg-v1": base64.b64encode(key).decode("ascii")
                    },
                }
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption_path)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)


def _identity(db, label: str):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        username=f"managed-search-{label}-{user_id.hex[:10]}",
        email=f"managed-search-{user_id.hex}@example.test",
        password_hash="test-only",
    )
    project = Project(
        id=uuid.uuid4(), owner_user_id=user_id, name=f"Managed search {label}"
    )
    db.add_all([user, project])
    db.flush()
    return user, project


def test_postgres_pins_and_decrypts_a_shared_global_search_connector(
    pg_sessions, monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    with pg_sessions() as db:
        owner, project = _identity(db, "owner")
        _other_owner, other_project = _identity(db, "other")
        monkeypatch.setattr(settings, "connector_operator_user_id", owner.id)
        conversation = Conversation(
            user_id=owner.id,
            project_id=project.id,
            title="Managed search PostgreSQL test",
        )
        db.add(conversation)
        db.flush()
        result = create_connector(
            db,
            actor=owner,
            visibility="global",
            project_id=None,
            kind="brave",
            name="Shared Brave",
            configuration_raw={"schema_version": 1, "kind": "brave"},
            api_key=API_KEY,
        )
        connector_id = uuid.UUID(result["id"])
        manifest = next(
            item
            for item in list_capability_manifests()
            if item.id == "brave_web_search"
        )
        command = ToolCommandRequest(
            client_request_id=uuid.uuid4(),
            capability=CapabilityRef(
                id=manifest.id,
                version=manifest.version,
                manifest_digest=manifest.manifest_digest,
            ),
            arguments={"query": "managed search authority", "max_results": 2},
            connector_selections={"brave": connector_id},
        )
        run = submit_tool_run(db, owner.id, conversation.id, command)
        db.commit()

        execution = decode_execution_config(run.execution_configuration)
        assert isinstance(execution, ExecutionConfigV2)
        assert run.graph_version == 2
        assert len(execution.connector_refs) == 1
        pinned = execution.connector_refs[0]
        assert pinned.id == connector_id
        project_id = project.id
        other_project_id = other_project.id
        durable_values = " ".join(
            str(getattr(run, column.name))
            for column in Run.__table__.columns
            if getattr(run, column.name) is not None
        )
        durable_values += " " + " ".join(
            event.payload for event in db.query(Event).filter(Event.run_id == run.id)
        )
        durable_values += " " + " ".join(
            row.payload
            for row in db.query(Outbox).filter(
                Outbox.conversation_id == conversation.id
            )
        )
        assert API_KEY not in durable_values

    broker = SearchCredentialBroker(pg_sessions)
    assert (
        broker.acquire(project_id=project_id, kind="brave", reference=pinned) == API_KEY
    )

    assert broker.acquire(
        project_id=other_project_id, kind="brave", reference=pinned
    ) == API_KEY

    with pg_sessions() as db:
        connector = db.get(Connector, connector_id)
        connector.revoked_at = datetime.now(UTC)
        db.commit()

    with pytest.raises(ConnectorCredentialError) as revoked:
        broker.acquire(project_id=project_id, kind="brave", reference=pinned)
    assert revoked.value.code == "connector_revoked"
