"""Where bulk work waits, so that answering someone never waits behind it."""

import asyncio
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from functools import partial

# Bulk work is bounded well below the pool that serves requests. Catching up
# on a corpus is thousands of hand-offs and there is no hurry about any one of
# them; what matters is that it cannot take every worker there is.
BULK_WORKERS = 3
SNAPSHOT_WORKERS = 8
INTERACTIVE_READ_WORKERS = 4
SOURCE_READER_WORKERS = 4

_pool: ThreadPoolExecutor | None = None
_snapshot_pool: ThreadPoolExecutor | None = None
_interactive_pool: ThreadPoolExecutor | None = None
_raw_read_pool: ThreadPoolExecutor | None = None
_memory_read_pool: ThreadPoolExecutor | None = None
_source_reader_pool: ThreadPoolExecutor | None = None
_warmed_readers: weakref.WeakSet[ThreadPoolExecutor] = weakref.WeakSet()
_reader_warm_lock = threading.Lock()
_interactive_reads = 0
_interactive_lock = threading.Lock()


def bulk_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(
            max_workers=BULK_WORKERS,
            thread_name_prefix="one-memory-bulk",
        )
    return _pool


def snapshot_pool() -> ThreadPoolExecutor:
    """Pool for long source snapshots, isolated from short background work."""
    global _snapshot_pool
    if _snapshot_pool is None:
        _snapshot_pool = ThreadPoolExecutor(
            max_workers=SNAPSHOT_WORKERS,
            thread_name_prefix="one-memory-snapshot",
        )
    return _snapshot_pool


def interactive_read_pool() -> ThreadPoolExecutor:
    """Small pool reserved for latency-bound, read-only request work."""
    global _interactive_pool
    if _interactive_pool is None:
        _interactive_pool = ThreadPoolExecutor(
            max_workers=INTERACTIVE_READ_WORKERS,
            thread_name_prefix="one-memory-interactive-read",
        )
    return _interactive_pool


def raw_read_pool() -> ThreadPoolExecutor:
    global _raw_read_pool
    if _raw_read_pool is None:
        _raw_read_pool = ThreadPoolExecutor(
            max_workers=INTERACTIVE_READ_WORKERS,
            thread_name_prefix="one-memory-raw-read",
        )
    return _raw_read_pool


def memory_read_pool() -> ThreadPoolExecutor:
    global _memory_read_pool
    if _memory_read_pool is None:
        _memory_read_pool = ThreadPoolExecutor(
            max_workers=INTERACTIVE_READ_WORKERS,
            thread_name_prefix="one-memory-memory-read",
        )
    return _memory_read_pool


def source_reader_pool() -> ThreadPoolExecutor:
    """Bounded process-wide pool for source iteration and parsing."""
    global _source_reader_pool
    if _source_reader_pool is None:
        _source_reader_pool = ThreadPoolExecutor(
            max_workers=SOURCE_READER_WORKERS,
            thread_name_prefix="source-reader",
        )
    return _source_reader_pool


def warm_bulk_pool() -> None:
    """Start the pool's threads now, while nothing is waiting on them.

    A pool starts a worker when work first arrives, and it starts it wherever
    that work was handed over — on the loop, if that is who handed it over.
    Starting a thread is nothing on an idle machine and 2.5 seconds on a busy
    one, which is precisely when bulk work is being handed over. So it is
    done here instead, at startup, before there is anything to be late for.
    """
    pool = bulk_pool()
    started = tuple(threading.Event() for _ in range(BULK_WORKERS))
    release = threading.Event()

    def occupy(marker: threading.Event) -> None:
        marker.set()
        release.wait()

    # Keep each submitted worker occupied until every slot has started. Tiny
    # no-op submissions can all run serially on one worker, which let this
    # function return while later workers were still first-started by the
    # request loop under load — exactly the pause this warm-up is meant to
    # prevent.
    futures = tuple(pool.submit(occupy, marker) for marker in started)
    try:
        for marker in started:
            if not marker.wait(10):
                raise RuntimeError("bulk_pool_warmup_timeout")
    finally:
        release.set()
    for future in futures:
        future.result(timeout=10)


def _nothing() -> None:
    """Handed to a pool only so that its threads come to exist."""


async def in_the_background[T](
    work: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    """`asyncio.to_thread`, but in the pool set aside for bulk work.

    Moving blocking work off the loop is not enough on its own. Everything
    handed to a thread the ordinary way shares one pool, and importing puts
    several hand-offs on it per record; with a corpus behind it, that pool is
    permanently full. A request that then needs a thread of its own does not
    get one — measured on this machine, opening the memory view took 28
    seconds while a catch-up ran, having waited its turn behind it.

    So bulk work is given a small pool of its own and left to be slow in it.
    """
    return await asyncio.get_running_loop().run_in_executor(
        bulk_pool(),
        copy_context().run,
        partial(work, *args, **kwargs),
    )


async def in_snapshot_background[T](
    work: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    """Run a potentially long source snapshot outside the shared bulk pool."""
    return await asyncio.get_running_loop().run_in_executor(
        snapshot_pool(),
        copy_context().run,
        partial(work, *args, **kwargs),
    )


async def in_interactive_read[T](
    work: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    return await in_read_pool(interactive_read_pool(), work, *args, **kwargs)


def _retrieve[T](future: "asyncio.Future[T]") -> None:
    """Mark a shielded read's outcome as seen.

    `in_read_pool` shields the wrapped worker, so a caller that gives up and
    cancels leaves the read running to completion. When that read then fails,
    nothing awaits the future any more: asyncio reports the orphan at GC as
    "Future exception was never retrieved", the loop handler turns it into a
    `core.loop.task_exception` error diagnostic, and a lane that degraded on
    its own budget -- correctly, and already reported -- looks like a crashed
    task on top of it. Retrieving here keeps the diagnostics honest. Awaiting
    still raises for callers that are still there, so no failure is hidden.
    """
    if not future.cancelled():
        future.exception()


async def in_read_pool[T](
    pool: ThreadPoolExecutor,
    work: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    global _interactive_reads
    with _interactive_lock:
        _interactive_reads += 1
    worker = pool.submit(copy_context().run, partial(work, *args, **kwargs))

    def released(_worker: object) -> None:
        global _interactive_reads
        with _interactive_lock:
            _interactive_reads -= 1

    worker.add_done_callback(released)
    pending = asyncio.wrap_future(worker)
    pending.add_done_callback(_retrieve)
    return await asyncio.shield(pending)


async def wait_for_interactive_reads() -> None:
    """Let latency-bound reads drain before starting another parser batch."""
    while True:
        with _interactive_lock:
            active = _interactive_reads
        if not active:
            return
        await asyncio.sleep(0.01)


async def warm_reader(reader: ThreadPoolExecutor) -> None:
    """Start every source-reader worker away from the request loop, once.

    One instantaneous no-op is insufficient: ThreadPoolExecutor may run every
    no-op on its first worker, or return before that worker is marked idle. The
    next request then synchronously starts another worker on the event-loop
    thread. Occupy all slots behind a barrier so returning from this function
    proves every worker already exists.
    """
    await in_the_background(_warm_reader_pool, reader)


def _warm_reader_pool(reader: ThreadPoolExecutor) -> None:
    with _reader_warm_lock:
        if reader in _warmed_readers:
            return
        started = tuple(threading.Event() for _ in range(SOURCE_READER_WORKERS))
        release = threading.Event()

        def occupy(marker: threading.Event) -> None:
            marker.set()
            release.wait()

        futures = tuple(reader.submit(occupy, marker) for marker in started)
        try:
            for marker in started:
                if not marker.wait(10):
                    raise RuntimeError("source_reader_pool_warmup_timeout")
        finally:
            release.set()
        for future in futures:
            future.result(timeout=10)
        _warmed_readers.add(reader)


def shutdown_bulk_pool() -> None:
    global _pool, _snapshot_pool, _interactive_pool, _raw_read_pool, _memory_read_pool
    global _source_reader_pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
    if _snapshot_pool is not None:
        _snapshot_pool.shutdown(wait=False, cancel_futures=True)
        _snapshot_pool = None
    if _interactive_pool is not None:
        _interactive_pool.shutdown(wait=False, cancel_futures=True)
        _interactive_pool = None
    if _raw_read_pool is not None:
        _raw_read_pool.shutdown(wait=False, cancel_futures=True)
        _raw_read_pool = None
    if _memory_read_pool is not None:
        _memory_read_pool.shutdown(wait=False, cancel_futures=True)
        _memory_read_pool = None
    if _source_reader_pool is not None:
        _source_reader_pool.shutdown(wait=False, cancel_futures=True)
        _source_reader_pool = None
