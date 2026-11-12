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
# One per source the drain runs side by side (`max_concurrent_sources`), and
# two over for the outcome inquiries that follow them.
SDK_WRITE_WORKERS = 12
# Recall's SDK reads: one search and the `get`s behind it (`_SDK_GET_CONCURRENCY`).
SDK_READ_WORKERS = 8
# The durable write queue's own bookkeeping: claim, settle, the per-job reads
# around an SDK write, and the drain's maintenance passes. Each is one short
# statement on the single writer or a reader; the drain runs at most
# `max_concurrent_sources` jobs, and they wait for the writer, not for CPU.
DURABLE_QUEUE_WORKERS = 4
# Lease renewal alone. Renewal is the drain's ownership fence: a lease that
# lapses while its SDK write is still running hands the job to recovery, so it
# must never wait for a thread behind any other work, its own queue's included.
LEASE_RENEWAL_WORKERS = 2

_pool: ThreadPoolExecutor | None = None
_snapshot_pool: ThreadPoolExecutor | None = None
_interactive_pool: ThreadPoolExecutor | None = None
_raw_read_pool: ThreadPoolExecutor | None = None
_memory_read_pool: ThreadPoolExecutor | None = None
_source_reader_pool: ThreadPoolExecutor | None = None
_sdk_write_pool: ThreadPoolExecutor | None = None
_durable_queue_pool: ThreadPoolExecutor | None = None
_lease_renewal_pool: ThreadPoolExecutor | None = None
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
    """Recall's own threads: SDK searches, gets, and what decides between them.

    Recall used to hand these to the default pool, which SDK writes share. A
    write that times out keeps its thread until the SDK returns (#231), so a
    few stuck writes left recall waiting for a thread while `/health`, which
    needs none, still answered.
    """
    global _memory_read_pool
    if _memory_read_pool is None:
        _memory_read_pool = ThreadPoolExecutor(
            max_workers=SDK_READ_WORKERS,
            thread_name_prefix="one-memory-memory-read",
        )
    return _memory_read_pool


def sdk_write_pool() -> ThreadPoolExecutor:
    """SDK writes, bounded and apart from everything else.

    Each thread here only waits for the SDK's own loop thread to answer
    (`_LoopThread.run`), so any thread outside that loop may make the call; a
    stuck write therefore costs a thread of this pool and nobody else's.
    """
    global _sdk_write_pool
    if _sdk_write_pool is None:
        _sdk_write_pool = ThreadPoolExecutor(
            max_workers=SDK_WRITE_WORKERS,
            thread_name_prefix="one-memory-sdk-write",
        )
    return _sdk_write_pool


def durable_queue_pool() -> ThreadPoolExecutor:
    """The durable write queue's database work, apart from bulk work.

    The queue used to hand its claims, settles and maintenance to the bulk
    pool, whose three threads also carry import heartbeats and job listings.
    Under an import peak all three sat in those for tens of seconds, and the
    queue's next statement waited for a thread before it could even join the
    writer's line, and leases lapsed while their SDK writes were still running.
    """
    global _durable_queue_pool
    if _durable_queue_pool is None:
        _durable_queue_pool = ThreadPoolExecutor(
            max_workers=DURABLE_QUEUE_WORKERS,
            thread_name_prefix="one-memory-write-queue",
        )
    return _durable_queue_pool


def lease_renewal_pool() -> ThreadPoolExecutor:
    """Threads that only renew durable write leases (`DurableWriteDrain.renew`)."""
    global _lease_renewal_pool
    if _lease_renewal_pool is None:
        _lease_renewal_pool = ThreadPoolExecutor(
            max_workers=LEASE_RENEWAL_WORKERS,
            thread_name_prefix="one-memory-lease-renewal",
        )
    return _lease_renewal_pool


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

    The durable write queue's pools are warmed here too, for the same reason:
    their first renewal must not pay for starting a thread.
    """
    _start_every_worker(bulk_pool(), BULK_WORKERS, "bulk_pool_warmup_timeout")
    _start_every_worker(
        durable_queue_pool(), DURABLE_QUEUE_WORKERS, "durable_queue_pool_warmup_timeout"
    )
    _start_every_worker(
        lease_renewal_pool(), LEASE_RENEWAL_WORKERS, "lease_renewal_pool_warmup_timeout"
    )


def _start_every_worker(pool: ThreadPoolExecutor, workers: int, timeout_code: str) -> None:
    started = tuple(threading.Event() for _ in range(workers))
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
                raise RuntimeError(timeout_code)
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


async def in_sdk_read[T](
    work: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    """A recall-path read, in recall's own pool (`memory_read_pool`)."""
    return await in_read_pool(memory_read_pool(), work, *args, **kwargs)


async def in_sdk_write[T](
    work: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    """One SDK write call, in the write pool (`sdk_write_pool`)."""
    return await asyncio.get_running_loop().run_in_executor(
        sdk_write_pool(),
        copy_context().run,
        partial(work, *args, **kwargs),
    )


async def in_the_write_queue[T](
    work: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    """One durable write queue statement, in the queue's pool (`durable_queue_pool`).

    It still goes through the one writer and its line, like every write; what
    it no longer does is wait for a bulk thread first.
    """
    return await asyncio.get_running_loop().run_in_executor(
        durable_queue_pool(),
        copy_context().run,
        partial(work, *args, **kwargs),
    )


async def in_lease_renewal[T](
    work: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> T:
    """One lease renewal, in the threads kept for nothing else (`lease_renewal_pool`)."""
    return await asyncio.get_running_loop().run_in_executor(
        lease_renewal_pool(),
        copy_context().run,
        partial(work, *args, **kwargs),
    )


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
    global _source_reader_pool, _sdk_write_pool, _durable_queue_pool, _lease_renewal_pool
    if _durable_queue_pool is not None:
        _durable_queue_pool.shutdown(wait=False, cancel_futures=True)
        _durable_queue_pool = None
    if _lease_renewal_pool is not None:
        _lease_renewal_pool.shutdown(wait=False, cancel_futures=True)
        _lease_renewal_pool = None
    if _sdk_write_pool is not None:
        _sdk_write_pool.shutdown(wait=False, cancel_futures=True)
        _sdk_write_pool = None
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
