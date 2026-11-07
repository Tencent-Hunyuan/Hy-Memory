"""History imports must reach the write queue, not stop at `assembling`.

`imports/runner.py` only calls `finalize_sdk_file()` from the
`ScanCursorTouch` branch, and only when `cursor.read_to_end` is true. An
adapter that never emits a touch therefore leaves every staged record in
`state='assembling'` forever: `flush_idle_assemblies()` covers only
`priority='live'`, and history is staged as `normal`.

These tests drive the real `ImportRunner` with each adapter's own event
stream, so a missing touch fails here instead of in a user's empty library.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from one_memory.database_access import main_database
from one_memory.imports.adapters import ScanEvent, ScanOptions, ScanCursorTouch
from one_memory.imports.jobs import ImportJobStore
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.runner import ImportRunner
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.durable_queue import DurableWriteQueue


def _evidence(source_id: str, message_id: str) -> EvidenceInput:
    return EvidenceInput(
        source_id=source_id,
        conversation_id="conversation",
        message_id=message_id,
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim=f"message {message_id}",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
    )


class _Adapter:
    """A harness-shaped adapter: evidence first, then one end-of-file touch."""

    descriptor = type("D", (), {"source_id": "hermes"})()

    def __init__(self, messages: list[str], *, touch: bool) -> None:
        self.messages = messages
        self.touch = touch

    async def scan(self, options: ScanOptions):  # noqa: ARG002 - protocol shape
        from one_memory.imports.adapters import ScannedEvidence, SourceCursor

        cursor = SourceCursor(
            file_key="a" * 64,
            file_identity="hermes:1",
            checkpoint="byte:100",
            observed_size=100,
            observed_modified_ns=1,
        )
        for message_id in self.messages:
            yield ScannedEvidence(evidence=_evidence("hermes", message_id), cursor=cursor)
        if self.touch:
            yield ScanCursorTouch(cursor=cursor.model_copy(update={"read_to_end": True}))


def _queue_states(db: Path) -> list[tuple[str, str]]:
    with main_database(db).read() as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT state, priority FROM memory_write_jobs ORDER BY queue_seq"
        ).fetchall()
    return [(str(row["state"]), str(row["priority"])) for row in rows]


def _run(tmp_path: Path, *, touch: bool):
    db = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(db)
    jobs = ImportJobStore(db)
    queue = DurableWriteQueue(db, evidence=evidence_store)
    job = jobs.create(user_id="local", source_id="hermes", operation="history")
    runner = ImportRunner(
        evidence_store=evidence_store,
        job_store=jobs,
        sdk_queue=queue,
        sdk_authority=lambda: True,
    )
    result = asyncio.run(
        runner.run(adapter=_Adapter(["m1", "m2", "m3", "m4"], touch=touch), job_id=job.job_id)
    )
    return result, queue, db


def test_an_end_of_file_touch_turns_staged_records_into_write_jobs(tmp_path: Path) -> None:
    """The guard: with a touch the records become a claimable job."""
    result, _, db = _run(tmp_path, touch=True)

    assert result.status.value == "completed"
    assert _queue_states(db) == [("pending", "normal")]


def test_without_a_touch_history_stays_in_assembling(tmp_path: Path) -> None:
    """This is the defect the adapters are being fixed for.

    Without the fix, `hermes` and `opencode` never emit the touch, so a
    completed history job leaves its records here and the idle flush -- which
    only looks at `priority='live'` -- never writes them.
    """
    result, queue, db = _run(tmp_path, touch=False)

    assert result.status.value == "completed"
    assert _queue_states(db) == [("assembling", "normal")]

    flushed = queue.flush_idle_assemblies(now=datetime.now(UTC) + timedelta(days=30))
    assert flushed == ()
    assert _queue_states(db) == [("assembling", "normal")]


@pytest.mark.parametrize("module", ["hermes", "opencode"])
def test_history_adapters_emit_a_cursor_touch(module: str) -> None:
    """Every adapter that can read history must end a file with a touch.

    Checked at the source level so a new adapter that forgets it fails here,
    not in a user's library. `common.py` is the shared implementation the
    JSONL adapters inherit from.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "one_memory"
        / "imports"
        / "sources"
        / f"{module}.py"
    ).read_text(encoding="utf-8")

    assert "ScanCursorTouch(" in source, f"{module} never yields a ScanCursorTouch"
