"""A1-03: versioned schema migrations, applied in order, recorded in the database.

The architecture forbids business modules creating or altering their own tables
at startup. The current Core does exactly that (inline `CREATE TABLE IF NOT
EXISTS` scattered across modules), which is why the schema has no single answer
to "what version is this database?" and why a partial failure can leave a
half-built schema that every later startup silently tolerates.

So migrations here are:

- **Ordered and explicit.** Each has a version and applies only after its
  predecessor, so a database cannot skip a step.
- **Recorded.** Applied versions live in a table, so re-running is a no-op and
  the current version is a query rather than an inference.
- **Retried, not assumed.** A migration that fails partway is not recorded, so
  the next run repeats it rather than assuming the schema is fine.

Adding a migration with a duplicate version is rejected. Two migrations sharing
a version would apply in list order, which across machines means different
schemas from identical code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

#: Table recording which migrations have been applied.
SCHEMA_TABLE = "schema_migrations"


@dataclass(frozen=True)
class Migration:
    """One schema step."""

    version: int
    name: str
    #: Receives a cursor. Must not commit: the runner owns the transaction.
    apply: Callable[[Any], Awaitable[Any]]
    #: Tables that must exist once this version is recorded. Empty means the
    #: migration only changes an existing table (for example, a column or
    #: index), so the runner cannot audit it by table existence alone.
    tables: tuple[str, ...] = ()


def validate_migrations(migrations: Sequence[Migration]) -> None:
    """Reject duplicate or unordered versions before anything runs."""
    seen: set[int] = set()
    for migration in migrations:
        if migration.version in seen:
            raise ValueError(f"duplicate migration version {migration.version}")
        seen.add(migration.version)
    if list(migrations) != sorted(migrations, key=lambda item: item.version):
        raise ValueError("migrations must be listed in ascending version order")


def pending_migrations(migrations: Sequence[Migration], applied: set[int]) -> list[Migration]:
    """Migrations not yet applied, in version order."""
    return [item for item in migrations if item.version not in applied]


def plan_upgrade(migrations: Sequence[Migration], current_version: int) -> list[Migration]:
    """What must run to move from `current_version` to the latest.

    `current_version` is the highest applied version, not a count, so a
    database that started late still gets exactly the right steps.
    """
    return [item for item in migrations if item.version > current_version]


def current_version(applied: set[int]) -> int:
    """Highest applied version. Zero means never migrated."""
    return max(applied, default=0)


# ---------------------------------------------------------------------------
# The migrations themselves. Each is one coherent schema state; the SQL is
# written for MySQL 8.x and avoids SQLite-isms the current Core relies on
# (`AUTOINCREMENT`, `PRAGMA`, `rowid`).
# ---------------------------------------------------------------------------


def _dialect(target: Any) -> str:
    """Which SQL rendering this run must emit.

    The store owns the engine decision (`MigrationStore.dialect`), so the
    migration bodies render for it rather than hardcoding MySQL. Defaults to
    MySQL for a bare cursor that carries no dialect, which keeps any older
    caller working.
    """
    return getattr(target, "dialect", "mysql")


async def _001_schema_table(cursor: Any) -> None:
    """The migrations ledger itself must exist before anything is recorded."""
    if _dialect(cursor) == "sqlite":
        await cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        return
    await cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (
            version INT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def memory_write_jobs_ddl(dialect: str = "mysql") -> str:
    """The durable write queue A2-02 requires.

    `session_key` is indexed first because A2-04 schedules per session: one
    session's writes run in order, and claims scan by (state, session).

    `idempotency_key` is UNIQUE, which is what makes "already enqueued" a
    database guarantee instead of a race between two scans.

    `hy_invoked` carries ARCHITECTURE's A2-05 rule: when a lease lapses, only
    a job that certainly never reached HY may go back to `pending`. One that
    may have reached it must become `unknown` instead, because HY has no
    idempotency key (A0-03) and re-running it would write the memory twice.
    Without the column there is no way to tell those two apart at reclaim
    time, which is why the distinction has to be recorded before the call.
    """
    if dialect == "sqlite":
        return """
        CREATE TABLE IF NOT EXISTS memory_write_jobs (
            job_id TEXT PRIMARY KEY,
            queue_seq INTEGER NOT NULL DEFAULT 0,
            session_key TEXT NOT NULL,
            source_key TEXT NOT NULL DEFAULT '',
            batch_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            state TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            lease_owner TEXT,
            lease_expires_at TEXT,
            hy_invoked INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            event_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            time_quality TEXT NOT NULL DEFAULT 'approximate',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_write_jobs_claim
        ON memory_write_jobs(state, priority, created_at);
        CREATE INDEX IF NOT EXISTS idx_write_jobs_session
        ON memory_write_jobs(session_key, state);
        CREATE INDEX IF NOT EXISTS idx_write_jobs_source_claim
        ON memory_write_jobs(state, source_key, priority, created_at);
        CREATE INDEX IF NOT EXISTS idx_write_jobs_source_fifo
        ON memory_write_jobs(source_key, state, queue_seq);
        CREATE INDEX IF NOT EXISTS idx_write_jobs_fifo
        ON memory_write_jobs(queue_seq);
        CREATE INDEX IF NOT EXISTS idx_write_jobs_event_claim
        ON memory_write_jobs(state, event_at, queue_seq);
        CREATE INDEX IF NOT EXISTS idx_write_jobs_source_recent
        ON memory_write_jobs(source_key, updated_at DESC);
        CREATE TABLE IF NOT EXISTS memory_write_job_evidence (
            job_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            PRIMARY KEY (job_id, evidence_id)
        );
        CREATE INDEX IF NOT EXISTS idx_write_job_evidence_evidence
        ON memory_write_job_evidence(evidence_id);
        CREATE TABLE IF NOT EXISTS memory_write_schedule_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            window_anchor TEXT NOT NULL,
            window_end TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    return """
        CREATE TABLE IF NOT EXISTS memory_write_jobs (
            job_id VARCHAR(64) PRIMARY KEY,
            queue_seq BIGINT NOT NULL DEFAULT 0,
            session_key VARCHAR(128) NOT NULL,
            source_key VARCHAR(128) NOT NULL DEFAULT '',
            batch_id VARCHAR(128) NOT NULL,
            idempotency_key VARCHAR(256) NOT NULL,
            payload_json JSON NOT NULL,
            evidence_ids_json JSON NOT NULL DEFAULT ('[]'),
            state VARCHAR(32) NOT NULL,
            priority VARCHAR(16) NOT NULL DEFAULT 'normal',
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 3,
            lease_owner VARCHAR(128) NULL,
            lease_expires_at DATETIME(6) NULL,
            hy_invoked TINYINT(1) NOT NULL DEFAULT 0,
            last_error TEXT NULL,
            event_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            time_quality VARCHAR(32) NOT NULL DEFAULT 'approximate',
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ON UPDATE CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_write_jobs_idempotency (idempotency_key),
            KEY idx_write_jobs_claim (state, priority, created_at),
            KEY idx_write_jobs_session (session_key, state),
            KEY idx_write_jobs_source_claim (state, source_key, priority, created_at),
            KEY idx_write_jobs_source_fifo (source_key, state, queue_seq),
            KEY idx_write_jobs_fifo (queue_seq),
            KEY idx_write_jobs_event_claim (state, event_at, queue_seq),
            KEY idx_write_jobs_source_recent (source_key, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

        CREATE TABLE IF NOT EXISTS memory_write_job_evidence (
            job_id VARCHAR(64) NOT NULL,
            evidence_id VARCHAR(128) NOT NULL,
            PRIMARY KEY (job_id, evidence_id),
            KEY idx_write_job_evidence_evidence (evidence_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

        CREATE TABLE IF NOT EXISTS memory_write_schedule_state (
            singleton INT PRIMARY KEY CHECK (singleton = 1),
            window_anchor DATETIME(6) NOT NULL,
            window_end DATETIME(6) NOT NULL,
            updated_at DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """


async def _002_write_jobs(cursor: Any) -> None:
    """Apply the durable write queue schema for the engine in force."""
    for statement in _split_statements(memory_write_jobs_ddl(_dialect(cursor))):
        await cursor.execute(statement)

    if _dialect(cursor) == "sqlite":
        # The `CREATE TABLE IF NOT EXISTS` above already declares `hy_invoked`,
        # and the migration store's `execute` cannot read rows back, so there
        # is nothing to probe here. A legacy SQLite table that predates the
        # column is upgraded by `DurableWriteQueue._add_legacy_columns`, the
        # single owner of that backfill; duplicating it here would create two
        # places to drift.
        return

    #: Existing databases: `CREATE TABLE IF NOT EXISTS` is a no-op for a table
    #: that is already there, so the column is added separately. MySQL has no
    #: `ADD COLUMN IF NOT EXISTS`, and a fresh database already has the column
    #: from the CREATE above, so the failure to watch for is 1060 (duplicate
    #: column) — which means the work is already done, not that it failed.
    try:
        await cursor.execute(
            """
            ALTER TABLE memory_write_jobs
            ADD COLUMN hy_invoked TINYINT(1) NOT NULL DEFAULT 0
            """
        )
    except Exception as error:  # noqa: BLE001 - see below
        if _is_duplicate_column(error):
            return
        raise


def _is_duplicate_column(error: Exception) -> bool:
    """Whether this is MySQL's "column already there", i.e. nothing to do.

    Matched on the driver's error number (1060) rather than on message text:
    `aiomysql`/PyMySQL expose `args[0]`, and a string match would also catch
    unrelated errors that merely mention a column name.
    """
    args: tuple[object, ...] = getattr(error, "args", ())
    return bool(args) and args[0] == 1060


def memory_write_jobs_sqlite_schema() -> tuple[str, ...]:
    """The D56 SQLite rendering of the durable write queue, for the real store.

    Migration 2 remains the MySQL definition the migration runner applies.
    Under D56 the engine in force is SQLite, so the same shape is created here
    through the unified access layer — one definition, one engine, no dual
    path. Statements are returned rather than executed so the caller owns the
    transaction.
    """
    return tuple(_split_statements(memory_write_jobs_ddl("sqlite")))


def memory_write_jobs_target_sqlite_schema() -> tuple[str, ...]:
    return tuple(
        statement
        for statement in memory_write_jobs_sqlite_schema()
        if "memory_write_job_evidence" not in statement
    )


async def _003_scanner_cursors(cursor: Any) -> None:
    """Per-file scanner state, keyed for batch lookup.

    `file_key` is UNIQUE so `get_many` can fetch a whole page in one query and
    upsert it the same way; a per-file index would be the N+1 the gate bans.
    """
    await cursor.execute(source_file_cursors_ddl(_dialect(cursor)))


def source_file_cursors_ddl(dialect: str = "mysql") -> str:
    """The per-file scanner cursor, rendered per dialect from one definition."""
    if dialect == "sqlite":
        return """
        CREATE TABLE IF NOT EXISTS source_file_cursors (
            file_key TEXT PRIMARY KEY,
            file_identity TEXT NOT NULL,
            observed_size INTEGER NOT NULL DEFAULT 0,
            observed_modified_ns INTEGER NOT NULL DEFAULT 0,
            checkpoint INTEGER NOT NULL DEFAULT 0,
            read_to_end INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    return """
    CREATE TABLE IF NOT EXISTS source_file_cursors (
        file_key VARCHAR(512) PRIMARY KEY,
        file_identity VARCHAR(256) NOT NULL,
        observed_size BIGINT NOT NULL DEFAULT 0,
        observed_modified_ns BIGINT NOT NULL DEFAULT 0,
        checkpoint BIGINT NOT NULL DEFAULT 0,
        read_to_end TINYINT(1) NOT NULL DEFAULT 0,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """


def change_outbox_ddl(dialect: str = "mysql") -> str:
    """The change_outbox table, rendered per dialect from one definition.

    One definition, two renderings. Duplicating the DDL per engine is how the
    two copies drift apart, after which the migration describes a schema the
    code no longer creates.

    `INSERT OR IGNORE` has no MySQL equivalent, so "already recorded" is
    carried by the PRIMARY KEY instead of by dialect-specific insert syntax;
    the writer uses `INSERT ... ON DUPLICATE KEY UPDATE` on MySQL.
    """
    if dialect == "sqlite":
        return """
        CREATE TABLE IF NOT EXISTS change_outbox (
            event_id TEXT PRIMARY KEY,
            cause TEXT NOT NULL,
            evidence_id TEXT,
            evidence_revision INTEGER,
            permission_epoch INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            notified_at TEXT
        )
        """
    return """
        CREATE TABLE IF NOT EXISTS change_outbox (
            event_id VARCHAR(128) PRIMARY KEY,
            cause VARCHAR(128) NOT NULL,
            evidence_id VARCHAR(128) NULL,
            evidence_revision INT NULL,
            permission_epoch INT NOT NULL,
            created_at DATETIME(6) NOT NULL,
            notified_at DATETIME(6) NULL,
            KEY idx_change_outbox_pending (notified_at, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """


async def _004_change_outbox(cursor: Any) -> None:
    """Change outbox, migrated out of `change_outbox.py`.

    This is the first domain moved under migration ownership, chosen because it
    is small and its writer already runs inside a caller-owned transaction.
    """
    await cursor.execute(change_outbox_ddl(_dialect(cursor)))


def session_leases_ddl(dialect: str = "mysql") -> str:
    """Per-session lease, so one session is not written by two workers at once.

    Mirrors what `memory_write_jobs.lease_*` does for queued jobs, but for the
    interactive path. The UNIQUE key on `session_key` is what makes "held by
    someone else" a database condition rather than a check-then-act race.
    """
    if dialect == "sqlite":
        return """
        CREATE TABLE IF NOT EXISTS session_leases (
            session_key TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            generation INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    return """
        CREATE TABLE IF NOT EXISTS session_leases (
            session_key VARCHAR(255) PRIMARY KEY,
            pid INT NOT NULL,
            generation INT NOT NULL,
            expires_at DATETIME(6) NOT NULL,
            KEY idx_session_leases_expiry (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """


def trusted_sessions_ddl(dialect: str = "mysql") -> str:
    """Trusted host sessions.

    The composite UNIQUE is the real constraint: the same native session seen
    from one installation must not be recorded twice, and no single column can
    express that.
    """
    if dialect == "sqlite":
        return """
        CREATE TABLE IF NOT EXISTS trusted_sessions (
            session_key TEXT PRIMARY KEY,
            installation_id TEXT NOT NULL,
            host_id TEXT NOT NULL,
            native_session_id TEXT NOT NULL,
            runtime_instance TEXT NOT NULL,
            workspace TEXT,
            UNIQUE (installation_id, host_id, native_session_id, runtime_instance)
        )
        """
    return """
        CREATE TABLE IF NOT EXISTS trusted_sessions (
            session_key VARCHAR(255) PRIMARY KEY,
            installation_id VARCHAR(255) NOT NULL,
            host_id VARCHAR(255) NOT NULL,
            native_session_id VARCHAR(255) NOT NULL,
            runtime_instance VARCHAR(255) NOT NULL,
            workspace VARCHAR(1024) NULL,
            #: Prefixed to fit InnoDB's 3072-byte key limit.
            #:
            #: Four `VARCHAR(255)` columns at `utf8mb4` (4 bytes/char) total
            #: 4080 bytes, which MySQL rejects with error 1071 — found by
            #: running 8.4.4, not by reading the docs. 190 characters each
            #: totals 3040 bytes. These carry generated identifiers, not user
            #: prose, so 190 is far beyond any real value, and the safe
            #: direction is preserved: a longer value collides and is rejected
            #: rather than silently admitted as a second session.
            UNIQUE KEY uq_trusted_session_identity
                (installation_id(190), host_id(190), native_session_id(190),
                 runtime_instance(190))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """


async def _005_session_leases(cursor: Any) -> None:
    await cursor.execute(session_leases_ddl(_dialect(cursor)))


async def _006_trusted_sessions(cursor: Any) -> None:
    await cursor.execute(trusted_sessions_ddl(_dialect(cursor)))


def import_jobs_ddl(dialect: str = "mysql") -> str:
    """Import jobs, issues, skip counts and the source cursor table.

    This is the domain the scanner actually depends on, and it is also where
    the SQLite-only constructs were densest:

    - `PRAGMA foreign_keys = ON` has no MySQL equivalent; referential integrity
      is declared per constraint instead of toggled per connection.
    - `INTEGER PRIMARY KEY AUTOINCREMENT` becomes `AUTO_INCREMENT`.
    - `TEXT` becomes a bounded `VARCHAR`, because MySQL would otherwise pick a
      type that cannot be indexed the way these columns are queried.

    The composite primary key on `source_cursors`
    `(user_id, source_id, file_key)` is the one the batch cursor lookup relies
    on, so it is preserved exactly rather than replaced with a surrogate id.
    """
    if dialect == "sqlite":
        return _IMPORT_JOBS_SQLITE
    return _IMPORT_JOBS_MYSQL


_IMPORT_JOBS_SQLITE = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS import_jobs (
    job_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT,
    runner_token TEXT,
    heartbeat_at TEXT,
    checkpoint TEXT,
    discovered INTEGER NOT NULL DEFAULT 0,
    discovered_targets INTEGER NOT NULL DEFAULT 0,
    emitted INTEGER NOT NULL DEFAULT 0,
    stored INTEGER NOT NULL DEFAULT 0,
    revised INTEGER NOT NULL DEFAULT 0,
    merged INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    history_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES import_jobs(job_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    conversation_id TEXT,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_skip_reason_counts (
    job_id TEXT NOT NULL REFERENCES import_jobs(job_id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, reason)
);

CREATE TABLE IF NOT EXISTS source_cursors (
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    file_key TEXT NOT NULL,
    file_identity TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    checkpoint_anchor TEXT,
    observed_size INTEGER NOT NULL,
    observed_modified_ns INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, source_id, file_key)
);

CREATE TABLE IF NOT EXISTS live_source_cursors (
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    file_key TEXT NOT NULL,
    file_identity TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    checkpoint_anchor TEXT,
    observed_size INTEGER NOT NULL,
    observed_modified_ns INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    read_to_end INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, source_id, file_key)
);
"""

_IMPORT_JOBS_MYSQL = """
CREATE TABLE IF NOT EXISTS import_jobs (
    job_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    phase VARCHAR(64) NULL,
    runner_token VARCHAR(128) NULL,
    heartbeat_at DATETIME(6) NULL,
    checkpoint TEXT NULL,
    discovered INT NOT NULL DEFAULT 0,
    discovered_targets INT NOT NULL DEFAULT 0,
    emitted INT NOT NULL DEFAULT 0,
    `stored` INT NOT NULL DEFAULT 0,
    revised INT NOT NULL DEFAULT 0,
    merged INT NOT NULL DEFAULT 0,
    skipped INT NOT NULL DEFAULT 0,
    errors INT NOT NULL DEFAULT 0,
    history_after DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    KEY idx_import_jobs_status (status, updated_at),
    KEY idx_import_jobs_source (user_id, source_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS import_issues (
    issue_id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    job_id VARCHAR(128) NOT NULL,
    kind VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(512) NULL,
    reason TEXT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    CONSTRAINT fk_import_issues_job FOREIGN KEY (job_id)
        REFERENCES import_jobs(job_id) ON DELETE CASCADE,
    KEY idx_import_issues_job (job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS import_skip_reason_counts (
    job_id VARCHAR(128) NOT NULL,
    reason VARCHAR(128) NOT NULL,
    count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, reason),
    CONSTRAINT fk_import_skip_job FOREIGN KEY (job_id)
        REFERENCES import_jobs(job_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS source_cursors (
    user_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    file_key VARCHAR(512) NOT NULL,
    file_identity VARCHAR(256) NOT NULL,
    checkpoint TEXT NOT NULL,
    checkpoint_anchor VARCHAR(128) NULL,
    observed_size BIGINT NOT NULL,
    observed_modified_ns BIGINT NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id, source_id, file_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS live_source_cursors (
    user_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    file_key VARCHAR(512) NOT NULL,
    file_identity VARCHAR(256) NOT NULL,
    checkpoint TEXT NOT NULL,
    checkpoint_anchor VARCHAR(128) NULL,
    observed_size BIGINT NOT NULL,
    observed_modified_ns BIGINT NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    read_to_end INT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, source_id, file_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _007_import_jobs(cursor: Any) -> None:
    """Statements are split because MySQL executes one DDL per call."""
    for statement in _split_statements(import_jobs_ddl(_dialect(cursor))):
        await cursor.execute(statement)


#: The evidence table and its indices, in both dialects.
#:
#: The index comments below are carried over verbatim from `imports/storage.py`
#: because each one records a *measurement* of a real database, not a guess.
#: Deleting them would throw away the only record of why these particular
#: compound indices exist and in this column order.
_EVIDENCE_SQLITE = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    workspace TEXT,
    project TEXT,
    conversation_id TEXT NOT NULL,
    message_id TEXT,
    correlation_id TEXT,
    source_ordinal TEXT,
    content_kind TEXT NOT NULL DEFAULT 'raw_conversation',
    role TEXT NOT NULL,
    written_by TEXT NOT NULL,
    created_at TEXT,
    verbatim TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    revision INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- Explicit import order, so nothing has to depend on SQLite's rowid.
    -- See migration 34 and `matters/selection.py`.
    ingest_seq INTEGER
);

CREATE INDEX IF NOT EXISTS evidence_conversation_index
ON evidence(user_id, source_id, conversation_id, workspace, project);

CREATE INDEX IF NOT EXISTS evidence_coverage_index
ON evidence(user_id, source_id, conversation_id, written_by, created_at, evidence_id);

CREATE INDEX IF NOT EXISTS evidence_message_index
ON evidence(user_id, source_id, conversation_id, message_id, content_hash);

CREATE INDEX IF NOT EXISTS evidence_conversation_project_index
ON evidence(user_id, source_id, conversation_id, project);

CREATE INDEX IF NOT EXISTS evidence_workspace_index
ON evidence(user_id, source_id, workspace, project)
WHERE workspace IS NOT NULL;

-- Serves `list_recent_conversations` (ORDER BY imported_at DESC, evidence_id DESC),
-- the production hot path. Before this index the query had nothing starting at
-- `imported_at` and sorted the whole ~430k-row table (measured median 2466ms;
-- 11218ms on the debug Agent's real migration), holding one pooled read
-- connection for the duration — that single query is what pushed
-- `reader_wait_ms_max` to 18138.5ms. After it: 0.0ms, plan becomes
-- SEARCH USING COVERING INDEX.
CREATE INDEX IF NOT EXISTS evidence_imported_index
ON evidence(user_id, imported_at DESC, evidence_id DESC);

-- Serves the durable-write reconcile discovery
-- (`unsubmitted_evidence_ids`: ORDER BY imported_at, evidence_id with no
-- user_id predicate). `evidence_imported_index` is prefixed by user_id and
-- cannot serve that order, so without this the discovery page scans the whole
-- Evidence table and sorts it through a temp B-tree on every page -- a cost
-- that grows with corpus size and page count on the reconcile pass.
CREATE INDEX IF NOT EXISTS evidence_reconcile_order
ON evidence(imported_at, evidence_id);

CREATE TABLE IF NOT EXISTS evidence_repairs (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_origins (
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    origin TEXT NOT NULL,
    raw_meta_json TEXT NOT NULL,
    source_version TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (evidence_id, origin)
);

CREATE TABLE IF NOT EXISTS evidence_versions (
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    verbatim TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    written_by TEXT NOT NULL,
    created_at TEXT,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (evidence_id, revision)
);

-- Import callbacks must not create memory work from a partially read file.
-- This survives runner slices and restarts; the file-end cursor drains it.
CREATE TABLE IF NOT EXISTS evidence_pending_memory_dispatch (
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    file_key TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('normal','live')),
    PRIMARY KEY (user_id, source_id, file_key, evidence_id)
);
"""

#: MySQL rendering. Two differences are forced, not cosmetic:
#: - Partial indices (`WHERE workspace IS NOT NULL`) do not exist in MySQL, so
#:   that index becomes a full one. The reason for the partial index was that
#:   unscoped rows are the majority and can never be an answer; on MySQL the
#:   planner makes that choice at execution time instead.
#: - **Every composite index carries explicit prefix lengths.** InnoDB caps a
#:   key at 3072 bytes and `utf8mb4` costs 4 bytes per character, so the
#:   declared widths here (two `VARCHAR(1024)` plus a `VARCHAR(512)`) make the
#:   unprefixed indexes 7168–11264 bytes. All of them were rejected by 8.4.4
#:   with error 1071, which also blocked `evidence` from existing at all and
#:   cascaded into v22/v34. The previous note said only `idempotency_key`
#:   needed a prefix; that was wrong, and nothing had been applied.
#:
#:   The prefixes are chosen so each index lands under 3072 bytes with room to
#:   spare while keeping the leading equality columns (`user_id`, `source_id`)
#:   complete — those are what make the index selective. Truncating a path-like
#:   tail column costs selectivity on a range scan, not correctness.
_EVIDENCE_MYSQL = """
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id VARCHAR(128) PRIMARY KEY,
    idempotency_key VARCHAR(255) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    workspace VARCHAR(1024) NULL,
    project VARCHAR(1024) NULL,
    conversation_id VARCHAR(512) NOT NULL,
    message_id VARCHAR(512) NULL,
    correlation_id VARCHAR(512) NULL,
    source_ordinal VARCHAR(256) NULL,
    content_kind VARCHAR(64) NOT NULL DEFAULT 'raw_conversation',
    role VARCHAR(64) NOT NULL,
    written_by VARCHAR(64) NOT NULL,
    created_at DATETIME(6) NULL,
    verbatim MEDIUMTEXT NOT NULL,
    content_hash VARCHAR(128) NOT NULL,
    revision INT NOT NULL,
    imported_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_evidence_idempotency (idempotency_key(191)),
    KEY evidence_conversation_index
        (user_id, source_id, conversation_id(128), workspace(128), project(128)),
    KEY evidence_coverage_index
        (user_id, source_id, conversation_id(128), written_by, created_at, evidence_id),
    KEY evidence_message_index
        (user_id, source_id, conversation_id(128), message_id(128), content_hash),
    KEY evidence_conversation_project_index
        (user_id, source_id, conversation_id(128), project(128)),
    KEY evidence_workspace_index (user_id, source_id, workspace(128), project(128)),
    -- Prefixed like the neighbours: InnoDB caps an index key at 3072 bytes
    -- and utf8mb4 is 4 bytes a character. `imported_at` is a fixed-width
    -- DATETIME(6), evidence_id is the 36-character uuid form.
    KEY evidence_imported_index (user_id, imported_at, evidence_id(36))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 存量 MySQL 库：CREATE TABLE IF NOT EXISTS 对已存在的 evidence 表是 no-op，故单独建索引。
-- 前缀长度同邻居惯例（InnoDB 索引键上限 3072 字节，utf8mb4 4 字节/字符；imported_at 是定长
-- DATETIME(6)，evidence_id 是 36 字符 uuid 形式。该索引服务 list_recent_conversations 的
-- ORDER BY imported_at DESC, evidence_id DESC；此前无对应索引导致 43 万行全表排序（秒级）。
CREATE INDEX IF NOT EXISTS evidence_imported_index
ON evidence (user_id, imported_at DESC, evidence_id(36));

CREATE TABLE IF NOT EXISTS evidence_repairs (
    name VARCHAR(128) PRIMARY KEY,
    applied_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS evidence_origins (
    evidence_id VARCHAR(128) NOT NULL,
    origin VARCHAR(64) NOT NULL,
    raw_meta_json MEDIUMTEXT NOT NULL,
    source_version VARCHAR(128) NULL,
    observed_at DATETIME(6) NOT NULL,
    PRIMARY KEY (evidence_id, origin),
    CONSTRAINT fk_evidence_origins_evidence FOREIGN KEY (evidence_id)
        REFERENCES evidence(evidence_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS evidence_versions (
    evidence_id VARCHAR(128) NOT NULL,
    revision INT NOT NULL,
    verbatim MEDIUMTEXT NOT NULL,
    content_hash VARCHAR(128) NOT NULL,
    role VARCHAR(64) NOT NULL,
    written_by VARCHAR(64) NOT NULL,
    created_at DATETIME(6) NULL,
    changed_at DATETIME(6) NOT NULL,
    PRIMARY KEY (evidence_id, revision),
    CONSTRAINT fk_evidence_versions_evidence FOREIGN KEY (evidence_id)
        REFERENCES evidence(evidence_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS evidence_pending_memory_dispatch (
    user_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    file_key VARCHAR(512) NOT NULL,
    evidence_id VARCHAR(128) NOT NULL,
    priority VARCHAR(16) NOT NULL DEFAULT 'normal',
    #: Prefixed for the same 3072-byte reason as the `evidence` indexes above:
    #: unprefixed this primary key is (128+128+512+128) x 4 = 3584 bytes and
    #: MySQL refuses the table. `file_key` keeps 200 characters (well past any
    #: real path) while the two identity columns stay whole.
    PRIMARY KEY (user_id, source_id, file_key(200), evidence_id),
    KEY idx_pending_dispatch_file (user_id, source_id, file_key(200)),
    CONSTRAINT fk_pending_dispatch_evidence FOREIGN KEY (evidence_id)
        REFERENCES evidence(evidence_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def evidence_ddl(dialect: str = "mysql", *, without_imported_index: bool = False) -> str:
    """The evidence table: the largest and most queried table in the product.

    `without_imported_index` omits the ordering index so that opening a large
    existing database does not block for the seconds it takes to build. The
    store builds it in the background instead; see
    `EvidenceStore.ensure_imported_index_in_background`. Measured at 9.28 s on
    432k rows, all of it previously inside startup.
    """
    ddl = _EVIDENCE_SQLITE if dialect == "sqlite" else _EVIDENCE_MYSQL
    if not without_imported_index:
        return ddl
    index = "evidence_imported_index"
    kept = [statement for statement in _split_statements(ddl) if index not in statement]
    return ";\n".join(kept) + ";\n"


async def _008_evidence(cursor: Any) -> None:
    for statement in _split_statements(evidence_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def source_grants_ddl(dialect: str = "mysql") -> str:
    """One canonical root grant per user and source."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS source_grants (
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    root_path TEXT NOT NULL,
    requested_path TEXT NOT NULL,
    root_identity TEXT NOT NULL,
    candidate_kind TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    revoked_at TEXT,
    revision INTEGER NOT NULL,
    PRIMARY KEY (user_id, source_id)
);
"""
    return """
CREATE TABLE IF NOT EXISTS source_grants (
    user_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    root_path VARCHAR(4096) NOT NULL,
    requested_path VARCHAR(4096) NOT NULL,
    root_identity VARCHAR(512) NOT NULL,
    candidate_kind VARCHAR(64) NOT NULL,
    granted_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    revision INT NOT NULL,
    PRIMARY KEY (user_id, source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def connection_intents_ddl(dialect: str = "mysql") -> str:
    """Connection intents, keyed so a repeated request cannot double-apply."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS source_connection_intents (
    user_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    accepted_json TEXT NOT NULL,
    failed_json TEXT NOT NULL,
    job_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, request_id)
);
"""
    return """
CREATE TABLE IF NOT EXISTS source_connection_intents (
    user_id VARCHAR(128) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    request_hash VARCHAR(128) NOT NULL,
    accepted_json MEDIUMTEXT NOT NULL,
    failed_json MEDIUMTEXT NOT NULL,
    job_ids_json MEDIUMTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id, request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _009_source_grants(cursor: Any) -> None:
    for statement in _split_statements(source_grants_ddl(_dialect(cursor))):
        await cursor.execute(statement)


async def _010_connection_intents(cursor: Any) -> None:
    await cursor.execute(connection_intents_ddl(_dialect(cursor)))


def session_inbox_ddl(dialect: str = "mysql") -> str:
    """Session subscriptions, cursors and batches.

    `permission_epoch` on each table is what lets a revoked grant invalidate
    already-scanned state without a scan; it is kept on every row rather than
    only on the subscription for the same reason a lease expires rather than
    being trusted.

    `session_inbox_batches` carries `state` (not a timestamp): a batch is
    either pending or consumed, and a creation time would not answer that.
    """
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS session_subscriptions (
    session_key TEXT PRIMARY KEY,
    granted_sources TEXT NOT NULL,
    permission_epoch INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS session_inbox_cursors (
    cursor_token TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    scanned_event_id TEXT,
    permission_epoch INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS session_inbox_batches (
    batch_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_snapshots (
    snapshot_token TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    frozen_ids TEXT NOT NULL
);
"""
    return """
CREATE TABLE IF NOT EXISTS session_subscriptions (
    session_key VARCHAR(255) PRIMARY KEY,
    granted_sources MEDIUMTEXT NOT NULL,
    permission_epoch INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS session_inbox_cursors (
    cursor_token VARCHAR(255) PRIMARY KEY,
    session_key VARCHAR(255) NOT NULL,
    scanned_event_id VARCHAR(128) NULL,
    permission_epoch INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS session_inbox_batches (
    batch_id VARCHAR(255) PRIMARY KEY,
    session_key VARCHAR(255) NOT NULL,
    state VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS session_snapshots (
    snapshot_token VARCHAR(255) PRIMARY KEY,
    session_key VARCHAR(255) NOT NULL,
    frozen_ids MEDIUMTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def onboarding_state_ddl(dialect: str = "mysql") -> str:
    """Singleton onboarding state.

    The `singleton = 1` CHECK is what stops a second row; it is preserved
    rather than replaced by "just read the first row", because a duplicate
    would silently make onboarding state ambiguous.
    """
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS onboarding_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    status TEXT NOT NULL,
    selected_source_ids_json TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);
"""
    return """
CREATE TABLE IF NOT EXISTS onboarding_state (
    singleton INT PRIMARY KEY CHECK (singleton = 1),
    status VARCHAR(64) NOT NULL,
    selected_source_ids_json MEDIUMTEXT NOT NULL,
    started_at DATETIME(6) NULL,
    completed_at DATETIME(6) NULL,
    updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _011_session_inbox(cursor: Any) -> None:
    for statement in _split_statements(session_inbox_ddl(_dialect(cursor))):
        await cursor.execute(statement)


async def _012_onboarding_state(cursor: Any) -> None:
    await cursor.execute(onboarding_state_ddl(_dialect(cursor)))


def memory_tasks_ddl(dialect: str = "mysql") -> str:
    """Memory tasks and their satellite tables.

    The CHECK on `memory_task_extractions` is the one thing here that must not
    be dropped in translation: it enforces that `content` and `layer` are
    either both present or both absent. A row with one and not the other is
    not a partial write to be tolerated, it is a contradiction, and it would
    otherwise be discovered much later at read time.

    `UNIQUE (user_id, kind, request_id)` is what makes re-submitting the same
    request a no-op rather than a second task.
    """
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS memory_tasks (
    task_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    input_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    profile_revision INTEGER NOT NULL,
    profile_snapshot_json TEXT NOT NULL,
    runner_token TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    duration_seconds REAL,
    UNIQUE (user_id, kind, request_id)
);
CREATE INDEX IF NOT EXISTS memory_tasks_status
ON memory_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS memory_task_evidence
ON memory_tasks(input_ref, created_at, task_id);
-- Serves the `unknown`-task reconcile order (`unknown_tasks`: ORDER BY
-- updated_at, task_id). `memory_tasks_status` is (status, created_at), so it
-- cannot cover that order; without this the task half of the reconcile pass
-- materialises and sorts every unknown row. Mirrors idx_write_jobs_unknown.
CREATE INDEX IF NOT EXISTS idx_memory_tasks_unknown
ON memory_tasks(status, updated_at, task_id);
CREATE TABLE IF NOT EXISTS memory_task_extractions (
    task_id TEXT PRIMARY KEY REFERENCES memory_tasks(task_id) ON DELETE CASCADE,
    content TEXT,
    layer TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (content IS NULL AND layer IS NULL)
        OR (content IS NOT NULL AND layer IS NOT NULL)
    )
);
CREATE TABLE IF NOT EXISTS memory_task_inputs (
    task_id TEXT PRIMARY KEY, refs_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_task_outputs (
    task_id TEXT NOT NULL, memory_id TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY(task_id, memory_id)
);
CREATE INDEX IF NOT EXISTS memory_task_outputs_memory ON memory_task_outputs(memory_id);
CREATE TABLE IF NOT EXISTS memory_task_contexts (
    task_id TEXT PRIMARY KEY, refs_json TEXT NOT NULL
);
"""
    return """
CREATE TABLE IF NOT EXISTS memory_tasks (
    task_id VARCHAR(128) PRIMARY KEY,
    request_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    kind VARCHAR(64) NOT NULL,
    input_ref VARCHAR(512) NOT NULL,
    status VARCHAR(64) NOT NULL,
    profile_id VARCHAR(128) NOT NULL,
    profile_revision INT NOT NULL,
    profile_snapshot_json MEDIUMTEXT NOT NULL,
    runner_token VARCHAR(128) NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_error_code VARCHAR(128) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    duration_seconds DOUBLE NULL,
    UNIQUE KEY uq_memory_task_request (user_id, kind, request_id),
    KEY memory_tasks_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS memory_task_extractions (
    task_id VARCHAR(128) PRIMARY KEY,
    content MEDIUMTEXT NULL,
    layer VARCHAR(64) NULL,
    created_at DATETIME(6) NOT NULL,
    CONSTRAINT chk_extraction_pair CHECK (
        (content IS NULL AND layer IS NULL)
        OR (content IS NOT NULL AND layer IS NOT NULL)
    ),
    CONSTRAINT fk_extraction_task FOREIGN KEY (task_id)
        REFERENCES memory_tasks(task_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS memory_task_inputs (
    task_id VARCHAR(128) PRIMARY KEY,
    refs_json MEDIUMTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS memory_task_outputs (
    task_id VARCHAR(128) NOT NULL,
    memory_id VARCHAR(128) NOT NULL,
    body MEDIUMTEXT NOT NULL,
    PRIMARY KEY (task_id, memory_id),
    KEY memory_task_outputs_memory (memory_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS memory_task_contexts (
    task_id VARCHAR(128) PRIMARY KEY,
    refs_json MEDIUMTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _014_memory_tasks(cursor: Any) -> None:
    for statement in _split_statements(memory_tasks_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def ingest_journal_ddl(dialect: str = "mysql") -> str:
    """Append-only ingest journal plus its singleton state row.

    `generation_eligible` is CHECKed to 0/1 because it is a boolean stored in a
    column type that would otherwise accept anything; a stray value would
    silently change which events are ever considered for generation.
    """
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS evidence_ingest_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE, incarnation TEXT NOT NULL,
    incarnation_seq INTEGER NOT NULL,
    user_id TEXT NOT NULL, evidence_id TEXT NOT NULL, source_id TEXT NOT NULL,
    evidence_revision INTEGER NOT NULL, kind TEXT NOT NULL,
    generation_eligible INTEGER NOT NULL CHECK(generation_eligible IN (0,1)),
    content_hash TEXT NOT NULL, material_hash TEXT NOT NULL, identity_hash TEXT NOT NULL,
    origin_hash TEXT NOT NULL, fingerprint TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ingest_events_identity
ON evidence_ingest_events(evidence_id,seq);
CREATE INDEX IF NOT EXISTS ingest_events_user_seq
ON evidence_ingest_events(user_id,seq);
CREATE TABLE IF NOT EXISTS evidence_ingest_journal_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL
)
"""
    return """
CREATE TABLE IF NOT EXISTS evidence_ingest_events (
    seq BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    event_id VARCHAR(128) NOT NULL,
    incarnation VARCHAR(128) NOT NULL,
    incarnation_seq INT NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    evidence_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    evidence_revision INT NOT NULL,
    kind VARCHAR(64) NOT NULL,
    generation_eligible TINYINT(1) NOT NULL CHECK (generation_eligible IN (0,1)),
    content_hash VARCHAR(128) NOT NULL,
    material_hash VARCHAR(128) NOT NULL,
    identity_hash VARCHAR(128) NOT NULL,
    origin_hash VARCHAR(128) NOT NULL,
    fingerprint VARCHAR(128) NOT NULL,
    recorded_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_ingest_event_id (event_id),
    KEY ingest_events_identity (evidence_id, seq),
    KEY ingest_events_user_seq (user_id, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS evidence_ingest_journal_state (
    singleton INT PRIMARY KEY CHECK (singleton = 1),
    version INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _016_ingest_journal(cursor: Any) -> None:
    for statement in _split_statements(ingest_journal_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def extraction_settings_ddl(dialect: str = "mysql") -> str:
    """Singleton extraction settings; the CHECK stops a second row."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS extraction_settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    window_preset TEXT NOT NULL
);
"""
    return """
CREATE TABLE IF NOT EXISTS extraction_settings (
    singleton INT PRIMARY KEY CHECK (singleton = 1),
    window_preset VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def model_request_budget_ddl(dialect: str = "mysql") -> str:
    """Per-request budget events, retained for the retention window."""
    if dialect == "sqlite":
        return (
            "CREATE TABLE IF NOT EXISTS model_request_budget_events ("
            "sequence INTEGER PRIMARY KEY, body_bytes INTEGER NOT NULL, "
            "output_tokens INTEGER NOT NULL, reserved_at TEXT NOT NULL)"
        )
    return """
CREATE TABLE IF NOT EXISTS model_request_budget_events (
    sequence BIGINT PRIMARY KEY,
    body_bytes INT NOT NULL,
    output_tokens INT NOT NULL,
    reserved_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def model_request_audit_ddl(dialect: str = "mysql") -> str:
    """Content-free accounting for physical precipitation model requests."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS model_request_audit (
    audit_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    profile_revision INTEGER NOT NULL,
    provider_kind TEXT NOT NULL,
    model TEXT NOT NULL,
    request_kind TEXT NOT NULL,
    prompt_stage TEXT NOT NULL DEFAULT 'other',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('in_flight','succeeded','failed')),
    http_status INTEGER,
    error_type TEXT,
    provider_request_id TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cached_prompt_tokens INTEGER,
    reasoning_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS model_request_audit_request
ON model_request_audit(request_id, started_at);
CREATE INDEX IF NOT EXISTS model_request_audit_started
ON model_request_audit(started_at);
"""
    return """
CREATE TABLE IF NOT EXISTS model_request_audit (
    audit_id VARCHAR(128) PRIMARY KEY,
    request_id VARCHAR(512) NOT NULL,
    profile_id VARCHAR(128) NOT NULL,
    profile_revision INT NOT NULL,
    provider_kind VARCHAR(64) NOT NULL,
    model VARCHAR(512) NOT NULL,
    request_kind VARCHAR(64) NOT NULL,
    prompt_stage VARCHAR(128) NOT NULL DEFAULT 'other',
    started_at DATETIME(6) NOT NULL,
    completed_at DATETIME(6) NULL,
    outcome VARCHAR(32) NOT NULL CHECK(outcome IN ('in_flight','succeeded','failed')),
    http_status INT NULL,
    error_type VARCHAR(128) NULL,
    provider_request_id VARCHAR(512) NULL,
    prompt_tokens BIGINT NULL,
    completion_tokens BIGINT NULL,
    total_tokens BIGINT NULL,
    cached_prompt_tokens BIGINT NULL,
    reasoning_tokens BIGINT NULL,
    KEY model_request_audit_request (request_id, started_at),
    KEY model_request_audit_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _052_model_request_audit(cursor: Any) -> None:
    for statement in _split_statements(model_request_audit_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def model_probe_operations_ddl(dialect: str = "mysql") -> str:
    """Probe operations; `state` is CHECKed to a known set.

    `reserved_at` bounds how long a reservation can fence out new probes: a
    dispatch whose outcome was lost must age into an explicit failure instead
    of fencing the profile forever (2026-10-12: a failed onboarding probe
    locked the config form with no way back).
    """
    if dialect == "sqlite":
        return """CREATE TABLE IF NOT EXISTS model_probe_operations (
    operation_id TEXT PRIMARY KEY NOT NULL,
    profile_id TEXT NOT NULL,
    input_revision INTEGER NOT NULL,
    input_stamp TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','completed','failed')),
    result_json TEXT,
    result_stamp TEXT,
    code TEXT,
    reserved_at TEXT
)"""
    return """
CREATE TABLE IF NOT EXISTS model_probe_operations (
    operation_id VARCHAR(128) PRIMARY KEY,
    profile_id VARCHAR(128) NOT NULL,
    input_revision INT NOT NULL,
    input_stamp VARCHAR(128) NOT NULL,
    state VARCHAR(32) NOT NULL CHECK (state IN ('pending','completed','failed')),
    result_json MEDIUMTEXT NULL,
    result_stamp VARCHAR(128) NULL,
    code VARCHAR(128) NULL,
    reserved_at VARCHAR(64) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _017_extraction_settings(cursor: Any) -> None:
    await cursor.execute(extraction_settings_ddl(_dialect(cursor)))


async def _018_model_budget_and_probe(cursor: Any) -> None:
    await cursor.execute(model_request_budget_ddl(_dialect(cursor)))
    await cursor.execute(model_probe_operations_ddl(_dialect(cursor)))


async def _050_model_probe_operations_reserved_at(cursor: Any) -> None:
    """Bound how long a probe reservation can fence out new probes.

    Rows written before this column exist have `reserved_at IS NULL`; they are
    exactly the reservations that could already be stuck, so the expiry logic
    treats NULL as expired on purpose.
    """
    columns = await cursor.columns("model_probe_operations")
    if "reserved_at" in columns:
        return
    if _dialect(cursor) == "sqlite":
        await cursor.execute("ALTER TABLE model_probe_operations ADD COLUMN reserved_at TEXT")
        return
    await cursor.execute(
        "ALTER TABLE model_probe_operations ADD COLUMN reserved_at VARCHAR(64) NULL"
    )


def model_profile_seeds_ddl(dialect: str = "mysql") -> str:
    """Which presets have been seeded, so seeding happens once."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS model_profile_seeds (
    name TEXT PRIMARY KEY,
    seeded_at TEXT NOT NULL
);
"""
    return """
CREATE TABLE IF NOT EXISTS model_profile_seeds (
    name VARCHAR(128) PRIMARY KEY,
    seeded_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def import_job_snapshots_ddl(dialect: str = "mysql") -> str:
    """Bind one immutable source selection to an import job."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS import_job_snapshots (
    job_id TEXT PRIMARY KEY REFERENCES import_jobs(job_id) ON DELETE CASCADE,
    selection_id TEXT NOT NULL
)
"""
    return """
CREATE TABLE IF NOT EXISTS import_job_snapshots (
    job_id VARCHAR(128) PRIMARY KEY,
    selection_id VARCHAR(128) NOT NULL,
    CONSTRAINT fk_job_snapshot_job FOREIGN KEY (job_id)
        REFERENCES import_jobs(job_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _020_model_profile_seeds(cursor: Any) -> None:
    await cursor.execute(model_profile_seeds_ddl(_dialect(cursor)))


async def _022_import_job_snapshots(cursor: Any) -> None:
    await cursor.execute(import_job_snapshots_ddl(_dialect(cursor)))


def source_candidates_ddl(dialect: str = "mysql") -> str:
    """Candidate source roots; `revision > 0` is CHECKed so it can never be 0."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS source_candidates (
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    requested_path TEXT NOT NULL,
    root_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, source_id)
);
"""
    return """
CREATE TABLE IF NOT EXISTS source_candidates (
    user_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    requested_path VARCHAR(4096) NOT NULL,
    root_id VARCHAR(256) NOT NULL,
    revision INT NOT NULL CHECK (revision > 0),
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id, source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def matter_model_cache_ddl(dialect: str = "mysql") -> str:
    """Cached matter model output, keyed by cache key."""
    if dialect == "sqlite":
        return (
            "CREATE TABLE IF NOT EXISTS matter_model_cache "
            "(cache_key TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )
    return """
CREATE TABLE IF NOT EXISTS matter_model_cache (
    cache_key VARCHAR(255) PRIMARY KEY,
    body MEDIUMTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def hy_node_permissions_ddl(dialect: str = "mysql") -> str:
    """HY node attestations, so a granted node stays auditable."""
    if dialect == "sqlite":
        return (
            "CREATE TABLE IF NOT EXISTS hy_node_permissions ("
            "node_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, evidence_json TEXT NOT NULL)"
        )
    return """
CREATE TABLE IF NOT EXISTS hy_node_permissions (
    node_id VARCHAR(128) PRIMARY KEY,
    fingerprint VARCHAR(128) NOT NULL,
    evidence_json MEDIUMTEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _023_source_candidates(cursor: Any) -> None:
    await cursor.execute(source_candidates_ddl(_dialect(cursor)))


async def _024_matter_model_cache(cursor: Any) -> None:
    await cursor.execute(matter_model_cache_ddl(_dialect(cursor)))


async def _026_hy_node_permissions(cursor: Any) -> None:
    # Historical migration retained as a ledger slot. Fresh databases no
    # longer create this retired proof table; migration 51 removes it from
    # databases that ran the original v26 body.
    return None


async def _051_drop_hy_node_permissions(cursor: Any) -> None:
    await cursor.execute("DROP TABLE IF EXISTS hy_node_permissions")


async def _053_memory_write_event_time(cursor: Any) -> None:
    """Add event-time scheduling without re-keying any existing write."""

    dialect = _dialect(cursor)
    columns = await cursor.columns("memory_write_jobs")
    added_event_at = "event_at" not in columns
    if added_event_at:
        await cursor.execute(
            "ALTER TABLE memory_write_jobs ADD COLUMN event_at "
            + ("TEXT" if dialect == "sqlite" else "DATETIME(6) NULL")
        )
    if "time_quality" not in columns:
        await cursor.execute(
            "ALTER TABLE memory_write_jobs ADD COLUMN time_quality "
            + (
                "TEXT NOT NULL DEFAULT 'approximate'"
                if dialect == "sqlite"
                else "VARCHAR(32) NOT NULL DEFAULT 'approximate'"
            )
        )
    await cursor.execute(
        """
        UPDATE memory_write_jobs
        SET event_at = COALESCE(
            (
                SELECT MAX(COALESCE(e.created_at, e.imported_at))
                FROM memory_write_job_evidence AS linked
                JOIN evidence AS e ON e.evidence_id = linked.evidence_id
                WHERE linked.job_id = memory_write_jobs.job_id
                  AND e.role = 'user'
            ),
            (
                SELECT MIN(COALESCE(e.created_at, e.imported_at))
                FROM memory_write_job_evidence AS linked
                JOIN evidence AS e ON e.evidence_id = linked.evidence_id
                WHERE linked.job_id = memory_write_jobs.job_id
            ),
            created_at
        )
        WHERE event_at IS NULL
        """
    )
    if dialect == "sqlite":
        await cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_write_jobs_event_claim "
            "ON memory_write_jobs(state, event_at, queue_seq)"
        )
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_write_schedule_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                window_anchor TEXT NOT NULL,
                window_end TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    else:
        if added_event_at:
            await cursor.execute(
                "CREATE INDEX idx_write_jobs_event_claim "
                "ON memory_write_jobs(state, event_at, queue_seq)"
            )
        await cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_write_schedule_state (
                singleton INT PRIMARY KEY CHECK (singleton = 1),
                window_anchor DATETIME(6) NOT NULL,
                window_end DATETIME(6) NOT NULL,
                updated_at DATETIME(6) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )


async def _054_legacy_possibly_sent_tasks(cursor: Any) -> None:
    """Preserve the old sent marker before removing its retired permission table."""
    columns = await cursor.columns("memory_task_continuous_permissions")
    if "task_id" not in columns:
        return
    await cursor.execute(
        """
        UPDATE memory_tasks
        SET hy_invocation_count=CASE
            WHEN hy_invocation_count<1 THEN 1 ELSE hy_invocation_count END
        WHERE task_id IN (SELECT task_id FROM memory_task_continuous_permissions)
        """
    )
    await cursor.execute("DROP TABLE memory_task_continuous_permissions")


def model_profiles_ddl(dialect: str = "mysql") -> str:
    """Model profiles and their version history.

    The "at most one active profile" rule is expressed differently per engine,
    and that difference is the reason this function exists rather than a single
    string:

    - SQLite has partial indexes, so `UNIQUE(is_active) WHERE is_active = 1`.
    - MySQL does not. But MySQL treats NULLs as distinct in a UNIQUE index, so
      a UNIQUE on a nullable `is_active` column enforces the same rule provided
      inactive rows store NULL rather than 0. The migration therefore declares
      the column so that only the active row holds a non-NULL value; code that
      writes 0 for inactive must write NULL instead.

    Losing this invariant would allow two active profiles, and then "the active
    profile" becomes whichever row a query happens to return first.
    """
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS model_profiles (
    profile_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    base_url TEXT,
    model TEXT NOT NULL,
    embedding_model TEXT,
    embedding_provider_kind TEXT,
    embedding_base_url TEXT,
    embedding_dimensions INTEGER,
    secret_ref TEXT,
    timeout_seconds INTEGER NOT NULL,
    max_concurrency INTEGER NOT NULL,
    context_window INTEGER,
    disable_thinking INTEGER NOT NULL DEFAULT 1,
    endpoint_reachable INTEGER NOT NULL DEFAULT 0,
    model_available INTEGER NOT NULL DEFAULT 0,
    structured_output INTEGER NOT NULL DEFAULT 0,
    embedding_available INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_model_profile
ON model_profiles(is_active) WHERE is_active = 1;
CREATE TABLE IF NOT EXISTS model_profile_versions (
    profile_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    provider_kind TEXT NOT NULL,
    base_url TEXT,
    model TEXT NOT NULL,
    embedding_model TEXT,
    embedding_provider_kind TEXT,
    embedding_base_url TEXT,
    embedding_dimensions INTEGER,
    timeout_seconds INTEGER NOT NULL,
    max_concurrency INTEGER NOT NULL,
    context_window INTEGER,
    disable_thinking INTEGER NOT NULL DEFAULT 0,
    secret_ref TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (profile_id, revision),
    FOREIGN KEY (profile_id) REFERENCES model_profiles(profile_id) ON DELETE CASCADE
);
"""
    return """
CREATE TABLE IF NOT EXISTS model_profiles (
    profile_id VARCHAR(128) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    provider_kind VARCHAR(64) NOT NULL,
    base_url VARCHAR(1024) NULL,
    model VARCHAR(255) NOT NULL,
    embedding_model VARCHAR(255) NULL,
    embedding_provider_kind VARCHAR(64) NULL,
    embedding_base_url VARCHAR(1024) NULL,
    embedding_dimensions INT NULL,
    secret_ref VARCHAR(512) NULL,
    timeout_seconds INT NOT NULL,
    max_concurrency INT NOT NULL,
    context_window INT NULL,
    disable_thinking TINYINT(1) NOT NULL DEFAULT 1,
    endpoint_reachable TINYINT(1) NOT NULL DEFAULT 0,
    model_available TINYINT(1) NOT NULL DEFAULT 0,
    structured_output TINYINT(1) NOT NULL DEFAULT 0,
    embedding_available TINYINT(1) NOT NULL DEFAULT 0,
    revision INT NOT NULL,
    -- NULL means inactive; see the docstring. Only one row may be non-NULL.
    is_active TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    #: Emulates SQLite's partial index `WHERE is_active = 1`.
    #:
    #: MySQL has no partial indexes, and a plain UNIQUE on `is_active` would be
    #: wrong twice over: it would treat every inactive row as colliding (the
    #: code stores 0, not NULL, for inactive), and it would not stop two active
    #: rows once NULLs are involved. A generated column that is NULL unless the
    #: row is active reproduces the partial index exactly, because UNIQUE
    #: ignores NULLs — verified on 8.4.4: three inactive rows insert fine, and
    #: a second active row is rejected.
    active_marker TINYINT(1) AS (IF(is_active = 1, 1, NULL)) VIRTUAL,
    UNIQUE KEY one_active_model_profile (active_marker)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS model_profile_versions (
    profile_id VARCHAR(128) NOT NULL,
    revision INT NOT NULL,
    provider_kind VARCHAR(64) NOT NULL,
    base_url VARCHAR(1024) NULL,
    model VARCHAR(255) NOT NULL,
    embedding_model VARCHAR(255) NULL,
    embedding_provider_kind VARCHAR(64) NULL,
    embedding_base_url VARCHAR(1024) NULL,
    embedding_dimensions INT NULL,
    timeout_seconds INT NOT NULL,
    max_concurrency INT NOT NULL,
    context_window INT NULL,
    disable_thinking TINYINT(1) NOT NULL DEFAULT 0,
    secret_ref VARCHAR(512) NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (profile_id, revision),
    CONSTRAINT fk_profile_versions_profile FOREIGN KEY (profile_id)
        REFERENCES model_profiles(profile_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def profile_creation_operations_ddl(dialect: str = "mysql") -> str:
    """Pending profile-creation operations.

    A row here is a fence, not permission to retry: it exists so that a secret
    written to the OS cannot be orphaned by a half-finished creation. `state`
    and `created_revision` are CHECKed so a row cannot claim a state the rest of
    the code does not handle.
    """
    if dialect == "sqlite":
        return """CREATE TABLE IF NOT EXISTS model_profile_creation_operations (
                    operation_id TEXT NOT NULL PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','committed')),
                    created_revision INTEGER NOT NULL CHECK(created_revision=1)
                )"""
    return """
CREATE TABLE IF NOT EXISTS model_profile_creation_operations (
    operation_id VARCHAR(128) NOT NULL PRIMARY KEY,
    profile_id VARCHAR(128) NOT NULL,
    state VARCHAR(32) NOT NULL CHECK (state IN ('pending','committed')),
    created_revision INT NOT NULL CHECK (created_revision = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def call_events_ddl(dialect: str = "mysql") -> str:
    """Call events and the two indices every read uses."""
    if dialect == "sqlite":
        return """
CREATE TABLE call_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    occurred_at TEXT NOT NULL,
    host_id TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX call_events_time ON call_events(occurred_at DESC, event_id DESC);
CREATE INDEX call_events_host_time ON call_events(host_id, occurred_at DESC, event_id DESC)
"""
    return """
CREATE TABLE IF NOT EXISTS call_events (
    event_id VARCHAR(128) PRIMARY KEY,
    occurred_at DATETIME(6) NOT NULL,
    host_id VARCHAR(128) NULL,
    payload MEDIUMTEXT NOT NULL,
    KEY call_events_time (occurred_at DESC, event_id DESC),
    KEY call_events_host_time (host_id, occurred_at DESC, event_id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _027_model_profiles(cursor: Any) -> None:
    for statement in _split_statements(model_profiles_ddl(_dialect(cursor))):
        await cursor.execute(statement)


async def _029_call_events(cursor: Any) -> None:
    statements = _split_statements(call_events_ddl(_dialect(cursor)))
    if _dialect(cursor) != "sqlite":
        for statement in statements:
            await cursor.execute(statement)
        return

    # `CallEventStore` compares `sqlite_master.sql` byte-for-byte, so the
    # SQLite DDL above deliberately omits `IF NOT EXISTS`. A crash between
    # creating those objects and recording migration 29 would otherwise make
    # every later start fail with "already exists". Mirror the store's own
    # initialize rule: create only missing objects, accept only exact existing
    # definitions, and fail closed on a different object with the same name.
    objects = await cursor.schema_objects("call_events")
    for statement in statements:
        name = _object_name(statement)
        existing = objects.get(name)
        if existing is None:
            await cursor.execute(statement)
        elif existing != statement:
            raise RuntimeError("call_event_schema_mismatch")


def _object_name(statement: str) -> str:
    """The `sqlite_master.name` a CREATE statement will produce."""
    tokens = statement.strip().split()
    if (
        len(tokens) >= 3
        and tokens[0].upper() == "CREATE"
        and tokens[1].upper() in {"TABLE", "INDEX"}
    ):
        return tokens[2]
    raise ValueError("unsupported call_events CREATE statement")


async def _030_profile_creation_operations(cursor: Any) -> None:
    await cursor.execute(profile_creation_operations_ddl(_dialect(cursor)))


def matters_ddl(dialect: str = "mysql") -> str:
    """Matters, their versions, notes, runs, dependencies and generation keys."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS matter_versions(
    matter_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
    dependencies TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(matter_id,version)
);
CREATE TABLE IF NOT EXISTS matters(
    matter_id TEXT PRIMARY KEY, version INTEGER NOT NULL, needs_update INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matter_notes(
    matter_id TEXT NOT NULL, evidence_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matter_runs(
    run_id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matter_memory_dependencies(
    matter_id TEXT NOT NULL, version INTEGER NOT NULL, memory_id TEXT NOT NULL,
    body TEXT NOT NULL, PRIMARY KEY(matter_id, version, memory_id)
);
CREATE TABLE IF NOT EXISTS matter_generation_keys(
    generation_key TEXT PRIMARY KEY, matter_id TEXT NOT NULL
);
"""
    return """
CREATE TABLE IF NOT EXISTS matter_versions (
    matter_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    body MEDIUMTEXT NOT NULL,
    dependencies MEDIUMTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (matter_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS matters (
    matter_id VARCHAR(128) PRIMARY KEY,
    version INT NOT NULL,
    needs_update TINYINT(1) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS matter_notes (
    matter_id VARCHAR(128) NOT NULL,
    evidence_id VARCHAR(128) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_matter_note_evidence (evidence_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS matter_runs (
    run_id VARCHAR(128) PRIMARY KEY,
    body MEDIUMTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS matter_memory_dependencies (
    matter_id VARCHAR(128) NOT NULL,
    version INT NOT NULL,
    memory_id VARCHAR(128) NOT NULL,
    body MEDIUMTEXT NOT NULL,
    PRIMARY KEY (matter_id, version, memory_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS matter_generation_keys (
    generation_key VARCHAR(255) PRIMARY KEY,
    matter_id VARCHAR(128) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _031_matters(cursor: Any) -> None:
    for statement in _split_statements(matters_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def governance_ddl(dialect: str = "mysql") -> str:
    """Governed memories and everything attached to them.

    Two comments carried over because they record design decisions that would
    otherwise be re-litigated:

    - Supersession is a table, not a column, so existing rows are never
      rewritten and one memory can be replaced by several others.
    - `governed_memory_evidence` carries `active` rather than deleting rows,
      because evidence that stops supporting a memory is still part of its
      history.
    """
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS governed_memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    content TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    origin TEXT,
    tags_json TEXT,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    undo_cursor_revision INTEGER
);
CREATE INDEX IF NOT EXISTS governed_memories_list
ON governed_memories(user_id, status, updated_at DESC, memory_id);
CREATE TABLE IF NOT EXISTS governed_memory_versions (
    memory_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
    status TEXT NOT NULL, content TEXT NOT NULL, scopes_json TEXT NOT NULL,
    created_at TEXT NOT NULL, migrated_from TEXT, by_model INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (memory_id, revision),
    FOREIGN KEY (memory_id) REFERENCES governed_memories(memory_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS governed_memory_evidence (
    memory_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (memory_id, evidence_id),
    FOREIGN KEY (memory_id) REFERENCES governed_memories(memory_id) ON DELETE CASCADE,
    FOREIGN KEY (evidence_id) REFERENCES evidence(evidence_id)
);
CREATE INDEX IF NOT EXISTS governed_memory_evidence_active_evidence
ON governed_memory_evidence(active, evidence_id, memory_id);
CREATE TABLE IF NOT EXISTS governed_memory_mutations (
    user_id TEXT NOT NULL, request_id TEXT NOT NULL, memory_id TEXT NOT NULL,
    operation TEXT NOT NULL, result_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (user_id, request_id)
);
CREATE TABLE IF NOT EXISTS memory_deletions (
    memory_id TEXT PRIMARY KEY, status TEXT NOT NULL,
    checkpoint INTEGER NOT NULL DEFAULT 0, runner_token TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, last_error_code TEXT,
    updated_at TEXT NOT NULL, next_attempt_at TEXT,
    FOREIGN KEY (memory_id) REFERENCES governed_memories(memory_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS governed_memory_supersessions (
    memory_id TEXT NOT NULL, superseded_by TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, superseded_by),
    FOREIGN KEY (memory_id) REFERENCES governed_memories(memory_id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by) REFERENCES governed_memories(memory_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS governed_memory_supersessions_head
ON governed_memory_supersessions(superseded_by, memory_id);
CREATE TABLE IF NOT EXISTS memory_index_syncs (
    memory_id TEXT PRIMARY KEY, status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, last_error_code TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (memory_id) REFERENCES governed_memories(memory_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS governed_memories_terms (
    term TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    PRIMARY KEY (term, memory_id),
    FOREIGN KEY (memory_id) REFERENCES governed_memories(memory_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS governed_memories_terms_memory
ON governed_memories_terms(memory_id);
CREATE TABLE IF NOT EXISTS governed_memories_terms_meta (
    memory_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (memory_id) REFERENCES governed_memories(memory_id) ON DELETE CASCADE
);
"""
    return """
CREATE TABLE IF NOT EXISTS governed_memories (
    memory_id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    kind VARCHAR(64) NOT NULL,
    status VARCHAR(64) NOT NULL,
    content MEDIUMTEXT NOT NULL,
    scopes_json MEDIUMTEXT NOT NULL,
    origin VARCHAR(64) NULL,
    tags_json MEDIUMTEXT NULL,
    revision INT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    deleted_at DATETIME(6) NULL,
    undo_cursor_revision INT NULL,
    KEY governed_memories_list (user_id, status, updated_at DESC, memory_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS governed_memory_versions (
    memory_id VARCHAR(128) NOT NULL,
    revision INT NOT NULL,
    action VARCHAR(64) NOT NULL,
    status VARCHAR(64) NOT NULL,
    content MEDIUMTEXT NOT NULL,
    scopes_json MEDIUMTEXT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    migrated_from VARCHAR(128) NULL,
    by_model TINYINT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (memory_id, revision),
    CONSTRAINT fk_gm_versions_memory FOREIGN KEY (memory_id)
        REFERENCES governed_memories(memory_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS governed_memory_evidence (
    memory_id VARCHAR(128) NOT NULL,
    evidence_id VARCHAR(128) NOT NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    PRIMARY KEY (memory_id, evidence_id),
    KEY governed_memory_evidence_active_evidence (active, evidence_id, memory_id),
    CONSTRAINT fk_gm_evidence_memory FOREIGN KEY (memory_id)
        REFERENCES governed_memories(memory_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS governed_memory_mutations (
    user_id VARCHAR(128) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    memory_id VARCHAR(128) NOT NULL,
    operation VARCHAR(64) NOT NULL,
    result_revision INT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id, request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS memory_deletions (
    memory_id VARCHAR(128) PRIMARY KEY,
    status VARCHAR(64) NOT NULL,
    checkpoint INT NOT NULL DEFAULT 0,
    runner_token VARCHAR(128) NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_error_code VARCHAR(128) NULL,
    updated_at DATETIME(6) NOT NULL,
    next_attempt_at DATETIME(6) NULL,
    CONSTRAINT fk_memory_deletions_memory FOREIGN KEY (memory_id)
        REFERENCES governed_memories(memory_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS governed_memory_supersessions (
    memory_id VARCHAR(128) NOT NULL,
    superseded_by VARCHAR(128) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (memory_id, superseded_by),
    KEY governed_memory_supersessions_head (superseded_by, memory_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS memory_index_syncs (
    memory_id VARCHAR(128) PRIMARY KEY,
    status VARCHAR(64) NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_error_code VARCHAR(128) NULL,
    updated_at DATETIME(6) NOT NULL,
    CONSTRAINT fk_memory_index_syncs_memory FOREIGN KEY (memory_id)
        REFERENCES governed_memories(memory_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS governed_memories_terms (
    term VARCHAR(255) NOT NULL,
    memory_id VARCHAR(128) NOT NULL,
    PRIMARY KEY (term, memory_id),
    KEY governed_memories_terms_memory (memory_id),
    CONSTRAINT fk_gm_terms_memory FOREIGN KEY (memory_id)
        REFERENCES governed_memories(memory_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS governed_memories_terms_meta (
    memory_id VARCHAR(128) PRIMARY KEY,
    content_hash VARCHAR(128) NOT NULL,
    CONSTRAINT fk_gm_terms_meta_memory FOREIGN KEY (memory_id)
        REFERENCES governed_memories(memory_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _032_governance(cursor: Any) -> None:
    for statement in _split_statements(governance_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def retrieval_index_ddl(dialect: str = "mysql", *, version: int = 2) -> str:
    """Retrieval index state, generations, consumers, documents and chunks.

    Defaults to **v2**, which is where the store ends up after its own
    `migrate_v2`. Folding the v2 shape in here means a fresh database never has
    to run the create-then-rename dance: that dance exists only to move existing
    rows across, and running it on a new database would create a table and
    immediately drop it.

    v2 differs from v1 by adding `excluded` to the document state set and by
    adding `retrieval_build_maps`. Both differences are gated on `version`:
    `version=1` emits a real v1 baseline (no `retrieval_build_maps`) so
    `index_store.initialize` can create one and `migrate_v2` can upgrade it;
    `version>=2` (the default, and the only value any production caller passes)
    is the folded v2 shape a fresh database is born with.

    The "one active generation" unique index is partial in SQLite; MySQL gets
    the nullable-column equivalent (see `model_profiles_ddl` for the same
    trade-off), so `state` must be NULL rather than a non-active value.
    """
    states = "'pending','leased','ready','blocked','deleted'"
    if version >= 2:
        states = "'pending','leased','ready','blocked','deleted','excluded'"

    # `retrieval_build_maps` is a v2-only table. Emitting it in a v1 baseline
    # would make a v1->v2 migration impossible to observe (the table already
    # exists) and break the explicit-upgrade contract, so it is gated on
    # `version >= 2` in both dialects.
    sqlite_build_maps = (
        """
CREATE TABLE IF NOT EXISTS retrieval_build_maps (
    build_id TEXT PRIMARY KEY,source_map_json TEXT NOT NULL,map_digest TEXT NOT NULL,
    coverage_state TEXT NOT NULL CHECK(coverage_state IN ('indexable','excluded')))
"""
        if version >= 2
        else ""
    )
    mysql_build_maps = (
        """,

CREATE TABLE IF NOT EXISTS retrieval_build_maps (
    build_id VARCHAR(128) PRIMARY KEY,
    source_map_json MEDIUMTEXT NOT NULL,
    map_digest VARCHAR(128) NOT NULL,
    coverage_state VARCHAR(32) NOT NULL
        CHECK (coverage_state IN ('indexable','excluded'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        if version >= 2
        else ""
    )

    if dialect == "sqlite":
        return f"""
CREATE TABLE IF NOT EXISTS retrieval_index_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    schema_version INTEGER NOT NULL,data_store_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS retrieval_index_generations (
    generation_id TEXT PRIMARY KEY,identity_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('building','active','retired')),
    created_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS retrieval_one_active
ON retrieval_index_generations(state) WHERE state='active';
CREATE TABLE IF NOT EXISTS retrieval_consumers (
    generation_id TEXT PRIMARY KEY REFERENCES retrieval_index_generations,
    after_seq INTEGER NOT NULL CHECK(after_seq>=0),
    retry_after_seq INTEGER NOT NULL DEFAULT 0 CHECK(retry_after_seq>=0));
CREATE TABLE IF NOT EXISTS retrieval_documents (
    generation_id TEXT NOT NULL REFERENCES retrieval_index_generations,
    evidence_id TEXT NOT NULL,source_id TEXT NOT NULL,event_seq INTEGER NOT NULL,
    build_id TEXT NOT NULL,state TEXT NOT NULL
    CHECK(state IN ({states})),
    active_build_id TEXT,lease_owner TEXT,lease_token TEXT,lease_until REAL,
    grant_fingerprint TEXT,attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(generation_id,evidence_id));
CREATE TABLE IF NOT EXISTS retrieval_chunks (
    chunk_id TEXT PRIMARY KEY,generation_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,build_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
    start_cp INTEGER NOT NULL,end_cp INTEGER NOT NULL,token_count INTEGER NOT NULL,
    text_hash TEXT NOT NULL,UNIQUE(build_id,ordinal));
CREATE INDEX IF NOT EXISTS retrieval_jobs
ON retrieval_documents(generation_id,state,source_id,event_seq);
{sqlite_build_maps}"""
    return f"""
CREATE TABLE IF NOT EXISTS retrieval_index_state (
    singleton INT PRIMARY KEY CHECK (singleton = 1),
    schema_version INT NOT NULL,
    data_store_id VARCHAR(128) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retrieval_index_generations (
    generation_id VARCHAR(128) PRIMARY KEY,
    identity_json MEDIUMTEXT NOT NULL,
    state VARCHAR(32) NOT NULL CHECK (state IN ('building','active','retired')),
    created_at DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retrieval_consumers (
    generation_id VARCHAR(128) PRIMARY KEY,
    after_seq BIGINT NOT NULL CHECK (after_seq >= 0),
    retry_after_seq BIGINT NOT NULL DEFAULT 0 CHECK (retry_after_seq >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retrieval_documents (
    generation_id VARCHAR(128) NOT NULL,
    evidence_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    event_seq BIGINT NOT NULL,
    build_id VARCHAR(128) NOT NULL,
    state VARCHAR(32) NOT NULL CHECK (state IN ({states})),
    active_build_id VARCHAR(128) NULL,
    lease_owner VARCHAR(128) NULL,
    lease_token VARCHAR(128) NULL,
    lease_until DOUBLE NULL,
    grant_fingerprint VARCHAR(128) NULL,
    attempts INT NOT NULL DEFAULT 0,
    PRIMARY KEY (generation_id, evidence_id),
    KEY retrieval_jobs (generation_id, state, source_id, event_seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retrieval_chunks (
    chunk_id VARCHAR(128) PRIMARY KEY,
    generation_id VARCHAR(128) NOT NULL,
    evidence_id VARCHAR(128) NOT NULL,
    build_id VARCHAR(128) NOT NULL,
    ordinal INT NOT NULL,
    start_cp INT NOT NULL,
    end_cp INT NOT NULL,
    token_count INT NOT NULL,
    text_hash VARCHAR(128) NOT NULL,
    UNIQUE KEY uq_chunk_build_ordinal (build_id, ordinal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4{mysql_build_maps}
"""


async def _033_retrieval_index(cursor: Any) -> None:
    for statement in _split_statements(retrieval_index_ddl(_dialect(cursor))):
        await cursor.execute(statement)


async def _036_retrieval_authority_index(cursor: Any) -> None:
    """Put source_id before event_seq in the retrieval job index.

    The authoritative read filters by generation, state and an explicit
    source allowlist, then orders by event_seq. Without source_id in the key,
    SQLite scans every ready document before applying the allowlist. The
    migration is deliberately an index rebuild: existing SQLite databases
    keep the same table and rows, and the operation only changes query
    planning.
    """
    await cursor.execute("DROP INDEX IF EXISTS retrieval_jobs")
    await cursor.execute(
        "CREATE INDEX IF NOT EXISTS retrieval_jobs "
        "ON retrieval_documents(generation_id,state,source_id,event_seq)"
    )


def unknown_jobs_index_ddl(dialect: str = "mysql") -> str:
    """Index the unknown-job reconcile order.

    `unknown_jobs()` orders by `(updated_at, job_id)` after filtering on
    `state='unknown'`. The existing claim index starts with state but then
    orders by `(priority, created_at)`, so SQLite has to materialize and sort
    every unknown row before applying the limit. This index makes the scan
    ordered from the start, which keeps a periodic reconcile bounded by the
    page it returns rather than by the whole unknown backlog.
    """
    if dialect == "sqlite":
        return (
            "CREATE INDEX IF NOT EXISTS idx_write_jobs_unknown "
            "ON memory_write_jobs(state, updated_at, job_id)"
        )
    return "CREATE INDEX idx_write_jobs_unknown ON memory_write_jobs(state, updated_at, job_id)"


async def _037_unknown_jobs_index(cursor: Any) -> None:
    await cursor.execute(unknown_jobs_index_ddl(_dialect(cursor)))


def memory_task_evidence_index_ddl(dialect: str = "mysql") -> str:
    """Index the evidence-to-latest-task lookup.

    ``task_for_evidence()`` is on the manual-note and evidence-detail paths:
    it filters by ``input_ref`` and orders by ``(created_at, task_id)``. The
    table otherwise has only ``(status, created_at)``, so this query scans
    every task and sorts; adding the compound index gives one indexed lookup.
    """
    if dialect == "sqlite":
        return (
            "CREATE INDEX IF NOT EXISTS memory_task_evidence "
            "ON memory_tasks(input_ref, created_at, task_id)"
        )
    return "CREATE INDEX memory_task_evidence ON memory_tasks(input_ref, created_at, task_id)"


async def _038_memory_task_evidence_index(cursor: Any) -> None:
    await cursor.execute(memory_task_evidence_index_ddl(_dialect(cursor)))


def evidence_reconcile_order_index_ddl(dialect: str = "mysql") -> str:
    """Index the orphan-discovery order used by the reconcile primitive.

    ``DurableWriteQueue.unsubmitted_evidence_ids`` filters on the durable
    reference table and orders by ``(imported_at, evidence_id)`` with **no**
    ``user_id`` predicate, so it cannot use ``evidence_imported_index``, whose
    leading column is ``user_id``. Without this index the discovery page scans
    the whole Evidence table and sorts it through a temp B-tree on every page
    (measured on a real schema: 7.7ms at 50k rows and ~22ms at 200k rows per
    page). The reconcile pass walks the corpus page by page, so the cost grows
    with both corpus size and page count. ``imported_at`` is ``NOT NULL``, so
    the index is a plain two-column order.
    """
    if dialect == "sqlite":
        return (
            "CREATE INDEX IF NOT EXISTS evidence_reconcile_order "
            "ON evidence(imported_at, evidence_id)"
        )
    return "CREATE INDEX evidence_reconcile_order ON evidence(imported_at, evidence_id(128))"


def memory_tasks_unknown_index_ddl(dialect: str = "mysql") -> str:
    """Index the `unknown`-task reconcile order.

    ``MemoryTaskStore.unknown_tasks`` orders by ``(updated_at, task_id)`` after
    filtering on ``status='unknown'``. The only status index is
    ``memory_tasks_status(status, created_at)``, so the order is not covered
    and SQLite materialises and sorts every unknown row. This mirrors
    ``idx_write_jobs_unknown`` (migration 37) for the durable half; without it
    the task half of the same reconcile pass is the slow one.
    """
    if dialect == "sqlite":
        return (
            "CREATE INDEX IF NOT EXISTS idx_memory_tasks_unknown "
            "ON memory_tasks(status, updated_at, task_id)"
        )
    return "CREATE INDEX idx_memory_tasks_unknown ON memory_tasks(status, updated_at, task_id)"


async def _042_reconcile_order_indexes(cursor: Any) -> None:
    await cursor.execute(evidence_reconcile_order_index_ddl(_dialect(cursor)))
    await cursor.execute(memory_tasks_unknown_index_ddl(_dialect(cursor)))


async def _043_memory_source_filter_index(cursor: Any) -> None:
    if _dialect(cursor) == "sqlite":
        await cursor.execute(
            "CREATE INDEX IF NOT EXISTS governed_memory_evidence_active_evidence "
            "ON governed_memory_evidence(active, evidence_id, memory_id)"
        )
        return
    await cursor.execute(
        "CREATE INDEX governed_memory_evidence_active_evidence "
        "ON governed_memory_evidence(active, evidence_id, memory_id)"
    )


async def _044_memory_task_hy_invocations(cursor: Any) -> None:
    column = (
        "INTEGER NOT NULL DEFAULT 0" if _dialect(cursor) == "sqlite" else "INT NOT NULL DEFAULT 0"
    )
    await cursor.execute(f"ALTER TABLE memory_tasks ADD COLUMN hy_invocation_count {column}")


async def _045_pending_dispatch_priority(cursor: Any) -> None:
    columns = await cursor.columns("evidence_pending_memory_dispatch")
    if "priority" in columns:
        return
    column = (
        "TEXT NOT NULL DEFAULT 'normal'"
        if _dialect(cursor) == "sqlite"
        else "VARCHAR(16) NOT NULL DEFAULT 'normal'"
    )
    await cursor.execute(
        f"ALTER TABLE evidence_pending_memory_dispatch ADD COLUMN priority {column}"
    )


async def _046_clean_codex_internal_context(cursor: Any) -> None:
    """Remove previously imported Codex continuation envelopes from product data.

    Each statement is independently idempotent because the SQLite migration
    store commits one statement at a time. A crash before the ledger write can
    therefore replay this repair without adding another memory revision.
    """
    if _dialect(cursor) != "sqlite":
        return
    statements = (
        """
        UPDATE governed_memory_evidence SET active=0
        WHERE active=1 AND evidence_id IN (
          SELECT evidence_id FROM evidence
          WHERE source_id='codex'
            AND lower(ltrim(verbatim)) LIKE '<codex_internal_context%'
        )
        """,
        """
        INSERT INTO governed_memory_versions (
          memory_id,revision,action,status,content,scopes_json,created_at,migrated_from,by_model
        )
        SELECT memory_id,revision+1,'cleanup_internal_context','deleted',
          '[已清理：该记忆包含 Codex 内部续跑提示]',scopes_json,CURRENT_TIMESTAMP,NULL,0
        FROM governed_memories AS memory
        WHERE lower(content) LIKE '%<codex_internal_context%'
          AND NOT EXISTS (
            SELECT 1 FROM governed_memory_versions AS version
            WHERE version.memory_id=memory.memory_id
              AND version.action='cleanup_internal_context'
          )
        """,
        """
        UPDATE governed_memories
        SET status='deleted',
            content='[已清理：该记忆包含 Codex 内部续跑提示]',
            revision=(
              SELECT max(revision) FROM governed_memory_versions AS version
              WHERE version.memory_id=governed_memories.memory_id
                AND version.action='cleanup_internal_context'
            ),
            updated_at=CURRENT_TIMESTAMP,
            deleted_at=CURRENT_TIMESTAMP,
            undo_cursor_revision=NULL
        WHERE EXISTS (
          SELECT 1 FROM governed_memory_versions AS version
          WHERE version.memory_id=governed_memories.memory_id
            AND version.action='cleanup_internal_context'
        ) AND status!='deleted'
        """,
        """
        UPDATE governed_memory_versions
        SET content='[已清理：该记忆包含 Codex 内部续跑提示]'
        WHERE lower(content) LIKE '%<codex_internal_context%'
        """,
        """
        DELETE FROM governed_memories_terms
        WHERE memory_id IN (
          SELECT memory_id FROM governed_memory_versions
          WHERE action='cleanup_internal_context'
        )
        """,
        """
        DELETE FROM governed_memories_terms_meta
        WHERE memory_id IN (
          SELECT memory_id FROM governed_memory_versions
          WHERE action='cleanup_internal_context'
        )
        """,
        """
        UPDATE evidence_versions
        SET role='system',written_by='agent',
            verbatim='[已清理的 Codex 内部续跑提示]',
            content_hash='aa5f8a0411d9c5a64f6b4542ab6120460698c31db9b6b5c025d0c6b684988e99'
        WHERE evidence_id IN (
          SELECT evidence_id FROM evidence
          WHERE source_id='codex'
            AND lower(ltrim(verbatim)) LIKE '<codex_internal_context%'
        )
        """,
        """
        UPDATE evidence
        SET role='system',written_by='agent',
            verbatim='[已清理的 Codex 内部续跑提示]',
            content_hash='aa5f8a0411d9c5a64f6b4542ab6120460698c31db9b6b5c025d0c6b684988e99',
            updated_at=CURRENT_TIMESTAMP
        WHERE source_id='codex'
          AND lower(ltrim(verbatim)) LIKE '<codex_internal_context%'
        """,
    )
    for statement in statements:
        await cursor.execute(statement)


def _internal_envelope_sql(column: str) -> str:
    patterns = (
        "%<heartbeat%",
        "%&lt;heartbeat%",
        "%<codex_internal_context%",
        "%&lt;codex_internal_context%",
        "%<environment_context%",
        "%&lt;environment_context%",
        "%<permissions instructions%",
        "%&lt;permissions instructions%",
        "%<system-reminder%",
        "%&lt;system-reminder%",
    )
    return "(" + " OR ".join(f"lower({column}) LIKE '{pattern}'" for pattern in patterns) + ")"


async def _047_clean_codex_harness_envelopes(cursor: Any) -> None:
    """Clean legacy harness envelopes from every Codex-authored role."""
    if _dialect(cursor) != "sqlite":
        return
    evidence_match = _internal_envelope_sql("verbatim")
    memory_match = _internal_envelope_sql("content")
    statements = (
        "UPDATE governed_memory_evidence SET active=0 WHERE active=1 AND evidence_id IN "
        f"(SELECT evidence_id FROM evidence WHERE source_id='codex' AND {evidence_match})",
        "INSERT INTO governed_memory_versions (memory_id,revision,action,status,content,"
        "scopes_json,created_at,migrated_from,by_model) "
        "SELECT memory_id,revision+1,'cleanup_internal_context','deleted',"
        "'[已清理：该记忆包含 Codex 内部提示]',scopes_json,CURRENT_TIMESTAMP,NULL,0 "
        "FROM governed_memories AS memory WHERE "
        f"{memory_match} AND NOT EXISTS (SELECT 1 FROM governed_memory_versions AS version "
        "WHERE version.memory_id=memory.memory_id "
        "AND version.action='cleanup_internal_context')",
        "UPDATE governed_memories SET status='deleted',"
        "content='[已清理：该记忆包含 Codex 内部提示]',"
        "revision=(SELECT max(revision) FROM governed_memory_versions AS version "
        "WHERE version.memory_id=governed_memories.memory_id "
        "AND version.action='cleanup_internal_context'),updated_at=CURRENT_TIMESTAMP,"
        "deleted_at=CURRENT_TIMESTAMP,undo_cursor_revision=NULL "
        "WHERE EXISTS (SELECT 1 FROM governed_memory_versions AS version "
        "WHERE version.memory_id=governed_memories.memory_id "
        "AND version.action='cleanup_internal_context') AND status!='deleted'",
        "UPDATE governed_memory_versions SET content='[已清理：该记忆包含 Codex 内部提示]' "
        f"WHERE {memory_match}",
        "DELETE FROM governed_memories_terms WHERE memory_id IN "
        "(SELECT memory_id FROM governed_memory_versions "
        "WHERE action='cleanup_internal_context')",
        "DELETE FROM governed_memories_terms_meta WHERE memory_id IN "
        "(SELECT memory_id FROM governed_memory_versions "
        "WHERE action='cleanup_internal_context')",
        "UPDATE evidence_versions SET role='system',written_by='agent',"
        "verbatim='[已清理的 Codex 内部提示]',"
        "content_hash='e455dd7c10b80517e6e345aeca2620083106479d3e8752432fa287036b24c3bb' "
        "WHERE evidence_id IN (SELECT evidence_id FROM evidence "
        f"WHERE source_id='codex' AND {evidence_match})",
        "UPDATE evidence SET role='system',written_by='agent',"
        "verbatim='[已清理的 Codex 内部提示]',"
        "content_hash='e455dd7c10b80517e6e345aeca2620083106479d3e8752432fa287036b24c3bb',"
        f"updated_at=CURRENT_TIMESTAMP WHERE source_id='codex' AND {evidence_match}",
    )
    for statement in statements:
        await cursor.execute(statement)


def tail_handoff_ddl(dialect: str = "mysql") -> str:
    """Durable handoff rows for optional post-response metadata."""
    if dialect == "sqlite":
        return """
CREATE TABLE tail_handoff (
    event_id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(length(CAST(payload_json AS BLOB)) <= 16384),
    state TEXT NOT NULL CHECK(state IN ('pending','running','completed','failed','unknown')),
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX tail_handoff_claim ON tail_handoff(state, created_at, event_id);
CREATE INDEX tail_handoff_lease ON tail_handoff(lease_expires_at, event_id);
"""
    return """
CREATE TABLE IF NOT EXISTS tail_handoff (
    event_id VARCHAR(128) PRIMARY KEY,
    created_at DATETIME(6) NOT NULL,
    payload_json MEDIUMTEXT NOT NULL CHECK(OCTET_LENGTH(payload_json) <= 16384),
    state VARCHAR(32) NOT NULL CHECK(state IN ('pending','running','completed','failed','unknown')),
    attempts INT NOT NULL DEFAULT 0,
    lease_owner VARCHAR(128) NULL,
    lease_expires_at DATETIME(6) NULL,
    last_error VARCHAR(255) NULL,
    updated_at DATETIME(6) NOT NULL,
    KEY tail_handoff_claim (state, created_at, event_id),
    KEY tail_handoff_lease (lease_expires_at, event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _040_tail_handoff(cursor: Any) -> None:
    for statement in _split_statements(tail_handoff_ddl(_dialect(cursor))):
        await cursor.execute(statement)


async def _041_tail_handoff_lease_token(cursor: Any) -> None:
    """Add a monotonic lease token so an old owner cannot settle recomputed work."""
    columns = await cursor.columns("tail_handoff")
    if "lease_token" in columns:
        return
    if _dialect(cursor) == "sqlite":
        await cursor.execute("ALTER TABLE tail_handoff ADD COLUMN lease_token TEXT")
        return
    await cursor.execute("ALTER TABLE tail_handoff ADD COLUMN lease_token VARCHAR(128) NULL")


async def _049_cursor_transcript_time_backfill(cursor: Any) -> None:
    """Re-read Cursor history once when older imports have no conversation time."""
    await cursor.execute(
        """
        DELETE FROM source_cursors
        WHERE source_id = 'cursor'
          AND EXISTS (
              SELECT 1 FROM evidence
              WHERE evidence.user_id = source_cursors.user_id
                AND evidence.source_id = source_cursors.source_id
                AND evidence.created_at IS NULL
          )
        """
    )


def evidence_ingest_seq_ddl(dialect: str = "mysql") -> str:
    """An explicit monotonic ordering column for `evidence`.

    SQLite's `rowid` gives insertion order for free and MySQL has no
    equivalent. `matters/selection.py` relies on it to keep a conversation's
    turns in the order they were imported, and neither `imported_at` (coarse;
    many rows share a timestamp during a bulk import) nor `evidence_id` (a
    hash, unordered) can replace it — verified by trying, which broke nine
    ordering tests.

    This column exists so that reliance can end: it is filled from rowid on
    SQLite and auto-increments on MySQL, so ordering code can name a real
    column in both dialects.
    """
    if dialect == "sqlite":
        return "ALTER TABLE evidence ADD COLUMN ingest_seq INTEGER"
    return """
ALTER TABLE evidence
    ADD COLUMN ingest_seq BIGINT NOT NULL AUTO_INCREMENT UNIQUE
"""


def backfill_ingest_seq_sql(dialect: str = "mysql") -> str:
    """Populate `ingest_seq` for rows written before the column existed."""
    if dialect == "sqlite":
        # rowid is the insertion order this column replaces, so it is the
        # correct source for the backfill.
        return "UPDATE evidence SET ingest_seq = rowid WHERE ingest_seq IS NULL"
    # On MySQL the column is added as AUTO_INCREMENT, so existing rows are
    # numbered automatically by the ALTER. Nothing to backfill.
    return "SELECT 1"


async def _034_evidence_ingest_seq(cursor: Any) -> None:
    if _dialect(cursor) == "sqlite":
        # Migration 8 uses `CREATE TABLE IF NOT EXISTS`, so an older
        # store-created `evidence` table survives that step unchanged and may
        # not have `ingest_seq` at all. Adding the column is conditional:
        # SQLite has no `ADD COLUMN IF NOT EXISTS`, and a fresh database
        # already declares it. The backfill then fills only NULL rows.
        columns = await cursor.columns("evidence")
        if "ingest_seq" not in columns:
            await cursor.execute(evidence_ingest_seq_ddl("sqlite"))
        await cursor.execute(backfill_ingest_seq_sql("sqlite"))
        return
    await cursor.execute(evidence_ingest_seq_ddl("mysql"))
    await cursor.execute(backfill_ingest_seq_sql("mysql"))


def scanner_state_ddl(dialect: str = "mysql") -> str:
    """The scanner's own state tables: files, sessions, ingest cursors, runs.

    A4-03 names four tables; `source_file_cursors` already ships in migration 3
    and is deliberately not redefined here, because a second `CREATE TABLE` for
    the same name is how two definitions drift apart unnoticed.

    `source_files` carries `file_key` as UNIQUE even though the architecture
    lists only `root_id`/`relative_path`. The reason is the metadata gate: the
    scanner looks a whole page's cursors up in one query keyed by `file_key`,
    and `source_file_cursors` is keyed the same way. Without a `file_key` on
    this table, joining a page back to its file row costs one query per file,
    which is exactly the N+1 `get_many` exists to avoid.

    `scan_runs.discovery_cursor` is stored, not just held in memory, so a crash
    mid-scan resumes from the last key instead of re-enumerating from the top.

    **Index lengths are capped, and every number here was found by running real
    MySQL 8.4.4, not by reading the docs.** InnoDB limits an index key to 3072
    bytes and `utf8mb4` costs 4 bytes per character:

    - `file_key VARCHAR(512)` alone = 2048 bytes, fits.
    - `root_id VARCHAR(128)` = 512 bytes, so the path prefix has at most
      (3072 − 512) / 4 = **640 characters**. A prefix of 700 was tried first and
      failed with error 1071 (3312 bytes).

    A prefix index is still unique in the safe direction: two paths differing
    only past 640 characters are *rejected* as duplicates rather than silently
    admitted. Real paths are far shorter; the alternative — no uniqueness on
    (root, path) at all — would let the same file be recorded twice.
    """
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS source_files (
    file_id TEXT PRIMARY KEY,
    file_key TEXT NOT NULL UNIQUE,
    root_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    filesystem_identity TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    fingerprint_state TEXT NOT NULL DEFAULT 'unfingerprinted',
    full_sha256 TEXT,
    generation INTEGER NOT NULL DEFAULT 0,
    parser_version TEXT,
    UNIQUE (root_id, relative_path)
);
CREATE TABLE IF NOT EXISTS source_sessions (
    internal_session_id TEXT PRIMARY KEY,
    harness_id TEXT NOT NULL,
    external_session_id_hmac TEXT NOT NULL,
    display_key TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (harness_id, external_session_id_hmac)
);
CREATE TABLE IF NOT EXISTS session_ingest_cursors (
    internal_session_id TEXT PRIMARY KEY,
    last_parsed_message_idx INTEGER NOT NULL DEFAULT 0,
    last_committed_message_idx INTEGER NOT NULL DEFAULT 0,
    last_committed_user_idx INTEGER NOT NULL DEFAULT 0,
    last_committed_user_hmac TEXT,
    pending_round_count INTEGER NOT NULL DEFAULT 0,
    pending_since_scan_id TEXT,
    last_write_request_id TEXT
);
CREATE TABLE IF NOT EXISTS scan_runs (
    scan_id TEXT PRIMARY KEY,
    root_id TEXT NOT NULL,
    status TEXT NOT NULL,
    discovery_cursor TEXT,
    counts TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
)
"""
    return """
CREATE TABLE IF NOT EXISTS source_files (
    file_id VARCHAR(128) PRIMARY KEY,
    file_key VARCHAR(512) NOT NULL,
    root_id VARCHAR(128) NOT NULL,
    relative_path VARCHAR(1024) NOT NULL,
    filesystem_identity VARCHAR(256) NOT NULL,
    size BIGINT NOT NULL DEFAULT 0,
    mtime_ns BIGINT NOT NULL DEFAULT 0,
    fingerprint_state VARCHAR(32) NOT NULL DEFAULT 'unfingerprinted',
    full_sha256 VARCHAR(128) NULL,
    generation INT NOT NULL DEFAULT 0,
    parser_version VARCHAR(64) NULL,
    UNIQUE KEY uq_source_files_file_key (file_key),
    UNIQUE KEY uq_source_files_path (root_id, relative_path(640))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS source_sessions (
    internal_session_id VARCHAR(128) PRIMARY KEY,
    harness_id VARCHAR(128) NOT NULL,
    external_session_id_hmac VARCHAR(128) NOT NULL,
    display_key VARCHAR(512) NULL,
    first_seen_at DATETIME(6) NOT NULL,
    last_seen_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_source_sessions_identity (harness_id, external_session_id_hmac)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS session_ingest_cursors (
    internal_session_id VARCHAR(128) PRIMARY KEY,
    last_parsed_message_idx BIGINT NOT NULL DEFAULT 0,
    last_committed_message_idx BIGINT NOT NULL DEFAULT 0,
    last_committed_user_idx BIGINT NOT NULL DEFAULT 0,
    last_committed_user_hmac VARCHAR(128) NULL,
    pending_round_count INT NOT NULL DEFAULT 0,
    pending_since_scan_id VARCHAR(128) NULL,
    last_write_request_id VARCHAR(128) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS scan_runs (
    scan_id VARCHAR(128) PRIMARY KEY,
    root_id VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    discovery_cursor JSON NULL,
    counts JSON NULL,
    started_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    KEY scan_runs_root_started (root_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _035_scanner_state(cursor: Any) -> None:
    for statement in _split_statements(scanner_state_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def sdk_data_plane_migration_ddl(dialect: str = "mysql") -> str:
    """Management-only ledger for the one-time SDK-authority cutover."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS sdk_data_plane_migrations (
    migration_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    phase TEXT NOT NULL,
    source_runtime_build TEXT NOT NULL,
    target_sdk_version TEXT NOT NULL,
    source_schema_version INTEGER NOT NULL,
    namespace_fingerprint TEXT NOT NULL,
    backend_fingerprint TEXT NOT NULL,
    backup_bundle_id TEXT,
    reconciliation_digest TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cutover_at TEXT
);
CREATE TABLE IF NOT EXISTS sdk_data_plane_migration_items (
    migration_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision TEXT,
    source_hash TEXT NOT NULL,
    target_memory_id TEXT,
    target_hash TEXT,
    operation_key TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (migration_id, item_key),
    UNIQUE (migration_id, operation_key)
);
CREATE INDEX IF NOT EXISTS sdk_migration_items_state
ON sdk_data_plane_migration_items(migration_id, state, item_key);
CREATE TABLE IF NOT EXISTS memory_id_aliases (
    legacy_memory_id TEXT PRIMARY KEY,
    canonical_memory_id TEXT NOT NULL,
    migration_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_id_aliases_canonical
ON memory_id_aliases(canonical_memory_id);
CREATE TABLE IF NOT EXISTS sdk_data_plane_cutover (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    migration_id TEXT NOT NULL,
    sdk_version TEXT NOT NULL,
    store_version TEXT NOT NULL,
    namespace_fingerprint TEXT NOT NULL,
    backend_fingerprint TEXT NOT NULL,
    backup_bundle_id TEXT NOT NULL,
    reconciliation_digest TEXT NOT NULL,
    completed_at TEXT NOT NULL
)
"""
    return """
CREATE TABLE IF NOT EXISTS sdk_data_plane_migrations (
    migration_id VARCHAR(128) PRIMARY KEY,
    state VARCHAR(32) NOT NULL,
    phase VARCHAR(64) NOT NULL,
    source_runtime_build VARCHAR(128) NOT NULL,
    target_sdk_version VARCHAR(64) NOT NULL,
    source_schema_version INT NOT NULL,
    namespace_fingerprint VARCHAR(128) NOT NULL,
    backend_fingerprint VARCHAR(128) NOT NULL,
    backup_bundle_id VARCHAR(128) NULL,
    reconciliation_digest VARCHAR(128) NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    cutover_at VARCHAR(64) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS sdk_data_plane_migration_items (
    migration_id VARCHAR(128) NOT NULL,
    item_key VARCHAR(384) NOT NULL,
    source_kind VARCHAR(64) NOT NULL,
    source_id VARCHAR(256) NOT NULL,
    source_revision VARCHAR(128) NULL,
    source_hash VARCHAR(128) NOT NULL,
    target_memory_id VARCHAR(256) NULL,
    target_hash VARCHAR(128) NULL,
    operation_key VARCHAR(256) NOT NULL,
    state VARCHAR(32) NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    error_code VARCHAR(128) NULL,
    updated_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (migration_id, item_key),
    UNIQUE KEY sdk_migration_item_operation (migration_id, operation_key),
    KEY sdk_migration_items_state (migration_id, state, item_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS memory_id_aliases (
    legacy_memory_id VARCHAR(256) PRIMARY KEY,
    canonical_memory_id VARCHAR(256) NOT NULL,
    migration_id VARCHAR(128) NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    KEY memory_id_aliases_canonical (canonical_memory_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS sdk_data_plane_cutover (
    singleton TINYINT PRIMARY KEY,
    migration_id VARCHAR(128) NOT NULL,
    sdk_version VARCHAR(64) NOT NULL,
    store_version VARCHAR(64) NOT NULL,
    namespace_fingerprint VARCHAR(128) NOT NULL,
    backend_fingerprint VARCHAR(128) NOT NULL,
    backup_bundle_id VARCHAR(128) NOT NULL,
    reconciliation_digest VARCHAR(128) NOT NULL,
    completed_at VARCHAR(64) NOT NULL,
    CONSTRAINT sdk_data_plane_cutover_singleton CHECK (singleton = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _055_sdk_data_plane_migration(cursor: Any) -> None:
    for statement in _split_statements(sdk_data_plane_migration_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def memory_task_sdk_receipts_ddl(dialect: str = "mysql") -> str:
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS memory_task_sdk_receipts (
    task_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    raw_ids_json TEXT NOT NULL,
    memory_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""
    return """
CREATE TABLE IF NOT EXISTS memory_task_sdk_receipts (
    task_id VARCHAR(128) PRIMARY KEY,
    request_id VARCHAR(256) NOT NULL,
    raw_ids_json JSON NOT NULL,
    memory_ids_json JSON NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    UNIQUE KEY memory_task_sdk_receipt_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def sdk_write_receipts_ddl(dialect: str = "mysql") -> str:
    """SDK ids returned after one normal add(); pending jobs keep these empty."""
    if dialect == "sqlite":
        return """
ALTER TABLE memory_write_jobs
ADD COLUMN sdk_raw_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE memory_write_jobs
ADD COLUMN sdk_memory_ids_json TEXT NOT NULL DEFAULT '[]';
""" + memory_task_sdk_receipts_ddl("sqlite")
    return """
ALTER TABLE memory_write_jobs
ADD COLUMN sdk_raw_ids_json JSON NOT NULL DEFAULT ('[]');
ALTER TABLE memory_write_jobs
ADD COLUMN sdk_memory_ids_json JSON NOT NULL DEFAULT ('[]');
""" + memory_task_sdk_receipts_ddl("mysql")


async def _056_sdk_write_receipts(cursor: Any) -> None:
    for statement in _split_statements(sdk_write_receipts_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def sdk_matter_references_ddl(dialect: str = "mysql") -> str:
    """Reference-only Matter projection for SDK authority."""
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS raw_id_aliases (
    legacy_evidence_id TEXT NOT NULL,
    item_index INTEGER NOT NULL DEFAULT 0,
    canonical_raw_id TEXT NOT NULL,
    migration_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (legacy_evidence_id, item_index)
);
CREATE INDEX IF NOT EXISTS raw_id_aliases_canonical
ON raw_id_aliases(canonical_raw_id);
CREATE TABLE IF NOT EXISTS matter_sdk_versions (
    matter_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    references_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (matter_id, version)
)
"""
    return """
CREATE TABLE IF NOT EXISTS raw_id_aliases (
    legacy_evidence_id VARCHAR(256) NOT NULL,
    item_index INT NOT NULL DEFAULT 0,
    canonical_raw_id VARCHAR(256) NOT NULL,
    migration_id VARCHAR(128) NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (legacy_evidence_id, item_index),
    KEY raw_id_aliases_canonical (canonical_raw_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS matter_sdk_versions (
    matter_id VARCHAR(256) NOT NULL,
    version INT NOT NULL,
    references_json JSON NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (matter_id, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _057_sdk_matter_references(cursor: Any) -> None:
    for statement in _split_statements(sdk_matter_references_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def sdk_matter_notes_ddl(dialect: str = "mysql") -> str:
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS matter_sdk_notes (
    matter_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    raw_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (matter_id, note_id)
)
"""
    return """
CREATE TABLE IF NOT EXISTS matter_sdk_notes (
    matter_id VARCHAR(256) NOT NULL,
    note_id VARCHAR(256) NOT NULL,
    request_id VARCHAR(256) NOT NULL,
    raw_id VARCHAR(256) NULL,
    created_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (matter_id, note_id),
    UNIQUE KEY matter_sdk_note_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


async def _058_sdk_matter_notes(cursor: Any) -> None:
    for statement in _split_statements(sdk_matter_notes_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def memory_authority_state_ddl(dialect: str = "mysql") -> str:
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS memory_authority_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    authority TEXT NOT NULL CHECK (authority IN ('legacy','sdk')),
    migration_id TEXT,
    updated_at TEXT NOT NULL
);
INSERT INTO memory_authority_state(singleton,authority,migration_id,updated_at)
VALUES (1,'legacy',NULL,CURRENT_TIMESTAMP)
ON CONFLICT(singleton) DO NOTHING
"""
    return """
CREATE TABLE IF NOT EXISTS memory_authority_state (
    singleton TINYINT PRIMARY KEY,
    authority VARCHAR(16) NOT NULL,
    migration_id VARCHAR(128) NULL,
    updated_at VARCHAR(64) NOT NULL,
    CONSTRAINT memory_authority_state_singleton CHECK (singleton = 1),
    CONSTRAINT memory_authority_state_value
        CHECK (authority IN ('legacy','sdk'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO memory_authority_state(singleton,authority,migration_id,updated_at)
VALUES (1,'legacy',NULL,CURRENT_TIMESTAMP)
ON DUPLICATE KEY UPDATE singleton=singleton
"""


async def _059_memory_authority_state(cursor: Any) -> None:
    for statement in _split_statements(memory_authority_state_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def memory_note_requests_ddl(dialect: str = "mysql") -> str:
    if dialect == "sqlite":
        return """
CREATE TABLE IF NOT EXISTS memory_note_requests (
    note_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)
"""
    return """
CREATE TABLE IF NOT EXISTS memory_note_requests (
    note_id VARCHAR(256) PRIMARY KEY,
    request_id VARCHAR(256) NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    UNIQUE KEY memory_note_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def fresh_management_ddl() -> str:
    """Minimal root schema for a fresh SDK-authority installation."""
    return """
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE memory_authority_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    authority TEXT NOT NULL CHECK (authority IN ('legacy','sdk')),
    migration_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE memory_note_requests (
    note_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)
"""


async def _060_memory_note_requests(cursor: Any) -> None:
    for statement in _split_statements(memory_note_requests_ddl(_dialect(cursor))):
        await cursor.execute(statement)


def _split_statements(sql: str) -> list[str]:
    """Split a multi-statement script, dropping blanks, PRAGMA and comments.

    `PRAGMA` is SQLite connection state, not schema, so it is not part of a
    migration; MySQL enables foreign keys by declaring constraints instead.

    `--` line comments are stripped, and this was found by running real MySQL:
    `model_profiles` carried an explanatory `--` comment inside a `CREATE TABLE`,
    and splitting on `;` alone produced a fragment starting with the comment
    text, which MySQL rejected with error 1064.

    The scan is quote-aware: a `;` or `--` inside a `'...'`/`"..."`/`` `...` ``
    literal is content, not a separator or a comment. A naive `str.split(";")`
    cuts `DEFAULT 'a;b'` in half, and stripping `--` first would silently delete
    everything after it in a string. Both would turn one statement into garbage
    the engine rejects far from the migration that caused it.

    Comments are dropped rather than preserved because the statement is sent
    to the server, and a leading `--` comment makes the rest of that line
    invisible — which silently swallowed the first real column in one case.
    """
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    index = 0
    length = len(sql)
    while index < length:
        character = sql[index]
        if quote is not None:
            current.append(character)
            if character == quote:
                quote = None
            index += 1
            continue
        if character in ("'", '"', "`"):
            quote = character
            current.append(character)
            index += 1
            continue
        if character == "-" and index + 1 < length and sql[index + 1] == "-":
            # Line comment: skip to the end of the line, keep the newline so
            # tokens on either side do not run together.
            newline = sql.find("\n", index)
            if newline == -1:
                break
            index = newline
            continue
        if character == ";":
            statement = "".join(current).strip()
            if statement and not statement.upper().startswith("PRAGMA"):
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(character)
        index += 1
    statement = "".join(current).strip()
    if statement and not statement.upper().startswith("PRAGMA"):
        statements.append(statement)
    return statements


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "schema_migrations", _001_schema_table, ("schema_migrations",)),
    Migration(
        2,
        "memory_write_jobs",
        _002_write_jobs,
        (
            "memory_write_jobs",
            "memory_write_job_evidence",
            "memory_write_schedule_state",
        ),
    ),
    Migration(3, "source_file_cursors", _003_scanner_cursors, ("source_file_cursors",)),
    Migration(4, "change_outbox", _004_change_outbox, ("change_outbox",)),
    Migration(5, "session_leases", _005_session_leases, ("session_leases",)),
    Migration(6, "trusted_sessions", _006_trusted_sessions, ("trusted_sessions",)),
    Migration(
        7,
        "import_jobs",
        _007_import_jobs,
        (
            "import_jobs",
            "import_issues",
            "import_skip_reason_counts",
            "source_cursors",
            "live_source_cursors",
        ),
    ),
    Migration(
        8,
        "evidence",
        _008_evidence,
        (
            "evidence",
            "evidence_repairs",
            "evidence_origins",
            "evidence_versions",
            "evidence_pending_memory_dispatch",
        ),
    ),
    Migration(9, "source_grants", _009_source_grants, ("source_grants",)),
    Migration(
        10,
        "connection_intents",
        _010_connection_intents,
        ("source_connection_intents",),
    ),
    Migration(
        11,
        "session_inbox",
        _011_session_inbox,
        (
            "session_subscriptions",
            "session_inbox_cursors",
            "session_inbox_batches",
            "session_snapshots",
        ),
    ),
    Migration(12, "onboarding_state", _012_onboarding_state, ("onboarding_state",)),
    Migration(
        14,
        "memory_tasks",
        _014_memory_tasks,
        (
            "memory_tasks",
            "memory_task_extractions",
            "memory_task_inputs",
            "memory_task_outputs",
            "memory_task_contexts",
        ),
    ),
    Migration(
        16,
        "ingest_journal",
        _016_ingest_journal,
        ("evidence_ingest_events", "evidence_ingest_journal_state"),
    ),
    Migration(17, "extraction_settings", _017_extraction_settings, ("extraction_settings",)),
    Migration(
        18,
        "model_budget_and_probe",
        _018_model_budget_and_probe,
        ("model_request_budget_events", "model_probe_operations"),
    ),
    Migration(20, "model_profile_seeds", _020_model_profile_seeds, ("model_profile_seeds",)),
    Migration(
        22,
        "import_job_snapshots",
        _022_import_job_snapshots,
        ("import_job_snapshots",),
    ),
    Migration(23, "source_candidates", _023_source_candidates, ("source_candidates",)),
    Migration(24, "matter_model_cache", _024_matter_model_cache, ("matter_model_cache",)),
    Migration(26, "hy_node_permissions", _026_hy_node_permissions),
    Migration(
        27,
        "model_profiles",
        _027_model_profiles,
        ("model_profiles", "model_profile_versions"),
    ),
    Migration(29, "call_events", _029_call_events, ("call_events",)),
    Migration(
        30,
        "profile_creation_operations",
        _030_profile_creation_operations,
        ("model_profile_creation_operations",),
    ),
    Migration(
        31,
        "matters",
        _031_matters,
        (
            "matter_versions",
            "matters",
            "matter_notes",
            "matter_runs",
            "matter_memory_dependencies",
            "matter_generation_keys",
        ),
    ),
    Migration(
        32,
        "governance",
        _032_governance,
        (
            "governed_memories",
            "governed_memory_versions",
            "governed_memory_evidence",
            "governed_memory_mutations",
            "memory_deletions",
            "governed_memory_supersessions",
            "memory_index_syncs",
            "governed_memories_terms",
            "governed_memories_terms_meta",
        ),
    ),
    Migration(
        33,
        "retrieval_index",
        _033_retrieval_index,
        (
            "retrieval_index_state",
            "retrieval_index_generations",
            "retrieval_consumers",
            "retrieval_documents",
            "retrieval_chunks",
            "retrieval_build_maps",
        ),
    ),
    Migration(34, "evidence_ingest_seq", _034_evidence_ingest_seq),
    Migration(
        35,
        "scanner_state",
        _035_scanner_state,
        ("source_files", "source_sessions", "session_ingest_cursors", "scan_runs"),
    ),
    Migration(36, "retrieval_authority_index", _036_retrieval_authority_index),
    Migration(37, "unknown_jobs_index", _037_unknown_jobs_index),
    Migration(38, "memory_task_evidence_index", _038_memory_task_evidence_index),
    Migration(40, "tail_handoff", _040_tail_handoff, ("tail_handoff",)),
    Migration(41, "tail_handoff_lease_token", _041_tail_handoff_lease_token),
    Migration(42, "reconcile_order_indexes", _042_reconcile_order_indexes),
    Migration(43, "memory_source_filter_index", _043_memory_source_filter_index),
    Migration(44, "memory_task_hy_invocations", _044_memory_task_hy_invocations),
    Migration(
        45,
        "pending_dispatch_priority",
        _045_pending_dispatch_priority,
        ("evidence_pending_memory_dispatch",),
    ),
    Migration(46, "clean_codex_internal_context", _046_clean_codex_internal_context),
    Migration(47, "clean_codex_harness_envelopes", _047_clean_codex_harness_envelopes),
    Migration(49, "cursor_transcript_time_backfill", _049_cursor_transcript_time_backfill),
    Migration(
        50,
        "model_probe_operations_reserved_at",
        _050_model_probe_operations_reserved_at,
    ),
    Migration(51, "drop_hy_node_permissions", _051_drop_hy_node_permissions),
    Migration(
        52,
        "model_request_audit",
        _052_model_request_audit,
        ("model_request_audit",),
    ),
    Migration(
        53,
        "memory_write_event_time",
        _053_memory_write_event_time,
        ("memory_write_schedule_state",),
    ),
    Migration(
        54,
        "legacy_possibly_sent_tasks",
        _054_legacy_possibly_sent_tasks,
    ),
    Migration(
        55,
        "sdk_data_plane_migration",
        _055_sdk_data_plane_migration,
        (
            "sdk_data_plane_migrations",
            "sdk_data_plane_migration_items",
            "memory_id_aliases",
            "sdk_data_plane_cutover",
        ),
    ),
    Migration(
        56,
        "sdk_write_receipts",
        _056_sdk_write_receipts,
        ("memory_task_sdk_receipts",),
    ),
    Migration(
        57,
        "sdk_matter_references",
        _057_sdk_matter_references,
        ("raw_id_aliases", "matter_sdk_versions"),
    ),
    Migration(
        58,
        "sdk_matter_notes",
        _058_sdk_matter_notes,
        ("matter_sdk_notes",),
    ),
    Migration(
        59,
        "memory_authority_state",
        _059_memory_authority_state,
        ("memory_authority_state",),
    ),
    Migration(
        60,
        "memory_note_requests",
        _060_memory_note_requests,
        ("memory_note_requests",),
    ),
)
