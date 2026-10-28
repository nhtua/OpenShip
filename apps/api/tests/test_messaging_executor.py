"""Thread-owned execution resources and reusable Worker cleanup."""

import asyncio
import threading
import uuid
from datetime import UTC, datetime

import pytest

from src.openship.database.session import SessionLocal
from src.openship.runs.queue import Claim
from src.openship.runs.worker import Supervision, Worker


def test_worker_interrupts_external_supervision_even_on_early_return():
    worker = Worker(session_factory=SessionLocal, checkpointer=None)
    supervision = Supervision()
    try:
        claim = Claim(uuid.uuid4(), 1, datetime.now(UTC), worker.worker_id)
        assert worker.process_job(claim, supervision=supervision) is False
        assert supervision.stop()
    finally:
        worker.engine.dispose()


def test_worker_close_exits_context_and_disposes_once_even_on_error(monkeypatch):
    worker = Worker(session_factory=SessionLocal, checkpointer=None)
    calls = []

    class Context:
        def __exit__(self, *_args):
            calls.append("exit")
            raise RuntimeError("close failed")

    worker._checkpointer_ctx = Context()
    monkeypatch.setattr(worker.engine, "dispose", lambda: calls.append("dispose"))
    with pytest.raises(RuntimeError, match="close failed"):
        worker.close()
    worker.close()
    assert calls == ["exit", "dispose"]


class Resource:
    def __init__(self):
        self.thread = threading.get_ident()
        self.checkpointer = object()
        self.closed = 0

    def close(self):
        assert threading.get_ident() == self.thread
        self.closed += 1


@pytest.mark.asyncio
async def test_independent_slots_reuse_own_resource_and_keep_loop_responsive():
    from src.openship.messaging.executor import ExecutionPool

    resources = []

    def factory():
        resource = Resource()
        resources.append(resource)
        return resource

    pool = ExecutionPool(3, factory)
    background = ExecutionPool(1, Resource)
    started = threading.Event()
    unblock = threading.Event()

    def blocked(resource):
        started.set()
        unblock.wait(5)
        return resource

    try:
        async with background.reserve() as bg, pool.reserve() as first:
            work = asyncio.create_task(first.run(blocked))
            await asyncio.wait_for(asyncio.to_thread(started.wait), 2)
            async with pool.reserve() as second, pool.reserve() as third:
                a, b = await asyncio.wait_for(
                    asyncio.gather(second.run(lambda r: r), third.run(lambda r: r)), 2
                )
                assert a is not b
                assert a.thread != b.thread != threading.get_ident()
                assert a.checkpointer is not b.checkpointer
                assert await second.run(lambda r: r) is a
                assert await bg.run(lambda r: r) not in resources
                await asyncio.sleep(0)
            unblock.set()
            c = await work
            assert c not in (a, b)
            assert len({r.thread for r in resources}) == 3
    finally:
        unblock.set()
        await pool.close()
        await background.close()
    await pool.close()
    assert all(r.closed == 1 for r in resources)


@pytest.mark.asyncio
async def test_cancel_keeps_slot_reserved_until_thread_exits_and_signals_stop():
    from src.openship.messaging.executor import ExecutionPool

    pool = ExecutionPool(1, Resource)
    started = threading.Event()
    unblock = threading.Event()
    stopped = threading.Event()

    def blocking(_resource):
        started.set()
        unblock.wait(5)

    async def owner():
        async with pool.reserve() as slot:
            await slot.run(blocking, stop=stopped.set)

    async def next_owner():
        async with pool.reserve() as slot:
            return await slot.run(lambda r: r)

    task = asyncio.create_task(owner())
    await asyncio.wait_for(asyncio.to_thread(started.wait), 2)
    task.cancel()
    await asyncio.sleep(0.02)
    waiter = asyncio.create_task(next_owner())
    try:
        assert stopped.is_set()
        assert not task.done()
        assert not waiter.done()
        task.cancel()
        await asyncio.sleep(0.02)
        assert not waiter.done()
        unblock.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(waiter, 2)
    finally:
        unblock.set()
        await pool.close()


@pytest.mark.asyncio
async def test_close_wakes_reservations_and_rejects_work_after_release():
    from src.openship.messaging.executor import ExecutionPool, PoolClosed

    pool = ExecutionPool(1, Resource)
    async with pool.reserve() as slot:
        resource = await slot.run(lambda r: r)

        async def waiting():
            async with pool.reserve():
                pytest.fail("closing pool admitted work")

        waiter = asyncio.create_task(waiting())
        await asyncio.sleep(0)
        closing = asyncio.create_task(pool.close())
        with pytest.raises(PoolClosed):
            await asyncio.wait_for(waiter, 2)
        assert not closing.done()
    await asyncio.wait_for(closing, 2)
    assert resource.closed == 1
    with pytest.raises(PoolClosed):
        async with pool.reserve():
            pass
    with pytest.raises(RuntimeError):
        await slot.run(lambda r: r)


def test_worker_normal_loop_shutdown_exits_checkpointer_context(monkeypatch):
    worker = Worker(session_factory=SessionLocal, checkpointer=None)
    calls = []

    class Context:
        def __exit__(self, *_args):
            calls.append("exit")

    worker._checkpointer_ctx = Context()
    monkeypatch.setattr(worker.engine, "dispose", lambda: calls.append("dispose"))
    worker.running = False
    worker.run()
    assert calls == ["exit", "dispose"]


def test_failed_checkpointer_setup_closes_entered_context(monkeypatch):
    from langgraph.checkpoint.postgres import PostgresSaver

    calls = []

    class Checkpointer:
        def setup(self):
            raise RuntimeError("setup failed")

    class Context:
        def __enter__(self):
            calls.append("enter")
            return Checkpointer()

        def __exit__(self, *_args):
            calls.append("exit")

    monkeypatch.setattr(PostgresSaver, "from_conn_string", lambda _url: Context())
    worker = Worker(session_factory=SessionLocal)
    try:
        assert worker.checkpointer is None
        assert calls == ["enter", "exit"]
    finally:
        worker.close()
    assert calls == ["enter", "exit"]


def test_external_supervision_interrupts_real_turn_and_sessions_stay_on_their_threads(
    monkeypatch,
):
    from sqlalchemy.orm import Session

    from src.openship.chat.models import Message
    from src.openship.messaging.delivery import prepare_run_delivery
    from src.openship.runs.models import Run
    from tests.test_messaging_command_claim import _body, _command

    created_sessions = []
    supervisor_threads = []
    seen = []
    supervision = Supervision()
    entered = threading.Event()

    class ThreadSession(Session):
        def __init__(self):
            super().__init__(bind=SessionLocal.kw["bind"])
            self.thread = threading.get_ident()
            created_sessions.append(self)

        def query(self, *args, **kwargs):
            assert threading.get_ident() == self.thread
            return super().query(*args, **kwargs)

        def close(self):
            assert threading.get_ident() == self.thread
            super().close()

    with SessionLocal() as db:
        run_id, _, row_id = _command(db)
        run = db.get(Run, run_id)
        db.add(
            Message(
                conversation_id=run.conversation_id,
                run_id=run_id,
                role="user",
                content="fixture",
            )
        )
        db.commit()
        body, subject = _body(db, row_id)
        prepared = prepare_run_delivery(
            db, body, subject=subject, worker_id="external-stop", lease_seconds=30
        )

    class Graph:
        def __init__(self, invocation):
            self.invocation = invocation

        def invoke(self, *_args, **_kwargs):
            control, _recorder = self.invocation()
            entered.set()
            assert supervision._stop.wait(2)
            seen.append(control.is_stopped())
            control.raise_if_stopped()
            return {"assistant_response": ""}

    monkeypatch.setattr(
        "src.openship.runs.worker.resolve_graph",
        lambda *_args: (
            lambda _checkpoint, invocation_factory: Graph(invocation_factory)
        ),
    )
    worker = Worker(
        worker_id="external-stop", session_factory=ThreadSession, checkpointer=None
    )
    original = worker._supervise

    def supervise(*args):
        supervisor_threads.append(threading.current_thread())
        original(*args)

    monkeypatch.setattr(worker, "_supervise", supervise)

    def interrupt():
        assert entered.wait(2)
        supervision.interrupt()

    interrupter = threading.Thread(target=interrupt)
    interrupter.start()
    try:
        worker.process_job(prepared.decision.claim, supervision=supervision)
        assert seen == [True]
        assert len({s.thread for s in created_sessions}) >= 2
        assert supervisor_threads
        assert all(not thread.is_alive() for thread in supervisor_threads)
    finally:
        supervision.interrupt()
        interrupter.join()
        worker.close()


@pytest.mark.asyncio
async def test_slots_create_distinct_workers_and_checkpointers_on_slot_threads(
    monkeypatch,
):
    from src.openship.messaging.executor import ExecutionPool

    created = []
    closed_on = []
    original_close = Worker.close

    def factory():
        worker = Worker(session_factory=SessionLocal, checkpointer=object())
        created.append((worker, threading.get_ident()))
        return worker

    def close(worker):
        closed_on.append(threading.get_ident())
        original_close(worker)

    monkeypatch.setattr(Worker, "close", close)
    pool = ExecutionPool(3, factory)
    async with pool.reserve() as a, pool.reserve() as b, pool.reserve() as c:
        workers = await asyncio.gather(*(slot.run(lambda r: r) for slot in (a, b, c)))
        assert len({id(w) for w in workers}) == 3
        assert len({id(w.checkpointer) for w in workers}) == 3
    await pool.close()
    assert sorted(closed_on) == sorted(thread for _, thread in created)


def test_worker_error_shutdown_closes_context_and_engine(monkeypatch):
    worker = Worker(session_factory=SessionLocal, checkpointer=None)
    calls = []

    class Context:
        def __exit__(self, *_args):
            calls.append("exit")

    def interrupted(_db):
        raise KeyboardInterrupt

    worker._checkpointer_ctx = Context()
    monkeypatch.setattr(worker, "_refresh_tool_availability", interrupted)
    monkeypatch.setattr(worker.engine, "dispose", lambda: calls.append("dispose"))
    with pytest.raises(KeyboardInterrupt):
        worker.run()
    assert calls == ["exit", "dispose"]


def test_external_supervision_is_interrupted_when_session_creation_fails():
    def unavailable():
        raise RuntimeError("session unavailable")

    worker = Worker(session_factory=unavailable, checkpointer=None)
    supervision = Supervision()
    claim = Claim(uuid.uuid4(), 1, datetime.now(UTC), worker.worker_id)
    try:
        assert worker.process_job(claim, supervision=supervision) is False
        assert supervision.stop()
    finally:
        worker.close()
