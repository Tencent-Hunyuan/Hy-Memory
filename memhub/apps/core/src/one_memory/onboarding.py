import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from one_memory.database import (
    take_the_write_lock_first,
)
from one_memory.database_access import main_database

#: Schema owned by `memory_server.migrations` (migration 12). No local copy.
from one_memory.memory_server.migrations import (
    onboarding_progress_ddl,
    onboarding_state_ddl,
)
from one_memory.memory_server.sql_compat import execute, is_mysql, row_to_dict
from one_memory.memory_server.store_binding import store_for

SCHEMA = onboarding_state_ddl("sqlite")
ONBOARDING_COLUMNS = (
    "singleton",
    "status",
    "selected_source_ids_json",
    "started_at",
    "completed_at",
    "updated_at",
    "mode_intent",
    "setup_step",
    "model_profile_id",
)

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
    "UPDATE onboarding_state SET status = 'completed', completed_at = ?, updated_at = ?, "
    "mode_intent = NULL, setup_step = NULL, model_profile_id = NULL "
    "WHERE singleton = 1"
)
COMPLETE_UPDATE_MYSQL = (
    "UPDATE onboarding_state SET status = 'completed', completed_at = %s, updated_at = %s, "
    "mode_intent = NULL, setup_step = NULL, model_profile_id = NULL "
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
    "mode_intent = NULL, setup_step = NULL, model_profile_id = NULL, "
    "updated_at = ? WHERE singleton = 1"
)
RESET_UPDATE_MYSQL = (
    "UPDATE onboarding_state SET status = 'not_started', "
    "selected_source_ids_json = '[]', started_at = NULL, completed_at = NULL, "
    "mode_intent = NULL, setup_step = NULL, model_profile_id = NULL, "
    "updated_at = %s WHERE singleton = 1"
)
ENSURE_PROGRESS_SQLITE = (
    "INSERT INTO onboarding_state ("
    "singleton,status,selected_source_ids_json,started_at,completed_at,updated_at"
    ") VALUES (1,'not_started','[]',NULL,NULL,?) "
    "ON CONFLICT(singleton) DO NOTHING"
)
ENSURE_PROGRESS_MYSQL = (
    "INSERT INTO onboarding_state ("
    "singleton,status,selected_source_ids_json,started_at,completed_at,updated_at"
    ") VALUES (1,'not_started','[]',NULL,NULL,%s) "
    "ON DUPLICATE KEY UPDATE singleton=VALUES(singleton)"
)


class OnboardingState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    selected_source_ids: tuple[str, ...]
    started_at: datetime | None
    completed_at: datetime | None
    mode_intent: Literal["lite", "pro", "ultra"] | None
    setup_step: Literal["review", "mode", "model"] | None
    model_profile_id: str | None


class OnboardingProgressUpdate(BaseModel):
    """Only non-sensitive, resumable choices accepted from the onboarding UI."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode_intent: Literal["lite", "pro", "ultra"] | None = None
    setup_step: Literal["review", "mode", "model"] | None = None
    model_profile_id: str | None = Field(default=None, min_length=36, max_length=36)

    @field_validator("model_profile_id")
    @classmethod
    def validate_model_profile_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError("invalid_model_profile_id") from None
        return value


class OnboardingCompletedError(RuntimeError):
    pass


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
            if not is_mysql(self._store):
                existing = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(onboarding_state)")
                }
                statements = tuple(
                    statement.strip()
                    for statement in onboarding_progress_ddl("sqlite").split(";")
                    if statement.strip()
                )
                for column, statement in zip(
                    ("mode_intent", "setup_step", "model_profile_id"),
                    statements,
                    strict=True,
                ):
                    if column not in existing:
                        connection.execute(statement)

    def get(self) -> OnboardingState:
        mysql = is_mysql(self._store)
        with self._connect() as connection:
            row = execute(
                connection,
                mysql,
                "SELECT " + ", ".join(ONBOARDING_COLUMNS) + " "
                "FROM onboarding_state WHERE singleton = 1",
            ).fetchone()
        if row is None:
            return OnboardingState(
                status="not_started",
                selected_source_ids=(),
                started_at=None,
                completed_at=None,
                mode_intent=None,
                setup_step=None,
                model_profile_id=None,
            )
        return self._to_state(row, mysql=mysql)

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

    def update_progress(self, update: OnboardingProgressUpdate) -> OnboardingState:
        fields = tuple(
            field
            for field in ("mode_intent", "setup_step", "model_profile_id")
            if field in update.model_fields_set
        )
        if not fields:
            state = self.get()
            if state.status == "completed":
                raise OnboardingCompletedError("onboarding_completed")
            return state
        mysql = is_mysql(self._store)
        placeholder = "%s" if mysql else "?"
        assignments = [f"{field} = {placeholder}" for field in fields]
        assignments.append(f"updated_at = {placeholder}")
        values = [getattr(update, field) for field in fields]
        now = datetime.now(UTC).isoformat()
        values.append(now)
        with self._connect(writing=True) as connection:
            execute(
                connection,
                mysql,
                ENSURE_PROGRESS_MYSQL if mysql else ENSURE_PROGRESS_SQLITE,
                (now,),
            )
            updated = execute(
                connection,
                mysql,
                "UPDATE onboarding_state SET "
                + ", ".join(assignments)
                + " WHERE singleton = 1 AND status != 'completed'",
                tuple(values),
            )
            if updated.rowcount != 1:
                raise OnboardingCompletedError("onboarding_completed")
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
    def _to_state(row: Any, *, mysql: bool) -> OnboardingState:
        """Map SQLite rows and MySQL tuples through the shared database adapter."""
        if mysql:
            if not isinstance(row, tuple):
                raise ValueError("onboarding_row_shape_invalid")
        elif not isinstance(row, sqlite3.Row):
            raise ValueError("onboarding_row_shape_invalid")
        if len(row) != len(ONBOARDING_COLUMNS):
            raise ValueError("onboarding_row_shape_invalid")
        if mysql:
            values = dict(zip(ONBOARDING_COLUMNS, tuple(row), strict=True))
        else:
            values = row_to_dict(row, ONBOARDING_COLUMNS, mysql=False)
            if set(values) != set(ONBOARDING_COLUMNS):
                raise ValueError("onboarding_row_shape_invalid")
        _as_datetime(values["updated_at"], mysql=mysql, nullable=False)
        raw_ids = json.loads(values["selected_source_ids_json"])
        return OnboardingState(
            status=values["status"],
            selected_source_ids=tuple(value for value in raw_ids if isinstance(value, str)),
            started_at=_as_datetime(values["started_at"], mysql=mysql),
            completed_at=_as_datetime(values["completed_at"], mysql=mysql),
            mode_intent=values["mode_intent"],
            setup_step=values["setup_step"],
            model_profile_id=values["model_profile_id"],
        )


def _as_datetime(
    value: object,
    *,
    mysql: bool,
    nullable: bool = True,
) -> datetime | None:
    """Validate the engine's timestamp shape and normalize it to UTC."""
    if value is None:
        if not nullable:
            raise ValueError("onboarding_datetime_invalid")
        return None
    if mysql:
        if not isinstance(value, datetime):
            raise ValueError("onboarding_datetime_invalid")
        parsed = value
    else:
        if not isinstance(value, str) or value == "":
            raise ValueError("onboarding_datetime_invalid")
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
