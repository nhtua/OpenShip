"""The public task view: one projection for reads, replay and the snapshot.

A reload must show what the same conversation's events already showed, so the
snapshot's task summary is compared against a replay of the task events in
sequence order. And nothing the browser is not owed may travel with it:
operation identities, call bindings, attempt digests and private continuations
belong to the ledgers, not to the plan card. The plan-confirmation digest the
browser will echo is the reviewed plan's own digest.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest

from src.openship.events.service import read_snapshot
from src.openship.runs.tasks.contracts import (
    TaskDecisionCommand,
    VerificationCheck,
    VerificationReport,
)
from src.openship.runs.tasks.decisions import apply_decision, park_task_wait
from src.openship.runs.tasks.models import TaskPlanRecord
from src.openship.runs.tasks.projections import read_task_summary
from src.openship.runs.tasks.state import (
    record_attempt_result,
    record_verification,
    save_plan,
)
from tests.conftest import TestSessionLocal
from tests.test_task_decisions import _pinned_execution
from tests.test_task_state import _identity, _operation, _plan


@pytest.fixture(autouse=True)
def _reset_profile_catalog(monkeypatch):
    from src.openship.config import settings
    from src.openship.sandboxes.profiles import reset_runtime_catalog

    monkeypatch.setattr(settings, "sandbox_endpoint", "http://sandbox.test")
    monkeypatch.setattr(settings, "sandbox_deployment_id", "test-deployment")
    yield
    reset_runtime_catalog()

TASK_PUBLIC_EVENT_TYPES = {
    "task.plan_proposed",
    "task.progress",
    "task.disposition",
    "task.decision_requested",
    "task.decision_resolved",
}
PRIVATE_KEYS = (
    "logical_call_id",
    "call_bindings",
    "continuation",
    "runtime_binding",
    "operation_id",
    "input_digest",
    "result_digest",
)


def _task_events(snapshot):
    return [
        event
        for event in snapshot.events
        if event.type in TASK_PUBLIC_EVENT_TYPES
    ]


def _replayed_task(events):
    """Fold the public task events, in sequence order, into one view."""
    plan = None
    state = {
        "schema_version": None,
        "run_id": None,
        "mode": None,
        "revision": 0,
        "plan": None,
        "status": "unknown",
        "steps": [],
        "skipped_steps": [],
        "verified_outputs": [],
        "last_disposition": "none",
        "pending_decision": None,
    }
    for event in sorted(events, key=lambda item: item.sequence):
        payload = event.payload
        if event.type == "task.plan_proposed":
            plan = {
                "revision": payload["revision"],
                "review_digest": payload["review_digest"],
                "steps": payload["steps"],
            }
            state["revision"] = payload["revision"]
            state["mode"] = payload["mode"]
            state["status"] = "planned"
        elif event.type == "task.progress":
            for key in (
                "schema_version",
                "run_id",
                "mode",
                "revision",
                "status",
                "steps",
                "skipped_steps",
                "verified_outputs",
                "last_disposition",
            ):
                state[key] = payload[key]
        elif event.type == "task.disposition":
            state["last_disposition"] = payload["disposition"]
        elif event.type == "task.decision_requested":
            # A parked request both names the decision and fences its worker:
            # the task is waiting for this person by the time the event lands.
            state["pending_decision"] = {
                "request_id": payload["request_id"],
                "kind": payload["kind"],
                "plan_revision": payload["revision"],
                "expires_at": payload["expires_at"],
            }
            state["status"] = "waiting"
        elif event.type == "task.decision_resolved":
            state["pending_decision"] = None
    state["plan"] = plan
    return state


def test_snapshot_matches_event_replay_without_private_fields():
    with TestSessionLocal() as db:
        user, project, conversation, run, claim = _identity(db)
        _pinned_execution(run, user, project, conversation)
        db.flush()
        save_plan(db, claim, _plan(), expected_revision=0)
        db.commit()

        operation = _operation(db, run, user, status="failed")
        db.commit()
        record_attempt_result(
            db, claim, step_id="inspect", attempt=1, operation_id=operation.id
        )
        db.commit()
        record_verification(
            db,
            claim,
            step_id="inspect",
            report=VerificationReport(
                step_id="inspect",
                passed=False,
                checks=(
                    VerificationCheck(name="chart exists", passed=False),
                ),
                evidence_refs=(),
                missing=("chart.png",),
                explanation="the attempt failed before publishing",
            ),
        )
        db.commit()
        save_plan(db, claim, _plan(revision=2), expected_revision=1)
        db.commit()
        parked = park_task_wait(
            db,
            claim,
            kind="clarification",
            prompt="Which source should the chart use?",
            now=datetime.now(UTC),
        )
        db.commit()

        snapshot = read_snapshot(db, user.id, str(conversation.id))
        assert len(snapshot.tasks) == 1
        task = snapshot.tasks[0]
        assert task.run_id == run.id
        assert task.mode == "copilot"
        assert task.revision == 2
        assert task.plan is not None
        assert task.plan.revision == 2
        assert task.status == "waiting"
        assert task.last_disposition == "known_failure"
        assert [(step.id, step.status) for step in task.steps] == [("inspect", "failed")]
        assert task.pending_decision is not None
        assert task.pending_decision.request_id == parked.pending_decision.request_id
        assert task.pending_decision.plan_revision == 2

        replayed = _replayed_task(_task_events(snapshot))
        task_view = task.model_dump(mode="json")
        pending = task_view.pop("pending_decision")
        assert pending is not None
        pending_view = {
            key: value
            for key, value in pending.items()
            if key in {"request_id", "kind", "plan_revision", "expires_at"}
        }
        assert replayed["pending_decision"] == pending_view
        assert {
            key: value
            for key, value in task_view.items()
            if key != "pending_decision"
        } == {
            key: value for key, value in replayed.items() if key != "pending_decision"
        }

        # What the browser will echo to confirm stays the plan's own digest.
        plan_row = (
            db.query(TaskPlanRecord)
            .filter_by(run_id=run.id)
            .filter_by(revision=task.plan.revision)
            .one()
        )
        assert task.plan.review_digest == plan_row.digest

        carried = json.dumps(task_view | pending_view)
        for event in _task_events(snapshot):
            carried += json.dumps(event.payload)
        for private in PRIVATE_KEYS:
            assert private not in carried

        # A committed approval shows its recorded outcome on the next read.
        apply_decision(
            db,
            principal_id=user.id,
            run_id=run.id,
            command=TaskDecisionCommand(
                client_request_id=uuid.uuid4(),
                request_id=pending["request_id"],
                plan_revision=task.plan.revision,
                expected_digest=task.plan.review_digest,
                action="answer",
                answer="The CSV file from the morning shift",
            ),
            now=datetime.now(UTC),
        )
        db.commit()

        after = read_task_summary(db, run.id)
        assert after.pending_decision is None
        resolved = [
            event
            for event in read_snapshot(
                db, user.id, str(conversation.id)
            ).events
            if event.type == "task.decision_resolved"
        ]
        assert len(resolved) == 1
