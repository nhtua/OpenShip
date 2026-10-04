"""Worker entry point for processing queued chat turns.

Claims jobs with lease-based ownership, executes the LangGraph chat turn,
and finalizes the run with fence-based optimistic concurrency control.
"""

import argparse
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta

import psycopg2
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ..config import settings
from ..database.session import get_db
from .checkpoints import get_checkpointer
from .graph import build_chat_graph, execute_model_turn
from .queue import claim_next, renew, finalize, mark_provider_started
from .service import submit_turn

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
            from ..runs.checkpoints import get_checkpointer
            with get_checkpointer(settings.database_url) as saver:
                self.checkpointer = saver
                logger.info("Checkpointer initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize checkpointer: {e}")
            self.checkpointer = None

        logger.info(f"Worker {self.worker_id} initialized")

    def process_job(self, claim):
        """Process a single claimed job."""
        run_id = claim.run_id
        fence = claim.fence
        logger.info(f"[{self.worker_id}] Processing run {run_id} (fence={fence})")

        db = self.Session()
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

            # Build graph state
            thread_id = f"{run_id}-{claim.checkpoint_ns}"
            state = {
                "messages": [
                    {"role": "user", "content": user_msg.content},
                ],
                "run_id": str(run_id),
                "conversation_id": str(conv.id),
            }

            # Mark provider call as started
            mark_provider_started(db, run_id, fence)
            db.commit()

            # Execute the model turn
            result = execute_model_turn(
                run_id=str(run_id),
                thread_id=thread_id,
                fence=fence,
                state=state,
            )

            # Finalize the run
            if result.error:
                finalize(
                    db=db,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": "failed",
                        "error_code": "provider_error",
                        "output": result.error,
                    },
                )
                logger.info(f"[{self.worker_id}] Run {run_id} failed: {result.error}")
            else:
                # Save assistant message
                assistant_msg = Message(
                    conversation_id=conv.id,
                    run_id=run_id,
                    role="assistant",
                    content=result.assistant_response,
                )
                db.add(assistant_msg)
                db.commit()

                finalize(
                    db=db,
                    run_id=run_id,
                    fence=fence,
                    result={
                        "status": "success",
                        "output": result.assistant_response,
                        "usage": result.usage,
                    },
                )
                logger.info(f"[{self.worker_id}] Run {run_id} completed")

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
            db.close()

    def run(self):
        """Main worker loop."""
        logger.info(f"[{self.worker_id}] Starting worker loop")

        while self.running:
            try:
                # Claim a job
                claim = claim_next(
                    db=self.Session(),
                    worker_id=self.worker_id,
                    lease_seconds=settings.worker_lease_seconds,
                )

                if claim is None:
                    # No jobs available, sleep and retry
                    time.sleep(settings.worker_poll_interval)
                    continue

                # Process the job
                self.process_job(claim)

            except KeyboardInterrupt:
                self.running = False
                logger.info(f"[{self.worker_id}] Interrupted, shutting down")
            except Exception as e:
                logger.exception(f"[{self.worker_id}] Error in worker loop")
                time.sleep(1)

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
