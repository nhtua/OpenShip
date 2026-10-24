from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from src.openship.auth.models import User
from src.openship.connectors.contracts import ConnectorConfiguration
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
    ProjectConnectorDefault,
)
from src.openship.connectors.selection import (
    ConnectorSelectionError,
    resolve_connector,
)
from src.openship.connectors.service import ConnectorCommandError, set_default
from src.openship.database.session import SessionLocal
from src.openship.workspace.models import Project


def _scope(db: Session) -> tuple[User, Project]:
    user = User(
        id=uuid.uuid4(),
        username=f"connector-{uuid.uuid4().hex[:12]}",
        email=f"{uuid.uuid4().hex}@example.test",
        password_hash="unused",
    )
    db.add(user)
    db.flush()
    project = Project(id=uuid.uuid4(), owner_user_id=user.id, name="selection project")
    db.add(project)
    db.flush()
    return user, project


def _connector(
    db: Session,
    *,
    owner: User,
    project: Project | None,
    visibility: str,
    kind: str = "brave",
    enabled: bool = True,
    revoked_at=None,
) -> Connector:
    connector = Connector(
        id=uuid.uuid4(),
        kind=kind,
        visibility=visibility,
        project_id=project.id if project else None,
        name=f"{kind}-{uuid.uuid4().hex[:8]}",
        current_revision=1,
        enabled=enabled,
        revoked_at=revoked_at,
        managed_by_user_id=owner.id,
        created_by_user_id=owner.id,
    )
    db.add(connector)
    db.flush()
    credential = CredentialVersion(
        id=uuid.uuid4(),
        connector_id=connector.id,
        visibility=visibility,
        project_id=project.id if project else None,
        kind=f"{kind}_api_key",
        schema_version=1,
        key_id="fixture-key",
        nonce=b"n" * 12,
        ciphertext=b"c" * 16,
        state="active",
    )
    db.add(credential)
    db.flush()
    configuration = ConnectorConfiguration(kind=kind)
    db.add(
        ConnectorRevision(
            id=uuid.uuid4(),
            connector_id=connector.id,
            revision=1,
            schema_version=1,
            visibility=visibility,
            project_id=project.id if project else None,
            configuration_json=configuration.canonical_bytes().decode(),
            configuration_digest=configuration.digest(),
            credential_version_id=credential.id,
            created_by_user_id=owner.id,
        )
    )
    db.flush()
    return connector


def test_explicit_selection_overrides_default_and_returns_immutable_revision_ref():
    with SessionLocal() as db:
        owner, project = _scope(db)
        default = _connector(db, owner=owner, project=project, visibility="project")
        selected = _connector(db, owner=owner, project=project, visibility="project")
        db.add(
            ProjectConnectorDefault(
                id=uuid.uuid4(),
                project_id=project.id,
                service="brave",
                connector_id=default.id,
                connector_revision=1,
                settings_revision=1,
                updated_by_user_id=owner.id,
            )
        )
        db.flush()
        resolved = resolve_connector(
            db, project_id=project.id, service="brave", selected_id=selected.id
        )

        assert resolved.connector_id == selected.id
        assert resolved.revision == 1
        assert resolved.visibility == "project"
        assert resolved.credential_version_id is not None


def test_project_default_is_shared_across_search_services_and_can_be_switched():
    with SessionLocal() as db:
        owner, project = _scope(db)
        brave = _connector(
            db, owner=owner, project=None, visibility="global", kind="brave"
        )
        tavily = _connector(
            db, owner=owner, project=None, visibility="global", kind="tavily"
        )

        set_default(
            db,
            project_id=project.id,
            actor=owner,
            service="brave",
            expected_revision=0,
            connector_id=brave.id,
        )
        set_default(
            db,
            project_id=project.id,
            actor=owner,
            service="tavily",
            expected_revision=1,
            connector_id=tavily.id,
        )

        defaults = (
            db.query(ProjectConnectorDefault)
            .filter(ProjectConnectorDefault.project_id == project.id)
            .all()
        )
        assert len(defaults) == 1
        assert defaults[0].service == "tavily"
        assert defaults[0].connector_id == tavily.id
        assert defaults[0].settings_revision == 2
        assert resolve_connector(
            db, project_id=project.id, service="tavily"
        ).connector_id == tavily.id
        # The non-default provider remains eligible as an automatic fallback.
        assert resolve_connector(
            db, project_id=project.id, service="brave"
        ).connector_id == brave.id


def test_historical_project_default_remains_executable_but_cannot_be_reassigned():
    with SessionLocal() as db:
        owner, project = _scope(db)
        legacy = _connector(db, owner=owner, project=project, visibility="project")
        row = ProjectConnectorDefault(
            id=uuid.uuid4(),
            project_id=project.id,
            service="brave",
            connector_id=legacy.id,
            connector_revision=1,
            settings_revision=1,
            updated_by_user_id=owner.id,
        )
        db.add(row)
        db.flush()

        resolved = resolve_connector(db, project_id=project.id, service="brave")
        assert resolved.connector_id == legacy.id
        assert resolved.visibility == "project"

        with pytest.raises(ConnectorCommandError) as refused:
            set_default(
                db,
                project_id=project.id,
                actor=owner,
                service="brave",
                expected_revision=1,
                connector_id=legacy.id,
            )
        assert refused.value.code == "connector_visibility_unsupported"
        assert row.connector_id == legacy.id
        assert row.settings_revision == 1


def test_one_eligible_candidate_is_selected_but_two_scopes_are_ambiguous():
    with SessionLocal() as db:
        owner, project = _scope(db)
        project_connector = _connector(
            db, owner=owner, project=project, visibility="project", kind="tavily"
        )
        resolved = resolve_connector(db, project_id=project.id, service="tavily")
        assert resolved.connector_id == project_connector.id

        global_connector = _connector(
            db, owner=owner, project=None, visibility="global", kind="tavily"
        )
        with pytest.raises(ConnectorSelectionError, match="connector_ambiguous"):
            resolve_connector(db, project_id=project.id, service="tavily")
        assert global_connector.visibility == "global"


@pytest.mark.parametrize("state", ["revoked", "disabled"])
def test_ineligible_default_never_falls_back_to_another_candidate(state):
    with SessionLocal() as db:
        owner, project = _scope(db)
        if state == "revoked":
            from datetime import UTC, datetime

            unavailable = _connector(
                db,
                owner=owner,
                project=project,
                visibility="project",
                revoked_at=datetime.now(UTC),
            )
        else:
            unavailable = _connector(
                db,
                owner=owner,
                project=project,
                visibility="project",
                enabled=False,
            )
        _connector(db, owner=owner, project=None, visibility="global")
        db.add(
            ProjectConnectorDefault(
                id=uuid.uuid4(),
                project_id=project.id,
                service="brave",
                connector_id=unavailable.id,
                connector_revision=1,
                settings_revision=1,
                updated_by_user_id=owner.id,
            )
        )
        db.flush()
        expected = "connector_revoked" if state == "revoked" else "connector_unavailable"
        with pytest.raises(ConnectorSelectionError, match=expected):
            resolve_connector(db, project_id=project.id, service="brave")


def test_another_projects_connector_is_hidden_even_when_explicitly_selected():
    with SessionLocal() as db:
        _owner, project = _scope(db)
        other_owner, other_project = _scope(db)
        foreign = _connector(
            db, owner=other_owner, project=other_project, visibility="project", kind="tavily"
        )
        with pytest.raises(ConnectorSelectionError, match="connector_required"):
            resolve_connector(db, project_id=project.id, service="tavily")
        with pytest.raises(ConnectorSelectionError, match="connector_scope_denied"):
            resolve_connector(
                db, project_id=project.id, service="tavily", selected_id=foreign.id
            )
