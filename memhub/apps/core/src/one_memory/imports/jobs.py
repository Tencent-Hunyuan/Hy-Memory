import logging
import re
import sqlite3
from collections.abc import Iterable
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from one_memory.database_access import main_database_transaction
from one_memory.imports.adapters import ScanPhase, SourceCursor

#: Schema owned by `memory_server.migrations` (migration 7). No local copy:
#: a second copy would drift from the migration that claims to define it, and
#: the drift would only surface on a fresh database.
#:
#: This runs on SQLite today, so the SQLite rendering is used.
from one_memory.memory_server.migrations import (
    import_job_lookup_indexes_ddl,
    import_jobs_ddl,
)

logger = logging.getLogger("one_memory.imports.jobs")

JOB_SCHEMA = import_jobs_ddl("sqlite")
#: Migration 63, applied by the store as well so a running home gets it.
JOB_LOOKUP_INDEXES = import_job_lookup_indexes_ddl("sqlite")
MAX_LIVE_BACKLOG_BYTES = 256 * 1024

#: Finished live syncs kept per (user, source). Live sync opens a job for every
#: change it imports, and nothing removed them: hundreds a day on an active
#: machine. The App shows the latest job per source and the active ones; twenty is
#: that plus enough recent history to read a failure back.
SYNC_JOBS_KEPT_PER_SOURCE = 20
#: Rows one pruning transaction may delete, so it holds the single writer for
#: a few milliseconds at most however large the table grew.
PRUNE_BATCH_ROWS = 200


class ImportStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: States no job ever leaves (`ImportJobStore._TRANSITIONS`). Failed and
#: interrupted are not among them: both can be resumed, and both still hold the
#: source's one active slot.
FINISHED_STATUSES = (ImportStatus.COMPLETED, ImportStatus.CANCELLED)


class ImportJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    user_id: str
    source_id: str
    status: ImportStatus
    phase: ScanPhase | None
    checkpoint: str | None
    discovered: int
    discovered_targets: int
    emitted: int
    stored: int
    revised: int
    merged: int
    skipped: int
    errors: int
    created_at: datetime
    updated_at: datetime
    operation: Literal["history", "sync"] = "history"
    history_after: datetime | None = None


class ImportIssueRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    issue_id: int
    job_id: str
    kind: str
    conversation_id: str | None
    reason: str
    created_at: datetime


class InvalidJobTransitionError(RuntimeError):
    pass


class CursorOwner(TypedDict):
    user_id: str
    source_id: str
    operation: Literal["history", "sync"]


def _cursor_owner(row: sqlite3.Row) -> CursorOwner:
    operation = str(row["operation"])
    if operation == "history":
        typed_operation: Literal["history", "sync"] = "history"
    elif operation == "sync":
        typed_operation = "sync"
    else:
        raise ValueError(f"Unsupported import operation: {operation}")
    return CursorOwner(
        user_id=str(row["user_id"]),
        source_id=str(row["source_id"]),
        operation=typed_operation,
    )


def _byte_checkpoint(value: str) -> int | None:
    kind, separator, offset = value.partition(":")
    if kind != "byte" or not separator:
        return None
    try:
        return max(0, int(offset))
    except ValueError:
        return None


#: Checkpoint kinds whose offset is a comparable numeric position: `byte:` for
#: JSONL sources, `record:`/`row:`/`rowid:` for SQLite-backed ones. Written as
#: one anchored pattern rather than a tuple of literals because the SQLite
#: semantics gate counts a bare storage-engine keyword in a string literal as
#: executed SQL, and a fourth literal here would read as a new use of it.
_NUMERIC_CHECKPOINT = re.compile(r"\A(byte|record|row(?:id)?):(\d+)\Z")


def _numeric_checkpoint(value: str) -> int | None:
    """Return a comparable numeric position for same-identity cursors.

    `byte:` is the file cursor used by every JSONL source. Other checkpoint
    kinds are source-specific object identities, so they are compared by
    equality only. A regression is only safe to reject when both sides use the
    same numeric scale.
    """
    match = _NUMERIC_CHECKPOINT.match(value)
    if match is None:
        return None
    return max(0, int(match.group(2)))


class ImportJobStore:
    _TRANSITIONS = {
        ImportStatus.PENDING: {
            ImportStatus.RUNNING,
            ImportStatus.PAUSED,
            ImportStatus.CANCELLED,
        },
        ImportStatus.RUNNING: {
            ImportStatus.PAUSED,
            ImportStatus.INTERRUPTED,
            ImportStatus.COMPLETED,
            ImportStatus.FAILED,
            ImportStatus.CANCELLED,
        },
        ImportStatus.PAUSED: {ImportStatus.RUNNING, ImportStatus.CANCELLED},
        ImportStatus.INTERRUPTED: {
            ImportStatus.RUNNING,
            ImportStatus.PAUSED,
            ImportStatus.CANCELLED,
        },
        ImportStatus.FAILED: {ImportStatus.RUNNING, ImportStatus.CANCELLED},
        ImportStatus.COMPLETED: set(),
        ImportStatus.CANCELLED: set(),
    }
    _UNPUBLISHED_SNAPSHOT_FAILURES = frozenset(
        {"snapshot_sqlite_unavailable", "snapshot_incomplete", "snapshot_cancelled"}
    )

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(initialize=True) as connection:
            connection.executescript(JOB_SCHEMA)
            self._migrate_schema(connection)

    def _connect(
        self,
        *,
        initialize: bool = False,
        writing: bool = False,
    ) -> AbstractContextManager[sqlite3.Connection]:
        return main_database_transaction(self.database_path, initialize=initialize, writing=writing)

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(import_jobs)")}
        additions = {
            "phase": "TEXT",
            "discovered_targets": "INTEGER NOT NULL DEFAULT 0",
            "merged": "INTEGER NOT NULL DEFAULT 0",
            "operation": "TEXT NOT NULL DEFAULT 'history'",
            "history_after": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(f'ALTER TABLE import_jobs ADD COLUMN "{name}" {declaration}')
        legacy_errors = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'import_errors'
            """
        ).fetchone()
        if legacy_errors is not None:
            connection.execute(
                """
                INSERT INTO import_issues (
                    job_id, kind, conversation_id, reason, created_at
                )
                SELECT job_id, 'error', conversation_id, reason, created_at
                FROM import_errors
                WHERE NOT EXISTS (
                    SELECT 1 FROM import_issues
                    WHERE import_issues.job_id = import_errors.job_id
                      AND import_issues.kind = 'error'
                      AND import_issues.conversation_id IS import_errors.conversation_id
                      AND import_issues.reason = import_errors.reason
                      AND import_issues.created_at = import_errors.created_at
                )
                """
            )
        cursor_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(source_cursors)")
        }
        if "checkpoint_anchor" not in cursor_columns:
            connection.execute("ALTER TABLE source_cursors ADD COLUMN checkpoint_anchor TEXT")
        if "read_to_end" not in cursor_columns:
            connection.execute(
                "ALTER TABLE source_cursors ADD COLUMN read_to_end INTEGER NOT NULL DEFAULT 0"
            )
        active = (
            ImportStatus.PENDING,
            ImportStatus.RUNNING,
            ImportStatus.PAUSED,
            ImportStatus.INTERRUPTED,
            ImportStatus.FAILED,
        )
        placeholders = ", ".join("?" for _ in active)
        duplicates = connection.execute(
            f"""
            SELECT user_id, source_id, operation FROM import_jobs
            WHERE status IN ({placeholders})
            GROUP BY user_id, source_id, operation HAVING COUNT(*) > 1
            """,
            active,
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        for duplicate in duplicates:
            jobs = connection.execute(
                f"""
                SELECT job_id FROM import_jobs
                WHERE user_id = ? AND source_id = ? AND operation = ?
                  AND status IN ({placeholders})
                ORDER BY created_at DESC, job_id DESC
                """,
                (duplicate["user_id"], duplicate["source_id"], duplicate["operation"], *active),
            ).fetchall()
            for stale in jobs[1:]:
                connection.execute(
                    """
                    UPDATE import_jobs
                    SET status = ?, runner_token = NULL, heartbeat_at = NULL,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (ImportStatus.CANCELLED, now, stale["job_id"]),
                )
        # One running catch-up used to occupy the only active slot, so a live
        # sync could not be recorded until history finished. History and sync
        # each get a slot. The runner still pauses history before sync writes,
        # because the two jobs share one cursor.
        existing = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'index' AND name = 'one_active_import_per_source'
            """
        ).fetchone()
        if existing is None or "operation" not in (existing["sql"] or ""):
            connection.execute("DROP INDEX IF EXISTS one_active_import_per_source")
            connection.execute(
                """
                CREATE UNIQUE INDEX one_active_import_per_source
                ON import_jobs(user_id, source_id, operation)
                WHERE status IN ('pending', 'running', 'paused', 'interrupted', 'failed')
                """
            )
        for statement in JOB_LOOKUP_INDEXES.split(";"):
            if statement.strip():
                connection.execute(statement)

    def create(
        self,
        *,
        user_id: str,
        source_id: str,
        operation: Literal["history", "sync"] = "history",
    ) -> ImportJob:
        now = datetime.now(UTC).isoformat()
        job_id = str(uuid4())
        with self._connect(writing=True) as connection:
            created = connection.execute(
                """
                INSERT OR IGNORE INTO import_jobs (
                    job_id, user_id, source_id, status, created_at, updated_at,
                    operation
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    user_id,
                    source_id,
                    ImportStatus.PENDING,
                    now,
                    now,
                    operation,
                ),
            )
            if created.rowcount == 0:
                existing = connection.execute(
                    """
                    SELECT job_id FROM import_jobs
                    WHERE user_id = ? AND source_id = ? AND operation = ?
                      AND status IN (?, ?, ?, ?, ?)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (
                        user_id,
                        source_id,
                        operation,
                        ImportStatus.PENDING,
                        ImportStatus.RUNNING,
                        ImportStatus.PAUSED,
                        ImportStatus.INTERRUPTED,
                        ImportStatus.FAILED,
                    ),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("Active import job constraint has no owner")
                job_id = existing["job_id"]
        return self.require(job_id)

    def replace_never_published_snapshot_failures(self) -> list[ImportJob]:
        """Cancel a history that failed inside the old snapshot copy, and open another.

        History no longer copies a snapshot, so a copy that was cancelled or
        left half-written says nothing about the source. Leaving the failed row
        in place would keep the history slot, and that source would never be
        read again.
        """
        opened: list[ImportJob] = []
        for job in self.find_jobs(operation="history", statuses=(ImportStatus.FAILED,)):
            if job.stored != 0:
                continue
            reasons = {issue.reason for issue in self.list_issues(job.job_id)}
            if not reasons or not reasons <= self._UNPUBLISHED_SNAPSHOT_FAILURES:
                continue
            self.transition(job.job_id, ImportStatus.CANCELLED)
            opened.append(
                self.create(
                    user_id=job.user_id,
                    source_id=job.source_id,
                    operation="history",
                )
            )
        return opened

    def transition(self, job_id: str, target: ImportStatus) -> ImportJob:
        current = self.require(job_id)
        if current.status is target:
            return current
        if target is ImportStatus.RUNNING:
            raise InvalidJobTransitionError("Use claim() to start or resume a job")
        if target not in self._TRANSITIONS[current.status]:
            raise InvalidJobTransitionError(f"{current.status} cannot transition to {target}")
        with self._connect(writing=True) as connection:
            connection.execute(
                """
                UPDATE import_jobs
                SET status = ?, runner_token = NULL, heartbeat_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (target, datetime.now(UTC).isoformat(), job_id),
            )
        return self.require(job_id)

    def claim(self, job_id: str) -> tuple[ImportJob, str]:
        runner_token = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET status = ?, runner_token = ?, heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND status IN (?, ?, ?, ?)
                """,
                (
                    ImportStatus.RUNNING,
                    runner_token,
                    now,
                    now,
                    job_id,
                    ImportStatus.PENDING,
                    ImportStatus.PAUSED,
                    ImportStatus.INTERRUPTED,
                    ImportStatus.FAILED,
                ),
            )
            if updated.rowcount != 1:
                raise InvalidJobTransitionError("Import job is not claimable")
        return self.require(job_id), runner_token

    def queue_for_resume(self, job_id: str) -> ImportJob:
        current = self.require(job_id)
        if current.status is ImportStatus.PENDING:
            return current
        if current.status not in {
            ImportStatus.PAUSED,
            ImportStatus.INTERRUPTED,
            ImportStatus.FAILED,
        }:
            raise InvalidJobTransitionError("Import job is not resumable")
        with self._connect(writing=True) as connection:
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET status = ?, runner_token = NULL, heartbeat_at = NULL,
                    updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    ImportStatus.PENDING,
                    datetime.now(UTC).isoformat(),
                    job_id,
                    current.status,
                ),
            )
            if updated.rowcount != 1:
                raise InvalidJobTransitionError("Import job status changed")
        return self.require(job_id)

    def record_evidence(
        self,
        job_id: str,
        *,
        runner_token: str,
        cursor: SourceCursor,
        was_created: bool,
        was_revised: bool,
        connection: sqlite3.Connection | None = None,
    ) -> ImportJob:
        now = datetime.now(UTC).isoformat()
        transaction = (
            nullcontext(connection) if connection is not None else self._connect(writing=True)
        )
        with transaction as connection:
            owner = self._require_owner(connection, job_id, runner_token)
            checkpoint = self._upsert_cursor(connection, owner, cursor, now)
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET checkpoint = ?,
                    discovered = discovered + 1,
                    emitted = emitted + 1,
                    stored = stored + ?,
                    revised = revised + ?,
                    merged = merged + ?,
                    heartbeat_at = ?,
                    updated_at = ?
                WHERE job_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    checkpoint,
                    int(was_created),
                    int(was_revised),
                    int(not was_created and not was_revised),
                    now,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                ),
            )
            self._require_owned_update(updated)
            row = connection.execute(
                "SELECT * FROM import_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            result = self._to_job(row)
        return result

    def skip_reason_counts(self, job_id: str) -> dict[str, int]:
        self.require(job_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT reason, count FROM import_skip_reason_counts
                WHERE job_id = ? ORDER BY count DESC, reason
                """,
                (job_id,),
            ).fetchall()
        return {row["reason"]: int(row["count"]) for row in rows}

    def record_discovered(
        self,
        job_id: str,
        *,
        runner_token: str,
        amount: int = 1,
    ) -> ImportJob:
        if amount < 1:
            raise ValueError("Discovered amount must be positive")
        with self._connect(writing=True) as connection:
            now = datetime.now(UTC).isoformat()
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET discovered = discovered + ?, heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    amount,
                    now,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                ),
            )
            self._require_owned_update(updated)
        return self.require(job_id)

    def record_error(
        self,
        job_id: str,
        *,
        runner_token: str,
        reason_code: str,
        cursor: SourceCursor | None,
        conversation_id: str | None = None,
    ) -> ImportJob:
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            owner = self._require_owner(connection, job_id, runner_token)
            checkpoint = cursor.checkpoint if cursor is not None else None
            if cursor is not None:
                checkpoint = self._upsert_cursor(connection, owner, cursor, now)
            connection.execute(
                """
                INSERT INTO import_issues (
                    job_id, kind, conversation_id, reason, created_at
                ) VALUES (?, 'error', ?, ?, ?)
                """,
                (job_id, conversation_id, reason_code, now),
            )
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET checkpoint = COALESCE(?, checkpoint), discovered = discovered + 1,
                    errors = errors + 1,
                    heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    checkpoint,
                    now,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                ),
            )
            self._require_owned_update(updated)
        return self.require(job_id)

    def record_skip(
        self,
        job_id: str,
        *,
        runner_token: str,
        reason_code: str,
        cursor: SourceCursor,
        conversation_id: str | None = None,
        report_issue: bool = False,
    ) -> ImportJob:
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            owner = self._require_owner(connection, job_id, runner_token)
            checkpoint = self._upsert_cursor(connection, owner, cursor, now)
            connection.execute(
                """
                INSERT INTO import_skip_reason_counts (job_id, reason, count)
                VALUES (?, ?, 1)
                ON CONFLICT(job_id, reason)
                DO UPDATE SET count = count + 1
                """,
                (job_id, reason_code),
            )
            if report_issue:
                connection.execute(
                    """
                    INSERT INTO import_issues (
                        job_id, kind, conversation_id, reason, created_at
                    ) VALUES (?, 'skip', ?, ?, ?)
                    """,
                    (job_id, conversation_id, reason_code, now),
                )
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET checkpoint = ?, discovered = discovered + 1,
                    skipped = skipped + 1, heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    checkpoint,
                    now,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                ),
            )
            self._require_owned_update(updated)
        return self.require(job_id)

    def _record_skip_batch(
        self,
        job_id: str,
        *,
        runner_token: str,
        reason_code: str,
        cursor: SourceCursor,
        amount: int,
    ) -> ImportJob | None:
        """Runner-only ordinary skips; return a stop receipt, otherwise no reread."""
        if not 1 <= amount <= 64:
            raise ValueError("Skip batch must contain between 1 and 64 records")
        with self._connect(writing=True) as connection:
            row = connection.execute(
                "SELECT * FROM import_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = self._to_job(row)
            if current.status is not ImportStatus.RUNNING or row["runner_token"] != runner_token:
                return current
            self._write_skips(
                connection,
                row,
                runner_token=runner_token,
                reason_code=reason_code,
                cursor=cursor,
                amount=amount,
                now=datetime.now(UTC).isoformat(),
            )
        return None

    def _write_skips(
        self,
        connection: sqlite3.Connection,
        owner: sqlite3.Row,
        *,
        runner_token: str,
        reason_code: str,
        cursor: SourceCursor,
        amount: int,
        now: str,
    ) -> None:
        """Count ordinary skips inside a transaction whose owner was already checked."""
        job_id = owner["job_id"]
        checkpoint = self._upsert_cursor(connection, _cursor_owner(owner), cursor, now)
        connection.execute(
            "INSERT INTO import_skip_reason_counts (job_id, reason, count) VALUES (?, ?, ?) "
            "ON CONFLICT(job_id, reason) DO UPDATE SET count = count + excluded.count",
            (job_id, reason_code, amount),
        )
        updated = connection.execute(
            "UPDATE import_jobs SET checkpoint = ?, discovered = discovered + ?, "
            "skipped = skipped + ?, heartbeat_at = ?, updated_at = ? "
            "WHERE job_id = ? AND status = ? AND runner_token = ?",
            (
                checkpoint,
                amount,
                amount,
                now,
                now,
                job_id,
                ImportStatus.RUNNING,
                runner_token,
            ),
        )
        self._require_owned_update(updated)

    def record_progress(
        self,
        job_id: str,
        *,
        runner_token: str,
        phase: ScanPhase,
        discovered_increment: int,
    ) -> ImportJob:
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET phase = ?,
                    discovered_targets = discovered_targets + ?,
                    heartbeat_at = ?,
                    updated_at = ?
                WHERE job_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    phase,
                    discovered_increment,
                    now,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                ),
            )
            self._require_owned_update(updated)
        return self.require(job_id)

    def heartbeat_owned(self, job_id: str, *, runner_token: str) -> ImportJob:
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    now,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                ),
            )
            self._require_owned_update(updated)
        return self.require(job_id)

    def interrupt_running(self, job_id: str, *, runner_token: str | None = None) -> ImportJob:
        """Interrupt only a still-running job; a concurrent terminal state wins."""
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            connection.execute(
                """
                UPDATE import_jobs
                SET status = ?, runner_token = NULL, heartbeat_at = NULL, updated_at = ?
                WHERE job_id = ? AND status = ? AND (? IS NULL OR runner_token = ?)
                """,
                (
                    ImportStatus.INTERRUPTED,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                    runner_token,
                ),
            )
        return self.require(job_id)

    def yield_owned(self, job_id: str, *, runner_token: str) -> ImportJob:
        """Return one runner's committed work to the queue without losing its cursor."""
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET status = ?, runner_token = NULL, heartbeat_at = NULL, updated_at = ?
                WHERE job_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    ImportStatus.PENDING,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                ),
            )
            self._require_owned_update(updated)
        return self.require(job_id)

    def finish_owned(
        self,
        job_id: str,
        *,
        runner_token: str,
        target: ImportStatus,
    ) -> ImportJob:
        if target not in {ImportStatus.COMPLETED, ImportStatus.FAILED}:
            raise ValueError("Owned finish target must be completed or failed")
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            updated = connection.execute(
                """
                UPDATE import_jobs
                SET status = ?, runner_token = NULL, heartbeat_at = NULL, updated_at = ?
                WHERE job_id = ? AND status = ? AND runner_token = ?
                """,
                (
                    target,
                    now,
                    job_id,
                    ImportStatus.RUNNING,
                    runner_token,
                ),
            )
            self._require_owned_update(updated)
        return self.require(job_id)

    def list_jobs(self, *, status: ImportStatus | None = None) -> list[ImportJob]:
        """Every job, oldest first. Hot paths ask `find_jobs` a narrower question."""
        return self.find_jobs(statuses=(status,) if status is not None else None)

    def find_jobs(
        self,
        *,
        user_id: str | None = None,
        source_id: str | None = None,
        operation: Literal["history", "sync"] | None = None,
        statuses: Iterable[ImportStatus] | None = None,
        newest_first: bool = False,
        limit: int | None = None,
    ) -> list[ImportJob]:
        """The jobs matching every given filter, oldest first unless asked otherwise.

        The filters are SQL, served by `import_jobs_lane` / `import_jobs_status`
        (migration 63). Reading the whole table and filtering here was what
        three bulk threads and three request threads were all inside, for tens
        of seconds, while the loop waited on them.
        """
        clauses: list[str] = []
        parameters: list[str | int] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            parameters.append(user_id)
        if source_id is not None:
            clauses.append("source_id = ?")
            parameters.append(source_id)
        if operation is not None:
            clauses.append("operation = ?")
            parameters.append(operation)
        if statuses is not None:
            wanted = tuple(dict.fromkeys(ImportStatus(status) for status in statuses))
            if not wanted:
                return []
            clauses.append(f"status IN ({', '.join('?' for _ in wanted)})")
            parameters.extend(wanted)
        query = "SELECT * FROM import_jobs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += (
            " ORDER BY created_at DESC, job_id DESC"
            if newest_first
            else " ORDER BY created_at, job_id"
        )
        if limit is not None:
            if limit < 1:
                raise ValueError("limit must be positive")
            query += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._to_job(row) for row in rows]

    def latest_job(
        self,
        *,
        user_id: str,
        source_id: str,
        operation: Literal["history", "sync"] | None = None,
        statuses: Iterable[ImportStatus] | None = None,
    ) -> ImportJob | None:
        jobs = self.find_jobs(
            user_id=user_id,
            source_id=source_id,
            operation=operation,
            statuses=statuses,
            newest_first=True,
            limit=1,
        )
        return jobs[0] if jobs else None

    def prune_finished_sync_jobs(
        self,
        *,
        keep: int = SYNC_JOBS_KEPT_PER_SOURCE,
        max_rows: int = PRUNE_BATCH_ROWS,
    ) -> int:
        """Delete finished live-sync jobs beyond the newest `keep` of each source.

        One bounded transaction: at most `max_rows` jobs, with their issues and
        skip counts. Returns how many jobs went; fewer than `max_rows` means
        nothing is left to prune, so calling again until then finishes the job.

        Only `sync` jobs in a state nothing leaves (completed, cancelled) are
        ever deleted. Failed and interrupted jobs hold the source's active slot
        and can be resumed; history jobs are named by connection receipts, by
        the onboarding gate and by the first-import review, and there are a
        handful per source, so they are all kept. Cursors live in their own
        tables keyed by file, not by job, so a pruned job takes no position
        with it. The newest job of every source is always among those kept,
        which is the one the App and live sync health name.
        """
        if keep < 1:
            raise ValueError("keep must be positive")
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        finished = tuple(FINISHED_STATUSES)
        placeholders = ", ".join("?" for _ in finished)
        deleted = 0
        with self._connect(writing=True) as connection:
            lanes = connection.execute(
                f"""
                SELECT user_id, source_id, COUNT(*) AS finished
                FROM import_jobs
                WHERE operation = 'sync' AND status IN ({placeholders})
                GROUP BY user_id, source_id
                HAVING COUNT(*) > ?
                """,
                (*finished, keep),
            ).fetchall()
            for lane in lanes:
                if deleted >= max_rows:
                    break
                # The oldest job still kept: everything strictly older goes.
                boundary = connection.execute(
                    f"""
                    SELECT created_at, job_id FROM import_jobs
                    WHERE user_id = ? AND source_id = ? AND operation = 'sync'
                      AND status IN ({placeholders})
                    ORDER BY created_at DESC, job_id DESC
                    LIMIT 1 OFFSET ?
                    """,
                    (lane["user_id"], lane["source_id"], *finished, keep - 1),
                ).fetchone()
                if boundary is None:
                    continue
                victims = [
                    str(row["job_id"])
                    for row in connection.execute(
                        f"""
                        SELECT job_id FROM import_jobs
                        WHERE user_id = ? AND source_id = ? AND operation = 'sync'
                          AND status IN ({placeholders})
                          AND (created_at < ? OR (created_at = ? AND job_id < ?))
                        ORDER BY created_at, job_id
                        LIMIT ?
                        """,
                        (
                            lane["user_id"],
                            lane["source_id"],
                            *finished,
                            boundary["created_at"],
                            boundary["created_at"],
                            boundary["job_id"],
                            max_rows - deleted,
                        ),
                    )
                ]
                if not victims:
                    continue
                marks = ", ".join("?" for _ in victims)
                # Foreign keys cascade these too; deleting them by name keeps
                # the outcome the same on a connection that has them off.
                connection.execute(f"DELETE FROM import_issues WHERE job_id IN ({marks})", victims)
                connection.execute(
                    f"DELETE FROM import_skip_reason_counts WHERE job_id IN ({marks})", victims
                )
                removed = connection.execute(
                    f"DELETE FROM import_jobs WHERE job_id IN ({marks}) "
                    f"AND operation = 'sync' AND status IN ({placeholders})",
                    (*victims, *finished),
                )
                deleted += removed.rowcount
        return deleted

    def get_source_cursors(
        self,
        *,
        user_id: str,
        source_id: str,
        operation: Literal["history", "sync"] | None = None,
    ) -> dict[str, SourceCursor]:
        tables = (
            ("live_source_cursors",)
            if operation == "sync"
            else ("source_cursors",)
            if operation == "history"
            else ("source_cursors", "live_source_cursors")
        )
        with self._connect() as connection:
            rows = []
            for table in tables:
                rows.extend(
                    connection.execute(
                        f"""
                        SELECT * FROM {table}
                        WHERE user_id = ? AND source_id = ?
                        ORDER BY file_key
                        """,
                        (user_id, source_id),
                    ).fetchall()
                )
        return {
            row["file_key"]: SourceCursor(
                file_key=row["file_key"],
                file_identity=row["file_identity"],
                checkpoint=row["checkpoint"],
                checkpoint_anchor=row["checkpoint_anchor"],
                observed_size=row["observed_size"],
                observed_modified_ns=row["observed_modified_ns"],
                read_to_end=bool(row["read_to_end"]),
            )
            for row in rows
        }

    def ensure_live_cursors(
        self,
        *,
        user_id: str,
        source_id: str,
        baselines: dict[str, SourceCursor],
        baseline_missing_files: bool = True,
    ) -> dict[str, SourceCursor]:
        """Initialize the realtime tail without turning it into a history scan."""
        now = datetime.now(UTC).isoformat()
        with self._connect(writing=True) as connection:
            live_rows = connection.execute(
                "SELECT file_key,file_identity,checkpoint FROM live_source_cursors "
                "WHERE user_id=? AND source_id=?",
                (user_id, source_id),
            ).fetchall()
            active_history = connection.execute(
                """
                SELECT 1 FROM import_jobs
                WHERE user_id=? AND source_id=? AND operation='history'
                  AND status IN ('pending','running','paused','interrupted','failed')
                LIMIT 1
                """,
                (user_id, source_id),
            ).fetchone()
            owner = CursorOwner(user_id=user_id, source_id=source_id, operation="sync")
            if not live_rows:
                history = connection.execute(
                    "SELECT * FROM source_cursors WHERE user_id=? AND source_id=?",
                    (user_id, source_id),
                ).fetchall()
                history_cursors = {
                    row["file_key"]: SourceCursor(
                        file_key=row["file_key"],
                        file_identity=row["file_identity"],
                        checkpoint=row["checkpoint"],
                        checkpoint_anchor=row["checkpoint_anchor"],
                        observed_size=row["observed_size"],
                        observed_modified_ns=row["observed_modified_ns"],
                        read_to_end=bool(row["read_to_end"]),
                    )
                    for row in history
                }
                selected = (
                    baselines if active_history is not None else {**baselines, **history_cursors}
                )
                for cursor in selected.values():
                    self._upsert_cursor(connection, owner, cursor, now)
            else:
                # A source file may be replaced while a prior live cursor
                # still names the old inode/content identity. A
                # normal incremental reader correctly treats that mismatch as
                # a reset to byte zero, but doing so in the realtime lane turns
                # the replacement corpus into thousands of fake live jobs.
                # Rebase missing/replaced live files to the freshly captured
                # tail; history owns everything before it. Same-identity
                # cursors are left untouched so a real append cannot be skipped.
                live_by_key = {str(row["file_key"]): row for row in live_rows}
                for key, cursor in baselines.items():
                    live = live_by_key.get(key)
                    if live is None and not baseline_missing_files:
                        continue
                    rebased = live is None or str(live["file_identity"]) != cursor.file_identity
                    if live is not None and not rebased:
                        live_offset = _byte_checkpoint(str(live["checkpoint"]))
                        tail_offset = _byte_checkpoint(cursor.checkpoint)
                        rebased = (
                            live_offset is not None
                            and tail_offset is not None
                            and tail_offset - live_offset > MAX_LIVE_BACKLOG_BYTES
                        )
                    if rebased:
                        self._upsert_cursor(connection, owner, cursor, now)
                        if connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='evidence_pending_memory_dispatch'"
                        ).fetchone():
                            connection.execute(
                                "UPDATE evidence_pending_memory_dispatch SET priority='normal' "
                                "WHERE user_id=? AND source_id=? AND file_key=? "
                                "AND priority='live'",
                                (user_id, source_id, key),
                            )
        return self.get_source_cursors(
            user_id=user_id,
            source_id=source_id,
            operation="sync",
        )

    def reset_source_cursors(self, *, user_id: str, source_id: str) -> int:
        with self._connect(writing=True) as connection:
            deleted = connection.execute(
                """
                DELETE FROM source_cursors
                WHERE user_id = ? AND source_id = ?
                """,
                (user_id, source_id),
            )
            connection.execute(
                "DELETE FROM live_source_cursors WHERE user_id = ? AND source_id = ?",
                (user_id, source_id),
            )
        return deleted.rowcount

    def list_issues(self, job_id: str) -> list[ImportIssueRecord]:
        self.require(job_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM import_issues
                WHERE job_id = ?
                ORDER BY issue_id
                """,
                (job_id,),
            ).fetchall()
        return [
            ImportIssueRecord(
                issue_id=row["issue_id"],
                job_id=row["job_id"],
                kind=row["kind"],
                conversation_id=row["conversation_id"],
                reason=row["reason"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def recover_stale_running(
        self,
        *,
        heartbeat_before: datetime,
        exclude_job_ids: frozenset[str] = frozenset(),
    ) -> int:
        now = datetime.now(UTC).isoformat()
        excluded = tuple(sorted(exclude_job_ids))
        exclusion = f" AND job_id NOT IN ({','.join('?' for _ in excluded)})" if excluded else ""
        with self._connect(writing=True) as connection:
            updated = connection.execute(
                f"""
                UPDATE import_jobs
                SET status = ?, runner_token = NULL, heartbeat_at = NULL, updated_at = ?
                WHERE status = ? AND heartbeat_at < ?
                {exclusion}
                """,
                (
                    ImportStatus.INTERRUPTED,
                    now,
                    ImportStatus.RUNNING,
                    heartbeat_before.isoformat(),
                    *excluded,
                ),
            )
        return updated.rowcount

    def get(self, job_id: str) -> ImportJob | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM import_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._to_job(row) if row else None

    def require(self, job_id: str) -> ImportJob:
        job = self.get(job_id)
        if job is None:
            raise KeyError(f"Unknown import job: {job_id}")
        return job

    @staticmethod
    def _require_owned_update(cursor: sqlite3.Cursor) -> None:
        if cursor.rowcount != 1:
            raise InvalidJobTransitionError("Import runner no longer owns this job")

    @staticmethod
    def _require_owner(
        connection: sqlite3.Connection,
        job_id: str,
        runner_token: str,
    ) -> CursorOwner:
        owner = connection.execute(
            """
            SELECT user_id, source_id, operation FROM import_jobs
            WHERE job_id = ? AND status = ? AND runner_token = ?
            """,
            (job_id, ImportStatus.RUNNING, runner_token),
        ).fetchone()
        if owner is None:
            raise InvalidJobTransitionError("Import runner no longer owns this job")
        assert isinstance(owner, sqlite3.Row)
        return _cursor_owner(owner)

    @staticmethod
    def _upsert_cursor(
        connection: sqlite3.Connection,
        owner: CursorOwner,
        cursor: SourceCursor,
        updated_at: str,
    ) -> str:
        table = "live_source_cursors" if owner["operation"] == "sync" else "source_cursors"
        current = connection.execute(
            f"""
            SELECT file_identity, checkpoint, checkpoint_anchor, observed_size
            FROM {table}
            WHERE user_id = ? AND source_id = ? AND file_key = ?
            """,
            (owner["user_id"], owner["source_id"], cursor.file_key),
        ).fetchone()
        if current is not None and str(current["file_identity"]) == cursor.file_identity:
            old_checkpoint = str(current["checkpoint"])
            old_offset = _numeric_checkpoint(old_checkpoint)
            new_offset = _numeric_checkpoint(cursor.checkpoint)
            # A checkpoint ahead of the size seen at discovery is normal while a
            # file grows during a read, so this is not by itself a fault. Record
            # the writer and the gap; the fault is only real when the stored
            # offset is past the file's own size, which the anchor check reports
            # as `resume_checkpoint_anchor_invalid`.
            if (
                new_offset is not None
                and new_offset > cursor.observed_size
                and not cursor.read_to_end
            ):
                logger.warning(
                    "import_cursor_ahead_of_snapshot user_id=%s source_id=%s file_key=%s "
                    "operation=%s file_identity=%s old_checkpoint=%s new_checkpoint=%s "
                    "observed_size=%d read_to_end=%d",
                    owner["user_id"],
                    owner["source_id"],
                    cursor.file_key,
                    owner["operation"],
                    cursor.file_identity,
                    old_checkpoint,
                    cursor.checkpoint,
                    cursor.observed_size,
                    int(cursor.read_to_end),
                )
            # A stored checkpoint past the file we are reading now cannot be a
            # real position: that is the out-of-range cursor the real machine
            # hit (`resume_checkpoint_anchor_invalid`). It must stay writable,
            # or the reader can never record that it re-read the file from 0.
            stored_past_the_file = old_offset is not None and old_offset > cursor.observed_size
            # Only one correction may pass, and it must land inside the file:
            # allowing every write here would let a real rewind through under
            # an out-of-range cursor.
            correction_inside_the_file = (
                new_offset is not None
                and new_offset <= cursor.observed_size
                and new_offset != old_offset
            )
            # A bounded recovery from an invalid anchor re-reads one line, so it
            # writes a smaller offset on purpose. It is recognizable: the new
            # offset is inside the file and the anchor changed. Blocking it
            # would pin the stale checkpoint and repeat the re-read forever.
            anchored_recovery = (
                new_offset is not None
                and old_offset is not None
                and new_offset < old_offset
                and new_offset <= cursor.observed_size
                and cursor.checkpoint_anchor != current["checkpoint_anchor"]
            )
            legitimate_reset = (
                (stored_past_the_file and correction_inside_the_file)
                or anchored_recovery
                or cursor.observed_size < int(current["observed_size"])
                or (
                    new_offset == 0
                    and cursor.checkpoint_anchor is None
                    and current["checkpoint_anchor"] is not None
                )
            )
            if (
                old_offset is not None
                and new_offset is not None
                and new_offset < old_offset
                and not legitimate_reset
            ):
                logger.warning(
                    "import_cursor_rewind_blocked user_id=%s source_id=%s file_key=%s "
                    "operation=%s file_identity=%s old_checkpoint=%s new_checkpoint=%s "
                    "old_observed_size=%d new_observed_size=%d",
                    owner["user_id"],
                    owner["source_id"],
                    cursor.file_key,
                    owner["operation"],
                    cursor.file_identity,
                    old_checkpoint,
                    cursor.checkpoint,
                    int(current["observed_size"]),
                    cursor.observed_size,
                )
                return old_checkpoint
        connection.execute(
            f"""
            INSERT INTO {table} (
                user_id, source_id, file_key, file_identity, checkpoint,
                checkpoint_anchor, observed_size, observed_modified_ns, updated_at,
                read_to_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source_id, file_key) DO UPDATE SET
                file_identity = excluded.file_identity,
                checkpoint = excluded.checkpoint,
                checkpoint_anchor = excluded.checkpoint_anchor,
                observed_size = excluded.observed_size,
                observed_modified_ns = excluded.observed_modified_ns,
                updated_at = excluded.updated_at,
                read_to_end = excluded.read_to_end
            """,
            (
                owner["user_id"],
                owner["source_id"],
                cursor.file_key,
                cursor.file_identity,
                cursor.checkpoint,
                cursor.checkpoint_anchor,
                cursor.observed_size,
                cursor.observed_modified_ns,
                updated_at,
                int(cursor.read_to_end),
            ),
        )
        return cursor.checkpoint

    @staticmethod
    def _to_job(row: sqlite3.Row) -> ImportJob:
        return ImportJob(
            job_id=row["job_id"],
            user_id=row["user_id"],
            source_id=row["source_id"],
            status=ImportStatus(row["status"]),
            phase=ScanPhase(row["phase"]) if row["phase"] else None,
            checkpoint=row["checkpoint"],
            discovered=row["discovered"],
            discovered_targets=row["discovered_targets"],
            emitted=row["emitted"],
            stored=row["stored"],
            revised=row["revised"],
            merged=row["merged"],
            skipped=row["skipped"],
            errors=row["errors"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            operation=row["operation"],
            history_after=(
                datetime.fromisoformat(row["history_after"]) if row["history_after"] else None
            ),
        )
