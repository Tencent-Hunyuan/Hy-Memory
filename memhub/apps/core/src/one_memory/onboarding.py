import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from one_memory.database import (
    take_the_write_lock_first,
)
from one_memory.database_access import main_database

#: Schema owned by `memory_server.migrations` (migration 12). No local copy.
from one_memory.memory_server.migrations import onboarding_state_ddl
from one_memory.memory_server.sql_compat import execute, is_mysql
from one_memory.memory_server.store_binding import store_for

SCHEMA = onboarding_state_ddl("sqlite")

#: The start/complete upserts, per engine.
#:
#: Same shape of difference as `extraction_settings`: `ON CONFLICT ... DO UPDATE`
#: is SQLite, `ON DUPLICATE KEY UPDATE` is MySQL. They do not raise when wrong —
#: the row simply keeps its old value, so onboarding would silently stay in a
#: previous state. Two named constants, not one shared string.
START_SQLITE = """
    INSERT INTO onboarding_state (
        singleton, status, selected_source_ids_json,
        started_at, completed_at, updated_at
    ) VALUES (1, 'importing', ?, ?, NULL, ?)
    ON CONFLICT(singleton) DO UPDATE SET
        status = 'importing',
        selected_source_ids_json = excluded.selected_source_ids_json,
        started_at = excluded.started_at,
        completed_at = NULL,
        updated_at = excluded.updated_at
"""
START_MYSQL = """
    INSERT INTO onboarding_state (
        singleton, status, selected_source_ids_json,
        started_at, completed_at, updated_at
    ) VALUES (1, 'importing', %s, %s, NULL, %s)
    ON DUPLICATE KEY UPDATE
        status = 'importing',
        selected_source_ids_json = VALUES(selected_source_ids_json),
        started_at = VALUES(started_at),
        completed_at = NULL,
        updated_at = VALUES(updated_at)
"""
COMPLETE_UPDATE_SQLITE = (
    "UPDATE onboarding_state SET status = 'completed', completed_at = ?, updated_at = ? "
    "WHERE singleton = 1"
)
COMPLETE_UPDATE_MYSQL = (
    "UPDATE onboarding_state SET status = 'completed', completed_at = %s, updated_at = %s "
    "WHERE singleton = 1"
)
COMPLETE_INSERT_SQLITE = (
    "INSERT INTO onboarding_state ("
    "singleton, status, selected_source_ids_json, started_at, completed_at, updated_at"
    ") VALUES (1, 'completed', '[]', NULL, ?, ?)"
)
COMPLETE_INSERT_MYSQL = (
    "INSERT INTO onboarding_state ("
    "singleton, status, selected_source_ids_json, started_at, completed_at, updated_at"
    ") VALUES (1, 'completed', '[]', NULL, %s, %s)"
)
RESET_UPDATE_SQLITE = (
    "UPDATE onboarding_state SET status = 'not_started', "
    "selected_source_ids_json = '[]', started_at = NULL, completed_at = NULL, "
    "updated_at = ? WHERE singleton = 1"
)
RESET_UPDATE_MYSQL = (
    "UPDATE onboarding_state SET status = 'not_started', "
    "selected_source_ids_json = '[]', started_at = NULL, completed_at = NULL, "
    "updated_at = %s WHERE singleton = 1"
)


class OnboardingState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    selected_source_ids: tuple[str, ...]
    started_at: datetime | None
    completed_at: datetime | None


class OnboardingStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._store: Any = store_for(database_path)
        self._initialize()

    def _initialize(self) -> None:
        """Create the table through the store, not a raw `executescript`.

        `executescript` is a sqlite3-only convenience; MySQL has no equivalent,
        so the schema goes through the ordinary checkout instead.
        """
        with self._connect(initialize=True) as connection:
            execute(connection, is_mysql(self._store), SCHEMA)

    def get(self) -> OnboardingState:
        mysql = is_mysql(self._store)
        with self._connect() as connection:
            row = execute(
                connection, mysql, "SELECT * FROM onboarding_state WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return OnboardingState(
                status="not_started",
                selected_source_ids=(),
                started_at=None,
                completed_at=None,
            )
        return self._to_state(row)

    def start(self, source_ids: tuple[str, ...]) -> OnboardingState:
        now = datetime.now(UTC).isoformat()
        mysql = is_mysql(self._store)
        with self._connect(writing=True) as connection:
            execute(
                connection,
                mysql,
                START_MYSQL if mysql else START_SQLITE,
                (json.dumps(source_ids), now, now),
            )
        return self.get()

    def complete(self) -> OnboardingState:
        now = datetime.now(UTC).isoformat()
        mysql = is_mysql(self._store)
        with self._connect(writing=True) as connection:
            updated = execute(
                connection,
                mysql,
                COMPLETE_UPDATE_MYSQL if mysql else COMPLETE_UPDATE_SQLITE,
                (now, now),
            )
            if updated.rowcount != 1:
                execute(
                    connection,
                    mysql,
                    COMPLETE_INSERT_MYSQL if mysql else COMPLETE_INSERT_SQLITE,
                    (now, now),
                )
        return self.get()

    def reset(self) -> OnboardingState:
        """Return onboarding to its first-run state, touching nothing else.

        The completion flag is stored beside the memories on purpose: a fresh
        install of the app must not greet someone who already has memories as a
        first-time user. That makes the way back to the cold-start screens an
        explicit action rather than a side effect of reinstalling, and it is
        the whole reason this exists: QA and long-time users need to see those
        screens again without giving up a single memory.

        Sources, memories and settings are separate tables and are not read or
        written here.
        """
        now = datetime.now(UTC).isoformat()
        mysql = is_mysql(self._store)
        with self._connect(writing=True) as connection:
            execute(
                connection,
                mysql,
                RESET_UPDATE_MYSQL if mysql else RESET_UPDATE_SQLITE,
                (now,),
            )
        return self.get()

    @contextmanager
    def _connect(
        self,
        *,
        initialize: bool = False,
        writing: bool = False,
    ) -> Iterator[Any]:
        """A checkout that yields something with `.execute()` on both engines."""
        mysql = is_mysql(self._store)
        if mysql:
            checkout = (
                self._store.write(autocommit=True)
                if (initialize or writing)
                else self._store.read()
            )
            with checkout as connection:
                yield connection
            return

        database = main_database(self.database_path)
        checkout = (
            database.write(autocommit=True, row_factory=sqlite3.Row)
            if initialize or writing
            else database.read(row_factory=sqlite3.Row)
        )
        with checkout as connection, connection:
            if writing:
                take_the_write_lock_first(connection)
            else:
                connection.autocommit = False
            yield connection

    @staticmethod
    def _to_state(row: Any) -> OnboardingState:
        """Read the row by position, so it works for `sqlite3.Row` and tuples.

        `SELECT *` makes the column order part of the contract here, which is
        why the query is `SELECT *` and the indices are named: relying on
        `row["..."]` would only work on SQLite, and relying on position alone
        without saying which column is which would be unreadable.

        Timestamps are a further engine difference: SQLite hands back the stored
        string, MySQL hands back a `datetime`. Accepting both is not padding —
        parsing a `datetime` raises, and treating a string as one would be
        silently wrong.
        """
        # singleton, status, selected_source_ids_json, started_at, completed_at, updated_at
        raw_ids = json.loads(row[2])
        return OnboardingState(
            status=row[1],
            selected_source_ids=tuple(value for value in raw_ids if isinstance(value, str)),
            started_at=_as_datetime(row[3]),
            completed_at=_as_datetime(row[4]),
        )


def _as_datetime(value: object) -> datetime | None:
    """SQLite stores these as text; MySQL returns `datetime` objects."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
