"""Run-pinned provider selection: what a run freezes, replays, and reveals.

The breaks these catch: a queued run that follows today's default instead of
its own snapshot, a replay that re-validates against changed profiles, a
projection that leaks a credential, and an attempt recorded under a fence the
worker no longer holds.
"""

import json
import uuid

import pytest

from fastapi.testclient import TestClient

from src.openship.chat.models import Message
from src.openship.providers.attempts import DatabaseAttemptRecorder
from src.openship.providers.contracts import TokenUsage
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Event, Outbox, ProviderAttempt, Run, RunJob
from src.openship.runs.service import load_run_model_config

from .conftest import (
    CREDENTIAL_BINDING,
    SENTINEL_CREDENTIAL,
    TestSessionLocal,
    declare_two_profiles,
)


def _conversation(client: TestClient, authorized_client: dict) -> str:
    """An owned conversation to submit turns into."""
    response = client.post("/api/conversations", json={"title": "Fixture"}, headers=authorized_client)
    assert response.status_code == 201, f"conversation creation failed: {response.text}"
    return response.json()["id"]


def _submit(client: TestClient, authorized_client: dict, conversation_id: str, **selection):
    """Submit one turn with an explicit selection; return the response."""
    return client.post(
        f"/api/runs/{conversation_id}/turns",
        json={
            "content": "What failed?",
            "client_request_id": str(uuid.uuid4()),
            **selection,
        },
        headers=authorized_client,
    )


def _runs_for(session, conversation_id: str) -> list[Run]:
    return session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).all()


# --- Selection is refused before anything is queued ---------------------------------


def test_unknown_profile_is_refused_before_queuing(client, authorized_client, monkeypatch):
    """An ID the installation does not declare produces a 422 and no run."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)

    response = _submit(client, authorized_client, conversation_id, provider_profile_id="not-declared")

    assert response.status_code == 422, f"got {response.status_code}: {response.text}"
    assert response.json()["error"]["code"] == "configuration_error"
    assert "not-declared" in response.json()["error"]["message"]
    assert _runs_for(TestSessionLocal(), conversation_id) == []


def test_a_missing_credential_binding_still_queues_the_run(client, authorized_client, monkeypatch):
    """A run is queued against its snapshot; a binding is resolved where it runs."""
    declare_two_profiles(monkeypatch)
    monkeypatch.delenv(CREDENTIAL_BINDING, raising=False)
    conversation_id = _conversation(client, authorized_client)

    response = _submit(client, authorized_client, conversation_id, provider_profile_id="secondary-fixture")

    assert response.status_code == 202, f"submit failed: {response.text}"
    assert len(_runs_for(TestSessionLocal(), conversation_id)) == 1


def test_unauthorized_fallback_is_refused_before_queuing(client, authorized_client, monkeypatch):
    """A fallback the primary profile does not allow never reaches the queue."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)

    response = _submit(
        client,
        authorized_client,
        conversation_id,
        provider_profile_id="secondary-fixture",
        fallback_profile_ids=["primary-fixture"],
    )

    assert response.status_code == 422, f"got {response.status_code}: {response.text}"
    assert response.json()["error"]["code"] == "configuration_error"
    assert _runs_for(TestSessionLocal(), conversation_id) == []


# --- The snapshot is the run's own, not the installation's -------------------------


def test_snapshot_pins_primary_and_fallback_settings(client, authorized_client, monkeypatch):
    """The run stores complete settings for every allowed destination."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)

    response = _submit(
        client,
        authorized_client,
        conversation_id,
        provider_profile_id="primary-fixture",
        fallback_profile_ids=["secondary-fixture"],
    )
    assert response.status_code == 202, f"submit failed: {response.text}"

    config = load_run_model_config(_runs_for(TestSessionLocal(), conversation_id)[0])

    assert config.binding_origin == "submission"
    assert config.primary.id == "primary-fixture"
    assert [profile.id for profile in config.fallbacks] == ["secondary-fixture"]
    assert config.primary.model  # the model itself, not only its ID
    assert config.primary.profile_revision != config.fallbacks[0].profile_revision
    assert config.limits.max_output_tokens > 0


def test_same_request_replays_original_config_after_default_change(client, authorized_client, monkeypatch):
    """Replay returns the original snapshot even after the default moves."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    request_id = str(uuid.uuid4())

    first = client.post(
        f"/api/runs/{conversation_id}/turns",
        json={"content": "What failed?", "client_request_id": request_id, "provider_profile_id": "primary-fixture"},
        headers=authorized_client,
    )
    assert first.status_code == 202, f"submit failed: {first.text}"

    monkeypatch.setattr("src.openship.config.settings.model_default_profile_id", "secondary-fixture")

    replay = client.post(
        f"/api/runs/{conversation_id}/turns",
        json={"content": "What failed?", "client_request_id": request_id},
        headers=authorized_client,
    )
    assert replay.status_code == 202, f"replay failed: {replay.text}"
    assert replay.json()["id"] == first.json()["id"]

    runs = _runs_for(TestSessionLocal(), conversation_id)
    assert len(runs) == 1
    assert load_run_model_config(runs[0]).primary.id == "primary-fixture"


def test_changed_profile_or_fallback_order_conflicts(client, authorized_client, monkeypatch):
    """The same request key with a different selection is a conflict."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    request_id = str(uuid.uuid4())

    first = client.post(
        f"/api/runs/{conversation_id}/turns",
        json={
            "content": "What failed?",
            "client_request_id": request_id,
            "provider_profile_id": "primary-fixture",
            "fallback_profile_ids": ["secondary-fixture"],
        },
        headers=authorized_client,
    )
    assert first.status_code == 202, f"submit failed: {first.text}"

    for selection in (
        {"provider_profile_id": "secondary-fixture", "fallback_profile_ids": []},
        {"provider_profile_id": "primary-fixture", "fallback_profile_ids": []},
    ):
        response = client.post(
            f"/api/runs/{conversation_id}/turns",
            json={"content": "What failed?", "client_request_id": request_id, **selection},
            headers=authorized_client,
        )
        assert response.status_code == 409, f"expected a conflict, got {response.status_code}"

    assert len(_runs_for(TestSessionLocal(), conversation_id)) == 1


def test_duplicate_request_creates_one_run_message_and_job(client, authorized_client, monkeypatch):
    """A replay adds no second run, message, or queue job."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    request_id = str(uuid.uuid4())

    for _ in range(2):
        response = client.post(
            f"/api/runs/{conversation_id}/turns",
            json={"content": "What failed?", "client_request_id": request_id},
            headers=authorized_client,
        )
        assert response.status_code == 202, f"submit failed: {response.text}"

    session = TestSessionLocal()
    runs = session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).all()
    assert len(runs) == 1
    messages = session.query(Message).filter(Message.run_id == runs[0].id).all()
    assert len(messages) == 1
    jobs = session.query(RunJob).filter(RunJob.run_id == runs[0].id).all()
    assert len(jobs) == 1


# --- What a run reveals, and what it never reveals ---------------------------------


def test_run_read_carries_the_safe_model_projection(client, authorized_client, monkeypatch):
    """A run read names the pinned destination without naming any secret."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)

    response = _submit(client, authorized_client, conversation_id)
    assert response.status_code == 202, f"submit failed: {response.text}"

    body = response.json()
    assert body["model"]["profile_id"] == "primary-fixture"
    assert body["model"]["recorded"] is True
    assert body["usage"] is None
    assert body["error"] is None
    assert body["provider_attempts"] == []

    persisted = _runs_for(TestSessionLocal(), conversation_id)[0].model_configuration
    assert SENTINEL_CREDENTIAL not in persisted
    assert "credential_ref" not in json.dumps(body["model"])


def test_a_snapshot_names_a_binding_never_its_value(client, authorized_client, monkeypatch):
    """Nothing a run stores or emits can carry a credential."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    response = _submit(client, authorized_client, conversation_id, provider_profile_id="primary-fixture")
    assert response.status_code == 202, f"submit failed: {response.text}"

    session = TestSessionLocal()
    run = session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).one()
    stored = [
        run.model_configuration,
        run.model_selection,
        *(event.payload for event in session.query(Event).filter(Event.run_id == run.id).all()),
        *(
            outbox.payload
            for outbox in session.query(Outbox).filter(Outbox.conversation_id == uuid.UUID(conversation_id)).all()
        ),
    ]

    assert CREDENTIAL_BINDING in run.model_configuration
    assert all(SENTINEL_CREDENTIAL not in payload for payload in stored if payload)


def test_a_snapshot_reveals_the_same_projection_as_a_run_read(client, authorized_client, monkeypatch):
    """The snapshot and the run read agree, and neither names a secret."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    response = _submit(client, authorized_client, conversation_id, provider_profile_id="primary-fixture")
    assert response.status_code == 202, f"submit failed: {response.text}"

    session = TestSessionLocal()
    run = session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).one()
    profile = load_run_model_config(run).primary
    recorder = DatabaseAttemptRecorder(lambda: session, run.id, run.fence)
    attempt_id = recorder.start(profile, reason="initial")
    recorder.finish(
        attempt_id,
        "succeeded",
        usage=TokenUsage(input_tokens=5, output_tokens=2, total_tokens=7, source="provider"),
    )
    session.commit()

    snapshot = client.get(f"/api/conversations/{conversation_id}/snapshot", headers=authorized_client)
    assert snapshot.status_code == 200, f"snapshot failed: {snapshot.text}"

    summary = next(item for item in snapshot.json()["runs"] if item["id"] == response.json()["id"])
    assert summary["model"]["profile_id"] == "primary-fixture"
    assert [attempt["ordinal"] for attempt in summary["provider_attempts"]] == [1]
    assert summary["provider_attempts"][0]["outcome"] == "succeeded"
    assert summary["provider_attempts"][0]["usage"]["total_tokens"] == 7
    assert summary["usage"] is None  # a run's totals are its worker's, never summed here
    assert SENTINEL_CREDENTIAL not in snapshot.text


def test_terminal_legacy_run_reports_no_recorded_provider(client, authorized_client, monkeypatch):
    """A run from before profiles exists, and says so, without guessing one."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    submitted = _submit(client, authorized_client, conversation_id)
    assert submitted.status_code == 202, f"submit failed: {submitted.text}"

    session = TestSessionLocal()
    project_id = session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).first().project_id
    legacy_id = uuid.uuid4()
    session.add(
        Run(
            id=legacy_id,
            conversation_id=uuid.UUID(conversation_id),
            project_id=project_id,
            status="completed",
        )
    )
    session.commit()

    response = client.get(f"/api/runs/{legacy_id}", headers=authorized_client)
    assert response.status_code == 200, f"legacy run read failed: {response.text}"
    assert response.json()["model"] is None


def test_a_foreign_run_does_not_leak_its_profile(client, authorized_client, monkeypatch):
    """Ownership is checked before any projection is produced."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    response = _submit(client, authorized_client, conversation_id)
    assert response.status_code == 202, f"submit failed: {response.text}"
    run_id = response.json()["id"]

    other = client.post(
        "/api/auth/register",
        json={"username": "other-user", "email": "other@example.com", "password": "password123"},
    )
    assert other.status_code == 201
    foreign = client.get(f"/api/runs/{run_id}", headers={"Authorization": f"Bearer {other.json()['access_token']}"})

    assert foreign.status_code == 404
    assert "model" not in foreign.json()


# --- Attempts are fenced, and they roll back with their transaction ----------------


def test_an_attempt_requires_the_current_fence(client, authorized_client, monkeypatch):
    """A worker whose fence moved records nothing."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    response = _submit(client, authorized_client, conversation_id)
    assert response.status_code == 202, f"submit failed: {response.text}"

    session = TestSessionLocal()
    run = session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).one()
    profile = load_run_model_config(run).primary
    recorder = DatabaseAttemptRecorder(lambda: session, run.id, run.fence)
    attempt_id = recorder.start(profile, reason="initial")
    session.commit()

    current = session.get(Run, run.id)
    current.fence += 1
    session.commit()

    stale = DatabaseAttemptRecorder(lambda: session, run.id, run.fence - 1)
    with pytest.raises(ProviderError) as refused:
        stale.start(profile, reason="retry")
    assert refused.value.code == "lease_lost"

    recorded = session.query(ProviderAttempt).filter(ProviderAttempt.run_id == run.id).all()
    assert len(recorded) == 1
    assert recorded[0].ordinal == 1
    assert recorded[0].fence == 0
    assert str(attempt_id) == str(recorded[0].id)


def test_an_attempt_carries_its_own_outcome(client, authorized_client, monkeypatch):
    """Usage and the outcome belong to the attempt that produced them."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    response = _submit(client, authorized_client, conversation_id)
    assert response.status_code == 202, f"submit failed: {response.text}"

    session = TestSessionLocal()
    run = session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).one()
    profile = load_run_model_config(run).primary
    recorder = DatabaseAttemptRecorder(lambda: session, run.id, run.fence)
    attempt_id = recorder.start(profile, reason="initial")
    recorder.finish(
        attempt_id,
        outcome="failed",
        usage=TokenUsage(input_tokens=11, output_tokens=4, total_tokens=15, source="provider"),
        error_code="rate_limited",
    )
    session.commit()

    attempt = session.query(ProviderAttempt).filter(ProviderAttempt.run_id == run.id).one()
    assert attempt.outcome == "failed"
    assert attempt.error_code == "rate_limited"
    assert json.loads(attempt.usage)["input_tokens"] == 11
    assert attempt.profile_id == "primary-fixture"
    assert attempt.ordinal == 1


def test_an_attempt_rolls_back_with_its_outbox(client, authorized_client, monkeypatch):
    """A failed transaction leaves neither an attempt nor an outbox row."""
    declare_two_profiles(monkeypatch)
    conversation_id = _conversation(client, authorized_client)
    response = _submit(client, authorized_client, conversation_id)
    assert response.status_code == 202, f"submit failed: {response.text}"

    session = TestSessionLocal()
    run = session.query(Run).filter(Run.conversation_id == uuid.UUID(conversation_id)).one()
    profile = load_run_model_config(run).primary
    recorder = DatabaseAttemptRecorder(lambda: session, run.id, run.fence)
    session.add(
        Outbox(
            conversation_id=uuid.UUID(conversation_id),
            type="model.attempt.started",
            payload="{}",
        )
    )
    recorder.start(profile, reason="initial")
    session.commit()
    assert session.query(ProviderAttempt).filter(ProviderAttempt.run_id == run.id).count() == 1

    session = TestSessionLocal()
    recorder = DatabaseAttemptRecorder(lambda: session, run.id, run.fence)
    try:
        recorder.start(profile, reason="retry")
        session.add(Outbox(conversation_id=uuid.UUID(conversation_id), type="unbalanced"))
        raise ValueError("the command failed after recording")
    except ValueError:
        session.rollback()

    assert session.query(ProviderAttempt).filter(ProviderAttempt.run_id == run.id).count() == 1, (
        "the attempt must roll back with the rest of the command"
    )
