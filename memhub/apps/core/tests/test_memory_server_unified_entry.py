"""The adapter that makes Evidence-first intake mechanical for every entry."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.durable_queue import (
    DurableWriteDrain,
    DurableWriteQueue,
    execute_job,
)
from one_memory.memory_server.unified_entry import (
    UnifiedWriteIntake,
)


def _record(
    store: EvidenceStore,
    *,
    conversation_id: str = "c1",
    message_id: str = "m1",
    verbatim: str = "durable intake",
):
    return store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id=conversation_id,
            message_id=message_id,
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=datetime.now(UTC),
            verbatim=verbatim,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record


def test_evidence_submission_creates_one_durable_row(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    record = _record(store)
    intake = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store))

    receipt = intake.submit_evidence(record, first_round=1, last_round=1)

    assert receipt.enqueued is True
    assert receipt.evidence_id == record.evidence_id
    assert receipt.request_id == f"entry:{record.evidence_id}"
    assert intake.queue.get(receipt.request_id) is not None
    with sqlite3.connect(path) as connection:
        event_at, quality = connection.execute(
            "SELECT event_at,time_quality FROM memory_write_jobs WHERE job_id=?",
            (receipt.record.job_id,),
        ).fetchone()
    assert datetime.fromisoformat(event_at) == record.created_at
    assert quality == "exact"


def test_cursor_turn_timestamp_orders_job_without_reordering_evidence(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    file_mtime = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)
    turn_time = datetime(2026, 10, 16, 0, 24, tzinfo=UTC)
    record = store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="cursor-session",
            message_id="cursor-user",
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=file_mtime,
            verbatim="timestamped Cursor turn",
            origin_meta={
                EvidenceOrigin.FILE: OriginMetadata(
                    raw_meta={
                        "time_basis": "file_mtime",
                        "turn_event_at": turn_time.isoformat(),
                    }
                )
            },
        )
    ).record
    intake = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store))

    receipt = intake.submit_evidence(record, first_round=1, last_round=1)

    with sqlite3.connect(path) as connection:
        event_at, quality = connection.execute(
            "SELECT event_at,time_quality FROM memory_write_jobs WHERE job_id=?",
            (receipt.record.job_id,),
        ).fetchone()
    assert record.created_at == file_mtime
    assert datetime.fromisoformat(event_at) == turn_time
    assert quality == "exact"


def test_a_long_branch_conversation_id_still_enqueues(tmp_path) -> None:
    """A `chat_exports` branch id can exceed the 128-char session contract.

    The branch id is `f"{conversation_id}:branch:{sha256[:16]}"` and the base
    conversation id may be up to 300 chars, so the raw id cannot be passed as
    ``internal_session_id`` (max 128). Before the shared derivation existed the
    Pydantic bound rejected the whole history import (`import_parse_failed`).
    The value that already fits must keep its exact identity; only the
    overlong value is derived, deterministically for (source, conversation).
    """
    from one_memory.memory_server.contracts import (
        INTERNAL_SESSION_ID_MAX,
        internal_session_id_for,
    )

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    branch_id = "generic-export_conversation_" + "a" * 100 + ":branch:" + "b" * 16
    assert len(branch_id) > INTERNAL_SESSION_ID_MAX

    record = _record(store, conversation_id=branch_id)
    intake = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store))

    receipt = intake.submit_evidence(record, first_round=1, last_round=1)

    assert receipt.enqueued is True
    stored = intake.queue.get(receipt.request_id)
    assert stored is not None
    # Enqueued under the derived, contract-sized session key.
    assert internal_session_id_for(branch_id, "cursor") == internal_session_id_for(
        branch_id, "cursor"
    )
    assert len(internal_session_id_for(branch_id, "cursor")) <= INTERNAL_SESSION_ID_MAX
    # The evidence row keeps the full original identity.
    assert store.get(record.evidence_id).conversation_id == branch_id
    # A short id is preserved byte-for-byte (no silent identity rewrite).
    assert internal_session_id_for("short-conv", "cursor") == "short-conv"


def test_long_conversation_id_is_identical_across_both_intake_paths(tmp_path) -> None:
    """The intake adapter and the Evidence-first builder must agree exactly."""
    from one_memory.memory_server.submit import build_request_from_evidence

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    branch_id = "generic-export_conversation_" + "a" * 100 + ":branch:" + "b" * 16
    record = _record(store, conversation_id=branch_id)

    via_adapter = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store)).submit_evidence(
        record, first_round=1, last_round=1
    )
    via_builder = build_request_from_evidence(
        [record.evidence_id],
        evidence=store,
        first_round=1,
        last_round=1,
        idempotency_key=f"entry:{record.evidence_id}",
    )

    assert via_builder.internal_session_id == via_builder.batch_identity.internal_session_id
    assert via_builder.request_key == f"entry:{record.evidence_id}"
    # Same conversation -> same session key / batch id from both entry points.
    with sqlite3.connect(path) as connection:
        session_key = connection.execute(
            "SELECT session_key FROM memory_write_jobs WHERE idempotency_key=?",
            (via_adapter.request_id,),
        ).fetchone()[0]
    assert session_key == via_builder.internal_session_id


def test_replaying_the_same_evidence_does_not_create_a_second_row(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    record = _record(store)
    intake = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store))

    first = intake.submit_evidence(record, first_round=1, last_round=1)
    replay = intake.submit_evidence(record, first_round=1, last_round=1)

    assert first.record.job_id == replay.record.job_id
    assert replay.enqueued is False


def test_rewound_harness_revision_appends_a_job_with_immutable_input(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    intake = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store))
    old = _record(store, verbatim="old branch")
    old_job = intake.submit_evidence(old, first_round=1, last_round=1)

    revised = _record(store, verbatim="new branch")
    new_job = intake.submit_evidence(revised, first_round=1, last_round=1)

    assert revised.evidence_id == old.evidence_id
    assert revised.revision == old.revision + 1
    assert new_job.record.job_id != old_job.record.job_id
    assert intake.queue.payload_for(old_job.record.job_id).input_records[0].verbatim == "old branch"
    assert intake.queue.payload_for(new_job.record.job_id).input_records[0].verbatim == "new branch"
    assert intake.queue.unsubmitted_evidence_ids() == ()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_write_jobs").fetchone()[0] == 2


def test_revision_committed_before_job_crash_is_discovered_for_reconcile(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    queue = DurableWriteQueue(path, evidence=store)
    intake = UnifiedWriteIntake(queue)
    old = _record(store, verbatim="old branch")
    intake.submit_evidence(old, first_round=1, last_round=1)

    revised = _record(store, verbatim="new branch")

    assert revised.evidence_id == old.evidence_id
    assert revised.revision == old.revision + 1
    assert queue.unsubmitted_evidence_ids() == (revised.evidence_id,)


def test_late_old_revision_snapshot_does_not_cover_the_current_revision(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    queue = DurableWriteQueue(path, evidence=store)
    intake = UnifiedWriteIntake(queue)
    old = _record(store, verbatim="old branch")
    revised = _record(store, verbatim="new branch")

    intake.submit_evidence(old, first_round=1, last_round=1)

    assert revised.evidence_id == old.evidence_id
    assert revised.revision == old.revision + 1
    assert queue.unsubmitted_evidence_ids() == (revised.evidence_id,)


def test_old_branch_job_executes_its_frozen_revision_after_harness_rewind(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    queue = DurableWriteQueue(path, evidence=store)
    intake = UnifiedWriteIntake(queue)
    old = _record(store, verbatim="old branch")
    old_job = intake.submit_evidence(old, first_round=1, last_round=1)
    revised = _record(store, verbatim="new branch")
    intake.submit_evidence(revised, first_round=1, last_round=1)
    drain = DurableWriteDrain(queue, execute=lambda _job_id: asyncio.sleep(0), owner="frozen")
    assert drain.claim(limit=1) == (old_job.record.job_id,)

    captured = []

    class Supervisor:
        async def process_sdk_job(
            self,
            records,
            *,
            request_id,
            before_hy_invocation,
        ):
            assert request_id == old_job.request_id
            captured.extend(records)
            await before_hy_invocation()
            return SimpleNamespace(raw_ids=(), memory_ids=())

    asyncio.run(
        execute_job(
            old_job.record.job_id,
            queue=queue,
            supervisor=Supervisor(),
            evidence=store,
            owner="frozen",
        )
    )

    assert [record.verbatim for record in captured] == ["old branch"]
    assert queue.get(old_job.request_id).state == "completed"


def test_reopening_reads_an_old_id_only_job_without_rewriting_it(tmp_path) -> None:
    from one_memory.memory_server.contracts import BatchIdentity, WriteRequest

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    record = _record(store)
    queue = DurableWriteQueue(path, evidence=store)
    legacy = queue.submit(
        WriteRequest(
            internal_session_id=record.conversation_id,
            source_id=record.source_id,
            batch_identity=BatchIdentity(
                internal_session_id=record.conversation_id,
                first_round=1,
                last_round=1,
            ),
            evidence_ids=[record.evidence_id],
            idempotency_key="legacy-id-only",
        )
    )
    with sqlite3.connect(path) as connection, connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM memory_write_jobs WHERE job_id=?",
                (legacy.job_id,),
            ).fetchone()[0]
        )
        payload.pop("input_records", None)
        connection.execute(
            "UPDATE memory_write_jobs SET payload_json=? WHERE job_id=?",
            (json.dumps(payload, sort_keys=True), legacy.job_id),
        )
    assert queue.payload_for(legacy.job_id).input_records == ()

    reopened = DurableWriteQueue(path, evidence=store)

    assert reopened.payload_for(legacy.job_id).input_records == ()
    with sqlite3.connect(path) as connection:
        persisted = json.loads(
            connection.execute(
                "SELECT payload_json FROM memory_write_jobs WHERE job_id=?",
                (legacy.job_id,),
            ).fetchone()[0]
        )
    assert "input_records" not in persisted


def test_committed_evidence_without_submit_is_the_red_state(tmp_path) -> None:
    """The exact crash window the C0 wiring has to close.

    Evidence is committed first; before the adapter/queue submit there is no
    ``memory_write_jobs`` row.  A restart must therefore run the same intake
    over the committed Evidence.  This test fixes that failure state so a
    future callback that only calls the supervisor cannot look healthy.
    """
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    record = _record(store)
    # The production queue is constructed during startup; initialize it before
    # the callback would have submitted, so the red state is a missing row
    # rather than a missing table.
    queue = DurableWriteQueue(path, evidence=store)

    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM memory_write_jobs").fetchone()[0] == 0

    # The repaired path: the same committed record is submitted once.
    intake = UnifiedWriteIntake(queue)
    receipt = intake.submit_evidence(record, first_round=1, last_round=1)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_write_jobs").fetchone()[0] == 1
        assert (
            connection.execute("SELECT idempotency_key FROM memory_write_jobs").fetchone()[0]
            == f"entry:{record.evidence_id}"
        )
    assert receipt.enqueued is True


def test_reopening_the_queue_keeps_the_intake_row_and_does_not_duplicate_it(tmp_path) -> None:
    """Restart is a reopen, not a new write identity."""
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    record = _record(store)
    intake = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store))
    first = intake.submit_evidence(record, first_round=1, last_round=1)

    reopened = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store))
    replay = reopened.submit_evidence(record, first_round=1, last_round=1)

    assert replay.record.job_id == first.record.job_id
    assert replay.enqueued is False
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_write_jobs").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("first_round", "last_round", "priority"),
    [(0, 1, "normal"), (2, 1, "normal"), (1, 1, "high")],
)
def test_invalid_batch_identity_is_refused_before_the_queue(
    tmp_path, first_round: int, last_round: int, priority: str
) -> None:
    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    record = _record(store)
    intake = UnifiedWriteIntake(DurableWriteQueue(path, evidence=store))

    with pytest.raises(ValueError):
        intake.submit_evidence(
            record,
            first_round=first_round,
            last_round=last_round,
            priority=priority,
        )
    assert intake.queue.unknown_jobs(limit=10) == ()
