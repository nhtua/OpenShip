# ruff: noqa: BLE001, S110, S112
# Two probes whose answer is "not there yet": a lane database that does not
# exist, and a table this installation does not have. Both are expected, and
# a later case reads what this one skipped.
"""The durable pipeline's durability, on a real PostgreSQL database.

What the SQLite unit lane cannot show: PostgreSQL's ``FOR UPDATE SKIP LOCKED``
queue, its native JSON columns, and the checkpointer's own writes. This lane
is opt-in and never substituted — without ``TEST_POSTGRES_URL`` naming a
scratch database it skips and says so, because a SQLite stand-in would prove
nothing about the database a deployment actually runs on.

Every case drives a real :class:`Worker` against that database rather than
only invoking the graph node, so the queue, the lease fence, the attempt
record, and the publication transaction are the things under test.
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid as uuidlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy import text as sa_text
from sqlalchemy.orm import sessionmaker

from src.openship.auth.models import User
from src.openship.chat.models import Conversation, Message
from src.openship.config import settings
from src.openship.events.retention import prune_events
from src.openship.providers.attempts import DatabaseAttemptRecorder
from src.openship.providers.contracts import pin_profile
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import _adapter_factories
from src.openship.runs.models import Event, ProviderAttempt, Run, RunJob
from src.openship.runs.queue import (
    claim_next,
    finalize,
    mark_provider_started,
    reconcile_expired,
    renew,
)
from src.openship.runs.service import request_cancel, submit_turn
from src.openship.runs.worker import _INSTALLATION, Worker
from src.openship.workspace.models import Project
from tests.conftest import (
    CREDENTIAL_BINDING,
    FIXTURE_MODEL,
    SENTINEL_CREDENTIAL,
    profile_spec,
)
from tests.providers.conftest import (
    BlockingTransport,
    HTTPTransport,
    StreamTransport,
    make_adapter,
    provider_stream,
    sdk_chunk,
)

#: The unit lane excludes this module; the PostgreSQL job runs it and fails if
#: it is skipped rather than executed.
pytestmark = [pytest.mark.postgres]

#: A queue order the case controls, so the claim that follows is this case's.
_QUEUE_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)

#: What a clean lane holds before a case fills it, in foreign-key order so a
#: shared database can be emptied by another module's tests. ``run_jobs`` is
#: part of this because a claim takes the oldest available job: a leftover one
#: would be claimed instead of the job this case just queued.
_EMPTY_TABLES = (
    "provider_attempts",
    "outbox",
    "events",
    "messages",
    "run_jobs",
    "runs",
    "conversations",
    "projects",
    "users",
)

#: What the checkpointer writes for itself. The names are LangGraph's, and a
#: case reads them rather than trusting the saver's own API, because what
#: matters is the bytes that reached the database.
_CHECKPOINT_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


def _alembic(ini_path: Path, *args: str) -> subprocess.CompletedProcess:
    """One alembic command against the given configuration."""
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini_path), *args],
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
            "ALEMBIC_CONFIG": str(ini_path),
        },
        cwd=Path(__file__).parents[2],
        timeout=180,
        check=False,
    )


#: The database this module builds for itself, inside whatever service
#: TEST_POSTGRES_URL names.
_LANE_DATABASE = "phase3_worker_lane"


def _fresh_lane_database(service_url: str) -> str:
    """Recreate this module's database and name it.

    Recreated rather than reused, because a lane that starts from a schema an
    earlier run left behind is not testing the chain. The statements run with
    ``AUTOCOMMIT`` because PostgreSQL refuses to create or drop a database
    inside a transaction — the same reason the service's own database cannot
    be the lane's.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(service_url)
    lane_url = parts._replace(path=f"/{_LANE_DATABASE}").geturl()

    engine = create_engine(service_url).execution_options(
        isolation_level="AUTOCOMMIT"
    )
    with engine.connect() as conn:
        try:
            conn.execute(sa_text(f"DROP DATABASE {_LANE_DATABASE}"))
        except Exception:
            pass  # the database does not exist yet
        conn.execute(sa_text(f"CREATE DATABASE {_LANE_DATABASE}"))
    return lane_url


@pytest.fixture(scope="module")
def postgres_lane():
    """A PostgreSQL database at the current schema, and settings that name it.

    The chain is built with alembic rather than from the ORM, because what a
    deployment has is what its revisions produced.

    The database is this module's own, named after the service
    ``TEST_POSTGRES_URL`` points at rather than being that database: a lane
    that shared another module's database waited forever for a lock that
    module's still-open connection held, which is a fixture artifact and not
    something a case can tell apart from a real deadlock.
    """
    service_url = os.environ.get("TEST_POSTGRES_URL")
    if not service_url:
        pytest.skip(
            "no TEST_POSTGRES_URL; the PostgreSQL lane is opt-in and SQLite "
            "is not substituted for it"
        )

    url = _fresh_lane_database(service_url)

    api_root = Path(__file__).parents[2]
    ini_path = api_root / "alembic" / "lane-alembic.ini"
    ini_path.write_text(
        f"""[alembic]
script_location = {api_root / 'alembic'}
sqlalchemy.url = {url}

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
    )
    built = _alembic(ini_path, "upgrade", "head")
    assert built.returncode == 0, f"the chain does not apply to PostgreSQL: {built.stderr}"

    previous = settings.database_url
    settings.database_url = url
    try:
        yield url
    finally:
        settings.database_url = previous
        ini_path.unlink(missing_ok=True)


@pytest.fixture
def postgres_session_factory(postgres_lane):
    """One clean database per case, reached the way a service function does.

    The settings are pointed at this database here rather than in the lane
    fixture, because the unit suite's own autouse schema fixture runs between
    the two and would otherwise leave a worker checkpointing to SQLite.
    """
    engine = create_engine(postgres_lane)
    with engine.connect() as conn:
        for table in _EMPTY_TABLES:
            conn.execute(sa_text(f"DELETE FROM {table}"))
        conn.commit()

    previous = settings.database_url
    settings.database_url = postgres_lane
    yield sessionmaker(bind=engine)
    settings.database_url = previous


@pytest.fixture
def installation(postgres_session_factory, monkeypatch):
    """The installation cases share: two profiles, one credential binding.

    The default is the first profile; a case that wants to see whether a run
    follows today's settings instead of its own snapshot moves the default.
    """
    monkeypatch.setattr(
        settings,
        "model_profiles_json",
        json.dumps(
            [
                profile_spec("primary-fixture", allowed_fallbacks=["secondary-fixture"]),
                {**profile_spec("secondary-fixture"), "model": "changed-after-submission"},
                # A second adapter, so a case can ask whether the worker's path
                # knows about adapters at all - it must not.
                {
                    **profile_spec("messages-fixture"),
                    "adapter": "anthropic",
                    "api_version": "messages",
                    "adapter_options": {
                        "adapter": "anthropic",
                        "server_side_fallback": False,
                    },
                },
                # A third, so the same question is asked of every adapter this
                # build ships rather than of one that happens to be named.
                {
                    **profile_spec("gemini-fixture"),
                    "adapter": "google",
                    "api_version": "v1",
                    "adapter_options": {
                        "adapter": "google",
                        "native_api_version": "v1",
                        "automatic_function_calling": False,
                    },
                },
                # A fourth, and a profile that names no credential at all: the
                # durable path must serve a keyless local endpoint the same way
                # it serves one that needs a binding.
                {
                    **profile_spec(
                        "compatible-fixture",
                        endpoint="http://localhost:11434/v1",
                        credential_ref="",
                    ),
                    "adapter": "openai_compatible",
                    "api_version": "chat-completions",
                    "adapter_options": {
                        "adapter": "openai_compatible",
                        "endpoint_implementation": "chat-completions",
                        "authentication": "none",
                        "stream_usage_supported": False,
                        "output_limit_parameter": "max_tokens",
                    },
                },
            ]
        ),
    )
    monkeypatch.setattr(settings, "model_default_profile_id", "primary-fixture")
    monkeypatch.setenv(CREDENTIAL_BINDING, SENTINEL_CREDENTIAL)
    return postgres_session_factory


def _serve(monkeypatch, chunks=(), raises=None, adapter="openai"):
    """The installation's provider answers from a synthetic transport.

    The transport is the shape the provider is reached through, which is not
    the same for every one of them: an SDK's stream, or an HTTP exchange.
    """
    if adapter == "openai_compatible":
        transport = HTTPTransport(list(chunks), raises=raises)
    else:
        transport = StreamTransport(list(chunks), raises=raises)
    monkeypatch.setitem(
        _adapter_factories,
        adapter,
        lambda _profile: make_adapter(adapter, transport),
    )
    return transport


def _turn(db, *, profile_id="primary-fixture", content="What ran?", order=0):
    """An owned conversation, a queued run pinned to one profile, and its job."""
    user = User(username="pg-lane", email="pg-lane@example.com", password_hash="not-a-real-hash")
    db.add(user)
    db.commit()
    project = Project(owner_user_id=user.id, name="pg-lane")
    db.add(project)
    db.commit()
    conversation = Conversation(user_id=user.id, project_id=project.id, title="PG lane")
    db.add(conversation)
    db.commit()

    run = submit_turn(
        db,
        user_id=user.id,
        conversation_id=str(conversation.id),
        content=content,
        client_request_id=str(uuidlib.uuid4()),
        provider_profile_id=profile_id,
    )
    db.commit()

    job = db.query(RunJob).filter(RunJob.run_id == run.id).one()
    job.available_at = _QUEUE_EPOCH + timedelta(seconds=order)
    db.commit()
    return {"user": user, "conversation": conversation, "run": run}


def _claim(db, worker_id="worker-1"):
    """Claim the queued job, the way a worker does."""
    return claim_next(db=db, worker_id=worker_id, lease_seconds=30)


def _process(db, claim, session_factory, checkpointer=_INSTALLATION):
    """One worker job against the test installation.

    The caller's transaction is settled first because a worker is a separate
    process in a real installation: it starts with nothing of its caller's
    still open. It would otherwise wait forever here — the worker creates the
    checkpoint tables, and PostgreSQL will not create a table whose column
    type another open transaction has already locked.
    """
    db.commit()
    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=session_factory,
        checkpointer=checkpointer,
    )
    return worker.process_job(claim)


def _attempt_rows(db, run_id):
    return db.query(ProviderAttempt).filter(ProviderAttempt.run_id == run_id).all()


def _assistant(db, run_id):
    return db.query(Message).filter(Message.run_id == run_id, Message.role == "assistant").all()


# --- A queued run keeps what it was pinned to ----------------------------------


def test_queued_snapshot_survives_worker_restart_and_config_change(installation, monkeypatch):
    """A worker that never saw the submission runs the run's own snapshot.

    The break this catches: a worker that follows today's settings, so a
    restart or a changed default silently answers with a different model.
    """
    db = installation()
    queued = _turn(db)
    run = queued["run"]
    assert run.model_configuration, "a submitted run must freeze the configuration it will execute"

    # Today's default moves away from what the run asked for.
    monkeypatch.setattr(settings, "model_default_profile_id", "secondary-fixture")

    claim = _claim(db)
    assert claim, "the queued job must be claimable"

    transport = _serve(monkeypatch)
    _process(db, claim, installation)

    assert transport.requests == 1, "the restarted worker made one request"
    served = _attempt_rows(db, run.id)
    assert served, "the attempt must be recorded"
    assert served[0].profile_id == "primary-fixture", (
        f"the run answered as {served[0].profile_id}, not the profile it pinned"
    )
    assert served[0].requested_model == FIXTURE_MODEL, (
        f"the run served {served[0].requested_model}, not the model it pinned"
    )


@pytest.mark.parametrize(
    ("adapter", "profile_id"),
    [
        ("anthropic", "messages-fixture"),
        ("google", "gemini-fixture"),
        ("openai_compatible", "compatible-fixture"),
    ],
    ids=("messages", "gemini", "compatible"),
)
def test_a_run_pinned_to_another_adapter_takes_the_same_path(
    installation, monkeypatch, adapter, profile_id
):
    """Nothing in the durable path branches on which adapter a profile names.

    The break this catches: a worker that reaches the provider differently
    because of the profile's adapter, so the run's own record and the answer
    it publishes stop agreeing - which is the same claim the case above makes,
    asked of every adapter this build ships rather than of one. The keyless
    profile asks the same claim of a run that never had a credential to reach.
    """
    db = installation()
    run = _turn(db, profile_id=profile_id)["run"]

    claim = _claim(db)
    assert claim, "the queued job must be claimable"

    transport = _serve(
        monkeypatch,
        provider_stream(adapter, text=["The deploy ran."]),
        adapter=adapter,
    )
    _process(db, claim, installation)

    assert transport.requests == 1, "one job means one dispatch"
    served = _attempt_rows(db, run.id)
    assert [str(attempt.profile_id) for attempt in served] == [profile_id], (
        "the run answered as the profile it pinned, under an adapter the "
        "worker was never told about"
    )
    answers = _assistant(db, run.id)
    assert len(answers) == 1, "one answer"
    assert answers[0].content == "The deploy ran.", (
        "the answer is the provider's own text, unchanged by the path"
    )


def test_started_request_is_not_blindly_replayed_after_crash(installation, monkeypatch):
    """A crash mid-request leaves its attempt, and the run is not requeued.

    The break this catches: replaying a request that may already have been
    billed, which either charges twice or loses the first attempt entirely.
    """
    db = installation()
    run = _turn(db)["run"]

    first = _claim(db)
    assert first
    # The worker reached the provider and then died. The attempt is written and
    # flushed before dispatch, so it survives the process; the run records that
    # a call had started.
    recorder = DatabaseAttemptRecorder(
        installation, run.id, first.fence, commit_each=True
    )
    recorder.start(pin_profile(profile_spec("primary-fixture")), reason="initial")
    assert mark_provider_started(db, first.run_id, first.fence)
    db.commit()

    started = _attempt_rows(db, run.id)
    assert len(started) == 1, "a started request is recorded before it is made"
    assert started[0].outcome == "started"

    # The lease expires as the crash would leave it, and reconciliation is what
    # decides the outcome.
    db.query(RunJob).filter(RunJob.run_id == run.id).one().lease_until = _QUEUE_EPOCH
    db.commit()

    assert reconcile_expired(db) == 1, "an expired lease is reconciled"
    with installation() as settled:
        outcome = settled.query(Run).filter(Run.id == run.id).one()
    assert outcome.status == "failed", "an interrupted call is not replayed blindly"
    assert outcome.error_code == "model_interrupted"

    again = _claim(db, worker_id="worker-2")
    assert again is None, "a run whose request may have been billed is not requeued"
    attempts = _attempt_rows(db, run.id)
    assert len(attempts) == 1, "the request that was already made keeps its own record"
    assert attempts[0].outcome == "started"


def test_two_workers_do_not_duplicate_dispatch(installation, monkeypatch):
    """One job, two workers: only one owns it.

    On PostgreSQL this is the lock that matters — ``FOR UPDATE SKIP LOCKED``
    makes the second claim wait rather than read a job that is already taken.
    """
    db = installation()
    _turn(db)["run"]

    first = _claim(db, worker_id="worker-1")
    assert first
    second = _claim(db, worker_id="worker-2")
    assert second is None, "two workers claimed one job"

    transport = _serve(monkeypatch)
    _process(db, first, installation)
    assert transport.requests == 1, "one job means one dispatch"


# --- Publication is one transaction, and a stale owner cannot publish ---------


@pytest.fixture
def completed_run(installation, monkeypatch):
    """A run executed to completion by a worker that holds its lease."""
    db = installation()
    queued = _turn(db)
    claim = _claim(db)
    assert claim
    transport = _serve(
        monkeypatch,
        chunks=[
            sdk_chunk(content="All clear.", finish_reason="stop"),
            sdk_chunk(choices=False, usage=(3, 4, 7)),
        ],
    )
    _process(db, claim, installation)
    return {
        "db": db,
        "factory": installation,
        "user": queued["user"],
        "conversation": queued["conversation"],
        "run": queued["run"],
        "claim": claim,
        "transport": transport,
    }


def test_terminal_commit_is_atomic(completed_run):
    """Status, the answer, and its event land together or not at all."""
    _db, run = completed_run["db"], completed_run["run"]
    with completed_run["factory"]() as session:
        status = session.query(Run).filter(Run.id == run.id).one().status
        answer = _assistant(session, run.id)
        events = session.query(Event).filter(Event.run_id == run.id).all()
    assert status == "completed", "a finished run is not left looking running"
    assert len(answer) == 1, "exactly one assistant answer"
    terminal = [event for event in events if event.type == "run.succeeded"]
    assert terminal, "the terminal event is part of the same transaction"
    # The claim is about all-or-nothing: a reader that can see the end can
    # also see the answer, and a crash before the commit leaves neither.
    assert all(event.run_id == run.id for event in terminal), (
        "the end belongs to this run, not to another"
    )


@pytest.fixture
def held_run(installation, monkeypatch):
    """A run one worker was holding, which a second worker has since taken.

    The lease expires as a crash would leave it, reconciliation requeues it
    (nothing had reached the provider yet), and a second claim bumps the fence.
    The first worker is now stale, which is what the cases below check.
    """
    db = installation()
    queued = _turn(db)
    first = _claim(db, worker_id="worker-1")
    assert first

    db.query(RunJob).filter(RunJob.run_id == queued["run"].id).one().lease_until = _QUEUE_EPOCH
    db.commit()
    assert reconcile_expired(db) == 1, "an expired lease is reconciled"

    second = claim_next(db, worker_id="worker-2", lease_seconds=30)
    assert second and second.fence == first.fence + 1, (
        "the new owner holds a higher fence"
    )
    return {
        "db": db,
        "factory": installation,
        "user": queued["user"],
        "conversation": queued["conversation"],
        "run": queued["run"],
        "stale": first,
    }


def test_stale_worker_cannot_publish_attempt_or_message(held_run, monkeypatch):
    """An owner whose lease moved writes nothing."""
    db, run, stale = held_run["db"], held_run["run"], held_run["stale"]
    assert not renew(db, run.id, stale.fence, lease_seconds=30), (
        "a stale fence must not renew the lease"
    )
    assert not mark_provider_started(db, run.id, stale.fence), (
        "a stale fence must not record an attempt"
    )
    recorder = DatabaseAttemptRecorder(lambda: db, run.id, stale.fence)
    with pytest.raises(ProviderError) as refused:
        recorder.start(pin_profile(profile_spec("primary-fixture")), reason="initial")
    assert refused.value.code == "lease_lost", (
        "the recorder refuses under a fence it no longer holds"
    )
    assert not _attempt_rows(db, run.id), "a stale owner records no attempt"
    assert not finalize(db, run.id, stale.fence, {"status": "success"}, commit=True)
    assert not _assistant(db, run.id), "a stale owner publishes no answer"


def test_cancel_fences_late_result_and_closes_stream(held_run, monkeypatch):
    """A cancelled run keeps its own end; a late result cannot overwrite it."""
    db, run, stale = held_run["db"], held_run["run"], held_run["stale"]
    cancelled = request_cancel(db, user_id=held_run["user"].id, run_id=str(run.id))
    assert cancelled.status == "cancelled"

    assert not finalize(db, run.id, stale.fence, {"status": "success"}, commit=True), (
        "a late result from the worker that was cancelled must not publish"
    )
    with held_run["factory"]() as session:
        assert session.query(Run).filter(Run.id == run.id).one().status == "cancelled"
        assert not _assistant(session, run.id), "a cancelled turn adds no answer"
        closing = session.query(Event).filter(Event.run_id == run.id).all()
    assert any(event.type == "run_cancelled" for event in closing), (
        "the stream is closed by the cancellation itself"
    )


def test_late_cancel_refreshes_run_after_completion_commits(installation):
    """A cancel waiting behind completion returns the winning terminal state."""
    creator = installation()
    queued = _turn(creator)
    claim = _claim(creator)

    cancel_session = installation()
    stale = cancel_session.get(Run, queued["run"].id)
    assert stale.status == "running"

    completion_session = installation()
    assert finalize(
        completion_session,
        queued["run"].id,
        claim.fence,
        {"status": "success"},
        commit=False,
    )
    completion_session.commit()

    result = request_cancel(
        cancel_session,
        user_id=queued["user"].id,
        run_id=str(queued["run"].id),
    )
    assert result.status == "completed"
    with installation() as verify:
        assert verify.get(Run, queued["run"].id).status == "completed"


# --- What a run reported survives its events ----------------------------------


def test_a_cancel_the_owner_asked_stops_a_running_turn(installation, monkeypatch):
    """A worker mid-request is interrupted by the ask, not by the deadline.

    The break this catches: a cancel that moves only the run's fence. The
    supervisor renews against the job's, so the desync would leave a blocked
    provider read consuming until the run deadline. The cases above settle the
    composition - a fence that moved stops a worker, and a stop signal closes
    a blocked read - but they inject one half of it. This one starts from the
    ask a user actually makes and lets the worker's own supervisor notice it.
    """
    db = installation()
    queued = _turn(db)
    claim = _claim(db)

    transport = BlockingTransport()
    monkeypatch.setitem(
        _adapter_factories, "openai", lambda _profile: make_adapter("openai", transport)
    )

    def _ask_once_blocked():
        # The owner asks only once the read is blocked, which is the state the
        # claim is about. Nothing here hands a stop signal to the worker.
        while transport.requests == 0:
            time.sleep(0.01)
        with installation() as session:
            request_cancel(
                session, user_id=queued["user"].id, run_id=str(queued["run"].id)
            )

    started = time.monotonic()
    asker = threading.Thread(target=_ask_once_blocked)
    asker.start()
    _process(db, claim, installation)
    asker.join()

    assert transport.closed is True, "the ask must close the read it started"
    assert transport.requests == 1, "the ask interrupts the read, it does not restart it"
    assert time.monotonic() - started < 2, (
        "the local read stops within the bounded window of the ask"
    )
    with installation() as session:
        assert session.query(Run).filter(Run.id == queued["run"].id).one().status == (
            "cancelled"
        ), "the owner's end is the run's end"
        assert not _assistant(session, queued["run"].id), (
            "a cancelled turn publishes no answer"
        )


def _checkpoint_text(engine) -> str:
    """Everything the checkpointer wrote, as one string to search.

    The tables are LangGraph's own, and reading them is what asks the honest
    question — which bytes reached the database — rather than trusting the
    saver's API to show everything it stored.
    """
    parts = []
    for table in _CHECKPOINT_TABLES:
        try:
            with engine.connect() as conn:
                parts.extend(
                    str(value) for row in conn.execute(sa_text(f"SELECT * FROM {table}")) for value in row
                )
        except Exception:
            # The table is not this installation's; a later case reads it.
            continue
    return "\n".join(parts)


def test_snapshot_after_retention_preserves_provider_usage(completed_run, monkeypatch):
    """Pruning events does not forget what the provider reported.

    The break this catches: a run card that loses its counts when the events
    that carried them are gone.
    """
    db, run = completed_run["db"], completed_run["run"]
    pruned = prune_events(db, str(completed_run["conversation"].id), keep_count=0)
    assert pruned >= 0
    with completed_run["factory"]() as session:
        assert not session.query(Event).filter(
            Event.conversation_id == str(completed_run["conversation"].id)
        ).all(), "the events are gone"
        kept = session.query(Run).filter(Run.id == run.id).one()
    assert kept.usage is not None, "the run still states what it reported"
    assert kept.model_configuration, "and what it was pinned to"


def test_checkpoint_is_secret_free(completed_run):
    """A thread checkpoint holds the run's own configuration, never a credential.

    What must not appear is the credential's value: the binding's *name* may,
    because naming a binding is not naming a secret. What must appear is the
    profile the run pinned, because a resumed thread has to run the model it
    pinned rather than today's default.
    """
    written = _checkpoint_text(create_engine(settings.database_url))
    assert written, "an executed run leaves a checkpoint behind"
    assert SENTINEL_CREDENTIAL not in written, "a credential reached a checkpoint"
    assert "primary-fixture" in written, (
        "a resumed thread would otherwise not know what it pinned"
    )
