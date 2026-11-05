"""A2-03: the claim contract, verified without a live MySQL.

The SQL is the deliverable here. HY Memory App's claim storm came from SQL
shape, not from Python, so what needs testing is the statement itself:
`FOR UPDATE SKIP LOCKED`, a bounded LIMIT, and claim+lease in one transaction.

Execution is faked at the cursor so the ordering of statements is observable.
That still tests the real code path — the sequencing is ours, not the driver's.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest

from one_memory.memory_server.queue import (
    CLAIM_SQL,
    DEFAULT_CLAIMER_COUNT,
    DEFAULT_EXECUTOR_COUNT,
    DEFAULT_LEASE,
    DEFAULT_QUEUE_BOUND,
    LEASE_SQL,
    MARK_INVOKED_SQL,
    RECLAIM_SQL,
    RECLAIM_UNKNOWN_SQL,
    ClaimedJob,
    QueueRoles,
    WriteQueuePipelines,
    build_claim_statements,
    build_mark_invoked_statement,
    build_reclaim_sqlite_statements,
    build_reclaim_statement,
    build_reclaim_statements,
    claim_sql_uses_skip_locked,
    claim_with,
    claimer_count,
    executor_count,
    handoff_bound,
)


class FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.statements: list[tuple[str, tuple]] = []
        self.rowcount = 1

    async def execute(self, sql: str, params: tuple = ()) -> None:
        self.statements.append((sql, params))

    async def fetchall(self) -> list[tuple]:
        return self._rows


class FakeDatabase:
    """Minimal stand-in exposing only `transaction()`, as `Database` does."""

    def __init__(self, rows: list[tuple]) -> None:
        self.cursor = FakeCursor(rows)
        self.committed = False

    def transaction(self) -> Any:
        outer = self

        class _Tx:
            async def __aenter__(self) -> FakeCursor:
                return outer.cursor

            async def __aexit__(self, *_exc: object) -> None:
                outer.committed = True
                return None

        return _Tx()


def _row(job_id: str = "j1") -> tuple:
    return (job_id, "sess-1", "batch-1", "idem-1", '{"a":1}', 0)


def test_claim_uses_skip_locked() -> None:
    """Without SKIP LOCKED, claimers serialise on each other's locks.

    That serialisation is exactly the storm HY Memory App hit: 80 coroutines
    per second against one table, timing out with only a few rows queued.

    Asserted on the exact clause, not on "FOR UPDATE". A guard that only looks
    for FOR UPDATE passes on a statement that locks rows without skipping them,
    which is precisely the failure being guarded against.
    """
    assert claim_sql_uses_skip_locked() is True
    assert "SKIP LOCKED" in CLAIM_SQL


def test_claim_is_bounded() -> None:
    """An unbounded claim would let one worker drain the queue and starve others."""
    assert "LIMIT" in CLAIM_SQL


def test_claim_orders_by_priority_then_age() -> None:
    """Live injections first, then oldest work; ties must not be arbitrary."""
    # `'live' < 'normal'`, so ASC is what puts live first. DESC would invert
    # them, and a CASE rank would force a temp B-tree sort off the claim index.
    assert "ORDER BY priority ASC, created_at" in CLAIM_SQL
    assert "priority DESC" not in CLAIM_SQL
    assert "CASE priority" not in CLAIM_SQL


def test_claim_only_takes_pending_or_expired() -> None:
    """A job another worker holds is not claimable until its lease lapses."""
    assert "state = 'pending'" in CLAIM_SQL
    assert "lease_expires_at < NOW(6)" in CLAIM_SQL


def test_claim_and_lease_run_in_one_transaction() -> None:
    """Selecting and leasing must be atomic.

    Across two transactions, two workers can select the same row and one lease
    silently replaces the other's, so the job runs twice.
    """

    async def run() -> FakeCursor:
        database = FakeDatabase([_row()])
        await claim_with(database, owner="w1", limit=5)  # type: ignore[arg-type]
        return database.cursor

    cursor = asyncio.run(run())
    assert len(cursor.statements) == 2
    assert "SELECT" in cursor.statements[0][0]
    assert "UPDATE" in cursor.statements[1][0]


def test_claim_leases_every_row_it_selected() -> None:
    """A row selected but not leased is a job that runs twice later."""

    async def run() -> FakeCursor:
        database = FakeDatabase([_row("j1"), _row("j2")])
        await claim_with(database, owner="w1", limit=5)  # type: ignore[arg-type]
        return database.cursor

    cursor = asyncio.run(run())
    update_params = cursor.statements[1][1]
    assert update_params[0] == "w1"
    assert "j1" in update_params and "j2" in update_params


def test_claim_returns_typed_jobs() -> None:
    async def run() -> list[ClaimedJob]:
        database = FakeDatabase([_row()])
        return await claim_with(database, owner="w1", limit=5)  # type: ignore[arg-type]

    jobs = asyncio.run(run())
    assert [job.job_id for job in jobs] == ["j1"]
    assert jobs[0].session_key == "sess-1"
    assert jobs[0].idempotency_key == "idem-1"


def test_claiming_nothing_does_not_lease() -> None:
    """No rows means no UPDATE; an empty IN () would not even be valid SQL."""

    async def run() -> FakeCursor:
        database = FakeDatabase([])
        await claim_with(database, owner="w1", limit=5)  # type: ignore[arg-type]
        return database.cursor

    cursor = asyncio.run(run())
    assert len(cursor.statements) == 1


def test_zero_limit_is_rejected() -> None:
    async def run() -> None:
        with pytest.raises(ValueError):
            await claim_with(FakeDatabase([_row()]), owner="w1", limit=0)  # type: ignore[arg-type]

    asyncio.run(run())


def test_lease_has_a_deadline() -> None:
    """A lease without expiry would block a job forever if the worker dies."""
    assert "lease_expires_at = DATE_ADD" in LEASE_SQL
    assert timedelta(seconds=120) == DEFAULT_LEASE


def test_claim_attempts_are_counted() -> None:
    """Attempts drive the retry limit; without it a bad job loops forever."""
    assert "attempts = attempts + 1" in LEASE_SQL


def test_reclaim_returns_expired_leases_to_pending() -> None:
    sql, _params = build_reclaim_statement()
    assert "state = 'pending'" in sql
    assert "lease_expires_at < NOW(6)" in sql


def test_reclaim_never_requeues_a_job_that_reached_hy() -> None:
    """ARCHITECTURE A2-05: only "certainly never called HY" may be requeued.

    HY has no idempotency key (A0-03), so requeuing a job that may already
    have reached it writes the memory twice. The safe path is `unknown`.
    """
    assert "hy_invoked = 0" in RECLAIM_SQL


def test_reclaim_unknown_routes_lapsed_invoked_jobs_to_unknown() -> None:
    assert "state = 'unknown'" in RECLAIM_UNKNOWN_SQL
    assert "hy_invoked = 1" in RECLAIM_UNKNOWN_SQL


def test_reclaim_returns_both_statements_in_order() -> None:
    """Both must run; running only the requeue would retry unknown outcomes."""
    statements = build_reclaim_statements()
    assert [sql for sql, _ in statements] == [RECLAIM_UNKNOWN_SQL, RECLAIM_SQL]


def test_reclaim_statements_partition_every_lapsed_job() -> None:
    """`hy_invoked` is a boolean: every lapsed job lands in exactly one path.

    A gap would strand jobs in `running` forever; an overlap would make the
    outcome depend on statement order.
    """
    assert "hy_invoked = 1" in RECLAIM_UNKNOWN_SQL
    assert "hy_invoked = 0" in RECLAIM_SQL


def test_sqlite_reclaim_partitions_into_unknown_dead_and_pending() -> None:
    """D56's SQLite reclaim must keep the three outcomes disjoint.

    `hy_invoked=1` is always `unknown`; a never-invoked job with budget left is
    `pending`; a never-invoked job with no budget left is `dead`. A missing
    branch would strand the row in `running`; an overlapping branch would make
    the outcome depend on statement order.
    """
    statements = build_reclaim_sqlite_statements("2026-09-29T00:00:00+00:00")
    assert len(statements) == 3

    unknown_sql = statements[0][0]
    dead_sql = statements[1][0]
    pending_sql = statements[2][0]

    assert "state = ?" in unknown_sql and statements[0][1][0] == "unknown"
    assert "hy_invoked = ?" in unknown_sql and statements[0][1][3] == 1

    assert "state = 'dead'" in dead_sql
    assert "hy_invoked = 0" in dead_sql
    assert "attempts >= max_attempts" in dead_sql

    assert "state = 'pending'" in pending_sql
    assert "hy_invoked = 0" in pending_sql
    assert "attempts < max_attempts" in pending_sql


def test_mark_invoked_is_scoped_to_the_lease_holder() -> None:
    """A worker that lost its lease must not mark a job it no longer owns.

    Doing so would strand a legitimately requeued job in `unknown`, where
    nothing will ever pick it up again.
    """
    sql, params = build_mark_invoked_statement("j1", "w1")
    assert "hy_invoked = 1" in sql
    assert "lease_owner = %s" in sql
    assert "state = 'running'" in sql
    assert params == ("j1", "w1")


def test_mark_invoked_sql_is_the_documented_constant() -> None:
    _sql, _params = build_mark_invoked_statement("j1", "w1")
    assert _sql is MARK_INVOKED_SQL


def test_build_claim_statements_is_ordered() -> None:
    statements = build_claim_statements("w1", 3)
    assert len(statements) == 2
    assert statements[0][0] is CLAIM_SQL
    assert statements[1][0] is LEASE_SQL
    assert statements[0][1] == (3,)


# ---------------------------------------------------------------------------
# A2-03: claimers and executors are separate roles.
#
# ARCHITECTURE.md is explicit: "不要复制 HY Memory App 的旧 claim 风暴 …
# 将 claimer 与 executor 分开", and "claimer 数量和 executor 数量分开配置；
# 禁止每个 executor 高频抢表". These tests pin the shape, because the failure
# mode (one claimer per executor) is invisible in a passing single-worker test.
# ---------------------------------------------------------------------------


def test_claimers_and_executors_are_configured_separately() -> None:
    """The whole point: few claimers, many executors, two numbers."""
    roles = QueueRoles(claimers=1, executors=16)
    assert claimer_count(roles) == 1
    assert executor_count(roles) == 16
    assert claimer_count(roles) != executor_count(roles)


def test_default_claimers_stay_small() -> None:
    """The storm came from claimer count, so the default must stay small.

    This is the guard against "just raise concurrency": that would raise both
    numbers, which is the original bug.
    """
    assert DEFAULT_CLAIMER_COUNT <= 4
    assert DEFAULT_CLAIMER_COUNT < DEFAULT_EXECUTOR_COUNT


def test_executor_count_does_not_change_claimer_count() -> None:
    """Raising throughput must not raise table contention."""
    small = QueueRoles(claimers=2, executors=4)
    large = QueueRoles(claimers=2, executors=64)
    assert claimer_count(small) == claimer_count(large) == 2


def test_the_handoff_queue_is_bounded() -> None:
    """Unbounded buffering hides backpressure: a slow HY grows memory."""
    assert handoff_bound(QueueRoles()) == DEFAULT_QUEUE_BOUND
    with pytest.raises(ValueError):
        QueueRoles(bound=0)


def test_roles_reject_zero_of_either() -> None:
    with pytest.raises(ValueError):
        QueueRoles(claimers=0)
    with pytest.raises(ValueError):
        QueueRoles(executors=0)


def test_pipelines_start_the_two_pools_at_their_configured_sizes() -> None:
    """Real coroutines, counted: 2 claimers and 5 executors, not 5 of each."""

    async def run() -> None:
        roles = QueueRoles(claimers=2, executors=5)
        pipelines = WriteQueuePipelines(
            FakeDatabase([]),  # type: ignore[arg-type]
            execute=_noop,
            roles=roles,
        )
        await pipelines.start()
        try:
            assert pipelines.claimers == 2
            assert pipelines.executors == 5
            names = {task.get_name() for task in pipelines._claimer_tasks}  # noqa: SLF001
            assert names == {"claimer-0", "claimer-1"}
            assert len(pipelines._executor_tasks) == 5  # noqa: SLF001
        finally:
            await pipelines.stop()

    asyncio.run(run())


def test_executor_marks_the_owned_job_invoked_before_calling_hy() -> None:
    observed: list[bool] = []
    finished = asyncio.Event()
    database = FakeDatabase([])

    async def execute(_job: ClaimedJob) -> None:
        observed.append(
            any(
                sql is MARK_INVOKED_SQL and params == ("j1", "claimer-0")
                for sql, params in database.cursor.statements
            )
        )
        finished.set()

    async def run() -> None:
        pipelines = WriteQueuePipelines(
            database,  # type: ignore[arg-type]
            execute=execute,
            roles=QueueRoles(claimers=1, executors=1),
        )
        await pipelines.start()
        try:
            await pipelines._handoff.put(  # noqa: SLF001
                ClaimedJob(
                    job_id="j1",
                    session_key="sess-1",
                    batch_id="batch-1",
                    idempotency_key="idem-1",
                    payload_json="{}",
                    attempts=1,
                    owner="claimer-0",
                )
            )
            await asyncio.wait_for(finished.wait(), 1)
        finally:
            await pipelines.stop()

    asyncio.run(run())
    assert observed == [True]


def test_failed_invocation_marker_skips_hy_and_keeps_executor_alive() -> None:
    executed: list[str] = []
    second_finished = asyncio.Event()
    database = FakeDatabase([])
    original_execute = database.cursor.execute
    failed_once = False

    async def fail_first_marker(sql: str, params: tuple = ()) -> None:
        nonlocal failed_once
        if sql is MARK_INVOKED_SQL and not failed_once:
            failed_once = True
            raise RuntimeError("synthetic marker failure")
        await original_execute(sql, params)

    database.cursor.execute = fail_first_marker  # type: ignore[method-assign]

    async def execute(job: ClaimedJob) -> None:
        executed.append(job.job_id)
        second_finished.set()

    async def run() -> None:
        pipelines = WriteQueuePipelines(
            database,  # type: ignore[arg-type]
            execute=execute,
            roles=QueueRoles(claimers=1, executors=1),
        )
        await pipelines.start()
        try:
            for job_id in ("first", "second"):
                await pipelines._handoff.put(  # noqa: SLF001
                    ClaimedJob(
                        job_id=job_id,
                        session_key="sess-1",
                        batch_id=f"batch-{job_id}",
                        idempotency_key=f"idem-{job_id}",
                        payload_json="{}",
                        attempts=1,
                        owner="claimer-0",
                    )
                )
            await asyncio.wait_for(second_finished.wait(), 1)
        finally:
            await pipelines.stop()

    asyncio.run(run())
    assert executed == ["second"]


def test_lost_lease_before_invocation_never_calls_hy() -> None:
    executed: list[str] = []
    database = FakeDatabase([])
    database.cursor.rowcount = 0

    async def execute(job: ClaimedJob) -> None:
        executed.append(job.job_id)

    async def run() -> None:
        pipelines = WriteQueuePipelines(
            database,  # type: ignore[arg-type]
            execute=execute,
            roles=QueueRoles(claimers=1, executors=1),
        )
        await pipelines.start()
        try:
            await pipelines._handoff.put(  # noqa: SLF001
                ClaimedJob(
                    job_id="lost",
                    session_key="sess-1",
                    batch_id="batch-lost",
                    idempotency_key="idem-lost",
                    payload_json="{}",
                    attempts=1,
                    owner="old-owner",
                )
            )
            await asyncio.sleep(0.1)
        finally:
            await pipelines.stop()

    asyncio.run(run())
    assert executed == []


async def _noop(job: ClaimedJob) -> None:
    return None


async def test_renew_loop_keeps_renewing_after_an_error() -> None:
    """One failed renewal must not end the loop for the rest of the job.

    ``_renew_loop`` is the only thing keeping the lease alive.  If a transient
    database error killed it, the lease would lapse, another worker would claim
    the job and the work would run twice -- the outcome the loop exists to
    prevent.
    """
    import asyncio
    from datetime import timedelta
    from types import SimpleNamespace

    from one_memory.memory_server.queue import WriteQueuePipelines

    worker = WriteQueuePipelines.__new__(WriteQueuePipelines)
    worker._stop = asyncio.Event()
    worker._renew_every = timedelta(seconds=0)
    worker._owner_for = lambda _job: "owner"  # type: ignore[method-assign]

    calls: list[str] = []

    async def renew(job_id: str, _owner: str) -> None:
        calls.append(job_id)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        if len(calls) == 3:
            worker._stop.set()

    worker._renew = renew  # type: ignore[method-assign]

    await asyncio.wait_for(worker._renew_loop(SimpleNamespace(job_id="job-1")), timeout=5)

    assert len(calls) == 3
