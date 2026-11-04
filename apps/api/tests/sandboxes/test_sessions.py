"""Workflow-scoped sandbox handle selection under the durable run claim."""

import uuid as _uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.chat.models import Conversation
from src.openship.database.models import Base
from src.openship.runs.models import Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.sandboxes import models, sessions
from src.openship.sandboxes.client import SandboxUnavailable
from src.openship.sandboxes.contracts import RuntimeRequirements, SandboxHandle
from src.openship.workspace.models import Project

NOW = datetime.now(UTC)


def _lane(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lane.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(factory):
    """Create one authorized running root run plus its lease claim."""
    user_id, project_id, conversation_id, run_id = (_uuid.uuid4() for _ in range(4))
    with factory() as db:
        db.add(User(id=user_id, username=f"u-{run_id.hex}",
                    email=f"sandbox-{run_id.hex}@example.test", password_hash="test-only"))
        db.add(Project(id=project_id, owner_user_id=user_id, name="sandbox lane"))
        db.add(Conversation(id=conversation_id, user_id=user_id, project_id=project_id,
                            title="lane"))
        db.add(Run(id=run_id, conversation_id=conversation_id, project_id=project_id,
                   status="running", fence=1))
        db.add(RunJob(run_id=run_id, fence=1, owner_id="worker-1",
                      lease_until=NOW + timedelta(seconds=300), available_at=NOW))
        db.commit()
    claim = Claim(run_id=run_id, fence=1, lease_until=NOW + timedelta(seconds=300),
                  worker_id="worker-1", user_id=user_id)
    return run_id, claim, project_id, conversation_id


def _seed_another_run(factory, owner_claim, project_id, conversation_id):
    """Create another run in the same project/conversation with an equal fence."""
    run_id = _uuid.uuid4()
    with factory() as db:
        db.add(Run(id=run_id, project_id=project_id, conversation_id=conversation_id,
                   status="running", fence=owner_claim.fence))
        db.add(RunJob(run_id=run_id, fence=owner_claim.fence,
                      owner_id=owner_claim.worker_id,
                      lease_until=owner_claim.lease_until, available_at=NOW))
        db.commit()
    claim = Claim(run_id=run_id, fence=owner_claim.fence,
                  lease_until=owner_claim.lease_until, worker_id=owner_claim.worker_id,
                  user_id=owner_claim.user_id)
    return run_id, claim


def _requirements(image="python:test", profile="python311", cpu="1", memory="512Mi"):
    return RuntimeRequirements(provider_id="local", profile_id=profile, image=image,
                               dependency_identity="stdlib", cpu=cpu, memory=memory)


def _saved_pointer(factory, run_id, project_id, conversation_id, *, status, requirements,
                   sandbox_id="sbx-keep", expires_at=None):
    with factory() as db:
        db.add(models.WorkflowSandbox(
            root_run_id=run_id, project_id=project_id, conversation_id=conversation_id,
            provider_id="local", requirements_key=requirements.key,
            status=status, fence=1,
            sandbox_id=sandbox_id if status == "active" else None,
            expires_at=expires_at if status == "active" else None,
            requested_at=NOW if status == "requested" else None,
            intent_id=_uuid.uuid4() if status == "requested" else None,
        ))
        db.commit()


def _row(factory, run_id):
    with factory() as db:
        return db.query(models.WorkflowSandbox).filter_by(root_run_id=run_id).one()


class FakeClient:
    """Records provider traffic; a client may only create or inspect here."""

    def __init__(self, *, inspect_state="running", on_wait=None, expiry_seconds=1800):
        self.calls = []
        self.inspect_state = inspect_state
        self.on_wait = on_wait
        self.expiry_seconds = expiry_seconds
        self.created = 0
        self.inspected = 0

    async def create(self, requirements):
        self.calls.append("create")
        self.created += 1
        if self.on_wait is not None:
            await self.on_wait()
        return SandboxHandle(provider_id=requirements.provider_id,
                             sandbox_id=f"sbx-{self.created}",
                             requirements_key=requirements.key,
                             expires_at=NOW + timedelta(seconds=self.expiry_seconds))

    async def inspect(self, handle):
        self.calls.append("inspect")
        self.inspected += 1
        if self.on_wait is not None:
            await self.on_wait()
        if self.inspect_state == "running":
            return handle.model_copy(update={"expires_at": NOW + timedelta(seconds=1500)})
        if self.inspect_state == "absent":
            return None
        raise SandboxUnavailable("sandbox inspection unavailable")

    def assert_no_cleanup(self):
        assert "delete" not in self.calls and "renew" not in self.calls


@pytest.mark.asyncio
async def test_equal_requirements_reuse_one_sandbox_id(lane, seeded):
    """Two deliveries with equal requirements create exactly one sandbox."""
    factory, (run_id, claim, _, _) = lane, seeded
    client = FakeClient()
    first = await sessions.get_or_create_sandbox(factory, client, claim=claim,
                                                 root_run_id=run_id, requirements=_requirements())
    second = await sessions.get_or_create_sandbox(
        factory, client, claim=claim, root_run_id=run_id, requirements=_requirements(),
    )
    assert client.created == 1
    assert first.sandbox_id == second.sandbox_id == "sbx-1"
    client.assert_no_cleanup()


@pytest.mark.asyncio
async def test_changed_runtime_identity_replaces_the_handle(lane, seeded):
    """Changed image/profile/resources change the key and replace the pointer."""
    factory, (run_id, claim, _project_id, _conversation_id) = lane, seeded
    client = FakeClient()
    await sessions.get_or_create_sandbox(factory, client, claim=claim, root_run_id=run_id,
                                         requirements=_requirements())
    handle = await sessions.get_or_create_sandbox(
        factory, client, claim=claim, root_run_id=run_id,
        requirements=_requirements(image="python:other"),
    )
    assert client.created == 2
    assert handle.sandbox_id == "sbx-2"
    row = _row(factory, run_id)
    assert row.requirements_key == _requirements(image="python:other").key
    client.assert_no_cleanup()


@pytest.mark.asyncio
async def test_stale_pointer_probes_then_replaces(lane, seeded):
    """A provider-confirmed absence replaces the pointer without extra creates."""
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    _saved_pointer(factory, run_id, project_id, conversation_id, status="active",
                   requirements=_requirements(), expires_at=NOW - timedelta(seconds=1))
    client = FakeClient(inspect_state="absent")
    handle = await sessions.get_or_create_sandbox(factory, client, claim=claim,
                                                  root_run_id=run_id, requirements=_requirements())
    assert client.created == 1
    assert client.inspected == 1
    assert handle.sandbox_id == "sbx-1"
    client.assert_no_cleanup()


@pytest.mark.asyncio
async def test_unavailable_provider_never_recreates(lane, seeded):
    """Timeout-class failure keeps the saved pointer and sends no create."""
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    _saved_pointer(factory, run_id, project_id, conversation_id, status="active",
                   requirements=_requirements(), expires_at=NOW + timedelta(seconds=600))
    client = FakeClient(inspect_state="unavailable")
    with pytest.raises(SandboxUnavailable):
        await sessions.get_or_create_sandbox(factory, client, claim=claim,
                                             root_run_id=run_id, requirements=_requirements())
    assert client.created == 0
    row = _row(factory, run_id)
    assert row.status == "active"
    assert row.sandbox_id == "sbx-keep"
    client.assert_no_cleanup()


@pytest.mark.asyncio
async def test_uncertain_creation_blocks_automatic_recreation(lane, seeded):
    """A committed but unacknowledged create is never silently replayed."""
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    _saved_pointer(factory, run_id, project_id, conversation_id, status="requested",
                   requirements=_requirements())
    client = FakeClient()
    with pytest.raises(sessions.SandboxCreationUncertain):
        await sessions.get_or_create_sandbox(factory, client, claim=claim,
                                             root_run_id=run_id, requirements=_requirements())
    assert client.created == 0


@pytest.mark.asyncio
async def test_expired_create_intent_can_be_replaced_by_later_authorized_run(lane, seeded):
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    _saved_pointer(factory, run_id, project_id, conversation_id, status="requested",
                   requirements=_requirements())
    with factory() as db:
        row = db.query(models.WorkflowSandbox).one()
        row.requested_at = NOW - timedelta(seconds=1831)
        db.commit()
    client = FakeClient()
    handle = await sessions.get_or_create_sandbox(
        factory, client, claim=claim, root_run_id=run_id,
        requirements=_requirements(),
    )
    assert handle.sandbox_id == "sbx-1"
    assert client.created == 1


@pytest.mark.asyncio
async def test_stale_claim_cannot_select_or_create(lane, seeded):
    """A released lease cannot adopt another owner's sandbox or send a create."""
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    _saved_pointer(factory, run_id, project_id, conversation_id, status="active",
                   requirements=_requirements(), expires_at=NOW + timedelta(seconds=600))
    with factory() as db:
        db.query(RunJob).filter_by(run_id=run_id).update(
            {RunJob.fence: claim.fence + 1, RunJob.owner_id: None, RunJob.lease_until: None}
        )
        db.commit()
    client = FakeClient()
    with pytest.raises(sessions.SandboxClaimUncertain):
        await sessions.get_or_create_sandbox(factory, client, claim=claim,
                                             root_run_id=run_id, requirements=_requirements())
    assert client.created == 0 and client.inspected == 0


@pytest.mark.asyncio
async def test_provider_wait_observes_committed_request(lane, seeded):
    """The request intent is committed before any provider round-trip."""
    factory, (run_id, claim, _project_id, _conversation_id) = lane, seeded

    async def observe():
        with factory() as observer:
            row = observer.query(models.WorkflowSandbox).filter_by(root_run_id=run_id).one()
            assert row.status == "requested"
            assert row.sandbox_id is None

    client = FakeClient(on_wait=observe)
    await sessions.get_or_create_sandbox(factory, client, claim=claim,
                                         root_run_id=run_id, requirements=_requirements())
    assert client.created == 1


@pytest.mark.asyncio
async def test_provider_wait_cannot_hold_a_transaction(lane, seeded):
    """A provider wait cannot sit inside an open DB transaction."""
    factory, (run_id, claim, _project_id, _conversation_id) = lane, seeded

    async def steal_claim():
        with factory() as db:
            db.query(RunJob).filter_by(run_id=run_id).update(
                {RunJob.fence: claim.fence + 1, RunJob.owner_id: None,
                 RunJob.lease_until: None}
            )
            db.commit()

    client = FakeClient(on_wait=steal_claim)
    with pytest.raises(sessions.SandboxClaimUncertain):
        await sessions.get_or_create_sandbox(factory, client, claim=claim,
                                             root_run_id=run_id, requirements=_requirements())
    assert client.created == 1
    row = _row(factory, run_id)
    assert row.status == "requested"
    assert row.sandbox_id is None


@pytest.mark.asyncio
async def test_replacement_leaves_old_sandbox_to_provider_ttl(lane, seeded):
    """Replacing a pointer never asks the provider to delete or renew anything."""
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    _saved_pointer(factory, run_id, project_id, conversation_id, status="active",
                   requirements=_requirements(profile="python312"),
                   sandbox_id="sbx-gone", expires_at=NOW + timedelta(seconds=600))
    client = FakeClient()
    handle = await sessions.get_or_create_sandbox(factory, client, claim=claim,
                                                  root_run_id=run_id, requirements=_requirements())
    assert client.inspected == 0
    assert handle.sandbox_id == "sbx-1"
    client.assert_no_cleanup()


@pytest.mark.asyncio
async def test_different_roots_never_share_a_handle(lane):
    """Two authorized root runs get separate providers and rows."""
    factory = lane
    first_run, first_claim, _first_project, _first_conversation = _seed(factory)
    second_run, second_claim, _second_project, _second_conversation = _seed(factory)
    client = FakeClient()
    await sessions.get_or_create_sandbox(factory, client, claim=first_claim,
                                         root_run_id=first_run, requirements=_requirements())
    await sessions.get_or_create_sandbox(factory, client, claim=second_claim,
                                         root_run_id=second_run, requirements=_requirements())
    assert client.created == 2
    with factory() as db:
        rows = db.query(models.WorkflowSandbox).all()
        assert len(rows) == 2
        assert {row.root_run_id for row in rows} == {first_run, second_run}
    client.assert_no_cleanup()


@pytest.mark.asyncio
async def test_foreign_root_run_is_refused(lane, seeded):
    """Selection is scoped to the claimed run, never an arbitrary root."""
    factory, (_, claim, _, _) = lane, seeded
    with pytest.raises(ValueError):
        await sessions.get_or_create_sandbox(factory, FakeClient(), claim=claim,
                                             root_run_id=_uuid.uuid4(), requirements=_requirements())


@pytest.mark.asyncio
async def test_two_runs_in_same_conversation_reuse_one_sandbox(lane, seeded):
    factory, (first_run, first_claim, project_id, conversation_id) = lane, seeded
    second_run, second_claim = _seed_another_run(
        factory, first_claim, project_id, conversation_id
    )
    client = FakeClient()
    first = await sessions.get_or_create_sandbox(
        factory, client, claim=first_claim, root_run_id=first_run,
        requirements=_requirements(),
    )
    second = await sessions.get_or_create_sandbox(
        factory, client, claim=second_claim, root_run_id=second_run,
        requirements=_requirements(),
    )
    assert first.sandbox_id == second.sandbox_id
    assert client.created == 1


@pytest.mark.asyncio
async def test_different_conversations_do_not_share_sandbox(lane):
    factory = lane
    first_run, first_claim, *_ = _seed(factory)
    second_run, second_claim, *_ = _seed(factory)
    client = FakeClient()
    first = await sessions.get_or_create_sandbox(
        factory, client, claim=first_claim, root_run_id=first_run,
        requirements=_requirements(),
    )
    second = await sessions.get_or_create_sandbox(
        factory, client, claim=second_claim, root_run_id=second_run,
        requirements=_requirements(),
    )
    assert first.sandbox_id != second.sandbox_id
    assert client.created == 2


@pytest.mark.asyncio
async def test_equal_fence_from_later_run_cannot_ack_prior_run_intent(lane, seeded):
    factory, (first_run, first_claim, project_id, conversation_id) = lane, seeded
    second_run, second_claim = _seed_another_run(
        factory, first_claim, project_id, conversation_id
    )
    client = FakeClient()
    with factory() as db:
        row = models.WorkflowSandbox(
            root_run_id=first_run, project_id=project_id,
            conversation_id=conversation_id, provider_id="local",
            requirements_key=_requirements().key, status="requested", fence=1,
            requested_at=NOW, intent_id=_uuid.uuid4(),
        )
        db.add(row)
        db.commit()
        row_id = row.id
    with pytest.raises(sessions.SandboxCreationUncertain):
        await sessions.get_or_create_sandbox(
            factory, client, claim=second_claim, root_run_id=second_run,
            requirements=_requirements(),
        )
    with factory() as db:
        row = db.get(models.WorkflowSandbox, row_id)
        assert row.root_run_id == first_run
    assert client.created == 0


@pytest.mark.asyncio
async def test_uncertain_command_blocks_reuse_until_provider_expiration(lane, seeded):
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    expiry = datetime.now(UTC) + timedelta(minutes=30)
    _saved_pointer(factory, run_id, project_id, conversation_id, status="active",
                   requirements=_requirements(), expires_at=expiry)
    row = _row(factory, run_id)
    sessions.mark_command_uncertain(
        factory, claim=claim, sandbox_id=row.sandbox_id, operation_id=_uuid.uuid4()
    )
    with pytest.raises(sessions.SandboxCommandUncertain):
        await sessions.get_or_create_sandbox(
            factory, FakeClient(), claim=claim, root_run_id=run_id,
            requirements=_requirements(),
        )
    with factory() as db:
        current = db.get(models.WorkflowSandbox, row.id)
        current.command_uncertain_until = NOW - timedelta(seconds=1)
        db.commit()
    client = FakeClient(inspect_state="absent")
    await sessions.get_or_create_sandbox(
        factory, client, claim=claim, root_run_id=run_id,
        requirements=_requirements(),
    )
    assert client.created == 1


def test_only_matching_operation_can_clear_command_uncertainty(lane, seeded):
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    expiry = datetime.now(UTC) + timedelta(minutes=30)
    _saved_pointer(factory, run_id, project_id, conversation_id, status="active",
                   requirements=_requirements(), expires_at=expiry)
    row = _row(factory, run_id)
    operation_id = _uuid.uuid4()
    sessions.mark_command_uncertain(
        factory, claim=claim, sandbox_id=row.sandbox_id, operation_id=operation_id
    )
    sessions.clear_command_uncertain(
        factory,
        claim=claim,
        sandbox_id=row.sandbox_id,
        operation_id=_uuid.uuid4(),
    )
    with factory() as db:
        assert db.get(models.WorkflowSandbox, row.id).command_operation_id == operation_id
    sessions.clear_command_uncertain(
        factory,
        claim=claim,
        sandbox_id=row.sandbox_id,
        operation_id=operation_id,
    )
    with factory() as db:
        current = db.get(models.WorkflowSandbox, row.id)
        assert current.command_operation_id is None
        assert current.command_uncertain_until is None


def test_stale_claim_cannot_dispatch_into_a_reused_sandbox(lane, seeded):
    factory, (run_id, claim, project_id, conversation_id) = lane, seeded
    _saved_pointer(
        factory,
        run_id,
        project_id,
        conversation_id,
        status="active",
        requirements=_requirements(),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    with factory() as db:
        db.query(RunJob).filter(RunJob.run_id == run_id).update(
            {RunJob.fence: claim.fence + 1, RunJob.owner_id: "replacement-worker"}
        )
        db.commit()
    with pytest.raises(sessions.SandboxClaimUncertain):
        sessions.mark_command_uncertain(
            factory,
            claim=claim,
            sandbox_id=_row(factory, run_id).sandbox_id,
            operation_id=_uuid.uuid4(),
        )


@pytest.fixture
def lane(tmp_path):
    return _lane(tmp_path)


@pytest.fixture
def seeded(lane):
    return _seed(lane)
