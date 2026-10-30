import asyncio
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest

from one_memory.imports.adapters import (
    ScanCursorTouch,
    ScanEvent,
    ScanFailure,
    ScanIssueCode,
    ScannedEvidence,
    ScanOptions,
    ScanPhase,
    ScanProgressEvent,
    ScanSkip,
    SourceCapabilities,
    SourceCursor,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.jobs import (
    ImportJobStore,
    ImportStatus,
    InvalidJobTransitionError,
)
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.runner import (
    ImportRunner,
    ImportScanIncompleteError,
    live_sync_arrival,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.retired_authority import RetiredEvidenceStore
from one_memory.target_schema import (
    bootstrap_fresh_management_schema,
    legacy_tables_present,
)


def evidence(message_id: str) -> EvidenceInput:
    return EvidenceInput(
        source_id="fixture",
        conversation_id="conversation",
        message_id=message_id,
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim=f"message {message_id}",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
    )


def cursor(checkpoint: str, *, file_key: str = "a" * 64) -> SourceCursor:
    return SourceCursor(
        file_key=file_key,
        file_identity="fixture:1",
        checkpoint=checkpoint,
        observed_size=100,
        observed_modified_ns=1,
    )


class FixtureAdapter:
    descriptor = SourceDescriptor(
        source_id="fixture",
        display_name="Fixture",
        storage_kinds=frozenset({StorageKind.JSONL}),
        capabilities=SourceCapabilities(
            can_append=True,
            can_observe_edits=True,
            can_observe_deletes=False,
            can_reconcile_complete_set=False,
        ),
    )

    def __init__(
        self,
        events: list[ScanEvent],
        *,
        after_scan: Callable[[], None] | None = None,
    ) -> None:
        self.events = events
        self.after_scan = after_scan
        self.received_cursors: dict[str, SourceCursor] = {}
        self.received_options: ScanOptions | None = None

    async def detect(self) -> bool:
        return True

    async def scan(self, options: ScanOptions) -> AsyncIterator[ScanEvent]:
        self.received_options = options
        self.received_cursors = options.cursors
        for event in self.events:
            await asyncio.sleep(0)
            yield event
        if self.after_scan:
            self.after_scan()


def stores(tmp_path) -> tuple[EvidenceStore, ImportJobStore]:
    database = tmp_path / "one-memory.sqlite3"
    return EvidenceStore(database), ImportJobStore(database)


def test_job_transitions_are_explicit(tmp_path) -> None:
    _, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")

    with pytest.raises(InvalidJobTransitionError):
        job_store.transition(job.job_id, ImportStatus.COMPLETED)


def test_sdk_authority_import_freezes_input_in_job_without_evidence_tables(
    tmp_path,
) -> None:
    database = tmp_path / "one-memory.sqlite3"
    bootstrap_fresh_management_schema(database)
    evidence_store = RetiredEvidenceStore(database)
    job_store = ImportJobStore(database)
    queue = DurableWriteQueue(database, sdk_authority=True)
    job = job_store.create(user_id="local", source_id="fixture")
    assistant = evidence("2").model_copy(
        update={
            "role": "assistant",
            "written_by": WrittenBy.AGENT,
        }
    )
    eof = cursor("line:2").model_copy(update={"read_to_end": True})
    adapter = FixtureAdapter(
        [
            ScannedEvidence(evidence=evidence("1"), cursor=cursor("line:1")),
            ScannedEvidence(evidence=assistant, cursor=cursor("line:2")),
            ScanCursorTouch(cursor=eof),
        ]
    )
    runner = ImportRunner(
        evidence_store=evidence_store,  # type: ignore[arg-type]
        job_store=job_store,
        sdk_queue=queue,
        sdk_authority=lambda: True,
    )

    completed = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert completed.status is ImportStatus.COMPLETED
    assert legacy_tables_present(database) == ()
    with sqlite3.connect(database) as connection:
        state, payload = connection.execute(
            "SELECT state,payload_json FROM memory_write_jobs WHERE state='pending'"
        ).fetchone()
    assert state == "pending"
    assert '"input_records"' in payload
    assert "message 1" in payload
    assert "message 2" in payload


def test_runner_keeps_per_record_errors_and_continues(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    adapter = FixtureAdapter(
        [
            ScannedEvidence(evidence=evidence("1"), cursor=cursor("line:1")),
            ScanFailure(
                cursor=cursor("line:2"),
                conversation_id="broken",
                reason_code=ScanIssueCode.INVALID_JSON,
            ),
            ScannedEvidence(evidence=evidence("3"), cursor=cursor("line:3")),
        ]
    )
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    completed = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert completed.status is ImportStatus.COMPLETED
    assert completed.checkpoint == "line:3"
    assert completed.discovered == 3
    assert completed.emitted == 2
    assert completed.stored == 2
    assert completed.skipped == 0
    assert completed.errors == 1
    assert evidence_store.count() == 2
    errors = job_store.list_issues(job.job_id)
    assert len(errors) == 1
    assert errors[0].kind == "error"
    assert errors[0].conversation_id != "broken"


def test_sync_slices_reuse_the_same_source_listing(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture", operation="sync")
    adapter = FixtureAdapter([])
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert adapter.received_options is not None
    assert adapter.received_options.reuse_listing is False


def test_workbuddy_sync_slices_reuse_the_same_source_listing(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="workbuddy", operation="sync")
    adapter = FixtureAdapter([])
    adapter.descriptor = adapter.descriptor.model_copy(update={"source_id": "workbuddy"})
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert adapter.received_options is not None
    assert adapter.received_options.reuse_listing is True


def test_discovery_budget_exhaustion_fails_incomplete_job(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    adapter = FixtureAdapter([ScanFailure(reason_code=ScanIssueCode.BUDGET_EXCEEDED)])
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    with pytest.raises(ImportScanIncompleteError):
        asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert job_store.require(job.job_id).status is ImportStatus.FAILED


def test_paused_job_resumes_from_its_last_committed_checkpoint(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    _, runner_token = job_store.claim(job.job_id)
    job_store.record_evidence(
        job.job_id,
        runner_token=runner_token,
        cursor=cursor("line:8"),
        was_created=True,
        was_revised=False,
    )
    job_store.transition(job.job_id, ImportStatus.PAUSED)
    adapter = FixtureAdapter([ScannedEvidence(evidence=evidence("9"), cursor=cursor("line:9"))])
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    completed = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert adapter.received_cursors["a" * 64].checkpoint == "line:8"
    assert completed.status is ImportStatus.COMPLETED
    assert completed.checkpoint == "line:9"


def test_preemption_persists_the_event_already_read_from_a_large_listing(tmp_path) -> None:
    """A due slice cannot discard the first event and restart the same file forever."""
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture", operation="sync")
    adapter = FixtureAdapter(
        [
            ScanProgressEvent(phase=ScanPhase.DISCOVER, discovered_increment=2455),
            ScanProgressEvent(phase=ScanPhase.READ),
            ScannedEvidence(evidence=evidence("expanded-1"), cursor=cursor("byte:0")),
            ScannedEvidence(evidence=evidence("expanded-2"), cursor=cursor("byte:0")),
            ScannedEvidence(evidence=evidence("expanded-3"), cursor=cursor("byte:1979")),
            ScanCursorTouch(cursor=cursor("byte:1979").model_copy(update={"read_to_end": True})),
        ]
    )
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    yielded = asyncio.run(
        runner.run(
            adapter=adapter,
            job_id=job.job_id,
            should_yield=lambda: True,
        )
    )

    assert yielded.status is ImportStatus.PENDING
    persisted = job_store.get_source_cursors(
        user_id="local", source_id="fixture", operation="sync"
    )["a" * 64]
    assert persisted.checkpoint == "byte:1979"
    assert evidence_store.count() == 3


def test_running_job_cannot_be_claimed_by_a_second_runner(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    job_store.claim(job.job_id)
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    with pytest.raises(InvalidJobTransitionError):
        asyncio.run(
            runner.run(
                adapter=FixtureAdapter([]),
                job_id=job.job_id,
            )
        )


def test_stale_running_job_is_discoverable_and_recoverable(tmp_path) -> None:
    _, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    job_store.claim(job.job_id)

    recovered = job_store.recover_stale_running(
        heartbeat_before=datetime.now(UTC) + timedelta(seconds=1)
    )

    assert recovered == 1
    assert job_store.list_jobs(status=ImportStatus.INTERRUPTED)[0].job_id == job.job_id


def test_pause_after_last_event_does_not_escape_as_an_error(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    adapter = FixtureAdapter(
        [ScannedEvidence(evidence=evidence("1"), cursor=cursor("line:1"))],
        after_scan=lambda: job_store.transition(job.job_id, ImportStatus.PAUSED),
    )
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    paused = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    # The unwritten batch is dropped with its cursor, so resuming reads it again.
    assert paused.status is ImportStatus.PAUSED
    assert paused.checkpoint is None and evidence_store.count() == 0
    assert job_store.get_source_cursors(user_id="local", source_id="fixture") == {}


def test_skip_is_distinct_from_error_and_advances_its_file_cursor(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    adapter = FixtureAdapter(
        [
            ScanSkip(
                cursor=cursor("line:4", file_key="b" * 64),
                reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED,
            )
        ]
    )
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    completed = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert completed.status is ImportStatus.COMPLETED
    assert completed.discovered == 1
    assert completed.skipped == 1
    assert completed.errors == 0
    assert (
        job_store.get_source_cursors(
            user_id="local",
            source_id="fixture",
        )["b" * 64].checkpoint
        == "line:4"
    )


def test_filtered_records_do_not_exhaust_the_evidence_slice_budget(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    adapter = FixtureAdapter(
        [
            ScanSkip(
                cursor=cursor(f"line:{index}"),
                reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED,
            )
            for index in range(1, 101)
        ]
        + [ScannedEvidence(evidence=evidence("kept"), cursor=cursor("line:101"))]
    )
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    yielded = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id, max_work_events=1))

    assert yielded.status is ImportStatus.PENDING
    assert yielded.stored == 1
    assert yielded.skipped == 100


class _Sluggish:
    """A store whose every call takes a moment, the way a contended one does."""

    def __init__(self, inner: object, delay: float) -> None:
        self._inner = inner
        self._delay = delay

    def __getattr__(self, name: str):
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def slowly(*args, **kwargs):
            time.sleep(self._delay)
            return attribute(*args, **kwargs)

        return slowly


def test_importing_leaves_the_loop_free_to_answer(tmp_path) -> None:
    """An import must not stop the app answering while it runs.

    Every record here costs several synchronous SQLite calls — the job's
    current status, the evidence write, the progress record — and an import is
    thousands of records. Made on the loop, they were made instead of
    answering anyone: the loop watchdog caught this path holding the loop for
    2.7 seconds on the real library, and while it is held no host can recall
    anything and the desktop app cannot even load its own memory list.
    """
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    adapter = FixtureAdapter(
        [
            ScannedEvidence(evidence=evidence(str(index)), cursor=cursor(f"line:{index}"))
            for index in range(1, 7)
        ]
    )
    delay = 0.05
    runner = ImportRunner(
        evidence_store=_Sluggish(evidence_store, delay),
        job_store=_Sluggish(job_store, delay),
    )

    async def import_while_watching_the_loop() -> tuple[float, object]:
        # The first hand-off to a thread in a process pays for importing the
        # pool and starting it — a fifth of a second from cold, on the loop,
        # once. Paid here so that what follows measures the import.
        await asyncio.to_thread(lambda: None)
        longest = 0.0

        async def heartbeat() -> None:
            # The gap between turns of the loop: a task cannot time a stall it
            # was not running for.
            nonlocal longest
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.005)
                now = time.perf_counter()
                longest = max(longest, now - last)
                last = now

        beat = asyncio.create_task(heartbeat())
        try:
            finished = await runner.run(adapter=adapter, job_id=job.job_id)
        finally:
            beat.cancel()
        return longest, finished

    longest, finished = asyncio.run(import_while_watching_the_loop())

    # The import really happened, so this cannot pass by importing nothing.
    assert finished.status is ImportStatus.COMPLETED
    assert finished.stored == 6
    assert evidence_store.count() == 6
    # Six records at three-odd slow calls each; on the loop that is most of a
    # second in a row with nobody served.
    assert longest < delay * 3, f"导入期间循环被堵住 {longest:.3f}s"


def test_job_lifecycle_does_not_run_storage_calls_on_the_event_loop(tmp_path):
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    loop_thread = threading.get_ident()
    calls = []

    class CheckedStore:
        def __getattr__(self, name):
            method = getattr(job_store, name)
            if not callable(method):
                return method

            def checked(*args, **kwargs):
                assert threading.get_ident() != loop_thread, f"{name} blocked the loop"
                calls.append(name)
                return method(*args, **kwargs)

            return checked

    adapter = FixtureAdapter([ScannedEvidence(evidence=evidence("one"), cursor=cursor("line:1"))])
    runner = ImportRunner(evidence_store=evidence_store, job_store=CheckedStore())
    result = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))
    assert result.status is ImportStatus.COMPLETED
    assert {"require", "claim", "get_source_cursors", "finish_owned"}.issubset(calls)


def test_the_heartbeat_leaves_the_loop_free_to_answer(tmp_path) -> None:
    """The beat that keeps a job alive must not be what stops the app.

    It reads the job and then writes to it, against the very table the import
    is writing to, so it waits behind it: the watchdog caught this line
    holding the loop on the real library while the import it was keeping
    alive was the thing it was waiting for.
    """
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    delay = 0.2
    # Claimed first, so the job is really RUNNING and the beat goes on beating.
    # Left PENDING, the first beat sees a job it does not own and returns, and
    # one slow call is not enough to show a stall: written that way, this test
    # passed against the very defect it was written for.
    _, token = job_store.claim(job.job_id)
    # Only the beat is slowed. The record-keeping was moved off the loop
    # already, and leaving it fast is what makes this test about the beat.
    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=job_store,
        heartbeat_interval_seconds=0.01,
    )
    runner.job_store = _Sluggish(job_store, delay)

    async def beat_while_watching_the_loop() -> tuple[float, int]:
        await asyncio.to_thread(lambda: None)
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
        await asyncio.sleep(0.05)
        keeping_alive = asyncio.create_task(
            runner._keep_alive(job_id=job.job_id, runner_token=token)  # noqa: SLF001
        )
        try:
            # Long enough for several beats at the interval set above.
            await asyncio.sleep(0.6)
        finally:
            keeping_alive.cancel()
            beat.cancel()
        return longest, ticks

    longest, ticks = asyncio.run(beat_while_watching_the_loop())

    assert ticks > 1, "心跳没有真的在跑"
    assert longest < delay, f"保活期间循环被堵住 {longest:.3f}s"


# --- stable-evidence dispatch must actually run an awaitable callback ---------
#
# Regression for the EXEC-1914 C2 audit: the composition root wires an *async*
# `on_stable_evidence` that submits committed Evidence to the durable intake.
# `_dispatch_stable` used to call it synchronously, so the callback returned a
# coroutine that was dropped and the durable row was never created on the live
# import path. These tests exercise the runner, not just the callback's identity.


def test_dispatch_stable_awaits_an_async_callback_exactly_once(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    seen: list[str] = []

    async def on_stable_evidence(record) -> None:
        # Yield once: a fire-and-forget create_task would let the runner return
        # before this body ran, so the count would be 0 at assert time.
        await asyncio.sleep(0)
        seen.append(record.message_id)

    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=job_store,
        on_stable_evidence=on_stable_evidence,
    )
    adapter = FixtureAdapter([ScannedEvidence(evidence=evidence("1"), cursor=cursor("line:1"))])

    completed = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert completed.status is ImportStatus.COMPLETED
    assert seen == ["1"], "async stable-evidence callback did not run in order"


def test_dispatch_stable_still_accepts_a_sync_callback(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    seen: list[str] = []

    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=job_store,
        on_stable_evidence=lambda record: seen.append(record.message_id),
    )
    adapter = FixtureAdapter([ScannedEvidence(evidence=evidence("9"), cursor=cursor("line:9"))])

    completed = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert completed.status is ImportStatus.COMPLETED
    assert seen == ["9"], "plain callable callback must keep working unchanged"


def test_dispatch_stable_sets_the_live_sync_context_for_the_callback(
    tmp_path,
) -> None:
    evidence_store, job_store = stores(tmp_path)
    # A `sync` operation marks this as live arrival, which is the case the
    # ContextVar exists to flag for the stable-evidence callback.
    job = job_store.create(user_id="local", source_id="fixture", operation="sync")
    observed: list[bool] = []

    async def on_stable_evidence(_record) -> None:
        observed.append(live_sync_arrival.get())

    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=job_store,
        on_stable_evidence=on_stable_evidence,
    )
    recent = evidence("2").model_copy(update={"created_at": datetime.now(UTC)})
    adapter = FixtureAdapter([ScannedEvidence(evidence=recent, cursor=cursor("line:2"))])

    completed = asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    assert completed.status is ImportStatus.COMPLETED
    assert observed == [True], "the callback must run inside the arrival context"
    assert live_sync_arrival.get() is False, "the context token must be reset"


def test_sync_eof_preserves_each_pending_conversations_original_priority(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    file_key = "a" * 64
    normal = evidence_store.put(
        evidence("history").model_copy(update={"conversation_id": "history"})
    ).record
    live = evidence_store.put(
        evidence("live").model_copy(
            update={"conversation_id": "live", "created_at": datetime.now(UTC)}
        )
    ).record
    with evidence_store._connect(writing=True) as connection:
        evidence_store.stage_memory_dispatch(
            normal,
            file_key=file_key,
            connection=connection,
            priority="normal",
        )
        evidence_store.stage_memory_dispatch(
            live,
            file_key=file_key,
            connection=connection,
            priority="live",
        )
    observed: dict[str, bool] = {}

    async def on_stable_evidence(record) -> None:
        observed[record.conversation_id] = live_sync_arrival.get()

    job = job_store.create(user_id="local", source_id="fixture", operation="sync")
    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=job_store,
        on_stable_evidence=on_stable_evidence,
    )
    end = cursor("line:2").model_copy(update={"read_to_end": True})
    completed = asyncio.run(
        runner.run(adapter=FixtureAdapter([ScanCursorTouch(cursor=end)]), job_id=job.job_id)
    )

    assert completed.status is ImportStatus.COMPLETED
    assert observed == {"history": False, "live": True}
    assert evidence_store.take_all_stable_records() == ()


def test_dispatch_stable_propagates_a_raising_async_callback(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")

    async def on_stable_evidence(_record) -> None:
        raise RuntimeError("intake_unavailable")

    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=job_store,
        on_stable_evidence=on_stable_evidence,
    )
    adapter = FixtureAdapter([ScannedEvidence(evidence=evidence("3"), cursor=cursor("line:3"))])

    with pytest.raises(RuntimeError, match="intake_unavailable"):
        asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))


# --- P0: a failed/cancelled dispatch must leave a replayable pending marker ----
#
# `take_stable_file_records` used to delete `evidence_pending_memory_dispatch`
# while reading, but the durable submit runs afterwards. A callback exception or
# a cancellation mid-await then left the Evidence committed and the cursor
# advanced with neither a durable row nor a pending marker: the record was lost
# and the next scan would not re-emit it. These tests pin that the pending
# marker survives until the durable dispatch actually succeeds.


def _pending_markers(database) -> list[tuple[str, str]]:
    with sqlite3.connect(database) as connection:
        return [
            (str(source_id), str(evidence_id))
            for source_id, evidence_id in connection.execute(
                "SELECT source_id, evidence_id "
                "FROM evidence_pending_memory_dispatch ORDER BY evidence_id"
            ).fetchall()
        ]


def _stable_cursor(checkpoint: str) -> SourceCursor:
    # `read_to_end` is what makes the runner drain the file's pending markers.
    return SourceCursor(
        file_key="a" * 64,
        file_identity="fixture:1",
        checkpoint=checkpoint,
        observed_size=100,
        observed_modified_ns=1,
        read_to_end=True,
    )


def test_failed_callback_keeps_a_replayable_pending_marker(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")

    async def on_stable_evidence(_record) -> None:
        raise RuntimeError("intake_unavailable")

    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=job_store,
        on_stable_evidence=on_stable_evidence,
    )
    adapter = FixtureAdapter(
        [ScannedEvidence(evidence=evidence("f1"), cursor=_stable_cursor("line:1"))]
    )

    with pytest.raises(RuntimeError, match="intake_unavailable"):
        asyncio.run(runner.run(adapter=adapter, job_id=job.job_id))

    # Evidence committed, but the durable submit never happened, so the pending
    # marker must survive for the next startup to replay.
    assert evidence_store.count() == 1
    assert len(_pending_markers(evidence_store.database_path)) == 1


def test_cancel_during_callback_keeps_a_replayable_pending_marker(tmp_path) -> None:
    evidence_store, job_store = stores(tmp_path)
    job = job_store.create(user_id="local", source_id="fixture")
    entered = asyncio.Event()

    async def on_stable_evidence(_record) -> None:
        entered.set()
        await asyncio.sleep(3600)

    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=job_store,
        on_stable_evidence=on_stable_evidence,
    )
    adapter = FixtureAdapter(
        [ScannedEvidence(evidence=evidence("c1"), cursor=_stable_cursor("line:1"))]
    )

    async def scenario() -> None:
        task = asyncio.create_task(runner.run(adapter=adapter, job_id=job.job_id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert evidence_store.count() == 1
    assert len(_pending_markers(evidence_store.database_path)) == 1


def said(message_id: str, role: str, text: str) -> EvidenceInput:
    writer = {"user": WrittenBy.PERSON, "assistant": WrittenBy.AGENT, "tool": WrittenBy.TOOL}
    return evidence(message_id).model_copy(
        update={"role": role, "written_by": writer[role], "verbatim": text}
    )


def _sdk_runner(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    bootstrap_fresh_management_schema(database)
    job_store = ImportJobStore(database)
    queue = DurableWriteQueue(database, sdk_authority=True)
    runner = ImportRunner(
        evidence_store=RetiredEvidenceStore(database),  # type: ignore[arg-type]
        job_store=job_store,
        sdk_queue=queue,
        sdk_authority=lambda: True,
    )
    return database, job_store, queue, runner


def _read(runner, job_store, parts, *, operation: str, first_line: int = 1):
    events: list[ScanEvent] = [
        ScannedEvidence(evidence=part, cursor=cursor(f"line:{first_line + index}"))
        for index, part in enumerate(parts)
    ]
    last = f"line:{first_line + len(parts) - 1}"
    events.append(ScanCursorTouch(cursor=cursor(last).model_copy(update={"read_to_end": True})))
    job = job_store.create(user_id="local", source_id="fixture", operation=operation)
    asyncio.run(runner.run(adapter=FixtureAdapter(events), job_id=job.job_id))


def _jobs(database) -> list[tuple[str, list[str]]]:
    import json

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT state,payload_json FROM memory_write_jobs ORDER BY queue_seq"
        ).fetchall()
    return [
        (state, [item["role"] for item in json.loads(payload)["input_records"]])
        for state, payload in rows
    ]


def _turns(count: int, *, start: int = 0) -> list[EvidenceInput]:
    return [
        said(f"{role[0]}{index}", role, f"{role} {index}")
        for index in range(start, start + count)
        for role in ("user", "assistant")
    ]


def test_history_import_writes_five_turn_batches_and_flushes_the_rest_at_eof(tmp_path):
    """A first import used to be one giant job per conversation."""
    database, job_store, _queue, runner = _sdk_runner(tmp_path)

    _read(runner, job_store, _turns(7), operation="history")

    assert _jobs(database) == [
        ("pending", ["user", "assistant"] * 5),
        ("pending", ["user", "assistant"] * 2),
    ]


def test_live_polling_carries_an_open_turn_and_a_tool_only_increment(tmp_path):
    """A transcript read mid-turn used to give tool-only jobs (16 unknown)."""
    database, job_store, queue, runner = _sdk_runner(tmp_path)

    _read(runner, job_store, _turns(2), operation="sync")
    assert _jobs(database) == [("assembling", ["user", "assistant"] * 2)]

    tools = [said("t1", "tool", "ls"), said("t2", "tool", "3 passed")]
    _read(runner, job_store, tools, operation="sync", first_line=5)
    assert _jobs(database) == [
        ("assembling", ["user", "assistant", "user", "assistant", "tool", "tool"])
    ]

    # Ten quiet minutes: the open turn is complete and the short batch goes.
    assert queue.flush_idle_assemblies(now=datetime.now(UTC) + timedelta(minutes=9)) == ()
    flushed = queue.flush_idle_assemblies(now=datetime.now(UTC) + timedelta(minutes=11))
    assert len(flushed) == 1
    assert _jobs(database) == [
        ("pending", ["user", "assistant", "user", "assistant", "tool", "tool"])
    ]


def test_live_polling_writes_five_complete_turns_and_keeps_the_open_one(tmp_path):
    database, job_store, queue, runner = _sdk_runner(tmp_path)

    _read(runner, job_store, _turns(5), operation="sync")
    assert [state for state, _ in _jobs(database)] == ["assembling"]

    _read(runner, job_store, [said("u5", "user", "user 5")], operation="sync", first_line=11)
    assert _jobs(database) == [
        ("assembling", ["user"]),
        ("pending", ["user", "assistant"] * 5),
    ]


def test_a_reread_increment_creates_no_second_job(tmp_path):
    database, job_store, queue, runner = _sdk_runner(tmp_path)

    _read(runner, job_store, _turns(6), operation="sync")
    _read(runner, job_store, _turns(6), operation="sync")
    queue.flush_idle_assemblies(now=datetime.now(UTC) + timedelta(minutes=11))
    _read(runner, job_store, _turns(6), operation="sync")
    queue.flush_idle_assemblies(now=datetime.now(UTC) + timedelta(minutes=11))

    assert _jobs(database) == [
        ("pending", ["user", "assistant"] * 5),
        ("pending", ["user", "assistant"]),
    ]


def test_history_records_with_no_user_message_are_dropped_at_eof(tmp_path):
    database, job_store, _queue, runner = _sdk_runner(tmp_path)

    _read(
        runner,
        job_store,
        [said("t1", "tool", "ls"), said("a1", "assistant", "ok")],
        operation="history",
    )

    assert _jobs(database) == []
