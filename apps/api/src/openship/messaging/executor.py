"""Bounded, reserved execution slots with thread-owned mutable resources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Generic, TypeVar

R = TypeVar("R")
T = TypeVar("T")


class PoolClosed(RuntimeError):
    """No reservations can be admitted after closure starts."""


class ExecutionSlot(Generic[R]):
    """One reserved resource; each call runs on the same dedicated thread."""

    def __init__(self, factory: Callable[[], R]):
        self._factory = factory
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._resource: R | None = None
        self._reserved = False
        self._active: asyncio.Future | None = None

    def _invoke(self, call, args, kwargs):
        if self._resource is None:
            self._resource = self._factory()
        return call(self._resource, *args, **kwargs)

    async def run(
        self,
        call: Callable[..., T],
        *args,
        stop: Callable[[], None] | None = None,
        **kwargs,
    ) -> T:
        """Execute in this reservation, retaining it through cancellation.

        `stop` must be thread-safe (for example, Supervision.interrupt).
        Cancellation is delivered to the caller only once synchronous work exits.
        """
        if not self._reserved or self._active is not None:
            raise RuntimeError("slot must be reserved and idle")
        future = asyncio.get_running_loop().run_in_executor(
            self._executor, self._invoke, call, args, kwargs
        )
        self._active = future
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            try:
                if stop is not None:
                    stop()
            finally:
                # A second cancellation must not release a still-running thread.
                completion = asyncio.gather(future, return_exceptions=True)
                while not completion.done():
                    try:
                        await asyncio.shield(completion)
                    except asyncio.CancelledError:
                        continue
            raise
        finally:
            self._active = None

    def _close_resource(self):
        if self._resource is not None:
            resource, self._resource = self._resource, None
            resource.close()

    async def _close(self):
        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor, self._close_resource
            )
        finally:
            # Work has exited; joining the executor must not block the loop.
            await asyncio.to_thread(self._executor.shutdown, wait=True)


class ExecutionPool(Generic[R]):
    """Independent capacity with no work submitted before reservation."""

    def __init__(self, size: int, resource_factory: Callable[[], R]):
        if type(size) is not int or size < 1:
            raise ValueError("pool size must be a positive integer")
        self._slots = [ExecutionSlot(resource_factory) for _ in range(size)]
        self._available = list(self._slots)
        self._condition = asyncio.Condition()
        self._closed = False
        self._closing: asyncio.Task | None = None

    @asynccontextmanager
    async def reserve(self):
        """Reserve before fetching a delivery; reuse this slot for each step."""
        async with self._condition:
            await self._condition.wait_for(lambda: self._closed or self._available)
            if self._closed:
                raise PoolClosed("execution pool is closed")
            slot = self._available.pop()
            slot._reserved = True
        try:
            yield slot
        finally:
            # Also handle a separately-created task still using this reservation.
            while slot._active is not None:
                completion = asyncio.gather(slot._active, return_exceptions=True)
                try:
                    await asyncio.shield(completion)
                    # Let the run coroutine finish its cleanup before reuse.
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    continue
            async with self._condition:
                slot._reserved = False
                self._available.append(slot)
                self._condition.notify_all()

    async def close(self):
        """Wake waiters, drain reservations and close all resources once."""
        if self._closing is None:
            self._closed = True
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        async with self._condition:
            self._condition.notify_all()
            await self._condition.wait_for(
                lambda: len(self._available) == len(self._slots)
            )
        results = await asyncio.gather(
            *(slot._close() for slot in self._slots), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
