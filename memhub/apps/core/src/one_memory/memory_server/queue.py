"""A2-03/A2-05: claim and lifecycle for the durable write queue.

The SQL here is the part the architecture is most specific about, because the
adjacent HY Memory App got it wrong first: it ran one claim coroutine per
executor, so 80 coroutines per second fought over one table and produced
`Lock wait timeout exceeded` (1205) even with only a couple of rows queued.
Their fix — and the rule adopted here — is to separate claimers from executors
and keep the claimer count small.

Two SQL details carry that lesson:

- `FOR UPDATE SKIP LOCKED` lets several claimers scan the same table without
  serialising on each other's locks. Without SKIP LOCKED they queue behind one
  another, which is the storm.
- The claim is a single statement with `LIMIT`, not a read-then-write. Reading
  candidate ids and then updating them lets two claimers select the same row
  and one of them overwrite the other's lease.

Leases expire, so a worker that dies holding a job does not block it forever;
`reclaim` returns those to pending. `unknown` is recorded rather than retried,
per A0-03's measurement that HY has no idempotency key.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from one_memory.memory_server.db import Database

logger = logging.getLogger(__name__)

#: Default lease length. Long enough for one HY write, short enough that a
#: crashed worker does not hold a session for long.
DEFAULT_LEASE = timedelta(seconds=120)

#: Claim statement. One statement, bounded by LIMIT, skipping rows another
#: claimer already holds.
CLAIM_SQL = """
SELECT job_id, session_key, batch_id, idempotency_key, payload_json, attempts
FROM memory_write_jobs
WHERE state = 'pending'
  AND (lease_expires_at IS NULL OR lease_expires_at < NOW(6))
ORDER BY priority ASC, created_at
LIMIT %s
FOR UPDATE SKIP LOCKED
"""

#: `priority` is the text `live`/`normal`. `'live' < 'normal'` in the database
#: collation, so `ASC` puts live first — the reserved slot `scheduler.py`
#: depends on — and still matches `idx_write_jobs_claim(state, priority,
#: created_at)` for an ordered scan. `DESC` would order normal before live; a
#: `CASE` rank would be correct but forces a temp B-tree sort.
PRIORITY_ORDER_SQL = "priority ASC"

#: Marks claimed rows as running under one owner, with an expiring lease.
LEASE_SQL = """
UPDATE memory_write_jobs
SET state = 'running',
    lease_owner = %s,
    lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
    attempts = attempts + 1
WHERE job_id IN (%s)
"""

RECLAIM_SQL = """
UPDATE memory_write_jobs
SET state = 'pending',
    lease_owner = NULL,
    lease_expires_at = NULL
WHERE state = 'running'
  AND lease_expires_at IS NOT NULL
  AND lease_expires_at < NOW(6)
  AND hy_invoked = 0
"""

#: A lapsed lease on a job that may already have reached HY. Retrying it would
#: write the memory a second time — HY has no idempotency key (A0-03) — so the
#: only honest terminal state is `unknown`, recorded rather than retried.
#: This is ARCHITECTURE's A2-05 rule and the whole reason `hy_invoked` exists.
RECLAIM_UNKNOWN_SQL = """
UPDATE memory_write_jobs
SET state = 'unknown',
    lease_owner = NULL,
    lease_expires_at = NULL,
    last_error = 'lease lapsed after HY was invoked; outcome unknown'
WHERE state = 'running'
  AND lease_expires_at IS NOT NULL
  AND lease_expires_at < NOW(6)
  AND hy_invoked = 1
"""

#: Recorded *before* the HY call, not after. After the call there may be no
#: process left to record anything, and a crash between the two would leave the
#: job looking retryable when it is not.
MARK_INVOKED_SQL = """
UPDATE memory_write_jobs
SET hy_invoked = 1
WHERE job_id = %s
  AND lease_owner = %s
  AND state = 'running'
"""

#: Extends a lease the holder is still running under. The owner and state
#: guards are the point: a job whose lease already lapsed may have been
#: reclaimed and re-leased by someone else, and renewing it here would put two
#: owners on one job — which is the duplicate-write this queue exists to avoid.
RENEW_LEASE_SQL = """
UPDATE memory_write_jobs
SET lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND)
WHERE job_id = %s
  AND lease_owner = %s
  AND state = 'running'
"""


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    session_key: str
    batch_id: str
    idempotency_key: str
    payload_json: str
    attempts: int
    #: Which claimer leased this row. Carried on the job rather than looked up
    #: later because renewal has to extend *this* owner's lease and no other's.
    owner: str = ""


def lease_covers(lease: timedelta, work_ceiling: timedelta) -> bool:
    """Whether a lease outlives the ceiling on the work it guards.

    HY Memory App had this backwards (lock TTL 330 < outer timeout 360): the
    lock lapsed while the work was still running, a second drain picked the
    same job up, and the memory was written twice. The relation is the rule;
    the renewal below is what keeps it true when the ceiling is only a budget.
    """
    return lease > work_ceiling


async def claim_with(
    database: Database,
    *,
    owner: str,
    limit: int,
    lease: timedelta = DEFAULT_LEASE,
) -> list[ClaimedJob]:
    """Claim jobs inside a single transaction.

    The transaction matters: selecting rows and leasing them must be atomic,
    or two workers can select the same row and one lease silently replaces the
    other's. `FOR UPDATE SKIP LOCKED` only helps while both statements are in
    the same transaction.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")

    claimed: list[ClaimedJob] = []
    async with database.transaction() as cursor:
        await cursor.execute(CLAIM_SQL, (limit,))
        rows = await cursor.fetchall()
        if not rows:
            return []
        job_ids = [row[0] for row in rows]
        placeholders = ", ".join(["%s"] * len(job_ids))
        await cursor.execute(
            LEASE_SQL.replace("IN (%s)", f"IN ({placeholders})"),
            (owner, int(lease.total_seconds()), *job_ids),
        )
        claimed = [
            ClaimedJob(
                job_id=row[0],
                session_key=row[1],
                batch_id=row[2],
                idempotency_key=row[3],
                payload_json=row[4],
                attempts=row[5],
                owner=owner,
            )
            for row in rows
        ]
    return claimed


def build_claim_statements(
    owner: str, limit: int, lease: timedelta = DEFAULT_LEASE
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    """The claim statements in order, as (sql, params) pairs.

    Exposed separately from execution so the SQL can be asserted without a
    database: the ordering and the SKIP LOCKED clause are the whole point, and
    a test that needs a live server to check them would not be run.
    """
    return (
        (CLAIM_SQL, (limit,)),
        (LEASE_SQL, (owner, int(lease.total_seconds()), "")),
    )


def claim_sql_uses_skip_locked() -> bool:
    """Guard for the property that prevents the claim storm."""
    return "FOR UPDATE SKIP LOCKED" in CLAIM_SQL


def build_reclaim_statement() -> tuple[str, tuple[Any, ...]]:
    return (RECLAIM_SQL, ())


def build_reclaim_statements() -> tuple[tuple[str, tuple[Any, ...]], ...]:
    """Both reclaim statements, in the order they must run.

    The split is the requirement: a lapsed lease is either "never reached HY"
    (safe to requeue) or "may have reached HY" (`unknown`, never retried).
    Running only the first would retry jobs whose outcome is unknown, which is
    the duplicate-write bug ARCHITECTURE calls out.
    """
    return (
        (RECLAIM_UNKNOWN_SQL, ()),
        (RECLAIM_SQL, ()),
    )


RECLAIM_SQLITE_SQL = """
UPDATE memory_write_jobs
SET state = ?, lease_owner = NULL, lease_expires_at = NULL, last_error = ?
WHERE state = 'running'
  AND (lease_expires_at IS NULL OR lease_expires_at < ?)
  AND hy_invoked = ?
"""

#: The lease lapsed before HY was reached and the attempt budget is gone.
#: Retrying forever would hide a permanently broken job, so it becomes `dead`.
RECLAIM_SQLITE_EXHAUSTED_SQL = """
UPDATE memory_write_jobs
SET state = 'dead', lease_owner = NULL, lease_expires_at = NULL,
    last_error = 'attempts exhausted before HY was invoked'
WHERE state = 'running'
  AND (lease_expires_at IS NULL OR lease_expires_at < ?)
  AND hy_invoked = 0
  AND attempts >= max_attempts
"""

#: The lease lapsed before HY was reached and the job still has attempts left,
#: so it may be retried.
RECLAIM_SQLITE_PENDING_SQL = """
UPDATE memory_write_jobs
SET state = 'pending', lease_owner = NULL, lease_expires_at = NULL, last_error = ?
WHERE state = 'running'
  AND (lease_expires_at IS NULL OR lease_expires_at < ?)
  AND hy_invoked = 0
  AND attempts < max_attempts
"""


def build_reclaim_sqlite_statements(
    now: str,
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    """The three-way reclaim, rendered for the engine in force (SQLite).

    `RECLAIM_*_SQL` use MySQL's `NOW(6)`; under D56 there is no MySQL, so a
    durable job whose worker died would never be recovered. Unknown first, then
    dead, then pending: the outcome may already be in HY (which has no
    idempotency key), and a never-invoked job with no attempts left must not
    return to the queue forever.
    """
    return (
        (
            RECLAIM_SQLITE_SQL,
            (
                "unknown",
                "lease lapsed after HY was invoked; outcome unknown",
                now,
                1,
            ),
        ),
        (RECLAIM_SQLITE_EXHAUSTED_SQL, (now,)),
        (RECLAIM_SQLITE_PENDING_SQL, ("worker_restarted", now)),
    )


def build_mark_invoked_statement(job_id: str, owner: str) -> tuple[str, tuple[Any, ...]]:
    """Mark a job as having reached HY, before the call is made.

    The `lease_owner` guard keeps a worker that has already lost its lease from
    marking a job it no longer owns, which would strand a requeued job in
    `unknown` forever.
    """
    return (MARK_INVOKED_SQL, (job_id, owner))


# ---------------------------------------------------------------------------
# A2-03: claimers and executors are separate roles.
#
# The architecture is explicit (ARCHITECTURE.md "不要复制 HY Memory App 的旧
# claim 风暴"): one claim coroutine per executor is what produced 80 claims a
# second against a single table and `Lock wait timeout exceeded` (1205) with
# only a couple of rows queued. So the claimer count stays small and fixed,
# executors take work from a bounded in-process queue, and an executor never
# touches the jobs table.
# ---------------------------------------------------------------------------

#: Claimer count. Deliberately small: the table is the contention point, and
#: more claimers buy nothing once they start skipping each other's locks.
DEFAULT_CLAIMER_COUNT = 2

#: Executor count. This is where throughput comes from, and it costs no
#: database contention because executors never claim.
DEFAULT_EXECUTOR_COUNT = 8

#: The hand-off queue between the two. Bounded so a slow HY cannot grow memory
#: without limit; a claimer that finds it full stops claiming, which is the
#: backpressure.
DEFAULT_QUEUE_BOUND = 64

#: How long a claimer waits before asking again when there is nothing to do.
#: Not zero: a claimer polling with `sleep(0)` would issue empty claims as fast
#: as the loop runs, which is the claim storm in a quieter form. Not long
#: either, or live work waits.
DEFAULT_CLAIM_IDLE_PAUSE = 0.25


@dataclass(frozen=True)
class QueueRoles:
    """The two counts, configured separately.

    Separate configuration is the requirement. A shared number cannot express
    "few claimers, many executors", which is the entire lesson.
    """

    claimers: int = DEFAULT_CLAIMER_COUNT
    executors: int = DEFAULT_EXECUTOR_COUNT
    bound: int = DEFAULT_QUEUE_BOUND

    def __post_init__(self) -> None:
        if self.claimers < 1:
            raise ValueError("claimers must be at least 1")
        if self.executors < 1:
            raise ValueError("executors must be at least 1")
        if self.bound < 1:
            raise ValueError("bound must be at least 1")


def claimer_count(roles: QueueRoles) -> int:
    """How many coroutines may touch the jobs table. Always small."""
    return roles.claimers


def executor_count(roles: QueueRoles) -> int:
    """How many jobs may run at once. Never claims."""
    return roles.executors


def handoff_bound(roles: QueueRoles) -> int:
    """The in-process queue's capacity, so backpressure has a number."""
    return roles.bound


class WriteQueuePipelines:
    """Runs a small fixed set of claimers feeding a larger pool of executors.

    The point is the asymmetry: only `roles.claimers` coroutines ever scan and
    claim the jobs table, however many executors are running. Executors take
    from a bounded in-process queue and update only the lifecycle of the exact
    lease they hold, so raising throughput cannot create a claim storm.

    `execute` is injected and is called with the claimed job; it is expected to
    make the HY call. `mark_invoked` is called *before* it, so a crash mid-call
    leaves the job marked and therefore routed to `unknown` rather than
    silently retried.
    """

    def __init__(
        self,
        database: Database,
        *,
        execute: Callable[[ClaimedJob], Awaitable[None]],
        roles: QueueRoles | None = None,
        lease: timedelta = DEFAULT_LEASE,
        claim_limit: int | None = None,
        idle_pause: float = DEFAULT_CLAIM_IDLE_PAUSE,
        renew_every: timedelta | None = None,
    ) -> None:
        self._database = database
        self._execute = execute
        self._roles = roles or QueueRoles()
        self._lease = lease
        # A claimer asks for at most what the queue can hold, so it never
        # leases rows it then has nowhere to put.
        self._claim_limit = claim_limit or self._roles.bound
        self._idle_pause = idle_pause
        # Renew at a fraction of the lease, so several attempts fit inside one
        # lease even if one of them is slow or the loop is briefly descheduled.
        # A quarter is the same shape as the lease/timeout relation: the lease
        # has to survive missing a renewal or two, or renewal buys nothing.
        self._renew_every = (
            renew_every
            if renew_every is not None
            else timedelta(seconds=max(0.1, lease.total_seconds() / 4))
        )
        self._handoff: asyncio.Queue[ClaimedJob] = asyncio.Queue(maxsize=self._roles.bound)
        self._stop = asyncio.Event()
        self._claimer_tasks: list[asyncio.Task[None]] = []
        self._executor_tasks: list[asyncio.Task[None]] = []
        self._renewal_tasks: list[asyncio.Task[None]] = []
        self.claims = 0
        self.executed = 0

    @property
    def claimers(self) -> int:
        return self._roles.claimers

    @property
    def executors(self) -> int:
        return self._roles.executors

    async def start(self) -> None:
        if self._claimer_tasks or self._executor_tasks:
            return
        self._stop = asyncio.Event()
        self._claimer_tasks = [
            asyncio.create_task(self._claim_loop(index), name=f"claimer-{index}")
            for index in range(self._roles.claimers)
        ]
        self._executor_tasks = [
            asyncio.create_task(self._execute_loop(), name=f"executor-{index}")
            for index in range(self._roles.executors)
        ]

    async def stop(self) -> None:
        self._stop.set()
        for task in (*self._claimer_tasks, *self._executor_tasks, *self._renewal_tasks):
            task.cancel()
        for task in (*self._claimer_tasks, *self._executor_tasks, *self._renewal_tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._claimer_tasks = []
        self._executor_tasks = []
        self._renewal_tasks = []

    async def _claim_loop(self, index: int) -> None:
        """The only coroutine that reads the jobs table."""
        while not self._stop.is_set():
            if self._handoff.full():
                # Backpressure: claiming more would lease rows this process
                # cannot start, holding them until their lease lapses.
                await asyncio.sleep(self._idle_pause)
                continue
            room = self._handoff.maxsize - self._handoff.qsize()
            jobs = await claim_with(
                self._database,
                # Per-claimer owner, so a lease is attributable to the exact
                # coroutine holding it rather than to the process.
                owner=f"claimer-{index}",
                limit=min(self._claim_limit, room),
                lease=self._lease,
            )
            if not jobs:
                # Nothing claimable. Polling with sleep(0) would issue empty
                # claims as fast as the loop runs — the storm in quieter form.
                await asyncio.sleep(self._idle_pause)
                continue
            self.claims += 1
            for job in jobs:
                await self._handoff.put(job)

    async def _execute_loop(self) -> None:
        """Never touches the jobs table; only runs claimed work."""
        while not self._stop.is_set():
            try:
                job = await asyncio.wait_for(self._handoff.get(), timeout=0.05)
            except TimeoutError:
                continue
            try:
                renewal = asyncio.create_task(self._renew_loop(job), name=f"renew-{job.job_id}")
                self._renewal_tasks.append(renewal)
                try:
                    await self._mark_invoked(job)
                    await self._execute(job)
                except Exception as error:
                    # If the invocation marker failed, HY was not called and
                    # reclaim can safely return the expired lease to pending.
                    # If execute failed after the marker, reclaim routes it to
                    # unknown. Either way one bad job must not kill a worker.
                    # Log the type, not the message: it may carry provider or
                    # user content (see docs/contracts/hy-failure-classification).
                    logger.error(
                        "write_queue_job_execution_failed job_id=%s error_type=%s",
                        job.job_id,
                        type(error).__name__,
                    )
                finally:
                    # Stop extending a lease for work that is over. Left
                    # running, it would keep a finished job looking live and
                    # hold off reclaim indefinitely.
                    renewal.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await renewal
                    with contextlib.suppress(ValueError):
                        self._renewal_tasks.remove(renewal)
                self.executed += 1
            finally:
                self._handoff.task_done()

    async def _renew_loop(self, job: ClaimedJob) -> None:
        """Keep the lease alive for as long as the job is running.

        A lease long enough on paper still lapses in practice: a timeout is a
        budget, not a promise, and a slow disk or a wedged model can run past
        it. When that happens after `hy_invoked` was recorded the job goes to
        `unknown` — a write that may well have succeeded, recorded as lost,
        and never retried because HY has no idempotency key (A0-03).
        """
        owner = self._owner_for(job)
        interval = self._renew_every.total_seconds()
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            try:
                await self._renew(job.job_id, owner)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A transient database error must not end the renewal for good.
                # This loop is the only thing keeping the lease alive, and a
                # lapsed lease lets another worker claim the job and run it a
                # second time -- the outcome this method exists to prevent.
                logger.warning("lease renewal failed for %s; retrying", job.job_id, exc_info=True)

    def _owner_for(self, job: ClaimedJob) -> str:
        return job.owner or "claimer-unknown"

    async def _renew(self, job_id: str, owner: str) -> None:
        """Extend one lease, or silently do nothing if it is no longer ours.

        A renewal that lost its job is not an error worth surfacing: the job
        finished, or it was reclaimed because the lease really did lapse, and
        both are already handled elsewhere. What matters is that we never
        extend a lease someone else now holds.
        """
        async with self._database.transaction() as cursor:
            await cursor.execute(RENEW_LEASE_SQL, (int(self._lease.total_seconds()), job_id, owner))

    async def _mark_invoked(self, job: ClaimedJob) -> None:
        """Persist the no-blind-retry boundary before entering the HY handler."""
        sql, params = build_mark_invoked_statement(job.job_id, self._owner_for(job))
        async with self._database.transaction() as cursor:
            await cursor.execute(sql, params)
            if cursor.rowcount != 1:
                raise RuntimeError("write queue lease was lost before HY invocation")
