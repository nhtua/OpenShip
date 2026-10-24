"""Operator lifecycle commands never print or persist plaintext credentials."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.capabilities.contracts import ConnectorRef
from src.openship.config import settings
from src.openship.connectors.cli import main
from src.openship.connectors.credentials.broker import (
    ConnectorCredentialError,
    SearchCredentialBroker,
)
from src.openship.connectors.credentials.dependencies import get_credential_store
from src.openship.connectors.credentials.keys import (
    load_encryption_keyring,
    load_hmac_keyring,
)
from src.openship.connectors.credentials.recovery import (
    managed_dispatch_is_locked,
    set_managed_dispatch_lock,
)
from src.openship.connectors.credentials.rotation import (
    initialize_key_files,
    reencrypt_credentials_batch,
    retire_encryption_key,
    retire_hmac_key,
    rotate_key_file,
    verify_retained_credentials,
)
from src.openship.connectors.credentials.store import (
    CredentialContext,
    CredentialEnvelope,
)
from src.openship.connectors.legacy import (
    import_legacy_search_connectors,
    preview_legacy_search_import,
)
from src.openship.connectors.models import (
    Connector,
    ConnectorRequestLedger,
    CredentialVersion,
    ProjectConnectorDefault,
)
from src.openship.connectors.service import ConnectorCommandError, set_default
from src.openship.database.models import Base
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import Project


def _write_keyring(path, key_id: str, key: bytes) -> None:
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


def _install_keys(monkeypatch, tmp_path) -> tuple[bytes, bytes]:
    encryption = tmp_path / "encryption.json"
    hmac = tmp_path / "hmac.json"
    encryption_key = b"e" * 32
    hmac_key = b"h" * 32
    _write_keyring(encryption, "enc-v1", encryption_key)
    _write_keyring(hmac, "hmac-v1", hmac_key)
    monkeypatch.setattr(settings, "connector_installation_id", uuid.uuid4())
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac)
    return encryption_key, hmac_key


def test_key_initialization_is_exclusive_protected_and_does_not_print_material(
    tmp_path, capsys
):
    encryption = tmp_path / "keys" / "encryption.json"
    hmac = tmp_path / "keys" / "hmac.json"
    encryption.parent.mkdir(mode=0o700)

    initialize_key_files(encryption, hmac)
    encryption_ring = load_encryption_keyring(encryption)
    hmac_document = json.loads(hmac.read_text(encoding="utf-8"))

    assert encryption.stat().st_mode & 0o777 == 0o600
    assert hmac.stat().st_mode & 0o777 == 0o600
    assert len(encryption_ring.key(encryption_ring.active_key_id)) == 32
    assert len(base64.b64decode(hmac_document["keys"][hmac_document["active_key_id"]])) >= 32
    with pytest.raises(FileExistsError):
        initialize_key_files(encryption, hmac)
    assert capsys.readouterr().out == ""


def test_key_rotation_adds_a_new_active_key_without_retiring_old_versions(tmp_path):
    path = tmp_path / "encryption.json"
    old_key = b"o" * 32
    _write_keyring(path, "enc-v1", old_key)

    rotate_key_file(path, key_id="enc-v2", key_kind="encryption")
    ring = load_encryption_keyring(path)

    assert ring.active_key_id == "enc-v2"
    assert set(ring.key_ids) == {"enc-v1", "enc-v2"}
    assert ring.key("enc-v1") == old_key
    assert len(ring.key("enc-v2")) == 32


def test_reencryption_resumes_by_key_id_preserves_version_and_verifies_all_rows(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'rotation.db'}")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = test_session()
    try:
        owner = User(
            username=f"rotation-{uuid.uuid4().hex[:12]}",
            email=f"{uuid.uuid4().hex}@example.test",
            password_hash="test-only",
        )
        db.add(owner)
        db.flush()
        project = Project(owner_user_id=owner.id, name="Rotation project")
        db.add(project)
        db.flush()
        monkeypatch.setattr(settings, "connector_operator_user_id", owner.id)
        create_result = import_legacy_search_connectors(
            db,
            actor=owner,
            visibility="global",
            project_id=None,
            services=("brave",),
            credentials={"brave": "synthetic-rotation-sentinel"},
        )[0]
        db.commit()

        connector_id = uuid.UUID(create_result["id"])
        before = (
            db.query(CredentialVersion)
            .filter(CredentialVersion.connector_id == connector_id)
            .one()
        )
        original_id = before.id
        original_nonce = before.nonce
        original_context = CredentialContext(
            installation_id=settings.connector_installation_id,
            visibility=before.visibility,
            project_id=before.project_id,
            connector_id=before.connector_id,
            credential_version_id=before.id,
            schema_version=before.schema_version,
            kind=before.kind,
        )
        original_payload = get_credential_store().decrypt(
            original_context,
            CredentialEnvelope(
                key_id=before.key_id, nonce=before.nonce, ciphertext=before.ciphertext
            ),
        )

        rotate_key_file(
            settings.connector_encryption_key_file,
            key_id="enc-v2",
            key_kind="encryption",
        )
        rotated_store = get_credential_store()
        assert reencrypt_credentials_batch(
            db,
            rotated_store,
            installation_id=settings.connector_installation_id,
            batch_size=1,
        ) == 1

        after = (
            db.query(CredentialVersion)
            .filter(CredentialVersion.connector_id == connector_id)
            .one()
        )
        assert after.id == original_id
        assert after.key_id == "enc-v2"
        assert after.nonce != original_nonce
        assert rotated_store.decrypt(
            original_context,
            CredentialEnvelope(
                key_id=after.key_id, nonce=after.nonce, ciphertext=after.ciphertext
            ),
        ) == original_payload
        assert reencrypt_credentials_batch(
            db,
            rotated_store,
            installation_id=settings.connector_installation_id,
            batch_size=1,
        ) == 0

        db.add(
            CredentialVersion(
                id=uuid.uuid4(),
                connector_id=connector_id,
                visibility="global",
                project_id=None,
                kind="brave_api_key",
                schema_version=1,
                key_id="purged",
                nonce=b"\x00" * 12,
                ciphertext=b"\x00" * 16,
                state="revoked",
                revoked_at=datetime.now(UTC),
                purged_at=datetime.now(UTC),
            )
        )
        db.commit()
        assert verify_retained_credentials(
            db, rotated_store, installation_id=settings.connector_installation_id
        ) == 1

        retire_encryption_key(
            db,
            settings.connector_encryption_key_file,
            key_id="enc-v1",
            installation_id=settings.connector_installation_id,
        )
        assert load_encryption_keyring(settings.connector_encryption_key_file).key_ids == (
            "enc-v2",
        )
        assert create_result["kind"] == "brave"
    finally:
        db.close()
        engine.dispose()


def test_managed_dispatch_lock_is_explicit_idempotent_and_plain_chat_independent(
    tmp_path, monkeypatch
):
    lock_path = tmp_path / "managed-dispatch.lock"
    monkeypatch.setattr(settings, "connector_dispatch_lock_file", lock_path)

    assert managed_dispatch_is_locked() is False
    set_managed_dispatch_lock(locked=True)
    set_managed_dispatch_lock(locked=True)
    assert managed_dispatch_is_locked() is True
    broker = SearchCredentialBroker(
        lambda: pytest.fail("a locked dispatch must not open a database session")
    )
    with pytest.raises(ConnectorCredentialError, match="connector_unavailable"):
        broker.validate(
            project_id=uuid.uuid4(),
            kind="brave",
            reference=ConnectorRef(
                id=uuid.uuid4(),
                kind="brave",
                configuration_revision=1,
                visibility="project",
                credential_version_id=uuid.uuid4(),
            ),
        )
    set_managed_dispatch_lock(locked=False)
    assert managed_dispatch_is_locked() is False


def test_cli_migration_preview_and_key_commands_never_print_source_values(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    encryption = tmp_path / "encryption.json"
    hmac_path = tmp_path / "hmac.json"
    lock_path = tmp_path / "managed-dispatch.lock"
    monkeypatch.setattr(settings, "connector_encryption_key_file", encryption)
    monkeypatch.setattr(settings, "connector_idempotency_hmac_key_file", hmac_path)
    monkeypatch.setattr(settings, "connector_dispatch_lock_file", lock_path)
    assert main(
        ["key-init", "--encryption-file", str(encryption), "--hmac-file", str(hmac_path)]
    ) == 0
    output = capsys.readouterr().out
    key_document = json.loads(encryption.read_text(encoding="utf-8"))
    generated_key = key_document["keys"][key_document["active_key_id"]]
    assert generated_key not in output
    assert main(["dispatch", "lock"]) == 0
    assert main(
        ["key-rotate", "--key-kind", "encryption", "--key-id", "enc-v2"]
    ) == 0
    assert load_encryption_keyring(encryption).active_key_id == "enc-v2"
    assert main(["dispatch", "unlock"]) == 0
    assert generated_key not in capsys.readouterr().out

    env_file = tmp_path / "legacy.env"
    env_file.write_text(
        "BRAVE_SEARCH_API_KEY=synthetic-cli-import-sentinel\n", encoding="utf-8"
    )
    assert main(
        [
            "search-import-preview",
            "--visibility",
            "global",
            "--service",
            "brave",
            "--env-file",
            str(env_file),
        ]
    ) == 0
    preview = capsys.readouterr().out
    assert "synthetic-cli-import-sentinel" not in preview
    assert '"credential_available": true' in preview
    assert '"will_set_default": false' in preview


def test_legacy_project_search_preview_is_rejected():
    with pytest.raises(ConnectorCommandError) as refused:
        preview_legacy_search_import(
            services=("brave", "tavily"),
            visibility="project",
            project_id=uuid.uuid4(),
            default_services=("brave",),
            available_services=("brave",),
        )
    assert refused.value.code == "connector_visibility_unsupported"


def test_legacy_global_search_preview_is_explicit_and_never_returns_credentials():
    preview = preview_legacy_search_import(
        services=("brave", "tavily"),
        visibility="global",
        project_id=None,
        available_services=("brave",),
    )

    assert [row["service"] for row in preview] == ["brave", "tavily"]
    assert preview[0]["will_set_default"] is False
    assert preview[0]["credential_available"] is True
    assert preview[1]["credential_available"] is False
    assert all("api_key" not in row and "secret" not in row for row in preview)


def test_hmac_key_retirement_waits_for_the_idempotency_retry_window(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    hmac_path = settings.connector_idempotency_hmac_key_file
    rotate_key_file(hmac_path, key_id="hmac-v2", key_kind="hmac")
    db = SessionLocal()
    try:
        actor = User(
            username=f"hmac-{uuid.uuid4().hex[:12]}",
            email=f"{uuid.uuid4().hex}@example.test",
            password_hash="test-only",
        )
        db.add(actor)
        db.flush()
        row = ConnectorRequestLedger(
            visibility="global",
            project_id=None,
            actor_user_id=actor.id,
            key_id="hmac-v1",
            request_key_hmac="a" * 64,
            payload_hmac="b" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(row)
        db.commit()

        with pytest.raises(RuntimeError, match="hmac_retry_window_active"):
            retire_hmac_key(db, hmac_path, key_id="hmac-v1")

        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        retire_hmac_key(db, hmac_path, key_id="hmac-v1")
        assert load_hmac_keyring(hmac_path).key_ids == ("hmac-v2",)
    finally:
        db.close()


def test_global_legacy_import_requires_configured_operator(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "connector_operator_user_id", uuid.uuid4())
    db = SessionLocal()
    try:
        actor = User(
            username=f"operator-{uuid.uuid4().hex[:12]}",
            email=f"{uuid.uuid4().hex}@example.test",
            password_hash="test-only",
        )
        db.add(actor)
        db.flush()

        with pytest.raises(ConnectorCommandError, match="global_management_forbidden"):
            import_legacy_search_connectors(
                db,
                actor=actor,
                visibility="global",
                project_id=None,
                services=("brave",),
                credentials={"brave": "synthetic-brave-sentinel"},
            )
    finally:
        db.rollback()
        db.close()


def test_project_legacy_import_is_refused_before_credential_lookup(monkeypatch, tmp_path):
    _install_keys(monkeypatch, tmp_path)

    class UnreadCredentials:
        def get(self, _service):
            pytest.fail("project-scoped migration must be refused before reading credentials")

    actor = User(
        id=uuid.uuid4(),
        username=f"legacy-{uuid.uuid4().hex[:12]}",
        email=f"{uuid.uuid4().hex}@example.test",
        password_hash="test-only",
    )
    with SessionLocal() as db, pytest.raises(ConnectorCommandError) as refused:
        import_legacy_search_connectors(
            db,
            actor=actor,
            visibility="project",
            project_id=uuid.uuid4(),
            services=("brave",),
            credentials=UnreadCredentials(),
        )
    assert refused.value.code == "connector_visibility_unsupported"


def test_cli_project_import_preview_is_refused_before_reading_env_file(
    tmp_path, capsys
):
    absent_env_file = tmp_path / "must-not-be-read.env"
    result = main(
        [
            "search-import-preview",
            "--visibility",
            "project",
            "--project-id",
            str(uuid.uuid4()),
            "--service",
            "brave",
            "--env-file",
            str(absent_env_file),
        ]
    )

    assert result == 2
    assert capsys.readouterr().err == "connector_visibility_unsupported\n"
    assert not absent_env_file.exists()


def test_cli_checks_global_authority_before_loading_legacy_environment_file(
    monkeypatch, tmp_path, capsys
):
    db = SessionLocal()
    actor = User(
        username=f"cli-operator-{uuid.uuid4().hex[:12]}",
        email=f"{uuid.uuid4().hex}@example.test",
        password_hash="test-only",
    )
    db.add(actor)
    db.commit()
    actor_id = actor.id
    db.close()
    monkeypatch.setattr(settings, "connector_operator_user_id", uuid.uuid4())
    absent_env_file = tmp_path / "must-not-be-read.env"

    result = main(
        [
            "search-import",
            "--visibility",
            "global",
            "--service",
            "brave",
            "--actor-user-id",
            str(actor_id),
            "--env-file",
            str(absent_env_file),
        ]
    )

    assert result == 2
    assert capsys.readouterr().err == "global_management_forbidden\n"
    assert not absent_env_file.exists()


def test_global_legacy_import_encrypts_selected_key_for_project_default(
    monkeypatch, tmp_path
):
    _install_keys(monkeypatch, tmp_path)
    db = SessionLocal()
    try:
        user = User(
            username=f"owner-{uuid.uuid4().hex[:12]}",
            email=f"{uuid.uuid4().hex}@example.test",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        project = Project(owner_user_id=user.id, name="Migration project")
        db.add(project)
        db.flush()
        monkeypatch.setattr(settings, "connector_operator_user_id", user.id)

        result = import_legacy_search_connectors(
            db,
            actor=user,
            visibility="global",
            project_id=None,
            services=("brave",),
            credentials={"brave": "synthetic-brave-migration-sentinel"},
        )
        set_default(
            db,
            project_id=project.id,
            actor=user,
            service="brave",
            expected_revision=0,
            connector_id=uuid.UUID(result[0]["id"]),
        )
        db.commit()

        connector = db.get(Connector, uuid.UUID(result[0]["id"]))
        credential = (
            db.query(CredentialVersion)
            .filter(CredentialVersion.connector_id == connector.id)
            .one()
        )
        default = (
            db.query(ProjectConnectorDefault)
            .filter(ProjectConnectorDefault.project_id == project.id)
            .one()
        )
        assert result[0]["id"] == str(connector.id)
        assert default.project_id == project.id
        assert default.connector_id == connector.id
        assert credential.project_id is None
        assert credential.visibility == "global"
        assert b"synthetic-brave-migration-sentinel" not in credential.ciphertext
        assert "synthetic-brave-migration-sentinel" not in repr(result)
    finally:
        db.close()
