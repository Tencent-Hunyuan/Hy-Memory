import asyncio
import threading
import time

import pytest

from one_memory.background_work import (
    in_read_pool,
    memory_read_pool,
    raw_read_pool,
    shutdown_bulk_pool,
    wait_for_interactive_reads,
    warm_bulk_pool,
)
from one_memory.imports.async_bridge import iterate_in_worker


def test_reader_rejects_parallel_workers_that_could_close_during_next() -> None:
    async def read() -> None:
        async for _ in iterate_in_worker(lambda: iter(()), max_workers=2):
            pass

    with pytest.raises(ValueError, match="exactly one worker"):
        asyncio.run(read())


def test_cancelling_a_read_stops_the_worker_before_the_batch_finishes() -> None:
    entered = threading.Event()
    closed = threading.Event()
    records_read = 0

    def records():
        nonlocal records_read
        try:
            for value in range(64):
                entered.set()
                time.sleep(0.02)
                records_read += 1
                yield value
        finally:
            closed.set()

    async def read() -> None:
        task = asyncio.create_task(anext(iterate_in_worker(records)))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(closed.wait, 1)

    asyncio.run(read())

    assert records_read < 64


def test_starting_a_read_does_not_stop_the_app_answering(monkeypatch) -> None:
    """Starting the reader's thread was made the loop's problem.

    A scan gets a thread of its own, and asking for the first batch is what
    starts it. Starting a thread is nothing until the machine is busy, and
    during a catch-up it is: the loop watchdog caught this waiting 2.5s for
    one, with a scan beginning per source and per file.
    """
    # The app warms this at startup, on an idle machine; what is under
    # test is the thread a scan needs, not the ones the process keeps.
    warm_bulk_pool()
    starting = threading.Thread.start
    event_loop_thread = threading.get_ident()
    source_reader_start_threads: list[int] = []

    def start_slowly(self) -> None:
        # What a loaded machine does to a thread start.
        if self.name.startswith("source-reader"):
            source_reader_start_threads.append(threading.get_ident())
            time.sleep(0.4)
        starting(self)

    monkeypatch.setattr(threading.Thread, "start", start_slowly)

    async def read_while_watching_the_loop() -> tuple[float, list[int]]:
        longest = 0.0

        async def heartbeat() -> None:
            nonlocal longest
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.005)
                now = time.perf_counter()
                longest = max(longest, now - last)
                last = now

        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.05)  # let it beat before anything is measured
        try:
            read = [item async for item in iterate_in_worker(lambda: iter(range(5)))]
        finally:
            beat.cancel()
        return longest, read

    try:
        longest, read = asyncio.run(read_while_watching_the_loop())
    finally:
        shutdown_bulk_pool()

    # It really read, so this cannot pass by reading nothing.
    assert read == [0, 1, 2, 3, 4]
    assert event_loop_thread not in source_reader_start_threads
    assert longest < 0.6, f"开始读取时循环被堵住 {longest:.3f}s"


def test_source_parsers_are_globally_serialized_to_protect_interactive_reads() -> None:
    active = 0
    maximum = 0
    lock = threading.Lock()

    def source():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        yield "record"

    async def collect() -> None:
        async def one(scope_key: str) -> list[str]:
            return [item async for item in iterate_in_worker(source, scope_key=scope_key)]

        assert await asyncio.gather(*(one("cursor") for _ in range(4))) == [
            ["record"],
            ["record"],
            ["record"],
            ["record"],
        ]

    asyncio.run(collect())
    assert maximum == 1


def test_source_parsers_for_different_sources_can_run_in_parallel() -> None:
    active = 0
    maximum = 0
    lock = threading.Lock()
    release = threading.Event()

    def source():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        assert release.wait(1)
        with lock:
            active -= 1
        yield "record"

    async def collect() -> None:
        tasks = [
            asyncio.create_task(_collect(source, scope_key=scope))
            for scope in ("workbuddy", "cursor", "codex", "claude-code")
        ]
        for _ in range(100):
            if maximum == 4:
                break
            await asyncio.sleep(0.005)
        release.set()
        assert await asyncio.gather(*tasks) == [["record"]] * 4

    async def _collect(factory, *, scope_key: str) -> list[str]:
        return [item async for item in iterate_in_worker(factory, scope_key=scope_key)]

    try:
        asyncio.run(collect())
    finally:
        release.set()
        shutdown_bulk_pool()
    assert maximum == 4


def test_source_reads_reuse_a_bounded_process_pool(monkeypatch) -> None:
    started = 0
    original_start = threading.Thread.start

    def count_source_readers(self) -> None:
        nonlocal started
        if self.name.startswith("source-reader"):
            started += 1
        original_start(self)

    monkeypatch.setattr(threading.Thread, "start", count_source_readers)

    async def collect() -> None:
        async def one(value: int) -> list[int]:
            return [item async for item in iterate_in_worker(lambda: iter((value,)))]

        for offset in range(0, 24, 4):
            assert await asyncio.gather(*(one(offset + index) for index in range(4))) == [
                [offset],
                [offset + 1],
                [offset + 2],
                [offset + 3],
            ]

    asyncio.run(collect())

    assert started <= 4


def test_source_parser_waits_while_an_interactive_read_is_running() -> None:
    read_started = threading.Event()
    release_read = threading.Event()
    parser_started = threading.Event()

    def blocking_read() -> None:
        read_started.set()
        assert release_read.wait(2)

    def source():
        parser_started.set()
        yield "record"

    async def scenario() -> list[str]:
        read = asyncio.create_task(in_read_pool(raw_read_pool(), blocking_read))
        assert await asyncio.to_thread(read_started.wait, 1)
        parsing = asyncio.create_task(_collect(source))
        await asyncio.sleep(0.05)
        assert not parser_started.is_set()
        release_read.set()
        await read
        return await parsing

    async def _collect(factory) -> list[str]:
        return [item async for item in iterate_in_worker(factory)]

    try:
        assert asyncio.run(scenario()) == ["record"]
    finally:
        release_read.set()
        shutdown_bulk_pool()


def test_cancelled_read_keeps_parser_backpressure_until_native_work_stops() -> None:
    read_started = threading.Event()
    release_read = threading.Event()
    parser_started = threading.Event()

    def blocking_read() -> None:
        read_started.set()
        assert release_read.wait(2)

    def source():
        parser_started.set()
        yield "record"

    async def collect() -> list[str]:
        return [item async for item in iterate_in_worker(source)]

    async def scenario() -> list[str]:
        read = asyncio.create_task(in_read_pool(raw_read_pool(), blocking_read))
        assert await asyncio.to_thread(read_started.wait, 1)
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        parsing = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        assert not parser_started.is_set()
        release_read.set()
        return await parsing

    try:
        assert asyncio.run(scenario()) == ["record"]
    finally:
        release_read.set()
        shutdown_bulk_pool()


def test_cancelled_interactive_read_releases_backpressure_after_loop_closes() -> None:
    read_started = threading.Event()
    release_read = threading.Event()

    def blocking_read() -> None:
        read_started.set()
        assert release_read.wait(2)

    async def cancel_read() -> None:
        task = asyncio.create_task(in_read_pool(memory_read_pool(), blocking_read))
        assert await asyncio.to_thread(read_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(cancel_read())
        release_read.set()
        asyncio.run(asyncio.wait_for(wait_for_interactive_reads(), 1))
    finally:
        release_read.set()
        shutdown_bulk_pool()


def test_saturated_raw_reads_do_not_delay_memory_reads() -> None:
    raw_started = threading.Barrier(5)
    release_raw = threading.Event()

    def blocking_raw() -> None:
        raw_started.wait(timeout=2)
        assert release_raw.wait(2)

    async def scenario() -> float:
        raw_tasks = [
            asyncio.create_task(in_read_pool(raw_read_pool(), blocking_raw)) for _ in range(4)
        ]
        await asyncio.to_thread(raw_started.wait, 2)
        started = time.monotonic()
        memory = await in_read_pool(memory_read_pool(), lambda: "memory")
        elapsed = time.monotonic() - started
        assert memory == "memory"
        release_raw.set()
        await asyncio.gather(*raw_tasks)
        return elapsed

    try:
        assert asyncio.run(scenario()) < 0.2
    finally:
        release_raw.set()
        shutdown_bulk_pool()


def test_recall_reads_answer_while_every_sdk_write_thread_is_stuck() -> None:
    """A write that hangs in the SDK keeps its thread, not recall's (#231)."""
    from one_memory.background_work import SDK_WRITE_WORKERS, in_sdk_read, in_sdk_write

    stuck = threading.Barrier(SDK_WRITE_WORKERS + 1)
    release = threading.Event()

    def hung_write() -> None:
        stuck.wait(timeout=2)
        assert release.wait(5)

    async def scenario() -> tuple[float, int]:
        writes = [asyncio.create_task(in_sdk_write(hung_write)) for _ in range(SDK_WRITE_WORKERS)]
        # One more than the pool holds: it waits for a write thread, as the
        # drain's next write would.
        queued = asyncio.create_task(in_sdk_write(lambda: None))
        await asyncio.to_thread(stuck.wait, 2)
        started = time.monotonic()
        answer = await asyncio.wait_for(in_sdk_read(lambda: 7), 1)
        elapsed = time.monotonic() - started
        assert not queued.done()
        release.set()
        await asyncio.gather(*writes, queued)
        return elapsed, answer

    try:
        elapsed, answer = asyncio.run(scenario())
        assert answer == 7
        assert elapsed < 0.2
    finally:
        release.set()
        shutdown_bulk_pool()


def test_sdk_writes_run_on_their_own_bounded_threads() -> None:
    from one_memory.background_work import SDK_WRITE_WORKERS, in_sdk_write, sdk_write_pool

    try:
        name = asyncio.run(in_sdk_write(lambda: threading.current_thread().name))
        assert name.startswith("one-memory-sdk-write")
        assert sdk_write_pool()._max_workers == SDK_WRITE_WORKERS
    finally:
        shutdown_bulk_pool()
