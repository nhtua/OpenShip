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
import logging
import re
import signal
import threading
import time
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..chat.models import Message
from ..config import settings
from ..providers.attempts import DatabaseAttemptRecorder
from ..providers.control import InvocationControl
from ..providers.errors import ProviderError
from ..runs.service import bind_run_model_config
from .graph import ModelResult, build_chat_graph
from .models import Run, RunJob
from .queue import claim_next, finalize, mark_provider_started, reconcile_expired, renew

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


def _usage_record(usage) -> dict | None:
    """A run's own usage as JSON text, schema-versioned, never an invention.

    ``estimated`` is kept for old consumers and means the provider's totals
    are incomplete, not that any value was estimated here.
    """
    if usage is None:
        return None
    record = {name: usage.get(name) for name in USAGE_FIELDS}
    record["schema_version"] = USAGE_SCHEMA_VERSION
    reported = [name for name in ("input_tokens", "output_tokens", "total_tokens") if record[name] is not None]
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

    def __init__(self, worker_id: str | None = None, session_factory=None, checkpointer=_INSTALLATION):
        if worker_id is None:
            worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self.worker_id = worker_id
        self.running = True

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

                # from_conn_string takes only the connection string: the pool
                # keyword arguments this installation once passed are not part
                # of the saver's API, and passing them left every worker
                # without a checkpointer at all.
                self._checkpointer_ctx = PostgresSaver.from_conn_string(
                    settings.database_url
                )
                self.checkpointer = self._checkpointer_ctx.__enter__()
                # The saver's tables are what make a thread resumable; without
                # this the graph silently has nowhere to write a checkpoint.
                self.checkpointer.setup()
                logger.info("Checkpointer initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize checkpointer: {e}")
                self.checkpointer = None

        logger.info(f"Worker {self.worker_id} initialized")

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
        while not supervision.stop():
            # A poll on an interval: the first check happens while the turn is
            # still running, so a lease that moved interrupts a blocked read
            # rather than deciding the turn before it began.
            time.sleep(poll)
            waited += poll
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
                logger.warning(f"[{self.worker_id}] Supervisor error for run {run_id}: {e}")
                supervision.deny_lease()
                return

            if waited < renew_every:
                continue
            waited = 0.0
            try:
                if not renew(db, run_id, fence):
                    logger.warning(
                        f"[{self.worker_id}] Heartbeat failed for run {run_id} (fence={fence})"
                    )
                    supervision.deny_lease()
                    return
            except Exception as e:
                logger.warning(f"[{self.worker_id}] Heartbeat error for run {run_id}: {e}")
                supervision.deny_lease()
                return

    def _ownership_lost(self, db, run_id, fence) -> bool:
        """Whether this worker is still the owner of the run it is working on.

        A read of both halves of the invariant the queue keeps: a claim moves
        the run's fence and its job's together, and so does a cancellation the
        owner asked. Either half having moved means this worker no longer holds
        the turn, so its read must stop.
        """
        run = db.query(Run).filter(Run.id == run_id).first()
        if run is None or run.fence != fence:
            return True
        if run.status not in ("queued", "running", "paused"):
            return True
        job = db.query(RunJob).filter(RunJob.run_id == run_id).first()
        return job is None or job.fence != fence

    def _is_first_message(self, db, conversation, user_msg):
        """Check if this is the first message in the conversation."""
        existing = db.query(Message).filter(
            Message.conversation_id == conversation.id,
            Message.id != user_msg.id,
        ).count()
        return existing == 0

    def _generate_title(self, user_content):
        """Generate a conversation title from the first user message (fallback)."""
        title = user_content.strip()
        if len(title) > 50:
            title = title[:50] + "..."
        return title

    def _extract_title_from_response(self, response):
        """Extract a conversation title from the LLM response."""
        match = re.search(r'## Title:\s*(.{1,50})', response)
        if match:
            return match.group(1).strip()
        return None

    def process_job(self, claim):
        """Process a single claimed job: supervise one model turn, then publish it."""
        run_id = claim.run_id
        fence = claim.fence
        logger.info(f"[{self.worker_id}] Processing run {run_id} (fence={fence})")

        # One session per thread: the worker's own work stays in one transaction,
        # and the supervisor thread gets a session of its own.
        job = self.Session()
        supervisor_db = self.Session()
        supervision = Supervision()
        supervisor_thread = None

        try:
            from ..chat.models import Conversation
            from ..events.service import append_event
            from ..runs.models import Run

            run = job.query(Run).filter(Run.id == run_id).first()
            if not run:
                logger.warning(f"Run {run_id} not found")
                return False

            conv = job.query(Conversation).filter(Conversation.id == run.conversation_id).first()
            if not conv:
                logger.warning(f"Conversation {run.conversation_id} not found")
                return False

            user_msg = job.query(Message).filter(
                Message.run_id == run_id,
                Message.role == "user",
            ).first()
            if not user_msg:
                logger.warning(f"No user message for run {run_id}")
                return False

            # A run from before profiles records nothing; it gets one snapshot now,
            # under the fence this worker holds, marked as bound at execution.
            try:
                snapshot = bind_run_model_config(job, run, fence)
            except ProviderError as refused:
                logger.warning(f"[{self.worker_id}] Run {run_id} cannot be run: {refused.code}")
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
                        payload={"error_code": refused.code, "error": refused.safe_message},
                        actor_id=None,
                    )
                    job.commit()
                return bool(finalized)

            # Check if this is the first message (for auto-titling)
            is_first = self._is_first_message(job, conv, user_msg)

            # Load conversation history for context
            history = job.query(Message).filter(
                Message.conversation_id == conv.id,
                Message.id != user_msg.id,
            ).order_by(Message.created_at.asc()).all()

            # Build graph state with checkpoint namespace
            messages = []

            # Add system prompt with title generation instruction for first message
            system_content = "You are OpenShip, an AI DevOps co-pilot. Help users with infrastructure, deployment, and operations tasks. Be concise and practical."
            if is_first:
                system_content += "\n\nAfter your response, include a short 50-character summary on a new line prefixed with '## Title: '. This will be used as the conversation title. Do not include any other content after the title."
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

            # The control and recorder are injected per invocation: neither is
            # ever serialized into graph state or a checkpoint.
            deadline = time.monotonic() + snapshot.limits.run_timeout_seconds

            def invocation():
                return (
                    InvocationControl(
                        deadline=deadline,
                        clock=time.monotonic,
                        stop=supervision.stop,
                    ),
                    DatabaseAttemptRecorder(lambda: job, run_id, fence),
                )

            graph = build_chat_graph(self.checkpointer, invocation_factory=invocation)

            # Construct the thread ID and persist it on the Run record
            thread_id = f"{run_id}-{claim.checkpoint_ns}"
            run.graph_thread_id = thread_id
            job.flush()

            # Provider dispatch boundary: immediately before the generation request
            # this worker is about to make. A lease this no longer holds stops here.
            if not mark_provider_started(job, run_id, fence):
                logger.warning(f"[{self.worker_id}] Lease already lost for run {run_id} before dispatch")
                supervision.deny_lease()
                return False

            # Start the supervisor: it watches the run while its turn runs.
            supervisor_thread = threading.Thread(
                target=self._supervise,
                args=(supervision, supervisor_db, run_id, fence),
                daemon=True,
            )
            supervisor_thread.start()

            # Invoke the graph with checkpointing via thread_id
            print(f"[worker] About to invoke graph for run {run_id}")
            failure = None
            try:
                graph_state = graph.invoke(state, config={"configurable": {"thread_id": thread_id}})
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
                supervisor_thread.join(timeout=5)

            # A lease this worker no longer holds publishes nothing: another
            # worker owns the run, and the reconciler will finish it.
            if supervision.lease_lost() or (failure is not None and failure.code == "lease_lost"):
                logger.warning(f"[{self.worker_id}] Lease lost for run {run_id}; publishing nothing")
                return False

            # Auto-title the conversation if this is the first message
            new_title = None
            cleaned_response = result.assistant_response
            if is_first and failure is None:
                new_title = self._extract_title_from_response(result.assistant_response)
                if new_title:
                    # Remove the title line from the response before storing
                    cleaned_response = re.sub(r'## Title:\s*.{1,50}', '', result.assistant_response).strip()
                else:
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
                    # The run's status, its event, and its outbox row are one command.
                    append_event(
                        db=job,
                        conversation_id=conv.id,
                        run_id=run_id,
                        event_type="run.failed",
                        payload={"error_code": failure.code, "error": failure.safe_message},
                        actor_id=None,
                    )
                    job.commit()
                    logger.info(f"[{self.worker_id}] Run {run_id} failed: {failure.safe_message}")
                else:
                    logger.warning(f"[{self.worker_id}] Run {run_id} already finalized by another worker")
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

                    # Emit run.succeeded event
                    append_event(
                        db=job,
                        conversation_id=conv.id,
                        run_id=run_id,
                        event_type="run.succeeded",
                        payload={"message_id": str(assistant_msg.id), "usage": result.usage},
                        actor_id=None,
                    )

                    # Emit title_updated event if title changed
                    if is_first and new_title:
                        append_event(
                            db=job,
                            conversation_id=conv.id,
                            run_id=run_id,
                            event_type="title_updated",
                            payload={"conversation_id": str(conv.id), "title": new_title},
                            actor_id=None,
                        )

                    # A completed status, its message, and its event land together.
                    job.commit()
                    logger.info(f"[{self.worker_id}] Run {run_id} completed")
                    if is_first and new_title:
                        logger.info(f"[{self.worker_id}] Auto-titled conversation: {new_title}")
                else:
                    logger.warning(f"[{self.worker_id}] Run {run_id} already finalized by another worker")

            return True

        except Exception as e:
            logger.exception(f"[{self.worker_id}] Error processing run {run_id}")
            try:
                finalize(
                    db=job,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": "failed",
                        "error_code": "internal_error",
                        "output": str(e),
                    },
                )
            except Exception:
                pass
            return False
        finally:
            if supervisor_thread and supervisor_thread.is_alive():
                supervisor_thread.join(timeout=5)
            job.close()
            supervisor_db.close()

    def run(self):
        """Main worker loop with automatic reconciliation."""
        logger.info(f"[{self.worker_id}] Starting worker loop")
        last_reconcile = time.time()
        reconcile_interval = getattr(settings, "worker_reconcile_interval", 60)  # 60 seconds

        while self.running:
            # Periodically reconcile expired leases
            now = time.time()
            if now - last_reconcile >= reconcile_interval:
                db = self.Session()
                try:
                    reclaimed = reconcile_expired(db)
                    if reclaimed > 0:
                        logger.info(f"[{self.worker_id}] Reclaimed {reclaimed} expired leases")
                except Exception as e:
                    logger.warning(f"[{self.worker_id}] Error during reconciliation: {e}")
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

        # Cleanup
        self.engine.dispose()
        logger.info(f"[{self.worker_id}] Shutdown complete")


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

    worker.run()


if __name__ == "__main__":
    main()
