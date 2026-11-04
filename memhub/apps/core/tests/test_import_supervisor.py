import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

import pytest

from one_memory.imports.adapters import (
    ScanEvent,
    ScannedEvidence,
    ScanOptions,
    SourceCapabilities,
    SourceCursor,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.imports.supervisor import ImportSupervisor


def scanned(message_id: str, *, source_id: str = "fixture") -> ScannedEvidence:
    return ScannedEvidence(
        evidence=EvidenceInput(
            source_id=source_id,
            conversation_id="conversation",
            message_id=message_id,
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=message_id,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        ),
        cursor=SourceCursor(
            file_key="a" * 64,
            file_identity="fixture:1",
            checkpoint=f"line:{message_id}",
            observed_size=100,
            observed_modified_ns=1,
        ),
    )


class ControlledAdapter:
    def __init__(
        self,
        events: list[ScanEvent],
        gate: asyncio.Event | None = None,
        source_id: str = "fixture",
    ) -> None:
        self.descriptor = SourceDescriptor(
            source_id=source_id,
            display_name=source_id,
            storage_kinds=frozenset({StorageKind.JSONL}),
            capabilities=SourceCapabilities(
                can_append=True,
                can_observe_edits=False,
                can_observe_deletes=False,
                can_reconcile_complete_set=False,
            ),
        )
        self.events = events
        self.gate = gate

    async def detect(self) -> bool:
        return True

    async def scan(self, _: ScanOptions) -> AsyncIterator[ScanEvent]:
        if self.gate is not None:
            await self.gate.wait()
        for event in self.events:
            yield event


class CursorAwareAdapter(ControlledAdapter):
    async def scan(self, options: ScanOptions) -> AsyncIterator[ScanEvent]:
        checkpoint = options.cursors.get("a" * 64)
        completed = int(checkpoint.checkpoint.removeprefix("line:")) if checkpoint else 0
        for event in self.events[completed:]:
            await asyncio.sleep(0)
            yield event


class FailOnceCursorAwareAdapter(CursorAwareAdapter):
    def __init__(self, events: list[ScanEvent], *, source_id: str = "fixture") -> None:
        super().__init__(events, source_id=source_id)
        self.scans = 0

    async def scan(self, options: ScanOptions) -> AsyncIterator[ScanEvent]:
        self.scans += 1
        async for event in super().scan(options):
            yield event
            if self.scans == 1:
                raise ValueError("transient history reader failure")


def stores(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    return EvidenceStore(database), ImportJobStore(database)


def test_supervisor_starts_all_granted_sources_together(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        gates = {source_id: asyncio.Event() for source_id in ("alpha", "beta", "gamma")}
        supervisor = ImportSupervisor(
            adapters={
                source_id: ControlledAdapter([scanned(f"{source_id}-1")], gate, source_id=source_id)
                for source_id, gate in gates.items()
            },
            evidence_store=evidence_store,
            job_store=job_store,
            max_concurrent_jobs=8,
        )
        jobs = [job_store.create(user_id="local", source_id=source_id) for source_id in gates]
        for job in jobs:
            supervisor.enqueue(job.job_id)
        for _ in range(40):
            if all(job_store.require(job.job_id).status is ImportStatus.RUNNING for job in jobs):
                break
            await asyncio.sleep(0)
        assert {job_store.require(job.job_id).status for job in jobs} == {ImportStatus.RUNNING}
        for gate in gates.values():
            gate.set()
        for job in jobs:
            completed = await supervisor.wait(job.job_id)
            assert completed.status is ImportStatus.COMPLETED

    asyncio.run(scenario())


def test_supervisor_runs_enqueued_job_in_background(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        supervisor = ImportSupervisor(
            adapters={"fixture": ControlledAdapter([scanned("1")])},
            evidence_store=evidence_store,
            job_store=job_store,
        )
        job = job_store.create(user_id="local", source_id="fixture")

        supervisor.enqueue(job.job_id)
        completed = await supervisor.wait(job.job_id)

        assert completed.status is ImportStatus.COMPLETED
        assert evidence_store.count() == 1

    asyncio.run(scenario())


def test_harness_freeze_keeps_pending_job_and_thaw_runs_the_same_row(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        enabled = False
        supervisor = ImportSupervisor(
            adapters={"fixture": ControlledAdapter([scanned("1")])},
            evidence_store=evidence_store,
            job_store=job_store,
            source_enabled=lambda _source_id: enabled,
        )
        job = job_store.create(user_id="local", source_id="fixture")

        supervisor.enqueue(job.job_id)
        await asyncio.sleep(0)
        frozen = job_store.require(job.job_id)
        assert frozen.status is ImportStatus.PENDING
        assert supervisor.active_job_ids() == frozenset()
        assert evidence_store.count() == 0

        enabled = True
        supervisor.enqueue(job.job_id)
        completed = await supervisor.wait(job.job_id)
        assert completed.job_id == job.job_id
        assert completed.status is ImportStatus.COMPLETED
        assert evidence_store.count() == 1

    asyncio.run(scenario())


def test_harness_freeze_does_not_cancel_a_job_already_running(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        gate = asyncio.Event()
        enabled = True
        supervisor = ImportSupervisor(
            adapters={"fixture": ControlledAdapter([scanned("1")], gate)},
            evidence_store=evidence_store,
            job_store=job_store,
            source_enabled=lambda _source_id: enabled,
        )
        job = job_store.create(user_id="local", source_id="fixture")
        supervisor.enqueue(job.job_id)
        for _ in range(40):
            if job_store.require(job.job_id).status is ImportStatus.RUNNING:
                break
            await asyncio.sleep(0)
        assert job_store.require(job.job_id).status is ImportStatus.RUNNING

        enabled = False
        gate.set()
        completed = await supervisor.wait(job.job_id)
        assert completed.status is ImportStatus.COMPLETED
        assert evidence_store.count() == 1

    asyncio.run(scenario())


def test_history_failure_retries_same_job_from_checkpoint(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        adapter = FailOnceCursorAwareAdapter([scanned("1"), scanned("2")])
        supervisor = ImportSupervisor(
            adapters={"fixture": adapter},
            evidence_store=evidence_store,
            job_store=job_store,
            retry_base_seconds=0.01,
        )
        job = supervisor.create_job("fixture")

        supervisor.enqueue(job.job_id)
        completed = await asyncio.wait_for(supervisor.wait(job.job_id), timeout=2)

        assert completed.status is ImportStatus.COMPLETED
        assert completed.stored == 2
        assert evidence_store.count() == 2
        assert adapter.scans == 2

    asyncio.run(scenario())


def test_shutdown_marks_running_job_interrupted_and_resume_finishes(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        gate = asyncio.Event()
        first = ImportSupervisor(
            adapters={"fixture": ControlledAdapter([scanned("1")], gate)},
            evidence_store=evidence_store,
            job_store=job_store,
        )
        job = job_store.create(user_id="local", source_id="fixture")
        first.enqueue(job.job_id)
        async with asyncio.timeout(2):
            while job_store.require(job.job_id).status is not ImportStatus.RUNNING:
                await asyncio.sleep(0.01)

        await first.shutdown()

        assert job_store.require(job.job_id).status is ImportStatus.INTERRUPTED

        second = ImportSupervisor(
            adapters={"fixture": ControlledAdapter([scanned("1")])},
            evidence_store=evidence_store,
            job_store=job_store,
        )
        queued = second.resume(job.job_id)
        assert queued.status is ImportStatus.PENDING
        completed = await second.wait(job.job_id)

        assert completed.status is ImportStatus.COMPLETED
        assert evidence_store.count() == 1

    asyncio.run(scenario())


def test_pending_workbuddy_runs_before_long_codex_history_finishes(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        delivered: list[str] = []
        supervisor = ImportSupervisor(
            adapters={
                "codex": CursorAwareAdapter(
                    [scanned(str(index), source_id="codex") for index in range(1, 258)],
                    source_id="codex",
                ),
                "workbuddy": CursorAwareAdapter(
                    [scanned("1", source_id="workbuddy")], source_id="workbuddy"
                ),
            },
            evidence_store=evidence_store,
            job_store=job_store,
            max_concurrent_jobs=1,
            on_evidence=lambda record: delivered.append(record.source_id),
        )
        codex = supervisor.create_job("codex")
        workbuddy = supervisor.create_job("workbuddy")

        supervisor.enqueue(codex.job_id)
        supervisor.enqueue(workbuddy.job_id)
        completed_workbuddy = await asyncio.wait_for(supervisor.wait(workbuddy.job_id), timeout=2)
        completed_codex = await asyncio.wait_for(supervisor.wait(codex.job_id), timeout=2)

        assert completed_workbuddy.status is ImportStatus.COMPLETED
        assert completed_codex.status is ImportStatus.COMPLETED
        assert delivered.index("workbuddy") < len(delivered) - 1

    asyncio.run(scenario())


def test_startup_recovers_interrupted_codex_before_pending_history(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        delivered: list[str] = []
        workbuddy = job_store.create(user_id="local", source_id="workbuddy")
        codex = job_store.create(user_id="local", source_id="codex")
        _, token = job_store.claim(codex.job_id)
        job_store.interrupt_running(codex.job_id, runner_token=token)
        supervisor = ImportSupervisor(
            adapters={
                "workbuddy": CursorAwareAdapter(
                    [scanned(str(index), source_id="workbuddy") for index in range(1, 5)],
                    source_id="workbuddy",
                ),
                "codex": CursorAwareAdapter([scanned("1", source_id="codex")], source_id="codex"),
            },
            evidence_store=evidence_store,
            job_store=job_store,
            max_concurrent_jobs=1,
            max_work_events_per_slice=1,
            on_evidence=lambda record: delivered.append(record.source_id),
        )

        supervisor.resume_interrupted()
        recovered = await asyncio.wait_for(supervisor.wait(codex.job_id), timeout=2)
        pending = await asyncio.wait_for(supervisor.wait(workbuddy.job_id), timeout=2)

        assert recovered.status is ImportStatus.COMPLETED
        assert pending.status is ImportStatus.COMPLETED
        assert delivered[0] == "codex"

    asyncio.run(scenario())


def test_single_worker_rotates_across_sources(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        delivered: list[str] = []
        source_ids = ("codex", "workbuddy", "cursor")
        supervisor = ImportSupervisor(
            adapters={
                source_id: CursorAwareAdapter(
                    [scanned(str(index), source_id=source_id) for index in range(1, 4)],
                    source_id=source_id,
                )
                for source_id in source_ids
            },
            evidence_store=evidence_store,
            job_store=job_store,
            max_concurrent_jobs=1,
            max_work_events_per_slice=1,
            on_evidence=lambda record: delivered.append(record.source_id),
        )
        jobs = [supervisor.create_job(source_id) for source_id in source_ids]

        for job in jobs:
            supervisor.enqueue(job.job_id)
        for job in jobs:
            completed = await asyncio.wait_for(supervisor.wait(job.job_id), timeout=2)
            assert completed.status is ImportStatus.COMPLETED

        assert delivered[:3] == list(source_ids)

    asyncio.run(scenario())


def test_cancelling_waiter_does_not_cancel_supervisor_owned_job(tmp_path) -> None:
    async def scenario() -> None:
        evidence_store, job_store = stores(tmp_path)
        gate = asyncio.Event()
        supervisor = ImportSupervisor(
            adapters={"fixture": ControlledAdapter([scanned("1")], gate)},
            evidence_store=evidence_store,
            job_store=job_store,
        )
        job = job_store.create(user_id="local", source_id="fixture")
        supervisor.enqueue(job.job_id)
        waiter = asyncio.create_task(supervisor.wait(job.job_id))
        for _ in range(20):
            if job_store.require(job.job_id).status is ImportStatus.RUNNING:
                break
            await asyncio.sleep(0)

        waiter.cancel()
        with suppress(asyncio.CancelledError):
            await waiter

        assert job_store.require(job.job_id).status is ImportStatus.RUNNING
        gate.set()
        completed = await supervisor.wait(job.job_id)
        assert completed.status is ImportStatus.COMPLETED

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", [ImportStatus.COMPLETED, ImportStatus.FAILED])
def test_shutdown_preserves_a_job_that_finished_after_the_running_snapshot(
    tmp_path, monkeypatch, terminal
) -> None:
    async def scenario() -> None:
        evidence, jobs = stores(tmp_path)
        supervisor = ImportSupervisor(adapters={}, evidence_store=evidence, job_store=jobs)
        job = jobs.create(user_id="local", source_id="fixture")
        job, token = jobs.claim(job.job_id)
        stopped = asyncio.Event()

        async def waiting() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        task = asyncio.create_task(waiting())
        supervisor._tasks[job.job_id] = task
        await asyncio.sleep(0)
        original = jobs.find_jobs

        def finish_after_snapshot(*args, **kwargs):
            found = original(*args, **kwargs)
            if tuple(kwargs.get("statuses") or ()) == (ImportStatus.RUNNING,):
                jobs.finish_owned(job.job_id, runner_token=token, target=terminal)
            return found

        monkeypatch.setattr(jobs, "find_jobs", finish_after_snapshot)
        try:
            await supervisor.shutdown()
            assert jobs.require(job.job_id).status is terminal
            assert task.done() and stopped.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_abandon_makes_its_database_calls_off_the_event_loop(tmp_path, monkeypatch) -> None:
    """Live sync abandons from its poll; a writer wait there froze the whole Core."""
    import threading

    async def scenario() -> None:
        evidence, jobs = stores(tmp_path)
        supervisor = ImportSupervisor(adapters={}, evidence_store=evidence, job_store=jobs)
        job = jobs.create(user_id="local", source_id="fixture", operation="sync")
        job, _token = jobs.claim(job.job_id)
        loop_thread = threading.current_thread()
        calls: list[tuple[str, threading.Thread]] = []
        for name in ("require", "interrupt_running"):
            original = getattr(jobs, name)

            def recorded(*args, _name=name, _original=original, **kwargs):
                calls.append((_name, threading.current_thread()))
                return _original(*args, **kwargs)

            monkeypatch.setattr(jobs, name, recorded)

        async def wedged() -> None:
            # Ignores the first cancel, like a runner stuck in a thread.
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)

        task = asyncio.create_task(wedged())
        supervisor._tasks[job.job_id] = task
        await asyncio.sleep(0)
        try:
            assert await supervisor.abandon(job.job_id, timeout=0.01) is False
        finally:
            await asyncio.gather(task, return_exceptions=True)

        assert {name for name, _thread in calls} == {"require", "interrupt_running"}
        assert all(thread is not loop_thread for _name, thread in calls)
        assert jobs.require(job.job_id).status is ImportStatus.INTERRUPTED

    asyncio.run(scenario())
