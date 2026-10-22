"""Trigger incremental import when a source's files change, not only on a timer.

A 60-second poll cannot meet the plan's ten-second freshness target, and simply
shortening the interval pays the file-discovery cost even when nothing changed.
Watching inverts that trade: an idle source costs nothing, and an active one is
scanned seconds after it writes.

This is deliberately *only* a trigger. It calls the same
`PollingLiveSyncSupervisor.trigger` the timer calls, so the host's files remain
the only thing we ever read and no new ingest path is introduced — see decision
D-15 for why a host-pushed write channel was dropped instead of built.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from watchfiles import awatch

from one_memory.diagnostic_log import diagnostic_event
from one_memory.imports.jobs import ImportStatus
from one_memory.imports.live_sync import PollingLiveSyncSupervisor

WATCH_MODE = "watch_and_polling"

#: How many times a watch-triggered scan will wait out a run that predates the
#: change before giving up and leaving it to the timer.
_FOLLOW_UP_ATTEMPTS = 3


class FileWatchLiveSync:
    """Watch each granted source root and trigger its incremental import."""

    def __init__(
        self,
        *,
        roots: Mapping[str, Path],
        supervisor: PollingLiveSyncSupervisor,
        debounce_seconds: float = 0.2,
        min_interval_seconds: float = 5.0,
    ) -> None:
        if debounce_seconds <= 0:
            raise ValueError("debounce_seconds must be positive")
        if min_interval_seconds < debounce_seconds:
            raise ValueError("min_interval_seconds must not be shorter than the debounce")
        # Only watch what this supervisor would actually import, and only roots
        # that exist: watching an absent directory fails the whole watch set,
        # which would take the present sources down with it.
        # Resolved on both sides of the comparison below. The watcher reports
        # real paths, so on macOS a root under `/var` comes back as
        # `/private/var` and a granted root that still has the symlink in it
        # would match nothing — watching would go silently dead for that source.
        self.roots = {
            source_id: Path(root).resolve()
            for source_id, root in roots.items()
            if source_id in supervisor.source_ids and Path(root).is_dir()
        }
        self.supervisor = supervisor
        self.debounce_seconds = debounce_seconds
        self.min_interval_seconds = min_interval_seconds
        self.failures: dict[str, int] = {}
        self.stopped_unexpectedly = False
        self._last_triggered: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._pending: dict[str, datetime] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._shutting_down = False

    @property
    def watching(self) -> tuple[str, ...]:
        return tuple(self.roots)

    def start(self, *, prime: bool = False) -> None:
        if not self.roots or self._shutting_down:
            return
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()
            self.stopped_unexpectedly = False
            self._last_triggered.clear()
            self._task = asyncio.create_task(self._run())
            if prime:
                # A source can be granted after the host already appended the
                # turn that caused setup. The watcher cannot observe a change
                # from before it existed, so immediately run the same bounded
                # trigger once instead of waiting for the 60-second backstop.
                self._queue_changes(set(self.roots.values()))

    async def shutdown(self) -> None:
        task = self._task
        self._shutting_down = True
        self._stop.set()
        try:
            if task is not None:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await self._cancel_workers()
        finally:
            self._task = None
            self._shutting_down = False

    async def _cancel_workers(self) -> None:
        self._pending.clear()
        workers = tuple(self._workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()

    def source_for(self, changed: Path) -> str | None:
        """Attribute a changed path to the source that owns the deepest root."""
        try:
            resolved = changed.resolve()
        except OSError:
            # A symlink loop or a path that vanished mid-resolve must not end the
            # watch loop for every source; the unresolved spelling still matches
            # roots that need no resolving, which is the common case.
            resolved = changed
        best: tuple[int, str] | None = None
        for source_id, root in self.roots.items():
            if resolved == root or root in resolved.parents:
                depth = len(root.parts)
                if best is None or depth > best[0]:
                    best = (depth, source_id)
        return None if best is None else best[1]

    async def handle_changes(
        self,
        changed: set[Path],
        *,
        observed_at: datetime | None = None,
    ) -> tuple[str, ...]:
        """Queue affected sources and wait for their coalesced work to settle."""
        workers = self._queue_changes(changed, observed_at=observed_at)
        await asyncio.gather(*(asyncio.shield(worker) for worker in workers.values()))
        return tuple(workers)

    def _queue_changes(
        self, changed: set[Path], *, observed_at: datetime | None = None
    ) -> dict[str, asyncio.Task[None]]:
        if self._stop.is_set():
            return {}
        observed = observed_at or datetime.now(UTC)
        workers: dict[str, asyncio.Task[None]] = {}
        for source_id in dict.fromkeys(
            source
            for source in (self.source_for(path) for path in sorted(changed))
            if source is not None
        ):
            diagnostic_event(
                level="debug",
                component="import.live_watch",
                event="live_sync.hook_received",
                outcome="queued",
                details={"source_id": source_id, "count": len(changed)},
            )
            self._pending[source_id] = max(self._pending.get(source_id, observed), observed)
            worker = self._workers.get(source_id)
            if worker is None or worker.done():
                worker = asyncio.create_task(self._drain_source(source_id))
                self._workers[source_id] = worker
            workers[source_id] = worker
        return workers

    async def _drain_source(self, source_id: str) -> None:
        try:
            while source_id in self._pending and not self._stop.is_set():
                previous = self._last_triggered.get(source_id)
                delay = (
                    max(0, previous + self.min_interval_seconds - time.monotonic())
                    if previous is not None
                    else 0
                )
                if delay:
                    await asyncio.sleep(delay)
                if self._stop.is_set():
                    return
                # Changes arriving during the cooldown collapse into one scan;
                # changes arriving during that scan remain pending for the next.
                observed = self._pending.pop(source_id)
                self._last_triggered[source_id] = time.monotonic()
                try:
                    await self._trigger_past(source_id, observed)
                    diagnostic_event(
                        level="debug",
                        component="import.live_watch",
                        event="live_sync.hook_completed",
                        outcome="written",
                        details={"source_id": source_id},
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.failures[source_id] = self.failures.get(source_id, 0) + 1
                    diagnostic_event(
                        level="error",
                        component="import.live_watch",
                        event="live_sync.hook_failed",
                        outcome="failed",
                        error_code="live_sync_hook_failed",
                        details={"source_id": source_id, "count": self.failures[source_id]},
                    )
        finally:
            if self._workers.get(source_id) is asyncio.current_task():
                self._workers.pop(source_id, None)

    async def _trigger_past(self, source_id: str, observed: datetime) -> None:
        """Make sure a scan reads the change we saw, not one that predates it.

        A run already in flight is handed back untouched, and one that started
        before this write cannot have read it. Polling tolerates that by trying
        again a minute later, which a ten-second target cannot wait for, so wait
        the stale run out and scan again. Retries remain bounded; a paused or
        interrupted job belongs to the user and must not be resumed by a write.
        """
        for _attempt in range(_FOLLOW_UP_ATTEMPTS):
            job = await self.supervisor.trigger(source_id)
            if (
                job is None
                or job.created_at >= observed
                or job.status in {ImportStatus.PAUSED, ImportStatus.INTERRUPTED}
            ):
                return
            await self.supervisor.import_supervisor.wait(job.job_id)

    async def _run(self) -> None:
        try:
            async for changes in awatch(
                *self.roots.values(),
                debounce=int(self.debounce_seconds * 1000),
                stop_event=self._stop,
                ignore_permission_denied=True,
            ):
                self._queue_changes({Path(path) for _change, path in changes})
        except asyncio.CancelledError:
            raise
        except Exception:
            # A watched root that is deleted or replaced can end the watch for
            # good. Sync still works on the timer, but the failure has to be
            # visible: a task that died holding an exception nobody reads would
            # leave `mode` claiming a watch that is no longer running.
            self.stopped_unexpectedly = True
            self.supervisor.set_watched_sources(())
            logging.getLogger(__name__).warning("file_watch_stopped_unexpectedly")
        finally:
            self._stop.set()
            await self._cancel_workers()
