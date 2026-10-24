"""Notices when the event loop stops answering, and says what held it.

Everything a host asks for is served from the same loop as the memory
pipeline, so anything synchronous run on the loop is time nobody gets an
answer. That has already happened twice here in ways nothing reported: a
conversation rebuild held it for 17 seconds, and a task's store writes for
several more. Both were found by hand, from the outside, long after.

The check is a thread rather than a task on purpose. A task cannot observe a
blocked loop, because it cannot run while the loop is blocked — it only sees
the gap afterwards, once the culprit has returned and left no trace. A thread
keeps running throughout, so it can take the stack while the loop is still
stuck in it.
"""

import logging
import re
import sys
import threading
import time
import traceback
from types import FrameType

logger = logging.getLogger("one_memory.loop_watchdog")

DEFAULT_STALL_SECONDS = 2.0
_SAFE_FALLBACK_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.py$")


class LoopWatchdog:
    def __init__(
        self,
        *,
        stall_seconds: float = DEFAULT_STALL_SECONDS,
        heartbeat_seconds: float = 0.2,
        report_interval_seconds: float = 30.0,
    ) -> None:
        self.stall_seconds = stall_seconds
        self.heartbeat_seconds = heartbeat_seconds
        # One report per episode is the point; a loop stuck for a minute
        # should not bury the log it is trying to explain.
        self.report_interval_seconds = report_interval_seconds
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reported_at = 0.0
        self.stalls: list[float] = []
        self.stall_stacks: list[tuple[str, ...]] = []

    def beat(self) -> None:
        """Called from the loop. Its silence is the signal."""
        self._beat = time.monotonic()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._beat = time.monotonic()
        self._thread = threading.Thread(
            target=self._watch,
            name="loop-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)

    def _watch(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            stalled_for = time.monotonic() - self._beat
            if stalled_for < self.stall_seconds:
                continue
            now = time.monotonic()
            if now - self._reported_at < self.report_interval_seconds:
                continue
            self._reported_at = now
            self.stalls.append(stalled_for)
            self.stall_stacks.append(_safe_stacks_for_all_threads())
            logger.warning(
                "event loop blocked for %.1fs — nothing is being answered:\n%s",
                stalled_for,
                _where_every_thread_is(),
            )


def _where_every_thread_is() -> str:
    """A stack per thread, taken while the loop is still blocked.

    The loop's own thread is the one that matters; the others are included
    because what blocks the loop is often waiting on one of them.
    """
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    frames: dict[int, FrameType] = sys._current_frames()
    lines: list[str] = []
    for ident, frame in frames.items():
        lines.append(f"  thread {names.get(ident, '?')} ({ident}):")
        lines.extend(f"    {entry.rstrip()}" for entry in traceback.format_stack(frame)[-12:])
    return "\n".join(lines)


def _safe_stack_for_main_thread() -> tuple[str, ...]:
    main = threading.main_thread().ident
    frame = sys._current_frames().get(main) if main is not None else None
    if frame is None:
        return ()
    lines: list[str] = []
    for entry in traceback.extract_stack(frame)[-16:]:
        parts = entry.filename.replace("\\", "/").split("/")
        try:
            root = parts.index("one_memory")
            relative = "/".join(parts[root:])
        except ValueError:
            relative = parts[-1]
        name = entry.name if entry.name.isidentifier() else "call"
        lines.append(f"{relative}:{entry.lineno}:{name}")
    return tuple(lines)


def _safe_stacks_for_all_threads() -> tuple[str, ...]:
    """Return bounded stack summaries without recording thread names."""
    lines: list[str] = []
    for _ident, frame in sorted(sys._current_frames().items()):
        stack: list[str] = []
        # Most specific frame first; append only complete frames so truncation
        # can never turn a valid location into free text.
        for entry in reversed(traceback.extract_stack(frame)[-8:]):
            parts = entry.filename.replace("\\", "/").split("/")
            try:
                root = parts.index("one_memory")
                relative = "/".join(parts[root:])
            except ValueError:
                fallback = parts[-1]
                relative = fallback if _SAFE_FALLBACK_FILE.fullmatch(fallback) else "unknown.py"
            name = entry.name if entry.name.isidentifier() else "call"
            candidate = f"{relative}:{entry.lineno}:{name}"
            separator = "" if not stack else " > "
            if len("thread|") + len(" > ".join(stack)) + len(separator) + len(candidate) > 256:
                break
            stack.append(candidate)
        if stack:
            lines.append(f"thread|{' > '.join(stack)}")
        if len(lines) == 16:
            break
    return tuple(lines)
