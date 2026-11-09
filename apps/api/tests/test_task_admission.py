"""Agentic task admission: exact mode capture, retries and rollout qualification."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.openship.chat.models import Conversation, Message
from src.openship.config import settings
from src.openship.database.session import SessionLocal
from src.openship.runs.models import Outbox, Run, RunJob
from src.openship.runs.queue import Claim
from src.openship.runs.tasks.contracts import TaskPlan, TaskStep
from src.openship.runs.tasks.models import TaskRecord
from src.openship.runs.tasks.state import save_plan
from src.openship.sandboxes.profiles import (
    empty_catalog,
    install_runtime_catalog,
    reset_runtime_catalog,
)
from tests.conftest import configure_profiles, profile_spec

pytestmark = pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")

SEARCH_CEILING = "brave_web_search,tavily_web_search"


@pytest.fixture(autouse=True)
def empty_profile_catalog():
    install_runtime_catalog(empty_catalog())
    yield
    reset_runtime_catalog()


def _agentic_profiles(
    monkeypatch,
    *,
    structured_output: bool = True,
    tool_calls: bool = True,
    allowlist: str = "primary-fixture",
    enabled: bool = True,
):
    primary = profile_spec("primary-fixture")
    secondary = profile_spec("secondary-fixture")
    primary["capabilities"].update(
        {
            "tool_calls": tool_calls,
            "structured_output": structured_output,
            "parallel_tool_calls": False,
        }
    )
    secondary["capabilities"].update(
        {
            "tool_calls": False,
            "structured_output": False,
            "parallel_tool_calls": False,
        }
    )
    configure_profiles(monkeypatch, [primary, secondary], "primary-fixture")
    monkeypatch.setattr(settings, "tool_enabled_ids", SEARCH_CEILING)
    monkeypatch.setattr(settings, "agentic_tasks_enabled", enabled)
    monkeypatch.setattr(settings, "agentic_task_profile_ids", allowlist)
    monkeypatch.setattr(settings, "sandbox_enabled", False)


def _no_managed_search_connectors(monkeypatch):
    from src.openship.connectors.selection import ConnectorSelectionError

    def missing(*_args, **_kwargs):
        raise ConnectorSelectionError("connector_required")

    monkeypatch.setattr("src.openship.runs.service.resolve_connector", missing)
    monkeypatch.setattr(
        "src.openship.runs.service._connector_rows_for_project",
        lambda *_args, **_kwargs: 0,
    )


def _conversation(client, headers) -> str:
    response = client.post(
        "/api/conversations", json={"title": "Agent mode"}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _turn(conversation_id: str, request_id: str, **overrides) -> dict:
    payload = {
        "content": "Chart the supplied weather observations.",
        "client_request_id": request_id,
        **overrides,
    }
    return payload


def _set_preference(client, headers, mode: str, expected_revision: int):
    response = client.patch(
        "/api/auth/preferences",
        json={"agent_mode": mode, "expected_revision": expected_revision},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _counts(db, conversation_id: uuid.UUID) -> tuple[int, int, int]:
    return (
        db.query(Message).filter(Message.conversation_id == conversation_id).count(),
        db.query(Run).filter(Run.conversation_id == conversation_id).count(),
        db.query(Outbox)
        .join(Run, Run.id == Outbox.run_id)
        .filter(Run.conversation_id == conversation_id)
        .count(),
    )


def test_stale_new_submission_creates_nothing(
    client, authorized_client, monkeypatch
):
    _agentic_profiles(monkeypatch)
    _no_managed_search_connectors(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    _set_preference(client, authorized_client, "auto", 1)

    with SessionLocal() as db:
        before = _counts(db, uuid.UUID(conversation_id))

    stale = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=_turn(
            conversation_id,
            str(uuid.uuid4()),
            agent_mode="copilot",
            agent_mode_revision=1,
        ),
        headers=authorized_client,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "stale_agent_preference"

    with SessionLocal() as db:
        after = _counts(db, uuid.UUID(conversation_id))
    assert after == before
    assert (after[0] - before[0], after[1] - before[1], after[2] - before[2]) == (
        0,
        0,
        0,
    )


def test_retry_after_preference_change_returns_original_run(
    client, authorized_client, monkeypatch
):
    _agentic_profiles(monkeypatch)
    _no_managed_search_connectors(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    request_id = str(uuid.uuid4())
    original_payload = _turn(
        conversation_id,
        request_id,
        enable_tools=True,
        agent_mode="copilot",
        agent_mode_revision=1,
    )

    original = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=original_payload,
        headers=authorized_client,
    )
    assert original.status_code == 202, original.text
    original_run_id = original.json()["id"]
    assert original.json()["graph_version"] == 5

    with SessionLocal() as db:
        run = db.get(Run, uuid.UUID(original_run_id))
        execution = json.loads(run.execution_configuration)
        assert execution["schema_version"] == 5
        assert execution["task_context"]["mode"] == "copilot"
        assert execution["task_context"]["root_run_id"] == original_run_id
        assert execution["profile_catalog_digest"]
        original_digest = run.submission_digest
        conversation_uuid = run.conversation_id
        initiator_id = db.query(Conversation.user_id).filter(
            Conversation.id == run.conversation_id
        ).scalar()

    _set_preference(client, authorized_client, "auto", 1)

    # The updated client resends its captured selection; the retry must not be
    # revalidated against the now-current preference.
    retry = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=original_payload,
        headers=authorized_client,
    )
    assert retry.status_code == 202, retry.text
    retry_run = retry.json()
    assert retry_run["id"] == original.json()["id"]
    with SessionLocal() as db:
        retry_execution = json.loads(
            db.get(Run, uuid.UUID(retry_run["id"])).execution_configuration
        )
        assert retry_execution["task_context"]["mode"] == "copilot"
        assert (
            db.get(Run, uuid.UUID(retry_run["id"])).submission_digest
            == original_digest
        )

    # A legacy client omitting both fields also replays the captured mode.
    legacy_retry = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=_turn(conversation_id, request_id, enable_tools=True),
        headers=authorized_client,
    )
    assert legacy_retry.status_code == 202, legacy_retry.text
    assert legacy_retry.json()["id"] == original_run_id
    with SessionLocal() as db:
        legacy_execution = json.loads(
            db.get(Run, uuid.UUID(original_run_id)).execution_configuration
        )
        assert legacy_execution["task_context"]["mode"] == "copilot"

    # The task row created later must inherit the pinned context, not the
    # account's current preference.
    lease_until = datetime.now(UTC) + timedelta(minutes=5)
    with SessionLocal() as db:
        run = db.get(Run, uuid.UUID(original_run_id))
        job = db.query(RunJob).filter(RunJob.run_id == run.id).one()
        run.status = "running"
        run.fence = 1
        job.fence = 1
        job.owner_id = "task-admission-worker"
        job.lease_until = lease_until
        db.commit()
        claim = Claim(
            run_id=run.id,
            fence=1,
            lease_until=lease_until,
            worker_id="task-admission-worker",
            user_id=initiator_id,
        )
        plan = TaskPlan(
            revision=1,
            intent="Chart the supplied weather observations.",
            steps=(
                TaskStep(
                    id="inspect",
                    intent="Read the supplied observations.",
                    expected_outputs=("chart.png",),
                    completion_checks=("chart exists",),
                ),
            ),
            status="validated",
        )
        save_plan(db, claim, plan, expected_revision=0)
        db.commit()
        record = db.query(TaskRecord).filter(TaskRecord.run_id == run.id).one()
        assert record.mode == "copilot"
        assert record.scope == "conversation"
        assert json.loads(record.context_json)["mode"] == "copilot"
        assert conversation_uuid == run.conversation_id


def test_feature_gate_and_unqualified_profile_fail_visibly(
    client, authorized_client, monkeypatch
):
    _no_managed_search_connectors(monkeypatch)

    # Enabled gate, but the selected profile is not explicitly allowlisted.
    _agentic_profiles(monkeypatch, allowlist="")
    conversation_id = _conversation(client, authorized_client)
    with SessionLocal() as db:
        before = _counts(db, uuid.UUID(conversation_id))
    refused = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=_turn(conversation_id, str(uuid.uuid4()), enable_tools=True),
        headers=authorized_client,
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "agentic_profile_not_enabled"
    with SessionLocal() as db:
        after = _counts(db, uuid.UUID(conversation_id))
    assert after == before

    # An allowlisted profile that cannot support tools and contract output is
    # refused rather than downgraded silently to a legacy/Auto path.
    _agentic_profiles(monkeypatch, structured_output=False)
    conversation_id = _conversation(client, authorized_client)
    with SessionLocal() as db:
        before = _counts(db, uuid.UUID(conversation_id))
    unsupported = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=_turn(conversation_id, str(uuid.uuid4()), enable_tools=True),
        headers=authorized_client,
    )
    assert unsupported.status_code == 422, unsupported.text
    assert unsupported.json()["error"]["code"] == "agentic_profile_unsupported"
    with SessionLocal() as db:
        after = _counts(db, uuid.UUID(conversation_id))
    assert after == before

    # The default gate leaves delivered tool-enabled chat unchanged.
    _agentic_profiles(monkeypatch, enabled=False)
    conversation_id = _conversation(client, authorized_client)
    delivered = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=_turn(conversation_id, str(uuid.uuid4()), enable_tools=True),
        headers=authorized_client,
    )
    assert delivered.status_code == 202, delivered.text
    assert delivered.json()["graph_version"] == 2

    # Ordinary tool-disabled chat remains usable with sandbox disabled.
    _agentic_profiles(monkeypatch, enabled=True)
    monkeypatch.setattr(settings, "sandbox_enabled", False)
    conversation_id = _conversation(client, authorized_client)
    ordinary = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=_turn(conversation_id, str(uuid.uuid4())),
        headers=authorized_client,
    )
    assert ordinary.status_code == 202, ordinary.text
    assert ordinary.json()["graph_version"] == 1


@pytest.mark.parametrize(
    "status", ["waiting_plan", "waiting_input", "waiting_config"]
)
def test_waiting_task_blocks_new_execution_admission(
    client, authorized_client, monkeypatch, status
):
    _agentic_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    with SessionLocal() as db:
        conversation = db.get(Conversation, uuid.UUID(conversation_id))
        db.add(
            Run(
                conversation_id=conversation.id,
                project_id=conversation.project_id,
                status=status,
                kind="chat",
                graph_version=5,
            )
        )
        db.commit()

    refused = client.post(
        f"/api/runs/{conversation_id}/turns",
        json=_turn(conversation_id, str(uuid.uuid4())),
        headers=authorized_client,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "run_already_active"


def test_mode_and_revision_are_paired():
    from src.openship.runs.schemas import TurnSubmitRequest

    with pytest.raises(ValueError):
        TurnSubmitRequest(
            content="hello",
            client_request_id=str(uuid.uuid4()),
            agent_mode="copilot",
        )
    with pytest.raises(ValueError):
        TurnSubmitRequest(
            content="hello",
            client_request_id=str(uuid.uuid4()),
            agent_mode_revision=1,
        )
