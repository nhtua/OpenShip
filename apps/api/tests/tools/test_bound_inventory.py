from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    ExecutionConfig,
    ToolInvocationContext,
)
from src.openship.connectors.models import (
    Connector,
    ConnectorRevision,
    CredentialVersion,
)
from src.openship.database.models import Base
from src.openship.providers.control import InvocationControl
from src.openship.resources.models import Environment, ResourceBinding, ResourceObject
from src.openship.tools.aws.bound_inventory import (
    BoundInventoryArguments,
    BoundInventoryError,
    list_bound_buckets,
)
from src.openship.workspace.models import Project

_ACCOUNT_ID = "123456789012"
_TOOL_ID = "aws_s3_list_bound_buckets"


@pytest.fixture
def inventory_scope(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'bound-inventory.db'}")
    event.listen(
        engine,
        "connect",
        lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        owner, project = _scope(db, "owner", "owned-project")
        foreign_owner, foreign_project = _scope(db, "foreign", "foreign-project")
        _seed_binding(db, owner, project, "revoked-bucket-one", revoked=True)
        _seed_binding(db, owner, project, "expired-bucket-two", expired=True)
        _seed_binding(db, owner, project, "retired-bucket", retired=True)
        for index in range(3, 9):
            _seed_binding(db, owner, project, f"owned-bucket-{index}")
        _seed_binding(db, foreign_owner, foreign_project, "foreign-bucket-one")
        db.commit()
        scope = {
            "owner_id": owner.id,
            "project_id": project.id,
            "conversation_id": _conversation(db, owner, project),
            "foreign_owner_id": foreign_owner.id,
            "foreign_project_id": foreign_project.id,
            "foreign_conversation_id": _conversation(
                db, foreign_owner, foreign_project
            ),
            "sessions": sessions,
        }
        db.commit()
    try:
        yield scope
    finally:
        engine.dispose()


def _scope(db, username: str, project_name: str):
    from src.openship.chat.models import Conversation

    suffix = uuid.uuid4().hex[:12]
    owner = User(
        username=f"{username}-{suffix}",
        email=f"{username}-{suffix}@example.test",
        password_hash="test-only",
    )
    db.add(owner)
    db.flush()
    project = Project(owner_user_id=owner.id, name=project_name)
    db.add(project)
    db.flush()
    conversation = Conversation(
        user_id=owner.id, project_id=project.id, title="Inventory"
    )
    db.add(conversation)
    db.flush()
    return owner, project


def _conversation(db, owner, project):
    from src.openship.chat.models import Conversation

    return (
        db.query(Conversation.id)
        .filter(
            Conversation.user_id == owner.id,
            Conversation.project_id == project.id,
        )
        .one()[0]
    )


def _seed_binding(
    db, owner, project, bucket_name: str, *, revoked=False, expired=False, retired=False
):
    credential_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    connector = Connector(
        id=connector_id,
        kind="aws",
        visibility="project",
        project_id=project.id,
        name=f"AWS {bucket_name}",
        current_revision=1,
        enabled=True,
        revoked_at=datetime.now(UTC) if revoked else None,
        managed_by_user_id=owner.id,
        created_by_user_id=owner.id,
    )
    credential = CredentialVersion(
        id=credential_id,
        connector_id=connector_id,
        visibility="project",
        project_id=project.id,
        kind="aws_bootstrap",
        schema_version=1,
        key_id="inventory-test-key",
        nonce=b"n" * 12,
        ciphertext=b"c" * 32,
        expires_at=datetime.now(UTC) - timedelta(days=1) if expired else None,
        state="revoked" if revoked else "active",
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    db.add(connector)
    db.flush()
    db.add(credential)
    db.flush()
    db.add(
        ConnectorRevision(
            id=uuid.uuid4(),
            connector_id=connector_id,
            revision=1,
            schema_version=1,
            visibility="project",
            project_id=project.id,
            configuration_json="{}",
            configuration_digest="a" * 64,
            credential_version_id=credential_id,
            created_by_user_id=owner.id,
        )
    )
    db.flush()
    environment = (
        db.query(Environment).filter(Environment.project_id == project.id).first()
    )
    if environment is None:
        environment = Environment(
            project_id=project.id,
            name="production",
            display_name="Production",
            created_by_user_id=owner.id,
        )
        db.add(environment)
        db.flush()
    resource_id = uuid.uuid4()
    db.add(
        ResourceObject(
            id=resource_id,
            project_id=project.id,
            kind="aws_s3_bucket",
            uri=f"openship://{project.id}/resource/{resource_id}",
            display_name=bucket_name,
            created_by_user_id=owner.id,
        )
    )
    db.flush()
    db.add(
        ResourceBinding(
            id=uuid.uuid4(),
            project_id=project.id,
            resource_object_id=resource_id,
            environment_id=environment.id,
            connector_id=connector_id,
            connector_revision=1,
            revision=1,
            account_id=_ACCOUNT_ID,
            region="us-east-1",
            bucket_name=bucket_name,
            bucket_arn=f"arn:aws:s3:::{bucket_name}",
            expected_owner=_ACCOUNT_ID,
            management_method="external",
            state="retired" if retired else "active",
            created_by_user_id=owner.id,
            retired_at=datetime.now(UTC) if retired else None,
        )
    )
    db.flush()


def _context(scope, *, foreign=False, credential_resolver=None):
    return ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=(scope["foreign_owner_id"] if foreign else scope["owner_id"]),
            project_id=(
                scope["foreign_project_id"] if foreign else scope["project_id"]
            ),
            conversation_id=(
                scope["foreign_conversation_id"]
                if foreign
                else scope["conversation_id"]
            ),
            action="execute_tool",
            target_id="run-for-inventory-test",
        ),
        execution=ExecutionConfig(graph_kind="chat", graph_version=2),
        credential_resolver=credential_resolver
        or (lambda _name: pytest.fail("unexpected credential lookup")),
        artifact_store=None,
        session_factory=scope["sessions"],
    )


def test_installed_inventory_is_local_and_has_no_connector_or_cloud_operations():
    manifest = next(item for item in list_capability_manifests() if item.id == _TOOL_ID)

    assert manifest.effect == "read_only_local"
    assert manifest.connector_kinds == ()
    assert manifest.operations == ()
    assert manifest.target_kinds == ()
    assert manifest.outputs == ()
    assert "conversation_workspace" not in manifest.enablement_requirements


@pytest.mark.asyncio
async def test_lists_only_owned_project_bindings_and_retains_unavailable_connector_refs(
    inventory_scope,
):
    result = await list_bound_buckets(
        BoundInventoryArguments(), _context(inventory_scope), InvocationControl()
    )

    assert result.source == "stored_bindings"
    names = {item.bucket_name for item in result.items}
    assert "revoked-bucket-one" in names
    assert "expired-bucket-two" in names
    assert "retired-bucket" not in names
    assert "foreign-bucket-one" not in names
    revoked = next(
        item for item in result.items if item.bucket_name == "revoked-bucket-one"
    )
    assert revoked.connector_id
    assert revoked.connector_revision == 1
    assert revoked.binding_revision == 1


@pytest.mark.asyncio
async def test_cursor_paginates_stably_and_is_scoped_to_owner_project_and_query(
    inventory_scope,
):
    context = _context(inventory_scope)
    first = await list_bound_buckets(
        BoundInventoryArguments(limit=2), context, InvocationControl()
    )
    second = await list_bound_buckets(
        BoundInventoryArguments(limit=2, cursor=first.next_cursor),
        context,
        InvocationControl(),
    )

    assert first.next_cursor is not None
    assert second.next_cursor is not None
    first_ids = {item.binding_id for item in first.items}
    second_ids = {item.binding_id for item in second.items}
    assert len(first.items) == len(second.items) == 2
    assert first_ids.isdisjoint(second_ids)

    with pytest.raises(BoundInventoryError, match="invalid_cursor"):
        await list_bound_buckets(
            BoundInventoryArguments(limit=2, cursor=first.next_cursor),
            _context(inventory_scope, foreign=True),
            InvocationControl(),
        )
    with pytest.raises(BoundInventoryError, match="invalid_cursor"):
        await list_bound_buckets(
            BoundInventoryArguments(limit=2, cursor="not-a-cursor"),
            context,
            InvocationControl(),
        )
    with pytest.raises(BoundInventoryError, match="invalid_cursor"):
        await list_bound_buckets(
            {"cursor": "x" * 513}, context, InvocationControl()
        )


@pytest.mark.asyncio
async def test_rejects_invalid_page_sizes(inventory_scope):
    with pytest.raises(BoundInventoryError, match="invalid_page_size"):
        await list_bound_buckets(
            {"limit": 51}, _context(inventory_scope), InvocationControl()
        )
    with pytest.raises(BoundInventoryError, match="invalid_page_size"):
        await list_bound_buckets(
            {"limit": 0}, _context(inventory_scope), InvocationControl()
        )
