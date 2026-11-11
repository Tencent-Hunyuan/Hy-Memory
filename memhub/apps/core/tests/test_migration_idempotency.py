"""A migration interrupted after a statement must be replayable, not fatal.

`SqliteMigrationStore.execute` commits every statement on its own, so a crash
can land between "the statement succeeded" and "the ledger recorded the
version". The next start then replays the whole migration. Any unguarded
`CREATE TABLE` or `ALTER TABLE ... ADD COLUMN` in that replay fails with
"already exists" and the database can never be opened again.

These tests reproduce that crash for every statement of migrations 40, 44, 56
and 61 — the four that had no `IF NOT EXISTS` or column check — and assert the
retry completes. The fake-cursor tests cover the MySQL rendering, which this
suite cannot execute locally.
"""

from __future__ import annotations

import asyncio

import pytest

from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS, tail_handoff_ddl

#: Migrations whose replay used to fail permanently after a crash.
REPLAYABLE_VERSIONS = (40, 44, 56, 61)


class CrashAfterNth(SqliteMigrationStore):
    """Raises after the Nth `execute`, i.e. after that statement committed.

    `record` disarms the counter: the crash must happen before the ledger
    write, which is exactly the window the real store leaves open.
    """

    def __init__(self, path, stop_after: int) -> None:
        super().__init__(path)
        self.stop_after = stop_after
        self.count = 0
        self.armed = False

    async def execute(self, sql: str) -> None:
        await super().execute(sql)
        if not self.armed:
            return
        self.count += 1
        if self.count >= self.stop_after:
            raise RuntimeError(f"crash after statement {self.count}")

    async def record(self, migration) -> None:
        self.armed = False
        await super().record(migration)


def _migration(version: int):
    return next(item for item in MIGRATIONS if item.version == version)


def _prefix(version: int):
    return tuple(item for item in MIGRATIONS if item.version < version)


@pytest.mark.parametrize("version", REPLAYABLE_VERSIONS)
def test_migration_replays_after_a_crash_before_the_ledger_write(tmp_path, version) -> None:
    """Every statement is a possible crash point; each must replay cleanly."""
    migration = _migration(version)
    prefix = _prefix(version)

    # Count this migration's statements on a throwaway database.
    counting_path = tmp_path / f"count-{version}.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(counting_path), prefix))
    counter = CrashAfterNth(counting_path, 10**9)
    counter.armed = True
    asyncio.run(migrate(counter, (migration,)))
    statement_count = counter.count
    assert statement_count >= 1, "the crash injector never saw a statement"

    for stop in range(1, statement_count + 1):
        path = tmp_path / f"crash-{version}-{stop}.sqlite3"
        asyncio.run(migrate(SqliteMigrationStore(path), prefix))

        crashing = CrashAfterNth(path, stop)
        crashing.armed = True
        with pytest.raises(RuntimeError, match=f"crash after statement {stop}"):
            asyncio.run(migrate(crashing, (migration,)))

        # The real startup path: a fresh store retries the unrecorded version.
        result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))
        assert version in result.applied, f"v{version} was not retried after stmt {stop}"
        recorded = asyncio.run(SqliteMigrationStore(path).applied_versions())
        assert recorded == {item.version for item in MIGRATIONS}


def test_tail_handoff_ddl_is_statement_level_idempotent_for_both_dialects() -> None:
    """v40 has no column to check, so its DDL itself must be replay-safe."""
    for dialect in ("sqlite", "mysql"):
        ddl = tail_handoff_ddl(dialect)
        assert "CREATE TABLE IF NOT EXISTS tail_handoff" in ddl
    sqlite = tail_handoff_ddl("sqlite")
    assert "CREATE INDEX IF NOT EXISTS tail_handoff_claim" in sqlite
    assert "CREATE INDEX IF NOT EXISTS tail_handoff_lease" in sqlite


def test_044_skips_an_existing_hy_invocation_count_column() -> None:
    from one_memory.memory_server.migrations import _044_memory_task_hy_invocations

    class Cursor:
        dialect = "mysql"

        def __init__(self, columns: set[str]) -> None:
            self.existing = columns
            self.executed: list[str] = []

        async def columns(self, table: str) -> set[str]:
            assert table == "memory_tasks"
            return self.existing

        async def execute(self, sql: str) -> None:
            self.executed.append(sql)

    already = Cursor({"task_id", "hy_invocation_count"})
    asyncio.run(_044_memory_task_hy_invocations(already))
    assert already.executed == []

    missing = Cursor({"task_id"})
    asyncio.run(_044_memory_task_hy_invocations(missing))
    assert len(missing.executed) == 1
    assert "hy_invocation_count" in missing.executed[0]


def test_056_skips_existing_receipt_columns_but_still_creates_the_table() -> None:
    from one_memory.memory_server.migrations import _056_sdk_write_receipts

    class Cursor:
        dialect = "mysql"

        def __init__(self, columns: set[str]) -> None:
            self.existing = columns
            self.executed: list[str] = []

        async def columns(self, table: str) -> set[str]:
            assert table == "memory_write_jobs"
            return self.existing

        async def execute(self, sql: str) -> None:
            self.executed.append(sql)

    # The first ALTER committed before the crash; only the rest may run.
    partial = Cursor({"job_id", "sdk_raw_ids_json"})
    asyncio.run(_056_sdk_write_receipts(partial))
    assert len(partial.executed) == 2
    assert "sdk_memory_ids_json" in partial.executed[0]
    assert "memory_task_sdk_receipts" in partial.executed[1]

    # Both ALTERs and the table committed; a replay only re-runs the
    # `IF NOT EXISTS` table DDL, never another ADD COLUMN.
    complete = Cursor({"job_id", "sdk_raw_ids_json", "sdk_memory_ids_json"})
    asyncio.run(_056_sdk_write_receipts(complete))
    assert len(complete.executed) == 1
    assert "CREATE TABLE IF NOT EXISTS memory_task_sdk_receipts" in complete.executed[0]


def test_061_skips_existing_retry_columns() -> None:
    from one_memory.memory_server.migrations import _061_write_job_retries

    class Cursor:
        dialect = "mysql"

        def __init__(self, columns: set[str]) -> None:
            self.existing = columns
            self.executed: list[str] = []

        async def columns(self, table: str) -> set[str]:
            assert table == "memory_write_jobs"
            return self.existing

        async def execute(self, sql: str) -> None:
            self.executed.append(sql)

    partial = Cursor({"job_id", "sdk_attempt"})
    asyncio.run(_061_write_job_retries(partial))
    assert len(partial.executed) == 1
    assert "retries" in partial.executed[0]

    complete = Cursor({"job_id", "sdk_attempt", "retries"})
    asyncio.run(_061_write_job_retries(complete))
    assert complete.executed == []
