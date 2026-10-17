import asyncio
import json
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from one_memory.background_work import in_the_background
from one_memory.diagnostic_log import configure_diagnostics, reset_diagnostics
from one_memory.imports.adapters import (
    ScannedEvidence,
    ScanOptions,
    ScanPhase,
    ScanProgressEvent,
    SourceCapabilities,
    SourceCursor,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.async_bridge import iterate_in_worker
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.live_sync import LiveSyncState, PollingLiveSyncSupervisor
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor


class IncrementalFixtureAdapter:
    descriptor = SourceDescriptor(
        source_id="fixture",
        display_name="Fixture",
        storage_kinds=frozenset({StorageKind.JSONL}),
        capabilities=SourceCapabilities(
            can_append=True,
            can_observe_edits=False,
            can_observe_deletes=False,
            can_reconcile_complete_set=False,
        ),
    )

    async def detect(self) -> bool:
        return True

    async def scan(self, options: ScanOptions):
        cursor = SourceCursor(
            file_key="a" * 64,
            file_identity="fixture",
            checkpoint="byte:10",
            checkpoint_anchor="b" * 64,
            observed_size=10,
            observed_modified_ns=1,
        )
        if cursor.file_key not in options.cursors:
            yield ScannedEvidence(
                evidence=EvidenceInput(
                    source_id="fixture",
                    conversation_id="conversation",
                    message_id="message",
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim="A new user turn",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                ),
                cursor=cursor,
            )
        yield ScanProgressEvent(phase=ScanPhase.DONE)


class SettledFixtureAdapter(IncrementalFixtureAdapter):
    """An authorized source with no new work on the next idle poll."""

    async def has_changes(self, options: ScanOptions) -> bool:
        return not options.cursors

    async def live_baseline(self) -> dict[str, SourceCursor]:
        return {
            "a" * 64: SourceCursor(
                file_key="a" * 64,
                file_identity="fixture",
                checkpoint="byte:10",
                checkpoint_anchor="b" * 64,
                observed_size=10,
                observed_modified_ns=1,
                read_to_end=True,
            )
        }


class HoldingHistoryAdapter:
    """First scan blocks until cancelled; later scans yield one new record."""

    descriptor = IncrementalFixtureAdapter.descriptor

    def __init__(self) -> None:
        self.scans = 0
        self.entered = asyncio.Event()
        self.boundary = asyncio.Event()

    async def detect(self) -> bool:
        return True

    async def scan(self, options: ScanOptions):
        self.scans += 1
        if self.scans == 1:
            self.entered.set()
            await self.boundary.wait()
            yield ScanProgressEvent(phase=ScanPhase.READ)
            await asyncio.Event().wait()
        cursor = SourceCursor(
            file_key="c" * 64,
            file_identity="holding",
            checkpoint="byte:10",
            checkpoint_anchor="d" * 64,
            observed_size=10,
            observed_modified_ns=1,
        )
        if cursor.file_key not in options.cursors:
            yield ScannedEvidence(
                evidence=EvidenceInput(
                    source_id="fixture",
                    conversation_id="live-conversation",
                    message_id="live-message",
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim="A turn that just synced",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                ),
                cursor=cursor,
            )
        yield ScanProgressEvent(phase=ScanPhase.DONE)


class BatchedHistoryAdapter:
    """History blocks inside the reader's unpublished batch until cancelled."""

    descriptor = IncrementalFixtureAdapter.descriptor

    def __init__(self) -> None:
        self.scans = 0
        self.reader_entered = threading.Event()
        self.history_records_read = 0

    async def detect(self) -> bool:
        return True

    def _slow_history(self):
        for _index in range(64):
            self.reader_entered.set()
            time.sleep(0.02)
            self.history_records_read += 1
            yield ScanProgressEvent(phase=ScanPhase.READ)

    async def scan(self, options: ScanOptions):
        self.scans += 1
        if self.scans == 1:
            async for event in iterate_in_worker(self._slow_history):
                yield event
            return
        cursor = SourceCursor(
            file_key="1" * 64,
            file_identity="batched-live",
            checkpoint="byte:1",
            checkpoint_anchor="2" * 64,
            observed_size=1,
            observed_modified_ns=1,
        )
        yield ScannedEvidence(
            evidence=EvidenceInput(
                source_id="fixture",
                conversation_id="live-conversation",
                message_id="live-message",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim="Realtime must not wait for an unpublished history batch",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            ),
            cursor=cursor,
        )
        yield ScanProgressEvent(phase=ScanPhase.DONE)


def test_live_sync_preempts_a_reader_batch_before_it_emits_work(tmp_path, monkeypatch) -> None:
    """P1 waits for the next P2 boundary without cancelling the history job."""

    async def scenario() -> None:
        monkeypatch.setattr(
            "one_memory.imports.live_sync.ABANDON_TIMEOUT_SECONDS",
            0.2,
        )
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        adapter = BatchedHistoryAdapter()
        imports = ImportSupervisor(
            adapters={"fixture": adapter},
            evidence_store=evidence,
            job_store=jobs,
            max_concurrent_jobs=1,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )

        async def must_not_abandon(*_args, **_kwargs):
            raise AssertionError("realtime scheduling must not abandon history")

        monkeypatch.setattr(imports, "abandon", must_not_abandon)
        history = imports.create_job("fixture", operation="history")
        imports.enqueue(history.job_id)
        assert await asyncio.to_thread(adapter.reader_entered.wait, 1)

        syncing = asyncio.create_task(live_sync.trigger("fixture"))
        for _ in range(100):
            waiting = imports.service_levels.snapshot().waiting["import"]["realtime"]
            if waiting:
                break
            await asyncio.sleep(0.01)
        assert waiting == 1
        synced = await syncing

        assert synced is not None
        assert synced.status is ImportStatus.COMPLETED
        assert synced.stored == 1
        assert adapter.history_records_read < 64
        resumed = await imports.wait(history.job_id)
        assert resumed.status is ImportStatus.COMPLETED
        assert evidence.count() == 1
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_live_sync_cuts_in_front_of_a_running_history_import(tmp_path) -> None:
    """A new sync must store its raw turn before the history import continues.

    History and sync share one cursor, so they cannot run together. The sync
    takes the source at the next boundary, then history resumes from its cursor.
    """

    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        adapter = HoldingHistoryAdapter()
        arrivals: list[bool] = []

        def on_evidence(_record) -> None:
            from one_memory.imports.runner import live_sync_arrival

            arrivals.append(live_sync_arrival.get())

        imports = ImportSupervisor(
            adapters={"fixture": adapter},
            evidence_store=evidence,
            job_store=jobs,
            on_evidence=on_evidence,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        history = imports.create_job("fixture", operation="history")
        imports.enqueue(history.job_id)
        await adapter.entered.wait()

        syncing = asyncio.create_task(live_sync.trigger("fixture"))
        for _ in range(100):
            waiting = imports.service_levels.snapshot().waiting["import"]["realtime"]
            if waiting:
                break
            await asyncio.sleep(0.01)
        assert waiting == 1
        adapter.boundary.set()
        synced = await syncing

        assert synced is not None
        assert synced.operation == "sync"
        assert synced.status is ImportStatus.COMPLETED
        assert synced.stored == 1
        assert evidence.count() == 1
        assert arrivals == [True]
        resumed = await imports.wait(history.job_id)
        assert resumed.status is ImportStatus.COMPLETED
        assert evidence.count() == 1
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_live_sync_does_not_resume_a_history_import_the_user_paused(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        history = imports.create_job("fixture", operation="history")
        await imports.pause(history.job_id)

        synced = await live_sync.trigger("fixture")

        assert synced is not None and synced.operation == "sync"
        assert jobs.require(history.job_id).status is ImportStatus.PAUSED
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_a_sync_left_running_after_its_worker_died_is_started_again(tmp_path) -> None:
    """A running row with no live worker used to be returned as 'already syncing'.

    The poll kept recording an internal error and never opened the new turn.
    A heartbeat that stopped is the worker being gone, so the next trigger
    has to run the sync, not hand back the corpse.
    """

    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        corpse = imports.create_job("fixture", operation="sync")
        claimed, token = jobs.claim(corpse.job_id)
        assert claimed.status is ImportStatus.RUNNING
        stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE import_jobs SET heartbeat_at = ? WHERE job_id = ?",
                (stale, corpse.job_id),
            )

        synced = await live_sync.trigger("fixture")

        assert synced is not None
        assert synced.status is ImportStatus.COMPLETED
        assert synced.stored == 1
        assert evidence.count() == 1
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


class _FlightAdapter:
    """Counts scans that are inside `scan` at the same time."""

    descriptor = IncrementalFixtureAdapter.descriptor

    def __init__(self, *, ignore_cancel: bool) -> None:
        self.ignore_cancel = ignore_cancel
        self.scans = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def detect(self) -> bool:
        return True

    async def scan(self, options: ScanOptions):
        self.scans += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        first = self.scans == 1
        try:
            if first:
                self.entered.set()
                while not self.release.is_set():
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        if not self.ignore_cancel:
                            raise
            else:
                cursor = SourceCursor(
                    file_key="e" * 64,
                    file_identity="flight",
                    checkpoint="byte:10",
                    checkpoint_anchor="f" * 64,
                    observed_size=10,
                    observed_modified_ns=1,
                )
                if cursor.file_key not in options.cursors:
                    yield ScannedEvidence(
                        evidence=EvidenceInput(
                            source_id="fixture",
                            conversation_id="live-conversation",
                            message_id="live-message",
                            role="user",
                            written_by=WrittenBy.PERSON,
                            verbatim="A turn that just synced",
                            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                        ),
                        cursor=cursor,
                    )
            yield ScanProgressEvent(phase=ScanPhase.DONE)
        finally:
            self.in_flight -= 1


def _backdate_heartbeat(database, job_id: str) -> None:
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE import_jobs SET heartbeat_at = ? WHERE job_id = ?",
            (stale, job_id),
        )


def test_a_stale_history_scan_yields_before_sync_writes(tmp_path, monkeypatch) -> None:
    """A stale history row keeps its checkpoint and yields instead of being cancelled."""

    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        adapter = HoldingHistoryAdapter()
        imports = ImportSupervisor(
            adapters={"fixture": adapter},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        history = imports.create_job("fixture", operation="history")
        imports.enqueue(history.job_id)
        await adapter.entered.wait()
        _backdate_heartbeat(database, history.job_id)

        async def must_not_abandon(*_args, **_kwargs):
            raise AssertionError("realtime scheduling must not abandon history")

        monkeypatch.setattr(imports, "abandon", must_not_abandon)
        try:
            syncing = asyncio.create_task(live_sync.trigger("fixture"))
            for _ in range(100):
                waiting = imports.service_levels.snapshot().waiting["import"]["realtime"]
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting == 1
            adapter.boundary.set()
            synced = await syncing

            assert synced is not None and synced.status is ImportStatus.COMPLETED
            assert evidence.count() == 1
            resumed = await imports.wait(history.job_id)
            assert resumed.status is ImportStatus.COMPLETED
        finally:
            await live_sync.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_realtime_waits_for_a_history_boundary_without_cancelling(tmp_path, monkeypatch) -> None:
    """A same-source P1 waits for a boundary, then runs without cancelling P2."""

    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        adapter = HoldingHistoryAdapter()
        imports = ImportSupervisor(
            adapters={"fixture": adapter},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        history = imports.create_job("fixture", operation="history")
        imports.enqueue(history.job_id)
        await adapter.entered.wait()

        async def must_not_abandon(*_args, **_kwargs):
            raise AssertionError("realtime scheduling must not abandon history")

        monkeypatch.setattr(imports, "abandon", must_not_abandon)
        try:
            syncing = asyncio.create_task(live_sync.trigger("fixture"))
            for _ in range(100):
                waiting = imports.service_levels.snapshot().waiting["import"]["realtime"]
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting == 1
            assert not syncing.done()
            adapter.boundary.set()
            synced = await asyncio.wait_for(syncing, 2)
            assert synced is not None and synced.status is ImportStatus.COMPLETED
            assert evidence.count() == 1
            assert (await imports.wait(history.job_id)).status is ImportStatus.COMPLETED
        finally:
            await live_sync.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_a_sync_worker_that_ignores_cancel_is_not_started_twice(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            "one_memory.imports.live_sync.ABANDON_TIMEOUT_SECONDS",
            0.05,
        )
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        adapter = _FlightAdapter(ignore_cancel=True)
        imports = ImportSupervisor(
            adapters={"fixture": adapter},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        held = imports.create_job("fixture", operation="sync")
        imports.enqueue(held.job_id)
        await adapter.entered.wait()
        _backdate_heartbeat(database, held.job_id)
        try:
            synced = await live_sync.trigger("fixture")

            assert adapter.scans == 1
            assert adapter.max_in_flight == 1
            assert evidence.count() == 0
            assert synced is not None and synced.job_id == held.job_id
            task = imports._tasks[held.job_id]
            assert not task.done()
        finally:
            adapter.release.set()
            await live_sync.shutdown()
            await imports.shutdown()

    asyncio.run(scenario())


def test_a_pending_sync_nobody_is_running_is_started(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        waiting = imports.create_job("fixture", operation="sync")

        synced = await live_sync.trigger("fixture")

        assert synced is not None
        assert synced.job_id == waiting.job_id
        assert synced.status is ImportStatus.COMPLETED
        assert evidence.count() == 1
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_a_pending_sync_left_by_a_dead_runner_is_started_again(tmp_path) -> None:
    """A pending row with no worker must be enqueued, not merely returned.

    ISSUE-0004 left a WorkBuddy sync row pending in the database with
    runner_token NULL.  The old path selected the row as active_sync, called
    abandon() (which cannot change a pending row), then called resume(), which
    returns a pending row unchanged instead of creating a task.  The health
    snapshot and the database therefore stayed pending forever.
    """

    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        old_jobs = ImportJobStore(database)
        old_imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=old_jobs,
        )
        waiting = old_imports.create_job("fixture", operation="sync")
        assert waiting.status is ImportStatus.PENDING
        history = old_imports.create_job("fixture", operation="history")
        assert history.status is ImportStatus.PENDING

        # A new process rebuilds the supervisor and live-sync supervisor over
        # the same database. The old in-memory task registry is gone; the row
        # is still pending with no owner.
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )

        synced = await live_sync.trigger("fixture")

        assert synced is not None
        assert synced.job_id == waiting.job_id
        assert synced.status is ImportStatus.COMPLETED
        assert synced.stored == 1
        assert evidence.count() == 1
        assert live_sync.health("fixture").last_success_at is not None
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_a_pending_sync_is_reenqueued_after_a_locked_heartbeat(tmp_path) -> None:
    """The real ISSUE-0004 shape: a heartbeat saw `database is locked`.

    The runner may finish its slice and leave a pending row after the beat
    failed.  The next trigger must create a task for that row instead of
    returning the same pending receipt forever.
    """

    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        waiting = imports.create_job("fixture", operation="sync")
        # Approximate the post-lock state: runner returned to pending, no task.
        assert waiting.status is ImportStatus.PENDING
        assert waiting.job_id not in imports._tasks

        synced = await live_sync.trigger("fixture")

        assert synced is not None
        assert synced.status is ImportStatus.COMPLETED
        assert synced.stored == 1
        assert evidence.count() == 1
        assert live_sync.health("fixture").last_success_at is not None
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_polling_does_not_run_global_stale_recovery_for_each_source(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        monkeypatch.setattr(
            jobs,
            "recover_stale_running",
            lambda **_kwargs: pytest.fail(
                "global stale recovery belongs to startup, not every source poll"
            ),
        )

        synced = await live_sync.trigger("fixture")

        assert synced is not None
        assert synced.status is ImportStatus.COMPLETED
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_polling_live_sync_reuses_incremental_cursor_without_duplicates(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )

        first = await live_sync.trigger("fixture")
        # The cursor from the first pass leaves the second poll with nothing to
        # read, so it declines to open a job rather than completing an empty one.
        second = await live_sync.trigger("fixture")

        assert first is not None and first.status is ImportStatus.COMPLETED
        assert first.stored == 1
        assert second is None
        assert [job.job_id for job in jobs.list_jobs()] == [first.job_id]
        assert evidence.count() == 1
        assert live_sync.health("fixture").state is LiveSyncState.IDLE
        assert live_sync.health("fixture").last_success_at is not None
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_live_sync_rebases_a_replaced_file_instead_of_rescanning_it(tmp_path) -> None:
    """A replacement corpus belongs to history, not the realtime lane.

    A persisted live cursor can point at yesterday's file identity. If a new
    history import replaces that file, preserving the stale live identity
    makes the next sync restart at byte zero and labels the whole corpus live.
    """
    jobs = ImportJobStore(tmp_path / "one-memory.sqlite3")
    key = "a" * 64
    old = SourceCursor(
        file_key=key,
        file_identity="old-file",
        checkpoint="byte:100",
        checkpoint_anchor="b" * 64,
        observed_size=100,
        observed_modified_ns=1,
        read_to_end=True,
    )
    new = SourceCursor(
        file_key=key,
        file_identity="replacement-file",
        checkpoint="byte:49990",
        checkpoint_anchor="c" * 64,
        observed_size=50000,
        observed_modified_ns=2,
        read_to_end=False,
    )
    assert (
        jobs.ensure_live_cursors(user_id="local", source_id="fixture", baselines={key: old})[key]
        == old
    )
    refreshed = jobs.ensure_live_cursors(user_id="local", source_id="fixture", baselines={key: new})

    assert refreshed[key] == new
    appended = new.model_copy(
        update={
            "checkpoint": "byte:50000",
            "checkpoint_anchor": "d" * 64,
            "observed_size": 50010,
            "observed_modified_ns": 3,
        }
    )
    assert (
        jobs.ensure_live_cursors(user_id="local", source_id="fixture", baselines={key: appended})[
            key
        ]
        == new
    ), "same-identity append progress belongs to live sync, not baseline refresh"


def test_live_sync_rebases_large_same_file_backlog_and_demotes_pending_dispatch(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    jobs = ImportJobStore(database)
    evidence = EvidenceStore(database)
    key = "b" * 64
    stale = SourceCursor(
        file_key=key,
        file_identity="same-file",
        checkpoint="byte:100",
        checkpoint_anchor="c" * 64,
        observed_size=100,
        observed_modified_ns=1,
        read_to_end=False,
    )
    tail = stale.model_copy(
        update={
            "checkpoint": "byte:500000",
            "checkpoint_anchor": "d" * 64,
            "observed_size": 500000,
            "observed_modified_ns": 2,
            "read_to_end": True,
        }
    )
    jobs.ensure_live_cursors(user_id="local", source_id="fixture", baselines={key: stale})
    record = evidence.put(
        EvidenceInput(
            source_id="fixture",
            conversation_id="old-live-backlog",
            message_id="old-1",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="旧内容不应继续占实时通道",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    with evidence._connect(writing=True) as connection:
        evidence.stage_memory_dispatch(
            record,
            file_key=key,
            connection=connection,
            priority="live",
        )

    refreshed = jobs.ensure_live_cursors(
        user_id="local", source_id="fixture", baselines={key: tail}
    )

    assert refreshed[key] == tail
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT priority FROM evidence_pending_memory_dispatch WHERE file_key=?",
            (key,),
        ).fetchone() == ("normal",)


def test_resume_keeps_files_created_while_sync_was_disabled_unbaselined(tmp_path) -> None:
    jobs = ImportJobStore(tmp_path / "one-memory.sqlite3")
    existing = SourceCursor(
        file_key="a" * 64,
        file_identity="existing",
        checkpoint="byte:100",
        checkpoint_anchor="b" * 64,
        observed_size=100,
        observed_modified_ns=1,
        read_to_end=True,
    )
    created_while_disabled = SourceCursor(
        file_key="c" * 64,
        file_identity="new-file",
        checkpoint="byte:500",
        checkpoint_anchor="d" * 64,
        observed_size=500,
        observed_modified_ns=2,
        read_to_end=True,
    )
    jobs.ensure_live_cursors(
        user_id="local", source_id="workbuddy", baselines={existing.file_key: existing}
    )

    resumed = jobs.ensure_live_cursors(
        user_id="local",
        source_id="workbuddy",
        baselines={
            existing.file_key: existing,
            created_while_disabled.file_key: created_while_disabled,
        },
        baseline_missing_files=False,
    )

    assert resumed == {existing.file_key: existing}


def test_live_sync_reports_unavailable_source(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={},
            evidence_store=EvidenceStore(database),
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )

        assert await live_sync.trigger("fixture") is None
        assert live_sync.health("fixture").state is LiveSyncState.UNAVAILABLE

    asyncio.run(scenario())


def test_authorized_health_clears_immediately_without_a_poll(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": SettledFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        authorized = False

        def sync_authorized(_source_id: str) -> bool:
            return authorized

        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
            sync_authorized=sync_authorized,
        )

        assert await live_sync.trigger("fixture") is None
        unavailable = live_sync.health("fixture")
        assert unavailable.state is LiveSyncState.UNAVAILABLE
        assert unavailable.last_error_code == "sync_permission_required"

        authorized = True
        # The fix under test: consent must clear the stale permission refusal
        # at read time, before any further poll runs.
        healthy = live_sync.health("fixture")
        assert healthy.state is LiveSyncState.IDLE
        assert healthy.last_error_code is None
        assert healthy.last_job_id == unavailable.last_job_id
        assert healthy.last_attempt_at == unavailable.last_attempt_at
        assert healthy.last_success_at == unavailable.last_success_at
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_stop_source_reports_revocation_immediately_and_clears_on_consent(tmp_path) -> None:
    """Revoking then stopping must show "sync required" at once, not a minute later.

    Stopping a source is the user's own act: the next `health()` read has to
    reflect it without waiting for a poll. The stored refusal is still live:
    once consent returns, `health()` clears it at read time.
    """

    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": SettledFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        authorized = False

        def sync_authorized(_source_id: str) -> bool:
            return authorized

        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
            sync_authorized=sync_authorized,
        )
        authorized = True
        assert await live_sync.trigger("fixture") is None
        assert live_sync.health("fixture").state is LiveSyncState.IDLE

        authorized = False
        await live_sync.stop_source("fixture")
        revoked = live_sync.health("fixture")
        assert revoked.state is LiveSyncState.UNAVAILABLE
        assert revoked.last_error_code == "sync_permission_required"

        authorized = True
        healthy = live_sync.health("fixture")
        assert healthy.state is LiveSyncState.IDLE
        assert healthy.last_error_code is None
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_unavailable_survives_a_read_when_no_reader_exists(tmp_path) -> None:
    """A grant without a reader is not a healthy source.

    `_sync` records `live_sync_unavailable` when the adapter is missing from
    the supervisor; a later `health()` read must not silently turn that into
    "idle" just because consent says yes. The refusal clears only once a real
    reader exists.
    """

    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": SettledFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture", "ghost"),
            import_supervisor=imports,
            job_store=jobs,
            sync_authorized=lambda _source_id: True,
        )
        assert await live_sync.trigger("ghost") is None
        unavailable = live_sync.health("ghost")
        assert unavailable.state is LiveSyncState.UNAVAILABLE
        assert unavailable.last_error_code == "live_sync_unavailable"
        assert live_sync.health("ghost").state is LiveSyncState.UNAVAILABLE
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_degraded_health_is_not_cleared_by_an_idle_poll(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": SettledFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
        )
        degraded = live_sync.health("fixture").model_copy(
            update={
                "state": LiveSyncState.DEGRADED,
                "last_error_code": "live_sync_import_failed",
            }
        )
        live_sync._set_health("fixture", degraded)  # noqa: SLF001

        assert await live_sync.trigger("fixture") is None
        assert live_sync.health("fixture") == degraded
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_revoked_permission_health_survives_an_idle_poll(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": SettledFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )

        def sync_authorized(_source_id: str) -> bool:
            return False

        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=jobs,
            sync_authorized=sync_authorized,
        )

        assert await live_sync.trigger("fixture") is None
        unavailable = live_sync.health("fixture")
        assert unavailable.state is LiveSyncState.UNAVAILABLE
        assert unavailable.last_error_code == "sync_permission_required"
        await live_sync.shutdown()
        await imports.shutdown()

    asyncio.run(scenario())


def test_slow_source_does_not_block_other_sources_or_leave_pollers_after_shutdown(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "one-memory.sqlite3"
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={}, evidence_store=EvidenceStore(database), job_store=jobs
        )
        live = PollingLiveSyncSupervisor(
            source_ids=("slow", "broken", "fast"),
            import_supervisor=imports,
            job_store=jobs,
        )
        # Speed up only this scheduler test; production still enforces >=5 seconds.
        live.interval_seconds = 0.01
        slow_started = asyncio.Event()
        slow_cancelled = asyncio.Event()
        two_fast_runs = asyncio.Event()
        never = asyncio.Event()
        counts = {"slow": 0, "broken": 0, "fast": 0}

        async def trigger(source_id):
            counts[source_id] += 1
            if source_id == "slow":
                slow_started.set()
                try:
                    await never.wait()
                finally:
                    slow_cancelled.set()
            elif source_id == "broken":
                raise RuntimeError("a source-specific read failure")
            elif counts["fast"] >= 2:
                two_fast_runs.set()

        live.trigger = trigger
        live.start()
        task = live._task
        assert task is not None
        live.start()
        assert live._task is task, "start must not create duplicate polling groups"
        try:
            await asyncio.wait_for(slow_started.wait(), timeout=0.5)
            try:
                await asyncio.wait_for(two_fast_runs.wait(), timeout=0.5)
            except TimeoutError:
                pytest.fail("a slow source blocked independent source synchronization")
            assert counts["slow"] == 1
            assert live.health("broken").state is LiveSyncState.DEGRADED
        finally:
            await live.shutdown()
            await imports.shutdown()
        assert slow_cancelled.is_set()
        assert task.done()
        stopped_counts = counts.copy()
        await asyncio.sleep(0.04)
        assert counts == stopped_counts, "shutdown left an active source poller"

    asyncio.run(scenario())


class _Sluggish:
    """A store whose every call costs what a contended one really costs.

    A write here now takes the write lock up front, so it waits behind
    whatever else is writing: on the real database, during a catch-up, that
    was seconds.
    """

    def __init__(self, inner, delay: float) -> None:
        self._inner = inner
        self._delay = delay

    def __getattr__(self, name):
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def slowly(*args, **kwargs):
            time.sleep(self._delay)
            return attribute(*args, **kwargs)

        return slowly


def test_triggering_a_sync_leaves_the_loop_free_to_answer(tmp_path) -> None:
    """This runs per source, on a timer, for the whole life of the app.

    It reads every job on record and then writes a new one. Made on the loop,
    the watchdog caught it holding the loop 5.7s on the real database, and a
    held loop is every host waiting for a recall that cannot be served.
    """
    delay = 0.2

    async def scenario() -> tuple[float, int]:
        # Paid up front: the first hand-off in a process starts the pool, which
        # is a fifth of a second once and is not what is being measured.
        await in_the_background(lambda: None)
        database = tmp_path / "one-memory.sqlite3"
        evidence = EvidenceStore(database)
        jobs = ImportJobStore(database)
        imports = ImportSupervisor(
            adapters={"fixture": IncrementalFixtureAdapter()},
            evidence_store=evidence,
            job_store=jobs,
        )
        live_sync = PollingLiveSyncSupervisor(
            source_ids=("fixture",),
            import_supervisor=imports,
            job_store=_Sluggish(jobs, delay),
        )
        longest = 0.0
        ticks = 0

        async def heartbeat() -> None:
            nonlocal longest, ticks
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.005)
                now = time.perf_counter()
                longest = max(longest, now - last)
                last = now
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        # Let it beat before the work starts, so its first measurement is not
        # taken after the very stall it is here to catch.
        await asyncio.sleep(0.05)
        try:
            await live_sync.trigger("fixture")
            # A stall is only measured by the beat that follows it.
            await asyncio.sleep(0.02)
        finally:
            beat.cancel()
        return longest, ticks

    longest, ticks = asyncio.run(scenario())

    assert ticks > 1, "心跳没有真的在跑"
    assert longest < delay * 2, f"触发同步期间循环被堵住 {longest:.3f}s"


def test_live_sync_diagnostics_only_emit_state_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("ONE_MEMORY_DIAGNOSTICS", "1")
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    receipt = configure_diagnostics(tmp_path)
    try:
        supervisor = PollingLiveSyncSupervisor(
            source_ids=("codex",),
            import_supervisor=object(),  # type: ignore[arg-type]
            job_store=object(),  # type: ignore[arg-type]
        )
        unavailable = supervisor.health("missing")
        supervisor._set_health("codex", unavailable.model_copy(update={"source_id": "codex"}))
        supervisor._set_health("codex", unavailable.model_copy(update={"source_id": "codex"}))
    finally:
        reset_diagnostics()
        root.setLevel(previous)
    events = [json.loads(line) for line in receipt.path.read_text().splitlines()]
    assert [event["event"] for event in events] == ["live_sync.state_changed"]
    assert events[0]["level"] == "warn"
