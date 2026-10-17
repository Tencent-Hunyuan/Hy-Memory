from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from one_memory.background_work import in_the_background
from one_memory.diagnostic_log import (
    diagnostic_event,
    diagnostic_exception,
    record_database_error,
)
from one_memory.imports.adapters import (
    ScanCursorTouch,
    ScanFailure,
    ScannedEvidence,
    ScanOptions,
    ScanSkip,
    SourceCursor,
)
from one_memory.imports.jobs import ImportJob, ImportJobStore, ImportStatus
from one_memory.imports.supervisor import ImportSupervisor

#: How long an automatic retry will wait for a wedged worker to notice cancel.
ABANDON_TIMEOUT_SECONDS = 5.0

#: Scan events the runner would have to write down. Progress events are the
#: adapter narrating itself; nothing about them needs a job to exist.
WORK_EVENTS = (ScannedEvidence, ScanCursorTouch, ScanSkip, ScanFailure)


@runtime_checkable
class _LiveBaselineAdapter(Protocol):
    async def live_baseline(self) -> dict[str, SourceCursor]: ...


class LiveSyncState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class LiveSyncHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    #: How this source is being followed. Watching is layered on top of the
    #: timer rather than replacing it, so the timer stays the backstop.
    mode: str = "polling"
    state: LiveSyncState
    last_job_id: str | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_code: str | None = None


class PollingLiveSyncSupervisor:
    """Near-live sync that reuses each adapter's persisted incremental cursor."""

    def __init__(
        self,
        *,
        source_ids: Iterable[str],
        import_supervisor: ImportSupervisor,
        job_store: ImportJobStore,
        interval_seconds: float = 60,
        mode: str = "polling",
        sync_authorized: Callable[[str], bool] | None = None,
    ) -> None:
        if interval_seconds < 5:
            raise ValueError("interval_seconds must be at least 5")
        self.source_ids = tuple(dict.fromkeys(source_ids))
        self.import_supervisor = import_supervisor
        self.job_store = job_store
        self.interval_seconds = interval_seconds
        self.mode = mode
        self.sync_authorized = sync_authorized
        self._active_jobs: dict[str, str] = {}
        self._watched_sources: frozenset[str] | None = None
        self._health = {
            source_id: LiveSyncHealth(source_id=source_id, mode=mode, state=LiveSyncState.IDLE)
            for source_id in self.source_ids
        }
        self._locks = {source_id: asyncio.Lock() for source_id in self.source_ids}
        self._task: asyncio.Task[None] | None = None
        self._observers: set[asyncio.Task[None]] = set()

    def _set_health(self, source_id: str, value: LiveSyncHealth) -> None:
        previous = self._health.get(source_id)
        self._health[source_id] = value
        if previous is not None and (
            previous.state,
            previous.last_error_code,
        ) == (value.state, value.last_error_code):
            return
        diagnostic_event(
            level=(
                "warn"
                if value.state in {LiveSyncState.DEGRADED, LiveSyncState.UNAVAILABLE}
                else "debug"
            ),
            component="import.live_sync",
            event="live_sync.state_changed",
            outcome="changed",
            job_id=value.last_job_id,
            state_before=str(previous.state) if previous is not None else "unknown",
            state_after=str(value.state),
            error_code=value.last_error_code,
            details={"source_id": source_id, "mode": value.mode},
        )

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def health(self, source_id: str) -> LiveSyncHealth:
        value = self._health.get(
            source_id,
            LiveSyncHealth(source_id=source_id, mode=self.mode, state=LiveSyncState.UNAVAILABLE),
        )
        # Stored permission refusals are stale the moment consent flips. Read
        # the authorization now rather than replaying what a past poll saw: an
        # authorized source must never show "sync required", and a revoked one
        # must show it immediately, whatever the poll wrote last.
        if (
            value.last_error_code == "sync_permission_required"
            and self.sync_authorized is not None
            and self.sync_authorized(source_id)
        ):
            value = value.model_copy(update={"state": LiveSyncState.IDLE, "last_error_code": None})
        # "Unavailable" means no reader exists for the source at all; only a
        # real reader can clear it. Without one, a grant alone does not make
        # the source healthy, so a stored refusal here survives the read.
        if self._watched_sources is None:
            return value
        return value.model_copy(
            update={
                "mode": "watch_and_polling" if source_id in self._watched_sources else "polling",
            }
        )

    def set_watched_sources(self, source_ids: Iterable[str]) -> None:
        self._watched_sources = frozenset(source_ids)

    def all_health(self) -> tuple[LiveSyncHealth, ...]:
        return tuple(self.health(source_id) for source_id in self.source_ids)

    async def initialize_source(
        self, source_id: str, *, baseline_missing_files: bool = True
    ) -> None:
        """Persist the current EOF before a newly enabled watcher can observe appends."""
        if source_id not in self.import_supervisor.adapters:
            return
        adapter = self.import_supervisor.adapters[source_id]
        baselines = (
            await adapter.live_baseline() if isinstance(adapter, _LiveBaselineAdapter) else {}
        )
        await in_the_background(
            partial(
                self.job_store.ensure_live_cursors,
                user_id="local",
                source_id=source_id,
                baselines=baselines,
                baseline_missing_files=baseline_missing_files,
            )
        )

    async def trigger(self, source_id: str) -> ImportJob | None:
        """Bring a source up to date, if it has anything to bring.

        This is the automatic path: the timer and the file watcher. Neither
        was asked for by a person, so neither may leave an import job behind
        for a source that had nothing new. None means there was nothing to do.
        """
        return await self._sync(source_id, only_when_new=True)

    async def trigger_now(self, source_id: str) -> ImportJob | None:
        """Sync because someone asked, and record it either way.

        A person who presses sync is owed a job to look at, even if the answer
        turns out to be that nothing had changed.
        """
        return await self._sync(source_id, only_when_new=False, wait_for_completion=False)

    async def _sync(
        self,
        source_id: str,
        *,
        only_when_new: bool,
        wait_for_completion: bool = True,
    ) -> ImportJob | None:
        if source_id not in self._locks or source_id not in self.import_supervisor.adapters:
            self._set_health(
                source_id,
                LiveSyncHealth(
                    source_id=source_id,
                    mode=self.mode,
                    state=LiveSyncState.UNAVAILABLE,
                    last_error_code="live_sync_unavailable",
                ),
            )
            return None
        async with self._locks[source_id]:
            if self.sync_authorized is not None and not await in_the_background(
                self.sync_authorized, source_id
            ):
                self._set_health(
                    source_id,
                    LiveSyncHealth(
                        source_id=source_id,
                        mode=self.mode,
                        state=LiveSyncState.UNAVAILABLE,
                        last_error_code="sync_permission_required",
                    ),
                )
                return None
            source_jobs = await in_the_background(self.job_store.list_jobs)
            active_sync = next(
                (
                    job
                    for job in reversed(source_jobs)
                    if job.source_id == source_id
                    and job.operation == "sync"
                    and job.status
                    in {
                        ImportStatus.PENDING,
                        ImportStatus.RUNNING,
                        ImportStatus.PAUSED,
                        ImportStatus.INTERRUPTED,
                    }
                ),
                None,
            )
            if active_sync is not None:
                if active_sync.status is ImportStatus.PAUSED:
                    return active_sync
                if not wait_for_completion and self._task_alive(active_sync.job_id):
                    return active_sync
                if active_sync.status is ImportStatus.PENDING and not self._task_alive(
                    active_sync.job_id
                ):
                    attempted_at = datetime.now(UTC)
                    self.import_supervisor.enqueue(active_sync.job_id)
                    if not wait_for_completion:
                        self._observe_sync(source_id, attempted_at, active_sync.job_id)
                        return active_sync
                    completed = await self.import_supervisor.wait(active_sync.job_id)
                    self._record_outcome(source_id, attempted_at, completed)
                    return completed
                released = await self.import_supervisor.abandon(
                    active_sync.job_id, timeout=ABANDON_TIMEOUT_SECONDS
                )
                if not released:
                    self._note_worker_still_running(source_id)
                    return await in_the_background(self.job_store.require, active_sync.job_id)
                # `resume()` deliberately leaves a pending row pending, so a
                # pending row with no in-memory task would otherwise be
                # selected forever without ever being executed.  This is the
                # corpse left by a heartbeat that lost the database lock or
                # by a process restart between yield and claim.  Enqueue is
                # the operation that actually creates the task.
                attempted_at = datetime.now(UTC)
                self.import_supervisor.resume(active_sync.job_id)
                self.import_supervisor.enqueue(active_sync.job_id)
                completed = await self.import_supervisor.wait(active_sync.job_id)
                self._record_outcome(source_id, attempted_at, completed)
                return completed
            competing = [
                job
                for job in source_jobs
                if job.source_id == source_id
                and job.operation != "sync"
                and job.status
                in {ImportStatus.PENDING, ImportStatus.RUNNING, ImportStatus.INTERRUPTED}
            ]
            # Establish the realtime EOF independently even while history owns
            # this source. Otherwise the P1 job starts from the P2 checkpoint
            # (or from zero) and becomes a second full-history scan.
            adapter = self.import_supervisor.adapters[source_id]
            await self._live_cursors(source_id, adapter)
            # Only one scan of a source may be in flight: they share the
            # cursor, and two readers means every file read twice. With a
            # catch-up still holding the source there is no free moment to ask
            # whether anything is new, and the answer would be yes anyway.
            if only_when_new and not competing and not await self._has_work(source_id):
                # Authorization can land between polls. Once its permission
                # error is stale, the next idle poll is where the health shows
                # "on". Leave any other degraded state alone: it reports a
                # real outcome from the last completed attempt.
                current = self.health(source_id)
                if current.state is LiveSyncState.UNAVAILABLE and current.last_error_code in {
                    "sync_permission_required",
                    "live_sync_unavailable",
                }:
                    self._set_health(
                        source_id,
                        current.model_copy(
                            update={"state": LiveSyncState.IDLE, "last_error_code": None}
                        ),
                    )
                return None
            # History and sync share the source cursor. The central scheduler
            # serializes that source and places this P1 sync ahead of the P2
            # history task at its next bounded checkpoint. No cancellation or
            # status rewrite is needed: history remains pending and resumes
            # automatically after the realtime slice completes.
            return await self._run_sync(source_id, wait_for_completion=wait_for_completion)

    async def _has_work(self, source_id: str) -> bool:
        """Ask the source whether a scan would write anything, before taking a job out.

        A job is a durable record of work: a row to insert, a claim to take, a
        heartbeat to keep and a terminal transition to write. Live sync was
        paying all of that per source per tick just to discover there was
        nothing to import — on a real machine, 83 `sync` jobs in a few minutes,
        every one completing with nothing stored.

        The answer is already cheap. A T17 adapter filters unchanged files out
        at discovery, so a settled source yields nothing but progress events.
        This is the same scan the job would have run, over the same cursors,
        stopped at the first event that needs recording; it is a trigger gate,
        not a second way into the source.
        """
        if self.import_supervisor.prepare_adapter is not None:
            # The hook is handed a job to prepare against, which is the thing
            # we are trying not to create yet. Unused in production.
            return True
        adapter = self.import_supervisor.adapters[source_id]
        cursors = await self._live_cursors(source_id, adapter)
        options = ScanOptions(cursors=cursors)
        has_changes = getattr(adapter, "has_changes", None)
        if has_changes is not None:
            try:
                return bool(await has_changes(options))
            except asyncio.CancelledError:
                raise
            except Exception:
                # The import job owns durable issue reporting. A failed probe
                # must fall through to that path, not masquerade as no change.
                return True
        stream = adapter.scan(options)
        try:
            async for event in stream:
                if isinstance(event, WORK_EVENTS):
                    return True
            return False
        except asyncio.CancelledError:
            raise
        except Exception:
            # Whatever went wrong here is the job's to report: it has the
            # issue table and the failure status, and a user watching the
            # source should see it there rather than lose it to a gate.
            return True
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    async def _live_cursors(self, source_id: str, adapter: object) -> dict[str, SourceCursor]:
        cursors = await in_the_background(
            partial(
                self.job_store.get_source_cursors,
                user_id="local",
                source_id=source_id,
                operation="sync",
            )
        )
        if cursors:
            return cursors
        baselines = (
            await adapter.live_baseline() if isinstance(adapter, _LiveBaselineAdapter) else {}
        )
        return await in_the_background(
            partial(
                self.job_store.ensure_live_cursors,
                user_id="local",
                source_id=source_id,
                baselines=baselines,
            )
        )

    async def _run_sync(
        self, source_id: str, *, wait_for_completion: bool = True
    ) -> ImportJob | None:
        attempted_at = datetime.now(UTC)
        job = await in_the_background(
            partial(self.import_supervisor.create_job, source_id, operation="sync")
        )
        # Consent may be revoked while SQLite creation waits for another writer.
        if self.sync_authorized is not None and not await in_the_background(
            self.sync_authorized, source_id
        ):
            await self.import_supervisor.pause(job.job_id)
            return None
        self._set_health(
            source_id,
            LiveSyncHealth(
                source_id=source_id,
                mode=self.mode,
                state=LiveSyncState.RUNNING,
                last_job_id=job.job_id,
                last_attempt_at=attempted_at,
            ),
        )
        self._active_jobs[source_id] = job.job_id
        self.import_supervisor.enqueue(job.job_id)
        if not wait_for_completion:
            self._observe_sync(source_id, attempted_at, job.job_id)
            return job
        try:
            completed = await self.import_supervisor.wait(job.job_id)
        finally:
            self._active_jobs.pop(source_id, None)
        self._record_outcome(source_id, attempted_at, completed)
        return completed

    def _observe_sync(self, source_id: str, attempted_at: datetime, job_id: str) -> None:
        observer = asyncio.create_task(self._finish_observed_sync(source_id, attempted_at, job_id))
        self._observers.add(observer)
        observer.add_done_callback(self._observers.discard)

    async def _finish_observed_sync(
        self, source_id: str, attempted_at: datetime, job_id: str
    ) -> None:
        try:
            completed = await self.import_supervisor.wait(job_id)
            self._record_outcome(source_id, attempted_at, completed)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._note_internal_error(source_id, error)
        finally:
            if self._active_jobs.get(source_id) == job_id:
                self._active_jobs.pop(source_id, None)

    def _task_alive(self, job_id: str) -> bool:
        task = self.import_supervisor._tasks.get(job_id)
        return task is not None and not task.done()

    def _record_outcome(self, source_id: str, attempted_at: datetime, completed: ImportJob) -> None:
        if completed.status is ImportStatus.COMPLETED and completed.errors == 0:
            self._set_health(
                source_id,
                LiveSyncHealth(
                    source_id=source_id,
                    mode=self.mode,
                    state=LiveSyncState.IDLE,
                    last_job_id=completed.job_id,
                    last_attempt_at=attempted_at,
                    last_success_at=datetime.now(UTC),
                ),
            )
        else:
            self._set_health(
                source_id,
                LiveSyncHealth(
                    source_id=source_id,
                    mode=self.mode,
                    state=LiveSyncState.DEGRADED,
                    last_job_id=completed.job_id,
                    last_attempt_at=attempted_at,
                    last_error_code=(
                        "live_sync_import_failed"
                        if completed.status is not ImportStatus.COMPLETED
                        else "live_sync_import_errors"
                    ),
                ),
            )

    async def stop_source(self, source_id: str) -> None:
        """Freeze future scans; already running work is allowed to finish."""
        self._set_health(
            source_id,
            LiveSyncHealth(
                source_id=source_id,
                mode=self.mode,
                state=LiveSyncState.UNAVAILABLE,
                last_error_code="sync_permission_required",
            ),
        )

    async def shutdown(self) -> None:
        task = self._task
        self._task = None
        if task is None or task.done():
            pass
        else:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        observers = tuple(self._observers)
        for observer in observers:
            observer.cancel()
        for observer in observers:
            with suppress(asyncio.CancelledError):
                await observer

    async def _run(self) -> None:
        # A source owns its cadence; one large catch-up cannot delay every
        # other source. The group keeps shutdown responsible for every poller.
        async with asyncio.TaskGroup() as group:
            for source_id in self.source_ids:
                group.create_task(self._poll_source(source_id))

    async def _poll_source(self, source_id: str) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.trigger(source_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._note_internal_error(source_id, error)
                # The failure may have been a corpse this trigger just cleared.
                # Try once more now, instead of waiting out the whole interval
                # with the source still stuck.
                try:
                    await self.trigger(source_id)
                except asyncio.CancelledError:
                    raise
                except Exception as again:
                    self._note_internal_error(source_id, again)

    def _note_worker_still_running(self, source_id: str) -> None:
        current = self.health(source_id)
        self._set_health(
            source_id,
            current.model_copy(
                update={
                    "state": LiveSyncState.DEGRADED,
                    "last_attempt_at": datetime.now(UTC),
                    "last_error_code": "live_sync_worker_still_running",
                }
            ),
        )

    def _note_internal_error(self, source_id: str, error: Exception) -> None:
        # Type alone used to be all this said. It is not enough to act on:
        # `OperationalError` covers a locked database, a missing table and a
        # corrupt page alike, and without the message and the frames two dozen
        # of these in a log could only be guessed at. The stack is the
        # redacted, `one_memory`-relative one, so it names the call site
        # without printing anyone's paths or query text.
        logging.getLogger(__name__).warning(
            "live_sync_internal_error %s: %s", type(error).__name__, error
        )
        diagnostic_event(
            level="error",
            component="import.live_sync",
            event="live_sync.internal_error",
            outcome="failed",
            error_code="live_sync_internal_error",
            # `reason` is the listed key that can carry the message; see
            # `diagnostic_log._DETAIL_KEYS`. Truncated because a detail value
            # longer than 256 characters is rejected.
            details={"source_id": source_id, "reason": str(error)[:200]},
            **diagnostic_exception(error),
        )
        if isinstance(error, sqlite3.Error):
            record_database_error("import.live_sync", error)
        current = self.health(source_id)
        self._set_health(
            source_id,
            current.model_copy(
                update={
                    "state": LiveSyncState.DEGRADED,
                    "last_attempt_at": datetime.now(UTC),
                    "last_error_code": "live_sync_internal_error",
                }
            ),
        )
