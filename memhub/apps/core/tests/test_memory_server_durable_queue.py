"""The unified write entry must be durable, not just an in-memory receipt."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from one_memory.database_access import main_database
from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import (
    CANCELLED,
    COMPLETED,
    DEAD,
    FAILED,
    PENDING,
    RUNNING,
    UNKNOWN,
    WAITING_FOR_MODEL,
    WAITING_FOR_PERMISSION,
    DurableWriteDrain,
    DurableWriteQueue,
    evidence_for_request,
    execute_job,
)


def _request(
    *,
    evidence: tuple[str, ...] = ("e1",),
    key: str = "idem-1",
    source: str | None = None,
    event_at: datetime | None = None,
) -> WriteRequest:
    batch = BatchIdentity(internal_session_id="sess-1", first_round=1, last_round=5)
    return WriteRequest(
        internal_session_id="sess-1",
        batch_identity=batch,
        evidence_ids=list(evidence),
        idempotency_key=key,
        source_id=source,
        event_at=event_at,
    )


def _rows(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM memory_write_jobs")]


def test_acceptance_is_written_to_the_business_database(tmp_path, monkeypatch) -> None:
    from one_memory.memory_server import durable_queue

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        durable_queue,
        "diagnostic_event",
        lambda **event: events.append(event),
    )
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)

    record = queue.submit(_request(source="cursor"))

    rows = _rows(path)
    assert len(rows) == 1
    assert rows[0]["job_id"] == record.job_id
    assert rows[0]["idempotency_key"] == "idem-1"
    assert rows[0]["state"] == "pending"
    assert rows[0]["hy_invoked"] == 0
    assert record.enqueued is True
    assert events == [
        {
            "level": "info",
            "component": "memory.write_queue",
            "event": "memory_write_job.queued",
            "outcome": "queued",
            "job_id": record.job_id,
            "details": {
                "source_id": "cursor",
                "mode": "normal",
                "count": 1,
            },
        }
    ]


def test_the_stored_payload_is_references_only(tmp_path) -> None:
    """The job payload must point at Evidence, never carry its text.

    A queue row is durable and much longer-lived than the API call that made
    it, so storing verbatim content here would be a second copy of the user's
    private text. The payload is the request's identifiers only.
    """
    import json

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request(evidence=("ev-1", "ev-2")))

    payload = json.loads(_rows(path)[0]["payload_json"])
    assert set(payload) == {
        "internal_session_id",
        "batch_identity",
        "evidence_ids",
        "idempotency_key",
        "priority",
        "source_id",
    }
    assert payload["evidence_ids"] == ["ev-1", "ev-2"]


def test_submit_refuses_a_write_whose_evidence_does_not_exist(tmp_path) -> None:
    """Evidence-first, then enqueue: a dangling write must never be accepted.

    `plan_intake` states the rule in the abstract, but the durable boundary is
    the last place it can be enforced before a queue row becomes authoritative.
    If the writer accepts ids the business database does not hold, the failure
    is deferred to the drain as a `failed` job instead of being rejected at the
    caller, and a crash between Evidence and submit has no queue row to replay.
    """
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path, evidence=EvidenceStore(path))
    request = _request(evidence=("missing-evidence",), key="idem-missing")

    with pytest.raises(LookupError, match="missing-evidence"):
        queue.submit(request)

    assert _rows(path) == []


def test_submit_accepts_a_write_whose_evidence_exists(tmp_path) -> None:
    """The Evidence-first guard must not reject a legitimate write."""
    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    occurred_at = datetime(2026, 10, 17, 23, 45, tzinfo=UTC)
    created = store.put(
        EvidenceInput(
            source_id="test",
            conversation_id="conv",
            message_id="m1",
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=occurred_at,
            verbatim="present evidence",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    queue = DurableWriteQueue(path, evidence=store)

    record = queue.submit(_request(evidence=(created.evidence_id,), key="idem-present"))

    assert record.enqueued is True
    assert len(_rows(path)) == 1
    assert datetime.fromisoformat(_rows(path)[0]["event_at"]) == occurred_at
    assert _rows(path)[0]["time_quality"] == "exact"


def test_a_replay_is_idempotent_even_after_its_evidence_is_removed(tmp_path) -> None:
    """Evidence-first must guard a new row, not re-open a settled identity.

    The replay contract is "same key + same payload -> return the original
    record". If the Evidence was later deleted, a replay must still reconcile
    against the existing queue row rather than surfacing a new failure.
    """
    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    created = store.put(
        EvidenceInput(
            source_id="test",
            conversation_id="conv",
            message_id="m1",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="will be removed",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    queue = DurableWriteQueue(path, evidence=store)
    request = _request(evidence=(created.evidence_id,), key="idem-replay")
    first = queue.submit(request)

    with sqlite3.connect(path) as connection, connection:
        connection.execute("DELETE FROM evidence WHERE evidence_id=?", (created.evidence_id,))

    replay = queue.submit(request)

    assert replay.job_id == first.job_id
    assert replay.enqueued is False
    assert len(_rows(path)) == 1


def test_a_settled_job_replays_as_a_hit_not_a_conflict(tmp_path) -> None:
    """Replaying a completed key must return the job, never raise.

    Real trigger: 236 startup replays of one key, all
    `ValueError: idempotency key ... already exists with different content`.
    The stored payload had been reaped to a digest sentinel, and the caller's
    key is derived from the evidence's revision, which can move on between
    replays while the payload's evidence ids stay put. Refusing that pair
    turned a normal startup replay into an exception every time.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    first = queue.submit(_request(evidence=("e1",), key="idem-settled"))

    # Settle it the way the drain does: terminal state, HY invoked, and the
    # payload reaped down to its digest so no body is kept.
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state=?, hy_invoked=1, payload_json=? WHERE job_id=?",
            (
                COMPLETED,
                json.dumps({"_one_memory_reaped_payload": "0" * 64}),
                first.job_id,
            ),
        )

    # A payload that cannot match the reaped digest, under the same key.
    replay = queue.submit(_request(evidence=("e1",), key="idem-settled"))

    assert replay.job_id == first.job_id
    assert replay.state == COMPLETED
    assert replay.enqueued is False
    assert len(_rows(path)) == 1


def test_a_killed_worker_leaves_a_possibly_sent_job_unknown_after_restart(tmp_path) -> None:
    """A real process death after HY was invoked must not become retryable.

    The in-process restart test simulates an expired lease. This one kills the
    process that owns the lease and reopens the database in a fresh process, so
    the recovery path is exercised across an actual crash boundary rather than
    by editing the row.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    if sys.platform == "win32":
        pytest.skip("POSIX process-kill recovery")

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1",)))

    child = (
        "import time\n"
        "from pathlib import Path\n"
        "from datetime import timedelta\n"
        "from one_memory.memory_server.durable_queue import "
        "DurableWriteDrain, DurableWriteQueue\n"
        "path = Path(" + repr(str(path)) + ")\n"
        "queue = DurableWriteQueue(path)\n"
        "drain = DurableWriteDrain(\n"
        "    queue, execute=lambda _job: None, owner='crashing-worker',\n"
        "    lease=timedelta(milliseconds=150),\n"
        ")\n"
        "assert drain.claim(limit=1)\n"
        "assert queue.mark_invoked(" + repr(record.job_id) + ", owner='crashing-worker')\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(path.parents[2]),
        env={**os.environ, "PYTHONPATH": str(path.parents[2] / "src")},
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=10)

    time.sleep(0.25)
    reopened = DurableWriteQueue(path)

    assert reopened.get("idem-1").state == UNKNOWN
    assert DurableWriteDrain(reopened, execute=_never_execute).claim(limit=1) == ()
    assert reopened.reclaim_expired() == 0


def test_a_killed_worker_before_invocation_is_retried_after_restart(tmp_path) -> None:
    """The crash boundary must distinguish sent from not-yet-sent.

    Marking every killed worker `unknown` would be safe but would strand work
    that provably never left the process. The durable marker is what allows the
    restart to return this row to pending and run it exactly once.
    """
    import os
    import signal
    import subprocess
    import sys
    import time

    if sys.platform == "win32":
        pytest.skip("POSIX process-kill recovery")

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1",)))

    child = (
        "import time\n"
        "from pathlib import Path\n"
        "from datetime import timedelta\n"
        "from one_memory.memory_server.durable_queue import "
        "DurableWriteDrain, DurableWriteQueue\n"
        "path = Path(" + repr(str(path)) + ")\n"
        "queue = DurableWriteQueue(path)\n"
        "drain = DurableWriteDrain(\n"
        "    queue, execute=lambda _job: None, owner='crashing-worker',\n"
        "    lease=timedelta(milliseconds=150),\n"
        ")\n"
        "assert drain.claim(limit=1)\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", child],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(path.parents[2]),
        env={**os.environ, "PYTHONPATH": str(path.parents[2] / "src")},
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=10)

    time.sleep(0.25)
    reopened = DurableWriteQueue(path)

    assert reopened.get("idem-1").state == PENDING
    assert DurableWriteDrain(reopened, execute=_never_execute).claim(limit=1) == (record.job_id,)


def test_an_older_queue_without_newer_columns_is_upgraded_in_place(tmp_path) -> None:
    """An older `memory_write_jobs` must gain columns, not stay broken.

    `CREATE TABLE IF NOT EXISTS` is a no-op for an existing table, so a user
    upgrading from a build whose queue predates `hy_invoked`/`attempts` would
    otherwise keep a table every claim and lease query fails on.
    """
    path = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "CREATE TABLE memory_write_jobs ("
            "job_id TEXT PRIMARY KEY, session_key TEXT NOT NULL, batch_id TEXT NOT NULL,"
            "idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,"
            "state TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal',"
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

    queue = DurableWriteQueue(path)
    queue.submit(_request())

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_write_jobs)")}
    assert {
        "evidence_ids_json",
        "attempts",
        "max_attempts",
        "lease_owner",
        "lease_expires_at",
        "hy_invoked",
        "last_error",
    } <= columns
    assert queue.get("idem-1").state == "pending"
    assert _rows(path)[0]["hy_invoked"] == 0
    assert _rows(path)[0]["evidence_ids_json"] == '["e1"]'


def test_an_older_queue_without_priority_is_upgraded_in_place(tmp_path) -> None:
    """A pre-priority queue must be upgraded before claim/submit touches it."""
    path = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "CREATE TABLE memory_write_jobs ("
            "job_id TEXT PRIMARY KEY, session_key TEXT NOT NULL, batch_id TEXT NOT NULL,"
            "idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,"
            "state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

    queue = DurableWriteQueue(path)
    queue.submit(_request())

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_write_jobs)")}
    assert "priority" in columns
    assert _rows(path)[0]["priority"] == "normal"
    assert DurableWriteDrain(queue, execute=_never_execute).claim(limit=1)


def test_an_older_queue_rebuilds_a_wrongly_shaped_claim_index(tmp_path) -> None:
    """An existing index with the wrong columns is not the current index.

    ``CREATE INDEX IF NOT EXISTS`` only checks the name. A queue upgraded from
    a build whose claim index omitted ``priority`` keeps that old shape, so the
    ordered claim has to sort and may stop using the intended index. The
    upgrade must inspect the actual column order and rebuild the index, not
    treat its presence as proof that the schema is current.
    """
    path = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "CREATE TABLE memory_write_jobs ("
            "job_id TEXT PRIMARY KEY, session_key TEXT NOT NULL, batch_id TEXT NOT NULL,"
            "idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,"
            "state TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal',"
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE INDEX idx_write_jobs_claim ON memory_write_jobs(state, created_at)"
        )

    queue = DurableWriteQueue(path)
    queue.submit(_request())

    with sqlite3.connect(path) as connection:
        columns = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM pragma_index_info('idx_write_jobs_claim') ORDER BY seqno"
            )
        ]
    assert columns == ["state", "priority", "created_at"], columns
    assert DurableWriteDrain(queue, execute=_never_execute).claim(limit=1)


def test_an_older_queue_backfills_evidence_refs_from_the_intact_payload(tmp_path) -> None:
    """Adding the reference column must not turn old queued rows into orphans."""
    path = tmp_path / "one-memory.sqlite3"
    request = _request(evidence=("legacy-evidence",), key="legacy-key")
    with sqlite3.connect(path) as connection, connection:
        connection.execute("CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, imported_at TEXT)")
        connection.execute(
            "CREATE TABLE memory_write_jobs ("
            "job_id TEXT PRIMARY KEY, session_key TEXT NOT NULL, batch_id TEXT NOT NULL,"
            "idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,"
            "state TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal',"
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO memory_write_jobs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "legacy-job",
                "sess-1",
                request.batch_identity.batch_id,
                "legacy-key",
                request.model_dump_json(),
                "pending",
                "normal",
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )

    queue = DurableWriteQueue(path)

    assert _rows(path)[0]["evidence_ids_json"] == '["legacy-evidence"]'
    assert _rows(path)[0]["event_at"] == "2026-09-01T00:00:00+00:00"
    assert _rows(path)[0]["time_quality"] == "approximate"
    assert queue.unsubmitted_evidence_ids() == ()


def test_a_missing_reference_table_is_rebuilt_from_the_persisted_ids(tmp_path) -> None:
    """A damaged/replaced reference table must not turn queued Evidence into orphans."""
    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    evidence = store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="m1",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="reference table recovery",
            created_at=datetime.now(UTC),
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    queue = DurableWriteQueue(path)
    queue.submit(_request(evidence=(evidence.evidence_id,), key="reference-recovery"))
    with sqlite3.connect(path) as connection, connection:
        connection.execute("DROP TABLE memory_write_job_evidence")

    reopened = DurableWriteQueue(path)

    assert reopened.unsubmitted_evidence_ids() == ()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT 1 FROM memory_write_job_evidence WHERE evidence_id=?",
            (evidence.evidence_id,),
        ).fetchone() == (1,)


def test_a_partially_missing_reference_table_is_rebuilt_row_by_row(tmp_path) -> None:
    """One surviving reference must not mask another row's missing reference.

    An interrupted backfill or partial external cleanup can leave the table
    non-empty while one queued job has lost its reference row. Reopening the
    queue must reconcile each job independently, or that job's Evidence is
    rediscovered as an orphan and may be submitted a second time.
    """
    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    evidence_ids = []
    for index in range(2):
        record = store.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="conversation",
                message_id=f"m{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=f"partial reference {index}",
                created_at=datetime.now(UTC),
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        evidence_ids.append(record.evidence_id)
    queue = DurableWriteQueue(path)
    queue.submit(_request(evidence=tuple(evidence_ids), key="partial-references"))
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "DELETE FROM memory_write_job_evidence WHERE evidence_id=?",
            (evidence_ids[1],),
        )

    reopened = DurableWriteQueue(path)

    assert reopened.unsubmitted_evidence_ids() == ()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM memory_write_job_evidence WHERE job_id=?",
                (queue.get("partial-references").job_id,),
            ).fetchone()[0]
            == 2
        )


def test_reference_backfill_rejects_non_string_legacy_ids(tmp_path) -> None:
    """A malformed legacy reference must not become a fake Evidence id.

    An old or externally edited `evidence_ids_json` may contain `null` or
    numbers. Stringifying those would insert a real-looking reference that
    matches nothing, letting discovery treat the orphan as queued. The repair
    must leave the row incomplete and fail closed instead of manufacturing an
    identity.
    """
    path = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "CREATE TABLE memory_write_jobs ("
            "job_id TEXT PRIMARY KEY, session_key TEXT NOT NULL, batch_id TEXT NOT NULL,"
            "idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,"
            "evidence_ids_json TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL,"
            "priority TEXT NOT NULL DEFAULT 'normal', attempts INTEGER NOT NULL DEFAULT 0,"
            "max_attempts INTEGER NOT NULL DEFAULT 3, lease_owner TEXT,"
            "lease_expires_at TEXT, hy_invoked INTEGER NOT NULL DEFAULT 0,"
            "last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO memory_write_jobs ("
            "job_id, session_key, batch_id, idempotency_key, payload_json,"
            "evidence_ids_json, state, created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "legacy-bad-ref",
                "sess-1",
                "batch-1",
                "legacy-bad-ref-key",
                '{"evidence_ids": [null]}',
                "[null]",
                "completed",
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )

    queue = DurableWriteQueue(path)

    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM memory_write_job_evidence WHERE job_id=?",
                ("legacy-bad-ref",),
            ).fetchone()[0]
            == 0
        )
    # Either the general incomplete-reconcile guard or the missing-evidence
    # schema guard is an acceptable fail-closed outcome; neither may treat the
    # row as cleanly queued.
    with pytest.raises(RuntimeError) as refused:
        queue.unsubmitted_evidence_ids()
    assert "reconcile" in str(refused.value) or "evidence table" in str(refused.value)


def test_reference_backfill_tolerates_a_duplicated_legacy_id(tmp_path) -> None:
    """A legacy row that lists the same Evidence twice must not abort startup.

    The reference table's primary key is `(job_id, evidence_id)`. A hand-edited,
    restored, or pre-validation row can list an id twice; inserting both would
    raise IntegrityError from `_backfill_legacy_references`, so the queue
    constructor would fail and the Runtime could not open the database at all.
    The repair must collapse duplicates and stay idempotent.
    """
    import json

    from one_memory.database_access import main_database
    from one_memory.memory_server.durable_queue import DurableWriteQueue
    from one_memory.memory_server.migrations import memory_write_jobs_sqlite_schema

    path = tmp_path / "one-memory.sqlite3"
    with main_database(path).write(autocommit=True) as connection:
        for statement in memory_write_jobs_sqlite_schema():
            connection.execute(statement)
        connection.execute(
            "INSERT INTO memory_write_jobs ("
            "job_id, session_key, batch_id, idempotency_key, payload_json,"
            "evidence_ids_json, state, created_at, updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "legacy-dup-ref",
                "sess-1",
                "batch-1",
                "legacy-dup-ref-key",
                json.dumps({"evidence_ids": ["e1", "e1"]}),
                "[]",
                "pending",
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )

    # Must not raise, and the duplicate collapses to one reference row.
    DurableWriteQueue(path)
    DurableWriteQueue(path)  # a second open is idempotent too
    with main_database(path).read() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM memory_write_job_evidence WHERE job_id=?",
                ("legacy-dup-ref",),
            ).fetchone()[0]
            == 1
        )


def test_discovery_fails_closed_when_a_legacy_reaped_row_has_no_refs(tmp_path) -> None:
    """A pre-upgrade reaped row cannot prove which Evidence it represented.

    Treating that row as absent would make every candidate look unqueued and
    could re-submit completed memories. Discovery must refuse the incomplete
    reconcile instead of guessing.
    """
    path = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "CREATE TABLE memory_write_jobs ("
            "job_id TEXT PRIMARY KEY, session_key TEXT NOT NULL, batch_id TEXT NOT NULL,"
            "idempotency_key TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,"
            "state TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal',"
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO memory_write_jobs VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "legacy-reaped",
                "sess-1",
                "batch-1",
                "legacy-key",
                '{"_one_memory_reaped_payload":"deadbeef"}',
                "completed",
                "normal",
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )

    queue = DurableWriteQueue(path)

    with pytest.raises(RuntimeError, match="refusing an incomplete reconcile"):
        queue.unsubmitted_evidence_ids()


def test_a_replayed_write_is_not_enqueued_twice(tmp_path) -> None:
    """A replayed scan must not produce a second write for the same batch."""
    path = tmp_path / "one-memory.sqlite3"
    first = DurableWriteQueue(path)
    first.submit(_request())

    reopened = DurableWriteQueue(path)
    replay = reopened.submit(_request())

    assert replay.enqueued is False
    assert len(_rows(path)) == 1


def test_same_key_different_evidence_is_refused(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request(evidence=("e1",)))

    with pytest.raises(ValueError, match="different content"):
        queue.submit(_request(evidence=("e2",)))

    assert len(_rows(path)) == 1


def test_same_turn_replayed_by_live_sync_upgrades_priority_without_conflict(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    normal = _request(evidence=("e1", "e2"), key="same-turn")
    first = queue.submit(normal)

    replay = queue.submit(normal.model_copy(update={"priority": "live"}))

    assert replay.job_id == first.job_id
    assert replay.enqueued is False
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT priority FROM memory_write_jobs WHERE job_id=?", (first.job_id,)
            ).fetchone()[0]
            == "live"
        )


class _Recorder:
    """Stands in for `MemoryProcessingSupervisor.process_records`."""

    def __init__(self, error: Exception | None = None, *, invoke_hy: bool = False) -> None:
        self.seen: list[tuple[str, ...]] = []
        self.error = error
        self.invoke_hy = invoke_hy

    async def process_records(self, records, **_kwargs):
        self.seen.append(tuple(record.evidence_id for record in records))
        if self.invoke_hy:
            await _kwargs["before_hy_invocation"]()
        if self.error is not None:
            raise self.error
        return ()


class _TwoInvocationRecorder:
    def __init__(self, pause_after_first) -> None:
        self.pause_after_first = pause_after_first
        self.invocations = 0

    async def process_records(self, _records, **kwargs):
        await kwargs["before_hy_invocation"]()
        self.invocations += 1
        self.pause_after_first()
        await kwargs["before_hy_invocation"]()
        self.invocations += 1
        return ()


class _EvidenceStore:
    def __init__(self, known: set[str]) -> None:
        self.known = known

    def get(self, evidence_id: str):
        if evidence_id not in self.known:
            return None
        return type("Record", (), {"evidence_id": evidence_id})()


def _state(path, job_id: str) -> str:
    rows = _rows(path)
    return str(next(row["state"] for row in rows if row["job_id"] == job_id))


def _claim_through_drain(queue: DurableWriteQueue, job_id: str) -> None:
    """Lease a submitted job the way production does before executing it.

    `execute_job` refuses to run a row it does not own, so tests that exercise
    the authorized path must claim first rather than calling it on a fresh
    `pending` row.
    """
    drain = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(seconds=30))
    claimed = drain.claim(limit=1)
    assert claimed == (job_id,), claimed


def test_a_durable_job_is_processed_through_the_authorized_path(tmp_path) -> None:
    """The executor must not bypass `process_records` (permission + runner)."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1", "e2")))
    supervisor = _Recorder()
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=supervisor,
            evidence=_EvidenceStore({"e1", "e2"}),
        )
    )

    assert supervisor.seen == [("e1", "e2")]
    assert _state(path, record.job_id) == "completed"


def test_missing_evidence_is_refused_without_calling_the_model(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("gone",)))
    supervisor = _Recorder()
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=supervisor,
            evidence=_EvidenceStore(set()),
        )
    )

    assert supervisor.seen == []
    assert _state(path, record.job_id) == "failed"


def test_a_claimed_row_with_no_readable_payload_is_settled_not_left_running(tmp_path) -> None:
    """A claimed row whose payload cannot be read must be released.

    Returning silently would leave the row `running` with the lease held, so
    the worker looks busy until the lease lapses. Nothing was sent, so the
    honest state is a terminal `failed`, released immediately.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)
    # A reaped/foreign payload the runner cannot interpret.
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET payload_json=? WHERE job_id=?",
            ('{"_one_memory_reaped_payload":"deadbeef"}', record.job_id),
        )

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert _state(path, record.job_id) == FAILED
    assert _rows(path)[0]["lease_owner"] is None


def test_an_unknown_outcome_is_recorded_as_unknown_not_retried(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    supervisor = _Recorder(error=RuntimeError("model_content_outcome_unknown"))
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=supervisor,
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert _state(path, record.job_id) == "unknown"


def test_unpermitted_evidence_parks_and_is_readmitted_after_confirmation(tmp_path) -> None:
    """A note claimed before its confirmation must not become terminal failed."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    request = _request().model_copy(update={"priority": "live"})
    record = queue.submit(request)
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError("model_content_evidence_not_permitted")),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    parked = queue.get(record.request_id)
    assert parked.state == WAITING_FOR_PERMISSION
    assert parked.detail == "model_content_evidence_not_permitted"
    assert queue.readmit_parked() == 1
    replay = queue.submit(request)
    assert replay.enqueued is False
    assert replay.state == PENDING


def test_a_permission_error_after_hy_invocation_stays_unknown(tmp_path) -> None:
    """An error code cannot erase the durable possibly-sent boundary."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(
                error=RuntimeError("model_content_evidence_changed"),
                invoke_hy=True,
            ),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert queue.get(record.request_id).state == UNKNOWN
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT hy_invoked,last_error FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone() == (1, "model_content_evidence_changed")
    assert queue.reclaim_expired() == 0


def test_replayed_live_evidence_readmits_a_pre_send_identity_failure(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    request = _request().model_copy(update={"priority": "live"})
    first = queue.submit(request)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='failed', attempts=1, hy_invoked=0, "
            "last_error='model_content_ingest_identity_unverified' WHERE job_id=?",
            (first.job_id,),
        )

    replay = queue.submit(request)

    assert replay.job_id == first.job_id
    assert replay.enqueued is True and replay.state == "pending"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT priority,attempts,hy_invoked,last_error FROM memory_write_jobs WHERE job_id=?",
            (first.job_id,),
        ).fetchone() == ("live", 1, 0, None)


def test_startup_repairs_stale_journal_and_requeues_unsent_identity_failure(tmp_path) -> None:
    from one_memory import ingest_journal
    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    evidence = EvidenceStore(path)
    with evidence._connect(writing=True) as connection:
        ingest_journal.initialize(connection)
    saved = evidence.put(
        EvidenceInput(
            source_id="workbuddy",
            conversation_id="blueberry",
            message_id="user-1",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="我喜欢蓝莓",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    queue = DurableWriteQueue(path, evidence=evidence)
    request = _request(evidence=(saved.evidence_id,), key="blueberry-live").model_copy(
        update={"source_id": "workbuddy", "priority": "live"}
    )
    job = queue.submit(request)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "INSERT INTO evidence_origins VALUES (?,'hook','{}',NULL,?)",
            (saved.evidence_id, datetime.now(UTC).isoformat()),
        )
        connection.execute(
            "UPDATE evidence SET updated_at=? WHERE evidence_id=?",
            (datetime.now(UTC).isoformat(), saved.evidence_id),
        )
        connection.execute(
            "UPDATE memory_write_jobs SET state='failed',attempts=1,hy_invoked=0,"
            "last_error='model_content_ingest_identity_unverified' WHERE job_id=?",
            (job.job_id,),
        )
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN")
        stale = ingest_journal.current(connection, "local", saved.evidence_id)
        assert stale is not None
        assert not ingest_journal.matches_current(connection, stale)
        connection.rollback()

    assert queue.repair_pre_send_identity_failures() == 1
    repaired = queue.get(job.request_id)
    assert repaired is not None
    assert repaired.state == PENDING
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN")
        current = ingest_journal.current(connection, "local", saved.evidence_id)
        assert current is not None
        assert current.generation_eligible is True
        assert ingest_journal.matches_current(connection, current)
        connection.rollback()


def test_startup_never_repairs_a_possibly_sent_identity_failure(tmp_path) -> None:
    from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    evidence = EvidenceStore(path)
    saved = evidence.put(
        EvidenceInput(
            source_id="workbuddy",
            conversation_id="possibly-sent",
            message_id="user-1",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="已经发给模型",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    queue = DurableWriteQueue(path, evidence=evidence)
    job = queue.submit(_request(evidence=(saved.evidence_id,), key="possibly-sent"))
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='unknown',attempts=1,hy_invoked=1,"
            "last_error='model_content_ingest_identity_unverified' WHERE job_id=?",
            (job.job_id,),
        )

    assert queue.repair_pre_send_identity_failures() == 0
    assert queue.get(job.request_id).state == UNKNOWN


def test_reconciled_absent_timeout_is_requeued_once_without_resetting_attempts(
    tmp_path,
) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='unknown', attempts=1, "
            "hy_invoked=1, last_error='hy_memory_timeout', updated_at=? WHERE job_id=?",
            ("2026-01-01T00:00:00+00:00", record.job_id),
        )

    assert (
        queue.retry_timed_out_unknown_after_reconcile(
            older_than=datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
        )
        == 1
    )
    with sqlite3.connect(path) as connection:
        state, attempts, invoked, error = connection.execute(
            "SELECT state, attempts, hy_invoked, last_error FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone()
    assert (state, attempts, invoked, error) == (
        "pending",
        1,
        0,
        "model_content_reconciled_absent_retry",
    )


def test_unresolved_unknown_is_automatically_retried_only_once(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='unknown',attempts=1,hy_invoked=1,"
            "last_error='model_content_outcome_unknown',updated_at=? WHERE job_id=?",
            ("2026-01-01T00:00:00+00:00", record.job_id),
        )

    cutoff = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    assert queue.retry_first_unknown_after_reconcile(older_than=cutoff) == 1
    first_retry = queue.get(record.request_id)
    assert first_retry.state == PENDING
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT attempts FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone()[0] == 1

    drain = DurableWriteDrain(queue, execute=_never_execute, owner="retry-owner")
    assert drain.claim(limit=1) == (record.job_id,)
    assert queue.mark_invoked(record.job_id, owner="retry-owner")
    queue.settle(
        record.job_id,
        state=UNKNOWN,
        owner="retry-owner",
        error_code="model_content_outcome_unknown",
        hy_invoked=True,
    )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT attempts FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone()[0] == 2
    assert (
        queue.retry_first_unknown_after_reconcile(
            older_than=datetime.now(UTC) + timedelta(minutes=1)
        )
        == 0
    )
    assert queue.get(record.request_id).state == UNKNOWN


def test_drain_runs_unknown_reconciliation_during_busy_safe_maintenance(tmp_path) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    calls = 0
    drain: DurableWriteDrain

    async def reconcile() -> None:
        nonlocal calls
        calls += 1
        drain.stop()

    drain = DurableWriteDrain(
        queue,
        execute=lambda _job_id: asyncio.sleep(0),
        reconcile_unknown=reconcile,
    )
    asyncio.run(drain.run_forever(idle_pause=0.001))
    assert calls == 1


def test_unknown_reconciliation_runs_on_a_ten_second_ceiling(tmp_path) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")

    production = DurableWriteDrain(queue, execute=_never_execute)
    immediate = DurableWriteDrain(
        queue,
        execute=_never_execute,
        reap_interval=timedelta(0),
    )

    assert production._unknown_reconcile_interval == timedelta(seconds=10)
    assert immediate._unknown_reconcile_interval == timedelta(0)


def test_a_reclaimed_uninvoked_job_with_attempts_left_is_retryable(tmp_path) -> None:
    """Recovery of a never-invoked lease is the retry path, not `unknown`.

    `execute_job` marks the no-blind-retry boundary before it enters the
    authorized path, so an exception there is honestly `unknown`. Recovery is
    the other case: a worker that died before the marker has `hy_invoked=0`,
    which proves nothing reached HY and is therefore safe to requeue.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _expire(path, record.job_id, hy_invoked=0)

    recovered = DurableWriteQueue(path)

    assert recovered.get("idem-1").state == PENDING
    assert _rows(path)[0]["attempts"] == 0


def test_post_invocation_transport_failure_stays_unknown_not_pending(tmp_path) -> None:
    """Once the bridge may have been opened, no error is safe to replay."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError("hy_memory_timeout")),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert _state(path, record.job_id) == UNKNOWN
    assert _rows(path)[0]["hy_invoked"] == 1


@pytest.mark.parametrize(
    "code,expected",
    [
        ("hy_memory_evidence_changed", CANCELLED),
        ("hy_memory_input_exceeds_window", FAILED),
    ],
)
def test_a_pre_send_refusal_is_terminal_not_unknown(tmp_path, code: str, expected: str) -> None:
    """A provably pre-bridge refusal must not be recorded as `unknown`.

    Both codes are raised while validating the frozen records, before the
    bridge exists, so nothing reached HY. Recording `unknown` would imply a
    possibly-applied write and block the honest signal; retrying would fail
    identically. The correct state is terminal `failed` with `hy_invoked=0`.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError(code)),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert _state(path, record.job_id) == expected
    assert _rows(path)[0]["hy_invoked"] == 0


def test_a_missing_permission_parks_instead_of_failing(tmp_path) -> None:
    """Authorization not yet granted is not a failure.

    The task layer distinguishes `waiting_for_permission`; the durable job must
    too, or a later grant can never revive it and a user's own switch pollutes
    the failure metric.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError("model_content_permission_required")),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert _state(path, record.job_id) == WAITING_FOR_PERMISSION
    assert _rows(path)[0]["hy_invoked"] == 0


def test_a_raw_provider_message_is_never_stored_as_the_error(tmp_path) -> None:
    """`last_error` is surfaced as HTTP `detail`; it must not carry content.

    A provider exception can quote the user's own text or embed a URL.
    docs/contracts/hy-failure-classification.md forbids persisting provider or
    user content, so an unrecognised message collapses to its exception type
    while a real machine code is kept for classification.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(
                error=RuntimeError("provider https://api.example.com failed on 'my secret diary'")
            ),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    stored = str(_rows(path)[0]["last_error"])
    assert "secret diary" not in stored
    assert "http" not in stored
    assert stored == "RuntimeError"


def test_a_machine_error_code_is_preserved(tmp_path) -> None:
    """Recognised codes survive; only free-form text is dropped."""
    from one_memory.memory_server.durable_queue import safe_error_code

    assert safe_error_code(RuntimeError("model_content_permission_required")) == (
        "model_content_permission_required"
    )
    assert safe_error_code(RuntimeError("hy_memory_unavailable")) == "hy_memory_unavailable"
    assert safe_error_code(ValueError("some sentence with spaces")) == "ValueError"


def test_an_inactive_profile_parks_for_model_before_any_send(tmp_path) -> None:
    """A pre-bridge model-not-ready code parks and keeps the intent."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError("model_profile_not_active")),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert _state(path, record.job_id) == WAITING_FOR_MODEL
    assert _rows(path)[0]["hy_invoked"] == 0


def test_an_authorization_stop_cancels_instead_of_failing(tmp_path) -> None:
    """A revoked/superseded policy is the user turning it off, not a break."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError("model_content_permission_revoked")),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert _state(path, record.job_id) == CANCELLED


def test_evidence_outside_the_enabled_policy_is_not_counted_as_a_failure(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError("model_content_evidence_outside_policy")),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    settled = queue.get(record.request_id)
    assert settled.state == CANCELLED
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT hy_invoked FROM memory_write_jobs WHERE job_id=?", (record.job_id,)
        ).fetchone() == (0,)
    assert DurableWriteDrain(queue, execute=_never_execute).claim(limit=1) == ()


def test_parked_jobs_are_not_hot_claimed_until_readmitted(tmp_path) -> None:
    """A parked job must not spin; explicit readmission makes it claimable."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(
        record.job_id,
        state=WAITING_FOR_PERMISSION,
        error_code="model_content_permission_required",
        hy_invoked=False,
    )
    drain = DurableWriteDrain(queue, execute=_never_execute)

    assert drain.claim(limit=1) == ()

    assert queue.readmit_parked() == 1
    assert drain.claim(limit=1) == (record.job_id,)


def test_readmitting_a_parked_job_clears_its_stale_detail(tmp_path) -> None:
    """A readmitted job must not keep reporting why it was once parked.

    `last_error` is surfaced over HTTP as `detail`. After the condition that
    parked the job changes, the row is claimable again; leaving the old code
    in place makes a healthy pending write report `permission_required` as if
    it were still blocked.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(
        record.job_id,
        state=WAITING_FOR_PERMISSION,
        error_code="model_content_permission_required",
        hy_invoked=False,
    )

    assert queue.readmit_parked() == 1

    readmitted = queue.get("idem-1")
    assert readmitted.state == PENDING
    assert readmitted.detail == "", readmitted.detail


def test_upgrade_readmits_only_pre_send_legacy_authority_failures(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    safe = queue.submit(_request(evidence=("e1",), key="legacy-safe"))
    possibly_sent = queue.submit(_request(evidence=("e2",), key="legacy-sent"))
    other = queue.submit(_request(evidence=("e3",), key="other-failure"))
    queue.settle(
        safe.job_id,
        state=FAILED,
        error_code="model_content_legacy_authority_changed",
        hy_invoked=False,
    )
    queue.settle(
        possibly_sent.job_id,
        state=UNKNOWN,
        error_code="model_content_outcome_unknown",
        hy_invoked=True,
    )
    queue.settle(
        other.job_id,
        state=FAILED,
        error_code="hy_memory_unknown_error",
        hy_invoked=False,
    )

    assert queue.recover_obsolete_legacy_authority_failures() == 1
    assert queue.get("legacy-safe").state == PENDING
    assert queue.get("legacy-sent").state == UNKNOWN
    assert queue.get("other-failure").state == FAILED


def test_readmission_can_target_a_single_parked_state(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    permission = queue.submit(_request(evidence=("e1",)))
    model = queue.submit(_request(evidence=("e2",), key="idem-2"))
    queue.settle(permission.job_id, state=WAITING_FOR_PERMISSION, hy_invoked=False)
    queue.settle(model.job_id, state=WAITING_FOR_MODEL, hy_invoked=False)

    assert queue.readmit_parked(states=(WAITING_FOR_PERMISSION,)) == 1
    assert queue.get("idem-1").state == PENDING
    assert queue.get("idem-2").state == WAITING_FOR_MODEL


def test_parking_does_not_burn_the_retry_budget(tmp_path) -> None:
    """A write that only ever waited for permission must not be dead-lettered.

    Claiming increments `attempts`. If parking left that increment in place, a
    job with no permission would exhaust `max_attempts` across admission
    retries and be marked `dead` without ever having been sent.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    drain = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(seconds=1))

    for _ in range(5):
        assert drain.claim(limit=1) == (record.job_id,)
        queue.settle(
            record.job_id,
            state=WAITING_FOR_PERMISSION,
            error_code="model_content_permission_required",
            hy_invoked=False,
        )
        assert queue.readmit_parked() == 1

    assert _rows(path)[0]["attempts"] == 0
    assert queue.get("idem-1").state == PENDING


def test_a_model_waiter_is_readmitted_once_its_wait_elapses(tmp_path) -> None:
    """A model outage clears on its own; nothing re-admits it otherwise.

    `waiting_for_permission` needs a grant event, but `waiting_for_model` is
    transient by definition. Without a timer the write would sit parked
    forever after the provider came back, so the drain re-admits it on a
    bounded delay and gives back the parking attempt.
    """
    from datetime import UTC, datetime, timedelta

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    drain = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(seconds=1))
    drain.claim(limit=1)
    queue.settle(
        record.job_id,
        state=WAITING_FOR_MODEL,
        error_code="model_profile_not_active",
        hy_invoked=False,
    )

    now = datetime.now(UTC)
    # Still inside the wait window: not yet re-admitted, and not reset.
    assert queue.readmit_model_waiters(now=now, after=timedelta(minutes=1)) == 0
    assert queue.get("idem-1").state == WAITING_FOR_MODEL

    # Past the window: back to pending with its attempt returned.
    assert (
        queue.readmit_model_waiters(now=now + timedelta(minutes=2), after=timedelta(minutes=1)) == 1
    )
    assert queue.get("idem-1").state == PENDING
    assert _rows(path)[0]["attempts"] == 0


def test_readmitting_a_model_waiter_clears_its_stale_detail(tmp_path) -> None:
    """The timer readmission path must clear the old blocking reason too."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(
        record.job_id,
        state=WAITING_FOR_MODEL,
        error_code="model_profile_not_active",
        hy_invoked=False,
    )
    now = datetime.now(UTC)

    assert queue.readmit_model_waiters(now=now + timedelta(minutes=2)) == 1

    readmitted = queue.get("idem-1")
    assert readmitted.state == PENDING
    assert readmitted.detail == ""


def test_readmit_parked_cannot_resurrect_a_terminal_row(tmp_path) -> None:
    """Only parked states may be re-admitted, never a finished write.

    `readmit_parked(states=...)` takes an arbitrary tuple. A caller that passed
    a terminal state would move `completed`/`dead`/`cancelled` back to
    `pending`, making a finished write claimable again — the blind re-send the
    terminal states exist to prevent. The narrow default is not enough; the
    argument must be validated.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state=COMPLETED)

    with pytest.raises(ValueError, match="parked"):
        queue.readmit_parked(states=(COMPLETED,))

    assert queue.get("idem-1").state == COMPLETED


def test_a_drain_allows_a_profile_ceiling_longer_than_its_renewed_lease(tmp_path) -> None:
    """The lease is renewed liveness, not the model's wall-clock budget."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    DurableWriteDrain(
        queue,
        execute=_never_execute,
        lease=timedelta(seconds=10),
        work_ceiling=timedelta(seconds=300),
    )


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.serial
def test_a_fifteen_second_model_task_completes_under_profile_timeout(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())

    async def execute(job_id: str) -> None:
        assert queue.mark_invoked(job_id, owner="profile-budget") is True
        await asyncio.sleep(15)
        queue.settle(job_id, state=COMPLETED, owner="profile-budget")

    drain = DurableWriteDrain(
        queue,
        execute=execute,
        owner="profile-budget",
        lease=timedelta(seconds=2),
        renew_every=timedelta(milliseconds=200),
        work_ceiling=lambda: timedelta(seconds=40),
    )

    asyncio.run(drain.run_one())

    assert queue.get(record.request_id).state == COMPLETED


def test_readmitting_model_waiters_does_not_touch_permission_waiters(tmp_path) -> None:
    """Only the transient state is on a timer; permission waiters are not."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    permission = queue.submit(_request(evidence=("e1",)))
    model = queue.submit(_request(evidence=("e2",), key="idem-2"))
    queue.settle(permission.job_id, state=WAITING_FOR_PERMISSION, hy_invoked=False)
    queue.settle(model.job_id, state=WAITING_FOR_MODEL, hy_invoked=False)
    with sqlite3.connect(path) as connection, connection:
        connection.execute("UPDATE memory_write_jobs SET updated_at='2000-01-01T00:00:00+00:00'")

    assert queue.readmit_model_waiters() == 1
    assert queue.get("idem-1").state == WAITING_FOR_PERMISSION
    assert queue.get("idem-2").state == PENDING


def test_policy_stop_codes_match_the_task_layer() -> None:
    """The durable classification must not drift from the task-layer list.

    `memory_tasks.POLICY_STOPPED_CODES` is where the marking, the migration of
    old rows and the counting live. A local copy that silently diverged would
    let an authorization stop be counted as a failure on one layer only.
    """
    from one_memory.memory_server import durable_queue
    from one_memory.memory_tasks import POLICY_STOPPED_CODES as task_codes

    assert set(task_codes) == durable_queue.POLICY_STOPPED_CODES


def test_evidence_for_request_keeps_the_requested_order(tmp_path) -> None:
    request = _request(evidence=("e2", "e1"))
    store = _EvidenceStore({"e1", "e2"})

    records = evidence_for_request(store, request)

    assert [record.evidence_id for record in records] == ["e2", "e1"]


def _expire(path, job_id: str, *, hy_invoked: int, lease_owner: str = "worker-1") -> None:
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', lease_owner=?, "
            "lease_expires_at='2000-01-01T00:00:00+00:00', hy_invoked=? WHERE job_id=?",
            (lease_owner, hy_invoked, job_id),
        )


def test_a_lapsed_lease_is_reclaimed_by_whether_hy_was_reached(tmp_path) -> None:
    """Expired leases split in two, and unknown never becomes pending again.

    `queue.py`'s reclaim SQL is written for MySQL (`NOW(6)`); under D56 the
    durable queue needs a rendering that actually runs. The invariant is the
    same: possibly-sent goes to `unknown`, never-sent may retry.
    """
    from datetime import UTC, datetime

    from one_memory.memory_server.queue import build_reclaim_sqlite_statements

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    sent = queue.submit(_request(evidence=("e1",)))
    unsent = queue.submit(_request(evidence=("e2",), key="idem-2"))
    _expire(path, sent.job_id, hy_invoked=1)
    _expire(path, unsent.job_id, hy_invoked=0)

    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(path) as connection, connection:
        for sql, params in build_reclaim_sqlite_statements(now):
            connection.execute(sql, params)

    after = {row["idempotency_key"]: row["state"] for row in _rows(path)}
    assert after["idem-1"] == "unknown"
    assert after["idem-2"] == "pending"

    # Re-running reclaim must not resurrect the unknown job as retryable.
    with sqlite3.connect(path) as connection, connection:
        for sql, params in build_reclaim_sqlite_statements(now):
            connection.execute(sql, params)
    assert queue.get("idem-1").state == "unknown"


def test_reopening_the_queue_recovers_a_dead_workers_leases(tmp_path) -> None:
    """Recovery must happen on process start, not only in a helper test.

    A worker can die between marking a lease running and settling it. The next
    process cannot ask the dead worker what happened, so opening the durable
    queue must reclaim before serving: never-invoked work goes back to
    pending, potentially-invoked work becomes sticky unknown.
    """
    path = tmp_path / "one-memory.sqlite3"
    first = DurableWriteQueue(path)
    sent = first.submit(_request(evidence=("e1",)))
    unsent = first.submit(_request(evidence=("e2",), key="idem-2"))
    _expire(path, sent.job_id, hy_invoked=1)
    _expire(path, unsent.job_id, hy_invoked=0)

    # Constructing the next queue is the restart boundary. No test helper or
    # manual SQL is allowed to be the only reason recovery happens.
    reopened = DurableWriteQueue(path)

    assert reopened.get("idem-1").state == "unknown"
    assert reopened.get("idem-2").state == "pending"
    assert reopened.reclaim_expired() == 0
    assert reopened.get("idem-1").state == "unknown"


def test_reclaim_dead_letters_an_uninvoked_job_with_no_attempts_left(tmp_path) -> None:
    """Never-invoked work still has a retry budget; exhausting it ends dead."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _expire(path, record.job_id, hy_invoked=0)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET attempts=max_attempts WHERE job_id=?",
            (record.job_id,),
        )

    reopened = DurableWriteQueue(path)

    assert reopened.get("idem-1").state == DEAD
    assert reopened.reclaim_expired() == 0
    assert reopened.get("idem-1").state == DEAD


def test_reclaim_never_dead_letters_a_possibly_invoked_job(tmp_path) -> None:
    """`unknown` outranks the attempts budget: a sent job is never discarded."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _expire(path, record.job_id, hy_invoked=1)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET attempts=max_attempts WHERE job_id=?",
            (record.job_id,),
        )

    reopened = DurableWriteQueue(path)

    assert reopened.get("idem-1").state == UNKNOWN


def test_reclaim_recovers_a_running_row_with_no_lease_expiry(tmp_path) -> None:
    """A `running` row is only ever written with a lease; a NULL expiry is an
    orphan a crash or an older build left behind.

    Such a row is invisible to the claim (which only takes `pending`) and was
    invisible to reclaim (which required a non-null expiry), so it would sit
    `running` forever and the write would never run or be reported.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', lease_owner='dead', "
            "lease_expires_at=NULL, hy_invoked=0 WHERE job_id=?",
            (record.job_id,),
        )

    assert queue.reclaim_expired() == 1
    assert queue.get("idem-1").state == PENDING


def test_reclaim_treats_an_orphaned_possibly_sent_row_as_unknown(tmp_path) -> None:
    """The same NULL-expiry orphan must not be retried if HY may have been hit."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', lease_owner='dead', "
            "lease_expires_at=NULL, hy_invoked=1 WHERE job_id=?",
            (record.job_id,),
        )

    assert queue.reclaim_expired() == 1
    assert queue.get("idem-1").state == UNKNOWN


def test_execute_job_crash_after_marking_hy_invoked_becomes_sticky_unknown(tmp_path) -> None:
    """A cancelled process must not turn a possibly-sent write into a retry.

    `execute_job` records `hy_invoked=1` in place before it enters the
    authorized processing path, keeping the lease. If the process is then
    cancelled or killed, the lease eventually expires and the next open must
    choose `unknown`, not `pending`: HY has no idempotency key and a retry
    could duplicate the write.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1",)))
    drain = DurableWriteDrain(
        queue,
        execute=lambda job_id: asyncio.sleep(0),
        lease=timedelta(seconds=1),
    )
    drain.claim(limit=1)

    class _CancelledBeforeReturn:
        async def process_records(self, _records, **_kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            execute_job(
                record.job_id,
                queue=queue,
                supervisor=_CancelledBeforeReturn(),
                evidence=_EvidenceStore({"e1"}),
            )
        )

    # The mark must not have dropped the lease; expiry is what lets the next
    # process see the abandoned row at all.
    with sqlite3.connect(path) as connection:
        owner, expires = connection.execute(
            "SELECT lease_owner, lease_expires_at FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone()
    assert owner is not None and expires is not None
    _expire(path, record.job_id, hy_invoked=1)
    reopened = DurableWriteQueue(path)
    assert reopened.get("idem-1").state == "unknown"
    assert reopened.reclaim_expired() == 0


def test_mark_invoked_keeps_the_lease_renewable(tmp_path) -> None:
    """Marking HY invocation must not make renewal a no-op.

    `settle(RUNNING)` clears the lease, so it must not be used for the
    in-flight marker: doing so would stop renewals and could strand a crashed
    row `running` with no expiry, invisibly to reclaim.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1",)))
    drain = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(seconds=1))

    drain.claim(limit=1)
    assert queue.mark_invoked(record.job_id) is True

    # Renewal still finds the row it owns, and the lease is still present.
    assert drain.renew(record.job_id) is True
    with sqlite3.connect(path) as connection:
        hy_invoked, owner = connection.execute(
            "SELECT hy_invoked, lease_owner FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone()
    assert hy_invoked == 1 and owner == drain.owner


def test_execute_job_stops_when_the_lease_was_lost_before_invocation(tmp_path) -> None:
    """A worker that no longer owns the row must not call HY.

    If the lease was reclaimed (or re-claimed) between claim and invocation,
    marking the boundary fails. Proceeding would send a write the queue no
    longer considers ours, which is the duplicate the marker exists to stop.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    supervisor = _Recorder()
    # Simulate a lost lease: the row is no longer `running`.
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='pending', lease_owner=NULL, "
            "lease_expires_at=NULL WHERE job_id=?",
            (record.job_id,),
        )

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=supervisor,
            evidence=_EvidenceStore({"e1"}),
        )
    )

    # The supervisor may prepare and validate the records, but the callback
    # still blocks the actual HY invocation at the last safe boundary.
    assert supervisor.seen == [("e1",)]


def test_drain_renews_the_lease_while_a_slow_job_is_in_flight(tmp_path) -> None:
    """A long job must not lose its lease and be re-claimed mid-flight.

    Without live renewal a slow write's lease lapses, and once `hy_invoked` is
    set a second worker could reclaim it to `unknown` while it is still
    running. The renewal loop is what keeps the lease valid for the whole call.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    observed: list[str | None] = []

    async def execute(job_id: str) -> None:
        queue.mark_invoked(job_id)
        await asyncio.sleep(0.25)
        with sqlite3.connect(path) as connection:
            observed.append(
                connection.execute(
                    "SELECT lease_expires_at FROM memory_write_jobs WHERE job_id=?",
                    (job_id,),
                ).fetchone()[0]
            )
        queue.settle(job_id, state="completed")

    async def scenario() -> None:
        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(milliseconds=200),
            renew_every=timedelta(milliseconds=40),
        )
        claimed = drain.claim(limit=1)
        assert claimed == (record.job_id,)
        with sqlite3.connect(path) as connection:
            initial_expiry = connection.execute(
                "SELECT lease_expires_at FROM memory_write_jobs WHERE job_id=?",
                (record.job_id,),
            ).fetchone()[0]
        await drain._run_claimed(claimed)  # noqa: SLF001
        return initial_expiry

    initial_expiry = asyncio.run(scenario())

    # Renewal ran while the job slept: the expiry is past the original claim.
    assert observed and observed[0] is not None
    assert observed[0] > initial_expiry
    assert queue.get("idem-1").state == "completed"


def test_a_wedged_job_hits_the_work_ceiling_and_settles_unknown(tmp_path) -> None:
    """Renewal alone would keep a hung model call alive forever.

    With a work ceiling the job is cancelled, marked from the durable marker,
    and settled `unknown` (it may have reached HY before hanging), so the lease
    stops being extended and the row is not stuck `running`.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())

    async def execute(job_id: str) -> None:
        queue.mark_invoked(job_id)
        await asyncio.sleep(10)

    async def scenario() -> None:
        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(seconds=1),
            renew_every=timedelta(milliseconds=50),
            work_ceiling=timedelta(milliseconds=150),
        )
        await drain.run_one()

    asyncio.run(scenario())

    assert queue.get("idem-1").state == UNKNOWN
    with sqlite3.connect(path) as connection:
        state, owner, expiry = connection.execute(
            "SELECT state, lease_owner, lease_expires_at FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone()
    assert (state, owner, expiry) == ("unknown", None, None)


def test_user_confirmed_unknown_retry_requeues_job_and_linked_tasks(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1",)))
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='unknown', attempts=2, hy_invoked=1, "
            "last_error='hy_memory_timeout' WHERE job_id=?",
            (record.job_id,),
        )
        connection.executescript(
            """
            CREATE TABLE memory_tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                runner_token TEXT,
                attempts INTEGER NOT NULL,
                last_error_code TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE memory_task_inputs (
                task_id TEXT PRIMARY KEY,
                refs_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO memory_tasks VALUES (?,?,?,?,?,?)",
            ("task-1", "unknown", None, 2, "model_content_outcome_unknown", "old"),
        )
        connection.execute(
            "INSERT INTO memory_task_inputs VALUES (?,?)",
            ("task-1", '[{"evidence_id":"e1"}]'),
        )

    assert queue.retry_unknown_confirmed(record.job_id) == 1
    retried = queue.get(record.request_id)
    assert retried is not None
    assert retried.state == PENDING
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT attempts, hy_invoked FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone() == (0, 0)
        assert connection.execute(
            "SELECT status, attempts, last_error_code FROM memory_tasks WHERE task_id='task-1'"
        ).fetchone() == ("pending", 0, "model_content_manual_retry_requested")

    assert queue.retry_unknown_confirmed(record.job_id) == 0


def test_restart_redrains_a_job_claimed_by_a_worker_that_died(tmp_path) -> None:
    """End-to-end restart recovery: a claimed-but-not-run job runs once.

    A drain claims a row (`attempts=1`, no `hy_invoked`) and the process dies
    before executing. On restart the lease is expired, reclaim returns it to
    pending, and a fresh drain must execute it exactly once.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1",)))

    # First process: claim, then vanish without executing.
    first = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(seconds=1))
    first.claim(limit=1)
    _expire(path, record.job_id, hy_invoked=0)

    # Restart: opening the queue reclaims the dead worker's lease.
    reopened = DurableWriteQueue(path)
    assert reopened.get("idem-1").state == PENDING
    assert reopened.reclaim_expired() == 0

    seen: list[str] = []

    async def execute(job_id: str) -> None:
        seen.append(job_id)
        await execute_job(
            job_id,
            queue=reopened,
            supervisor=_Recorder(),
            evidence=_EvidenceStore({"e1"}),
        )

    async def scenario() -> None:
        drain = DurableWriteDrain(reopened, execute=execute, lease=timedelta(seconds=5))
        await drain.run_one()

    asyncio.run(scenario())

    assert seen == [record.job_id]
    assert reopened.get("idem-1").state == "completed"


async def _never_execute(job_id: str) -> None:
    raise AssertionError(f"first drain must not execute {job_id}")


def test_sqlite_drain_claims_renews_and_executes_a_job(tmp_path) -> None:
    """The durable queue needs a real SQLite worker, not only an executor.

    The MySQL `WriteQueuePipelines` path cannot claim this table under D56.
    This exercises the C2-owned drain: claim makes the row running under this
    owner, renewal extends that owner's lease, and the injected executor
    settles the row through the same authorized `execute_job` path.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1",)))
    seen: list[str] = []

    async def execute(job_id: str) -> None:
        seen.append(job_id)
        await execute_job(
            job_id,
            queue=queue,
            supervisor=_Recorder(),
            evidence=_EvidenceStore({"e1"}),
        )

    async def scenario() -> None:
        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(seconds=1),
            renew_every=timedelta(milliseconds=10),
        )
        await drain.run_one()

    asyncio.run(scenario())
    assert seen == [record.job_id]
    assert queue.get("idem-1").state == "completed"


def test_sqlite_claim_keeps_fifo_order_when_later_work_is_live(tmp_path) -> None:
    """Realtime priority must not move a later Harness job ahead of its head."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    normal = queue.submit(_request(evidence=("e1",), key="idem-normal"))
    live = queue.submit(_request(evidence=("e2",), key="idem-live"))
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET priority='live' WHERE job_id=?", (live.job_id,)
        )

    drain = DurableWriteDrain(queue, execute=_never_execute)
    assert drain.claim(limit=1) == (normal.job_id,)


def test_unknown_source_head_does_not_block_later_pending_work(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    uncertain = queue.submit(_request(evidence=("e1",), key="unknown"))
    later = queue.submit(_request(evidence=("e2",), key="later"))
    queue.settle(uncertain.job_id, state=UNKNOWN, hy_invoked=True)

    drain = DurableWriteDrain(queue, execute=_never_execute, owner="next-owner")

    assert drain.claim(limit=1) == (later.job_id,)
    assert queue.get("unknown").state == UNKNOWN


def test_sqlite_claim_orders_different_sources_by_enqueue_sequence(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    cursor = queue.submit(
        _request(key="cursor-live", source="cursor").model_copy(update={"priority": "live"})
    )
    workbuddy = queue.submit(
        _request(key="workbuddy-live", source="workbuddy").model_copy(update={"priority": "live"})
    )
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="fifo-owner")

    assert drain.claim(limit=1) == (cursor.job_id,)
    assert queue.get(workbuddy.request_id).state == PENDING


def test_live_claim_cannot_bypass_an_earlier_running_job(tmp_path) -> None:
    """Once the source head is running, later live work waits for it to finish."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    normal = queue.submit(_request(key="normal"))
    live = queue.submit(_request(key="live").model_copy(update={"priority": "live"}))
    second = DurableWriteDrain(queue, execute=_never_execute, owner="live-owner")

    # Put the older background row in the exact failure shape: it owns a
    # source lease but has not crossed the HY invocation boundary.
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='pending',lease_owner=NULL,"
            "lease_expires_at=NULL WHERE job_id=?",
            (live.job_id,),
        )
        connection.execute(
            "UPDATE memory_write_jobs SET state='running',lease_owner='normal-owner',"
            "lease_expires_at=?,hy_invoked=0 WHERE job_id=?",
            ((datetime.now(UTC) + timedelta(minutes=1)).isoformat(), normal.job_id),
        )
    assert second.claim(limit=1) == ()

    # The next live turn remains blocked until the earlier job settles.
    queue.settle(live.job_id, state=PENDING, owner="live-owner")
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET hy_invoked=1 WHERE job_id=?", (normal.job_id,)
        )
    assert second.claim(limit=1) == ()


def test_claim_serializes_one_session_across_two_drains(tmp_path) -> None:
    """Two drains must not run two turns of one session at once.

    Harness serialism is a correctness rule: concurrent turns of a session can
    each conclude without seeing the other, and the second can overwrite a
    conclusion the first had not finished. The claim can only enforce this if
    it excludes a session that already holds a live lease.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    first = queue.submit(_request(evidence=("e1",), key="idem-1"))
    second = queue.submit(_request(evidence=("e2",), key="idem-2"))

    drain_a = DurableWriteDrain(queue, execute=_never_execute, owner="drain-a")
    drain_b = DurableWriteDrain(queue, execute=_never_execute, owner="drain-b")

    assert drain_a.claim(limit=2) == (first.job_id,)
    # The same session is already leased, so nothing else is claimable.
    assert drain_b.claim(limit=2) == ()

    # Once the first lease ends, the second turn becomes claimable.
    queue.settle(first.job_id, state="completed")
    assert drain_b.claim(limit=2) == (second.job_id,)


def test_claim_allows_different_sessions_to_run_in_parallel(tmp_path) -> None:
    """Serialisation is per session, not global."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    first = queue.submit(_request(evidence=("e1",), key="idem-1"))
    other_session = WriteRequest(
        internal_session_id="sess-2",
        batch_identity=BatchIdentity(internal_session_id="sess-2", first_round=1, last_round=1),
        evidence_ids=["e2"],
        idempotency_key="idem-2",
    )
    second = queue.submit(other_session)

    drain_a = DurableWriteDrain(queue, execute=_never_execute, owner="drain-a")
    drain_b = DurableWriteDrain(queue, execute=_never_execute, owner="drain-b")

    assert drain_a.claim(limit=1) == (first.job_id,)
    assert drain_b.claim(limit=1) == (second.job_id,)


def test_forever_drain_admits_a_new_source_while_an_earlier_source_is_running(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    first = queue.submit(_request(key="cursor-1").model_copy(update={"source_id": "cursor"}))
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()

    async def execute(job_id: str) -> None:
        if job_id == first.job_id:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        queue.settle(job_id, state=COMPLETED)

    async def scenario() -> None:
        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(seconds=5),
            max_concurrent_sources=2,
        )
        worker = asyncio.create_task(drain.run_forever(idle_pause=0.005))
        await asyncio.wait_for(first_started.wait(), 1)
        queue.submit(
            WriteRequest(
                internal_session_id="workbuddy-session",
                source_id="workbuddy",
                batch_identity=BatchIdentity(
                    internal_session_id="workbuddy-session", first_round=1, last_round=1
                ),
                evidence_ids=["e2"],
                idempotency_key="workbuddy-1",
                priority="live",
            )
        )
        try:
            await asyncio.wait_for(second_started.wait(), 0.25)
        finally:
            release_first.set()
            drain.stop()
            await asyncio.wait_for(worker, 1)

    asyncio.run(scenario())
    assert queue.get("cursor-1").state == COMPLETED
    assert queue.get("workbuddy-1").state == COMPLETED


def test_sqlite_drain_preserves_unknown_after_execute_job(tmp_path) -> None:
    """A drain failure after invocation must remain unknown, not look retryable."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request(evidence=("e1",)))

    async def execute(job_id: str) -> None:
        await execute_job(
            job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError("model_content_outcome_unknown")),
            evidence=_EvidenceStore({"e1"}),
        )

    async def scenario() -> None:
        drain = DurableWriteDrain(queue, execute=execute)
        assert await drain.run_one() == 1

    asyncio.run(scenario())
    assert queue.get("idem-1").state == "unknown"
    assert queue.reclaim_expired() == 0
    assert queue.get("idem-1").state == "unknown"


def test_sqlite_drain_keeps_running_after_an_executor_raises(tmp_path) -> None:
    """One executor exception must not kill the drain or strand other rows.

    `execute_job` handles its own failures, so an exception reaching the drain
    is unexpected. The worker still has to settle that row from the durable
    marker and keep going, or every later pending job starves.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    first = queue.submit(_request(evidence=("e1",)))
    # Different sessions on purpose: one session is serial (claim takes one
    # job), so fault isolation is shown with two independent harnesses.
    second = queue.submit(
        WriteRequest(
            internal_session_id="sess-2",
            batch_identity=BatchIdentity(internal_session_id="sess-2", first_round=1, last_round=1),
            evidence_ids=["e2"],
            idempotency_key="idem-2",
        )
    )

    seen: list[str] = []

    async def execute(job_id: str) -> None:
        seen.append(job_id)
        if job_id == first.job_id:
            raise RuntimeError("unexpected executor bug")
        queue.settle(job_id, state="completed")

    async def scenario() -> None:
        drain = DurableWriteDrain(queue, execute=execute, lease=timedelta(seconds=5))
        await drain.run_one(limit=2)

    asyncio.run(scenario())

    assert set(seen) == {first.job_id, second.job_id}
    assert queue.get("idem-1").state == PENDING
    assert queue.get("idem-2").state == "completed"


def test_a_batched_claim_renews_every_parallel_job(tmp_path) -> None:
    """Every concurrently executing source owns and renews its own lease."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    first = queue.submit(_request(evidence=("e1",), key="idem-1"))
    second = queue.submit(
        WriteRequest(
            internal_session_id="sess-2",
            batch_identity=BatchIdentity(internal_session_id="sess-2", first_round=1, last_round=1),
            evidence_ids=["e2"],
            idempotency_key="idem-2",
        )
    )

    seen: list[str] = []
    release_first = asyncio.Event()
    second_executed_by_second = asyncio.Event()

    async def first_drain_execute(job_id: str) -> None:
        seen.append(job_id)
        if job_id == first.job_id:
            await release_first.wait()
        queue.settle(job_id, state=COMPLETED)

    async def second_drain_execute(job_id: str) -> None:
        seen.append(f"second:{job_id}")
        queue.settle(job_id, state=COMPLETED)
        second_executed_by_second.set()

    async def scenario() -> None:
        first_drain = DurableWriteDrain(
            queue,
            execute=first_drain_execute,
            owner="drain-a",
            lease=timedelta(milliseconds=200),
            renew_every=timedelta(milliseconds=20),
        )
        second_drain = DurableWriteDrain(
            queue,
            execute=second_drain_execute,
            owner="drain-b",
            lease=timedelta(seconds=5),
        )
        running = asyncio.create_task(first_drain.run_one(limit=2))

        # Both source jobs start together and each receives a renewal loop.
        for _ in range(100):
            await asyncio.sleep(0.01)
            if first.job_id in seen:
                break
        assert first.job_id in seen
        await asyncio.sleep(0.25)
        assert queue.reclaim_expired() == 0
        assert second_drain.claim(limit=1) == ()
        assert not second_executed_by_second.is_set()
        release_first.set()
        await running

    asyncio.run(scenario())

    assert seen.count(second.job_id) == 1
    assert f"second:{second.job_id}" not in seen


def test_worker_error_settlement_is_conservative_about_hy(tmp_path) -> None:
    """Unexpected errors never guess that HY was skipped."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    sent = queue.submit(_request(evidence=("e1",)))
    unsent = queue.submit(_request(evidence=("e2",), key="idem-2"))

    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', hy_invoked=1, attempts=1 WHERE job_id=?",
            (sent.job_id,),
        )
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', hy_invoked=0, attempts=1 WHERE job_id=?",
            (unsent.job_id,),
        )

    assert queue.settle_worker_error(sent.job_id, error_code="boom") == UNKNOWN
    assert queue.settle_worker_error(unsent.job_id, error_code="boom") == PENDING


def test_worker_error_settlement_dead_letters_exhausted_uninvoked_job(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', hy_invoked=0, "
            "attempts=max_attempts WHERE job_id=?",
            (record.job_id,),
        )

    assert queue.settle_worker_error(record.job_id, error_code="boom") == DEAD


def test_worker_error_settlement_leaves_a_terminal_row_alone(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state="completed")

    assert queue.settle_worker_error(record.job_id, error_code="boom") == "completed"
    assert queue.get("idem-1").state == "completed"


def test_worker_error_settlement_does_not_claim_an_ownerless_row(tmp_path) -> None:
    """The returned state must describe what was actually written.

    A `running` row with no lease owner is an orphan, not this worker's job.
    The owner guard used to accept `lease_owner IS NULL` in Python while the
    SQL update required `lease_owner = ?`; the update then matched zero rows,
    but the method still returned the state it had computed as if it wrote it.
    The caller was told the orphan was settled while it stayed `running`.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', lease_owner=NULL, "
            "lease_expires_at=NULL, hy_invoked=0, attempts=0 WHERE job_id=?",
            (record.job_id,),
        )

    assert queue.settle_worker_error(record.job_id, error_code="boom", owner="owner-a") == RUNNING
    assert queue.get("idem-1").state == RUNNING

    # The ownerless row is recovered by reclaim, not by a worker guessing.
    assert queue.reclaim_expired() == 1
    assert queue.get("idem-1").state == PENDING


def test_settle_with_an_owner_does_not_touch_an_ownerless_row(tmp_path, caplog) -> None:
    """`settle`'s Python guard must agree with its SQL guard.

    Like `settle_worker_error`, `settle` accepted a NULL `lease_owner` at the
    Python layer while the UPDATE required `lease_owner = ?`. An ownerless
    orphan must be left for reclaim; a worker must not silently no-op and let
    its caller believe the outcome was recorded. The DB state is the same
    either way, so the discriminating observation is the sanitised warning:
    the buggy version early-returns without ever attempting the guarded UPDATE.
    """
    import logging

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', lease_owner=NULL, "
            "lease_expires_at=NULL WHERE job_id=?",
            (record.job_id,),
        )

    with caplog.at_level(logging.WARNING, logger="one_memory.memory_server.durable_queue"):
        queue.settle(record.job_id, state=FAILED, error_code="boom", owner="owner-a")

    assert queue.get("idem-1").state == RUNNING
    messages = [r.getMessage() for r in caplog.records]
    assert any("durable_write_settle_rejected_stale_owner" in message for message in messages), (
        messages
    )
    assert not any("durable_write_settle_missed_row" in message for message in messages), messages


def test_terminal_payloads_are_reaped_but_replay_still_detects_conflicts(tmp_path) -> None:
    """Payload cleanup must not weaken idempotency.

    Once a job is terminal the serialized request is dead weight, so it is
    replaced by a digest. Replaying the same intent must still resolve to the
    same row, and the same key with different content must still be refused.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=("e1",)))
    queue.settle(record.job_id, state="completed")

    assert queue.clear_terminal_payloads() == 1
    stored = _rows(path)[0]["payload_json"]
    assert "e1" not in stored and "_one_memory_reaped_payload" in stored
    assert _rows(path)[0]["evidence_ids_json"] == '["e1"]'

    # Same intent still resolves to the existing row, not a second write.
    replay = queue.submit(_request(evidence=("e1",)))
    assert replay.enqueued is False
    assert replay.job_id == record.job_id
    assert len(_rows(path)) == 1

    # Same key, different content is still a conflict.
    with pytest.raises(ValueError, match="different content"):
        queue.submit(_request(evidence=("e2",)))


def test_terminal_payload_reaping_is_bounded_and_idempotent(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    for index in range(3):
        record = queue.submit(_request(evidence=("e1",), key=f"idem-{index}"))
        queue.settle(record.job_id, state="dead")

    assert queue.clear_terminal_payloads(limit=2) == 2
    assert queue.clear_terminal_payloads(limit=2) == 1
    assert queue.clear_terminal_payloads(limit=2) == 0


def test_unknown_and_pending_payloads_are_never_reaped(tmp_path) -> None:
    """Non-terminal rows keep their intent for retry and reconciliation."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    pending = queue.submit(_request(evidence=("e1",)))
    unknown = queue.submit(_request(evidence=("e2",), key="idem-2"))
    queue.settle(unknown.job_id, state="unknown")

    assert queue.clear_terminal_payloads() == 0

    payloads = {row["idempotency_key"]: row["payload_json"] for row in _rows(path)}
    assert "e1" in payloads[pending.request_id]
    assert "e2" in payloads[unknown.request_id]


def test_reaped_terminal_payload_does_not_reappear_as_an_unsubmitted_orphan(tmp_path) -> None:
    """Payload cleanup must not make a completed Evidence look unqueued.

    The reconcile primitive scans the durable queue by Evidence id. If it
    derived that set from `payload_json`, reaping a terminal row would erase
    the ids and the completed write would be submitted again on the next
    reconcile pass, risking a duplicate HY call.
    """
    from datetime import UTC, datetime

    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    evidence = store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="m1",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="private text",
            created_at=datetime.now(UTC),
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(evidence=(evidence.evidence_id,), key="idem-reaped"))
    queue.settle(record.job_id, state="completed")
    assert queue.clear_terminal_payloads() == 1
    assert queue.unsubmitted_evidence_ids() == ()

    orphan = store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="m2",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="orphan text",
            created_at=datetime.now(UTC),
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    assert queue.unsubmitted_evidence_ids() == (orphan.evidence_id,)


def test_payload_for_a_reaped_terminal_row_returns_none(tmp_path) -> None:
    """A stale claim of a reaped row must not resurrect a dead intent."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state="completed")
    queue.clear_terminal_payloads()

    assert queue.payload_for(record.job_id) is None


def test_drain_reaps_terminal_payloads_when_idle(tmp_path) -> None:
    """The reaper must actually run in the worker, not only in tests."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state="completed")
    executed: list[str] = []

    async def execute(job_id: str) -> None:
        executed.append(job_id)

    async def scenario() -> None:
        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(seconds=1),
            reap_interval=timedelta(0),
        )
        task = asyncio.create_task(drain.run_forever(idle_pause=0.02))
        await asyncio.sleep(0.2)
        drain.stop()
        await task

    asyncio.run(scenario())

    assert executed == []
    assert "_one_memory_reaped_payload" in _rows(path)[0]["payload_json"]


def test_the_drain_reaps_terminal_payloads_even_while_never_idle(tmp_path) -> None:
    """Terminal payloads must be reaped even if the queue never goes idle.

    Regression: `clear_terminal_payloads` ran only on the `executed == 0` idle
    branch, so a sustained backlog kept every finished row's full serialized
    request body (which holds the written evidence references) in the business
    database indefinitely — a disk/privacy-retention invariant starved exactly
    when the queue is busiest. Reaping is a bounded maintenance write and must
    be gated by its own interval, not by idleness.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    finished = queue.submit(_request(key="finished"))
    queue.settle(finished.job_id, state=COMPLETED)
    for index in range(4000):
        queue.submit(_request(key=f"backlog-{index}"))

    async def scenario() -> None:
        async def execute(job_id: str) -> None:
            await asyncio.sleep(0.001)
            queue.settle(job_id, state=COMPLETED)

        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(seconds=5),
            reap_interval=timedelta(0),
        )
        task = asyncio.create_task(drain.run_forever(idle_pause=0.01))
        await asyncio.sleep(1.0)
        drain.stop()
        await task

    asyncio.run(scenario())

    payload = _rows(path)
    by_key = {row["idempotency_key"]: row["payload_json"] for row in payload}
    assert "_one_memory_reaped_payload" in by_key["finished"], (
        "a completed row's payload must be reaped even under a standing backlog"
    )


def test_the_drain_readmits_model_waiters_even_while_never_idle(tmp_path) -> None:
    """`waiting_for_model` jobs must be re-admitted even under a backlog.

    Same regression shape as the reclaim/reap cases: `readmit_model_waiters`
    ran only on the idle branch, so a queue that never went idle (or was busy
    for longer than the model-wait window) never re-admitted a `waiting_for_model`
    job after its wait elapsed — the model could be healthy again while the
    write stayed parked indefinitely.
    """
    from datetime import UTC, datetime, timedelta

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    parked = queue.submit(_request(key="model-waiter"))
    queue.settle(parked.job_id, state=WAITING_FOR_MODEL, hy_invoked=False)
    # Backdate so the 1-minute re-admission threshold is already satisfied.
    old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET updated_at=? WHERE idempotency_key='model-waiter'",
            (old,),
        )
    for index in range(4000):
        queue.submit(_request(key=f"backlog-{index}"))

    async def scenario() -> None:
        async def execute(job_id: str) -> None:
            await asyncio.sleep(0.001)
            queue.settle(job_id, state=COMPLETED)

        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(seconds=5),
            reap_interval=timedelta(0),
        )
        task = asyncio.create_task(drain.run_forever(idle_pause=0.01))
        for _ in range(150):
            await asyncio.sleep(0.02)
            if queue.get("model-waiter").state != WAITING_FOR_MODEL:
                break
        drain.stop()
        await task

    asyncio.run(scenario())

    assert queue.get("model-waiter").state != WAITING_FOR_MODEL, (
        "a waiting_for_model job must be re-admitted even under a standing backlog"
    )


def test_the_drain_does_not_block_the_loop_on_a_slow_writer(tmp_path, monkeypatch) -> None:
    """Claim/settle/renew are blocking SQLite calls and must run off the loop.

    A busy writer holds the single write connection; running `claim` on the
    loop would freeze health checks and the API for the whole wait. The drain
    hands the call to a thread, so a deliberately slow claim must not stop
    other coroutines from making progress.
    """
    import time

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request())
    drain = DurableWriteDrain(queue, execute=_never_execute)

    real_claim = drain.claim

    def slow_claim(**kwargs):
        time.sleep(0.3)
        return real_claim(**kwargs)

    monkeypatch.setattr(drain, "claim", slow_claim)

    async def scenario() -> bool:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            await drain.run_one()
        finally:
            ticker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker_task
        return ticks > 3

    assert asyncio.run(scenario()) is True


def test_drain_budget_counts_cleanup_reserve_once() -> None:
    from one_memory.memory_server.durable_queue import DrainBudget, derive_drain_budget

    handle = timedelta(seconds=30)
    reserve = timedelta(seconds=5)
    budget = derive_drain_budget(
        handle_budget=handle,
        cleanup_reserve=reserve,
        renew_every=timedelta(seconds=2),
        claim_budget_ms=250,
        lease_ttl=timedelta(seconds=60),
    )

    assert isinstance(budget, DrainBudget)
    assert budget.drain_budget == handle + reserve
    assert budget.outer_timeout == handle + reserve
    assert budget.drain_work_window == handle
    assert budget.work_deadline(100.0) == 130.0
    assert budget.deadline(100.0) == 135.0
    assert budget.can_start_work(99.0, 100.0) is True
    assert budget.can_start_work(100.1, 100.0) is False


def test_production_drain_budget_is_single_source_and_obeys_contract() -> None:
    from one_memory.memory_server.durable_queue import (
        PRODUCTION_DRAIN_BUDGET,
        PRODUCTION_DRAIN_CLAIM_BUDGET_MS,
        PRODUCTION_DRAIN_CLEANUP_RESERVE,
        PRODUCTION_DRAIN_HANDLE_BUDGET,
        PRODUCTION_DRAIN_LEASE_TTL,
        PRODUCTION_DRAIN_RENEW_EVERY,
        production_drain_budget,
    )

    budget = production_drain_budget()
    assert budget.handle_budget == PRODUCTION_DRAIN_HANDLE_BUDGET
    assert budget.cleanup_reserve == PRODUCTION_DRAIN_CLEANUP_RESERVE
    assert budget.drain_budget == PRODUCTION_DRAIN_BUDGET
    assert budget.lease_ttl == PRODUCTION_DRAIN_LEASE_TTL
    assert budget.renew_every == PRODUCTION_DRAIN_RENEW_EVERY
    assert budget.claim_budget_ms == PRODUCTION_DRAIN_CLAIM_BUDGET_MS

    # Contract §2: the reserve is counted once and is not re-added to outer.
    assert budget.work_window == PRODUCTION_DRAIN_HANDLE_BUDGET
    assert budget.work_ceiling == PRODUCTION_DRAIN_HANDLE_BUDGET
    assert budget.work_ceiling < budget.lease_ttl
    assert budget.outer_timeout == PRODUCTION_DRAIN_BUDGET
    assert budget.lease_ttl > budget.handle_budget + budget.renew_every
    assert budget.renew_every <= budget.lease_ttl / 3
    assert budget.claim_budget_ms <= (budget.claim_budget.total_seconds() * 1000)
    assert budget.claim_budget_ms > 0

    # Claim waits in the same FIFO as every other short write. A 250ms override
    # turned ordinary import transactions into noisy retry failures.
    assert budget.claim_budget_ms == 5_000


def test_drain_budget_work_window_is_total_minus_reserve_not_handle() -> None:
    """A larger drain budget must admit multiple bounded work units."""
    from one_memory.memory_server.durable_queue import derive_drain_budget

    budget = derive_drain_budget(
        handle_budget=timedelta(seconds=30),
        drain_budget=timedelta(minutes=10),
        cleanup_reserve=timedelta(seconds=5),
        renew_every=timedelta(seconds=10),
        claim_budget_ms=250,
        lease_ttl=timedelta(minutes=3),
    )
    assert budget.work_window == timedelta(seconds=595)
    assert budget.work_deadline(100.0) == 695.0
    assert budget.deadline(100.0) == 700.0


def test_production_budget_worker_keeps_draining_after_one_window(tmp_path) -> None:
    """The perpetual drain must not exit after its first budget window.

    Regression for a P0 introduced when C0 wired `production_drain_budget()`
    into the composition root: `work_window == handle_budget == 5s` gives the
    cycle zero slack, so the admission check `now + handle_budget >
    work_deadline` was already true on the first tick and `run_forever`
    returned before claiming anything. `api.py` starts this worker once and
    stops it only at shutdown, so every job submitted after that point stayed
    `pending` forever.

    The worker must bound one *cycle*, not its lifetime: it closes the window
    and schedules a fresh one instead of returning.
    """
    from one_memory.memory_server.durable_queue import production_drain_budget

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    budget = production_drain_budget()
    executed: list[str] = []

    async def execute(job_id: str) -> None:
        executed.append(job_id)
        queue.settle(job_id, state=COMPLETED)

    async def scenario() -> None:
        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=budget.lease_ttl,
            renew_every=budget.renew_every,
            claim_budget_ms=budget.claim_budget_ms,
            drain_handle_budget=budget.handle_budget,
            drain_cleanup_reserve=budget.cleanup_reserve,
        )
        # Production wiring passes the same total so the window is exactly one
        # handle budget with zero slack.
        assert drain.budget is not None
        assert drain.budget.work_window == drain.budget.handle_budget

        # Job submitted *after* the loop has started, i.e. in the steady state
        # the production worker actually serves.
        task = asyncio.create_task(drain.run_forever(idle_pause=0.01))
        await asyncio.sleep(0.05)
        assert not task.done(), "the perpetual drain exited before any work arrived"
        record = queue.submit(_request(key="after-window"))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if queue.get(record.request_id).state == COMPLETED:
                break
        drain.stop()
        await task

    asyncio.run(scenario())

    assert executed, "the drain never claimed the submitted job"
    assert queue.get("after-window").state == COMPLETED


def test_drain_budget_validates_reserve_before_derived_total() -> None:
    from one_memory.memory_server.durable_queue import derive_drain_budget

    with pytest.raises(ValueError, match="cleanup_reserve"):
        derive_drain_budget(
            handle_budget=timedelta(seconds=30),
            drain_budget=timedelta(seconds=20),
            cleanup_reserve=timedelta(0),
            renew_every=timedelta(seconds=10),
            claim_budget_ms=250,
            lease_ttl=timedelta(minutes=3),
        )


def test_drain_budget_rejects_lease_shorter_than_work_plus_renewal() -> None:
    from one_memory.memory_server.durable_queue import derive_drain_budget

    with pytest.raises(ValueError, match="lease_ttl"):
        derive_drain_budget(
            handle_budget=timedelta(seconds=30),
            cleanup_reserve=timedelta(seconds=5),
            renew_every=timedelta(seconds=10),
            claim_budget_ms=250,
            lease_ttl=timedelta(seconds=35),
        )


def test_drain_stops_admitting_before_the_cleanup_reserve(tmp_path) -> None:
    """The deadline is checked against the work window, not deadline again."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    drain = DurableWriteDrain(
        queue,
        execute=_never_execute,
        drain_handle_budget=timedelta(seconds=2),
        drain_cleanup_reserve=timedelta(seconds=1),
        lease=timedelta(seconds=10),
    )
    assert drain.budget is not None
    assert drain.budget.drain_work_window == timedelta(seconds=2)
    assert drain.budget.cleanup_reserve == timedelta(seconds=1)
    assert drain.budget.outer_timeout == timedelta(seconds=3)
    assert drain.budget.can_start_work(0.0, 0.0) is True
    assert drain.budget.can_start_work(0.1, 0.0) is False


def test_an_empty_queue_claim_does_not_take_the_writer(tmp_path) -> None:
    """An empty queue must not contend for the single writer.

    Observed on the R5.6 full-corpus run: with automatic processing off and
    `memory_write_jobs` empty, the drain's 250ms claim was refused 2000+ times
    against the import, purely to discover there was nothing to claim. The
    claim now checks the reader pool first (WAL lets that read proceed while the
    writer is held) and returns without opening a write transaction when no
    pending row exists.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    drain = DurableWriteDrain(queue, execute=_never_execute, claim_budget_ms=250)
    release = threading.Event()
    held = threading.Event()

    def hold_writer() -> None:
        with main_database(path).write() as writer, writer:
            writer.execute("BEGIN IMMEDIATE")
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold_writer, daemon=True)
    holder.start()
    assert held.wait(2)
    started = time.monotonic()
    try:
        # No pending row exists, so this returns () and never waits on the writer.
        assert drain.claim(limit=1, budget_ms=250) == ()
    finally:
        release.set()
        holder.join(timeout=5)
    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms < 100, f"empty-queue claim waited {elapsed_ms:.1f}ms on the writer"


def test_drain_claim_uses_its_own_short_budget(tmp_path) -> None:
    """Background admission must not consume the whole 5s writer budget.

    A held writer is exactly the H92 window: the tail metadata write has only
    250ms, so a claim with the global 5000ms budget cannot be the first thing
    to keep the writer.

    The claim now also has a reader-side gate: an EMPTY queue returns without
    touching the writer at all, so this test seeds a pending row to force the
    write path and measure its budget.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request())
    drain = DurableWriteDrain(queue, execute=_never_execute, claim_budget_ms=100)
    release = threading.Event()
    held = threading.Event()

    def hold_writer() -> None:
        with main_database(path).write() as writer, writer:
            writer.execute("BEGIN IMMEDIATE")
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold_writer, daemon=True)
    holder.start()
    assert held.wait(2)
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError):
            asyncio.run(drain.run_one(limit=1))
    finally:
        release.set()
        holder.join(timeout=5)
    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms < 1000, f"claim waited {elapsed_ms:.1f}ms"


def test_production_claim_waits_through_a_short_import_transaction(tmp_path) -> None:
    """A normal import write must not make the production drain log a lock failure."""
    from one_memory.memory_server.durable_queue import production_drain_budget

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request())
    release = threading.Event()
    held = threading.Event()

    def hold_writer() -> None:
        with main_database(path).write() as writer, writer:
            writer.execute("BEGIN IMMEDIATE")
            held.set()
            release.wait(2)

    holder = threading.Thread(target=hold_writer, daemon=True)
    holder.start()
    assert held.wait(2)
    timer = threading.Timer(0.4, release.set)
    timer.start()
    started = time.monotonic()
    try:
        claimed = DurableWriteDrain(
            queue,
            execute=_never_execute,
            claim_budget_ms=production_drain_budget().claim_budget_ms,
        ).claim(limit=1, budget_ms=production_drain_budget().claim_budget_ms)
    finally:
        release.set()
        timer.cancel()
        holder.join(timeout=2)

    elapsed = time.monotonic() - started
    assert len(claimed) == 1
    assert 0.3 <= elapsed < 2


def test_claim_budget_ab_against_a_held_writer(tmp_path) -> None:
    """A/B: global 5000ms claim vs the 100ms drain budget.

    A pending row is seeded so the claim reaches the writer; the reader-side
    gate would otherwise return immediately on an empty queue without measuring
    either budget.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request())

    def run_with_budget(budget_ms: int) -> tuple[float, str | None]:
        release = threading.Event()
        held = threading.Event()

        def hold_writer() -> None:
            with main_database(path).write() as writer, writer:
                writer.execute("BEGIN IMMEDIATE")
                held.set()
                release.wait(8)

        holder = threading.Thread(target=hold_writer, daemon=True)
        holder.start()
        assert held.wait(2)
        started = time.monotonic()
        error_code = None
        try:
            DurableWriteDrain(queue, execute=_never_execute, claim_budget_ms=budget_ms).claim(
                limit=1, budget_ms=budget_ms
            )
        except sqlite3.OperationalError as error:
            error_code = str(error)
        finally:
            release.set()
            holder.join(timeout=8)
        return (time.monotonic() - started) * 1000, error_code

    old_elapsed, old_error = run_with_budget(5000)
    new_elapsed, new_error = run_with_budget(100)
    print(
        "\n"
        + json.dumps(
            {
                "old_budget_ms": 5000,
                "old_elapsed_ms": old_elapsed,
                "old_error": old_error,
                "new_budget_ms": 100,
                "new_elapsed_ms": new_elapsed,
                "new_error": new_error,
            },
            sort_keys=True,
        )
    )
    assert old_elapsed > new_elapsed
    assert new_error is not None


def test_idle_maintenance_does_not_write_every_poll(tmp_path, monkeypatch) -> None:
    """An empty queue must not issue a write per idle poll.

    `clear_terminal_payloads` and `readmit_model_waiters` both write. On an
    empty queue the loop wakes every `idle_pause`; running them each time would
    contend with imports for the single writer for no benefit. They share a
    coarse interval gate, so a 20-poll window performs at most a couple of
    writes.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    drain = DurableWriteDrain(queue, execute=_never_execute)

    reaps = 0
    readmits = 0
    real_reap = queue.clear_terminal_payloads
    real_readmit = queue.readmit_model_waiters

    def counted_reap(*args, **kwargs):
        nonlocal reaps
        reaps += 1
        return real_reap(*args, **kwargs)

    def counted_readmit(*args, **kwargs):
        nonlocal readmits
        readmits += 1
        return real_readmit(*args, **kwargs)

    monkeypatch.setattr(queue, "clear_terminal_payloads", counted_reap)
    monkeypatch.setattr(queue, "readmit_model_waiters", counted_readmit)
    monkeypatch.setattr(drain, "claim", lambda **_kw: ())

    async def scenario() -> None:
        task = asyncio.create_task(drain.run_forever(idle_pause=0.005))
        await asyncio.sleep(0.2)
        drain.stop()
        await task

    asyncio.run(scenario())

    # Well over 20 polls happened; maintenance ran at most once (the interval
    # gate), not once per poll.
    assert reaps <= 1
    assert readmits <= 1


def test_idle_drain_backs_off_before_hitting_the_single_writer(tmp_path, monkeypatch) -> None:
    """An empty queue must not claim every 50ms forever.

    A 1m Evidence table makes an idle claim miss expensive enough to sit in
    front of the tail call-event write. Backoff is only for an idle queue:
    the next successful claim resets it, so live work is not delayed.
    """
    path = tmp_path / "one-memory.sqlite3"
    drain = DurableWriteDrain(DurableWriteQueue(path), execute=_never_execute)
    claims = 0

    def counted_claim(**_kwargs):
        nonlocal claims
        claims += 1
        return ()

    monkeypatch.setattr(drain, "claim", counted_claim)

    async def scenario() -> None:
        task = asyncio.create_task(drain.run_forever(idle_pause=0.05))
        await asyncio.sleep(0.6)
        drain.stop()
        await task

    asyncio.run(scenario())

    assert claims < 8, f"idle drain claimed {claims} times in 0.6s"


def test_idle_backoff_ab_claims_and_resets_on_work(tmp_path, monkeypatch) -> None:
    """A/B shape for H92: old 50ms polling vs idle backoff.

    Prints raw JSON so the scheduler decision can be made from numbers, not
    from the implementation story. The successful-claim half proves backoff
    resets when there is real work.
    """
    path = tmp_path / "one-memory.sqlite3"
    old = DurableWriteDrain(DurableWriteQueue(path), execute=_never_execute)
    new = DurableWriteDrain(DurableWriteQueue(path), execute=_never_execute)
    old_claims = 0
    new_claims = 0

    def old_claim(**_kwargs):
        nonlocal old_claims
        old_claims += 1
        old._idle_streak = 0  # emulate pre-backoff polling
        return ()

    def new_claim(**_kwargs):
        nonlocal new_claims
        new_claims += 1
        return ()

    monkeypatch.setattr(old, "claim", old_claim)
    monkeypatch.setattr(new, "claim", new_claim)

    async def measure() -> tuple[int, int]:
        old_task = asyncio.create_task(old.run_forever(idle_pause=0.05))
        new_task = asyncio.create_task(new.run_forever(idle_pause=0.05))
        await asyncio.sleep(0.6)
        old.stop()
        new.stop()
        await old_task
        await new_task
        return old_claims, new_claims

    before, after = asyncio.run(measure())
    print(
        "\n"
        + json.dumps(
            {
                "window_s": 0.6,
                "pre_backoff_claims": before,
                "candidate_claims": after,
                "claim_reduction": before - after,
            },
            sort_keys=True,
        )
    )
    assert before > after

    async def reset_on_work() -> None:
        drain = DurableWriteDrain(DurableWriteQueue(path), execute=_never_execute)
        drain._idle_streak = 3
        claimed = False

        def claim(**_kwargs):
            nonlocal claimed
            if claimed:
                return ()
            claimed = True
            return ("synthetic-job",)

        async def execute(_job_id: str) -> None:
            drain.stop()

        monkeypatch.setattr(drain, "claim", claim)
        monkeypatch.setattr(drain, "_run_claimed_job", execute)
        task = asyncio.create_task(drain.run_forever(idle_pause=0.01))
        await task
        assert drain._idle_streak == 0

    asyncio.run(reset_on_work())


def test_idle_maintenance_database_failure_is_sampled_without_database_error(
    tmp_path, monkeypatch
) -> None:
    """A lock during idle maintenance must reach the database error counter.

    The drain suppresses maintenance exceptions so one transient failure does
    not kill the worker, but swallowing a persistent `database is locked`
    would make an import peak that starves queue maintenance look like a
    healthy idle loop. Count the SQLite failure, and do not count unrelated
    programming errors as database faults.
    """
    path = tmp_path / "one-memory.sqlite3"
    drain = DurableWriteDrain(DurableWriteQueue(path), execute=_never_execute)
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "one_memory.memory_server.durable_queue.diagnostic_event",
        lambda **event: events.append(event),
    )

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(drain.queue, "clear_terminal_payloads", locked)
    monkeypatch.setattr(drain, "claim", lambda **_kw: ())

    async def scenario() -> None:
        task = asyncio.create_task(drain.run_forever(idle_pause=0.005))
        await asyncio.sleep(0.1)
        drain.stop()
        await task

    asyncio.run(scenario())

    assert [event["event"] for event in events] == ["memory.write_queue.retry_scheduled"]

    def programming_error(*_args, **_kwargs):
        raise RuntimeError("maintenance bug")

    monkeypatch.setattr(drain.queue, "clear_terminal_payloads", programming_error)
    drain._last_terminal_reap = None  # noqa: SLF001 - force the next maintenance tick

    asyncio.run(scenario())

    assert events[-1]["error_type"] == "RuntimeError"


def test_an_unknown_is_retired_only_by_an_inquiry(tmp_path) -> None:
    """`unknown` must be resolvable, but never by a blind retry.

    A crash between HY's success and our recording leaves `unknown`. A0-03
    measured the SDK can look the result up by request id, so an inquiry may
    close it either way; anything that instead made it `pending` would re-send
    the write and duplicate the memory.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state="unknown", error_code="lease lapsed", hy_invoked=True)

    # The inquiry found the write in HY: it is complete, not retried.
    assert queue.resolve_unknown(record.job_id, found=True, detail="found in HY") == "completed"
    assert queue.get("idem-1").state == "completed"

    # A second resolution is a no-op, not a state change.
    assert queue.resolve_unknown(record.job_id, found=False) is None
    assert queue.get("idem-1").state == "completed"


def test_an_unknown_the_inquiry_cannot_find_gives_up(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state="unknown", hy_invoked=True)

    assert queue.resolve_unknown(record.job_id, found=False, detail="not found") == "dead"
    assert queue.get("idem-1").detail == "not found"

    # And an unknown is never driven back into `pending`.
    assert queue.get("idem-1").state == "dead"

    no_detail = queue.submit(_request(evidence=("e2",), key="idem-2"))
    queue.settle(no_detail.job_id, state="unknown", hy_invoked=True)
    assert queue.resolve_unknown(no_detail.job_id, found=False) == "dead"
    assert queue.get("idem-2").detail == "model_content_outcome_not_found"


def test_resolving_a_non_unknown_row_is_refused(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())

    assert queue.resolve_unknown(record.job_id, found=True) is None
    assert queue.get("idem-1").state == PENDING


def test_unknown_jobs_lists_only_unknown_rows_with_a_stable_cursor(tmp_path) -> None:
    """A Runtime needs discovery before it can ask HY; retrying is never how.

    `unknown` is non-terminal but not claimable. A reconcile pass must be able
    to enumerate the rows and their request ids, page without a full scan, and
    leave every row untouched until the caller has an inquiry result.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    unknown_ids = []
    for index in range(3):
        record = queue.submit(_request(evidence=(f"e{index}",), key=f"unknown-{index}"))
        queue.settle(record.job_id, state=UNKNOWN, hy_invoked=True)
        unknown_ids.append(record.job_id)
    queue.submit(_request(evidence=("pending",), key="pending-1"))

    with sqlite3.connect(path) as connection, connection:
        for offset, job_id in enumerate(unknown_ids):
            connection.execute(
                "UPDATE memory_write_jobs SET updated_at=? WHERE job_id=?",
                (f"2026-01-01T00:00:0{offset}+00:00", job_id),
            )

    first = queue.unknown_jobs(limit=2)
    second = queue.unknown_jobs(
        limit=2,
        after_cursor=(first[-1].updated_at, first[-1].job_id),
    )

    assert [item.job_id for item in (*first, *second)] == unknown_ids
    assert {item.request_id for item in (*first, *second)} == {
        "unknown-0",
        "unknown-1",
        "unknown-2",
    }
    assert all(queue.get(item.request_id) is not None for item in (*first, *second))
    assert {queue.get(item.request_id).state for item in (*first, *second)} == {UNKNOWN}


def test_unknown_jobs_does_not_resolve_or_make_a_row_claimable(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state=UNKNOWN, hy_invoked=True)

    found = queue.unknown_jobs(limit=10)

    assert len(found) == 1
    assert DurableWriteDrain(queue, execute=_never_execute).claim(limit=1) == ()
    assert queue.get("idem-1").state == UNKNOWN
    assert queue.resolve_unknown(found[0].job_id, found=False, detail="not in HY") == DEAD


def test_unknown_jobs_tolerates_a_row_resolved_between_pages(tmp_path) -> None:
    """Discovery is read-only and may overlap an inquiry from another worker.

    The discovery cursor is explicitly not a cross-call snapshot. A row on
    the next page may already have been resolved by the time that page is
    read; it must simply disappear from the remaining result, not be returned
    as pending inquiry work or resurrected as claimable.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    records = []
    for index in range(3):
        record = queue.submit(_request(evidence=(f"resolved-{index}",), key=f"unknown-{index}"))
        queue.settle(record.job_id, state=UNKNOWN, hy_invoked=True)
        records.append(record)
    with sqlite3.connect(path) as connection, connection:
        for index, record in enumerate(records):
            connection.execute(
                "UPDATE memory_write_jobs SET updated_at=? WHERE job_id=?",
                (f"2026-01-01T00:00:0{index}+00:00", record.job_id),
            )

    first = queue.unknown_jobs(limit=1)
    assert len(first) == 1
    assert queue.resolve_unknown(records[1].job_id, found=True) == COMPLETED

    rest = queue.unknown_jobs(
        limit=10,
        after_cursor=(first[-1].updated_at, first[-1].job_id),
    )

    assert [item.job_id for item in rest] == [records[2].job_id]
    assert queue.get(records[1].request_id).state == COMPLETED
    assert DurableWriteDrain(queue, execute=_never_execute).claim(limit=1) == ()


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_unknown_jobs_rejects_an_invalid_limit(tmp_path, limit: object) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    with pytest.raises(ValueError, match="limit must be positive"):
        queue.unknown_jobs(limit=limit)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "cursor",
    [
        ("only-one",),
        ("a", "b", "c"),
        ("2026-01-01T00:00:00+00:00", None),
        (None, "job"),
        ("", "job"),
    ],
)
def test_unknown_jobs_rejects_an_invalid_cursor(tmp_path, cursor: object) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    with pytest.raises(ValueError, match="after_cursor"):
        queue.unknown_jobs(after_cursor=cursor)  # type: ignore[arg-type]


def test_set_state_cannot_resurrect_a_terminal_write(tmp_path) -> None:
    """A completed write must not be moved back to a live state.

    `set_state` is the HTTP-facing setter and its `state` is caller-supplied.
    Letting a terminal row become `accepted`/`pending` would let a blind re-send
    duplicate a memory that was already written.
    """
    from one_memory.memory_server.durable_queue import DurableWriteRegistry

    path = tmp_path / "one-memory.sqlite3"
    registry = DurableWriteRegistry(path)
    record, _ = registry.submit(_request())
    registry.set_state(record.request_id, "completed")

    assert registry.set_state(record.request_id, "accepted") is None
    assert registry.get(record.request_id).state == "completed"

    # A terminal row is sticky: a later set_state must not change it either.
    registry.set_state(record.request_id, "dead")
    assert registry.get(record.request_id).state == "completed"
    assert registry.set_state(record.request_id, "pending") is None
    assert registry.get(record.request_id).state == "completed"


def test_set_state_cannot_clobber_a_running_leased_job(tmp_path) -> None:
    """The HTTP setter must not settle work a drain currently owns.

    `set_state` calls `settle` without an owner, so a caller could move a row
    that is `running` under a live lease. That clears the lease and writes an
    outcome for work still in flight — the drain later completes and calls
    HY again, or its real result is overwritten. A running row belongs to its
    lease owner; the registry must leave it alone.
    """
    from one_memory.memory_server.durable_queue import DurableWriteRegistry

    path = tmp_path / "one-memory.sqlite3"
    registry = DurableWriteRegistry(path)
    record, _ = registry.submit(_request())
    DurableWriteDrain(registry.queue, execute=_never_execute, owner="owner-a").claim(limit=1)

    assert registry.set_state(record.request_id, "completed") is None

    with sqlite3.connect(path) as connection:
        state, holder = connection.execute(
            "SELECT state, lease_owner FROM memory_write_jobs WHERE idempotency_key=?",
            (record.request_id,),
        ).fetchone()
    assert (state, holder) == (RUNNING, "owner-a")


def test_set_state_rejects_an_unvalidated_state(tmp_path) -> None:
    """An invalid state, including a non-string, must not enter the jobs table."""
    from one_memory.memory_server.durable_queue import DurableWriteRegistry

    path = tmp_path / "one-memory.sqlite3"
    registry = DurableWriteRegistry(path)
    record, _ = registry.submit(_request())

    with pytest.raises(ValueError, match="unknown write job state"):
        registry.set_state(record.request_id, "banana")
    with pytest.raises(ValueError, match="unknown write job state"):
        registry.set_state(record.request_id, ["pending"])  # type: ignore[arg-type]

    assert registry.get(record.request_id).state == "accepted"


def test_set_state_cannot_resolve_unknown_without_an_inquiry(tmp_path) -> None:
    """The HTTP-facing setter must not bypass the inquiry-only exit.

    `DurableWriteQueue.settle` deliberately treats `unknown` as sticky; a
    direct `set_state("completed"/"failed")` would otherwise turn a
    possibly-delivered write into a terminal claim without asking HY. The
    durable registry is the shared caller surface, so it must preserve that
    boundary even though today only tests call it.
    """
    from one_memory.memory_server.durable_queue import DurableWriteRegistry

    path = tmp_path / "one-memory.sqlite3"
    registry = DurableWriteRegistry(path)
    durable = registry.queue.submit(_request())
    registry.queue.settle(durable.job_id, state=UNKNOWN, hy_invoked=True)

    assert registry.set_state(durable.request_id, "completed") is None
    assert registry.set_state(durable.request_id, "failed") is None
    assert registry.get(durable.request_id).state == UNKNOWN


@pytest.mark.parametrize("state", (WAITING_FOR_PERMISSION, WAITING_FOR_MODEL))
def test_set_state_accepts_the_route_parked_states(tmp_path, state: str) -> None:
    """The durable setter must accept every non-terminal route state.

    The HTTP route's `WriteState` includes `waiting_for_permission` and
    `waiting_for_model`; the durable adapter is the storage side of that same
    contract. Rejecting either one makes the composition root unable to persist
    a parked write, even though the queue itself already supports it.
    """
    from one_memory.memory_server.durable_queue import DurableWriteRegistry

    path = tmp_path / "one-memory.sqlite3"
    registry = DurableWriteRegistry(path)
    record, _ = registry.submit(_request())

    updated = registry.set_state(record.request_id, state, "waiting for an external condition")

    assert updated is not None
    assert updated.state == state
    assert updated.detail == "waiting for an external condition"
    assert registry.get(record.request_id).state == state


def test_a_stale_worker_cannot_overwrite_a_reclaimed_unknown(tmp_path) -> None:
    """`unknown` is sticky against a late settle from the worker that lost it.

    A worker whose lease lapsed may still finish and call `settle`. If that
    overwrote the reclaimed `unknown` with `failed`, the "HY may already hold
    this" fact would be lost and a later retry could duplicate the write.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    DurableWriteDrain(queue, execute=_never_execute).claim(limit=1)
    queue.mark_invoked(record.job_id)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00'"
        )
    queue.reclaim_expired()
    assert queue.get("idem-1").state == UNKNOWN

    # The stale worker's late failure must not clobber the unknown.
    queue.settle(record.job_id, state=FAILED, error_code="hy_memory_unavailable", hy_invoked=False)
    assert queue.get("idem-1").state == UNKNOWN

    # Re-asserting unknown is allowed (idempotent), and an inquiry still resolves.
    queue.settle(record.job_id, state=UNKNOWN, hy_invoked=True)
    assert queue.resolve_unknown(record.job_id, found=True) == "completed"


def test_a_full_disk_reserve_refuses_a_new_write(tmp_path) -> None:
    """`disk_full` must refuse writes before the INSERT, not fail after it.

    The fault matrix names `refuse_writes` for this case: a half-applied change
    is worse than a pause. An impossibly high reserve forces the refusal
    deterministically.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path, min_free_bytes=1 << 62)

    with pytest.raises(OSError, match="disk reserve breached"):
        queue.submit(_request())

    # Nothing was accepted.
    assert _rows(path) == []


def test_no_configured_reserve_accepts_normally(tmp_path) -> None:
    """The default must not change existing callers."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)

    record = queue.submit(_request())

    assert record.enqueued is True
    assert _rows(path)[0]["state"] == "pending"


def test_a_locked_database_in_the_drain_reaches_the_error_counter(tmp_path) -> None:
    """A SQLite lock in the durable path must be counted, not only logged.

    `database is locked` under an import peak is the symptom the operator
    watches; the scheduler counts it via `record_database_error`. The durable
    drain is a second writer and must report it the same way, or a peak that
    starves the queue looks like silence.
    """
    import one_memory.diagnostic_log as diagnostics

    before = diagnostics._database_errors[("memory.write_queue", "database_locked")]  # noqa: SLF001
    drain = DurableWriteDrain(
        DurableWriteQueue(tmp_path / "one-memory.sqlite3"),
        execute=_never_execute,
    )

    code = drain._record_database_error(sqlite3.OperationalError("database is locked"))  # noqa: SLF001

    assert code == "database_locked"
    assert diagnostics._database_errors[("memory.write_queue", "database_locked")] == before + 1  # noqa: SLF001
    # A non-SQLite error is left to the exception log, not miscounted here.
    assert drain._record_database_error(RuntimeError("boom")) is None  # noqa: SLF001
    assert diagnostics._database_errors[("memory.write_queue", "database_locked")] == before + 1  # noqa: SLF001


def test_a_failing_renewal_does_not_kill_the_drain(tmp_path) -> None:
    """A transient DB error while renewing must not escape the drain.

    The renewal task is awaited in the executor's `finally`. If a lock error
    propagated from it, it would replace the job's real outcome and abort the
    whole drain. A missed renewal only shortens the lease, which reclaim
    handles, so the loop swallows the error and keeps the job running.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    renewed: list[str] = []

    async def execute(job_id: str) -> None:
        await asyncio.sleep(0.3)
        renewed.append(job_id)
        queue.settle(job_id, state=COMPLETED)

    drain = DurableWriteDrain(
        queue,
        execute=execute,
        lease=timedelta(seconds=1),
        renew_every=timedelta(milliseconds=30),
    )

    def failing_renew(job_id: str, **_kwargs) -> bool:
        raise sqlite3.OperationalError("database is locked")

    drain.renew = failing_renew  # type: ignore[method-assign]

    asyncio.run(drain.run_one())

    assert renewed == [record.job_id]
    assert queue.get("idem-1").state == COMPLETED


@pytest.mark.parametrize("terminal", [COMPLETED, FAILED, DEAD])
def test_a_terminal_row_is_sticky_against_a_late_settle(tmp_path, terminal: str) -> None:
    """A late settle must not overwrite an outcome that already happened.

    Only `unknown` used to be sticky. A stale worker (or a caller holding a
    stale id) could otherwise overwrite `completed`/`failed`/`dead` with a
    different terminal state, erasing the real result.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state=terminal)

    queue.settle(record.job_id, state=CANCELLED, error_code="late", hy_invoked=False)

    assert queue.get("idem-1").state == terminal


def test_reasserting_a_terminal_state_preserves_the_first_detail(tmp_path) -> None:
    """An idempotent re-settle must not overwrite the recorded outcome.

    `settle` allows the same terminal state again so callers can retry safely.
    That must be a no-op: a late worker can carry a different error code, and
    replacing `last_error` would erase the real diagnostic while the state
    still looks completed.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state=COMPLETED, error_code="first_result")
    before = _rows(path)[0]

    queue.settle(record.job_id, state=COMPLETED, error_code="late_result")

    after = _rows(path)[0]
    assert after["state"] == COMPLETED
    assert after["last_error"] == "first_result"
    assert after["updated_at"] == before["updated_at"]


def test_reasserting_unknown_preserves_its_first_detail(tmp_path) -> None:
    """The same idempotency rule applies to the non-terminal unknown state."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state=UNKNOWN, error_code="first_unknown", hy_invoked=True)
    before = _rows(path)[0]

    queue.settle(record.job_id, state=UNKNOWN, error_code="late_unknown", hy_invoked=True)

    after = _rows(path)[0]
    assert after["state"] == UNKNOWN
    assert after["last_error"] == "first_unknown"
    assert after["updated_at"] == before["updated_at"]


def test_mark_invoked_rejects_a_worker_that_lost_the_lease(tmp_path) -> None:
    """A stale worker must not mark a job another worker now owns.

    `mark_invoked` is the no-blind-retry boundary `execute_job` checks before
    calling HY. Without an owner guard, a worker whose lease lapsed and was
    re-claimed elsewhere would still succeed and then call HY for a job it no
    longer owns — the duplicate the boundary exists to prevent.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    claimer = DurableWriteDrain(queue, execute=_never_execute, owner="owner-a")
    claimer.claim(limit=1)

    # The stale worker with a different owner is refused.
    assert queue.mark_invoked(record.job_id, owner="owner-b") is False
    assert _rows(path)[0]["hy_invoked"] == 0

    # The real owner still succeeds.
    assert queue.mark_invoked(record.job_id, owner="owner-a") is True
    assert _rows(path)[0]["hy_invoked"] == 1


def test_execute_owned_refuses_when_the_lease_moved_on(tmp_path) -> None:
    """`execute_owned` is what production wiring passes the owner through.

    The drain supplies its owner, `execute_job` forwards it to `mark_invoked`,
    and a worker that lost its lease is refused before calling HY. This pins
    that the owner actually reaches the boundary.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    DurableWriteDrain(queue, execute=_never_execute, owner="owner-a").claim(limit=1)
    calls: list[tuple[str, str]] = []

    async def owned(job_id: str, owner: str) -> None:
        calls.append((job_id, owner))
        if not queue.mark_invoked(job_id, owner=owner):
            return
        queue.settle(job_id, state=COMPLETED)

    asyncio.run(
        DurableWriteDrain(
            queue, execute=_never_execute, execute_owned=owned, owner="owner-a"
        )._execute_job(record.job_id)  # noqa: SLF001
    )

    assert calls == [(record.job_id, "owner-a")]
    assert queue.get("idem-1").state == COMPLETED


def test_the_drain_reclaims_an_abandoned_running_job_without_a_restart(tmp_path) -> None:
    """An abandoned in-flight job must recover inside one long-lived process.

    `reclaim_expired` used to run only at construction, so a job left `running`
    by a cancelled executor (or a worker that died without settling) stayed
    unclaimable until the next process start. The idle path now reclaims on its
    maintenance gate, so the job becomes claimable again in the same process.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request())
    # Simulate the abandoned state a cancelled executor leaves behind: the job
    # is `running`, owned by a dead owner, with an already-expired lease.
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', lease_owner='dead', "
            "lease_expires_at='2000-01-01T00:00:00+00:00', hy_invoked=0"
        )
    assert queue.get("idem-1").state == RUNNING

    async def scenario() -> None:
        async def execute(job_id: str) -> None:
            queue.settle(job_id, state=COMPLETED)

        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(seconds=5),
            reap_interval=timedelta(0),
        )
        runner = asyncio.create_task(drain.run_forever(idle_pause=0.01))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if queue.get("idem-1").state == COMPLETED:
                break
        drain.stop()
        await runner

    asyncio.run(scenario())

    assert queue.get("idem-1").state == COMPLETED


def test_the_drain_reclaims_an_abandoned_job_even_while_never_idle(tmp_path) -> None:
    """An abandoned job must recover even if the queue never goes idle.

    Regression: `reclaim_expired` (and the other maintenance writes) ran only on
    the `executed == 0` idle branch. Under a sustained backlog that branch never
    runs, so a job left `running` with an expired lease (a cancelled executor or
    a worker that died without settling) stayed unclaimable until the next
    process start — an unbounded "infinite accumulation" on the exact busy
    window where it matters. Reclaim must be gated by its own interval, not by
    idleness.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    # A large standing backlog guarantees `run_one` never returns 0.
    for index in range(4000):
        queue.submit(_request(key=f"backlog-{index}"))
    queue.submit(_request(key="abandoned"))
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET state='running', lease_owner='dead', "
            "lease_expires_at='2000-01-01T00:00:00+00:00', hy_invoked=0 "
            "WHERE idempotency_key='abandoned'"
        )
    assert queue.get("abandoned").state == RUNNING

    async def scenario() -> None:
        async def execute(job_id: str) -> None:
            await asyncio.sleep(0.001)
            queue.settle(job_id, state=COMPLETED)

        drain = DurableWriteDrain(
            queue,
            execute=execute,
            lease=timedelta(seconds=5),
            reap_interval=timedelta(0),
        )
        runner = asyncio.create_task(drain.run_forever(idle_pause=0.01))
        for _ in range(150):
            await asyncio.sleep(0.02)
            if queue.get("abandoned").state != RUNNING:
                break
        drain.stop()
        await runner

    asyncio.run(scenario())

    # The abandoned job must leave `running` (reclaimed to `pending`) even
    # though the standing backlog means `run_one` never returns 0. Whether the
    # drain then reaches it inside this short window is throughput, not the
    # recoverability property under test.
    assert queue.get("abandoned").state != RUNNING, (
        "an abandoned job must be reclaimed even while the queue is never idle"
    )


def test_a_provider_message_never_reaches_the_log(tmp_path, caplog) -> None:
    """The log must not carry provider/user text, not just the stored column.

    `last_error` was sanitised, but `logger.exception` still printed the full
    traceback — including a provider message that can quote the user's own
    text. The durable path logs only the sanitised code / exception type.
    """
    import logging

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    with caplog.at_level(logging.DEBUG, logger="one_memory.memory_server.durable_queue"):
        asyncio.run(
            execute_job(
                record.job_id,
                queue=queue,
                supervisor=_Recorder(
                    error=RuntimeError("provider echoed 'my secret diary' url https://x")
                ),
                evidence=_EvidenceStore({"e1"}),
            )
        )

    assert "secret diary" not in caplog.text
    assert "https://x" not in caplog.text
    assert "RuntimeError" in caplog.text  # the type is safe and still useful


def test_a_stale_worker_cannot_settle_a_row_another_worker_now_owns(tmp_path) -> None:
    """Settling must be guarded by the lease owner, like marking invoked.

    A worker whose lease lapsed and whose row was re-claimed elsewhere can
    still finish and call `settle`. Without an owner guard it would overwrite
    the new owner's row with a stale `failed`/`completed`, erasing the real
    outcome and (for `failed`) making it look retryable.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    DurableWriteDrain(queue, execute=_never_execute, owner="owner-a").claim(limit=1)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00'"
        )
    queue.reclaim_expired()
    DurableWriteDrain(queue, execute=_never_execute, owner="owner-b").claim(limit=1)

    # owner-a's stale report is refused; owner-b still owns a running row.
    queue.settle(record.job_id, state=FAILED, error_code="stale", hy_invoked=False, owner="owner-a")
    with sqlite3.connect(path) as connection:
        state, holder = connection.execute(
            "SELECT state, lease_owner FROM memory_write_jobs WHERE job_id=?", (record.job_id,)
        ).fetchone()
    assert (state, holder) == (RUNNING, "owner-b")

    # owner-b can settle its own row.
    queue.settle(record.job_id, state=COMPLETED, owner="owner-b")
    assert queue.get("idem-1").state == COMPLETED


def test_a_stale_worker_error_cannot_release_another_workers_lease(tmp_path) -> None:
    """The fallback settlement must also be owner-guarded.

    `settle_worker_error` runs when an executor raises unexpectedly. A worker
    whose lease lapsed and whose row was re-claimed elsewhere must not use it to
    reset the new owner's `running` row to `pending` and drop its lease.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    DurableWriteDrain(queue, execute=_never_execute, owner="owner-a").claim(limit=1)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00'"
        )
    queue.reclaim_expired()
    DurableWriteDrain(queue, execute=_never_execute, owner="owner-b").claim(limit=1)

    # A stale error must not release owner-b's lease.
    assert queue.settle_worker_error(record.job_id, error_code="stale", owner="owner-a") == RUNNING
    with sqlite3.connect(path) as connection:
        state, holder = connection.execute(
            "SELECT state, lease_owner FROM memory_write_jobs WHERE job_id=?", (record.job_id,)
        ).fetchone()
    assert (state, holder) == (RUNNING, "owner-b")

    # The real owner can still settle it.
    assert queue.settle_worker_error(record.job_id, error_code="real", owner="owner-b") == PENDING


def test_a_limit_is_filled_with_one_job_per_session(tmp_path) -> None:
    """A session with many rows must not starve the claim's capacity.

    Deduping after the SQL `LIMIT` would let one session's backlog consume the
    whole limit, so other sessions with ready work get nothing this round. The
    query returns the earliest job per session, so a limit of N covers up to N
    distinct sessions.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)

    def request(session: str, key: str) -> WriteRequest:
        return WriteRequest(
            internal_session_id=session,
            batch_identity=BatchIdentity(internal_session_id=session, first_round=1, last_round=1),
            evidence_ids=["e1"],
            idempotency_key=key,
        )

    for index in range(3):
        queue.submit(request("sess-a", f"a{index}"))
    queue.submit(request("sess-b", "b0"))
    queue.submit(request("sess-c", "c0"))

    drain = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(seconds=300))
    claimed = drain.claim(limit=3)

    assert len(claimed) == 3
    with sqlite3.connect(path) as connection:
        sessions = [
            row[0]
            for row in connection.execute(
                "SELECT session_key FROM memory_write_jobs WHERE state='running'"
            )
        ]
    # One row per session: sess-a appears once, and b/c are not starved.
    assert sorted(sessions) == ["sess-a", "sess-b", "sess-c"]


def test_submit_assigns_a_persistent_monotonic_queue_sequence(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(_request(key="first", source="cursor"))
    queue.submit(_request(key="second", source="cursor"))

    with sqlite3.connect(path) as connection:
        sequences = [
            row[0]
            for row in connection.execute(
                "SELECT queue_seq FROM memory_write_jobs ORDER BY queue_seq"
            )
        ]

    assert sequences == [1, 2]


def test_claim_takes_one_job_per_source_and_parallelizes_sources(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)

    def request(source: str, session: str, key: str) -> WriteRequest:
        return WriteRequest(
            internal_session_id=session,
            source_id=source,
            batch_identity=BatchIdentity(internal_session_id=session, first_round=1, last_round=1),
            evidence_ids=[key],
            idempotency_key=key,
        )

    first_a = queue.submit(request("cursor", "a-1", "a-1"))
    queue.submit(request("cursor", "a-2", "a-2"))
    first_b = queue.submit(request("codex", "b-1", "b-1"))

    claimed = DurableWriteDrain(queue, execute=_never_execute).claim(limit=10)

    assert set(claimed) == {first_a.job_id, first_b.job_id}


def test_two_claimers_never_receive_the_same_row(tmp_path) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    records = [queue.submit(_request(key=f"job-{index}")) for index in range(2)]
    barrier = threading.Barrier(2)

    def claim(owner: str) -> tuple[str, ...]:
        barrier.wait()
        return DurableWriteDrain(queue, execute=_never_execute, owner=owner).claim(limit=1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, ("a", "b")))

    flattened = [job_id for batch in claimed for job_id in batch]
    assert len(flattened) == len(set(flattened)) == 1
    assert flattened[0] in {record.job_id for record in records}


def test_default_drain_owner_is_unique_across_lease_generations(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    stale = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(milliseconds=10))
    current = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(seconds=1))
    assert stale.owner != current.owner
    stale.claim(limit=1)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET lease_expires_at=? WHERE job_id=?",
            ("2000-01-01T00:00:00+00:00", record.job_id),
        )
    queue.reclaim_expired()
    assert current.claim(limit=1) == (record.job_id,)

    assert stale.renew(record.job_id) is False
    assert queue.mark_invoked(record.job_id, owner=stale.owner) is False
    queue.settle(record.job_id, state=COMPLETED, owner=stale.owner)
    assert queue.get(record.request_id).state == RUNNING


def test_live_work_never_precedes_older_background_for_the_same_source(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    background = queue.submit(_request(key="background"))
    live = [
        queue.submit(_request(key=f"live-{index}").model_copy(update={"priority": "live"}))
        for index in range(5)
    ]
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET created_at=? WHERE job_id=?",
            ("2000-01-01T00:00:00+00:00", background.job_id),
        )

    drain = DurableWriteDrain(queue, execute=_never_execute, owner="fairness")
    claimed: list[str] = []
    for _ in range(6):
        job_id = drain.claim(limit=1)[0]
        claimed.append(job_id)
        queue.settle(job_id, state=COMPLETED, owner="fairness")

    assert claimed[0] == background.job_id
    assert claimed[1:] == [record.job_id for record in live]


def test_disabled_source_is_not_claimed_until_reenabled(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(key="paused", source="cursor"))
    enabled = {"cursor": False}
    drain = DurableWriteDrain(
        queue,
        execute=_never_execute,
        source_writes_permitted=lambda source_id: enabled.get(source_id, False),
    )

    assert drain.claim(limit=1) == ()
    assert queue.get(record.request_id).state == PENDING

    enabled["cursor"] = True
    assert drain.claim(limit=1) == (record.job_id,)


def test_a_source_admitted_only_by_grant_is_claimed(tmp_path) -> None:
    """The claim gate must ask the question the write gate asks.

    `one-memory-manual` is not a connected app: it never appears in the
    configuration document, so a per-app toggle answers False for it while the
    write gate admits it by its grant. Wired as the drain's claim gate, that
    second answer skipped the job on every tick — a saved manual note stayed
    pending with no task behind it and never reached the model. Admission is
    one decision, and it has one predicate.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(key="manual-note", source="one-memory-manual"))
    # What `sync_enabled` answers for this source: no entry, therefore not on.
    per_app_toggle: dict[str, bool] = {}
    granted = {"one-memory-manual"}

    drained = DurableWriteDrain(
        queue,
        execute=_never_execute,
        source_writes_permitted=lambda source_id: source_id in granted,
        source_claim_enabled=lambda source_id: source_id in granted,
    )
    assert drained.claim(limit=1) == (record.job_id,)

    # The wiring that shipped: admitted for writes, refused at claim.
    queue.settle(record.job_id, state=PENDING, owner=None)
    stranded = DurableWriteDrain(
        queue,
        execute=_never_execute,
        source_writes_permitted=lambda source_id: source_id in granted,
        source_claim_enabled=lambda source_id: per_app_toggle.get(source_id, False),
    )
    assert stranded.claim(limit=1) == ()


def test_pause_before_hy_releases_an_already_claimed_job(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(key="pause-race", source="cursor"))
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="pause-owner")
    assert drain.claim(limit=1) == (record.job_id,)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(invoke_hy=True),
            evidence=_EvidenceStore({"e1"}),
            owner="pause-owner",
            source_writes_permitted=lambda _source_id: False,
        )
    )

    current = queue.get(record.request_id)
    assert current is not None
    assert current.state == PENDING
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT attempts,hy_invoked FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone() == (0, 0)


def test_event_time_window_claims_cross_harness_jobs_within_ten_minutes(
    tmp_path,
) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    anchor = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)
    first = queue.submit(
        _request(key="window-a", source="cursor", event_at=anchor)
    )
    inside = queue.submit(
        _request(
            key="window-b",
            source="codex",
            event_at=anchor + timedelta(minutes=9, seconds=59),
        )
    )
    boundary = queue.submit(
        _request(
            key="window-c",
            source="claude-code",
            event_at=anchor + timedelta(minutes=10),
        )
    )
    drain = DurableWriteDrain(
        queue,
        execute=_never_execute,
        owner="window-owner",
        max_concurrent_sources=10,
    )

    assert drain.claim(limit=10) == (first.job_id, inside.job_id)
    queue.settle(
        first.job_id,
        state=COMPLETED,
        hy_invoked=True,
        owner="window-owner",
    )
    queue.settle(
        inside.job_id,
        state=COMPLETED,
        hy_invoked=True,
        owner="window-owner",
    )
    assert drain.claim(limit=10) == (boundary.job_id,)


def test_event_time_window_anchor_does_not_slide_while_a_job_is_running(
    tmp_path,
) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    anchor = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)
    first = queue.submit(
        _request(key="fixed-a", source="cursor", event_at=anchor)
    )
    inside = queue.submit(
        _request(
            key="fixed-b",
            source="codex",
            event_at=anchor + timedelta(minutes=9),
        )
    )
    chained = queue.submit(
        _request(
            key="fixed-c",
            source="claude-code",
            event_at=anchor + timedelta(minutes=18),
        )
    )
    drain = DurableWriteDrain(
        queue,
        execute=_never_execute,
        owner="fixed-window-owner",
        max_concurrent_sources=10,
    )

    assert drain.claim(limit=2) == (first.job_id, inside.job_id)
    queue.settle(
        first.job_id,
        state=COMPLETED,
        hy_invoked=True,
        owner="fixed-window-owner",
    )
    assert drain.claim(limit=1) == ()
    assert queue.get(chained.request_id).state == PENDING
    queue.settle(
        inside.job_id,
        state=COMPLETED,
        hy_invoked=True,
        owner="fixed-window-owner",
    )
    assert drain.claim(limit=1) == (chained.job_id,)


def test_sedimentation_pause_releases_claim_without_spending_an_attempt(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(key="sedimentation-pause", source="cursor"))
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="pause-owner")
    assert drain.claim(limit=1) == (record.job_id,)

    async def refuse_before_hy(_source_id, _mark_invoked):
        raise RuntimeError("model_sedimentation_disabled")

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(invoke_hy=True),
            evidence=_EvidenceStore({"e1"}),
            owner="pause-owner",
            source_writes_permitted=lambda _source_id: True,
            admit_hy_invocation=refuse_before_hy,
        )
    )

    current = queue.get(record.request_id)
    assert current is not None and current.state == PENDING
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT attempts,hy_invoked FROM memory_write_jobs WHERE job_id=?",
            (record.job_id,),
        ).fetchone() == (0, 0)


def test_pause_after_first_hy_call_does_not_interrupt_the_current_job(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request(key="pause-after-start", source="cursor"))
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="started-owner")
    assert drain.claim(limit=1) == (record.job_id,)
    enabled = {"cursor": True}
    recorder = _TwoInvocationRecorder(lambda: enabled.update(cursor=False))

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=recorder,
            evidence=_EvidenceStore({"e1"}),
            owner="started-owner",
            source_writes_permitted=lambda source_id: enabled[source_id],
        )
    )

    assert recorder.invocations == 2
    assert queue.get(record.request_id).state == COMPLETED


def test_database_lock_retry_is_exponential_capped_and_sampled(tmp_path, monkeypatch) -> None:
    waits: list[float] = []
    samples: list[int] = []
    attempts = 0
    drain = DurableWriteDrain(
        DurableWriteQueue(tmp_path / "one-memory.sqlite3"), execute=_never_execute
    )

    def claim(*, limit: int = 1, **_kwargs) -> tuple[str, ...]:
        nonlocal attempts
        attempts += 1
        if attempts <= 8:
            raise sqlite3.OperationalError("database is locked")
        drain.stop()
        return ("synthetic-job",)

    async def sleep(delay: float) -> None:
        waits.append(delay)

    async def finish(_job_id: str) -> None:
        return None

    monkeypatch.setattr(drain, "claim", claim)
    monkeypatch.setattr(drain, "_run_claimed_job", finish)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(
        "one_memory.memory_server.durable_queue.diagnostic_event",
        lambda **event: samples.append(event["details"]["count"]),
    )

    asyncio.run(drain.run_forever())

    assert waits == [0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 5.0]
    assert samples == [1, 2, 4, 8]


def test_permanent_claim_error_stops_the_worker(tmp_path, monkeypatch) -> None:
    drain = DurableWriteDrain(
        DurableWriteQueue(tmp_path / "one-memory.sqlite3"), execute=_never_execute
    )

    def broken(*, limit: int = 1, **_kwargs) -> tuple[str, ...]:
        raise sqlite3.OperationalError("no such column: broken")

    monkeypatch.setattr(drain, "claim", broken)
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        asyncio.run(drain.run_forever())


def test_expired_owner_cannot_mark_hy_invoked(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="stale")
    drain.claim(limit=1)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET lease_expires_at=? WHERE job_id=?",
            ("2000-01-01T00:00:00+00:00", record.job_id),
        )

    assert queue.mark_invoked(record.job_id, owner="stale") is False


def test_expired_owner_cannot_renew_or_settle(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="stale")
    drain.claim(limit=1)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET lease_expires_at=? WHERE job_id=?",
            ("2000-01-01T00:00:00+00:00", record.job_id),
        )

    assert drain.renew(record.job_id) is False
    queue.settle(record.job_id, state=COMPLETED, owner="stale")
    assert queue.get(record.request_id).state == RUNNING
    assert queue.settle_worker_error(record.job_id, error_code="late", owner="stale") == RUNNING


def test_repeated_renewal_errors_keep_source_slot_until_execution_ends(
    tmp_path, monkeypatch
) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    queue.submit(_request())
    release = asyncio.Event()

    async def execute(_job_id: str) -> None:
        await release.wait()

    drain = DurableWriteDrain(
        queue,
        execute=execute,
        lease=timedelta(milliseconds=60),
        renew_every=timedelta(milliseconds=10),
    )
    monkeypatch.setattr(
        drain,
        "renew",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )

    async def scenario() -> None:
        running = asyncio.create_task(drain.run_one())
        await asyncio.sleep(0.1)
        assert not running.done()
        release.set()
        await asyncio.wait_for(running, 1)

    asyncio.run(scenario())


def test_blocked_renewal_keeps_source_slot_until_execution_ends(tmp_path, monkeypatch) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    queue.submit(_request())
    release = asyncio.Event()
    timing: dict[str, float] = {}
    renew_returned = threading.Event()

    async def execute(_job_id: str) -> None:
        await release.wait()
        timing["execution_returned"] = time.monotonic()

    def blocked_renew(*_args, **_kwargs) -> bool:
        time.sleep(0.2)
        timing["renew_returned"] = time.monotonic()
        renew_returned.set()
        return True

    drain = DurableWriteDrain(
        queue,
        execute=execute,
        lease=timedelta(milliseconds=60),
        renew_every=timedelta(milliseconds=10),
    )
    monkeypatch.setattr(drain, "renew", blocked_renew)

    async def scenario() -> None:
        running = asyncio.create_task(drain.run_one())
        await asyncio.sleep(0.1)
        assert not running.done()
        release.set()
        await asyncio.wait_for(running, 1)

    asyncio.run(scenario())
    assert renew_returned.wait(1)
    assert timing["execution_returned"] < timing["renew_returned"]


def test_permanent_renewal_error_stops_the_worker(tmp_path, monkeypatch) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    queue.submit(_request())
    drain = DurableWriteDrain(
        queue,
        execute=lambda _job_id: asyncio.sleep(0.03),
        lease=timedelta(seconds=1),
        renew_every=timedelta(milliseconds=10),
    )
    monkeypatch.setattr(
        drain,
        "renew",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("database schema is corrupt")
        ),
    )

    with pytest.raises(RuntimeError):
        asyncio.run(drain.run_one())


def test_lost_renewal_waits_for_execution_before_releasing_source(tmp_path, monkeypatch) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    release = asyncio.Event()

    async def execute(_job_id: str) -> None:
        await release.wait()

    drain = DurableWriteDrain(
        queue,
        execute=execute,
        lease=timedelta(seconds=1),
        renew_every=timedelta(milliseconds=10),
    )
    monkeypatch.setattr(drain, "renew", lambda *_args, **_kwargs: False)

    async def scenario() -> None:
        running = asyncio.create_task(drain.run_one())
        await asyncio.sleep(0.05)
        assert not running.done()
        release.set()
        await running

    asyncio.run(scenario())
    assert queue.get(record.request_id).state == RUNNING


def test_a_session_beyond_the_over_fetch_window_is_not_starved(tmp_path) -> None:
    """Fairness cannot depend on every ready session fitting in one window.

    The bounded over-fetch exists to avoid the slow per-session subquery, but
    if claim keeps the first `limit` sessions in raw age order, a session whose
    backlog is longer than the window can hide every later session from the
    batch's concurrency. One claim must advance every ready session before
    taking a second job from any of them.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)

    def request(session: str, key: str) -> WriteRequest:
        return WriteRequest(
            internal_session_id=session,
            batch_identity=BatchIdentity(internal_session_id=session, first_round=1, last_round=1),
            evidence_ids=["e1"],
            idempotency_key=key,
        )

    first: str | None = None
    for index in range(520):
        record = queue.submit(request("sess-a", f"a{index}"))
        first = first or record.job_id
    second = queue.submit(request("sess-b", "b0"))

    drain = DurableWriteDrain(queue, execute=_never_execute, lease=timedelta(seconds=300))
    claimed = drain.claim(limit=2)

    assert set(claimed) == {first, second.job_id}, (
        "a ready session behind the bounded over-fetch window must take a concurrency "
        "slot instead of staying hidden behind one session's backlog"
    )


def test_an_oversized_pre_send_write_is_not_claimable_again(tmp_path) -> None:
    """Pin the durable queue's `FAILED` semantics, which differ from the task layer.

    `job_state.py` (and `contracts.WriteJobState.is_terminal`) describe a
    retryable `FAILED`. The durable queue deliberately does not retry it: its
    `failed` rows are deterministic pre-send refusals (`hy_memory_evidence_changed`,
    `hy_memory_input_exceeds_window`) where re-running the same intent fails
    identically. This test records the behavior so the divergence is explicit
    rather than implicit; if the product decides pre-send failures should retry,
    this assertion is the one to change together with the claim SQL.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(
        record.job_id,
        state=FAILED,
        error_code="hy_memory_input_exceeds_window",
        hy_invoked=False,
    )

    assert queue.get("idem-1").state == FAILED
    assert DurableWriteDrain(queue, execute=_never_execute).claim(limit=1) == ()
    assert queue.reclaim_expired() == 0


def test_a_superseded_snapshot_is_cancelled_before_hy_and_not_retried(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    _claim_through_drain(queue, record.job_id)

    asyncio.run(
        execute_job(
            record.job_id,
            queue=queue,
            supervisor=_Recorder(error=RuntimeError("hy_memory_evidence_changed")),
            evidence=_EvidenceStore({"e1"}),
        )
    )

    assert queue.get(record.request_id).state == CANCELLED
    assert DurableWriteDrain(queue, execute=_never_execute).claim(limit=1) == ()


@pytest.mark.parametrize(
    "error_code,hy_invoked,expected",
    [
        ("model_content_permission_revoked", False, 1),
        ("model_content_permission_revoked", True, 0),
        ("user_cancelled", False, 0),
    ],
)
def test_explicit_consent_only_readmits_safe_permission_cancellation(
    tmp_path, error_code, hy_invoked, expected
) -> None:
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    record = queue.submit(_request())
    queue.settle(
        record.job_id,
        state=CANCELLED,
        error_code=error_code,
        hy_invoked=hy_invoked,
    )
    queue.clear_terminal_payloads()

    assert queue.readmit_cancelled_permission_confirmed(record.job_id, _request()) == expected
    assert queue.get("idem-1").state == (PENDING if expected else CANCELLED)


def test_a_running_job_is_reported_as_accepted_not_a_500(tmp_path) -> None:
    """The HTTP state vocabulary has no `running`; a live drain creates it.

    Once a real consumer runs, a submitted row is claimed to `running` between
    the POST and the terminal result. `MemoryWriteReceipt.state` is a
    `WriteState` literal without `running`, so returning it raw makes
    `GET /memory-writes/{id}` raise a validation error (HTTP 500). To a caller
    "accepted and being worked on" is still `accepted`.
    """
    from one_memory.memory_server.durable_queue import DurableWriteRegistry

    path = tmp_path / "one-memory.sqlite3"
    registry = DurableWriteRegistry(path)
    record, _ = registry.submit(_request())
    DurableWriteDrain(registry.queue, execute=_never_execute, owner="owner-a").claim(limit=1)

    reported = registry.get(record.request_id)

    assert reported is not None
    assert reported.state == "accepted"


def test_a_possibly_sent_row_cannot_be_put_back_to_pending(tmp_path) -> None:
    """Once HY may have been reached, the row must never become claimable again.

    `mark_invoked` sets `hy_invoked=1` before the HY call. If any settle path
    could then write `pending`, a crash-recovery or stale-worker path would
    re-claim it and call HY a second time, duplicating the memory (HY has no
    idempotency key). This pins the invariant at the `settle` boundary.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_request())
    queue.settle(record.job_id, state=RUNNING)
    assert queue.mark_invoked(record.job_id) is True

    # A stale worker's attempt to requeue the possibly-sent job is refused.
    queue.settle(record.job_id, state=PENDING, error_code="retry?")
    row = _rows(path)[0]
    assert row["state"] == RUNNING
    assert row["hy_invoked"] == 1

    # The only safe terminal exit is `unknown`.
    queue.settle(record.job_id, state=UNKNOWN, error_code="lease lapsed", hy_invoked=True)
    row = _rows(path)[0]
    assert row["state"] == UNKNOWN
    assert row["hy_invoked"] == 1
    # And an unknown still cannot be requeued.
    queue.settle(record.job_id, state=PENDING, error_code="late retry")
    assert _rows(path)[0]["state"] == UNKNOWN


def test_readmit_paths_never_requeue_a_possibly_sent_row(tmp_path) -> None:
    """`readmit_parked` / `readmit_model_waiters` must not resurrect sent work."""
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    parked = queue.submit(_request(evidence=("e1",), key="parked"))
    model = queue.submit(_request(evidence=("e2",), key="model"))
    with sqlite3.connect(path) as db, db:
        db.execute(
            "UPDATE memory_write_jobs SET state=?, hy_invoked=1 WHERE job_id=?",
            (WAITING_FOR_PERMISSION, parked.job_id),
        )
        db.execute(
            "UPDATE memory_write_jobs SET state=?, hy_invoked=1 WHERE job_id=?",
            (WAITING_FOR_MODEL, model.job_id),
        )

    assert queue.readmit_parked(states=(WAITING_FOR_PERMISSION,)) == 0
    assert queue.readmit_model_waiters(after=timedelta(0)) == 0

    rows = {row["idempotency_key"]: row for row in _rows(path)}
    assert rows["parked"]["state"] == WAITING_FOR_PERMISSION
    assert rows["model"]["state"] == WAITING_FOR_MODEL
    assert DurableWriteDrain(queue, execute=_never_execute).claim(limit=5) == ()


def test_a_window_holding_only_blocked_rows_advances_instead_of_starving(
    tmp_path,
) -> None:
    """A window whose remaining rows FIFO holds back must be retired.

    Live shape: the anchor sat on 2026-10-07T03:24Z while the four pending rows
    inside it were each blocked by 900+ earlier siblings of the same Harness
    whose own event times sat outside the range (the earliest was 2026-03-12).
    None of them could ever be claimed from inside that window, and because the
    window only asked whether the range still held *some* pending row it never
    retired: 3.8k queued turns sat at zero completions until restart, which
    does not help because the anchor is persisted.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    anchor = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)

    # The anchor itself: another Harness, claimable, inside the range.
    opener = queue.submit(
        _request(key="starve-opener", source="codex", event_at=anchor)
    )
    # Same Harness as the blocked row but an hour later: outside the range, and
    # it sorts earlier in queue_seq, so FIFO holds the row below behind it.
    older_seq_newer_time = queue.submit(
        _request(
            key="starve-head",
            source="workbuddy",
            event_at=anchor + timedelta(hours=1),
        )
    )
    blocked = queue.submit(
        _request(
            key="starve-blocked",
            source="workbuddy",
            event_at=anchor + timedelta(minutes=5),
        )
    )

    drain = DurableWriteDrain(
        queue,
        execute=_never_execute,
        owner="starve-owner",
        max_concurrent_sources=10,
    )

    # The blocked row is inside the window but FIFO-refused, so it is not
    # claimed alongside the opener.
    assert drain.claim(limit=10) == (opener.job_id,)
    queue.settle(
        opener.job_id,
        state=COMPLETED,
        hy_invoked=True,
        owner="starve-owner",
    )

    # The window now holds exactly one row and that row can never be claimed
    # from inside it. Retire the window and re-anchor on the blocker.
    assert drain.claim(limit=10) == (older_seq_newer_time.job_id,)

    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        window = db.execute(
            "SELECT window_anchor FROM memory_write_schedule_state "
            "WHERE singleton=1"
        ).fetchone()
    assert window is not None
    assert str(window[0]) == (
        anchor + timedelta(hours=1)
    ).astimezone(UTC).isoformat()

    queue.settle(
        older_seq_newer_time.job_id,
        state=COMPLETED,
        hy_invoked=True,
        owner="starve-owner",
    )
    # Once the blocker is gone the deferred row keeps its original event time
    # and is reached on the next pass.
    assert drain.claim(limit=10) == (blocked.job_id,)
