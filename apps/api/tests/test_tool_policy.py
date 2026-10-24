from __future__ import annotations

from src.openship.capabilities.routes import router
from src.openship.config import Settings
from src.openship.database.models import Base


def test_deployment_ceiling_distinguishes_unset_empty_and_explicit_ids(monkeypatch):
    monkeypatch.delenv("TOOL_ENABLED_IDS", raising=False)
    unset = Settings(_env_file=None)
    assert getattr(unset, "tool_enabled_ceiling", "not-implemented") is None

    monkeypatch.setenv("TOOL_ENABLED_IDS", "")
    empty = Settings(_env_file=None)
    assert getattr(empty, "tool_enabled_ceiling", "not-implemented") == frozenset()

    monkeypatch.setenv("TOOL_ENABLED_IDS", "brave_web_search,unknown_tool")
    limited = Settings(_env_file=None)
    assert getattr(limited, "tool_enabled_ceiling", "not-implemented") == frozenset(
        {"brave_web_search", "unknown_tool"}
    )


def test_global_tool_policy_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/api/global-tool-policy" in paths
    assert "/api/global-tool-policy/{capability_id}" in paths
    assert "/api/global-tool-policy/cutover" in paths


def test_policy_state_and_override_tables_are_registered_with_database_metadata():
    assert "tool_policy_state" in Base.metadata.tables
    assert "tool_policy_overrides" in Base.metadata.tables
    assert "tool_policy_audit_events" in Base.metadata.tables


def _policy_module():
    from importlib import import_module

    import pytest

    try:
        return import_module("src.openship.capabilities.policy")
    except ModuleNotFoundError as missing:
        pytest.fail(f"shared tool policy module is missing: {missing.name}")


def test_operator_policy_is_shared_by_catalog_and_execution(tmp_path, monkeypatch):
    from importlib import import_module
    from uuid import uuid4

    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.openship.auth.models import User
    from src.openship.capabilities.catalog import (
        list_capability_manifests,
        resolve_capability,
    )
    from src.openship.capabilities.contracts import CapabilityRef
    from src.openship.config import settings
    from src.openship.execution.errors import CapabilityError

    policy_module = _policy_module()
    state_model = import_module("src.openship.capabilities.models").ToolPolicyState
    override_model = import_module("src.openship.capabilities.models").ToolPolicyOverride
    engine = create_engine(f"sqlite:///{tmp_path / 'tool-policy.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    actor = User(
        id=uuid4(), username="policy-operator", email="policy-operator@example.test", password_hash="test-only"
    )
    with Session() as db:
        db.add(actor)
        db.flush()
        db.add(state_model(id=1, revision=1))
        db.commit()
        monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
        monkeypatch.setattr(settings, "tool_enabled_ids", None)

        snapshot = policy_module.read_effective_tool_policy(db)
        installed_ids = {item.id for item in list_capability_manifests()}
        assert snapshot.installed_ids == installed_ids
        assert snapshot.effective_ids == installed_ids - {
            "aws_read_catalog",
            "aws_read",
        }

        changed = policy_module.set_tool_policy(
            db,
            actor=actor,
            capability_id="workspace_list_files",
            enabled=False,
            expected_revision=snapshot.revision,
        )
        assert changed.revision == snapshot.revision + 1
        assert "workspace_list_files" not in changed.effective_ids
        assert db.query(override_model).filter_by(
            capability_id="workspace_list_files", enabled=False
        ).one()
        with pytest.raises(policy_module.ToolPolicyError, match="disabled"):
            policy_module.require_current_tool_policy(
                db,
                frozenset({"workspace_list_files"}),
                pinned_revision=snapshot.revision,
            )

        with pytest.raises(policy_module.ToolPolicyError, match="revision_changed"):
            policy_module.set_tool_policy(
                db,
                actor=actor,
                capability_id="workspace_list_files",
                enabled=True,
                expected_revision=snapshot.revision,
            )

        manifest = next(
            item
            for item in list_capability_manifests()
            if item.id == "workspace_list_files"
        )
        ref = CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        with pytest.raises(CapabilityError, match="disabled"):
            resolve_capability(ref, changed.effective_ids)

        summaries = import_module("src.openship.capabilities.routes").get_capabilities(
            actor, db
        )
        api_workspace = next(
            item for item in summaries if item.id == "workspace_list_files"
        )
        assert api_workspace.enabled is False

        monkeypatch.setattr(settings, "connector_operator_user_id", uuid4())
        with pytest.raises(policy_module.ToolPolicyError, match="forbidden"):
            policy_module.set_tool_policy(
                db,
                actor=actor,
                capability_id="workspace_list_files",
                enabled=True,
                expected_revision=changed.revision,
            )
    engine.dispose()


def test_missing_policy_state_returns_no_effective_capabilities(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'missing-tool-policy.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        snapshot = _policy_module().read_effective_tool_policy(db)
        assert snapshot.state_available is False
        assert snapshot.revision == 0
        assert snapshot.effective_ids == frozenset()
    engine.dispose()


def test_aws_discovery_is_disabled_by_default_and_operator_opt_in(tmp_path, monkeypatch):
    from uuid import uuid4

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.openship.auth.models import User
    from src.openship.capabilities.models import ToolPolicyState
    from src.openship.config import settings

    policy_module = _policy_module()
    engine = create_engine(f"sqlite:///{tmp_path / 'aws-read-policy.db'}")
    Base.metadata.create_all(engine)
    actor = User(
        id=uuid4(),
        username="aws-policy-operator",
        email="aws-policy-operator@example.test",
        password_hash="test-only",
    )
    with sessionmaker(bind=engine)() as db:
        db.add(actor)
        db.add(ToolPolicyState(id=1, revision=1))
        db.commit()
        monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
        monkeypatch.setattr(settings, "tool_enabled_ids", None)

        initial = policy_module.read_effective_tool_policy(db)
        assert "aws_read_catalog" not in initial.effective_ids
        assert "aws_read" not in initial.effective_ids
        for capability_id in ("aws_read_catalog", "aws_read"):
            initial = policy_module.set_tool_policy(
                db,
                actor=actor,
                capability_id=capability_id,
                enabled=True,
                expected_revision=initial.revision,
            )
        assert {"aws_read_catalog", "aws_read"}.issubset(initial.effective_ids)
    engine.dispose()


def test_dotenv_empty_ceiling_is_preserved_without_changing_other_empty_settings(
    tmp_path, monkeypatch
):
    env_file = tmp_path / "tool-policy.env"
    env_file.write_text("TOOL_ENABLED_IDS=\nMODEL_MAX_TOKENS=\n", encoding="utf-8")
    monkeypatch.delenv("TOOL_ENABLED_IDS", raising=False)
    monkeypatch.delenv("MODEL_MAX_TOKENS", raising=False)

    configured = Settings(_env_file=env_file)

    assert configured.tool_enabled_ceiling == frozenset()
    assert configured.model_max_tokens == 4000


def test_legacy_cutover_preserves_current_effective_ids_after_ceiling_removal(
    tmp_path, monkeypatch
):
    from importlib import import_module
    from uuid import uuid4

    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.openship.auth.models import User
    from src.openship.config import settings

    policy_module = _policy_module()
    state_model = import_module("src.openship.capabilities.models").ToolPolicyState
    engine = create_engine(f"sqlite:///{tmp_path / 'tool-policy-cutover.db'}")
    Base.metadata.create_all(engine)
    actor = User(
        id=uuid4(), username="cutover-operator", email="cutover@example.test", password_hash="test-only"
    )
    with sessionmaker(bind=engine)() as db:
        db.add(actor)
        db.flush()
        db.add(state_model(id=1, revision=1))
        db.commit()
        monkeypatch.setattr(settings, "connector_operator_user_id", actor.id)
        monkeypatch.setattr(settings, "tool_enabled_ids", "workspace_list_files,unknown_tool")

        before = policy_module.read_effective_tool_policy(db)
        assert before.effective_ids == frozenset({"workspace_list_files"})
        assert before.unknown_deployment_ids == ("unknown_tool",)
        after = policy_module.cutover_legacy_tool_allowlist(
            db, actor=actor, expected_revision=before.revision
        )
        db.commit()
        assert after.effective_ids == before.effective_ids

        monkeypatch.setattr(settings, "tool_enabled_ids", None)
        managed = policy_module.read_effective_tool_policy(db)
        assert managed.effective_ids == before.effective_ids
        assert managed.legacy_cutover_at is not None
        with pytest.raises(policy_module.ToolPolicyError, match="cutover_complete"):
            policy_module.cutover_legacy_tool_allowlist(
                db, actor=actor, expected_revision=managed.revision
            )
    engine.dispose()
