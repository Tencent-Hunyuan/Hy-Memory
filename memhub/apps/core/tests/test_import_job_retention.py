"""`import_jobs` stays bounded, and the questions asked of it are SQL, not scans.

Live sync opened a job per change it imported and nothing ever removed one, so
the table only grew; every caller then read all of it and filtered in Python.
These tests hold the three halves of the fix: what pruning keeps, that the
filtered queries answer exactly what the old filters did, and that the indexes
behind them arrive by migration.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import _prune_finished_import_jobs, create_app
from one_memory.config import Settings
from one_memory.imports.jobs import (
    FINISHED_STATUSES,
    SYNC_JOBS_KEPT_PER_SOURCE,
    ImportJob,
    ImportJobStore,
    ImportStatus,
)
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import (
    IMPORT_JOB_LOOKUP_INDEXES_MIGRATION_VERSION,
    MIGRATIONS,
)

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path) -> ImportJobStore:
    return ImportJobStore(tmp_path / "one-memory.sqlite3")


def _job(
    store: ImportJobStore,
    *,
    user_id: str = "local",
    source_id: str = "fixture",
    operation: str = "sync",
    status: ImportStatus = ImportStatus.COMPLETED,
    minute: int,
    issue: bool = False,
) -> str:
    """Insert one row as its terminal shape, at a chosen creation time."""
    created = (_BASE + timedelta(minutes=minute)).isoformat()
    job_id = f"{source_id}-{user_id}-{operation}-{minute:05d}"
    with store._connect(writing=True) as connection:
        connection.execute(
            "INSERT INTO import_jobs (job_id,user_id,source_id,status,created_at,updated_at,"
            "operation) VALUES (?,?,?,?,?,?,?)",
            (job_id, user_id, source_id, status, created, created, operation),
        )
        if issue:
            connection.execute(
                "INSERT INTO import_issues (job_id,kind,conversation_id,reason,created_at) "
                "VALUES (?,'error',NULL,'synthetic_reason',?)",
                (job_id, created),
            )
            connection.execute(
                "INSERT INTO import_skip_reason_counts (job_id,reason,count) VALUES (?,'r',1)",
                (job_id,),
            )
    return job_id


def _ids(store: ImportJobStore, **filters) -> list[str]:
    return [job.job_id for job in store.find_jobs(**filters)]


def _count(store: ImportJobStore, table: str, job_id: str) -> int:
    with store._connect() as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE job_id=?", (job_id,)
            ).fetchone()[0]
        )


def test_pruning_keeps_the_newest_finished_syncs_of_every_source(tmp_path) -> None:
    store = _store(tmp_path)
    keep = 5
    for source in ("alpha", "beta"):
        for minute in range(12):
            status = ImportStatus.CANCELLED if minute % 4 == 0 else ImportStatus.COMPLETED
            _job(store, source_id=source, minute=minute, status=status, issue=True)
    # A source with fewer than `keep` keeps all of them.
    for minute in range(3):
        _job(store, source_id="gamma", minute=minute)
    # Never pruned: the active sync, history of any state, another user's lane.
    running = _job(store, source_id="alpha", minute=1000, status=ImportStatus.RUNNING)
    failed_history = _job(
        store, source_id="alpha", operation="history", minute=2, status=ImportStatus.FAILED
    )
    histories = [
        _job(store, source_id="alpha", operation="history", minute=100 + minute)
        for minute in range(10)
    ]
    other_user = [_job(store, user_id="other", source_id="alpha", minute=m) for m in range(8)]

    removed = store.prune_finished_sync_jobs(keep=keep)

    assert removed == (12 - keep) * 2 + (8 - keep)
    for source in ("alpha", "beta"):
        finished = _ids(
            store, user_id="local", source_id=source, operation="sync", statuses=FINISHED_STATUSES
        )
        assert finished == [f"{source}-local-sync-{m:05d}" for m in range(12 - keep, 12)]
        # Their issues and skip counts went with them; the kept ones still have theirs.
        assert _count(store, "import_issues", f"{source}-local-sync-00000") == 0
        assert _count(store, "import_skip_reason_counts", f"{source}-local-sync-00000") == 0
        assert _count(store, "import_issues", f"{source}-local-sync-00011") == 1
    assert len(_ids(store, source_id="gamma")) == 3
    assert store.require(running).status is ImportStatus.RUNNING
    assert store.require(failed_history).status is ImportStatus.FAILED
    assert all(store.get(job_id) is not None for job_id in histories)
    assert _ids(store, user_id="other") == other_user[-keep:]
    # The newest job of every lane, which the App and live sync health show,
    # is always kept.
    assert store.latest_job(user_id="local", source_id="beta").job_id == "beta-local-sync-00011"

    before = _ids(store)
    assert store.prune_finished_sync_jobs(keep=keep) == 0
    assert _ids(store) == before


def test_pruning_is_bounded_per_call_and_finishes_when_repeated(tmp_path) -> None:
    store = _store(tmp_path)
    for minute in range(60):
        _job(store, minute=minute)

    passes = []
    while True:
        removed = store.prune_finished_sync_jobs(keep=10, max_rows=15)
        passes.append(removed)
        if removed < 15:
            break

    assert passes == [15, 15, 15, 5]
    assert len(_ids(store)) == 10


def test_the_default_keep_count_is_twenty(tmp_path) -> None:
    store = _store(tmp_path)
    for minute in range(SYNC_JOBS_KEPT_PER_SOURCE + 7):
        _job(store, minute=minute)
    assert store.prune_finished_sync_jobs() == 7
    assert len(_ids(store)) == SYNC_JOBS_KEPT_PER_SOURCE == 20


def test_startup_pruning_runs_to_completion_off_the_loop(tmp_path) -> None:
    store = _store(tmp_path)
    for minute in range(500):
        _job(store, minute=minute)

    async def scenario() -> None:
        task = asyncio.create_task(_prune_finished_import_jobs(store, interval_seconds=3600))
        for _ in range(500):
            await asyncio.sleep(0.01)
            if len(await asyncio.to_thread(_ids, store)) == SYNC_JOBS_KEPT_PER_SOURCE:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(_ids(store)) == SYNC_JOBS_KEPT_PER_SOURCE


def test_filtered_queries_answer_what_the_python_filters_did(tmp_path) -> None:
    store = _store(tmp_path)
    rng = random.Random(7)
    lanes = [(user, source) for user in ("local", "other") for source in ("a", "b", "c")]
    for minute in range(240):
        user, source = rng.choice(lanes)
        _job(
            store,
            user_id=user,
            source_id=source,
            operation=rng.choice(("sync", "history")),
            # Only terminal shapes: the active-slot index allows one active job per lane.
            status=rng.choice(FINISHED_STATUSES),
            minute=minute,
        )
    everything: list[ImportJob] = store.list_jobs()
    assert len(everything) == 240

    status_sets = [
        None,
        (ImportStatus.COMPLETED,),
        (ImportStatus.CANCELLED, ImportStatus.COMPLETED),
        (ImportStatus.RUNNING,),
    ]
    for user_id in (None, "local", "other"):
        for source_id in (None, "a", "c"):
            for operation in (None, "sync", "history"):
                for statuses in status_sets:
                    expected = [
                        job
                        for job in everything
                        if (user_id is None or job.user_id == user_id)
                        and (source_id is None or job.source_id == source_id)
                        and (operation is None or job.operation == operation)
                        and (statuses is None or job.status in statuses)
                    ]
                    found = store.find_jobs(
                        user_id=user_id,
                        source_id=source_id,
                        operation=operation,
                        statuses=statuses,
                    )
                    assert found == expected
                    newest = store.find_jobs(
                        user_id=user_id,
                        source_id=source_id,
                        operation=operation,
                        statuses=statuses,
                        newest_first=True,
                        limit=1,
                    )
                    assert newest == (
                        [max(expected, key=lambda job: (job.created_at, job.job_id))]
                        if expected
                        else []
                    )
    for status in ImportStatus:
        assert store.list_jobs(status=status) == [job for job in everything if job.status is status]
    assert store.find_jobs(statuses=()) == []


def test_the_lookup_indexes_arrive_by_migration_and_serve_the_lane_query(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    older = tuple(
        item for item in MIGRATIONS if item.version < IMPORT_JOB_LOOKUP_INDEXES_MIGRATION_VERSION
    )
    asyncio.run(migrate(SqliteMigrationStore(path), older))
    with sqlite3.connect(path) as database:
        before = {
            row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert "import_jobs_lane" not in before

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    assert result.applied == (IMPORT_JOB_LOOKUP_INDEXES_MIGRATION_VERSION,)
    with sqlite3.connect(path) as database:
        indexes = {
            row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        plan = " ".join(
            str(row[-1])
            for row in database.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM import_jobs "
                "WHERE user_id=? AND source_id=? AND operation=? AND status IN (?,?) "
                "ORDER BY created_at DESC LIMIT 1",
                ("local", "fixture", "sync", "pending", "running"),
            )
        )
    assert {"import_jobs_lane", "import_jobs_status"} <= indexes
    assert "import_jobs_lane" in plan
    # Re-running it is a no-op, and a store opened on the database agrees.
    asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))
    ImportJobStore(path)


def test_a_running_home_gets_the_indexes_from_the_store(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    ImportJobStore(path)
    with sqlite3.connect(path) as database:
        database.execute("DROP INDEX import_jobs_lane")
        database.execute("DROP INDEX import_jobs_status")
    ImportJobStore(path)
    with sqlite3.connect(path) as database:
        indexes = {
            row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert {"import_jobs_lane", "import_jobs_status"} <= indexes


def test_the_mysql_rendering_tolerates_an_index_already_there() -> None:
    class Cursor:
        dialect = "mysql"

        def __init__(self) -> None:
            self.attempted: list[str] = []

        async def execute(self, statement: str) -> None:
            self.attempted.append(statement)
            raise RuntimeError(1061, "duplicate key name")

    cursor = Cursor()
    migration = next(
        item for item in MIGRATIONS if item.version == IMPORT_JOB_LOOKUP_INDEXES_MIGRATION_VERSION
    )
    asyncio.run(migration.apply(cursor))
    assert cursor.attempted and "import_jobs" in cursor.attempted[0]


def test_the_import_jobs_route_keeps_its_shape_and_order(tmp_path) -> None:
    async def scenario() -> None:
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path / "home",
                api_token=SecretStr("synthetic"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            )
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer synthetic"},
            ) as client,
        ):
            store: ImportJobStore = app.state.import_job_store
            for minute in (3, 1, 2):
                _job(store, source_id="fixture", minute=minute)
            _job(store, source_id="fixture", operation="history", minute=0)
            _job(store, user_id="other", source_id="hermes", minute=4)

            response = await client.get("/api/v1/import-jobs")
            other = await client.get("/api/v1/import-jobs", params={"user_id": "other"})

            assert response.status_code == 200
            body = response.json()
            expected = [job for job in store.list_jobs() if job.user_id == "local"]
            assert [item["job_id"] for item in body] == [job.job_id for job in expected]
            assert [item["created_at"] for item in body] == sorted(
                item["created_at"] for item in body
            )
            assert set(body[0]) == set(ImportJob.model_fields)
            assert [item["user_id"] for item in other.json()] == ["other"]

    asyncio.run(scenario())


def test_memory_processing_reports_the_write_drain(tmp_path) -> None:
    async def scenario() -> None:
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path / "home",
                api_token=SecretStr("synthetic"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            )
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer synthetic"},
            ) as client,
        ):
            for _ in range(500):
                if app.state.recovery in {"done", "failed"}:
                    break
                await asyncio.sleep(0.01)
            body = (await client.get("/api/v1/memory-processing")).json()
            assert body["write_drain"] == "running"
            assert body["write_drain_error"] is None

            drain = app.state.memory_write_drain
            drain.liveness = "restarting"
            drain.last_error_code = "operational_error"
            body = (await client.get("/api/v1/memory-processing")).json()
            assert body["write_drain"] == "restarting"
            assert body["write_drain_error"] == "operational_error"
            drain.liveness = "running"

    asyncio.run(scenario())
