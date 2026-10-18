import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from itertools import islice

from one_memory.background_work import (
    source_reader_pool,
    wait_for_interactive_reads,
    warm_reader,
)
from one_memory.diagnostic_log import diagnostic_event, elapsed_ms

logger = logging.getLogger(__name__)

# One worker, because a single iterator is consumed by one `_take_batch` at a
# time: the loop awaits each batch before asking for the next, so a second
# worker would never find work. Widening this was measured and made no
# difference; it is a parameter now only so the event below can report it.
_DEFAULT_SOURCE_READER_WORKERS = 1
_DEFAULT_SOURCE_READER_BATCH_SIZE = 1
# JSON decoding and source-specific record parsing hold the GIL. Four source
# readers doing that together can delay the request loop for seconds even
# though each reader is off-loop. Keep filesystem discovery parallel, but let
# only one parser execute Python work at a time.
_SOURCE_PARSE_SLOTS: dict[str, threading.Lock] = {}
_SOURCE_PARSE_SLOTS_LOCK = threading.Lock()


async def iterate_in_worker[T](
    factory: Callable[[], Iterator[T]],
    *,
    batch_size: int = _DEFAULT_SOURCE_READER_BATCH_SIZE,
    max_workers: int = _DEFAULT_SOURCE_READER_WORKERS,
    scope_key: str = "shared",
) -> AsyncIterator[T]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if max_workers != 1:
        raise ValueError("iterate_in_worker requires exactly one worker")
    loop = asyncio.get_running_loop()
    executor = source_reader_pool()
    stop_requested = threading.Event()
    # Started from another thread, because starting it is not free when it is
    # needed most: the first hand-off is what starts the worker, and the loop
    # watchdog caught the loop waiting 2.5s for one during a catch-up, with a
    # read beginning per source and per file. Warmed here, the hand-offs below
    # find the thread already there and only ever queue.
    await warm_reader(executor)
    iterator = factory()
    # Timed because the only evidence for or against widening this executor was
    # a synthetic benchmark: one worker or four made no difference there, but
    # that says nothing about a walk over a real source's tens of thousands of
    # files. This reports the whole iteration, and how much of it the loop spent
    # waiting, which is what a queue behind one worker would actually show.
    started = time.monotonic()
    waited = 0.0
    batches = 0
    current_batch: asyncio.Future[list[T]] | None = None
    try:
        while True:
            await wait_for_interactive_reads()
            batch_started = time.monotonic()
            current_batch = loop.run_in_executor(
                executor,
                _take_batch,
                iterator,
                batch_size,
                stop_requested,
                _source_parse_slot(scope_key),
            )
            batch = await current_batch
            current_batch = None
            waited += time.monotonic() - batch_started
            if not batch:
                return
            batches += 1
            for item in batch:
                yield item
    finally:
        # Cancelling the asyncio Future does not stop the function already
        # running in its thread. Signal the batch itself so a live sync can
        # take over at the next record boundary instead of waiting for all 64
        # prefetched records to finish.
        stop_requested.set()
        if current_batch is not None:
            while not current_batch.done():
                try:
                    await asyncio.shield(current_batch)
                except asyncio.CancelledError:
                    continue
            current_batch.exception()
        close = getattr(iterator, "close", None)
        if callable(close):
            closing = loop.run_in_executor(executor, close)
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    continue
            closing.exception()
        # Never fatal. This runs in a `finally`, often while the generator is
        # being closed: an exception raised here replaces whatever the caller
        # was unwinding with, and a diagnostic is not worth an import.
        try:
            diagnostic_event(
                level="debug",
                component="import.async_bridge",
                event="import.source_read_finished",
                outcome="succeeded",
                duration_ms=elapsed_ms(started),
                details={
                    # Keys are fixed by `diagnostic_log._DETAIL_KEYS`; an
                    # unlisted key raises. These are the listed ones that fit:
                    # `count` is the batches, `accounted_ms` the time the loop
                    # spent waiting on this reader.
                    "count": batches,
                    "accounted_ms": round(waited * 1000, 1),
                },
            )
        except Exception:
            logger.debug("source_read_finished not recorded", exc_info=True)


def _take_batch[T](
    iterator: Iterator[T],
    batch_size: int,
    stop_requested: threading.Event,
    parse_slot: threading.Lock,
) -> list[T]:
    with parse_slot:
        batch: list[T] = []
        for item in islice(iterator, batch_size):
            batch.append(item)
            if stop_requested.is_set():
                break
        return batch


def _source_parse_slot(scope_key: str) -> threading.Lock:
    with _SOURCE_PARSE_SLOTS_LOCK:
        return _SOURCE_PARSE_SLOTS.setdefault(scope_key, threading.Lock())
