"""NATS supervisor for durable MaxDeliver exhaustion recovery."""

# Broker and database errors are deliberately converted to safe ACK/NAK outcomes.
# ruff: noqa: BLE001

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
import uuid
from datetime import UTC, datetime

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.errors import NotFoundError
from sqlalchemy.orm import Session

from .command import InvalidCommandEnvelope
from .delivery import DeliveryDisposition, PreparedDelivery
from .exhaustion import (
    MAX_ADVISORY_BYTES,
    ExhaustionObservationSnapshot,
    ExhaustionSettlementClaim,
    InvalidMaxDeliverAdvisory,
    UnboundExhaustionSource,
    _block_observation,
    _call_with_resource,
    _call_without_resource,
    _utc,
    claim_exhaustion_settlement,
    due_exhaustion_observation_ids,
    exhaustion_observation_snapshot,
    mark_exhaustion_resolved,
    parse_max_deliver_advisory,
    record_exhaustion_observation,
    record_missing_source_observation,
    refresh_live_owner_observation,
)
from .failure import (
    FailureHandoff,
    JetStreamSourceIdentity,
    failure_handoff_for_source,
    failure_record_exists,
    handoff_matches_delivery,
    persist_failure_handoff,
    record_failure_source_conflict,
)

logger = logging.getLogger(__name__)
_ADVISORY_RETRY_DELAYS = (1.0, 5.0, 15.0, 30.0, 60.0)


class ExhaustionSupervisor:
    """Durably consume MaxDeliver advisories and resume live-owner checks."""

    def __init__(
        self,
        client,
        *,
        installation_id: uuid.UUID,
        session_factory,
        failure_publisher=None,
        ack_timeout: float = 2.0,
        poll_interval: float = 10.0,
    ):
        if not math.isfinite(ack_timeout) or ack_timeout <= 0:
            raise ValueError("advisory ACK timeout must be positive and finite")
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("exhaustion recovery interval must be positive and finite")
        self.client = client
        self.js = client.jetstream(timeout=ack_timeout)
        self.installation_id = installation_id
        self.session_factory = session_factory
        self.failure_publisher = failure_publisher
        self.ack_timeout = ack_timeout
        self.poll_interval = poll_interval
        self._pool = None

    async def _run_sync(self, function, *args, **kwargs):
        if self._pool is None:
            raise RuntimeError("exhaustion supervisor is not running")
        async with self._pool.reserve() as slot:
            return await slot.run(_call_with_resource, function, args, kwargs)

    async def _run_relay_sync(self, function, *args, **kwargs):
        """Run relay session-factory helpers on the bounded maintenance thread."""
        if self._pool is None:
            raise RuntimeError("exhaustion supervisor is not running")
        async with self._pool.reserve() as slot:
            return await slot.run(_call_without_resource, function, args, kwargs)

    async def _pause(self, stop: asyncio.Event, delay: float = 0.25) -> None:
        try:
            await asyncio.wait_for(stop.wait(), delay)
        except TimeoutError:
            pass

    async def run(
        self,
        stop: asyncio.Event,
        *,
        started: asyncio.Event | None = None,
    ) -> None:
        """Bind the required durable before run intake and recover by DB state."""
        from .executor import ExecutionPool
        from .topology import TopologyCompatibilityError, ensure_topology

        self._pool = ExecutionPool(1, self.session_factory)
        subscription = None
        reconnect = None
        try:
            while not stop.is_set():
                try:
                    if not self.client.is_connected:
                        await self._pause(stop)
                        continue
                    current_reconnect = self.client.stats["reconnects"]
                    if reconnect != current_reconnect:
                        report = await ensure_topology(
                            apply=False,
                            js=self.js,
                            installation_id=self.installation_id,
                        )
                        if not report.ready:
                            raise TopologyCompatibilityError(
                                "exhaustion advisory topology is not ready"
                            )
                        reconnect = current_reconnect
                    subscription = await self.js.pull_subscribe_bind(
                        stream="OS_ADVISORIES",
                        durable="exhaustion-v1",
                        pending_msgs_limit=1,
                        pending_bytes_limit=MAX_ADVISORY_BYTES,
                    )
                    await self.client.flush(timeout=self.ack_timeout)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning(
                        "exhaustion_observer_start_unavailable type=%s",
                        type(error).__name__,
                    )
                    await self._pause(stop)
            if started is not None:
                started.set()
            if stop.is_set() or subscription is None:
                return

            next_recovery = time.monotonic()
            while not stop.is_set():
                if not self.client.is_connected:
                    await self._pause(stop)
                    continue
                current_reconnect = self.client.stats["reconnects"]
                if reconnect != current_reconnect:
                    try:
                        report = await ensure_topology(
                            apply=False,
                            js=self.js,
                            installation_id=self.installation_id,
                        )
                        if not report.ready:
                            raise TopologyCompatibilityError(
                                "exhaustion advisory topology is not ready after reconnect"
                            )
                        reconnect = current_reconnect
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        logger.warning(
                            "exhaustion_topology_recheck_failed type=%s",
                            type(error).__name__,
                        )
                        await self._pause(stop)
                        continue

                if time.monotonic() >= next_recovery:
                    await self._recover_observations()
                    next_recovery = time.monotonic() + self.poll_interval
                try:
                    messages = await subscription.fetch(batch=1, timeout=1.0)
                except NatsTimeoutError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning(
                        "exhaustion_advisory_fetch_unavailable type=%s",
                        type(error).__name__,
                    )
                    reconnect = None
                    await self._pause(stop)
                    continue
                if messages:
                    try:
                        await self._handle_advisory(messages[0])
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        logger.warning(
                            "exhaustion_advisory_processing_failed type=%s",
                            type(error).__name__,
                        )
                        await self._nak_advisory(
                            messages[0], "advisory_processing_failed"
                        )
        finally:
            if started is not None and not started.is_set():
                started.set()
            if subscription is not None:
                try:
                    await subscription.unsubscribe()
                except Exception as error:
                    logger.warning(
                        "exhaustion_advisory_unsubscribe_failed type=%s",
                        type(error).__name__,
                    )
            if self._pool is not None:
                await self._pool.close()
                self._pool = None

    async def _ack_advisory(self, message) -> bool:
        try:
            await message.ack_sync(timeout=self.ack_timeout)
            return True
        except Exception as error:
            logger.warning(
                "exhaustion_advisory_ack_unconfirmed type=%s", type(error).__name__
            )
            return False

    async def _nak_advisory(self, message, code: str) -> None:
        try:
            attempt = message.metadata.num_delivered
        except Exception:
            attempt = 2
        if type(attempt) is not int or attempt < 1:
            attempt = 2
        delay = _ADVISORY_RETRY_DELAYS[
            min(attempt - 1, len(_ADVISORY_RETRY_DELAYS) - 1)
        ]
        try:
            await message.nak(delay=delay)
        except Exception as error:
            logger.warning(
                "exhaustion_advisory_retry_unconfirmed code=%s type=%s",
                code,
                type(error).__name__,
            )

    def _source_identity(self, raw_message) -> JetStreamSourceIdentity | None:
        try:
            return JetStreamSourceIdentity(
                stream="OS_RUNS",
                sequence=raw_message.seq,
                timestamp=raw_message.time,
            ).validated()
        except Exception:
            return None

    def _source_matches_snapshot(
        self,
        raw_message,
        snapshot: ExhaustionObservationSnapshot | ExhaustionSettlementClaim,
    ) -> bool:
        source = self._source_identity(raw_message)
        expected_subject = f"openship.{self.installation_id.hex}.v1.command.run.execute"
        return (
            source is not None
            and source.sequence == snapshot.source_sequence
            and source.stream == "OS_RUNS"
            and raw_message.subject == expected_subject
            and snapshot.source_timestamp is not None
            and source.timestamp == _utc(snapshot.source_timestamp)
            and snapshot.payload_digest is not None
            and isinstance(raw_message.data, bytes)
            and hashlib.sha256(raw_message.data).hexdigest() == snapshot.payload_digest
        )

    async def _delete_exact_source(
        self,
        raw_message,
        source_identity: JetStreamSourceIdentity,
        body_digest: str,
    ) -> bool:
        expected_subject = f"openship.{self.installation_id.hex}.v1.command.run.execute"
        if (
            raw_message.subject != expected_subject
            or raw_message.seq != source_identity.sequence
            or _utc(raw_message.time) != source_identity.timestamp
            or not isinstance(raw_message.data, bytes)
            or hashlib.sha256(raw_message.data).hexdigest() != body_digest
        ):
            return False
        try:
            if await self.js.delete_msg("OS_RUNS", source_identity.sequence):
                return True
        except NotFoundError:
            return True
        except Exception as error:
            logger.warning(
                "exhaustion_source_delete_unconfirmed type=%s", type(error).__name__
            )
        try:
            await self.js.get_msg("OS_RUNS", seq=source_identity.sequence)
            return False
        except NotFoundError:
            return True
        except Exception:
            return False

    async def _publish_failure(self, handoff: FailureHandoff) -> bool:
        from .relay import failure_handoff_state, publish_failure_handoff

        try:
            if self.failure_publisher is None:
                confirmed = await publish_failure_handoff(
                    self.js,
                    handoff,
                    session_factory=self.session_factory,
                    run_sync=self._run_relay_sync,
                )
            else:
                confirmed = await self.failure_publisher(handoff) is True
            if not confirmed:
                return False
            valid, processed = await self._run_relay_sync(
                failure_handoff_state, self.session_factory, handoff
            )
            return valid and processed
        except Exception as error:
            logger.warning(
                "exhaustion_failure_handoff_unconfirmed type=%s",
                type(error).__name__,
            )
            return False

    async def _load_failure_handoff(
        self, source_identity: JetStreamSourceIdentity
    ) -> FailureHandoff | None:
        return await self._run_sync(
            failure_handoff_for_source,
            installation_id=self.installation_id,
            source_identity=source_identity,
        )

    async def _handle_advisory(self, message) -> None:
        try:
            advisory = parse_max_deliver_advisory(message.subject, message.data)
        except InvalidMaxDeliverAdvisory:
            logger.warning("max_deliver_advisory_invalid")
            await self._nak_advisory(message, "invalid_advisory")
            return
        try:
            raw_message = await self.js.get_msg("OS_RUNS", seq=advisory.source_sequence)
        except NotFoundError:
            receipt = await self._run_sync(
                record_missing_source_observation,
                source_sequence=advisory.source_sequence,
                delivery_count=advisory.delivery_count,
            )
            if receipt is not None:
                await self._ack_advisory(message)
            else:
                await self._nak_advisory(message, "source_missing_unrecorded")
            return
        except Exception as error:
            logger.warning(
                "max_deliver_source_lookup_failed type=%s", type(error).__name__
            )
            await self._nak_advisory(message, "source_lookup_failed")
            return

        source_identity = self._source_identity(raw_message)
        if (
            source_identity is None
            or source_identity.sequence != advisory.source_sequence
        ):
            await self._nak_advisory(message, "source_metadata_invalid")
            return

        handoff = await self._load_failure_handoff(source_identity)
        if handoff is not None:
            disposition = DeliveryDisposition(
                handoff.classification, handoff.error_code
            )
            if not handoff_matches_delivery(
                handoff,
                installation_id=self.installation_id,
                source_identity=source_identity,
                disposition=disposition,
                body=raw_message.data,
            ):
                logger.warning("max_deliver_failure_source_conflict")
                await self._run_sync(
                    record_failure_source_conflict,
                    installation_id=self.installation_id,
                    source_identity=source_identity,
                    body=raw_message.data,
                )
                await self._nak_advisory(message, "failure_source_conflict")
                return
            if not await self._publish_failure(handoff):
                await self._nak_advisory(message, "failure_notice_unconfirmed")
                return
            if not await self._delete_exact_source(
                raw_message, source_identity, handoff.body_digest
            ):
                await self._nak_advisory(message, "failure_source_delete_unconfirmed")
                return
            await self._ack_advisory(message)
            return

        if await self._run_sync(
            failure_record_exists,
            installation_id=self.installation_id,
            source_identity=source_identity,
        ):
            await self._run_sync(
                record_failure_source_conflict,
                installation_id=self.installation_id,
                source_identity=source_identity,
                body=raw_message.data,
            )
            await self._nak_advisory(message, "failure_handoff_incomplete")
            return

        receipt = await self._run_sync(
            record_exhaustion_observation,
            body=raw_message.data,
            subject=raw_message.subject,
            source_identity=source_identity,
            delivery_count=advisory.delivery_count,
            advisory_sequence=advisory.source_sequence,
        )
        if receipt is None:
            await self._nak_advisory(message, "observation_unavailable")
            return
        if isinstance(receipt, UnboundExhaustionSource):
            await self._handoff_unbound_source(message, raw_message, source_identity)
            return
        if receipt.state == "watching_live_owner":
            await self._ack_advisory(message)
            return
        if receipt.state == "settling":
            await self._settle_observation(receipt.id)
        elif receipt.state == "blocked":
            logger.warning("max_deliver_observation_blocked")
        await self._ack_advisory(message)

    async def _handoff_unbound_source(
        self, advisory_message, raw_message, source_identity
    ) -> None:
        from .command import parse_run_command

        try:
            command = parse_run_command(
                raw_message.data,
                subject=raw_message.subject,
                installation_id=self.installation_id,
            )
            code = "identity_mismatch"
        except (InvalidCommandEnvelope, RecursionError):
            command = None
            code = "invalid_run_command"
        disposition = DeliveryDisposition("quarantine", code)
        prepared = PreparedDelivery(command, None, None)
        handoff = await self._run_sync(
            _persist_failure_handoff,
            prepared,
            disposition,
            source_identity,
            raw_message.data,
        )
        if handoff is None or not handoff_matches_delivery(
            handoff,
            installation_id=self.installation_id,
            source_identity=source_identity,
            disposition=disposition,
            body=raw_message.data,
        ):
            await self._nak_advisory(advisory_message, "failure_record_unconfirmed")
            return
        if not await self._publish_failure(handoff):
            await self._nak_advisory(advisory_message, "failure_notice_unconfirmed")
            return
        if not await self._delete_exact_source(
            raw_message, source_identity, handoff.body_digest
        ):
            await self._nak_advisory(
                advisory_message, "failure_source_delete_unconfirmed"
            )
            return
        await self._ack_advisory(advisory_message)

    async def _settle_observation(self, observation_id: uuid.UUID) -> bool:
        claim = await self._run_sync(
            claim_exhaustion_settlement,
            observation_id,
        )
        if claim is None:
            return False
        try:
            raw_message = await self.js.get_msg("OS_RUNS", seq=claim.source_sequence)
        except NotFoundError:
            return await self._run_sync(
                mark_exhaustion_resolved,
                observation_id,
            )
        except Exception as error:
            logger.warning(
                "exhaustion_recovery_source_lookup_failed type=%s",
                type(error).__name__,
            )
            return False
        if not self._source_matches_snapshot(raw_message, claim):
            await self._run_sync(
                _block_observation,
                observation_id,
                "source_binding_mismatch",
                datetime.now(UTC),
            )
            logger.warning("max_deliver_source_binding_mismatch")
            return False
        source_identity = self._source_identity(raw_message)
        if source_identity is None:
            return False
        deleted = await self._delete_exact_source(
            raw_message, source_identity, claim.payload_digest
        )
        if not deleted:
            return False
        return await self._run_sync(
            mark_exhaustion_resolved,
            observation_id,
        )

    async def _recover_observations(self) -> None:
        try:
            observation_ids = await self._run_sync(
                due_exhaustion_observation_ids,
                limit=16,
            )
        except Exception as error:
            logger.warning(
                "exhaustion_recovery_scan_failed type=%s", type(error).__name__
            )
            return
        for observation_id in observation_ids:
            try:
                snapshot = await self._run_sync(
                    exhaustion_observation_snapshot,
                    observation_id,
                )
                if snapshot is None or snapshot.state in {"blocked", "resolved"}:
                    continue
                if snapshot.state == "settling":
                    await self._settle_observation(observation_id)
                    continue
                try:
                    raw_message = await self.js.get_msg(
                        "OS_RUNS", seq=snapshot.source_sequence
                    )
                except NotFoundError:
                    receipt = await self._run_sync(
                        refresh_live_owner_observation,
                        observation_id,
                    )
                    if receipt is not None and receipt.state == "settling":
                        await self._run_sync(
                            mark_exhaustion_resolved,
                            observation_id,
                        )
                    continue
                if not self._source_matches_snapshot(raw_message, snapshot):
                    await self._run_sync(
                        _block_observation,
                        observation_id,
                        "source_binding_mismatch",
                        datetime.now(UTC),
                    )
                    logger.warning("max_deliver_source_binding_mismatch")
                    continue
                receipt = await self._run_sync(
                    refresh_live_owner_observation,
                    observation_id,
                )
                if receipt is not None and receipt.state == "settling":
                    await self._settle_observation(observation_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "exhaustion_observation_recovery_failed type=%s",
                    type(error).__name__,
                )


def _persist_failure_handoff(
    db: Session,
    prepared: PreparedDelivery,
    disposition: DeliveryDisposition,
    source_identity: JetStreamSourceIdentity,
    body: bytes,
) -> FailureHandoff | None:
    try:
        return persist_failure_handoff(db, prepared, disposition, source_identity, body)
    finally:
        db.rollback()
