"""JetStream event notices with database-authoritative replay and repair."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from ..chat.models import Conversation
from ..config import settings
from ..database.session import SessionLocal
from ..messaging.connection import connect_nats
from ..messaging.models import MessageTransportState
from ..runs.models import Event
from .schemas import EventRead
from .service import AuthorizedScope, _event_to_read

logger = logging.getLogger(__name__)
MAX_EVENT_NOTICE_BYTES = 16 * 1024
EVENT_PAGE_SIZE = 100
CLIENT_NOTICE_LIMIT = 256
HEARTBEAT_SECONDS = 15


def event_consumer_config(
    installation_id: uuid.UUID,
    *,
    name: str,
) -> tuple[str, ConsumerConfig]:
    """Build an independent, live-only consumer for one API process."""
    subject = f"openship.{installation_id.hex}.v1.event.>"
    # A per-process ephemeral consumer lets every API receive each notice. It
    # starts at new messages because each SSE client repairs from PostgreSQL.
    # Source: https://nats-io.github.io/nats.py/modules.html
    return subject, ConsumerConfig(
        name=name,
        ack_policy=AckPolicy.NONE,
        deliver_policy=DeliverPolicy.NEW,
        filter_subject=subject,
        inactive_threshold=30.0,
    )


class InvalidEventNotice(ValueError):
    """A JetStream event notice does not match its scoped reference contract."""


class SnapshotRequired(RuntimeError):
    """Stored history cannot safely continue from the requested cursor."""


class BrokerUnavailable(RuntimeError):
    """The API process cannot currently receive event notices."""


@dataclass(frozen=True, slots=True)
class EventNotice:
    conversation_id: uuid.UUID
    event_id: uuid.UUID


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise InvalidEventNotice()
        value[key] = item
    return value


def decode_event_notice(
    installation_id: uuid.UUID,
    subject: str,
    payload: bytes,
) -> EventNotice:
    """Validate a reference-only event notice and its installation subject."""
    if not isinstance(payload, bytes) or len(payload) > MAX_EVENT_NOTICE_BYTES:
        raise InvalidEventNotice()
    parts = subject.split(".")
    if (
        len(parts) != 5
        or parts[:4] != ["openship", installation_id.hex, "v1", "event"]
        or len(parts[4]) != 32
    ):
        raise InvalidEventNotice()
    try:
        conversation_id = uuid.UUID(hex=parts[4])
        record = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise InvalidEventNotice() from None
    if not isinstance(record, dict) or record.keys() != {"event_id"}:
        raise InvalidEventNotice()
    event_id_value = record["event_id"]
    if not isinstance(event_id_value, str):
        raise InvalidEventNotice()
    try:
        event_id = uuid.UUID(event_id_value)
    except ValueError:
        raise InvalidEventNotice() from None
    if parts[4] != conversation_id.hex or str(event_id) != event_id_value:
        raise InvalidEventNotice()
    return EventNotice(conversation_id=conversation_id, event_id=event_id)


@dataclass(eq=False, slots=True)
class _ClientSubscription:
    conversation_id: uuid.UUID
    notices: asyncio.Queue[EventNotice | object] = field(
        default_factory=lambda: asyncio.Queue(maxsize=CLIENT_NOTICE_LIMIT)
    )


_OVERFLOW = object()
_BROKER_LOST = object()


class EventFanout:
    """Multiplex local SSE clients over one per-process JetStream consumer."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        installation_id: uuid.UUID | None = None,
        run_sync: Callable[..., Awaitable[Any]] = asyncio.to_thread,
        nats_client_factory: Callable[..., Awaitable[Client]] = connect_nats,
    ) -> None:
        self._session_factory = session_factory
        self.installation_id = installation_id
        self._run_sync = run_sync
        self._nats_client_factory = nats_client_factory
        self._clients: dict[uuid.UUID, set[_ClientSubscription]] = defaultdict(set)
        self._client: Client | None = None
        self._subscription: Subscription | None = None
        self._connection_lost: asyncio.Event | None = None
        self._stop_event: asyncio.Event | None = None
        self._supervisor: asyncio.Task | None = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def client(self) -> Client | None:
        return self._client

    async def start(self) -> None:
        """Start background connection supervision without delaying API startup."""
        if self._supervisor is not None:
            return
        if settings.nats_auth_file is None:
            return
        self._stop_event = asyncio.Event()
        self._supervisor = asyncio.create_task(
            self._run(), name="openship-api-event-consumer"
        )

    async def close(self) -> None:
        """Stop the consumer and close the API-owned NATS connection."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._connection_lost is not None:
            self._connection_lost.set()
        if self._supervisor is not None:
            await self._supervisor
            self._supervisor = None
        self._ready = False
        self._close_subscribers()

    def _installation_id(self) -> uuid.UUID:
        db = self._session_factory()
        try:
            state = db.get(MessageTransportState, 1)
            if state is None:
                raise RuntimeError("messaging installation has not been initialized")
            return state.installation_id
        finally:
            db.close()

    async def _run(self) -> None:
        backoff = 1.0
        while self._stop_event is not None and not self._stop_event.is_set():
            try:
                await self._serve_connection()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - retry broker startup safely.
                logger.warning(
                    "api_event_consumer_unavailable type=%s",
                    type(error).__name__,
                )
            finally:
                self._ready = False
                self._close_subscribers()
                await self._close_connection()

            if self._stop_event is None or self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)

    async def _serve_connection(self) -> None:
        if self._stop_event is None:
            return
        connection_lost = asyncio.Event()
        self._connection_lost = connection_lost

        async def signal_connection_lost(*_args) -> None:
            connection_lost.set()

        installation_id = await self._run_sync(self._installation_id)
        if self.installation_id is not None and self.installation_id != installation_id:
            raise RuntimeError("API messaging installation identity changed")
        self.installation_id = installation_id
        client = await self._nats_client_factory(
            name="openship-api-events",
            inbox_prefix=f"openship.{installation_id.hex}.v1.inbox.api".encode(),
            allow_reconnect=False,
            error_cb=signal_connection_lost,
            disconnected_cb=signal_connection_lost,
            closed_cb=signal_connection_lost,
        )
        self._client = client
        subject, config = event_consumer_config(
            installation_id,
            name=f"api-events-{uuid.uuid4().hex}",
        )
        js = client.jetstream(timeout=2)
        # JetStream creates an independent ephemeral consumer when no durable is
        # supplied. A fresh DB high-water replay repairs messages predating it.
        self._subscription = await js.subscribe(
            subject,
            cb=self._on_message,
            stream="OS_EVENTS",
            config=config,
            pending_msgs_limit=4096,
            pending_bytes_limit=16 * 1024 * 1024,
        )
        await client.flush(timeout=2)
        self._ready = True
        stop_task = asyncio.create_task(self._stop_event.wait())
        loss_task = asyncio.create_task(connection_lost.wait())
        try:
            await asyncio.wait(
                (stop_task, loss_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (stop_task, loss_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stop_task, loss_task, return_exceptions=True)

    async def _on_message(self, message: Msg) -> None:
        try:
            if self.installation_id is None:
                raise InvalidEventNotice()
            notice = decode_event_notice(
                self.installation_id,
                message.subject,
                message.data,
            )
        except InvalidEventNotice:
            logger.error("api_event_notice_invalid")
            self._close_subscribers()
            if self._connection_lost is not None:
                self._connection_lost.set()
            return
        self._publish_notice(notice)

    async def _close_connection(self) -> None:
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            except Exception as error:  # noqa: BLE001 - close is best effort.
                logger.debug(
                    "api_event_subscription_close_failed type=%s",
                    type(error).__name__,
                )
            self._subscription = None
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as error:  # noqa: BLE001 - close is best effort.
                logger.debug(
                    "api_event_client_close_failed type=%s",
                    type(error).__name__,
                )
            self._client = None

    def _publish_notice(self, notice: EventNotice) -> None:
        """Offer a reference to each local client without blocking JetStream."""
        for subscriber in tuple(self._clients.get(notice.conversation_id, ())):
            try:
                subscriber.notices.put_nowait(notice)
            except asyncio.QueueFull:
                while not subscriber.notices.empty():
                    subscriber.notices.get_nowait()
                subscriber.notices.put_nowait(_OVERFLOW)

    def _close_subscribers(self, reason: object = _BROKER_LOST) -> None:
        for subscribers in tuple(self._clients.values()):
            for subscriber in tuple(subscribers):
                while not subscriber.notices.empty():
                    subscriber.notices.get_nowait()
                subscriber.notices.put_nowait(reason)

    def _event_window(self, scope: AuthorizedScope) -> tuple[int, int | None]:
        db = self._session_factory()
        try:
            conversation = (
                db.query(Conversation)
                .filter(
                    Conversation.id == scope.conversation_id,
                    Conversation.user_id == scope.user_id,
                )
                .one_or_none()
            )
            if conversation is None:
                raise SnapshotRequired()
            first_retained = (
                db.query(func.min(Event.sequence))
                .filter(Event.conversation_id == scope.conversation_id)
                .scalar()
            )
            return conversation.next_event_sequence - 1, first_retained
        finally:
            db.close()

    def _event_page(
        self,
        conversation_id: uuid.UUID,
        after_sequence: int,
        high_water: int,
    ) -> list[EventRead]:
        db = self._session_factory()
        try:
            events = (
                db.query(Event)
                .filter(
                    Event.conversation_id == conversation_id,
                    Event.sequence > after_sequence,
                    Event.sequence <= high_water,
                )
                .order_by(Event.sequence.asc())
                .limit(EVENT_PAGE_SIZE)
                .all()
            )
            return [_event_to_read(event) for event in events]
        finally:
            db.close()

    async def _events_through(
        self,
        conversation_id: uuid.UUID,
        after_sequence: int,
        high_water: int,
        first_retained: int | None,
    ):
        if high_water <= after_sequence:
            return
        if first_retained is None or first_retained > after_sequence + 1:
            raise SnapshotRequired()

        next_sequence = after_sequence + 1
        while next_sequence <= high_water:
            page = await self._run_sync(
                self._event_page,
                conversation_id,
                next_sequence - 1,
                high_water,
            )
            if not page:
                raise SnapshotRequired()
            for event in page:
                if event.sequence != next_sequence:
                    raise SnapshotRequired()
                yield event
                next_sequence += 1
            if len(page) < EVENT_PAGE_SIZE and next_sequence <= high_water:
                raise SnapshotRequired()

    async def subscribe_conversation(
        self,
        scope: AuthorizedScope,
        after_sequence: int,
    ):
        """Replay committed history, then repair from PostgreSQL on each notice."""
        subscriber = _ClientSubscription(scope.conversation_id)
        self._clients[scope.conversation_id].add(subscriber)
        last_sequence = after_sequence
        try:
            high_water, first_retained = await self._run_sync(
                self._event_window,
                scope,
            )
            if after_sequence > high_water:
                raise SnapshotRequired()
            async for event in self._events_through(
                scope.conversation_id,
                last_sequence,
                high_water,
                first_retained,
            ):
                last_sequence = event.sequence
                yield event

            while True:
                notice = await subscriber.notices.get()
                if notice is _OVERFLOW:
                    raise SnapshotRequired()
                if notice is _BROKER_LOST:
                    raise BrokerUnavailable()
                high_water, first_retained = await self._run_sync(
                    self._event_window,
                    scope,
                )
                async for event in self._events_through(
                    scope.conversation_id,
                    last_sequence,
                    high_water,
                    first_retained,
                ):
                    last_sequence = event.sequence
                    yield event
        finally:
            subscribers = self._clients.get(scope.conversation_id)
            if subscribers is not None:
                subscribers.discard(subscriber)
                if not subscribers:
                    self._clients.pop(scope.conversation_id, None)
