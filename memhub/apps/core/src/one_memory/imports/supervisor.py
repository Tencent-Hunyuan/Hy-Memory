import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from time import monotonic
from typing import Literal

from one_memory.background_work import in_the_background
from one_memory.diagnostic_log import diagnostic_event, diagnostics_enabled, elapsed_ms
from one_memory.imports.access import SourceAccessError, SourceAccessErrorCode
from one_memory.imports.adapters import SourceAdapter
from one_memory.imports.jobs import (
    ImportJob,
    ImportJobStore,
    ImportStatus,
    InvalidJobTransitionError,
)
from one_memory.imports.models import EvidenceRecord
from one_memory.imports.runner import ImportRunner
from one_memory.imports.storage import EvidenceStore
from one_memory.service_levels import (
    ResourceLimit,
    ServiceLevel,
    ServiceLevelScheduler,
    ServiceResource,
)


class UnknownSourceError(KeyError):
    pass


class ImportSupervisor:
    def __init__(
        self,
        *,
        adapters: Mapping[str, SourceAdapter],
        evidence_store: EvidenceStore,
        job_store: ImportJobStore,
        on_evidence: Callable[[EvidenceRecord], object] | None = None,
        on_stable_evidence: Callable[[EvidenceRecord], object] | None = None,
        max_concurrent_jobs: int = 8,
        max_work_events_per_slice: int = 50,
        max_work_seconds_per_slice: float = 0.2,
        retry_base_seconds: float = 0.25,
        max_history_retries: int = 3,
        service_levels: ServiceLevelScheduler | None = None,
        source_enabled: Callable[[str], bool] | None = None,
        prepare_adapter: Callable[[ImportJob, SourceAdapter], Awaitable[SourceAdapter]]
        | None = None,
    ) -> None:
        if max_work_events_per_slice < 1:
            raise ValueError("max_work_events_per_slice must be positive")
        if max_work_seconds_per_slice <= 0:
            raise ValueError("max_work_seconds_per_slice must be positive")
        if retry_base_seconds < 0 or max_history_retries < 0:
            raise ValueError("history retry settings must not be negative")
        self.adapters = dict(adapters)
        self.evidence_store = evidence_store
        self.job_store = job_store
        self.on_evidence = on_evidence
        self.on_stable_evidence = on_stable_evidence
        self.source_enabled = source_enabled
        self.prepare_adapter = prepare_adapter
        self.max_work_events_per_slice = max_work_events_per_slice
        self.max_work_seconds_per_slice = max_work_seconds_per_slice
        self.retry_base_seconds = retry_base_seconds
        self.max_history_retries = max_history_retries
        self.service_levels = service_levels or ServiceLevelScheduler(
            {
                ServiceResource.IMPORT: ResourceLimit(
                    capacity=max_concurrent_jobs + 1,
                    background_capacity=max_concurrent_jobs,
                )
            },
            interactive_cooldown_seconds=0,
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._drains: dict[str, asyncio.Task[None]] = {}

    def register_adapter(self, adapter: SourceAdapter) -> None:
        self.adapters[adapter.descriptor.source_id] = adapter

    def unregister_adapter(self, source_id: str) -> None:
        self.adapters.pop(source_id, None)

    def active_job_ids(self) -> frozenset[str]:
        return frozenset(job_id for job_id, task in self._tasks.items() if not task.done())

    def create_job(
        self, source_id: str, *, operation: Literal["history", "sync"] = "history"
    ) -> ImportJob:
        if self.source_enabled is not None and not self.source_enabled(source_id):
            raise SourceAccessError(SourceAccessErrorCode.GRANT_REQUIRED)
        return self.job_store.create(user_id="local", source_id=source_id, operation=operation)

    def enqueue(self, job_id: str) -> ImportJob:
        if job_id in self._drains:
            raise InvalidJobTransitionError("import_cleanup_in_progress")
        job = self.job_store.require(job_id)
        if job.source_id not in self.adapters:
            raise UnknownSourceError(job.source_id)
        if self.source_enabled is not None and not self.source_enabled(job.source_id):
            return job
        existing = self._tasks.get(job_id)
        if existing is None or existing.done():
            self._tasks[job_id] = asyncio.create_task(
                self._execute(
                    job_id,
                    source_id=job.source_id,
                    operation=job.operation,
                )
            )
            diagnostic_event(
                level="info",
                component="import.supervisor",
                event="import.queued",
                outcome="queued",
                job_id=job_id,
                details={"source_id": job.source_id, "mode": job.operation},
            )
        return job

    def resume_interrupted(self) -> None:
        # A process restart must not place recoverable work behind every newly
        # pending history. Each resumed runner will yield after one bounded slice.
        for status in (ImportStatus.INTERRUPTED, ImportStatus.PENDING):
            for job in self.job_store.list_jobs(status=status):
                if job.source_id in self.adapters:
                    if self.source_enabled is not None and not self.source_enabled(job.source_id):
                        continue
                    if job.status is not ImportStatus.PENDING:
                        self.job_store.queue_for_resume(job.job_id)
                    self.enqueue(job.job_id)

    def resume(self, job_id: str) -> ImportJob:
        if job_id in self._drains:
            raise InvalidJobTransitionError("import_cleanup_in_progress")
        job = self.job_store.require(job_id)
        if job.status is ImportStatus.RUNNING:
            return job
        if job.status not in {
            ImportStatus.PENDING,
            ImportStatus.PAUSED,
            ImportStatus.INTERRUPTED,
            ImportStatus.FAILED,
        }:
            return job
        queued = self.job_store.queue_for_resume(job_id)
        self.enqueue(job_id)
        return queued

    async def wait(self, job_id: str) -> ImportJob:
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.shield(task)
        return self.job_store.require(job_id)

    async def pause(self, job_id: str) -> ImportJob:
        job = self.job_store.require(job_id)
        if job.status in {ImportStatus.PAUSED, ImportStatus.INTERRUPTED}:
            return job
        await self._cancel_task(job_id)
        current = self.job_store.require(job_id)
        if current.status is ImportStatus.COMPLETED:
            return current
        return self.job_store.transition(job_id, ImportStatus.PAUSED)

    async def cancel(self, job_id: str) -> ImportJob:
        job = self.job_store.require(job_id)
        if job.status is ImportStatus.CANCELLED:
            return job
        await self._cancel_task(job_id)
        current = self.job_store.require(job_id)
        if current.status is ImportStatus.COMPLETED:
            return current
        return self.job_store.transition(job_id, ImportStatus.CANCELLED)

    async def shutdown(self) -> None:
        running = [
            job
            for job in self.job_store.list_jobs(status=ImportStatus.RUNNING)
            if job.job_id in self._tasks
        ]
        for job in running:
            self.job_store.interrupt_running(job.job_id)
        await asyncio.gather(
            *(self._cancel_task(job_id) for job_id in tuple(self._tasks)),
        )

    async def _execute(
        self,
        job_id: str,
        *,
        source_id: str,
        operation: Literal["history", "sync"],
        retry_attempt: int = 0,
    ) -> None:
        started = monotonic()
        failure_type: str | None = None
        diagnostic_event(
            level="info",
            component="import.supervisor",
            event="import.started",
            outcome="started",
            job_id=job_id,
        )
        try:
            while True:
                level = ServiceLevel.REALTIME if operation == "sync" else ServiceLevel.BACKGROUND
                async with self.service_levels.admit(
                    ServiceResource.IMPORT,
                    level,
                    work_id=job_id,
                    scope_key=source_id,
                ):
                    job = await in_the_background(self.job_store.require, job_id)
                    if job.status not in {
                        ImportStatus.PENDING,
                        ImportStatus.PAUSED,
                        ImportStatus.INTERRUPTED,
                        ImportStatus.FAILED,
                    }:
                        return
                    if self.source_enabled is not None and not await in_the_background(
                        self.source_enabled, job.source_id
                    ):
                        return
                    runner = ImportRunner(
                        evidence_store=self.evidence_store,
                        job_store=self.job_store,
                        on_evidence=self.on_evidence,
                        on_stable_evidence=self.on_stable_evidence,
                    )
                    adapter = self.adapters[job.source_id]
                    if self.prepare_adapter is not None:
                        adapter = await self.prepare_adapter(job, adapter)

                    def should_yield(current_level: ServiceLevel = level) -> bool:
                        return self.service_levels.should_yield(
                            ServiceResource.IMPORT,
                            current_level,
                        )

                    result = await runner.run(
                        adapter=adapter,
                        job_id=job_id,
                        max_work_events=self.max_work_events_per_slice,
                        max_work_seconds=self.max_work_seconds_per_slice,
                        should_yield=should_yield,
                    )
                if result.status is not ImportStatus.PENDING:
                    return
                # Re-enter behind work already waiting at this service level.
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            failure_type = "CancelledError"
            raise
        except Exception as error:
            failure_type = type(error).__name__
            # ImportRunner persists FAILED before propagating. The API exposes status and
            # machine-coded record issues; background tasks must not leak local exceptions.
            current = await in_the_background(self.job_store.require, job_id)
            if (
                operation == "history"
                and current.status is ImportStatus.FAILED
                and retry_attempt < self.max_history_retries
                and (
                    self.source_enabled is None
                    or await in_the_background(self.source_enabled, current.source_id)
                )
            ):
                delay = self.retry_base_seconds * (2**retry_attempt)
                diagnostic_event(
                    level="warn",
                    component="import.supervisor",
                    event="import.retry_scheduled",
                    outcome="retrying",
                    job_id=job_id,
                    error_type=failure_type,
                    details={
                        "source_id": source_id,
                        "mode": operation,
                        "count": retry_attempt + 1,
                    },
                )
                if delay:
                    await asyncio.sleep(delay)
                await in_the_background(self.job_store.queue_for_resume, job_id)
                await self._execute(
                    job_id,
                    source_id=source_id,
                    operation=operation,
                    retry_attempt=retry_attempt + 1,
                )
            return
        finally:
            if diagnostics_enabled():
                await self._log_finished(job_id, started, failure_type)

    async def _log_finished(self, job_id: str, started: float, failure_type: str | None) -> None:
        # Best effort: a diagnostic read or a rejected event never replaces the job's outcome.
        with suppress(Exception):
            current = await in_the_background(self.job_store.require, job_id)
            failed = current.status is ImportStatus.FAILED
            diagnostic_event(
                level="error" if failed else "info",
                component="import.supervisor",
                event="import.finished",
                outcome="failed" if failed else str(current.status),
                job_id=job_id,
                duration_ms=elapsed_ms(started),
                state_after=str(current.status),
                error_code="import_failed" if failed else None,
                **({"error_type": failure_type} if failure_type is not None else {}),
                details={
                    "source_id": current.source_id,
                    "mode": current.operation,
                    "count": current.stored,
                    "stored_count": current.stored,
                    "revised_count": current.revised,
                    "skipped_count": current.skipped,
                    "error_count": current.errors,
                },
            )

    async def abandon(self, job_id: str, *, timeout: float) -> bool:
        """Ask a wedged worker to stop.

        True means nothing is still running this job, so another execute may
        start. A worker that ignores cancel stays registered: dropping the
        slot would let the next enqueue scan the same cursor beside it.
        """
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except TimeoutError:
                current = self.job_store.require(job_id)
                if current.status is ImportStatus.RUNNING:
                    self.job_store.interrupt_running(job_id)
                return False
            except asyncio.CancelledError:
                # The worker noticed cancel and exited. A cancel of this
                # caller, such as shutdown, must still propagate.
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling():
                    raise
        current = self.job_store.require(job_id)
        if current.status is ImportStatus.RUNNING:
            self.job_store.interrupt_running(job_id)
        return True

    async def _cancel_task(self, job_id: str) -> None:
        drain = self._drains.get(job_id)
        if drain is not None:
            await asyncio.shield(drain)
            return
        task = self._tasks.get(job_id)
        if task is None:
            return

        async def finish() -> None:
            try:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            finally:
                if task.done() and self._tasks.get(job_id) is task:
                    self._tasks.pop(job_id, None)
                self._drains.pop(job_id, None)

        # Retain the runner until cleanup finishes, and cancel it only once even
        # when pause, cancellation and shutdown concurrently join the same drain.
        drain = asyncio.create_task(finish())
        self._drains[job_id] = drain
        drain.add_done_callback(
            lambda completed: None if completed.cancelled() else completed.exception()
        )
        await asyncio.shield(drain)
