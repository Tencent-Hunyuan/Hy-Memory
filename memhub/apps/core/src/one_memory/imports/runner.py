import asyncio
import logging
import math
import sqlite3
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from time import monotonic

from one_memory.background_work import in_the_background
from one_memory.diagnostic_log import (
    diagnostic_event,
    diagnostic_exception,
    diagnostics_enabled,
    elapsed_ms,
    record_database_error,
)
from one_memory.imports.access import opaque_conversation_error_id
from one_memory.imports.adapters import (
    ScanCursorTouch,
    ScanEvent,
    ScanIssueCode,
    ScannedEvidence,
    ScanOptions,
    ScanPhase,
    ScanSkip,
    SourceAdapter,
    SourceCursor,
)
from one_memory.imports.jobs import (
    ImportJob,
    ImportJobStore,
    ImportStatus,
    InvalidJobTransitionError,
)
from one_memory.imports.models import EvidenceRecord, materialize_write_input
from one_memory.imports.storage import EvidenceStore, PendingMemoryDispatch
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.service_levels import ServiceLevel, service_level_context

_SKIP_BATCH_SECONDS = 0.1
_SKIP_BATCH_RECORDS = 64

#: Originals and their cursors commit together, this many per transaction.
#: The grant is checked once per batch, so revocation stops within one batch.
EVIDENCE_BATCH_RECORDS = 10
_EVIDENCE_BATCH_SECONDS = 0.2

type _Write = ScannedEvidence | ScanCursorTouch | ScanSkip


def _same_skip_run(last: ScanSkip, event: ScanSkip) -> bool:
    return last.reason_code == event.reason_code and last.cursor.file_key == event.cursor.file_key


def _cursor_advanced(previous: SourceCursor | None, current: SourceCursor) -> bool:
    if previous is not None:
        return previous != current
    kind, separator, value = current.checkpoint.partition(":")
    at_origin = bool(separator) and kind in {"byte", "record", "row", "rowid"} and value == "0"
    return current.read_to_end or not at_origin


def _apply_history_window(job: ImportJob, event: ScanEvent) -> ScanEvent:
    """Advance old records without storing or dispatching them to a model."""
    cutoff = job.history_after
    if job.operation != "history" or cutoff is None or not isinstance(event, ScannedEvidence):
        return event
    observed = event.evidence.created_at
    if observed is None and event.cursor.observed_modified_ns > 0:
        observed = datetime.fromtimestamp(
            event.cursor.observed_modified_ns / 1_000_000_000,
            tz=UTC,
        )
    if observed is None:
        return event
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)
    if observed.astimezone(UTC) >= cutoff.astimezone(UTC):
        return event
    return ScanSkip(
        cursor=event.cursor,
        conversation_id=event.evidence.conversation_id,
        reason_code=ScanIssueCode.HISTORY_WINDOW_EXCLUDED,
        report_issue=False,
    )


@dataclass
class _PendingWrites:
    events: list[_Write] = field(default_factory=list)
    deadline: float = 0.0

    def take(self) -> list[_Write]:
        taken, self.events = self.events, []
        return taken


@dataclass(frozen=True)
class _BatchOutcome:
    stop: ImportJob | None = None
    stored: tuple[EvidenceRecord, ...] = ()
    stable: tuple[PendingMemoryDispatch, ...] = ()
    # File keys whose `evidence_pending_memory_dispatch` rows were only READ,
    # not consumed, so the pending marker survives until the durable dispatch
    # for these records has actually succeeded.
    pending_file_keys: tuple[tuple[str, str, str], ...] = ()


@dataclass
class _PendingSkips:
    last: ScanSkip | None = None
    amount: int = 0
    deadline: float = 0.0

    def matches(self, event: ScanEvent) -> bool:
        previous = self.last
        return (
            previous is not None
            and isinstance(event, ScanSkip)
            and not event.report_issue
            and event.reason_code == previous.reason_code
            and event.cursor.file_key == previous.cursor.file_key
            and event.cursor.file_identity == previous.cursor.file_identity
        )

    def clear(self) -> None:
        self.last = None
        self.amount = 0
        self.deadline = 0.0


@dataclass(frozen=True)
class _RecordOutcome:
    """What recording one event leaves for the loop to do.

    `stop` is the job as found when it is no longer ours to run; `stored` is a
    record whose memory work still needs scheduling.
    """

    stop: ImportJob | None = None
    stored: EvidenceRecord | None = None
    scan_incomplete: bool = False


#: True only while a live-sync import is handing a stored record to its callback.
#:
#: History import and live sync share this callback. Precipitation reads the
#: flag synchronously inside the callback so a synced turn can jump the queue
#: without changing the callback's one-argument shape.
live_sync_arrival: ContextVar[bool] = ContextVar("one_memory_live_sync_arrival", default=False)
_RUNTIME_STARTED_AT = datetime.now(UTC)


class ImportRunner:
    def __init__(
        self,
        *,
        evidence_store: EvidenceStore,
        job_store: ImportJobStore,
        on_evidence: Callable[[EvidenceRecord], object] | None = None,
        on_stable_evidence: Callable[[EvidenceRecord], object] | None = None,
        heartbeat_interval_seconds: float = 10.0,
        sdk_queue: DurableWriteQueue | None = None,
        sdk_authority: Callable[[], bool] | None = None,
    ) -> None:
        if evidence_store.database_path.resolve() != job_store.database_path.resolve():
            raise ValueError("Evidence and import jobs must share one database")
        self.evidence_store = evidence_store
        self.job_store = job_store
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.on_evidence = on_evidence
        self.on_stable_evidence = on_stable_evidence
        self._live_import = False
        self.sdk_queue = sdk_queue
        self.sdk_authority = sdk_authority or (lambda: False)

    async def run(
        self,
        *,
        adapter: SourceAdapter,
        job_id: str,
        max_work_events: int | None = None,
        max_work_seconds: float | None = None,
        should_yield: Callable[[], bool] | None = None,
    ) -> ImportJob:
        if max_work_events is not None and max_work_events < 1:
            raise ValueError("max_work_events must be positive")
        if max_work_seconds is not None and (
            not math.isfinite(max_work_seconds) or max_work_seconds <= 0
        ):
            raise ValueError("max_work_seconds must be positive and finite")
        started = monotonic()
        slice_deadline = started + max_work_seconds if max_work_seconds is not None else None
        job = await in_the_background(self.job_store.require, job_id)
        self._live_import = job.operation == "sync"
        if job.source_id != adapter.descriptor.source_id:
            raise ValueError("Import job source does not match adapter source")
        job, runner_token = await in_the_background(self.job_store.claim, job_id)
        diagnostic_event(
            level="debug",
            component="import.parser",
            event="import.parse.started",
            outcome="started",
            job_id=job_id,
            details={"source_id": job.source_id, "operation_kind": job.operation},
        )
        if job.operation == "sync":
            await in_the_background(
                self.job_store.ensure_live_cursors,
                user_id=job.user_id,
                source_id=job.source_id,
                baselines={},
            )
        cursors = await in_the_background(
            self.job_store.get_source_cursors,
            user_id=job.user_id,
            source_id=job.source_id,
            operation=job.operation,
        )
        heartbeat = asyncio.create_task(self._keep_alive(job_id=job_id, runner_token=runner_token))
        pending = _PendingSkips()
        writes = _PendingWrites()
        stream = adapter.scan(
            ScanOptions(
                cursors=cursors,
                reuse_listing=(
                    job.operation == "history"
                    or (job.operation == "sync" and job.source_id == "workbuddy")
                ),
            )
        )
        slice_cursors = dict(cursors)
        next_event: asyncio.Future[ScanEvent] | None = None
        last_phase: str | None = None
        work_events = 0
        discovery_progress_pending = False
        touched_file_keys: set[str] = set()

        async def flush() -> ImportJob | None:
            return await _settled_work(
                self._flush_skips, pending, job_id=job_id, runner_token=runner_token
            )

        async def flush_writes() -> ImportJob | None:
            if not writes.events:
                return None
            batch = writes.take()
            touched_file_keys.update(event.cursor.file_key for event in batch)
            outcome = await _settled_work(
                self._record_batch,
                batch,
                after=self._dispatch_batch,
                job_id=job_id,
                runner_token=runner_token,
            )
            return outcome.stop

        async def yield_slice() -> ImportJob:
            if (stopped := await flush_writes()) is not None:
                return stopped
            if (stopped := await flush()) is not None:
                return stopped
            return await in_the_background(
                self.job_store.yield_owned,
                job_id,
                runner_token=runner_token,
            )

        try:
            while True:
                if next_event is None:
                    next_event = asyncio.ensure_future(anext(stream))
                deadlines = [
                    deadline
                    for deadline, waiting in (
                        (pending.deadline, pending.last is not None),
                        (writes.deadline, bool(writes.events)),
                    )
                    if waiting
                ]
                if deadlines:
                    ready, _ = await asyncio.wait(
                        (next_event,), timeout=max(0.0, min(deadlines) - monotonic())
                    )
                    if not ready:
                        if (
                            writes.events
                            and monotonic() >= writes.deadline
                            and (stopped := await flush_writes()) is not None
                        ):
                            await self._log_parse_result(stopped, started)
                            return stopped
                        if (
                            pending.last is not None
                            and monotonic() >= pending.deadline
                            and (stopped := await flush()) is not None
                        ):
                            await self._log_parse_result(stopped, started)
                            return stopped
                        continue
                try:
                    event = await asyncio.shield(next_event)
                except StopAsyncIteration:
                    next_event = None
                    break
                next_event = None
                event = _apply_history_window(job, event)
                if event.kind == "progress" and str(event.phase) != last_phase:
                    phase = str(event.phase)
                    diagnostic_event(
                        level="debug",
                        component="import.parser",
                        event="import.phase_changed",
                        outcome="changed",
                        job_id=job_id,
                        state_before=last_phase or "started",
                        state_after=phase,
                        details={"source_id": job.source_id, "phase": phase},
                    )
                    last_phase = phase
                if isinstance(event, ScannedEvidence | ScanCursorTouch) or (
                    isinstance(event, ScanSkip) and not event.report_issue
                ):
                    discovery_progress_pending = False
                    boundary = _cursor_advanced(
                        slice_cursors.get(event.cursor.file_key), event.cursor
                    )
                    if boundary:
                        slice_cursors[event.cursor.file_key] = event.cursor
                    # The count limit bounds persisted Evidence and file
                    # boundaries. Filtered host noise is still bounded by the
                    # wall-clock slice, but must not spend the whole quota
                    # before one useful record can be committed.
                    if not isinstance(event, ScanSkip):
                        work_events += 1
                    if not writes.events:
                        writes.deadline = monotonic() + _EVIDENCE_BATCH_SECONDS
                    writes.events.append(event)
                    if (
                        len(writes.events) >= EVIDENCE_BATCH_RECORDS
                        and (stopped := await flush_writes()) is not None
                    ):
                        await self._log_parse_result(stopped, started)
                        return stopped
                    if boundary and (
                        (should_yield is not None and should_yield())
                        or (max_work_events is not None and work_events >= max_work_events)
                        or (slice_deadline is not None and monotonic() >= slice_deadline)
                    ):
                        return await yield_slice()
                    continue
                if (stopped := await flush_writes()) is not None:
                    await self._log_parse_result(stopped, started)
                    return stopped
                # Recorded in a thread. Every record costs several blocking
                # SQLite calls and an import is thousands of records, so done
                # on the loop this was time nobody was served: the watchdog
                # caught this path holding the loop 2.7s, during which no host
                # could recall anything.
                outcome = await _settled_work(
                    self._record_event,
                    event,
                    after=self._dispatch_committed,
                    job=job,
                    job_id=job_id,
                    runner_token=runner_token,
                    pending=pending,
                )
                if outcome.stop is not None:
                    await self._log_parse_result(outcome.stop, started)
                    return outcome.stop
                if outcome.scan_incomplete:
                    raise ImportScanIncompleteError(
                        "Source discovery exceeded its configured target limit"
                    )
                # Discovery/phase progress has no file cursor. Yielding after
                # only that event restarts the next slice at directory
                # discovery and can starve the first file forever on a large
                # source. Preemption is safe only after a cursor-bearing work
                # event has been committed.
                if event.kind == "progress":
                    follows_discovery = discovery_progress_pending
                    discovery_progress_pending = event.phase is ScanPhase.DISCOVER
                    if (
                        not follows_discovery
                        and event.phase is not ScanPhase.DISCOVER
                        and (
                            (should_yield is not None and should_yield())
                            or (slice_deadline is not None and monotonic() >= slice_deadline)
                        )
                    ):
                        return await yield_slice()
                else:
                    discovery_progress_pending = False
                    work_events += 1
                    if (
                        (should_yield is not None and should_yield())
                        or (max_work_events is not None and work_events >= max_work_events)
                        or (slice_deadline is not None and monotonic() >= slice_deadline)
                    ):
                        return await yield_slice()
            if (stopped := await flush_writes()) is not None:
                await self._log_parse_result(stopped, started)
                return stopped
            if (stopped := await flush()) is not None:
                await self._log_parse_result(stopped, started)
                return stopped

            if self.on_stable_evidence is not None:
                for file_key in touched_file_keys:
                    stable = await in_the_background(
                        self.evidence_store.take_stable_file_dispatches,
                        user_id=job.user_id,
                        source_id=job.source_id,
                        file_key=file_key,
                        # Read the pending markers without consuming them: the
                        # durable submit below must succeed first, or the marker
                        # has to survive for startup replay. Consuming here is
                        # what let a failed/cancelled callback lose the record.
                    )
                    await self._dispatch_stable(stable)
                    await in_the_background(
                        self.evidence_store.consume_stable_file_records,
                        user_id=job.user_id,
                        source_id=job.source_id,
                        file_key=file_key,
                    )
        except asyncio.CancelledError:
            # Pause/cancel waits for the owned tail. Already-revoked/interrupted
            # jobs reject it in the writer; their old cursor remains replayable.
            with suppress(Exception):
                await flush_writes()
            with suppress(Exception):
                await flush()
            current = await in_the_background(self.job_store.require, job_id)
            if current.status is ImportStatus.RUNNING:
                await in_the_background(
                    self.job_store.interrupt_running, job_id, runner_token=runner_token
                )
            raise
        except Exception as error:
            if isinstance(error, sqlite3.Error):
                record_database_error("import.runner", error)
            # A failed tail remains replayable. Preserve the original failure
            # and still mark the owned job failed if this cleanup also fails.
            # A batch that failed was taken before it was written, so what is
            # still pending here was read before the failure and is kept.
            with suppress(Exception):
                await flush_writes()
            with suppress(Exception):
                await flush()
            current = await in_the_background(self.job_store.require, job_id)
            if current.status is not ImportStatus.RUNNING:
                await self._log_parse_result(current, started)
                return current
            try:
                await in_the_background(
                    self.job_store.finish_owned,
                    job_id,
                    runner_token=runner_token,
                    target=ImportStatus.FAILED,
                )
            except InvalidJobTransitionError:
                current = await in_the_background(self.job_store.require, job_id)
                if current.status is not ImportStatus.RUNNING:
                    await self._log_parse_result(current, started)
                    return current
            diagnostic_event(
                level="error",
                component="import.parser",
                event="import.parse.failed",
                outcome="failed",
                job_id=job_id,
                duration_ms=elapsed_ms(started),
                error_code="import_parse_failed",
                **diagnostic_exception(error),
                details={"source_id": job.source_id, "phase": "parse"},
            )
            raise
        finally:
            if next_event is not None:
                next_event.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await next_event
            close = getattr(stream, "aclose", None)
            if close is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await close()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat

        try:
            result = await in_the_background(
                self.job_store.finish_owned,
                job_id,
                runner_token=runner_token,
                target=ImportStatus.COMPLETED,
            )
            await self._log_parse_result(result, started)
            return result
        except InvalidJobTransitionError:
            current = await in_the_background(self.job_store.require, job_id)
            if current.status is not ImportStatus.RUNNING:
                await self._log_parse_result(current, started)
                return current
            raise

    async def _log_parse_result(self, job: ImportJob, started: float) -> None:
        # Best effort: the job's state is already persisted; diagnostics never replace it.
        with suppress(Exception):
            await self._emit_parse_result(job, started)

    async def _emit_parse_result(self, job: ImportJob, started: float) -> None:
        succeeded = job.status is ImportStatus.COMPLETED
        diagnostic_event(
            level="debug" if succeeded else "warn",
            component="import.parser",
            event="import.parse.completed",
            outcome="succeeded" if succeeded else str(job.status),
            job_id=job.job_id,
            duration_ms=elapsed_ms(started),
            state_after=str(job.status),
            error_code=None if succeeded else "import_parse_incomplete",
            details={
                "source_id": job.source_id,
                "operation_kind": job.operation,
                "stored_count": job.stored,
                "revised_count": job.revised,
                "skipped_count": job.skipped,
                "error_count": job.errors,
            },
        )
        if not diagnostics_enabled():
            return
        issues, skipped = await asyncio.gather(
            in_the_background(self.job_store.list_issues, job.job_id),
            in_the_background(self.job_store.skip_reason_counts, job.job_id),
        )
        reasons = Counter(issue.reason for issue in issues)
        reasons.update(skipped)
        for reason, count in sorted(reasons.items()):
            diagnostic_event(
                level="warn",
                component="import.parser",
                event="import.parse.issue_summary",
                outcome="observed",
                job_id=job.job_id,
                error_code=reason,
                details={"source_id": job.source_id, "reason": reason, "count": count},
            )

    def _dispatch_committed(self, outcome: _RecordOutcome) -> None:
        # Runs on the event loop inside the joined task, including when its
        # caller was cancelled during the write. Never dispatch a rolled-back row.
        if self.on_evidence is not None and outcome.stored is not None:
            token = live_sync_arrival.set(self._live_import)
            try:
                self.on_evidence(outcome.stored)
            finally:
                live_sync_arrival.reset(token)

    async def _dispatch_batch(self, outcome: _BatchOutcome) -> None:
        if self.on_evidence is not None:
            for record in outcome.stored:
                token = live_sync_arrival.set(self._live_import)
                try:
                    self.on_evidence(record)
                finally:
                    live_sync_arrival.reset(token)
        # Await the durable dispatch first. If it raises (or the task is
        # cancelled mid-await) this line is never reached, so the pending
        # markers stay in the database and the next startup replays them; only
        # a dispatch that actually succeeded consumes them.
        await self._dispatch_stable(outcome.stable)
        for user_id, source_id, file_key in outcome.pending_file_keys:
            await in_the_background(
                self.evidence_store.consume_stable_file_records,
                user_id=user_id,
                source_id=source_id,
                file_key=file_key,
            )

    async def _dispatch_stable(self, dispatches: tuple[PendingMemoryDispatch, ...]) -> None:
        # `on_stable_evidence` may be a plain callable or an awaitable one. The
        # composition root wires an async closure that submits to the durable
        # intake in a thread; calling it without awaiting builds a coroutine and
        # drops it, so a committed record silently never reaches the queue.
        # Await whatever the callback returns, exactly once, without blocking
        # the loop for a synchronous callback.
        if self.on_stable_evidence is None:
            return
        for dispatch in dispatches:
            created_at = dispatch.record.created_at
            genuinely_live = (
                dispatch.priority == "live"
                and created_at is not None
                and created_at >= _RUNTIME_STARTED_AT
            )
            token = live_sync_arrival.set(genuinely_live)
            try:
                result = self.on_stable_evidence(dispatch.record)
                if asyncio.iscoroutine(result):
                    await result
            finally:
                live_sync_arrival.reset(token)

    def _record_batch(
        self, batch: list[_Write], *, job_id: str, runner_token: str
    ) -> _BatchOutcome:
        """Commit originals and cursors together, after one ownership and grant check.

        An interrupt cannot split an original from its count or checkpoint, and
        taking the writer lock first orders the grant check after revocation.
        """
        stored: list[EvidenceRecord] = []
        stable: list[PendingMemoryDispatch] = []
        pending_keys: list[tuple[str, str, str]] = []
        with self.job_store._connect(writing=True) as connection:
            row = connection.execute(
                "SELECT * FROM import_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = self.job_store._to_job(row)
            if current.status is not ImportStatus.RUNNING or row["runner_token"] != runner_token:
                return _BatchOutcome(stop=current)
            now = datetime.now(UTC).isoformat()
            skips: list[ScanSkip] = []

            def count_skips() -> None:
                if skips:
                    self.job_store._write_skips(
                        connection,
                        row,
                        runner_token=runner_token,
                        reason_code=skips[-1].reason_code,
                        cursor=skips[-1].cursor,
                        amount=len(skips),
                        now=now,
                    )
                    skips.clear()

            for event in batch:
                if isinstance(event, ScanSkip):
                    if skips and not _same_skip_run(skips[-1], event):
                        count_skips()
                    skips.append(event)
                    continue
                count_skips()
                if isinstance(event, ScanCursorTouch):
                    self.job_store._upsert_cursor(connection, row, event.cursor, now)
                    if (
                        self.sdk_authority()
                        and self.sdk_queue is not None
                        and event.cursor.read_to_end
                    ):
                        self.sdk_queue.finalize_sdk_file(
                            user_id=current.user_id,
                            source_id=current.source_id,
                            file_key=event.cursor.file_key,
                            connection=connection,
                        )
                        continue
                    if self.on_stable_evidence is not None and event.cursor.read_to_end:
                        # Read without consuming: the durable submit runs after
                        # this transaction closes, so the pending marker must
                        # survive until that submit succeeds (see
                        # `_dispatch_batch`, which consumes it afterwards).
                        stable.extend(
                            self.evidence_store.take_stable_file_dispatches(
                                user_id=current.user_id,
                                source_id=current.source_id,
                                file_key=event.cursor.file_key,
                                connection=connection,
                            )
                        )
                        pending_keys.append(
                            (current.user_id, current.source_id, event.cursor.file_key)
                        )
                    continue
                if self.sdk_authority() and self.sdk_queue is not None:
                    record = materialize_write_input(event.evidence)
                    self.sdk_queue.stage_sdk_input(
                        record,
                        file_key=event.cursor.file_key,
                        connection=connection,
                        priority="live" if self._live_import else "normal",
                    )
                    self.job_store.record_evidence(
                        job_id,
                        runner_token=runner_token,
                        cursor=event.cursor,
                        was_created=True,
                        was_revised=False,
                        connection=connection,
                    )
                    continue
                result = self.evidence_store.put(event.evidence, connection=connection)
                self.job_store.record_evidence(
                    job_id,
                    runner_token=runner_token,
                    cursor=event.cursor,
                    was_created=result.was_created,
                    was_revised=result.was_revised,
                    connection=connection,
                )
                if result.was_created or result.was_revised:
                    stored.append(result.record)
                    if self.on_stable_evidence is not None:
                        self.evidence_store.stage_memory_dispatch(
                            result.record,
                            file_key=event.cursor.file_key,
                            connection=connection,
                            priority="live" if self._live_import else "normal",
                        )
            count_skips()
        return _BatchOutcome(
            stored=tuple(stored),
            stable=tuple(stable),
            pending_file_keys=tuple(pending_keys),
        )

    def _record_event(
        self,
        event: ScanEvent,
        *,
        job: ImportJob,
        job_id: str,
        runner_token: str,
        pending: _PendingSkips,
    ) -> "_RecordOutcome":
        """Write down one scanned event.

        Synchronous on purpose: the caller runs it in a thread, because every
        call it makes blocks. It reports back rather than acting on the
        findings itself, so that scheduling and raising stay with the caller
        on the loop.
        """
        current = self.job_store.require(job_id)
        if current.status is not ImportStatus.RUNNING:
            pending.clear()
            return _RecordOutcome(stop=current)
        if (
            pending.last is not None
            and (not pending.matches(event) or monotonic() >= pending.deadline)
            and (stopped := self._flush_skips(pending, job_id=job_id, runner_token=runner_token))
            is not None
        ):
            return _RecordOutcome(stop=stopped)
        if isinstance(event, ScanSkip) and not event.report_issue:
            if pending.last is None:
                pending.deadline = monotonic() + _SKIP_BATCH_SECONDS
            pending.last = event
            pending.amount += 1
            if pending.amount >= _SKIP_BATCH_RECORDS:
                return _RecordOutcome(
                    stop=self._flush_skips(pending, job_id=job_id, runner_token=runner_token)
                )
            return _RecordOutcome()
        if event.kind == "progress":
            self.job_store.record_progress(
                job_id,
                runner_token=runner_token,
                phase=event.phase,
                discovered_increment=event.discovered_increment,
            )
            return _RecordOutcome()
        if event.kind == "error":
            self.job_store.record_error(
                job_id,
                runner_token=runner_token,
                reason_code=event.reason_code,
                cursor=event.cursor,
                conversation_id=opaque_conversation_error_id(
                    job.source_id,
                    event.conversation_id,
                ),
            )
            return _RecordOutcome(
                scan_incomplete=event.reason_code is ScanIssueCode.BUDGET_EXCEEDED
            )
        if event.kind == "skip":
            self.job_store.record_skip(
                job_id,
                runner_token=runner_token,
                reason_code=event.reason_code,
                cursor=event.cursor,
                conversation_id=opaque_conversation_error_id(
                    job.source_id,
                    event.conversation_id,
                ),
                report_issue=event.report_issue,
            )
            return _RecordOutcome()

        if not isinstance(event, ScannedEvidence):
            raise TypeError(f"Unexpected scan event kind: {event.kind}")
        batch = self._record_batch([event], job_id=job_id, runner_token=runner_token)
        return _RecordOutcome(stop=batch.stop, stored=batch.stored[0] if batch.stored else None)

    def _flush_skips(
        self, pending: _PendingSkips, *, job_id: str, runner_token: str
    ) -> ImportJob | None:
        last, amount = pending.last, pending.amount
        pending.clear()
        if last is None:
            return None
        return self.job_store._record_skip_batch(
            job_id,
            runner_token=runner_token,
            reason_code=last.reason_code,
            cursor=last.cursor,
            amount=amount,
        )

    async def _keep_alive(self, *, job_id: str, runner_token: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            for attempt in range(8):
                try:
                    # In a thread, like the record-keeping it runs alongside. This
                    # is a read and a write against the same table the import is
                    # writing to, so it waits behind it; the watchdog caught this
                    # line holding the loop while the beat it exists to keep was
                    # itself what stopped the app answering.
                    with service_level_context(ServiceLevel.REALTIME):
                        await in_the_background(
                            partial(self._beat_once, job_id=job_id, runner_token=runner_token)
                        )
                    break
                except (_JobIsNoLongerRunning, InvalidJobTransitionError):
                    return
                except sqlite3.OperationalError as error:
                    if (
                        "locked" in str(error).lower() or "busy" in str(error).lower()
                    ) and attempt < 7:
                        await asyncio.sleep(min(0.05 * (2**attempt), 5.0))
                        continue
                    self._log_heartbeat_failure(job_id, error)
                    break
                except Exception as error:
                    self._log_heartbeat_failure(job_id, error)
                    break

    @staticmethod
    def _log_heartbeat_failure(job_id: str, error: Exception) -> None:
        # A locked database used to kill the beat. The import then
        # looked running forever, and every later sync refused to start.
        # Saying only the type made every one of these unactionable:
        # `OperationalError` is what a lock, a missing table and a full
        # disk all look like from the outside.
        logging.getLogger(__name__).warning(
            "import_heartbeat_failed %s: %s", type(error).__name__, error
        )
        diagnostic_event(
            level="error",
            component="import.runner",
            event="import.heartbeat_failed",
            outcome="failed",
            job_id=job_id,
            error_code="import_heartbeat_failed",
            # `reason` is the listed key for this; see
            # `diagnostic_log._DETAIL_KEYS`. Truncated: a detail value
            # over 256 characters is rejected.
            details={"reason": str(error)[:200]},
            **diagnostic_exception(error),
        )

    def _beat_once(self, *, job_id: str, runner_token: str) -> None:
        """One heartbeat, all of it blocking, so it can be given to a thread."""
        current = self.job_store.require(job_id)
        if current.status is not ImportStatus.RUNNING:
            raise _JobIsNoLongerRunning
        self.job_store.heartbeat_owned(job_id, runner_token=runner_token)


class _JobIsNoLongerRunning(RuntimeError):
    """Told across the thread boundary, where returning early cannot be seen."""


class ImportScanIncompleteError(RuntimeError):
    pass


async def _settled_work[T](
    work: Callable[..., T],
    *args: object,
    after: Callable[[T], object] | None = None,
    **kwargs: object,
) -> T:
    async def complete() -> T:
        result = await in_the_background(work, *args, **kwargs)
        if after is not None:
            # `after` may be a plain callable or an awaitable one (the stable
            # Evidence dispatch awaits the durable intake). Await it so an async
            # hook actually runs instead of returning a dropped coroutine.
            dispatched = after(result)
            if asyncio.iscoroutine(dispatched):
                await dispatched
        return result

    task = asyncio.create_task(complete())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Cancelling an executor future does not stop its SQLite writer. Join it
        # before a control action returns or touches the same pending batch.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        raise
