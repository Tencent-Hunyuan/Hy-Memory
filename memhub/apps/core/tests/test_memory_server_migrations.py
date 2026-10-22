"""A1-03: migrations apply in order, are recorded, and are retried on failure.

The property that matters is that a failed migration is *not* recorded. If it
were, the next start would believe the database reached a version it never did,
and the missing table would surface much later as a confusing runtime error.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from one_memory.memory_server.migration_runner import (
    InMemoryMigrationStore,
    MigrationResult,
    MigrationSchemaMismatchError,
    ledger_sql,
    migrate,
)
from one_memory.memory_server.migrations import (
    MIGRATIONS,
    Migration,
    current_version,
    pending_migrations,
    plan_upgrade,
    validate_migrations,
)


def _migration(version: int, store: InMemoryMigrationStore | None = None) -> Migration:
    async def apply(target: object) -> None:
        if isinstance(target, InMemoryMigrationStore):
            await target.execute(f"-- migration {version}")

    return Migration(version, f"m{version}", apply)


def test_duplicate_versions_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_migrations([_migration(1), _migration(1)])


def test_out_of_order_migrations_are_rejected() -> None:
    """Order must be explicit; list order would differ across machines."""
    with pytest.raises(ValueError, match="ascending"):
        validate_migrations([_migration(2), _migration(1)])


def test_shipped_migrations_are_valid() -> None:
    validate_migrations(MIGRATIONS)
    assert [item.version for item in MIGRATIONS] == sorted(item.version for item in MIGRATIONS)


def test_each_migration_metadata_names_the_tables_its_ddl_creates(tmp_path) -> None:
    """A newly created table must also be visible to the recorded-schema audit.

    The audit can only fail closed for tables a migration declares. A migration
    that creates a table but omits it from `tables` silently recreates the gap
    this test is meant to prevent.
    """
    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "one-memory.sqlite3"
    store = SqliteMigrationStore(path)
    recorded: list[tuple[int, str, set[str]]] = []

    for migration in MIGRATIONS:
        with main_database(path).read() as connection:
            before = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        asyncio.run(migrate(store, (migration,)))
        with main_database(path).read() as connection:
            after = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        recorded.append((migration.version, migration.name, after - before))

    for version, name, created in recorded:
        migration = next(item for item in MIGRATIONS if item.version == version)
        assert created <= set(migration.tables), (
            f"migration {version} {name} creates undeclared tables: "
            f"{sorted(created - set(migration.tables))}"
        )


def test_governance_candidate_shadow_is_owned_by_migration_32() -> None:
    """Business modules must not define the candidate-shadow schema."""
    from one_memory.memory_server.migrations import MIGRATIONS, governance_ddl

    migration = next(item for item in MIGRATIONS if item.version == 32)
    assert {"governed_memories_terms", "governed_memories_terms_meta"} <= set(migration.tables)
    sqlite = governance_ddl("sqlite")
    assert "CREATE TABLE IF NOT EXISTS governed_memories_terms " in sqlite
    assert "CREATE TABLE IF NOT EXISTS governed_memories_terms_meta " in sqlite


def test_sdk_data_plane_cutover_ledger_is_owned_by_migration_55() -> None:
    from one_memory.memory_server.migrations import (
        MIGRATIONS,
        sdk_data_plane_migration_ddl,
    )

    migration = next(item for item in MIGRATIONS if item.version == 55)
    assert set(migration.tables) == {
        "sdk_data_plane_migrations",
        "sdk_data_plane_migration_items",
        "memory_id_aliases",
        "sdk_data_plane_cutover",
    }
    for dialect in ("sqlite", "mysql"):
        ddl = sdk_data_plane_migration_ddl(dialect)
        for table in migration.tables:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
        assert "UNIQUE" in ddl
        assert "operation_key" in ddl


def test_sdk_write_receipts_are_owned_by_migration_56() -> None:
    from one_memory.memory_server.migrations import MIGRATIONS, sdk_write_receipts_ddl

    migration = next(item for item in MIGRATIONS if item.version == 56)
    assert migration.tables == ("memory_task_sdk_receipts",)
    for dialect in ("sqlite", "mysql"):
        ddl = sdk_write_receipts_ddl(dialect)
        assert "sdk_raw_ids_json" in ddl
        assert "sdk_memory_ids_json" in ddl
        assert "CREATE TABLE IF NOT EXISTS memory_task_sdk_receipts" in ddl


def test_sdk_matter_references_are_owned_by_migration_57() -> None:
    from one_memory.memory_server.migrations import MIGRATIONS, sdk_matter_references_ddl

    migration = next(item for item in MIGRATIONS if item.version == 57)
    assert set(migration.tables) == {"raw_id_aliases", "matter_sdk_versions"}
    for dialect in ("sqlite", "mysql"):
        ddl = sdk_matter_references_ddl(dialect)
        assert "CREATE TABLE IF NOT EXISTS raw_id_aliases" in ddl
        assert "CREATE TABLE IF NOT EXISTS matter_sdk_versions" in ddl
        assert "statement" not in ddl
        assert "quote" not in ddl


def test_sdk_matter_notes_are_owned_by_migration_58() -> None:
    from one_memory.memory_server.migrations import MIGRATIONS, sdk_matter_notes_ddl

    migration = next(item for item in MIGRATIONS if item.version == 58)
    assert migration.tables == ("matter_sdk_notes",)
    for dialect in ("sqlite", "mysql"):
        ddl = sdk_matter_notes_ddl(dialect)
        assert "CREATE TABLE IF NOT EXISTS matter_sdk_notes" in ddl
        assert "raw_id" in ddl
        assert "text" not in ddl


def test_plan_upgrade_from_zero_is_everything() -> None:
    expected = [item.version for item in MIGRATIONS]
    assert [item.version for item in plan_upgrade(MIGRATIONS, 0)] == expected


def test_plan_upgrade_skips_applied_versions() -> None:
    expected = [item.version for item in MIGRATIONS if item.version > 2]
    assert [item.version for item in plan_upgrade(MIGRATIONS, 2)] == expected


def test_current_version_of_empty_is_zero() -> None:
    assert current_version(set()) == 0


def test_current_version_is_the_highest_not_the_count() -> None:
    assert current_version({1, 2, 5}) == 5


def test_pending_excludes_applied() -> None:
    expected = [item.version for item in MIGRATIONS if item.version != 1]
    assert [item.version for item in pending_migrations(MIGRATIONS, {1})] == expected


def test_migrate_applies_in_order() -> None:
    async def run() -> MigrationResult:
        store = InMemoryMigrationStore()
        return await migrate(store, MIGRATIONS)

    result = asyncio.run(run())
    expected = tuple(item.version for item in MIGRATIONS)
    assert result.applied == expected
    assert result.before == 0
    assert result.after == expected[-1]


def test_migrate_is_idempotent() -> None:
    """Running twice must not re-apply, or DDL would repeat forever."""

    async def run() -> tuple[MigrationResult, MigrationResult]:
        store = InMemoryMigrationStore()
        first = await migrate(store, MIGRATIONS)
        second = await migrate(store, MIGRATIONS)
        return first, second

    first, second = asyncio.run(run())
    expected = tuple(item.version for item in MIGRATIONS)
    assert first.applied == expected
    assert second.applied == ()
    assert second.after == expected[-1]


def test_a_newer_database_is_refused_instead_of_downgraded() -> None:
    """Rollback safety: an older build must not touch a newer schema.

    A newer build may have written tables/columns this build has never seen, so
    "apply only the migrations I know" would leave a shape neither build
    understands. The correct move is to refuse and keep the file intact.
    """
    from one_memory.memory_server.migration_runner import DatabaseSchemaTooNewError

    class _Newer(InMemoryMigrationStore):
        async def applied_versions(self) -> set[int]:
            return {MIGRATIONS[-1].version, 99}

    async def run() -> None:
        await migrate(_Newer(), MIGRATIONS)

    with pytest.raises(DatabaseSchemaTooNewError) as caught:
        asyncio.run(run())
    assert caught.value.found == 99
    assert caught.value.known == MIGRATIONS[-1].version


def test_a_database_at_the_known_version_is_not_refused() -> None:
    """The guard must not reject an ordinary up-to-date database."""

    class _Current(InMemoryMigrationStore):
        async def applied_versions(self) -> set[int]:
            return {MIGRATIONS[-1].version}

    result = asyncio.run(migrate(_Current(), MIGRATIONS))

    assert result.before == MIGRATIONS[-1].version
    assert result.applied == ()


def test_a_recorded_migration_with_a_missing_table_is_refused(tmp_path) -> None:
    """A ledger row must not hide a missing table behind IF NOT EXISTS.

    SQLite can lose a table without losing the ledger (manual repair, a
    partially restored file, or an interrupted external operation). Every
    shipped migration uses `CREATE TABLE IF NOT EXISTS`, so replaying the
    version would silently succeed and leave the database inconsistent. The
    runner must detect the disagreement before applying anything else.
    """
    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))
    with main_database(path).write() as connection, connection:
        connection.execute("DROP TABLE scan_runs")

    with pytest.raises(MigrationSchemaMismatchError, match="scan_runs"):
        asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))


def test_a_recorded_queue_with_a_missing_reference_table_is_refused(tmp_path) -> None:
    """Migration 2 owns both queue tables, including the reconcile reference."""
    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))
    with main_database(path).write() as connection, connection:
        connection.execute("DROP TABLE memory_write_job_evidence")

    with pytest.raises(MigrationSchemaMismatchError, match="memory_write_job_evidence"):
        asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))


def test_the_recorded_table_audit_catches_a_missing_early_table(tmp_path) -> None:
    """The audit must not only notice the newest migration's tables."""
    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))
    with main_database(path).write() as connection, connection:
        connection.execute("DROP TABLE source_file_cursors")

    with pytest.raises(MigrationSchemaMismatchError, match="source_file_cursors"):
        asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))


def test_a_migration_that_skips_a_declared_table_is_not_recorded(tmp_path) -> None:
    """The ledger must not record a version whose declared table was skipped.

    Without the post-apply check the bad version is recorded, and the error
    only appears on the next startup as a mismatch. Failing before `record`
    keeps the migration unapplied and retryable.
    """
    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "one-memory.sqlite3"
    store = SqliteMigrationStore(path)

    class _SkipsDeclaredTable:
        version = 7
        name = "declared-table"
        tables = ("required_table",)

        async def apply(self, target_store) -> None:
            await target_store.execute(
                "CREATE TABLE IF NOT EXISTS unrelated_table (id INTEGER PRIMARY KEY)"
            )

    with pytest.raises(MigrationSchemaMismatchError, match="required_table"):
        asyncio.run(migrate(store, (_SkipsDeclaredTable(),)))

    assert asyncio.run(store.applied_versions()) == set()


def test_the_shipped_migrations_build_a_real_sqlite_schema(tmp_path) -> None:
    """D56: the migration runner must actually run against SQLite.

    The migration bodies were MySQL-only, so the runner could not execute at
    all under D56 and the "SQLite schema upgrade" requirement had no working
    implementation. This applies every shipped migration to a fresh SQLite file
    through `main_database`, then requires the second run to be a no-op.
    """
    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "one-memory.sqlite3"

    async def run_twice() -> tuple[int, int]:
        first = await migrate(SqliteMigrationStore(path), MIGRATIONS)
        second = await migrate(SqliteMigrationStore(path), MIGRATIONS)
        return len(first.applied), len(second.applied)

    first_count, second_count = asyncio.run(run_twice())

    assert first_count == len(MIGRATIONS)
    assert second_count == 0
    with main_database(path).read() as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    # A representative table from an early, a middle and the last migration.
    assert {"schema_migrations", "memory_write_jobs", "evidence", "scan_runs"} <= tables


def test_upgrading_an_existing_store_database_keeps_its_rows(tmp_path) -> None:
    """The real upgrade path: a store-created DB, then the migration runner.

    A production database is created by stores (which build their own tables)
    and only later opened by the migration runner. This pins that the runner
    accepts that shape, adds the tables it owns, and leaves the user's existing
    rows untouched.
    """
    from datetime import UTC, datetime

    from one_memory.imports.models import (
        EvidenceInput,
        EvidenceOrigin,
        OriginMetadata,
        WrittenBy,
    )
    from one_memory.imports.storage import EvidenceStore
    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(path)
    for index in range(5):
        store.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="conversation",
                message_id=f"m{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=f"row {index}",
                created_at=datetime.now(UTC),
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        )

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    expected = tuple(item.version for item in MIGRATIONS)
    assert result.after == expected[-1]
    assert result.applied == expected, (
        "the runner must actually apply every migration, not just report a version"
    )
    import sqlite3

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 5
        # Prove the migrations really ran: the ledger is fully recorded and the
        # tables only the migration runner creates exist. Asserting only the
        # row count would pass even if `migrate` were a no-op stub, because a
        # store-created database already holds the evidence rows.
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"governed_memories", "scan_runs", "model_profiles", "matters"} <= tables


def test_migration_schema_matches_the_store_built_schema(tmp_path) -> None:
    """An upgraded database and a fresh one must end up with the same columns.

    Stores build their tables from the shared `*_ddl` helpers at startup, and
    the migration runner now builds them too. This proves the two paths agree
    on real column names and types for the tables they share, so a database
    created by one is readable by code that expects the other.
    """
    from one_memory.database_access import main_database
    from one_memory.imports.storage import EvidenceStore
    from one_memory.memory_server.durable_queue import DurableWriteQueue
    from one_memory.memory_server.migration_runner import SqliteMigrationStore
    from one_memory.memory_tasks import MemoryTaskStore

    migrated = tmp_path / "migrated.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(migrated), MIGRATIONS))

    stored = tmp_path / "stored.sqlite3"
    EvidenceStore(stored)
    MemoryTaskStore(stored)
    DurableWriteQueue(stored)

    def columns(path) -> dict[str, list[tuple[str, str]]]:
        with main_database(path).read() as connection:
            names = [
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ]
            return {
                name: [
                    (str(row[1]), str(row[2]))
                    for row in connection.execute(f"PRAGMA table_info({name})")
                ]
                for name in names
            }

    migrated_columns = columns(migrated)
    for table in ("evidence", "memory_tasks", "memory_write_jobs"):
        assert migrated_columns[table] == columns(stored)[table]


def test_no_migration_emits_mysql_only_syntax_for_sqlite(tmp_path) -> None:
    """Every migration body must render engine-correct SQL for SQLite.

    Reverting `_dialect` to a hardcoded "mysql" leaves the failure at the first
    `ENGINE=InnoDB` / `AUTO_INCREMENT`, far from the hardcode. This records the
    exact statements a SQLite run is asked to execute and rejects the
    MySQL-only constructs, so the regression is caught where it starts.
    """
    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    class RecordingStore(SqliteMigrationStore):
        def __init__(self, path) -> None:
            super().__init__(path)
            self.statements: list[str] = []

        async def execute(self, sql: str) -> None:
            self.statements.append(sql)
            await super().execute(sql)

    store = RecordingStore(tmp_path / "one-memory.sqlite3")
    asyncio.run(migrate(store, MIGRATIONS))

    joined = "\n".join(store.statements).upper()
    for construct in ("ENGINE=INNODB", "AUTO_INCREMENT", "TINYINT", "VARCHAR(", "DATETIME("):
        assert construct not in joined, f"{construct} leaked into the SQLite migrations"


def test_failed_migration_is_not_recorded() -> None:
    """A failure must be retried next run, not skipped as if it succeeded.

    Asserted on the *ledger*, not merely on the exception. Recording a version
    before its DDL succeeds is the actual bug: the next start then believes the
    database reached a version it never did, and the missing table surfaces
    much later as a confusing runtime error. An earlier version of this test
    only asserted the exception propagated, and stayed green against a mutated
    runner that recorded first - a test guarding nothing.
    """

    class FailingStore(InMemoryMigrationStore):
        """Fails the second migration specifically, by version not by text.

        Matching on text is fragile: migration 1's SQL happens to contain "2"
        inside a column definition, so a text match fails the wrong step and
        the test then asserts about a migration that never ran.
        """

        async def execute(self, sql: str) -> None:
            if "memory_write_jobs" in sql:
                raise RuntimeError("ddl failed")
            await super().execute(sql)

    async def run() -> InMemoryMigrationStore:
        store = FailingStore()
        with pytest.raises(RuntimeError):
            await migrate(store, MIGRATIONS)
        return store

    store = asyncio.run(run())
    assert 1 in store.applied, "the successful migration should be recorded"
    assert 2 not in store.applied, "a failed migration must NOT be recorded"
    assert 3 not in store.applied


def test_after_a_failure_the_migration_is_retried() -> None:
    """The follow-up run picks up where it stopped, once the fault clears."""

    class FlakyStore(InMemoryMigrationStore):
        fail_next = True

        async def execute(self, sql: str) -> None:
            if "memory_write_jobs" in sql and self.fail_next:
                self.fail_next = False
                raise RuntimeError("transient")
            await super().execute(sql)

    async def run() -> MigrationResult:
        store = FlakyStore()
        with pytest.raises(RuntimeError):
            await migrate(store, MIGRATIONS)
        return await migrate(store, MIGRATIONS)

    result = asyncio.run(run())
    assert 2 in result.applied
    assert 3 in result.applied


def test_ledger_sql_creates_the_migrations_table() -> None:
    sql = ledger_sql()
    assert "schema_migrations" in sql
    assert "version INT PRIMARY KEY" in sql


def test_scanner_state_ddl_creates_all_four_tables() -> None:
    """A4-03 names four tables. Execute the rendering rather than reading it.

    A DDL that parses as text but fails on the engine is the realistic risk:
    these tables were absent from the migration set entirely, and the schema
    was marked done on the strength of the code path rather than the schema.
    """
    import sqlite3

    from one_memory.memory_server.migrations import scanner_state_ddl

    db = sqlite3.connect(":memory:")
    db.executescript(scanner_state_ddl("sqlite"))
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "source_files",
        "source_sessions",
        "session_ingest_cursors",
        "scan_runs",
    } <= tables


def test_scanner_state_sqlite_has_no_mysql_only_syntax() -> None:
    """The SQLite rendering must be executable by the engine in use today."""
    from one_memory.memory_server.migrations import scanner_state_ddl

    sqlite = scanner_state_ddl("sqlite")
    assert "AUTO_INCREMENT" not in sqlite
    assert "ENGINE=InnoDB" not in sqlite
    assert "JSON" not in sqlite.upper().replace("JSON_EXTRACT", "")


def test_scanner_state_both_dialects_agree_on_columns() -> None:
    """Same columns in both renderings; only types differ.

    `source_files.file_key` and `source_sessions.internal_session_id` are the
    two keys the metadata gate batches on, so a column present in MySQL only
    would leave the SQLite path — the one that runs today — unable to batch.
    """
    from one_memory.memory_server.migrations import scanner_state_ddl

    def columns(sql: str, table: str) -> set[str]:
        start = sql.index(f"CREATE TABLE IF NOT EXISTS {table} (")
        body = sql[start:]
        depth = 0
        for offset, char in enumerate(body):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    body = body[: offset + 1]
                    break
        names = set()
        for line in body.splitlines():
            token = line.strip().split()[0] if line.strip() else ""
            if token and not token.startswith(("UNIQUE", "PRIMARY", "KEY", "CONSTRAINT")):
                names.add(token.rstrip(","))
        return {name for name in names if name and name not in ("CREATE", "TABLE")}

    for table in (
        "source_files",
        "source_sessions",
        "session_ingest_cursors",
        "scan_runs",
    ):
        assert columns(scanner_state_ddl("mysql"), table) == columns(
            scanner_state_ddl("sqlite"), table
        ), table


def test_source_files_is_keyed_for_batch_lookup() -> None:
    """`file_key` must be unique so one query can resolve a whole page.

    Without it, joining a page of scanned metadata back to `source_files`
    costs one query per file, which is the N+1 the metadata gate forbids.
    """
    import sqlite3

    from one_memory.memory_server.migrations import scanner_state_ddl

    db = sqlite3.connect(":memory:")
    db.executescript(scanner_state_ddl("sqlite"))
    db.execute(
        "INSERT INTO source_files VALUES "
        "('f1','root:a.jsonl','r1','a.jsonl','dev:1',10,100,'unfingerprinted',NULL,0,NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO source_files VALUES "
            "('f2','root:a.jsonl','r1','b.jsonl','dev:2',10,100,'unfingerprinted',NULL,0,NULL)"
        )


def test_scan_runs_stores_the_discovery_cursor() -> None:
    """A resumable scan needs the cursor persisted, not only in memory.

    A crash mid-scan must resume from the last key; a run row without the
    cursor would restart enumeration from the top on a large tree.
    """
    import sqlite3

    from one_memory.memory_server.migrations import scanner_state_ddl

    db = sqlite3.connect(":memory:")
    db.executescript(scanner_state_ddl("sqlite"))
    db.execute(
        "INSERT INTO scan_runs VALUES ('s1','r1','running','{\"last_key\":\"a\"}',NULL,'2026',NULL)"
    )
    stored = db.execute("SELECT discovery_cursor FROM scan_runs WHERE scan_id='s1'").fetchone()
    assert stored[0] == '{"last_key":"a"}'


def test_scanner_state_is_registered_as_migration_35() -> None:
    from one_memory.memory_server.migrations import MIGRATIONS

    entry = [item for item in MIGRATIONS if item.name == "scanner_state"]
    assert len(entry) == 1
    assert entry[0].version == 35


def test_retrieval_authority_index_is_registered_as_migration_36() -> None:
    from one_memory.memory_server.migrations import MIGRATIONS

    entry = [item for item in MIGRATIONS if item.name == "retrieval_authority_index"]
    assert len(entry) == 1
    assert entry[0].version == 36


def test_change_outbox_ddl_has_one_definition_per_dialect() -> None:
    """The migrated table must come from the migration, not from a local copy.

    If the business module kept its own DDL, the migration would describe a
    schema the code does not create, and the drift would only surface on a
    fresh database.
    """
    from one_memory.change_outbox import _SCHEMA
    from one_memory.memory_server.migrations import change_outbox_ddl

    assert change_outbox_ddl("sqlite") == _SCHEMA
    assert "change_outbox" in _SCHEMA
    # The SQLite rendering must not carry MySQL-only syntax it cannot parse.
    assert "ENGINE=InnoDB" not in _SCHEMA


def test_change_outbox_ddl_mysql_and_sqlite_agree_on_columns() -> None:
    """Same columns in both dialects; only the types are spelled differently.

    A column added to one rendering and not the other is the drift this single
    definition exists to prevent.
    """
    from one_memory.memory_server.migrations import change_outbox_ddl

    def columns(sql: str) -> set[str]:
        body = sql[sql.index("(") + 1 : sql.rindex(")")]
        names = set()
        for line in body.splitlines():
            token = line.strip().split()[0] if line.strip() else ""
            if token and not token.startswith(("KEY", "PRIMARY", "UNIQUE")):
                names.add(token)
        return names

    mysql = change_outbox_ddl("mysql")
    sqlite = change_outbox_ddl("sqlite")
    assert columns(mysql) == columns(sqlite)
    assert "event_id" in columns(mysql)


@pytest.mark.parametrize(
    ("getter", "table"),
    [
        ("session_leases_ddl", "session_leases"),
        ("trusted_sessions_ddl", "trusted_sessions"),
    ],
)
def test_migrated_ddl_has_one_source(getter: str, table: str) -> None:
    """Each migrated module must use the migration's definition, not a copy.

    Modules that ran on one engine held a single `_SCHEMA` constant. Migrated
    ones hold both renderings and select at use time, so the check reads
    whichever the module actually uses — the point is still that the text comes
    from the migration and is not retyped inline.
    """
    import importlib

    migrations = importlib.import_module("one_memory.memory_server.migrations")
    render = getattr(migrations, getter)

    module_name = {
        "session_leases_ddl": "one_memory.session_lease",
        "trusted_sessions_ddl": "one_memory.session_registry",
    }[getter]
    module = importlib.import_module(module_name)

    if hasattr(module, "schema_for"):
        sqlite_text = module.schema_for(False)
        mysql_text = module.schema_for(True)
    else:
        sqlite_text = module._SCHEMA
        mysql_text = None

    assert sqlite_text == render("sqlite"), "module copies the DDL instead of using the migration"
    assert table in sqlite_text
    assert "ENGINE=InnoDB" not in sqlite_text
    # A migrated module must also be able to render MySQL, or it will send
    # SQLite DDL to MySQL when the engine switches.
    if mysql_text is not None:
        assert mysql_text == render("mysql")
        assert table in mysql_text


def test_import_jobs_ddl_is_split_before_execution() -> None:
    """MySQL takes one DDL per call, so a script must be split.

    Sending all five CREATEs in one execute() would either fail or silently
    apply only the first, leaving the scanner's cursor table missing while the
    migration is recorded as applied.
    """
    from one_memory.memory_server.migrations import _split_statements, import_jobs_ddl

    statements = _split_statements(import_jobs_ddl("mysql"))
    assert len(statements) == 5
    assert all(item.upper().startswith("CREATE TABLE") for item in statements)
    # PRAGMA is SQLite connection state, not schema; it must not be sent.
    assert not any("PRAGMA" in item.upper() for item in statements)


def test_import_jobs_keeps_the_cursor_composite_key() -> None:
    """The batch cursor lookup depends on (user_id, source_id, file_key).

    Replacing it with a surrogate id would break `get_many` and reintroduce
    the per-file lookup the metadata gate exists to avoid.
    """
    from one_memory.memory_server.migrations import import_jobs_ddl

    for dialect in ("mysql", "sqlite"):
        sql = import_jobs_ddl(dialect)
        assert "PRIMARY KEY (user_id, source_id, file_key)" in sql


def test_sqlite_rendering_has_no_mysql_only_syntax() -> None:
    """The SQLite rendering must be executable by SQLite.

    `AUTO_INCREMENT` and `ENGINE=InnoDB` would make the whole script fail on
    the engine this module still runs on today.
    """
    from one_memory.memory_server.migrations import import_jobs_ddl

    sqlite = import_jobs_ddl("sqlite")
    assert "AUTO_INCREMENT" not in sqlite
    assert "ENGINE=InnoDB" not in sqlite


def test_evidence_sqlite_rendering_creates_every_table() -> None:
    """Execute it for real: a rendering that cannot run is the real risk.

    DDL translation fails quietly if a clause survives that the target engine
    cannot parse, so this builds the schema in an in-memory database and
    checks all five tables and the compound indices exist.
    """
    import sqlite3

    from one_memory.memory_server.migrations import evidence_ddl

    db = sqlite3.connect(":memory:")
    db.executescript(evidence_ddl("sqlite"))
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "evidence",
        "evidence_origins",
        "evidence_versions",
        "evidence_repairs",
        "evidence_pending_memory_dispatch",
    } <= tables

    indices = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    }
    assert "evidence_conversation_index" in indices
    assert "evidence_workspace_index" in indices


def test_evidence_has_an_index_serving_the_recent_list_order() -> None:
    """`list_recent_conversations` is the production hot path.

    It orders by `imported_at DESC, evidence_id DESC`, and with no index
    starting at `imported_at` SQLite sorts the whole table — measured at
    2466ms median over 430k rows, and 11218ms on the debug Agent's real
    migration — while holding one pooled read connection for the duration.
    That single query is what pushed `reader_wait_ms_max` to 18138.5ms in
    production.

    The guard is the query plan, not the index's existence: an index on the
    wrong columns, or in the wrong direction, would still be present and
    would still leave the sort.
    """
    import sqlite3

    from one_memory.memory_server.migrations import evidence_ddl

    db = sqlite3.connect(":memory:")
    db.executescript(evidence_ddl("sqlite"))
    query = (
        "SELECT evidence_id FROM evidence WHERE user_id = ? "
        "ORDER BY imported_at DESC, evidence_id DESC LIMIT 100 OFFSET 0"
    )
    plan = db.execute("EXPLAIN QUERY PLAN " + query, ("local",)).fetchall()
    rendered = " ".join(row[-1] for row in plan)
    assert "TEMP B-TREE" not in rendered, rendered


def test_the_recent_list_index_covers_both_ordering_columns() -> None:
    """The plan guard alone cannot see a missing trailing column.

    Dropping `evidence_id` from the index DDL still produced a covering-index
    plan and left every caller green, while ties inside one `imported_at`
    batch then came back in storage order — a *wrong result*, not a slow one.
    So the index's column list is asserted outright.
    """
    import sqlite3

    from one_memory.memory_server.migrations import evidence_ddl

    db = sqlite3.connect(":memory:")
    db.executescript(evidence_ddl("sqlite"))
    rows = db.execute(
        "SELECT name FROM pragma_index_info('evidence_imported_index') ORDER BY seqno"
    ).fetchall()
    columns = [row[0] for row in rows]

    # The index is scoped by `user_id` first, then carries both ordering
    # columns in order. Asserting the ordering columns outright is the point:
    # without `evidence_id`, ties inside one `imported_at` batch come back in
    # storage order and the guard above cannot see it.
    assert columns[-2:] == ["imported_at", "evidence_id"], columns


def test_retrieval_job_index_puts_source_before_event_order() -> None:
    """The authority filter must be in the key, not applied after a scan.

    C4's 10k/430k benchmark showed the old `(generation_id,state,event_seq)`
    index satisfying only generation and state, so SQLite materialized every
    ready document before filtering `source_id`. The positive control added
    `(generation_id,state,source_id,event_seq)` and changed the plan and p95
    materially. This guard asserts both the exact column list and the plan so
    a later index edit cannot silently reintroduce the full-generation scan.
    """
    import sqlite3

    from one_memory.memory_server.migrations import retrieval_index_ddl

    db = sqlite3.connect(":memory:")
    db.executescript(retrieval_index_ddl("sqlite"))
    columns = [
        row[0]
        for row in db.execute("SELECT name FROM pragma_index_info('retrieval_jobs') ORDER BY seqno")
    ]
    assert columns == ["generation_id", "state", "source_id", "event_seq"], columns

    query = (
        "SELECT d.* FROM retrieval_documents d "
        "WHERE d.generation_id=? AND d.state IN ('ready','excluded') "
        "AND d.source_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY d.event_seq,d.evidence_id LIMIT 256"
    )
    plan = db.execute(
        "EXPLAIN QUERY PLAN " + query,
        ("generation", '["codex"]'),
    ).fetchall()
    rendered = " ".join(row[-1] for row in plan)
    assert "retrieval_jobs" in rendered, rendered
    assert "source_id=?" in rendered or "source_id IN" in rendered, rendered


def test_unknown_jobs_index_matches_the_reconcile_order() -> None:
    """The unknown reconcile must not sort the whole unknown backlog.

    `unknown_jobs()` filters by state and orders by `(updated_at, job_id)`.
    The claim index shares the leading state column but then orders by
    `(priority, created_at)`, so a plan can use it and still need a temp sort.
    This asserts both the exact key columns and the plan, because either one
    alone can look correct while the other regresses.
    """
    import sqlite3

    from one_memory.memory_server.migrations import (
        memory_write_jobs_sqlite_schema,
        unknown_jobs_index_ddl,
    )

    db = sqlite3.connect(":memory:")
    try:
        for statement in memory_write_jobs_sqlite_schema():
            db.execute(statement)
        db.execute(unknown_jobs_index_ddl("sqlite"))
        columns = [
            row[0]
            for row in db.execute(
                "SELECT name FROM pragma_index_info('idx_write_jobs_unknown') ORDER BY seqno"
            )
        ]
        assert columns == ["state", "updated_at", "job_id"], columns
        query = (
            "SELECT job_id, idempotency_key, created_at, updated_at, last_error "
            "FROM memory_write_jobs WHERE state=? "
            "ORDER BY updated_at, job_id LIMIT ?"
        )
        plan = db.execute("EXPLAIN QUERY PLAN " + query, ("unknown", 128)).fetchall()
    finally:
        db.close()

    rendered = " ".join(str(row[-1]) for row in plan)
    assert "idx_write_jobs_unknown" in rendered, rendered
    assert "TEMP B-TREE" not in rendered, rendered


def test_memory_task_evidence_index_matches_the_lookup_order() -> None:
    """The evidence-to-latest-task lookup must not scan and sort the table."""
    import sqlite3

    from one_memory.memory_server.migrations import (
        memory_task_evidence_index_ddl,
        memory_tasks_ddl,
    )

    db = sqlite3.connect(":memory:")
    try:
        db.executescript(memory_tasks_ddl("sqlite"))
        db.execute(memory_task_evidence_index_ddl("sqlite"))
        columns = [
            row[0]
            for row in db.execute(
                "SELECT name FROM pragma_index_info('memory_task_evidence') ORDER BY seqno"
            )
        ]
        query = (
            "SELECT * FROM memory_tasks WHERE input_ref=? "
            "ORDER BY created_at DESC, task_id DESC LIMIT 1"
        )
        plan = db.execute("EXPLAIN QUERY PLAN " + query, ("e1",)).fetchall()
    finally:
        db.close()

    assert columns == ["input_ref", "created_at", "task_id"], columns
    rendered = " ".join(str(row[-1]) for row in plan)
    assert "memory_task_evidence" in rendered, rendered
    assert "TEMP B-TREE" not in rendered, rendered


def test_memory_task_evidence_index_is_registered_as_migration_38() -> None:
    from one_memory.memory_server.migrations import MIGRATIONS

    entry = [item for item in MIGRATIONS if item.name == "memory_task_evidence_index"]
    assert len(entry) == 1
    assert entry[0].version == 38


def test_migration_38_upgrades_a_v37_database_and_records_the_index(tmp_path) -> None:
    """The index is not only for fresh databases; v37 must upgrade in place."""
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    through_37 = tuple(item for item in MIGRATIONS if item.version <= 37)
    asyncio.run(migrate(SqliteMigrationStore(path), through_37))

    # A database migrated before this index existed has the table but not the
    # index. Remove it to model that exact upgrade input.
    with main_database(path).write() as connection, connection:
        connection.execute("DROP INDEX memory_task_evidence")

    with main_database(path).read() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='memory_task_evidence'"
            ).fetchone()
            is None
        )

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    # A late index-only migration may append after 41; the in-place
    # upgrade must still include every version newer than 37.
    assert result.applied == tuple(range(38, MIGRATIONS[-1].version + 1))
    with main_database(path).read() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='memory_task_evidence'"
            ).fetchone()
            is not None
        )
    assert asyncio.run(SqliteMigrationStore(path).applied_versions()).issuperset({38, 39})


def test_migration_39_repairs_a_v38_database_recorded_without_preview_table(tmp_path) -> None:
    """A real v28 database records a version whose preview table was omitted.

    Restoring the table only in the v28 DDL would leave that database with the
    ledger already marked healthy while `retain_preview()` still fails. The
    forward v39 migration must both recreate the table and let the startup
    ledger audit through, while an ordinary missing table still fails closed.
    """
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    through_38 = tuple(item for item in MIGRATIONS if item.version <= 38)
    asyncio.run(migrate(SqliteMigrationStore(path), through_38))
    with main_database(path).write() as connection, connection:
        connection.execute("DROP TABLE model_content_previews")

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    assert result.applied == tuple(range(39, MIGRATIONS[-1].version + 1))


def test_migration_41_adds_tail_handoff_lease_token_to_a_v40_database(tmp_path) -> None:
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    through_40 = tuple(item for item in MIGRATIONS if item.version <= 40)
    asyncio.run(migrate(SqliteMigrationStore(path), through_40))
    with main_database(path).read() as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(tail_handoff)").fetchall()
        }
    assert "lease_token" not in columns

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))
    assert result.applied == tuple(range(41, MIGRATIONS[-1].version + 1))
    with main_database(path).read() as connection:
        upgraded = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(tail_handoff)").fetchall()
        }
    assert "lease_token" in upgraded


def test_migration_46_cleans_existing_codex_internal_context(tmp_path) -> None:
    """Existing internal continuation envelopes leave raw and governed search."""
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    through_45 = tuple(item for item in MIGRATIONS if item.version <= 45)
    asyncio.run(migrate(SqliteMigrationStore(path), through_45))
    internal = '<codex_internal_context source="goal">Continue working</codex_internal_context>'
    with main_database(path).write() as connection, connection:
        connection.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,revision,"
            "imported_at,updated_at,ingest_seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "ev-internal",
                "idem-internal",
                "local",
                "codex",
                "session",
                "raw_conversation",
                "user",
                "person",
                internal,
                "old-evidence-hash",
                1,
                "2026-10-05T00:00:00+00:00",
                "2026-10-05T00:00:00+00:00",
                1,
            ),
        )
        connection.execute(
            "INSERT INTO evidence_versions (evidence_id,revision,verbatim,content_hash,role,"
            "written_by,changed_at) VALUES (?,?,?,?,?,?,?)",
            (
                "ev-internal",
                1,
                internal,
                "old-evidence-hash",
                "user",
                "person",
                "2026-10-05T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO governed_memories (memory_id,user_id,kind,status,content,scopes_json,"
            "revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "mem-internal",
                "local",
                "chat",
                "confirmed",
                f"A derived claim containing {internal}",
                '["all-apps"]',
                1,
                "2026-10-05T00:00:00+00:00",
                "2026-10-05T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO governed_memory_evidence (memory_id,evidence_id,active) VALUES (?,?,1)",
            ("mem-internal", "ev-internal"),
        )

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    assert 46 in result.applied
    with main_database(path).read() as connection:
        evidence = connection.execute(
            "SELECT role,written_by,verbatim,content_hash FROM evidence WHERE evidence_id=?",
            ("ev-internal",),
        ).fetchone()
        assert tuple(evidence) == (
            "system",
            "agent",
            "[已清理的 Codex 内部续跑提示]",
            "aa5f8a0411d9c5a64f6b4542ab6120460698c31db9b6b5c025d0c6b684988e99",
        )
        assert (
            connection.execute(
                "SELECT active FROM governed_memory_evidence WHERE memory_id=? AND evidence_id=?",
                ("mem-internal", "ev-internal"),
            ).fetchone()[0]
            == 0
        )
        memory = connection.execute(
            "SELECT status,content,revision,deleted_at FROM governed_memories WHERE memory_id=?",
            ("mem-internal",),
        ).fetchone()
        assert tuple(memory[:3]) == (
            "deleted",
            "[已清理：该记忆包含 Codex 内部续跑提示]",
            2,
        )
        assert memory[3] is not None
        assert connection.execute(
            "SELECT action,status,content FROM governed_memory_versions "
            "WHERE memory_id=? AND revision=2",
            ("mem-internal",),
        ).fetchone() == (
            "cleanup_internal_context",
            "deleted",
            "[已清理：该记忆包含 Codex 内部续跑提示]",
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM evidence "
                "WHERE lower(verbatim) LIKE '%<codex_internal_context%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM evidence_versions "
                "WHERE lower(verbatim) LIKE '%<codex_internal_context%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM governed_memories "
                "WHERE lower(content) LIKE '%<codex_internal_context%'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM governed_memory_versions "
                "WHERE lower(content) LIKE '%<codex_internal_context%'"
            ).fetchone()[0]
            == 0
        )


def test_migration_47_cleans_heartbeat_from_every_codex_role(tmp_path) -> None:
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    through_46 = tuple(item for item in MIGRATIONS if item.version <= 46)
    asyncio.run(migrate(SqliteMigrationStore(path), through_46))
    with main_database(path).write() as connection, connection:
        for index, role in enumerate(("user", "assistant", "tool"), start=1):
            evidence_id = f"heartbeat-{index}"
            text = (
                "<heartbeat>internal</heartbeat>"
                if role != "tool"
                else '[automation] {"message":"&lt;heartbeat&gt;internal&lt;/heartbeat&gt;"}'
            )
            connection.execute(
                "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
                "conversation_id,content_kind,role,written_by,verbatim,content_hash,revision,"
                "imported_at,updated_at,ingest_seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    f"idem-{index}",
                    "local",
                    "codex",
                    "session",
                    "raw_conversation",
                    role,
                    "person" if role == "user" else role,
                    text,
                    f"hash-{index}",
                    1,
                    "2026-10-05T00:00:00+00:00",
                    "2026-10-05T00:00:00+00:00",
                    index,
                ),
            )
        connection.execute(
            "INSERT INTO governed_memories (memory_id,user_id,kind,status,content,scopes_json,"
            "revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "heartbeat-memory",
                "local",
                "chat",
                "confirmed",
                "summary <heartbeat>internal</heartbeat>",
                '["all-apps"]',
                1,
                "2026-10-05T00:00:00+00:00",
                "2026-10-05T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO governed_memory_evidence (memory_id,evidence_id,active) VALUES (?,?,1)",
            ("heartbeat-memory", "heartbeat-1"),
        )

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    assert 47 in result.applied
    with main_database(path).read() as connection:
        rows = connection.execute(
            "SELECT role,written_by,verbatim FROM evidence ORDER BY evidence_id"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("system", "agent", "[已清理的 Codex 内部提示]"),
        ("system", "agent", "[已清理的 Codex 内部提示]"),
        ("system", "agent", "[已清理的 Codex 内部提示]"),
    ]
    with main_database(path).read() as connection:
        memory = connection.execute(
            "SELECT status,content FROM governed_memories WHERE memory_id='heartbeat-memory'"
        ).fetchone()
        active = connection.execute(
            "SELECT active FROM governed_memory_evidence "
            "WHERE memory_id='heartbeat-memory' AND evidence_id='heartbeat-1'"
        ).fetchone()[0]
    assert tuple(memory) == ("deleted", "[已清理：该记忆包含 Codex 内部提示]")
    assert active == 0
    with main_database(path).read() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_content_previews'"
            ).fetchone()
            is not None
        )


def test_a_recorded_table_audit_still_refuses_a_table_without_a_repair_migration(tmp_path) -> None:
    """The v39 bridge must not turn every missing table into a warning."""
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import (
        MigrationSchemaMismatchError,
        SqliteMigrationStore,
        migrate,
    )
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))
    with main_database(path).write() as connection, connection:
        connection.execute("DROP TABLE scan_runs")

    with pytest.raises(MigrationSchemaMismatchError, match="scan_runs"):
        asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))


def test_evidence_declares_the_columns_that_used_to_be_altered() -> None:
    """`source_ordinal` and `content_kind` were added by ALTER TABLE per open.

    They are now declared outright, so opening a database must not depend on
    a patch step that grows in cost with the corpus.
    """
    from one_memory.memory_server.migrations import evidence_ddl

    sqlite = evidence_ddl("sqlite")
    assert "source_ordinal" in sqlite
    assert "content_kind" in sqlite
    assert "ALTER TABLE" not in sqlite


def test_memory_tasks_keeps_the_extraction_pair_check() -> None:
    """The CHECK must survive the move, and must actually fire.

    Dropping it would let a row exist with `content` but no `layer` (or the
    reverse) — not a partial write to tolerate but a contradiction, which
    would otherwise surface much later at read time. Asserted by inserting a
    violating row and requiring the database to reject it.
    """
    import sqlite3

    import pytest

    from one_memory.memory_server.migrations import memory_tasks_ddl

    assert "CHECK" in memory_tasks_ddl("sqlite").upper()

    db = sqlite3.connect(":memory:")
    db.executescript(memory_tasks_ddl("sqlite"))
    db.execute(
        "INSERT INTO memory_tasks VALUES "
        "('t','r','u','k','i','pending','p',1,'{}',NULL,0,NULL,'2026','2026',NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO memory_task_extractions VALUES ('t','content',NULL,'2026')")


def test_processing_requests_keeps_its_state_check() -> None:
    """The state CHECK must actually reject an unknown state.

    `state` is an enum in practice; without the constraint a writer could store
    a value no reader handles, and the failure would surface far from the cause.
    """
    import sqlite3

    import pytest

    from one_memory.memory_server.migrations import processing_requests_ddl

    db = sqlite3.connect(":memory:")
    db.executescript(processing_requests_ddl("sqlite"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO processing_requests VALUES "
            "('r','u',1,0,'p',1,'NOT_A_STATE',NULL,'2026','2026')"
        )


def test_import_job_snapshots_rejects_a_malformed_manifest_hash() -> None:
    """A sha256 that is not 64 chars must be refused at write time.

    Storing it and noticing later would make every subsequent comparison
    meaningless; the CHECK is what keeps the stored value trustworthy.
    """
    import sqlite3

    import pytest

    from one_memory.memory_server.migrations import import_job_snapshots_ddl

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE import_jobs (job_id TEXT PRIMARY KEY)")
    db.execute(import_job_snapshots_ddl("sqlite"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO import_job_snapshots VALUES ('j','s','tooshort')")


def test_only_one_model_profile_can_be_active() -> None:
    """At most one active profile, enforced by the database.

    Two active profiles would make "the active profile" whichever row a query
    happened to return first. Verified by trying to activate a second row and
    requiring rejection.

    Note the engine difference recorded in the migration: SQLite uses a partial
    unique index, MySQL uses UNIQUE over a nullable column (NULLs are distinct
    there). Both must preserve this invariant.
    """
    import sqlite3

    import pytest

    from one_memory.memory_server.migrations import _split_statements, model_profiles_ddl

    db = sqlite3.connect(":memory:")
    for statement in _split_statements(model_profiles_ddl("sqlite")):
        db.execute(statement)

    def insert(profile_id: str, active: int) -> None:
        db.execute(
            "INSERT INTO model_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                profile_id,
                "n",
                "openai_compatible",
                None,
                "m",
                None,
                None,
                None,
                None,
                None,
                60,
                1,
                None,
                1,
                0,
                0,
                0,
                0,
                1,
                active,
                "2026",
                "2026",
            ),
        )

    insert("p1", 1)
    with pytest.raises(sqlite3.IntegrityError):
        insert("p2", 1)


def test_call_event_ddl_matches_the_store_that_compares_it() -> None:
    """The two copies of the call_events DDL must stay byte-identical.

    `call_event_store` compares `sqlite_master.sql` against its own strings and
    raises `CallEventStoreUnavailable` on any difference, so the DDL text there
    is part of an integrity check. Migration 29 holds the canonical copy for new
    databases. If the two drift, existing databases start looking tampered with
    — so the drift is pinned here rather than left to be discovered at runtime.
    """
    from one_memory.call_event_store import _SCHEMA
    from one_memory.memory_server.migrations import _split_statements, call_events_ddl

    statements = _split_statements(call_events_ddl("sqlite"))
    assert statements == [
        _SCHEMA["call_events"],
        _SCHEMA["call_events_time"],
        _SCHEMA["call_events_host_time"],
    ]


#: InnoDB caps an index key at 3072 bytes; `utf8mb4` spends 4 bytes per char.
INNODB_KEY_BYTES = 3072
UTF8MB4_BYTES_PER_CHAR = 4


def _indexed_columns(sql: str, table: str) -> list[list[str]]:
    """The column list of each KEY/UNIQUE KEY in `table`, with prefix lengths."""
    start = sql.index(f"CREATE TABLE IF NOT EXISTS {table} (")
    body = sql[start:]
    depth = 0
    for offset, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                body = body[: offset + 1]
                break
    # Join wrapped lines first: an index definition may span several lines.
    flat = " ".join(body.split())
    keys = []
    for token in re.split(r"(?=UNIQUE KEY |KEY )", flat):
        token = token.strip()
        upper = token.upper()
        if not (upper.startswith("UNIQUE KEY ") or upper.startswith("KEY ")):
            continue
        # FOREIGN KEY (...) REFERENCES ... also matches "KEY ("; it is a
        # constraint, not an index whose length InnoDB caps the same way.
        if upper.startswith("FOREIGN KEY"):
            continue
        open_at = token.index("(")
        # Take only the first balanced parenthesised group: an index token may
        # be followed by `CONSTRAINT ... FOREIGN KEY (...) REFERENCES ...`.
        depth = 0
        close_at = -1
        for offset, char in enumerate(token[open_at:], start=open_at):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close_at = offset
                    break
        if close_at == -1:
            continue
        inner = token[open_at + 1 : close_at]
        keys.append([part.strip() for part in inner.split(",") if part.strip()])
    return keys


def _declared_widths(sql: str, table: str) -> dict[str, int]:
    start = sql.index(f"CREATE TABLE IF NOT EXISTS {table} (")
    body = sql[start:]
    widths = {}
    for line in body.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.startswith(
            ("UNIQUE", "KEY", "PRIMARY", "CONSTRAINT", "CREATE")
        ):
            continue
        parts = stripped.split()
        if len(parts) < 2 or "(" not in parts[1]:
            continue
        name = parts[0]
        try:
            widths[name] = int(parts[1][parts[1].index("(") + 1 : parts[1].index(")")])
        except ValueError:
            continue
    return widths


@pytest.mark.parametrize("dialect", ["mysql"])
def test_scanner_state_indexes_fit_innodb_key_limit(dialect: str) -> None:
    """Every index must fit 3072 bytes, or MySQL rejects the table outright.

    Found by applying these migrations to a real MySQL 8.4.4: a plain UNIQUE on
    `relative_path VARCHAR(1024)` is 4096 bytes and fails with error 1071, as
    does a 700-char prefix once `root_id` (512 bytes) is added. The limit is
    arithmetic, so it is asserted here rather than left to be rediscovered.
    """
    from one_memory.memory_server.migrations import scanner_state_ddl

    sql = scanner_state_ddl(dialect)
    for table in ("source_files", "source_sessions"):
        widths = _declared_widths(sql, table)
        for key in _indexed_columns(sql, table):
            total = 0
            for column in key:
                if "(" in column:
                    name, prefix = column.split("(", 1)
                    total += int(prefix.rstrip(")")) * UTF8MB4_BYTES_PER_CHAR
                else:
                    total += widths.get(column, 0) * UTF8MB4_BYTES_PER_CHAR
            assert total <= INNODB_KEY_BYTES, f"{table} index {key} = {total} bytes"


def _mysql_tables(sql: str) -> list[str]:
    return [
        line.split("(")[0].strip().split()[-1]
        for line in sql.splitlines()
        if line.strip().upper().startswith("CREATE TABLE IF NOT EXISTS")
    ]


def _all_mysql_ddl() -> dict[str, str]:
    import importlib

    migrations = importlib.import_module("one_memory.memory_server.migrations")
    out = {}
    for name in dir(migrations):
        if not name.endswith("_ddl"):
            continue
        render = getattr(migrations, name)
        try:
            out[name] = render("mysql")
        except TypeError:
            continue
    return out


def test_every_mysql_index_fits_the_innodb_key_limit() -> None:
    """The gate that would have caught all six pre-existing failures.

    Applying the migrations to a real MySQL 8.4.4 failed on `trusted_sessions`,
    `evidence`, and `evidence_pending_memory_dispatch` with error 1071, which
    also meant `evidence` never existed and cascaded into two more failures.
    None of it was visible on SQLite, which has no such limit.

    This asserts the arithmetic for every MySQL renderer, so widening a column
    now fails here instead of on a user's first launch.
    """
    offenders: list[str] = []
    for renderer, sql in _all_mysql_ddl().items():
        for table in _mysql_tables(sql):
            widths = _declared_widths(sql, table)
            for key in _indexed_columns(sql, table):
                total = 0
                for column in key:
                    if "(" in column:
                        name, prefix = column.split("(", 1)
                        total += int(prefix.rstrip(")")) * UTF8MB4_BYTES_PER_CHAR
                    else:
                        total += widths.get(column, 0) * UTF8MB4_BYTES_PER_CHAR
                if total > INNODB_KEY_BYTES:
                    offenders.append(f"{renderer}.{table} {key} = {total}B")
    assert not offenders, "indexes over the InnoDB 3072-byte limit:\n" + "\n".join(offenders)


def test_no_ddl_uses_a_reserved_word_as_a_bare_column() -> None:
    """`stored` is reserved in MySQL 8.x (window functions) and was used bare.

    That is why `import_jobs` failed with error 1064 on 8.4.4. A bare reserved
    word is a syntax error, so it must be backticked — asserted here because
    the mistake is invisible on SQLite, where `stored` is an ordinary word.
    """
    import re

    reserved = {"stored", "rank", "groups", "rows", "range", "system", "window"}
    offenders: list[str] = []
    for renderer, sql in _all_mysql_ddl().items():
        for line in sql.splitlines():
            stripped = line.strip()
            if stripped.startswith(("CREATE", "KEY", "UNIQUE", "PRIMARY", "CONSTRAINT")):
                continue
            first = re.split(r"[\s(,]+", stripped)[0].lower()
            if first in reserved:
                offenders.append(f"{renderer}: {stripped[:60]}")
    assert not offenders, "reserved words used as bare columns:\n" + "\n".join(offenders)


def test_memory_write_jobs_has_a_sqlite_rendering_for_the_engine_in_force(tmp_path) -> None:
    """D56: the durable queue must be creatable on SQLite, the only engine.

    Migration 2 still declares the MySQL shape; what is asserted here is that
    the same table exists in the engine actually used, with the columns the
    claim/reclaim SQL depends on.
    """
    from one_memory.database_access import main_database
    from one_memory.memory_server.migrations import memory_write_jobs_sqlite_schema

    database = main_database(tmp_path / "one-memory.sqlite3")
    with database.write() as connection, connection:
        for statement in memory_write_jobs_sqlite_schema():
            connection.execute(statement)

    with database.read() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_write_jobs)")}

    assert {
        "job_id",
        "session_key",
        "batch_id",
        "idempotency_key",
        "payload_json",
        "state",
        "priority",
        "attempts",
        "max_attempts",
        "lease_owner",
        "lease_expires_at",
        "hy_invoked",
        "last_error",
        "created_at",
        "updated_at",
    } <= columns
    database.close()


def test_an_interrupted_upgrade_is_resumed_from_the_durable_ledger(tmp_path) -> None:
    """Crash between DDL and ledger write must be retried, not half-applied.

    The ledger is the authority on what landed. If a version were recorded
    before its SQL succeeded, the next start would believe the database
    reached a version it never did; here the interruption happens exactly
    there, and the follow-up run still completes the version.
    """
    from one_memory.memory_server.migration_runner import (
        SqliteMigrationStore,
        migrate,
    )

    path = tmp_path / "one-memory.sqlite3"
    store = SqliteMigrationStore(path)

    class _Target:
        version = 7
        name = "interrupted"

        async def apply(self, target_store) -> None:
            await target_store.execute(
                "CREATE TABLE IF NOT EXISTS interrupted_table (id INTEGER PRIMARY KEY)"
            )
            raise RuntimeError("process died before recording")

    async def run() -> None:
        with pytest.raises(RuntimeError):
            await migrate(store, (_Target(),))
        return await migrate(
            store,
            (
                type(
                    "Recovered",
                    (),
                    {
                        "version": 7,
                        "name": "interrupted",
                        "apply": lambda self, target_store: target_store.execute(
                            "CREATE TABLE IF NOT EXISTS interrupted_table (id INTEGER PRIMARY KEY)"
                        ),
                    },
                )(),
            ),
        )

    result = asyncio.run(run())

    assert 7 in result.applied
    assert asyncio.run(store.applied_versions()) == {7}


def test_interrupted_migration_36_replays_drop_before_create(tmp_path) -> None:
    """The real index rebuild must survive a crash between its two statements.

    Migration 36 drops the old index and then creates the authority index.
    If the process dies after the drop but before the create-or-ledger write,
    the migration is not recorded. A restart must replay it and leave the
    database with the new column order while preserving every document row.
    """
    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    database = main_database(path)
    with database.write() as connection, connection:
        connection.execute(
            "CREATE TABLE retrieval_documents ("
            "generation_id TEXT NOT NULL, evidence_id TEXT NOT NULL, "
            "source_id TEXT NOT NULL, event_seq INTEGER NOT NULL, "
            "build_id TEXT NOT NULL, state TEXT NOT NULL, "
            "PRIMARY KEY(generation_id,evidence_id))"
        )
        connection.execute(
            "CREATE INDEX retrieval_jobs ON retrieval_documents(generation_id,state,event_seq)"
        )
        connection.execute(
            "INSERT INTO retrieval_documents VALUES ('g','e1','codex',1,'b','ready')"
        )
    database.close()

    migration = next(item for item in MIGRATIONS if item.version == 36)

    class InterruptAfterDrop(SqliteMigrationStore):
        async def execute(self, sql: str) -> None:
            await super().execute(sql)
            if sql.lstrip().upper().startswith("DROP INDEX"):
                raise RuntimeError("process died after dropping the old index")

    async def run() -> None:
        with pytest.raises(RuntimeError, match="after dropping"):
            await migrate(InterruptAfterDrop(path), (migration,))
        # The failed migration is unrecorded and is retried from the same path.
        reopened = SqliteMigrationStore(path)
        result = await migrate(reopened, (migration,))
        assert result.applied == (36,)
        assert await reopened.applied_versions() == {36}

    asyncio.run(run())

    with main_database(path).read() as connection:
        columns = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM pragma_index_info('retrieval_jobs') ORDER BY seqno"
            )
        ]
        rows = connection.execute("SELECT * FROM retrieval_documents").fetchall()
    assert columns == ["generation_id", "state", "source_id", "event_seq"]
    assert rows == [("g", "e1", "codex", 1, "b", "ready")]


def test_interrupted_migration_36_recovers_when_create_itself_failed(tmp_path) -> None:
    """A crash after DROP but before the unrecorded CREATE must be recoverable.

    Migration 36 is an index rebuild: it drops the old `retrieval_jobs` index
    and creates the authority-shaped replacement. The runner records the
    migration only after both statements succeed. A crash after the DROP but
    before the CREATE leaves no index and no ledger row; the next startup must
    replay the migration and restore the index, not assume the database is
    current because the table exists.
    """
    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    with main_database(path).write() as connection, connection:
        connection.execute(
            "CREATE TABLE retrieval_documents ("
            "generation_id TEXT NOT NULL, evidence_id TEXT NOT NULL, "
            "source_id TEXT NOT NULL, event_seq INTEGER NOT NULL, "
            "build_id TEXT NOT NULL, state TEXT NOT NULL, "
            "PRIMARY KEY(generation_id,evidence_id))"
        )
        connection.execute(
            "CREATE INDEX retrieval_jobs ON retrieval_documents(generation_id,state,event_seq)"
        )
    migration = next(item for item in MIGRATIONS if item.version == 36)

    class InterruptAfterDropBeforeCreate(SqliteMigrationStore):
        async def execute(self, sql: str) -> None:
            if sql.lstrip().upper().startswith("CREATE INDEX"):
                raise RuntimeError("process died before recreating the index")
            await super().execute(sql)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="before recreating"):
            await migrate(InterruptAfterDropBeforeCreate(path), (migration,))
        result = await migrate(SqliteMigrationStore(path), (migration,))
        assert result.applied == (36,)

    asyncio.run(run())

    with main_database(path).read() as connection:
        index_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='retrieval_jobs'"
        ).fetchall()
        columns = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM pragma_index_info('retrieval_jobs') ORDER BY seqno"
            )
        ]
    assert index_rows == [("retrieval_jobs",)]
    assert columns == ["generation_id", "state", "source_id", "event_seq"]


def test_migration_34_adds_ingest_seq_to_a_pre_migration_8_database(tmp_path) -> None:
    """An old store-created evidence table may not have `ingest_seq` yet.

    Migration 8 uses `CREATE TABLE IF NOT EXISTS`, so an existing pre-8 table
    is left untouched. Migration 34 then backfills `ingest_seq = rowid`; on a
    table that never got the column, that statement fails and the upgrade
    cannot resume. The migration must add the column when it is absent and
    leave an already-upgraded table alone.
    """
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    with main_database(path).write() as connection, connection:
        connection.execute(
            """
            CREATE TABLE evidence (
                evidence_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                message_id TEXT,
                correlation_id TEXT,
                content_kind TEXT NOT NULL DEFAULT 'raw_conversation',
                role TEXT NOT NULL,
                written_by TEXT NOT NULL,
                created_at TEXT,
                verbatim TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                revision INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-1",
                "legacy-key",
                "local",
                "cursor",
                "conversation",
                "m1",
                None,
                "raw_conversation",
                "user",
                "person",
                None,
                "old row",
                "hash",
                1,
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )

    migration = next(item for item in MIGRATIONS if item.version == 34)
    result = asyncio.run(migrate(SqliteMigrationStore(path), (migration,)))

    assert result.applied == (34,)
    with main_database(path).read() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(evidence)")}
        row = connection.execute(
            "SELECT evidence_id, ingest_seq FROM evidence WHERE evidence_id='legacy-1'"
        ).fetchone()
    assert "ingest_seq" in columns
    assert row == ("legacy-1", 1)


def test_migration_29_replays_after_its_schema_landed_but_ledger_did_not(
    tmp_path,
) -> None:
    """Call-event objects are not re-creatable after a crash before the ledger.

    `CallEventStore` compares `sqlite_master.sql` byte-for-byte, so migration
    29's SQLite DDL deliberately omits `IF NOT EXISTS`. If the objects landed
    but the process died before the ledger write, a restart must recognise the
    exact existing objects and continue rather than fail with "table already
    exists".
    """
    import asyncio
    import sqlite3

    from one_memory.call_event_store import _SCHEMA
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    store = SqliteMigrationStore(path)
    with sqlite3.connect(path) as connection, connection:
        for sql in _SCHEMA.values():
            connection.execute(sql)

    migration = next(item for item in MIGRATIONS if item.version == 29)
    result = asyncio.run(migrate(store, (migration,)))

    assert result.applied == (29,)
    with sqlite3.connect(path) as connection:
        objects = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type IN ('table','index') AND tbl_name='call_events' "
                "AND sql IS NOT NULL"
            )
        }
    assert objects == _SCHEMA


def test_migration_29_refuses_a_conflicting_call_events_object(tmp_path) -> None:
    """Idempotent replay must not bless an object with a different definition."""
    import asyncio
    import sqlite3

    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    store = SqliteMigrationStore(path)
    with sqlite3.connect(path) as connection, connection:
        connection.execute("CREATE TABLE call_events (payload TEXT)")

    migration = next(item for item in MIGRATIONS if item.version == 29)
    with pytest.raises(RuntimeError, match="call_event_schema_mismatch"):
        asyncio.run(migrate(store, (migration,)))


def test_the_statement_splitter_respects_string_literals() -> None:
    """A `;` inside a string literal must not end the statement.

    The splitter is what turns a rendered migration script into the statements
    the runner executes. A naive `str.split(";")` cuts a default value such as
    `DEFAULT 'a;b'` in half, producing two invalid fragments. The docstring
    already claims string-awareness; this pins it.
    """
    from one_memory.memory_server.migrations import _split_statements

    statements = _split_statements("CREATE TABLE t (x TEXT DEFAULT 'a;b'); CREATE INDEX i ON t(x);")

    assert statements == [
        "CREATE TABLE t (x TEXT DEFAULT 'a;b')",
        "CREATE INDEX i ON t(x)",
    ]


def test_the_statement_splitter_keeps_comment_markers_inside_strings() -> None:
    """`--` inside a string is content, not a comment."""
    from one_memory.memory_server.migrations import _split_statements

    statements = _split_statements(
        "CREATE TABLE t (x TEXT DEFAULT 'a--b'); CREATE INDEX i ON t(x);"
    )

    assert statements == [
        "CREATE TABLE t (x TEXT DEFAULT 'a--b')",
        "CREATE INDEX i ON t(x)",
    ]


def test_the_statement_splitter_still_drops_real_comments() -> None:
    """A genuine `--` comment is still removed, as before."""
    from one_memory.memory_server.migrations import _split_statements

    statements = _split_statements(
        "-- explain\nCREATE TABLE t (x TEXT); -- trailing\nCREATE INDEX i ON t(x);"
    )

    assert statements == ["CREATE TABLE t (x TEXT)", "CREATE INDEX i ON t(x)"]


def test_retrieval_index_ddl_v1_is_a_real_baseline_and_v2_folds_the_upgrade() -> None:
    """migration 33 owns the schema: v1 baseline vs folded v2.

    `index_store.initialize` needs a real v1 shape so `migrate_v2` can be an
    observable create-copy-drop-rename upgrade, while a fresh database (and
    every production caller) uses the default folded v2. The single structural
    delta is `retrieval_build_maps` (plus the v2 `excluded` document state).
    Emitting `retrieval_build_maps` in a v1 baseline made the upgrade impossible
    to observe and left the C3 `migrate_v2` tests red.
    """
    import sqlite3

    from one_memory.memory_server.migrations import (
        _split_statements,
        retrieval_index_ddl,
    )

    def tables(dialect: str, *, version: int) -> set[str]:
        db = sqlite3.connect(":memory:")
        db.executescript(retrieval_index_ddl(dialect, version=version))
        return {
            str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    v1 = tables("sqlite", version=1)
    v2 = tables("sqlite", version=2)

    # v2 adds exactly the build-maps table; everything else is shared.
    assert "retrieval_build_maps" not in v1
    assert "retrieval_build_maps" in v2
    assert v2 - v1 == {"retrieval_build_maps"}
    assert v1 - v2 == set()

    # The document state CHECK reflects the same delta: v1 has no `excluded`.
    v1_docs = next(
        s
        for s in _split_statements(retrieval_index_ddl("sqlite", version=1))
        if "retrieval_documents (" in s
    )
    v2_docs = next(
        s
        for s in _split_statements(retrieval_index_ddl("sqlite", version=2))
        if "retrieval_documents (" in s
    )
    assert "'excluded'" not in v1_docs
    assert "'excluded'" in v2_docs

    # The default (no version arg) is the folded v2, so production is unchanged.
    assert retrieval_index_ddl("sqlite") == retrieval_index_ddl("sqlite", version=2)
    # MySQL mirrors the same gate and still splits cleanly per statement.
    assert "retrieval_build_maps" not in retrieval_index_ddl("mysql", version=1)
    assert "retrieval_build_maps" in retrieval_index_ddl("mysql", version=2)


def test_migration_49_resets_only_cursor_history_when_conversation_time_is_missing(
    tmp_path,
) -> None:
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    through_48 = tuple(item for item in MIGRATIONS if item.version <= 48)
    asyncio.run(migrate(SqliteMigrationStore(path), through_48))
    cursor_values = (
        "local",
        "cursor",
        "cursor-file",
        "identity",
        "byte:100",
        100,
        1,
        "2026-10-06T00:00:00+00:00",
    )
    with main_database(path).write() as connection, connection:
        connection.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,revision,"
            "imported_at,updated_at,ingest_seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "cursor-missing-time",
                "cursor-missing-time",
                "local",
                "cursor",
                "session",
                "raw_conversation",
                "user",
                "person",
                "fixture",
                "hash",
                1,
                "2026-10-06T00:00:00+00:00",
                "2026-10-06T00:00:00+00:00",
                1,
            ),
        )
        for table in ("source_cursors", "live_source_cursors"):
            connection.execute(
                f"INSERT INTO {table} (user_id,source_id,file_key,file_identity,checkpoint,"
                "observed_size,observed_modified_ns,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                cursor_values,
            )

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    assert result.applied == (49, 50, 51, 52, 53, 54, 55, 56, 57, 58)
    with main_database(path).read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_cursors").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM live_source_cursors").fetchone()[0] == 1


def test_upgrade_drops_obsolete_hy_node_permissions_without_touching_memories(
    tmp_path,
) -> None:
    import sqlite3

    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "existing.sqlite3"
    asyncio.run(
        migrate(
            SqliteMigrationStore(path),
            tuple(migration for migration in MIGRATIONS if migration.version <= 50),
        )
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE hy_node_permissions ("
            "node_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, evidence_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO hy_node_permissions(node_id,fingerprint,evidence_json) "
            "VALUES ('legacy-node', ?, '[]')",
            ("a" * 64,),
        )

    result = asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    assert result.applied == (51, 52, 53, 54, 55, 56, 57, 58)
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "hy_node_permissions" not in tables
    assert "governed_memories" in tables
    assert "evidence" in tables


def test_migration_53_backfills_write_job_event_time_from_person_turn(
    tmp_path,
) -> None:
    import sqlite3

    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "legacy-write-jobs.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE memory_write_jobs (
                job_id TEXT PRIMARY KEY,
                queue_seq INTEGER NOT NULL,
                session_key TEXT NOT NULL,
                source_key TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL,
                state TEXT NOT NULL,
                priority TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                lease_owner TEXT,
                lease_expires_at TEXT,
                hy_invoked INTEGER NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE evidence (
                evidence_id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                written_by TEXT NOT NULL,
                created_at TEXT,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE memory_write_job_evidence (
                job_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                PRIMARY KEY (job_id, evidence_id)
            );
            INSERT INTO memory_write_jobs VALUES (
                'job-1',1,'session','cursor','batch','idem','{}','[]',
                'pending','normal',0,3,NULL,NULL,0,NULL,
                '2026-10-18T08:00:00+00:00','2026-10-18T08:00:00+00:00'
            );
            INSERT INTO evidence VALUES (
                'user-1','user','person','2026-10-16T01:02:03+00:00',
                '2026-10-18T08:00:00+00:00'
            );
            INSERT INTO memory_write_job_evidence VALUES ('job-1','user-1');
            """
        )

    migration = next(item for item in MIGRATIONS if item.version == 53)
    result = asyncio.run(migrate(SqliteMigrationStore(path), (migration,)))

    assert result.applied == (53,)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT event_at,time_quality FROM memory_write_jobs"
        ).fetchone() == ("2026-10-16T01:02:03+00:00", "approximate")
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='memory_write_schedule_state'"
        ).fetchone() == (1,)


def test_migration_54_preserves_legacy_possibly_sent_tasks(tmp_path) -> None:
    import sqlite3

    from one_memory.memory_server.migration_runner import SqliteMigrationStore

    path = tmp_path / "one-memory.sqlite3"
    through_53 = tuple(item for item in MIGRATIONS if item.version <= 53)
    asyncio.run(migrate(SqliteMigrationStore(path), through_53))
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            """
            INSERT INTO memory_tasks (
                task_id,request_id,user_id,kind,input_ref,status,
                profile_id,profile_revision,profile_snapshot_json,
                attempts,created_at,updated_at,hy_invocation_count
            ) VALUES (?,?,?,?,?,?,?,?,?,0,?,?,0)
            """,
            (
                "legacy-sent",
                "legacy-sent-request",
                "local",
                "extract",
                "evidence",
                "running",
                "profile",
                1,
                "{}",
                "2026-10-18T00:00:00+00:00",
                "2026-10-18T00:00:00+00:00",
            ),
        )
        connection.execute(
            "CREATE TABLE memory_task_continuous_permissions "
            "(task_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO memory_task_continuous_permissions VALUES (?)",
            ("legacy-sent",),
        )

    asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT hy_invocation_count FROM memory_tasks WHERE task_id='legacy-sent'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='memory_task_continuous_permissions'"
        ).fetchone() is None
