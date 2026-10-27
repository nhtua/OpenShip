"""Explicit PostgreSQL polling harness for pre-JetStream full-stack fixtures.

Task 7 replaces these fixture launches with the real JetStream worker. This
helper keeps the existing synthetic provider tests independent of a broker in
the meantime; production entry points never call it.
"""

import argparse
import logging
import signal
from collections.abc import Sequence

from src.openship.runs.worker import Worker


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Synthetic legacy worker harness")
    parser.add_argument("--worker-id", type=str, default=None, help="Worker identity")
    parser.add_argument("--log-level", type=str, default="INFO", help="Log level")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    worker = Worker(worker_id=args.worker_id)

    def shutdown(_signum, _frame):
        worker.running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        worker.run()
    finally:
        worker.close()
