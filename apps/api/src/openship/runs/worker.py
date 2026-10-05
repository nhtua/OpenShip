"""Worker entry point for processing queued chat turns.

Claims jobs with lease-based ownership, executes the LangGraph chat turn,
and finalizes the run with fence-based optimistic concurrency control.
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
import uuid

import psycopg2
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..database.session import get_db
from .checkpoints import get_checkpointer
from .graph import build_chat_graph, ModelResult
from .queue import claim_next, renew, finalize, mark_provider_started, reconcile_expired

logger = logging.getLogger("worker")


class Worker:
    """A chat turn worker that claims and executes queued jobs."""

    def __init__(self, worker_id: str = None):
        if worker_id is None:
            worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        self.worker_id = worker_id
        self.running = True

        # Set up database connection
        self.engine = create_engine(settings.database_url)
        self.Session = sessionmaker(bind=self.engine)

        # Set up checkpointer
        self.checkpointer = None
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            # PostgresSaver.from_conn_string returns a context manager
            saver_ctx = PostgresSaver.from_conn_string(settings.database_url)
            self.checkpointer = saver_ctx.__enter__()
            logger.info("Checkpointer initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize checkpointer: {e}")
            self.checkpointer = None

        logger.info(f"Worker {self.worker_id} initialized")

    def _heartbeat(self, db, run_id, fence, stop_event, interval=None):
        """Background heartbeat thread that renews the lease periodically."""
        if interval is None:
            interval = getattr(settings, "worker_heartbeat_interval", 10)

        while not stop_event.wait(timeout=interval):
            try:
                ok = renew(db, run_id, fence)
                if not ok:
                    logger.warning(f"[{self.worker_id}] Heartbeat failed for run {run_id} (fence={fence})")
                    break
            except Exception as e:
                logger.warning(f"[{self.worker_id}] Heartbeat error for run {run_id}: {e}")
                break

    def process_job(self, claim):
        """Process a single claimed job with lease heartbeat."""
        run_id = claim.run_id
        fence = claim.fence
        logger.info(f"[{self.worker_id}] Processing run {run_id} (fence={fence})")

        db = self.Session()
        heartbeat_stop = threading.Event()
        heartbeat_thread = None

        try:
            # Get run and conversation info
            from ..runs.models import Run
            from ..chat.models import Conversation, Message

            run = db.query(Run).filter(Run.id == run_id).first()
            if not run:
                logger.warning(f"Run {run_id} not found")
                return False

            conv = db.query(Conversation).filter(Conversation.id == run.conversation_id).first()
            if not conv:
                logger.warning(f"Conversation {run.conversation_id} not found")
                return False

            # Get user message
            user_msg = db.query(Message).filter(
                Message.run_id == run_id,
                Message.role == "user",
            ).first()
            if not user_msg:
                logger.warning(f"No user message for run {run_id}")
                return False

            # Load conversation history for context
            history = db.query(Message).filter(
                Message.conversation_id == conv.id,
                Message.id != user_msg.id,
            ).order_by(Message.created_at.asc()).all()

            # Build graph state with checkpoint namespace
            messages = []
            for msg in history:
                messages.append({"role": msg.role, "content": msg.content})
            messages.append({"role": "user", "content": user_msg.content})

            state = {
                "messages": messages,
                "run_id": str(run_id),
                "conversation_id": str(conv.id),
            }

            # Build the graph with the checkpointer
            graph = build_chat_graph(self.checkpointer)

            # Construct the thread ID and persist it on the Run record
            thread_id = f"{run_id}-{claim.checkpoint_ns}"
            run.graph_thread_id = thread_id
            db.flush()

            # Mark provider call as started
            mark_provider_started(db, run_id, fence)

            # Start heartbeat thread to renew lease during processing
            heartbeat_thread = threading.Thread(
                target=self._heartbeat,
                args=(db, run_id, fence, heartbeat_stop),
                daemon=True,
            )
            heartbeat_thread.start()

            # Invoke the graph with checkpointing via thread_id
            try:
                config = {"configurable": {"thread_id": thread_id}}
                graph_state = graph.invoke(state, config=config)
                result = ModelResult(
                    assistant_response=graph_state.get("assistant_response", ""),
                    usage=graph_state.get("usage"),
                )
            except Exception as e:
                result = ModelResult(assistant_response="", error=str(e))

            # Stop heartbeat
            heartbeat_stop.set()
            if heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=5)

            # Finalize the run first to check ownership
            from ..events.service import append_event

            if result.error:
                finalized = finalize(
                    db=db,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": "failed",
                        "error_code": "provider_error",
                        "output": result.error,
                    },
                )
                if finalized:
                    # Emit run.failed event
                    append_event(
                        db=db,
                        conversation_id=conv.id,
                        run_id=run_id,
                        event_type="run.failed",
                        payload={"error_code": "provider_error", "error": result.error},
                        actor_id=None,
                    )
                    logger.info(f"[{self.worker_id}] Run {run_id} failed: {result.error}")
                else:
                    logger.warning(f"[{self.worker_id}] Run {run_id} already finalized by another worker")
            else:
                # Try to finalize first to check ownership
                finalized = finalize(
                    db=db,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": "success",
                        "output": result.assistant_response,
                        "usage": result.usage,
                    },
                )
                if finalized:
                    # Only save assistant message if we own the run
                    assistant_msg = Message(
                        conversation_id=conv.id,
                        run_id=run_id,
                        role="assistant",
                        content=result.assistant_response,
                    )
                    db.add(assistant_msg)

                    # Emit run.succeeded event
                    append_event(
                        db=db,
                        conversation_id=conv.id,
                        run_id=run_id,
                        event_type="run.succeeded",
                        payload={"message_id": str(assistant_msg.id), "usage": result.usage},
                        actor_id=None,
                    )

                    db.commit()
                    logger.info(f"[{self.worker_id}] Run {run_id} completed")
                else:
                    logger.warning(f"[{self.worker_id}] Run {run_id} already finalized by another worker")

            return True

        except Exception as e:
            logger.exception(f"[{self.worker_id}] Error processing run {run_id}")
            try:
                finalize(
                    db=db,
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
            heartbeat_stop.set()
            if heartbeat_thread and heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=5)
            db.close()

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
            except Exception as e:
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
