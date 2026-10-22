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
