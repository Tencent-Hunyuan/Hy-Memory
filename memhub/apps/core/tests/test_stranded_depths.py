"""`stranded_running` must count rows a worker no longer holds.

On the SDK data plane `active_depths()` returns the same number twice -- the
count of `running` rows -- so the legacy `running - in_flight_count` difference
is always zero and `memory-processing` reported a healthy queue however many
workers had died. `stranded_depths()` counts the rows whose lease is gone,
which is what the field's contract promises.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from one_memory.database_access import main_database
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.sdk_memory_runtime import SdkMemoryRuntime

INSERT = (
    "INSERT INTO memory_write_jobs ("
    " job_id,queue_seq,session_key,source_key,batch_id,idempotency_key,"
    " payload_json,evidence_ids_json,state,priority,attempts,max_attempts,"
    " lease_owner,lease_expires_at,hy_invoked,last_error,event_at,time_quality,"
    " created_at,updated_at"
    ") VALUES (?,?,?,?,?,?,'{}','[]','running','normal',0,3,?,?,0,NULL,?,"
    "'exact','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
)


def _bare_runtime(db: Path):
    runtime = object.__new__(SdkMemoryRuntime)
    runtime.write_queue = DurableWriteQueue(db)
    return runtime


def _add_row(
    db: Path,
    *,
    job_id: str,
    seq: int,
    owner: str | None,
    expires_at: datetime | None,
) -> None:
    with main_database(db).write() as connection, connection:
        connection.execute(
            INSERT,
            (
                job_id,
                seq,
                "sess-" + job_id,
                "local",
                "b-" + job_id,
                "k-" + job_id,
                owner,
                expires_at.isoformat() if expires_at is not None else None,
                "2026-11-07T12:00:00+00:00",
            ),
        )


def test_a_running_row_with_a_live_lease_is_not_stranded(tmp_path: Path) -> None:
    db = tmp_path / "one-memory.sqlite3"
    runtime = _bare_runtime(db)
    now = datetime.now(UTC)
    _add_row(db, job_id="live", seq=1, owner="worker-a", expires_at=now + timedelta(minutes=5))

    running, stranded = runtime.stranded_depths(now=now)

    assert (running, stranded) == (1, 0)


def test_a_running_row_without_a_lease_is_stranded(tmp_path: Path) -> None:
    db = tmp_path / "one-memory.sqlite3"
    runtime = _bare_runtime(db)
    now = datetime.now(UTC)
    _add_row(db, job_id="orphan", seq=1, owner=None, expires_at=None)

    running, stranded = runtime.stranded_depths(now=now)

    assert (running, stranded) == (1, 1)


def test_a_running_row_whose_lease_lapsed_is_stranded(tmp_path: Path) -> None:
    db = tmp_path / "one-memory.sqlite3"
    runtime = _bare_runtime(db)
    now = datetime.now(UTC)
    _add_row(
        db,
        job_id="lapsed",
        seq=1,
        owner="worker-gone",
        expires_at=now - timedelta(minutes=1),
    )

    running, stranded = runtime.stranded_depths(now=now)

    assert (running, stranded) == (1, 1)


def test_running_and_stranded_come_from_the_same_read(tmp_path: Path) -> None:
    db = tmp_path / "one-memory.sqlite3"
    runtime = _bare_runtime(db)
    now = datetime.now(UTC)
    _add_row(db, job_id="live-1", seq=1, owner="w", expires_at=now + timedelta(minutes=5))
    _add_row(db, job_id="live-2", seq=2, owner="w", expires_at=now + timedelta(minutes=5))
    _add_row(db, job_id="orphan-1", seq=3, owner=None, expires_at=None)
    _add_row(db, job_id="orphan-2", seq=4, owner="w", expires_at=now - timedelta(minutes=1))

    running, stranded = runtime.stranded_depths(now=now)

    assert running == 4
    assert stranded == 2
