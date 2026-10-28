"""Production JetStream run-worker process entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Sequence

from .connection import connect_nats
from .consumer import RunConsumer

logger = logging.getLogger("openship.messaging.worker")


async def run_worker(
    stop: asyncio.Event,
    *,
    worker_id: str | None = None,
    client_factory=connect_nats,
    consumer_factory=RunConsumer,
) -> None:
    """Run the JetStream consumer and close its process-owned connection."""
    client = await client_factory(name="openship-run-worker")
    try:
        consumer = consumer_factory(client, worker_id=worker_id)
        await consumer.run(stop)
    finally:
        try:
            await client.close()
        except Exception as error:  # noqa: BLE001 - preserve worker failure context.
            logger.warning(
                "run_worker_connection_close_failed type=%s", type(error).__name__
            )


async def _run(worker_id: str | None) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await run_worker(stop, worker_id=worker_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenShip JetStream run worker")
    parser.add_argument("--worker-id", type=str, default=None, help="Worker identity")
    parser.add_argument("--log-level", type=str, default="INFO", help="Log level")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    asyncio.run(_run(args.worker_id))
