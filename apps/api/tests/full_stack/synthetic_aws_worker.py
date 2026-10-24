"""One-shot worker process for AWS crash-boundary qualification tests."""

from __future__ import annotations

import json
import os
import uuid
from urllib.parse import parse_qs

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.openship.config import settings
from src.openship.connectors.aws.broker import AwsRoleBroker
from src.openship.execution.gateway import ToolGateway
from src.openship.runs.queue import claim_next
from src.openship.runs.worker import Worker
from tests.integration.test_cloud_observation_publication_postgres import (
    _synthetic_transport,
)


def _record_request(request) -> None:
    if request.url.host.startswith("sts."):
        operation = parse_qs(request.content.decode("ascii"))["Action"][0]
        service = "sts"
    elif request.url.host.startswith("s3."):
        operation = "GetBucketLocation"
        service = "s3"
    else:
        service = "cloudwatch"
        operation = request.headers["x-amz-target"].rsplit(".", 1)[-1]
    safe_record = json.dumps(
        {"service": service, "operation": operation},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    descriptor = os.open(
        os.environ["AWS_SYNTHETIC_REQUEST_LOG"],
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        0o600,
    )
    try:
        os.write(descriptor, safe_record)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _install_crash_point() -> None:
    crash_at = os.environ.get("AWS_SYNTHETIC_CRASH_AT")
    if crash_at == "before_intent":

        async def stop_before_intent(*_args, **_kwargs):
            os._exit(71)

        ToolGateway._acquire_and_mark = stop_before_intent
    elif crash_at == "after_read":
        from src.openship.artifacts.filesystem import LocalArtifactStore

        def stop_after_read(self, *_args, **_kwargs):
            os._exit(72)

        LocalArtifactStore.stage = stop_after_read
    elif crash_at == "before_publication":
        from src.openship.artifacts.filesystem import LocalArtifactStore

        def stop_before_publication(self, *_args, **_kwargs):
            os._exit(73)

        LocalArtifactStore.publish_file = stop_before_publication


def main() -> int:
    engine = create_engine(settings.database_url)
    if engine.dialect.name == "sqlite":
        event.listen(
            engine,
            "connect",
            lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"),
        )
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    requests = []
    transport_factory = _synthetic_transport(requests, on_request=_record_request)
    import src.openship.execution.gateway as gateway_module

    gateway_module.AwsRoleBroker = lambda session_factory: AwsRoleBroker(
        session_factory,
        transport_factory=transport_factory,
    )
    _install_crash_point()

    with sessions() as db:
        claim = claim_next(db, worker_id=f"aws-crash-{uuid.uuid4().hex[:8]}")
    if claim is None:
        engine.dispose()
        return 0

    worker = Worker(
        worker_id=claim.worker_id,
        session_factory=sessions,
        checkpointer=None,
    )
    try:
        worker.process_job(claim)
    finally:
        worker.engine.dispose()
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
