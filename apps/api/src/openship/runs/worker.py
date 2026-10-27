# ruff: noqa: BLE001, S110
# Worker supervision is deliberately broad: a run whose heartbeat, cleanup, or
# reconciliation failed must not stop the worker from serving the next one,
# and each site records what it could not do.
"""Worker entry point for processing queued chat turns.

Claims jobs with lease-based ownership, executes the LangGraph chat turn
through the configuration the run pinned, and finalizes the run with fence-
based optimistic concurrency control.
"""

import argparse
import json
import logging
import re
import signal
import threading
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..artifacts.filesystem import ArtifactStorageError
from ..artifacts.maintenance import configured_store
from ..capabilities.contracts import (
    AuthorizationContext,
    PublishedToolResult,
    ToolInvocationContext,
    decode_execution_config,
)
from ..capabilities.policy import read_effective_tool_policy
from ..chat.models import Conversation, Message
from ..config import settings
from ..connectors.blockers import park_connector_wait
from ..connectors.readiness import preflight_connector_references
from ..execution.availability import (
    observe_tool_availability,
    publish_tool_availability,
)
from ..execution.gateway import ToolGateway
from ..providers.attempts import DatabaseAttemptRecorder
from ..providers.control import InvocationControl
from ..providers.credentials import credential_value
from ..providers.errors import ProviderError
from ..providers.runner import authorize_current_profile
from ..runs.schemas import _model_projection
from ..runs.service import bind_run_model_config
from .dispatch import UnsupportedGraphVersion, resolve_graph
from .graph import ModelResult
from .models import ProviderAttempt, Run, RunJob
from .queue import (
    begin_execution_window,
    claim_next,
    finalize,
    mark_provider_started,
    reconcile_expired,
    renew,
)

logger = logging.getLogger("worker")

#: Built in __init__ when the caller did not say which checkpointer to use.
_INSTALLATION = object()

#: The run status a normalized provider failure shows.
_OUTCOME = {"budget_exceeded": "timed_out", "cancelled": "cancelled"}

#: The usage record a run keeps: provider counts, plus what old consumers read.
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
)
USAGE_SCHEMA_VERSION = 1


def _run_outcome(error_code: str) -> str:
    """The run status a normalized provider failure shows."""
    return _OUTCOME.get(error_code, "failed")


def _chat_system_prompt(
    *, is_first: bool, preferred_search_kind: str | None = None
) -> str:
    if preferred_search_kind in {"brave", "tavily"}:
        preferred_name = "Brave" if preferred_search_kind == "brave" else "Tavily"
        search_guidance = (
            f"For automatic searches, use {preferred_name} Search first because it is the project's "
            "default. If that search returns an error, retry with another available search provider. "
            "Do not repeat an equivalent successful search through another provider unless the user "
            "asks to compare providers. Explicit manual connector selections take precedence."
        )
    else:
        search_guidance = (
            "When equivalent search tools from different providers are available, choose one provider "
            "and use its results. If the chosen provider returns an error, retry with another available "
            "search provider. Do not repeat an equivalent successful search through another provider "
            "unless the user asks to compare providers."
        )
    system_content = (
        "You are OpenShip, an AI DevOps co-pilot. Help users with infrastructure, deployment, "
        "and operations tasks. Be concise and practical. "
        f"{search_guidance}"
    )
    if is_first:
        system_content += "\n\nAfter your response, include a short 50-character summary on a new line prefixed with '## Title: '. This will be used as the conversation title. Do not include any other content after the title."
    return system_content


def _terminal_model(job, run) -> dict | None:
    """The safe provider summary a terminal event carries.

    A terminal event has to be readable on its own: a conversation whose
    events were pruned by retention still has to say which profile a run was
    pinned to and which model answered. The run's own record is what says
    that, so the summary is projected from it and from the attempts it made -
    never from the events that may already be gone.
    """
    attempts = job.query(ProviderAttempt).filter(ProviderAttempt.run_id == run.id).all()
    summary = _model_projection(run.model_configuration, attempts)
    return summary.model_dump(mode="json") if summary is not None else None


def _usage_record(usage) -> dict | None:
    """A run's own usage as JSON text, schema-versioned, never an invention.

    ``estimated`` is kept for old consumers and means the provider's totals
    are incomplete, not that any value was estimated here.
    """
    if usage is None:
        return None
    record = {name: usage.get(name) for name in USAGE_FIELDS}
    record["schema_version"] = USAGE_SCHEMA_VERSION
    reported = [
        name
        for name in ("input_tokens", "output_tokens", "total_tokens")
        if record[name] is not None
    ]
    record["estimated"] = len(reported) < 3
    return record


class Supervision:
    """The stop signal one job's model turn obeys, and why it was set."""

    def __init__(self):
        self._stop = threading.Event()
        self._lease_lost = threading.Event()

    def stop(self) -> bool:
        return self._stop.is_set()

    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    def interrupt(self) -> None:
        self._stop.set()

    def deny_lease(self) -> None:
        """A denied heartbeat or failed ownership check stops the turn."""
        self._stop.set()
        self._lease_lost.set()


class Worker:
    """A chat turn worker that claims and executes queued jobs."""

    def __init__(
        self,
        worker_id: str | None = None,
        session_factory=None,
        checkpointer=_INSTALLATION,
    ):
        if worker_id is None:
            worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self.worker_id = worker_id
        self.running = True
        self._closed = False

        # Set up database connection
        self.engine = create_engine(settings.database_url)
        self.Session = session_factory or sessionmaker(bind=self.engine)

        # Set up checkpointer
        self.checkpointer = None
        self._checkpointer_ctx = None
        if checkpointer is not _INSTALLATION:
            self.checkpointer = checkpointer
        else:
            try:
                from langgraph.checkpoint.postgres import PostgresSaver
                from psycopg.conninfo import make_conninfo

                # from_conn_string takes only the connection string: the pool
                # keyword arguments this installation once passed are not part
                # of the saver's API, and passing them left every worker
                # without a checkpointer at all.
                # psycopg accepts a PostgreSQL URI, not SQLAlchemy's
                # postgresql+psycopg / postgresql+psycopg2 driver scheme.
                # Build libpq connection info from parsed fields so query
                # options and credentials keep their original encoding.
                checkpoint_url = settings.database_url
                if self.engine.dialect.name == "postgresql":
                    connection_fields = self.engine.url.translate_connect_args(
                        username="user", database="dbname"
                    )
                    connection_fields.update(self.engine.url.query)
                    checkpoint_url = make_conninfo(**connection_fields)
                context = PostgresSaver.from_conn_string(checkpoint_url)
                self.checkpointer = context.__enter__()
                self._checkpointer_ctx = context
                # The saver's tables are what make a thread resumable; without
                # this the graph silently has nowhere to write a checkpoint.
                if self.engine.dialect.name == "postgresql":
                    import psycopg

                    # Saver setup runs DDL outside our SQLAlchemy transaction.
                    # Serialize independent slots/processes on a separate
                    # autocommit connection; closing it also releases the lock
                    # if setup raises. Each saver keeps its own connection.
                    with psycopg.connect(
                        checkpoint_url,
                        autocommit=True,
                        connect_timeout=5,
                        application_name="openship-checkpointer-setup",
                    ) as setup_guard:
                        # A blocking lock statement would leave a transaction
                        # open while waiting. Saver's concurrent-index DDL can
                        # itself wait for that transaction, deadlocking startup.
                        # Try in short autocommit statements on this slot thread.
                        deadline = time.monotonic() + 30
                        while not setup_guard.execute(
                            "SELECT pg_try_advisory_lock(5716001871110882128)"
                        ).fetchone()[0]:
                            if time.monotonic() >= deadline:
                                raise RuntimeError("checkpointer setup lock timed out")
                            time.sleep(0.05)
                        self.checkpointer.setup()
                else:
                    self.checkpointer.setup()
                logger.info("Checkpointer initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize checkpointer: {e}")
                context, self._checkpointer_ctx = self._checkpointer_ctx, None
                if context is not None:
                    try:
                        context.__exit__(None, None, None)
                    except Exception:
                        logger.warning(
                            "Failed to close checkpointer after setup failure"
                        )
                self.checkpointer = None

        logger.info(f"Worker {self.worker_id} initialized")

    def close(self):
        """Release this resource's checkpointer and engine exactly once."""
        if self._closed:
            return
        self._closed = True
        self.running = False
        context, self._checkpointer_ctx = self._checkpointer_ctx, None
        try:
            if context is not None:
                context.__exit__(None, None, None)
        finally:
            self.checkpointer = None
            self.engine.dispose()

    def _supervise_in_thread(self, supervision, run_id, fence):
        # Session creation, use and closure all belong to this thread.
        try:
            with self.Session() as db:
                self._supervise(supervision, db, run_id, fence)
        except Exception:
            supervision.deny_lease()
            logger.warning("Worker supervisor session failed")

    def _supervise(self, supervision, db, run_id, fence):
        """Watch the run while its turn runs, and keep its lease alive.

        Two concerns, one loop, because they are not the same kind of work.
        The frequent one is a **read**: the run's own fence and status are
        what a cancellation the owner asked changes, and reading them on a
        short interval is what interrupts a blocked provider read inside the
        bounded window this repo guarantees. The rare one is a **write** -
        renewing the lease so a long turn is not reclaimed by another worker.
        A write on the short interval would race the worker's own open
        transaction, which is exactly what a short turn on SQLite does, so the
        two intervals are kept apart.
        """
        poll = getattr(settings, "worker_cancellation_poll_seconds", 0.5)
        renew_every = getattr(settings, "worker_heartbeat_interval", 10)

        waited = 0.0
        availability_waited = 0.0
        while not supervision.stop():
            # A poll on an interval: the first check happens while the turn is
            # still running, so a lease that moved interrupts a blocked read
            # rather than deciding the turn before it began.
            time.sleep(poll)
            waited += poll
            availability_waited += poll
            if supervision.stop():
                return
            try:
                if self._ownership_lost(db, run_id, fence):
                    logger.warning(
                        f"[{self.worker_id}] Ownership moved for run {run_id} (fence={fence})"
                    )
                    supervision.deny_lease()
                    return
            except Exception as e:
                logger.warning(
                    f"[{self.worker_id}] Supervisor error for run {run_id}: {e}"
                )
                supervision.deny_lease()
                return

            if waited < renew_every:
                if availability_waited >= 10:
                    availability_waited = 0.0
                    try:
                        self._refresh_tool_availability(db)
                    except Exception:
                        db.rollback()
                        logger.warning("Runtime tool availability refresh failed")
                continue
            waited = 0.0
            if availability_waited >= 10:
                availability_waited = 0.0
                try:
                    self._refresh_tool_availability(db)
                except Exception:
                    db.rollback()
                    logger.warning("Runtime tool availability refresh failed")
            try:
                if not renew(db, run_id, fence):
                    logger.warning(
                        f"[{self.worker_id}] Heartbeat failed for run {run_id} (fence={fence})"
                    )
                    supervision.deny_lease()
                    return
            except Exception:
                db.rollback()
                logger.warning("Worker heartbeat failed")
                supervision.deny_lease()
                return

    def _ownership_lost(self, db, run_id, fence) -> bool:
        """Whether this worker is still the owner of the run it is working on.

        A read of both halves of the invariant the queue keeps: a claim moves
        the run's fence and its job's together, and so does a cancellation the
        owner asked. Either half having moved means this worker no longer holds
        the turn, so its read must stop.
        """
        run = db.query(Run).filter(Run.id == run_id).populate_existing().first()
        if run is None or run.fence != fence:
            return True
        if run.status not in ("queued", "running", "paused"):
            return True
        job = (
            db.query(RunJob).filter(RunJob.run_id == run_id).populate_existing().first()
        )
        return job is None or job.fence != fence

    def _refresh_tool_availability(self, db) -> None:
        policy = read_effective_tool_policy(db)
        observations = observe_tool_availability(enabled_ids=policy.effective_ids)
        publish_tool_availability(db, self.worker_id, observations)
        db.commit()

    def _tool_context(self, claim, run, conversation, execution):
        try:
            store = configured_store()
            store.verify()
        except ArtifactStorageError as exc:
            logger.warning("Conversation artifact storage is unavailable: %s", exc)
            store = None
        return ToolInvocationContext(
            authorization=AuthorizationContext(
                principal_id=conversation.user_id,
                project_id=run.project_id,
                conversation_id=conversation.id,
                action="execute_tool",
                target_id=str(run.id),
            ),
            execution=execution,
            credential_resolver=credential_value,
            artifact_store=store,
            session_factory=self.Session,
        )

    def _finalize_tool_run(
        self,
        db,
        run,
        conversation,
        claim,
        *,
        outcome: str,
        error_code: str | None = None,
        safe_message: str | None = None,
        operation_id: uuid.UUID | None = None,
    ) -> bool:
        from ..events.service import append_event
        from ..providers.errors import SAFE_MESSAGES

        # Tool publication and cancellation use conversation → run lock order.
        locked_conversation = (
            db.query(Conversation)
            .filter(Conversation.id == conversation.id)
            .with_for_update()
            .first()
        )
        if locked_conversation is None:
            db.rollback()
            return False
        finalized = finalize(
            db=db,
            run_id=run.id,
            fence=claim.fence,
            result={
                "status": outcome,
                "error_code": error_code,
            },
            commit=False,
        )
        if not finalized:
            db.rollback()
            return False
        if outcome == "success":
            event_type = "run.succeeded"
            payload = {
                "kind": "tool",
                "operation_id": str(operation_id) if operation_id else None,
            }
        else:
            event_type = "run.failed"
            payload = {
                "kind": "tool",
                "error_code": error_code,
                "error": safe_message
                or SAFE_MESSAGES.get(
                    error_code, "The tool execution could not be completed safely."
                ),
                "model": None,
            }
        append_event(
            db,
            conversation_id=conversation.id,
            run_id=run.id,
            event_type=event_type,
            payload=payload,
        )
        db.flush()
        db.commit()
        return True

    def _process_tool_job(
        self,
        claim,
        job,
        supervision,
        run,
        conversation,
        graph_factory,
    ) -> bool:
        try:
            execution = decode_execution_config(run.execution_configuration or "")
        except Exception:
            return self._finalize_tool_run(
                job,
                run,
                conversation,
                claim,
                outcome="failed",
                error_code="configuration_error",
            )
        if (
            execution.graph_kind != "tool"
            or execution.graph_version != run.graph_version
        ):
            return self._finalize_tool_run(
                job,
                run,
                conversation,
                claim,
                outcome="failed",
                error_code="configuration_error",
            )

        thread_id = f"{run.id}-{claim.checkpoint_ns}"
        window = begin_execution_window(
            job,
            claim,
            deadline_seconds=execution.loop_limits.deadline_seconds,
            graph_thread_id=thread_id,
        )
        if window is None:
            supervision.deny_lease()
            return False
        _started_at, deadline_at = window
        remaining = max(0.0, (deadline_at - datetime.now(UTC)).total_seconds())
        deadline = time.monotonic() + remaining
        if remaining <= 0:
            return self._finalize_tool_run(
                job,
                run,
                conversation,
                claim,
                outcome="timed_out",
                error_code="budget_exceeded",
            )

        readiness_failure = preflight_connector_references(
            session_factory=self.Session,
            project_id=run.project_id,
            references=tuple(getattr(execution, "connector_refs", ())),
        )
        if readiness_failure is not None:
            parked = park_connector_wait(
                job,
                claim=claim,
                run_id=run.id,
                conversation_id=conversation.id,
                project_id=run.project_id,
                execution=execution,
                failure=readiness_failure,
            )
            if not parked:
                supervision.deny_lease()
            return parked

        def invocation_factory(state):
            if state.get("run_id") != str(run.id):
                raise ProviderError("lease_lost")
            context = self._tool_context(claim, run, conversation, execution)
            control = InvocationControl(
                deadline=deadline,
                clock=time.monotonic,
                stop=supervision.stop,
            )
            return claim, context, control

        graph = graph_factory(
            self.checkpointer,
            gateway_factory=lambda: ToolGateway(self.Session),
            invocation_factory=invocation_factory,
        )
        state = {
            "run_id": str(run.id),
            "tool_command_json": run.tool_command_json or "",
            "execution_configuration": run.execution_configuration or "",
        }
        supervisor_thread = threading.Thread(
            target=self._supervise_in_thread,
            args=(supervision, run.id, claim.fence),
            daemon=True,
        )
        supervisor_thread.start()
        result = None
        graph_failure_code = None
        graph_failure_message = None
        try:
            if self._ownership_lost(job, run.id, claim.fence):
                supervision.deny_lease()
                return False
            graph_state = graph.invoke(
                state,
                config={"configurable": {"thread_id": thread_id}},
            )
            result = PublishedToolResult.model_validate_json(
                json.dumps(graph_state["tool_result"])
            )
        except ProviderError as refused:
            graph_failure_code = refused.code
            graph_failure_message = refused.safe_message
        except Exception:
            logger.warning("Tool graph execution failed before a terminal result")
            graph_failure_code = "execution_interrupted"
            graph_failure_message = "The tool execution was interrupted and will not be repeated automatically."
        finally:
            supervision.interrupt()
            if supervisor_thread.is_alive():
                supervisor_thread.join()

        if supervision.lease_lost():
            return False
        if graph_failure_code is not None:
            if graph_failure_code == "lease_lost":
                return False
            if graph_failure_code == "budget_exceeded":
                outcome = "timed_out"
            elif graph_failure_code == "cancelled":
                outcome = "cancelled"
            else:
                outcome = "failed"
            return self._finalize_tool_run(
                job,
                run,
                conversation,
                claim,
                outcome=outcome,
                error_code=graph_failure_code,
                safe_message=graph_failure_message,
            )

        assert result is not None
        if result.status == "succeeded":
            return self._finalize_tool_run(
                job,
                run,
                conversation,
                claim,
                outcome="success",
                operation_id=result.operation_id,
            )
        if result.status == "cancelled":
            outcome = "cancelled"
        elif result.error and result.error.code == "budget_exceeded":
            outcome = "timed_out"
        else:
            outcome = "failed"
        error_code = result.error.code if result.error else "execution_interrupted"
        if time.monotonic() >= deadline and error_code == "capacity_timeout":
            outcome, error_code = "timed_out", "budget_exceeded"
        return self._finalize_tool_run(
            job,
            run,
            conversation,
            claim,
            outcome=outcome,
            error_code=error_code,
            safe_message=result.error.message if result.error else None,
            operation_id=result.operation_id,
        )

    def _is_first_message(self, db, conversation, user_msg):
        """Check if this is the first message in the conversation."""
        existing = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation.id,
                Message.id != user_msg.id,
            )
            .count()
        )
        return existing == 0

    def _generate_title(self, user_content):
        """Generate a conversation title from the first user message (fallback)."""
        title = user_content.strip()
        if len(title) > 50:
            title = title[:50] + "..."
        return title

    def _extract_title_from_response(self, response):
        """Extract a standalone, at-most-50-character title line."""
        match = re.search(r"(?m)^## Title:[ \t]*(.*)$", response)
        if match:
            title = match.group(1).strip()
            return title[:50] or None
        return None

    def _remove_title_from_response(self, response):
        """Remove complete standalone title lines from the visible answer."""
        cleaned = re.sub(r"(?m)^## Title:[^\r\n]*(?:\r?\n|$)", "", response)
        return cleaned.strip() if cleaned != response else response

    def process_job(self, claim, *, supervision: Supervision | None = None):
        """Process a single claimed job: supervise one model turn, then publish it."""
        run_id = claim.run_id
        fence = claim.fence
        logger.info(f"[{self.worker_id}] Processing run {run_id} (fence={fence})")

        # One session per thread: the worker's own work stays in one transaction,
        # and the supervisor thread gets a session of its own.
        job = None
        supervision = supervision if supervision is not None else Supervision()
        supervisor_thread = None

        try:
            job = self.Session()
            from ..events.service import append_event

            run = job.query(Run).filter(Run.id == run_id).first()
            if not run:
                logger.warning(f"Run {run_id} not found")
                return False

            conv = (
                job.query(Conversation)
                .filter(Conversation.id == run.conversation_id)
                .first()
            )
            if not conv:
                logger.warning(f"Conversation {run.conversation_id} not found")
                return False

            try:
                graph_factory = resolve_graph(run.kind, run.graph_version)
            except UnsupportedGraphVersion:
                safe_message = (
                    "This run uses a graph version this runtime does not support."
                )
                finalized = finalize(
                    db=job,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": "failed",
                        "error_code": "unsupported_graph_version",
                        "output": safe_message,
                    },
                    commit=False,
                )
                if finalized:
                    append_event(
                        db=job,
                        conversation_id=conv.id,
                        run_id=run_id,
                        event_type="run.failed",
                        payload={
                            "error_code": "unsupported_graph_version",
                            "error": safe_message,
                            "model": None,
                        },
                        actor_id=None,
                    )
                    job.commit()
                return bool(finalized)

            if run.kind == "tool":
                return self._process_tool_job(
                    claim,
                    job,
                    supervision,
                    run,
                    conv,
                    graph_factory,
                )

            user_msg = (
                job.query(Message)
                .filter(
                    Message.run_id == run_id,
                    Message.role == "user",
                )
                .first()
            )
            if not user_msg:
                logger.warning(f"No user message for run {run_id}")
                return False

            # A run from before profiles records nothing; it gets one snapshot now,
            # under the fence this worker holds, marked as bound at execution.
            agent_execution = None
            try:
                snapshot = bind_run_model_config(job, run, fence)
                if run.kind == "chat" and run.graph_version in (2, 3, 4):
                    try:
                        agent_execution = decode_execution_config(
                            run.execution_configuration or ""
                        )
                    except Exception as invalid:
                        raise ProviderError(
                            "configuration_error",
                            safe_message="The pinned tool settings cannot be read by this runtime.",
                            cause=invalid,
                        ) from invalid
                    if (
                        agent_execution.graph_kind != "chat"
                        or agent_execution.graph_version != run.graph_version
                    ):
                        raise ProviderError("configuration_error")
            except ProviderError as refused:
                logger.warning(
                    f"[{self.worker_id}] Run {run_id} cannot be run: {refused.code}"
                )
                finalized = finalize(
                    db=job,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": _run_outcome(refused.code),
                        "error_code": refused.code,
                        "output": refused.safe_message,
                    },
                    commit=False,
                )
                if finalized:
                    # The run's status and its terminal event land in one command.
                    append_event(
                        db=job,
                        conversation_id=conv.id,
                        run_id=run_id,
                        event_type="run.failed",
                        payload={
                            "error_code": refused.code,
                            "error": refused.safe_message,
                            "model": _terminal_model(job, run),
                        },
                        actor_id=None,
                    )
                    job.commit()
                return bool(finalized)

            # Check if this is the first message (for auto-titling)
            is_first = self._is_first_message(job, conv, user_msg)

            # Load conversation history for context
            history_query = job.query(Message).filter(
                Message.conversation_id == conv.id,
                Message.id != user_msg.id,
            )
            admitted_context_ids = tuple(
                getattr(agent_execution, "admitted_context_message_ids", ())
            )
            if admitted_context_ids:
                if user_msg.id not in admitted_context_ids:
                    raise ProviderError("configuration_error")
                history_query = history_query.filter(
                    Message.id.in_(admitted_context_ids)
                )
            history = history_query.order_by(
                Message.created_at.asc(), Message.id.asc()
            ).all()

            # Build graph state with checkpoint namespace
            messages = []

            # Add system prompt with title generation instruction for first message
            system_content = _chat_system_prompt(
                is_first=is_first,
                preferred_search_kind=getattr(
                    agent_execution, "preferred_search_kind", None
                ),
            )
            messages.append({"role": "system", "content": system_content})

            for msg in history:
                messages.append({"role": msg.role, "content": msg.content})
            messages.append({"role": "user", "content": user_msg.content})

            state = {
                "messages": messages,
                "run_id": str(run_id),
                "conversation_id": str(conv.id),
                "model_configuration": snapshot.model_dump_json(),
            }
            thread_id = f"{run_id}-{claim.checkpoint_ns}"
            if agent_execution is not None:
                state["execution_configuration"] = agent_execution.model_dump_json()
                state["tool_policy_revision"] = run.tool_policy_revision

            # The control and recorder are injected per invocation: neither is
            # ever serialized into graph state or a checkpoint.
            if agent_execution is not None:
                window = begin_execution_window(
                    job,
                    claim,
                    deadline_seconds=agent_execution.loop_limits.deadline_seconds,
                    graph_thread_id=thread_id,
                )
                if window is None:
                    supervision.deny_lease()
                    return False
                _started_at, persisted_deadline = window
                remaining = max(
                    0.0,
                    (persisted_deadline - datetime.now(UTC)).total_seconds(),
                )
                deadline = time.monotonic() + remaining
                if remaining <= 0:
                    finalized = finalize(
                        db=job,
                        run_id=run.id,
                        fence=claim.fence,
                        result={
                            "status": "timed_out",
                            "error_code": "budget_exceeded",
                        },
                        commit=False,
                    )
                    if finalized:
                        append_event(
                            db=job,
                            conversation_id=conv.id,
                            run_id=run.id,
                            event_type="run.failed",
                            payload={
                                "error_code": "budget_exceeded",
                                "error": "The run exceeded its time limit.",
                                "model": _terminal_model(job, run),
                            },
                        )
                        job.commit()
                    return bool(finalized)
                readiness_failure = preflight_connector_references(
                    session_factory=self.Session,
                    project_id=run.project_id,
                    references=tuple(getattr(agent_execution, "connector_refs", ())),
                )
                if readiness_failure is not None:
                    parked = park_connector_wait(
                        job,
                        claim=claim,
                        run_id=run.id,
                        conversation_id=conv.id,
                        project_id=run.project_id,
                        execution=agent_execution,
                        failure=readiness_failure,
                    )
                    if not parked:
                        supervision.deny_lease()
                    return parked
            else:
                deadline = time.monotonic() + snapshot.limits.run_timeout_seconds

            # Attempt admission and its event commit in a short transaction
            # before each provider request. This keeps the conversation lock
            # out of the network turn and leaves an audit record after a crash.
            recorder = DatabaseAttemptRecorder(
                lambda: self.Session(), run_id, fence, commit_each=True
            )

            def invocation():
                return (
                    InvocationControl(
                        deadline=deadline,
                        clock=time.monotonic,
                        stop=supervision.stop,
                    ),
                    recorder,
                )

            if agent_execution is not None:
                from ..runs.budget import RunBudget

                tool_context = self._tool_context(claim, run, conv, agent_execution)

                def agent_invocation(_state):
                    return SimpleNamespace(
                        claim=claim,
                        context=tool_context,
                        control=InvocationControl(
                            deadline=deadline,
                            clock=time.monotonic,
                            stop=supervision.stop,
                        ),
                        recorder=recorder,
                        budget=RunBudget(
                            self.Session, claim, snapshot.limits, agent_execution
                        ),
                        session_factory=self.Session,
                        authorize_profile=authorize_current_profile,
                    )

                graph = graph_factory(
                    self.checkpointer,
                    gateway_factory=lambda: ToolGateway(self.Session),
                    invocation_factory=agent_invocation,
                )
            else:
                graph = graph_factory(self.checkpointer, invocation_factory=invocation)

            # Persist the graph thread on the Run record.
            run.graph_thread_id = thread_id
            job.flush()

            # Provider dispatch boundary: immediately before the generation request
            # this worker is about to make. A lease this no longer holds stops here.
            if not mark_provider_started(job, run_id, fence):
                logger.warning(
                    f"[{self.worker_id}] Lease already lost for run {run_id} before dispatch"
                )
                supervision.deny_lease()
                return False

            # Start the supervisor: it watches the run while its turn runs.
            supervisor_thread = threading.Thread(
                target=self._supervise_in_thread,
                args=(supervision, run_id, fence),
                daemon=True,
            )
            supervisor_thread.start()

            # Invoke the graph with checkpointing via thread_id
            print(f"[worker] About to invoke graph for run {run_id}")
            failure = None
            try:
                graph_state = graph.invoke(
                    state, config={"configurable": {"thread_id": thread_id}}
                )
                print("[worker] graph.invoke returned")
                result = ModelResult(
                    assistant_response=graph_state.get("assistant_response", ""),
                    usage=graph_state.get("usage"),
                    finish_reason=graph_state.get("finish_reason"),
                )
            except ProviderError as refused:
                print(f"[worker] provider turn failed: {refused.code}")
                failure = refused
                result = ModelResult(assistant_response="")

            # Stop the heartbeat before reading anything it may have changed.
            supervision.interrupt()
            if supervisor_thread.is_alive():
                supervisor_thread.join()

            # A lease this worker no longer holds publishes nothing: another
            # worker owns the run, and the reconciler will finish it.
            if supervision.lease_lost() or (
                failure is not None and failure.code == "lease_lost"
            ):
                logger.warning(
                    f"[{self.worker_id}] Lease lost for run {run_id}; publishing nothing"
                )
                return False

            # Auto-title the conversation if this is the first message
            new_title = None
            cleaned_response = result.assistant_response
            if is_first and failure is None:
                new_title = self._extract_title_from_response(result.assistant_response)
                # A blank title still needs to stay out of the visible answer;
                # when no standalone marker exists, this leaves the response intact.
                cleaned_response = self._remove_title_from_response(
                    result.assistant_response
                )
                if not new_title:
                    # Fallback: generate title from user message
                    new_title = self._generate_title(user_msg.content)

            if failure is not None:
                status = _run_outcome(failure.code)
                finalized = finalize(
                    db=job,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": status,
                        "error_code": failure.code,
                        "output": failure.safe_message,
                    },
                    commit=False,
                )
                if finalized:
                    # What the run asked first, then what it ended as: a reader
                    # follows the requests in the order they were made.
                    recorder.publish(job)
                    # The run's status, its event, and its outbox row are one command.
                    append_event(
                        db=job,
                        conversation_id=conv.id,
                        run_id=run_id,
                        event_type="run.failed",
                        payload={
                            "error_code": failure.code,
                            "error": failure.safe_message,
                            "model": _terminal_model(job, run),
                        },
                        actor_id=None,
                    )
                    job.commit()
                    logger.info(
                        f"[{self.worker_id}] Run {run_id} failed: {failure.safe_message}"
                    )
                else:
                    logger.warning(
                        f"[{self.worker_id}] Run {run_id} already finalized by another worker"
                    )
            else:
                # Try to finalize first to check ownership
                finalized = finalize(
                    db=job,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": "success",
                        "output": cleaned_response,
                        "usage": _usage_record(result.usage),
                    },
                    commit=False,
                )
                if finalized:
                    # Only save assistant message if we own the run
                    assistant_msg = Message(
                        conversation_id=conv.id,
                        run_id=run_id,
                        role="assistant",
                        content=cleaned_response,
                    )
                    job.add(assistant_msg)

                    # Update conversation title if this was the first message
                    if is_first and new_title:
                        conv.title = new_title

                    # The requests this turn made, stated in the order they
                    # were made, before the answer that one of them produced.
                    recorder.publish(job)

                    # Emit run.succeeded event
                    append_event(
                        db=job,
                        conversation_id=conv.id,
                        run_id=run_id,
                        event_type="run.succeeded",
                        payload={
                            "message_id": str(assistant_msg.id),
                            "usage": result.usage,
                            "model": _terminal_model(job, run),
                        },
                        actor_id=None,
                    )

                    # Emit title_updated event if title changed
                    if is_first and new_title:
                        append_event(
                            db=job,
                            conversation_id=conv.id,
                            run_id=run_id,
                            event_type="title_updated",
                            payload={
                                "conversation_id": str(conv.id),
                                "title": new_title,
                            },
                            actor_id=None,
                        )

                    # A completed status, its message, and its event land together.
                    job.commit()
                    logger.info(f"[{self.worker_id}] Run {run_id} completed")
                    if is_first and new_title:
                        logger.info(
                            f"[{self.worker_id}] Auto-titled conversation: {new_title}"
                        )
                else:
                    logger.warning(
                        f"[{self.worker_id}] Run {run_id} already finalized by another worker"
                    )

            return True

        except Exception as e:
            logger.exception(f"[{self.worker_id}] Error processing run {run_id}")
            # A failed publication may have staged a success status, answer,
            # attempts, or event in this session. Discard all of it before a
            # fresh fenced transaction publishes the failure atomically.
            try:
                if job is not None:
                    job.rollback()
                failure_db = self.Session()
                finalized = finalize(
                    db=failure_db,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": "failed",
                        "error_code": "internal_error",
                        "output": str(e),
                    },
                    commit=False,
                )
                failed_run = failure_db.get(Run, run_id)
                if finalized and failed_run is not None:
                    from ..events.service import append_event

                    conversation = (
                        failure_db.query(Conversation)
                        .filter_by(id=failed_run.conversation_id)
                        .first()
                    )
                    if conversation is not None:
                        append_event(
                            db=failure_db,
                            conversation_id=conversation.id,
                            run_id=run_id,
                            event_type="run.failed",
                            payload={
                                "error_code": "internal_error",
                                "error": "The run could not be completed.",
                                "model": _terminal_model(failure_db, failed_run),
                            },
                            actor_id=None,
                        )
                        failure_db.commit()
                failure_db.close()
            except Exception:
                try:
                    failure_db.rollback()
                    failure_db.close()
                except Exception:
                    pass
            return False
        finally:
            supervision.interrupt()
            if supervisor_thread and supervisor_thread.is_alive():
                supervisor_thread.join()
            if job is not None:
                job.close()

    def run(self):
        """Main worker loop with automatic reconciliation and resource cleanup."""
        try:
            self._run_loop()
        finally:
            self.close()
        logger.info(f"[{self.worker_id}] Shutdown complete")

    def _run_loop(self):
        logger.info(f"[{self.worker_id}] Starting worker loop")
        last_reconcile = time.time()
        reconcile_interval = settings.reconciler_interval
        last_availability = 0.0

        while self.running:
            # Periodically reconcile expired leases
            now = time.time()
            if now - last_availability >= 10:
                availability_db = self.Session()
                try:
                    self._refresh_tool_availability(availability_db)
                except Exception:
                    availability_db.rollback()
                    logger.warning("Runtime tool availability refresh failed")
                finally:
                    availability_db.close()
                last_availability = now

            if now - last_reconcile >= reconcile_interval:
                db = self.Session()
                try:
                    from ..connectors.blockers import expire_connector_blockers

                    expired_blockers = expire_connector_blockers(db)
                    if expired_blockers > 0:
                        logger.info(
                            f"[{self.worker_id}] Expired {expired_blockers} Connector waits"
                        )
                    reclaimed = reconcile_expired(db)
                    if reclaimed > 0:
                        logger.info(
                            f"[{self.worker_id}] Reclaimed {reclaimed} expired leases"
                        )
                except Exception as e:
                    logger.warning(
                        f"[{self.worker_id}] Error during reconciliation: {e}"
                    )
                finally:
                    db.close()
                last_reconcile = now

            db = self.Session()
            try:
                # Claim a job
                claim = claim_next(
                    db=db,
                    worker_id=self.worker_id,
                    lease_seconds=settings.worker_lease_seconds,
                )

                if claim is None:
                    # No jobs available, sleep and retry
                    db.close()
                    time.sleep(settings.worker_poll_interval)
                    continue

                # Process the job (process_job will close db)
                self.process_job(claim)
                db = None  # process_job closed it

            except KeyboardInterrupt:
                self.running = False
                logger.info(f"[{self.worker_id}] Interrupted, shutting down")
            except Exception:
                logger.exception(f"[{self.worker_id}] Error in worker loop")
                time.sleep(1)
            finally:
                if db is not None:
                    db.close()


def main():
    parser = argparse.ArgumentParser(description="OpenShip chat turn worker")
    parser.add_argument("--worker-id", type=str, default=None, help="Unique worker ID")
    parser.add_argument("--log-level", type=str, default="INFO", help="Log level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    worker = Worker(worker_id=args.worker_id)

    def shutdown(signum, frame):
        logger.info("Received shutdown signal")
        worker.running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        worker.run()
    finally:
        worker.close()


if __name__ == "__main__":
    main()
