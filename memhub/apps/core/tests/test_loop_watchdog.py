import asyncio
import logging
import threading
import time

from one_memory.loop_watchdog import LoopWatchdog


def test_a_blocked_loop_is_reported_while_it_is_still_blocked(caplog) -> None:
    """Nothing reported either of the two stalls found here so far.

    Both were noticed from outside, by hand, days later — a conversation
    rebuild holding the loop 17 seconds, and a task's store writes holding it
    for several more. In between, every host asking to recall something simply
    waited, and no log said why.
    """
    watchdog = LoopWatchdog(stall_seconds=0.3, heartbeat_seconds=0.05)

    async def block_the_loop_on_purpose() -> None:
        async def heartbeat() -> None:
            while True:
                watchdog.beat()
                await asyncio.sleep(0.05)

        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.1)
        # Synchronous, on the loop: the shape of the real defect.
        i_am_the_culprit()
        beat.cancel()

    def i_am_the_culprit() -> None:
        time.sleep(0.8)

    watchdog.start()
    with caplog.at_level(logging.WARNING, logger="one_memory.loop_watchdog"):
        asyncio.run(block_the_loop_on_purpose())
    watchdog.stop()

    assert watchdog.stalls, "看门狗没有发现阻塞"
    assert watchdog.stalls[0] >= 0.3
    assert any("i_am_the_culprit" in line for line in watchdog.stall_stacks[0])
    report = "\n".join(record.getMessage() for record in caplog.records)
    # The point is not that something was slow but which thing it was, so the
    # report has to name it.
    assert "i_am_the_culprit" in report, report


def test_a_loop_that_keeps_running_is_left_alone(caplog) -> None:
    """A watchdog that cries during normal work would be turned off.

    The margin between beating and complaining is wide on purpose. A machine
    busy enough can hold a healthy loop for a tenth of a second, and this
    failing then would be the test crying rather than the watchdog.
    """
    watchdog = LoopWatchdog(stall_seconds=2.0, heartbeat_seconds=0.05)

    async def ordinary_work() -> None:
        for _ in range(25):
            watchdog.beat()
            await asyncio.sleep(0.02)

    watchdog.start()
    with caplog.at_level(logging.WARNING, logger="one_memory.loop_watchdog"):
        asyncio.run(ordinary_work())
    watchdog.stop()

    assert watchdog.stalls == []
    assert caplog.records == []


def test_one_stall_is_reported_once_rather_than_every_check() -> None:
    """A loop stuck for a minute should not bury the log explaining it."""
    watchdog = LoopWatchdog(
        stall_seconds=0.2,
        heartbeat_seconds=0.05,
        report_interval_seconds=30.0,
    )
    watchdog.start()
    # Never beats, so it is stalled for the whole of this.
    time.sleep(1.0)
    watchdog.stop()

    assert len(watchdog.stalls) == 1


def test_stall_captures_a_safe_stack_for_every_thread() -> None:
    stop = threading.Event()

    def background_reader() -> None:
        stop.wait()

    worker = threading.Thread(target=background_reader, name="source-reader-test")
    worker.start()
    watchdog = LoopWatchdog(stall_seconds=0.1, heartbeat_seconds=0.02)
    try:
        watchdog.start()
        time.sleep(0.3)
        watchdog.stop()
    finally:
        stop.set()
        worker.join(timeout=1)

    assert watchdog.stall_stacks
    captured = watchdog.stall_stacks[0]
    assert captured
    assert len(captured) <= 16
    assert all(line.startswith("thread|") for line in captured)
    assert all("source-reader-test" not in line and "MainThread" not in line for line in captured)
    assert all("/Users/" not in line and "\n" not in line for line in captured)
