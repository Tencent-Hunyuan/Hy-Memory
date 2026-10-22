import asyncio
import gc
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from one_memory.background_work import (
    BULK_WORKERS,
    bulk_pool,
    in_interactive_read,
    in_read_pool,
    in_the_background,
    shutdown_bulk_pool,
)

# What `asyncio.to_thread` has to hand out. Bulk work sharing this pool has to
# fill it before a request notices, so a test that pins the separation has to
# queue more than this.
SHARED_WORKERS = min(32, (os.cpu_count() or 1) + 4)


def test_a_request_does_not_wait_behind_a_catch_up() -> None:
    """The reason moving work off the loop was not enough by itself.

    Everything handed to a thread the ordinary way shares one pool, and an
    import puts several hand-offs on it per record. With a corpus behind it
    that pool is permanently full, and a request that needs a thread of its
    own waits for one: opening the memory view took 28 seconds on this
    machine while a catch-up ran, the loop free the whole time.
    """
    try:

        async def scenario() -> float:
            # More than the shared pool could serve at once, so sharing it
            # would be visible below rather than absorbed by its width.
            catching_up = [
                asyncio.create_task(in_the_background(time.sleep, 5.0))
                for _ in range(SHARED_WORKERS + BULK_WORKERS + 4)
            ]
            await asyncio.sleep(0.3)  # let them take what they are going to take

            started = time.perf_counter()
            await asyncio.to_thread(lambda: None)  # what answering a request needs
            waited = time.perf_counter() - started

            for task in catching_up:
                task.cancel()
            return waited

        waited = asyncio.run(scenario())
    finally:
        shutdown_bulk_pool()

    assert waited < 0.5, f"请求为拿到一个线程等了 {waited:.3f}s，排在批量工作后面"


def test_bulk_work_is_kept_to_a_few_threads_at_a_time() -> None:
    """A pool that grows with the work is the same problem again."""
    try:
        at_once = 0
        most_at_once = 0
        counting = threading.Lock()

        def occupy_a_thread() -> None:
            nonlocal at_once, most_at_once
            with counting:
                at_once += 1
                most_at_once = max(most_at_once, at_once)
            # Held long enough that everything given a thread is holding one
            # at the same moment, so the count below is the pool's real width
            # rather than how fast the work happened to be.
            time.sleep(0.3)
            with counting:
                at_once -= 1

        async def scenario() -> None:
            await asyncio.gather(
                *(in_the_background(occupy_a_thread) for _ in range(BULK_WORKERS * 4))
            )

        asyncio.run(scenario())
    finally:
        shutdown_bulk_pool()

    assert most_at_once == BULK_WORKERS


def test_the_pool_outlives_one_hand_off() -> None:
    """Rebuilding it per call would cost a thread start per record."""
    try:
        first = bulk_pool()
        assert bulk_pool() is first
    finally:
        shutdown_bulk_pool()


def test_interactive_read_does_not_wait_behind_saturated_default_pool() -> None:
    try:

        async def scenario() -> float:
            blockers = [asyncio.create_task(asyncio.to_thread(time.sleep, 1.0)) for _ in range(64)]
            await asyncio.sleep(0.1)
            started = time.perf_counter()
            await in_interactive_read(lambda: None)
            waited = time.perf_counter() - started
            for blocker in blockers:
                blocker.cancel()
            return waited

        waited = asyncio.run(scenario())
    finally:
        shutdown_bulk_pool()

    assert waited < 0.25


def test_an_abandoned_read_does_not_report_an_unretrieved_failure() -> None:
    """A read that outlives its caller must not leave its failure lying about.

    `in_read_pool` shields the worker, so giving up on an answer does not yank
    a query out from under SQLite. The caller cancels; the read runs on. When
    such an abandoned read then failed, nothing awaited its future any more and
    asyncio reported an exception nobody retrieved -- which the loop handler
    files as `core.loop.task_exception`, a red error sitting next to a lane
    that had already degraded on its own budget and already said so.
    """

    def open_pool() -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-read-pool")

    async def scenario() -> list[BaseException]:
        loop = asyncio.get_running_loop()
        reported: list[BaseException] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: reported.append(context.get("exception")))
        gate = threading.Event()

        def work() -> None:
            gate.wait(5.0)
            raise TimeoutError("raw_context_timeout")

        pool = open_pool()
        try:
            read = asyncio.create_task(in_read_pool(pool, work))
            await asyncio.sleep(0.05)
            read.cancel()  # the lane ran out of budget; the read is still going
            await asyncio.sleep(0.10)  # the cancellation settles first
            gate.set()  # only now does the abandoned read fail
            await asyncio.gather(read, return_exceptions=True)
            # Let the shielded worker finish, then let asyncio notice anything
            # that nobody ever retrieved. The reference has to go: asyncio only
            # reports an unretrieved outcome when the future is collected.
            del read
            for _ in range(5):
                await asyncio.sleep(0.02)
            gc.collect()
            await asyncio.sleep(0)
        finally:
            pool.shutdown(wait=True)
            loop.set_exception_handler(previous)
        return [error for error in reported if isinstance(error, TimeoutError)]

    assert asyncio.run(scenario()) == []


def test_an_awaited_read_still_raises_its_failure() -> None:
    """Marking the outcome seen must not swallow it for a caller who waited."""

    def open_pool() -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-read-pool")

    async def scenario() -> str:
        def work() -> None:
            raise TimeoutError("raw_context_timeout")

        pool = open_pool()
        try:
            with pytest.raises(TimeoutError):
                await in_read_pool(pool, work)
        finally:
            pool.shutdown(wait=True)
        return "raised"

    assert asyncio.run(scenario()) == "raised"
