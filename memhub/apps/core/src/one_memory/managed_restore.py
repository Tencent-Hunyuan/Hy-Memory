"""Product boundary for restoring a managed One Memory backup.

The atomic replacement and identity checks live in `MainDatabase.restore_from`;
this module only adds the product rules: the source must be inside the managed
``backups/`` directory, the current database gets a protective copy first, and
failures are reported with the stable CLI vocabulary.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from one_memory.database_access import close_main_databases, main_database
from one_memory.memory_server.migrations import MIGRATIONS, SCHEMA_TABLE

#: What makes a SQLite file a One Memory database, by era. Before the SDK
#: cutover the evidence and governance tables; after it those are dropped
#: (`target_schema`), and a home is its management plane: the authority row
#: and the write queue. A fresh home has only the second shape, so a restore
#: that asked for `evidence` refused every backup a fresh or migrated home took.
_LEGACY_IDENTITY = frozenset({"evidence", "governed_memories"})
_SDK_IDENTITY = frozenset({"memory_authority_state", "memory_write_jobs"})


class RestoreRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DatabaseIdentity:
    """What makes a SQLite file a One Memory management database."""

    #: The tables the atomic replace checks again (`MainDatabase.restore_from`).
    required_tables: frozenset[str]
    schema_version: int
    #: Written by a newer build than this one.
    too_new: bool


def database_identity(path: Path) -> DatabaseIdentity | None:
    """The identity of a backed-up management database; None if it is not one.

    Identity is structural: a real One Memory database always has these
    tables. It must not depend on the migration ledger, which only exists
    after a migration run and is absent from normally-created databases.
    """
    try:
        with main_database(path).read() as connection:
            present = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if present >= _LEGACY_IDENTITY:
                required = frozenset({"evidence"})
            elif present >= _SDK_IDENTITY and _sdk_authority(connection):
                required = _SDK_IDENTITY
            else:
                return None
            version = 0
            if SCHEMA_TABLE in present:
                row = connection.execute(
                    f"SELECT version FROM {SCHEMA_TABLE} ORDER BY version DESC LIMIT 1"
                ).fetchone()
                if row is not None:
                    version = int(row[0])
    except sqlite3.Error:
        return None
    latest_known = max((migration.version for migration in MIGRATIONS), default=0)
    return DatabaseIdentity(required, version, version > latest_known > 0)


@dataclass(frozen=True)
class RestoreResult:
    restored_bytes: int
    previous_backup_path: Path
    source_schema_version: int


def restore_managed_backup(
    *,
    data_dir: Path,
    source: str,
    now: datetime | None = None,
) -> RestoreResult:
    data_dir = data_dir.resolve()
    live = data_dir / "one-memory.sqlite3"
    backups = (data_dir / "backups").resolve()
    candidate = Path(source)
    resolved = (candidate if candidate.is_absolute() else data_dir / candidate).resolve()
    if resolved.is_symlink() or resolved.parent != backups or not resolved.is_file():
        raise RestoreRejected("backup_restore_source_outside_home")

    identity = database_identity(resolved)
    if identity is None:
        raise RestoreRejected("backup_restore_source_invalid")
    if identity.too_new:
        # A newer build wrote this backup. Restoring it would give the current
        # build a schema it cannot interpret, so fail closed and leave the
        # live database untouched.
        raise RestoreRejected("backup_restore_source_too_new")
    source_version = identity.schema_version

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    protective = backups / f"pre-restore-{stamp}.sqlite3"
    try:
        if live.exists():
            main_database(live).back_up(protective)
    except (OSError, sqlite3.Error) as error:
        raise RestoreRejected("backup_restore_protective_backup_failed") from error

    try:
        # Drop every cached connection before the atomic replacement: a pooled
        # connection would otherwise keep reading the old inode.
        close_main_databases()
        main_database(live).restore_from(resolved, required_tables=identity.required_tables)
        close_main_databases()
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
        close_main_databases()
        raise RestoreRejected("backup_restore_replace_failed") from error

    return RestoreResult(
        restored_bytes=live.stat().st_size,
        previous_backup_path=protective,
        source_schema_version=source_version,
    )


def _sdk_authority(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT authority FROM memory_authority_state WHERE singleton=1"
    ).fetchone()
    return row is not None and row[0] == "sdk"
