#: Schema ownership note: the evidence schema is owned by
#: `memory_server.migrations` (migration 8), imported below. There is
#: deliberately no local copy — each compound index there records a
#: *measurement* on a real database rather than a guess, and a second copy
#: would let the two drift.
#:
#: Two runtime `ALTER TABLE ... ADD COLUMN` statements used to live here. They
#: are folded into the migration instead: A1-03 forbids a business module
#: altering its own schema on open, and doing it per-open is also what made
#: startup cost grow with the corpus.
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from one_memory.database_access import main_database, main_database_transaction
from one_memory.imports.models import (
    EvidenceContentKind,
    EvidenceInput,
    EvidenceOrigin,
    EvidenceRecord,
    OriginMetadata,
    WrittenBy,
)
from one_memory.memory_server.migrations import evidence_ddl
from one_memory.workspace_resolution import KnownWorkspace

_CURSOR_HOST_GENERATED_PREFIXES = (
    "<dynamic_tool_catalog>",
    "Briefly inform the user about the task result and perform any follow-up actions",
    "Perform any necessary follow-up actions in response to the subagent completion above",
)
_PIPELINE_JSON_SIGNATURE = re.compile(
    r'^\s*\{[\s\S]*"request_id"\s*:[\s\S]*'
    r'(?:(?:"type"\s*:\s*"(?:write|search|delete|digest)"[\s\S]*"steps"\s*:)|'
    r'(?:"reader"\s*:\s*"client"[\s\S]*"recommended_order"\s*:))',
    re.IGNORECASE,
)


_UNANSWERED_PREFIXES = (
    "<codex_internal_context",
    "<environment_context>",
    "<heartbeat>",
    "/goal ",
    "# agents.md instructions",
)


def _is_visible_user_row(role: str, written_by: str, verbatim: str) -> bool:
    del written_by  # Legacy storage metadata; protocol role is authoritative.
    if role != "user":
        return False
    return not verbatim.lstrip().lower().startswith(_UNANSWERED_PREFIXES)


def _is_visible_user_message(record: EvidenceRecord) -> bool:
    return _is_visible_user_row(
        record.role,
        record.written_by.value
        if isinstance(record.written_by, WrittenBy)
        else str(record.written_by),
        record.verbatim,
    )


def last_row_opens_an_unanswered_turn(
    last: tuple[str, str, str] | None,
) -> bool:
    """Whether a conversation ends on a question nobody answered.

    A turn is a question together with the answer to it. A conversation whose
    last row is the question is holding a turn that is still open; one whose
    last row is anything else is not. An assistant message, a tool result and a
    tool call each mean the turn received something back.

    A tool call counts as an answer on purpose. The assistant is mid-work at
    that point, and reading that as silence would mark most real conversations
    incomplete. The other error is the worse one: a memory drawn out of a
    question with no answer reads as settled, and it is not.
    """
    if last is None:
        return False
    role, written_by, verbatim = last
    return _is_visible_user_row(role, written_by, verbatim)


@dataclass(frozen=True)
class ConversationSummaryRecord:
    evidence_id: str
    source_id: str
    conversation_id: str
    workspace: str | None
    project: str | None
    title: str
    latest_at: datetime
    turn_count: int
    memory_count: int
    anchor_turn_offset: int = 0


@dataclass(frozen=True)
class ConversationTurnPage:
    records: tuple[EvidenceRecord, ...]
    turn_by_evidence_id: dict[str, int]
    total_turns: int
    offset: int
    limit: int
    #: The conversation ends on a question with no answer yet. The last turn is
    #: still open, so nothing about it is settled enough to draw a memory from.
    trailing_turn_unanswered: bool = False


#: The ordering index is left out on purpose: it is the one statement here
#: that costs seconds on a real corpus, and opening a database must not pay
#: for it inline. `ensure_imported_index_in_background` builds it instead.
SCHEMA = evidence_ddl("sqlite", without_imported_index=True)

#: Substring search over imported conversations, for the stretch before a
#: generation model exists. Deliberately not the vector store: that one is
#: written by extraction, and a user with no generation model has never run
#: any, so a semantic path would search an empty corpus however it was wired.
#:
#: External content, so the text is stored once and the index holds only
#: postings; triggers rather than Python, so the index stays true whichever
#: code path writes. Trigram because Chinese has no word boundaries and
#: `unicode61` would make each sentence a single token.
#:
#: Measured on 90,000 rows of 8.6M characters: 0.85s to build, 27MB of index
#: on 24MB of text, and 1-5ms per query against 21-54ms for the scan it
#: replaces.
SEARCH_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_search USING fts5(
    verbatim,
    content='evidence',
    content_rowid='rowid',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS evidence_search_insert AFTER INSERT ON evidence BEGIN
    INSERT INTO evidence_search(rowid, verbatim) VALUES (new.rowid, new.verbatim);
END;

CREATE TRIGGER IF NOT EXISTS evidence_search_delete AFTER DELETE ON evidence BEGIN
    INSERT INTO evidence_search(evidence_search, rowid, verbatim)
    VALUES ('delete', old.rowid, old.verbatim);
END;

CREATE TRIGGER IF NOT EXISTS evidence_search_update AFTER UPDATE ON evidence BEGIN
    INSERT INTO evidence_search(evidence_search, rowid, verbatim)
    VALUES ('delete', old.rowid, old.verbatim);
    INSERT INTO evidence_search(rowid, verbatim) VALUES (new.rowid, new.verbatim);
END;
"""

#: Below this, the trigram index holds nothing at all. Two Chinese characters
#: is an ordinary query, so those fall back to a scan rather than to silence.
TRIGRAM_FLOOR = 3

# Cursor also runs ~/.claude/settings.json hooks, so before capture learned to
# skip them each Cursor turn was filed a second time under claude-code with
# Cursor's conversation id. Those rows stay stored; lists show the Cursor copy.
CURSOR_REPLAYED_THROUGH_CLAUDE_HOOKS = """
 AND NOT (source_id = 'claude-code' AND EXISTS (
    SELECT 1 FROM evidence AS cursor_copy
    WHERE cursor_copy.user_id = evidence.user_id
      AND cursor_copy.source_id = 'cursor'
      AND cursor_copy.conversation_id = evidence.conversation_id
      AND cursor_copy.workspace IS evidence.workspace))"""


def _journal_enabled(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_ingest_events'"
        ).fetchone()
        is not None
    )


@contextmanager
def _journal_put(connection: sqlite3.Connection) -> Iterator[bool]:
    enabled = _journal_enabled(connection)
    if not enabled:
        yield False
        return
    if not connection.in_transaction:
        raise RuntimeError("journaled_put_requires_caller_transaction")
    from one_memory import ingest_journal

    ingest_journal.watermark(connection)  # An incomplete journal must fail before writing.
    savepoint = "journaled_put_" + uuid4().hex
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield True
    except BaseException:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE {savepoint}")


def _journal_maintenance(connection: sqlite3.Connection, evidence_ids: tuple[str, ...]) -> None:
    if evidence_ids and _journal_enabled(connection):
        from one_memory import ingest_journal

        for evidence_id in dict.fromkeys(evidence_ids):
            ingest_journal.record(connection, evidence_id, cause="maintenance")


@dataclass(frozen=True)
class PutEvidenceResult:
    record: EvidenceRecord
    was_created: bool
    was_revised: bool


@dataclass(frozen=True)
class PendingMemoryDispatch:
    record: EvidenceRecord
    priority: Literal["normal", "live"]


class ConversationSearchUnavailableError(RuntimeError):
    """The index is not there, which is not the same as finding nothing.

    Both come back as an empty list, and that is the shape the packaging bug
    took: a core shipped without `onnxruntime` looked, on screen, exactly like
    a user who had never mentioned what they searched for. Raising keeps the
    two apart all the way to the interface.
    """


class EvidenceStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        from one_memory.change_outbox import ChangeOutbox

        # Keep the legacy table readable by ChangeOutbox and SessionInbox while
        # evidence imports no longer append to the dormant notification path.
        ChangeOutbox(database_path).initialize()
        with self._connect(initialize=True) as connection:
            connection.executescript(SCHEMA)
            # `source_ordinal` and `content_kind` used to be added here with
            # ALTER TABLE on every open. Migration 8 declares them, so an
            # up-to-date database needs nothing; a genuinely old database is
            # migrated rather than patched per-open.
            try:
                connection.executescript(SEARCH_SCHEMA)
            except sqlite3.OperationalError:
                # A SQLite built without FTS5. Everything else here still
                # works, so refuse the search rather than the whole store —
                # and refuse it loudly, in `search_conversations`.
                logging.getLogger(__name__).warning("conversation_search_index_unavailable")
        with self._connect(writing=True) as connection:
            self._add_legacy_columns(connection)
        self._apply_pending_repairs()

    #: The index that `list_recent_conversations` orders by. Built in the
    #: background when missing, because on a real corpus it costs seconds and
    #: the app must not sit unresponsive for them.
    _IMPORTED_INDEX = "evidence_imported_index"
    _IMPORTED_INDEX_REPAIR = "evidence_imported_index"
    _IMPORTED_INDEX_DDL = (
        "CREATE INDEX IF NOT EXISTS evidence_imported_index "
        "ON evidence(user_id, imported_at DESC, evidence_id DESC)"
    )

    #: Backfills of rows an older build wrote, newest last. See
    #: `evidence_repairs` for why each runs only once.
    _REPAIRS = (
        "cursor_host_authorship",
        "cursor_conversation_attribution",
        "conversation_search_index",
    )

    #: Columns an up-to-date schema declares but an older database may lack.
    #: Adding a column SQLite already has raises, so each is checked before it
    #: is added. Unlike the repairs above this needs no row backfill: a missing
    #: ordinal means "no stable source position was recorded", which is exactly
    #: what a null means.
    _LEGACY_COLUMN_ADDITIONS = (("evidence", "source_ordinal", "TEXT"),)

    def _add_legacy_columns(self, connection: sqlite3.Connection) -> None:
        for table, column, declaration in self._LEGACY_COLUMN_ADDITIONS:
            existing = {
                str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if column in existing:
                continue
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _apply_pending_repairs(self) -> None:
        """Carry out any backfill this database has not had yet.

        Deliberately outside the schema transaction. Two processes opening the
        same database at the same moment is ordinary here — the app's Core and
        a developer's — and a transaction that has already read cannot wait for
        another writer: SQLite refuses the upgrade outright rather than
        honouring the busy timeout. Asking on a read first also means the
        ordinary case, where every repair is long done, takes no write lock at
        all.
        """
        with self._connect(writing=True) as connection:
            done = {
                str(row["name"]) for row in connection.execute("SELECT name FROM evidence_repairs")
            }
        pending = [name for name in self._REPAIRS if name not in done]
        if not pending:
            return

        repairs: dict[str, Callable[[sqlite3.Connection], None]] = {
            "cursor_host_authorship": self._migrate_cursor_host_authorship,
            "cursor_conversation_attribution": self._migrate_cursor_conversation_attribution,
            "conversation_search_index": self._build_conversation_search_index,
        }
        for name in pending:
            # Generous, because this waits for another process doing the very
            # same backfill, and the work itself is a scan of the corpus.
            with main_database(self.database_path).write(
                autocommit=True,
                foreign_keys=True,
                row_factory=sqlite3.Row,
                busy_timeout_ms=60_000,
            ) as connection:
                # A writer from the first statement, so the busy timeout
                # applies; and one transaction per repair, so the record and
                # the work commit together and an interrupted one is retried
                # rather than recorded.
                connection.execute("BEGIN IMMEDIATE")
                try:
                    claimed = connection.execute(
                        "INSERT INTO evidence_repairs (name, applied_at) VALUES (?, ?) "
                        "ON CONFLICT(name) DO NOTHING",
                        (name, datetime.now(UTC).isoformat()),
                    )
                    if claimed.rowcount == 1:
                        repairs[name](connection)
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise

    def has_imported_index(self) -> bool:
        """Whether the ordering index `list_recent_conversations` needs exists."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (self._IMPORTED_INDEX,),
            ).fetchone()
        return row is not None

    def ensure_imported_index_in_background(self) -> bool:
        """Start building the ordering index without blocking the caller.

        Returns True when a build was started, False when the index is already
        there or a build is already recorded. The caller goes on serving
        requests either way: until the index lands the query is slow but
        correct, and afterwards it is fast — no state in between is wrong.

        Building it inline was measured at 9.28 s on 432k rows, all of it
        inside `EvidenceStore.__init__`, which is on Core's startup path.
        A desktop app cannot be unresponsive for that long the first time a
        user opens an upgraded build.
        """
        if not self._claim_imported_index_repair():
            return False
        thread = threading.Thread(
            target=self._build_imported_index,
            name="one-memory-imported-index",
            daemon=True,
        )
        thread.start()
        return True

    def _claim_imported_index_repair(self) -> bool:
        """Take the one-time slot for this build, so two processes do not race.

        Mirrors `_apply_pending_repairs`: the record and the claim commit
        together, so a build interrupted by a crash is retried rather than
        being recorded as done.
        """
        try:
            with main_database(self.database_path).write(
                autocommit=True,
                foreign_keys=True,
                row_factory=sqlite3.Row,
                busy_timeout_ms=60_000,
            ) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if self.has_imported_index():
                        connection.execute("COMMIT")
                        return False
                    done = connection.execute(
                        "SELECT 1 FROM evidence_repairs WHERE name=?",
                        (self._IMPORTED_INDEX_REPAIR,),
                    ).fetchone()
                    if done is not None:
                        connection.execute("COMMIT")
                        return False
                    connection.execute(
                        "INSERT INTO evidence_repairs (name, applied_at) VALUES (?, ?)",
                        (self._IMPORTED_INDEX_REPAIR, datetime.now(UTC).isoformat()),
                    )
                    connection.execute("COMMIT")
                    return True
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
        except sqlite3.Error as error:
            logging.getLogger(__name__).warning(
                "imported_index_claim_failed %s: %s", type(error).__name__, error
            )
            return False

    def _build_imported_index(self) -> None:
        started = time.monotonic()
        try:
            with main_database(self.database_path).write(
                autocommit=True,
                foreign_keys=True,
                row_factory=sqlite3.Row,
                busy_timeout_ms=120_000,
            ) as connection:
                connection.execute(self._IMPORTED_INDEX_DDL)
        except BaseException as error:
            # Recorded as claimed, so a later start retries rather than
            # believing it is done. Removing the record here would let two
            # processes retry forever against a permanently failing database.
            logging.getLogger(__name__).warning(
                "imported_index_build_failed %s: %s", type(error).__name__, error
            )
            return
        logging.getLogger(__name__).info(
            "imported_index_built seconds=%.3f", time.monotonic() - started
        )

    def _connect(
        self,
        *,
        initialize: bool = False,
        writing: bool = False,
    ) -> AbstractContextManager[sqlite3.Connection]:
        return main_database_transaction(self.database_path, initialize=initialize, writing=writing)

    def put(
        self, evidence: EvidenceInput, *, connection: sqlite3.Connection | None = None
    ) -> PutEvidenceResult:
        now = datetime.now(UTC).isoformat()
        was_created = False
        was_revised = False
        transaction = (
            nullcontext(connection) if connection is not None else self._connect(writing=True)
        )
        with transaction as connection, _journal_put(connection) as journal_enabled:
            anchored = self._anchor_existing(connection, evidence, now)
            if anchored is not None:
                self._write_origins(connection, anchored, evidence.origin_meta, now)
                workspace, project = self._resolve_conversation_attribution(connection, evidence)
                existing = connection.execute(
                    "SELECT role,revision FROM evidence WHERE evidence_id=?",
                    (anchored,),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("anchored evidence disappeared")
                if str(existing["role"]) != evidence.role:
                    was_revised = True
                    revision = int(existing["revision"]) + 1
                    connection.execute(
                        """
                        UPDATE evidence
                        SET workspace=COALESCE(workspace,?),
                            project=COALESCE(project,?),
                            content_kind=?,role=?,created_at=COALESCE(?,created_at),
                            verbatim=?,content_hash=?,revision=?,updated_at=?
                        WHERE evidence_id=?
                        """,
                        (
                            workspace,
                            project,
                            evidence.content_kind,
                            evidence.role,
                            evidence.created_at.isoformat() if evidence.created_at else None,
                            evidence.verbatim,
                            evidence.content_hash,
                            revision,
                            now,
                            anchored,
                        ),
                    )
                    self._insert_current_version(
                        connection,
                        anchored,
                        revision=revision,
                        changed_at=now,
                    )
                else:
                    connection.execute(
                        """
                        UPDATE evidence
                        SET workspace=COALESCE(workspace,?),
                            project=COALESCE(project,?),
                            updated_at=?
                        WHERE evidence_id=?
                        """,
                        (workspace, project, now, anchored),
                    )
                record = self._get_with_connection(connection, anchored)
                if record is None:
                    raise RuntimeError("anchored evidence disappeared")
                if journal_enabled:
                    from one_memory import ingest_journal

                    ingest_journal.record(connection, anchored, cause="import")
                return PutEvidenceResult(
                    record=record,
                    was_created=False,
                    was_revised=was_revised,
                )
            insert = connection.execute(
                """
                INSERT INTO evidence (
                    evidence_id, idempotency_key, user_id, source_id, workspace,
                    project, conversation_id, message_id, correlation_id,
                    source_ordinal, content_kind, role, written_by,
                    created_at, verbatim, content_hash, revision,
                    imported_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    evidence.evidence_id,
                    evidence.idempotency_key,
                    evidence.user_id,
                    evidence.source_id,
                    evidence.workspace,
                    evidence.project,
                    evidence.conversation_id,
                    evidence.message_id,
                    evidence.correlation_id,
                    evidence.source_ordinal,
                    evidence.content_kind,
                    evidence.role,
                    evidence.written_by,
                    evidence.created_at.isoformat() if evidence.created_at else None,
                    evidence.verbatim,
                    evidence.content_hash,
                    now,
                    now,
                ),
            )
            was_created = insert.rowcount == 1
            existing = connection.execute(
                "SELECT * FROM evidence WHERE idempotency_key = ?",
                (evidence.idempotency_key,),
            ).fetchone()
            if existing is None:
                raise RuntimeError("Evidence insert did not return its claimed identity")
            workspace, project = self._resolve_conversation_attribution(connection, evidence)
            if was_created:
                self._insert_current_version(
                    connection,
                    evidence.evidence_id,
                    revision=1,
                    changed_at=now,
                )
            elif self._has_revision_change(existing, evidence):
                was_revised = True
                revision = int(existing["revision"]) + 1
                connection.execute(
                    """
                    UPDATE evidence
                    SET workspace = COALESCE(?, workspace),
                        project = COALESCE(?, project),
                        message_id = COALESCE(message_id, ?),
                        correlation_id = COALESCE(correlation_id, ?),
                        content_kind = ?,
                        role = ?,
                        written_by = CASE WHEN ? = 'unknown' THEN written_by ELSE ? END,
                        created_at = COALESCE(?, created_at),
                        verbatim = ?, content_hash = ?,
                        revision = ?, updated_at = ?
                    WHERE evidence_id = ?
                    """,
                    (
                        workspace,
                        project,
                        evidence.message_id,
                        evidence.correlation_id,
                        evidence.content_kind,
                        evidence.role,
                        evidence.written_by,
                        evidence.written_by,
                        evidence.created_at.isoformat() if evidence.created_at else None,
                        evidence.verbatim,
                        evidence.content_hash,
                        revision,
                        now,
                        evidence.evidence_id,
                    ),
                )
                self._insert_current_version(
                    connection,
                    evidence.evidence_id,
                    revision=revision,
                    changed_at=now,
                )

            if not was_created and not was_revised:
                connection.execute(
                    """
                    UPDATE evidence
                    SET workspace = COALESCE(?, workspace),
                        project = COALESCE(?, project),
                        message_id = COALESCE(message_id, ?),
                        correlation_id = COALESCE(correlation_id, ?),
                        content_kind = ?,
                        written_by = CASE WHEN ? = 'unknown' THEN written_by ELSE ? END,
                        created_at = COALESCE(?, created_at),
                        updated_at = ?
                    WHERE evidence_id = ?
                    """,
                    (
                        workspace,
                        project,
                        evidence.message_id,
                        evidence.correlation_id,
                        evidence.content_kind,
                        evidence.written_by,
                        evidence.written_by,
                        evidence.created_at.isoformat() if evidence.created_at else None,
                        now,
                        evidence.evidence_id,
                    ),
                )
            attributed_ids = self._propagate_conversation_attribution(
                connection,
                evidence=evidence,
                workspace=workspace,
                project=project,
                updated_at=now,
            )
            self._write_origins(connection, evidence.evidence_id, evidence.origin_meta, now)

            if journal_enabled:
                from one_memory import ingest_journal

                for evidence_id in attributed_ids:
                    if evidence_id != evidence.evidence_id:
                        ingest_journal.record(connection, evidence_id, cause="maintenance")
                ingest_journal.record(connection, evidence.evidence_id, cause="import")
            record = self._get_with_connection(connection, evidence.evidence_id)
        if record is None:
            raise RuntimeError("Evidence disappeared after an atomic write")
        return PutEvidenceResult(record, was_created, was_revised)

    @staticmethod
    def _conversation_labels(
        connection: sqlite3.Connection, evidence: EvidenceInput
    ) -> tuple[set[str], set[str]]:
        """The conversation's distinct workspaces and projects, as far as two of each.

        Callers only ask whether there is none, exactly one, or more, so the
        smallest and largest value answer it through an index. Reading every
        label instead made each imported row cost the length of its conversation.
        """
        key = (evidence.user_id, evidence.source_id, evidence.conversation_id)
        found: list[set[str]] = []
        for column in ("workspace", "project"):
            values = set()
            for aggregate in ("MIN", "MAX"):
                row = connection.execute(
                    f"SELECT {aggregate}({column}) FROM evidence "
                    "WHERE user_id = ? AND source_id = ? AND conversation_id = ? "
                    f"AND {column} IS NOT NULL",
                    key,
                ).fetchone()
                if row[0] is not None:
                    values.add(str(row[0]))
            found.append(values)
        return found[0], found[1]

    @staticmethod
    def _resolve_conversation_attribution(
        connection: sqlite3.Connection,
        evidence: EvidenceInput,
    ) -> tuple[str | None, str | None]:
        workspaces, projects = EvidenceStore._conversation_labels(connection, evidence)
        workspace = evidence.workspace
        project = evidence.project
        if workspace is None and len(workspaces) == 1:
            workspace = next(iter(workspaces))
        if project is None and len(projects) == 1:
            project = next(iter(projects))
        return workspace, project

    @staticmethod
    def _propagate_conversation_attribution(
        connection: sqlite3.Connection,
        *,
        evidence: EvidenceInput,
        workspace: str | None,
        project: str | None,
        updated_at: str,
    ) -> tuple[str, ...]:
        if workspace is None and project is None:
            return ()
        workspaces, projects = EvidenceStore._conversation_labels(connection, evidence)
        safe_workspace = workspace if workspace is not None and workspaces == {workspace} else None
        safe_project = project if project is not None and projects == {project} else None
        if safe_workspace is None and safe_project is None:
            return ()
        changed: dict[str, None] = {}
        # One statement per missing column, so each reaches only its gaps
        # through an index instead of walking the whole conversation.
        for column, value in (("workspace", safe_workspace), ("project", safe_project)):
            if value is None:
                continue
            rows = connection.execute(
                f"""
                UPDATE evidence
                SET workspace = COALESCE(workspace, ?),
                    project = COALESCE(project, ?),
                    updated_at = ?
                WHERE user_id = ? AND source_id = ? AND conversation_id = ?
                  AND {column} IS NULL
                RETURNING evidence_id
                """,
                (
                    safe_workspace,
                    safe_project,
                    updated_at,
                    evidence.user_id,
                    evidence.source_id,
                    evidence.conversation_id,
                ),
            ).fetchall()
            changed.update(dict.fromkeys(str(row["evidence_id"]) for row in rows))
        return tuple(changed)

    @staticmethod
    def _anchor_existing(
        connection: sqlite3.Connection, evidence: EvidenceInput, now: str
    ) -> str | None:
        """One hook row with this text becomes the file's message instead of a second copy."""

        def claim_legacy_key(legacy_key: str | None) -> str | None:
            if legacy_key is None:
                return None
            legacy = connection.execute(
                """
                SELECT evidence_id, workspace FROM evidence
                WHERE idempotency_key = ?
                """,
                (legacy_key,),
            ).fetchone()
            # A hook captures before the workspace is known, so a hook-shaped
            # replay of a file row arrives with no workspace of its own. Match
            # on identity first and let attribution fill in afterwards.
            if legacy is None:
                return None
            if evidence.workspace is not None and legacy["workspace"] not in {
                None,
                evidence.workspace,
            }:
                return None
            return str(legacy["evidence_id"])

        if evidence.message_id:
            candidates = connection.execute(
                """
                SELECT evidence_id, idempotency_key, workspace FROM evidence
                WHERE user_id = ? AND source_id = ? AND conversation_id = ?
                  AND message_id = ?
                ORDER BY CASE WHEN workspace IS ? THEN 0 ELSE 1 END
                LIMIT 3
                """,
                (
                    evidence.user_id,
                    evidence.source_id,
                    evidence.conversation_id,
                    evidence.message_id,
                    evidence.workspace,
                ),
            ).fetchall()
            same_scope = next(
                (row for row in candidates if row["idempotency_key"] == evidence.idempotency_key),
                None,
            )
            # The exact identity goes through the normal revise path. Otherwise
            # a unique source/conversation/message row is the same host message
            # seen first by a hook and later by its transcript file. Adopt it
            # while the file supplies the missing workspace and origin.
            if same_scope is not None:
                return None
            matching_workspace = [
                row for row in candidates if row["workspace"] == evidence.workspace
            ]
            if len(matching_workspace) == 1:
                return str(matching_workspace[0]["evidence_id"])
            if len(candidates) == 1 and (
                candidates[0]["workspace"] is None or evidence.workspace is None
            ):
                return str(candidates[0]["evidence_id"])
            legacy_id = claim_legacy_key(evidence.legacy_message_idempotency_key)
            if legacy_id is not None:
                return legacy_id
        elif evidence.correlation_id:
            # A hook sees the correlation id before the file supplies its
            # workspace. That legacy bridge is safe only when exactly one row
            # has that id; two project-scoped rows must never be guessed.
            rows = connection.execute(
                """
                SELECT evidence_id, idempotency_key, workspace FROM evidence
                WHERE user_id = ? AND source_id = ? AND conversation_id = ?
                  AND (correlation_id = ? OR message_id = ?)
                """,
                (
                    evidence.user_id,
                    evidence.source_id,
                    evidence.conversation_id,
                    evidence.correlation_id,
                    evidence.correlation_id,
                ),
            ).fetchall()
            same_scope = [row for row in rows if row["workspace"] == evidence.workspace]
            if evidence.workspace is None and len(rows) == 1:
                # A hook that predates workspace discovery may merge into the
                # one later file row. This is the inverse legacy direction and
                # is safe only while the correlation has no competing row.
                same_scope = list(rows)
            if (
                len(same_scope) == 1
                and same_scope[0]["idempotency_key"] != evidence.idempotency_key
            ):
                return str(same_scope[0]["evidence_id"])
        elif (
            legacy_id := next(
                (
                    anchored
                    for key in (
                        *evidence.written_by_derived_v3_idempotency_keys,
                        *evidence.older_written_by_idempotency_keys,
                    )
                    if (anchored := claim_legacy_key(key)) is not None
                ),
                None,
            )
        ) is not None:
            return legacy_id
        if (
            EvidenceOrigin.HOOK in evidence.origins
            and evidence.message_id is None
            and evidence.created_at is not None
        ):
            rows = connection.execute(
                """
                SELECT e.evidence_id FROM evidence e
                JOIN evidence_origins file
                  ON file.evidence_id=e.evidence_id AND file.origin='file'
                LEFT JOIN evidence_origins hook
                  ON hook.evidence_id=e.evidence_id AND hook.origin='hook'
                WHERE e.user_id=? AND e.source_id=? AND e.conversation_id=?
                  AND (e.workspace IS ? OR e.workspace IS NULL OR ? IS NULL)
                  AND e.role=? AND e.content_hash=?
                  AND e.message_id IS NOT NULL AND hook.evidence_id IS NULL
                  AND ABS((julianday(e.created_at)-julianday(?))*86400.0) <= 5.0
                ORDER BY ABS((julianday(e.created_at)-julianday(?))*86400.0), e.evidence_id
                LIMIT 2
                """,
                (
                    evidence.user_id,
                    evidence.source_id,
                    evidence.conversation_id,
                    evidence.workspace,
                    evidence.workspace,
                    evidence.role,
                    evidence.content_hash,
                    evidence.created_at.isoformat(),
                    evidence.created_at.isoformat(),
                ),
            ).fetchall()
            if len(rows) == 1:
                return str(rows[0]["evidence_id"])
        if EvidenceOrigin.FILE not in evidence.origins or not evidence.message_id:
            return None
        rows = connection.execute(
            """
            SELECT e.evidence_id FROM evidence e
            JOIN evidence_origins o ON o.evidence_id = e.evidence_id AND o.origin = 'hook'
            WHERE e.user_id = ? AND e.source_id = ? AND e.conversation_id = ?
              -- A hook fires before the workspace is known, so a stored hook row
              -- has none. Requiring an exact match here is what left one message
              -- as two rows whenever the file scan learned the project later.
              AND (e.workspace IS ? OR e.workspace IS NULL OR ? IS NULL)
              -- Unary plus keeps the coverage index from looking better than
              -- the message index, which narrows to this exact text.
              AND e.role = ? AND e.content_hash = ?
              AND e.message_id IS NULL
            """,
            (
                evidence.user_id,
                evidence.source_id,
                evidence.conversation_id,
                evidence.workspace,
                evidence.workspace,
                evidence.role,
                evidence.content_hash,
            ),
        ).fetchall()
        if len(rows) != 1:
            return None
        evidence_id = str(rows[0]["evidence_id"])
        # The hook copy only knew when it was written; the file knows where the
        # message sits. Adopt that once, here, where message_id changes anyway.
        connection.execute(
            """
            UPDATE evidence
            SET message_id = ?,
                created_at = COALESCE(?, created_at),
                source_ordinal = COALESCE(?, source_ordinal),
                updated_at = ?
            WHERE evidence_id = ?
            """,
            (
                evidence.message_id,
                evidence.created_at.isoformat() if evidence.created_at else None,
                evidence.source_ordinal,
                now,
                evidence_id,
            ),
        )
        return evidence_id

    @staticmethod
    def _write_origins(
        connection: sqlite3.Connection,
        evidence_id: str,
        origin_meta: dict[EvidenceOrigin, OriginMetadata],
        now: str,
    ) -> None:
        for origin, origin_metadata in origin_meta.items():
            raw_meta_json = json.dumps(
                origin_metadata.raw_meta,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            connection.execute(
                """
                INSERT INTO evidence_origins (
                    evidence_id, origin, raw_meta_json, source_version, observed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id, origin) DO UPDATE SET
                    raw_meta_json = excluded.raw_meta_json,
                    source_version = excluded.source_version,
                    observed_at = excluded.observed_at
                """,
                (
                    evidence_id,
                    origin,
                    raw_meta_json,
                    origin_metadata.source_version,
                    now,
                ),
            )

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        with self._connect() as connection:
            return self._get_with_connection(connection, evidence_id)

    def get_many(self, evidence_ids: list[str]) -> list[EvidenceRecord]:
        """Hydrate a page in two queries, preserving the requested order.

        The single-record `get` is a round trip each; callers that need a
        whole set — the memory map needs every citation of every memory it
        draws — should not pay one connection checkout per id.
        """
        if not evidence_ids:
            return []
        with self._connect() as connection:
            return self._get_many_with_connection(connection, list(dict.fromkeys(evidence_ids)))

    @staticmethod
    def _get_with_connection(
        connection: sqlite3.Connection,
        evidence_id: str,
    ) -> EvidenceRecord | None:
        row = connection.execute(
            "SELECT * FROM evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            return None
        origin_rows = connection.execute(
            """
            SELECT origin, raw_meta_json, source_version
            FROM evidence_origins
            WHERE evidence_id = ?
            ORDER BY origin
            """,
            (evidence_id,),
        ).fetchall()
        origin_meta = {
            EvidenceOrigin(item["origin"]): OriginMetadata(
                raw_meta=json.loads(item["raw_meta_json"]),
                source_version=item["source_version"],
            )
            for item in origin_rows
        }
        return EvidenceRecord(
            evidence_id=row["evidence_id"],
            user_id=row["user_id"],
            source_id=row["source_id"],
            workspace=row["workspace"],
            project=row["project"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            correlation_id=row["correlation_id"],
            source_ordinal=row["source_ordinal"],
            content_kind=EvidenceContentKind(row["content_kind"]),
            role=row["role"],
            written_by=WrittenBy(row["written_by"]),
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            verbatim=row["verbatim"],
            origin_meta=origin_meta,
            content_hash=row["content_hash"],
            revision=row["revision"],
            imported_at=datetime.fromisoformat(row["imported_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _get_many_with_connection(
        connection: sqlite3.Connection,
        evidence_ids: list[str],
    ) -> list[EvidenceRecord]:
        """Hydrate a page with two queries, preserving the requested order.

        The recent-conversation path is a hot API read. Hydrating each of its
        rows separately turns a page into an N+1 query pattern, so fetch the
        page and its origins in bulk, then assemble records in memory.
        """
        if not evidence_ids:
            return []
        placeholders = ",".join("?" for _ in evidence_ids)
        rows = connection.execute(
            f"SELECT * FROM evidence WHERE evidence_id IN ({placeholders})",
            evidence_ids,
        ).fetchall()
        rows_by_id = {str(row["evidence_id"]): row for row in rows}
        origin_rows = connection.execute(
            f"""
            SELECT evidence_id, origin, raw_meta_json, source_version
            FROM evidence_origins
            WHERE evidence_id IN ({placeholders})
            ORDER BY evidence_id, origin
            """,
            evidence_ids,
        ).fetchall()
        origins_by_id: dict[str, dict[EvidenceOrigin, OriginMetadata]] = {
            evidence_id: {} for evidence_id in evidence_ids
        }
        for item in origin_rows:
            origins_by_id[str(item["evidence_id"])][EvidenceOrigin(item["origin"])] = (
                OriginMetadata(
                    raw_meta=json.loads(item["raw_meta_json"]),
                    source_version=item["source_version"],
                )
            )
        records: list[EvidenceRecord] = []
        for evidence_id in evidence_ids:
            row = rows_by_id.get(evidence_id)
            if row is None:
                continue
            records.append(
                EvidenceRecord(
                    evidence_id=row["evidence_id"],
                    user_id=row["user_id"],
                    source_id=row["source_id"],
                    workspace=row["workspace"],
                    project=row["project"],
                    conversation_id=row["conversation_id"],
                    message_id=row["message_id"],
                    correlation_id=row["correlation_id"],
                    source_ordinal=row["source_ordinal"],
                    content_kind=EvidenceContentKind(row["content_kind"]),
                    role=row["role"],
                    written_by=WrittenBy(row["written_by"]),
                    created_at=(
                        datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
                    ),
                    verbatim=row["verbatim"],
                    origin_meta=origins_by_id[evidence_id],
                    content_hash=row["content_hash"],
                    revision=row["revision"],
                    imported_at=datetime.fromisoformat(row["imported_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
            )
        return records

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM evidence").fetchone()
        return int(row["count"]) if row else 0

    def source_totals(self, *, user_id: str, source_id: str) -> tuple[int, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS messages,
                    COUNT(DISTINCT conversation_id || char(0) || COALESCE(workspace, ''))
                        AS conversations
                FROM evidence
                WHERE user_id = ? AND source_id = ?
                """,
                (user_id, source_id),
            ).fetchone()
        if row is None:
            return (0, 0)
        return (int(row["messages"]), int(row["conversations"]))

    def high_water_mark(self) -> int:
        """A number that changes exactly when a row is added.

        Cheap on purpose — the largest rowid comes straight off the table's
        own index — so that a caller can ask "is anything different" without
        paying for the answer it would otherwise recompute.
        """
        with self._connect() as connection:
            row = connection.execute("SELECT MAX(rowid) AS mark FROM evidence").fetchone()
        return int(row["mark"] or 0)

    def source_totals_by_source(self, *, user_id: str) -> dict[str, tuple[int, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    source_id,
                    COUNT(*) AS messages,
                    COUNT(DISTINCT conversation_id || char(0) || COALESCE(workspace, ''))
                        AS conversations
                FROM evidence
                WHERE user_id = ?
                GROUP BY source_id
                """,
                (user_id,),
            ).fetchall()
        return {
            str(row["source_id"]): (int(row["messages"]), int(row["conversations"])) for row in rows
        }

    def known_workspaces(self, *, user_id: str) -> list[KnownWorkspace]:
        """The workspaces imports have seen, with the label each host used.

        Recall needs this because a host that names its workspaces itself —
        Cursor does — stores an identity no caller can compute. The plaintext
        label recorded alongside it is what makes the translation possible.

        It answers from `evidence_workspace_index` alone. That is load-bearing,
        not incidental: the answer is 131 rows on this machine but the rows it
        is drawn from are 87,689, and reading them instead cost 4.2-7.1 seconds
        per recall. See the index's own note; a test pins the query plan.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_id, workspace, project
                FROM evidence
                WHERE user_id = ? AND workspace IS NOT NULL
                GROUP BY source_id, workspace, project
                """,
                (user_id,),
            ).fetchall()
        return [
            KnownWorkspace(
                source_id=str(row["source_id"]),
                workspace=str(row["workspace"]),
                project=str(row["project"]) if row["project"] is not None else None,
            )
            for row in rows
        ]

    def search_conversations(
        self,
        query: str,
        *,
        user_id: str,
        limit: int = 20,
        source_ids: tuple[str, ...] | None = None,
    ) -> list[EvidenceRecord]:
        """Find imported messages containing `query`, newest first.

        Ordered by when the message was said rather than by relevance score.
        The match is a substring either way, so bm25 would rank on how often
        a phrase repeats in one message, which says nothing a reader wants;
        recency at least answers "when did I last talk about this".
        """
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        needle = query.strip()
        if not needle:
            return []
        if source_ids is not None and not source_ids:
            return []
        source_clause = (
            " AND e.source_id IN (" + ",".join("?" for _ in source_ids) + ")"
            if source_ids is not None
            else ""
        )
        source_parameters = source_ids or ()
        with self._connect() as connection:
            if not self._search_index_exists(connection):
                raise ConversationSearchUnavailableError("conversation_search_unavailable")
            if len(needle) >= TRIGRAM_FLOOR:
                rows = connection.execute(
                    f"""
                    SELECT e.evidence_id FROM evidence_search
                    JOIN evidence e ON e.rowid = evidence_search.rowid
                    WHERE evidence_search MATCH ? AND e.user_id = ?
                    {source_clause}
                    ORDER BY julianday(e.created_at) DESC, e.evidence_id
                    LIMIT ?
                    """,
                    (_as_fts_phrase(needle), user_id, *source_parameters, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT e.evidence_id FROM evidence e
                    WHERE e.user_id = ? AND e.verbatim LIKE ? ESCAPE '\\'
                    {source_clause}
                    ORDER BY julianday(e.created_at) DESC, e.evidence_id
                    LIMIT ?
                    """,
                    (user_id, f"%{_as_like_pattern(needle)}%", *source_parameters, limit),
                ).fetchall()
            records: list[EvidenceRecord] = []
            for row in rows:
                record = self._get_with_connection(connection, row["evidence_id"])
                if record is not None:
                    records.append(record)
            return records

    @staticmethod
    def _search_index_exists(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'evidence_search'"
        ).fetchone()
        return row is not None

    def _build_conversation_search_index(self, connection: sqlite3.Connection) -> None:
        """Index what earlier builds imported, which is every real library.

        Everyone who reaches this feature already has their history on disk,
        so an index covering only rows written after it existed would leave
        them all searching nothing. Measured at 0.85s over 90,000 rows.
        """
        if not self._search_index_exists(connection):
            return
        connection.execute("INSERT INTO evidence_search(evidence_search) VALUES ('rebuild')")

    def list_extractable(self, *, limit: int = 10_000) -> list[EvidenceRecord]:
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence_id FROM evidence
                WHERE user_id = 'local' AND role = 'user'
                ORDER BY imported_at, evidence_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            records: list[EvidenceRecord] = []
            for row in rows:
                record = self._get_with_connection(connection, row["evidence_id"])
                if record is not None:
                    records.append(record)
            return records

    def list_personal_candidates(self, *, limit: int = 10_000) -> list[EvidenceRecord]:
        """Return a cheap SQL superset of stable personal-memory candidates."""
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        signals = (
            "我是一名",
            "我是一个",
            "我叫",
            "我姓",
            "我喜欢",
            "我偏好",
            "我讨厌",
            "我不喜欢",
            "我习惯",
            "我通常",
            "我总是",
            "我觉得",
            "我的偏好",
            "我的习惯",
            "我的背景",
            "请记住",
            "记一下",
            "以后",
            "每次",
            "默认",
            "i am a",
            "i'm a",
            "i like",
            "i prefer",
            "i dislike",
            "i usually",
            "i always",
            "my preference",
            "my background",
            "remember that",
        )
        predicates = " OR ".join("LOWER(verbatim) LIKE ?" for _ in signals)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT evidence_id FROM evidence
                WHERE user_id = 'local' AND role = 'user'
                  AND ({predicates})
                ORDER BY imported_at, evidence_id
                LIMIT ?
                """,
                (*[f"%{signal}%" for signal in signals], limit),
            ).fetchall()
            records: list[EvidenceRecord] = []
            for row in rows:
                record = self._get_with_connection(connection, row["evidence_id"])
                if record is not None:
                    records.append(record)
            return records

    def list_workspace_candidates(self, *, limit: int = 10_000) -> list[EvidenceRecord]:
        """Return explicit project-memory cues that have a stable workspace scope."""
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        signals = (
            "这个可以记一下",
            "这可以记一下",
            "可以记录结论",
            "结论可以记录",
            "已经定稿",
            "就这么定",
            "最终确定",
        )
        predicates = " OR ".join("verbatim LIKE ?" for _ in signals)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT evidence_id FROM evidence
                WHERE user_id = 'local' AND role = 'user'
                  AND workspace IS NOT NULL
                  AND ({predicates})
                ORDER BY imported_at, evidence_id
                LIMIT ?
                """,
                (*[f"%{signal}%" for signal in signals], limit),
            ).fetchall()
            records: list[EvidenceRecord] = []
            for row in rows:
                record = self._get_with_connection(connection, row["evidence_id"])
                if record is not None:
                    records.append(record)
            return records

    def list_recent_conversations(
        self,
        *,
        user_id: str,
        limit: int = 30,
        offset: int = 0,
        source_ids: tuple[str, ...] | None = None,
        recent_activity: bool = False,
    ) -> tuple[list[EvidenceRecord], int]:
        """Newest imported messages, by the time they landed, not when they were said.

        A message typed a moment ago can carry an older `created_at`, or none.
        The board that says "just synced" has to follow `imported_at`.
        """
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if source_ids is not None and not source_ids:
            return [], 0
        source_clause = CURSOR_REPLAYED_THROUGH_CLAUDE_HOOKS
        parameters: list[object] = [user_id]
        count_clause = ""
        count_parameters: list[object] = [user_id]
        if source_ids is not None:
            parameters.extend(source_ids)
            count_clause = " AND source_id IN (" + ",".join("?" for _ in source_ids) + ")"
            count_parameters.extend(source_ids)
            source_clause += count_clause
        visible_clause = ""
        if recent_activity:
            visible_clause = """
              AND NOT (
                role='user' AND (
                  lower(ltrim(verbatim)) LIKE '<codex_internal_context%' OR
                  lower(ltrim(verbatim)) LIKE '<environment_context>%' OR
                  lower(ltrim(verbatim)) LIKE '<heartbeat>%' OR
                  lower(ltrim(verbatim)) LIKE '/goal %' OR
                  lower(ltrim(verbatim)) LIKE '# agents.md instructions%'
                )
              )"""
        order = (
            "julianday(COALESCE(created_at, imported_at)) DESC, imported_at DESC, evidence_id DESC"
            if recent_activity
            else "imported_at DESC, evidence_id DESC"
        )
        with self._connect() as connection:
            total = connection.execute(
                f"""
                WITH cursor_conversations AS MATERIALIZED (
                    SELECT DISTINCT conversation_id, workspace
                    FROM evidence
                    WHERE user_id = ? AND source_id = 'cursor'
                )
                SELECT (
                    SELECT COUNT(*) FROM evidence
                    WHERE user_id = ?{count_clause}{visible_clause}
                ) - (
                    SELECT COUNT(*)
                    FROM evidence
                    WHERE user_id = ?{count_clause}{visible_clause}
                      AND source_id = 'claude-code'
                      AND EXISTS (
                          SELECT 1 FROM cursor_conversations
                          WHERE cursor_conversations.conversation_id = evidence.conversation_id
                            AND cursor_conversations.workspace IS evidence.workspace
                      )
                ) AS count
                """,
                (user_id, *count_parameters, *count_parameters),
            ).fetchone()
            rows = connection.execute(
                f"""
                SELECT evidence_id FROM evidence
                WHERE user_id = ?{source_clause}{visible_clause}
                ORDER BY {order}
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
            records = self._get_many_with_connection(
                connection,
                [str(row["evidence_id"]) for row in rows],
            )
        return records, int(total["count"]) if total else 0

    def list_conversation(self, evidence_id: str) -> list[EvidenceRecord]:
        seed = self.get(evidence_id)
        if seed is None:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence_id FROM evidence
                WHERE user_id = ? AND source_id = ? AND conversation_id = ?
                  AND workspace IS ?
                -- imported_at is the microsecond insertion-order tiebreak.
                ORDER BY julianday(COALESCE(created_at, imported_at)),
                         source_ordinal, imported_at, evidence_id
                """,
                (seed.user_id, seed.source_id, seed.conversation_id, seed.workspace),
            ).fetchall()
            records: list[EvidenceRecord] = []
            for row in rows:
                record = self._get_with_connection(connection, row["evidence_id"])
                if record is not None:
                    records.append(record)
        return records

    def list_conversation_summaries(
        self,
        *,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        source_ids: tuple[str, ...] | None = None,
    ) -> tuple[list[ConversationSummaryRecord], int]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid conversation page")
        if source_ids is not None and not source_ids:
            return [], 0
        source_clause = (
            " AND source_id IN (" + ",".join("?" for _ in source_ids) + ")"
            if source_ids is not None
            else ""
        )
        values: list[object] = [user_id, *(source_ids or ())]
        visible_user = """
            role='user' AND NOT (
              lower(ltrim(verbatim)) LIKE '<codex_internal_context%' OR
              lower(ltrim(verbatim)) LIKE '<environment_context>%' OR
              lower(ltrim(verbatim)) LIKE '<heartbeat>%' OR
              lower(ltrim(verbatim)) LIKE '/goal %' OR
              lower(ltrim(verbatim)) LIKE '# agents.md instructions%'
            )
        """
        selected_visible_user = """
            e.role='user' AND NOT (
              lower(ltrim(e.verbatim)) LIKE '<codex_internal_context%' OR
              lower(ltrim(e.verbatim)) LIKE '<environment_context>%' OR
              lower(ltrim(e.verbatim)) LIKE '<heartbeat>%' OR
              lower(ltrim(e.verbatim)) LIKE '/goal %' OR
              lower(ltrim(e.verbatim)) LIKE '# agents.md instructions%'
            )
        """
        with self._connect() as connection:
            has_memory_links = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governed_memory_evidence'"
            ).fetchone()
            memory_count = (
                """(SELECT COUNT(DISTINCT link.memory_id)
                   FROM governed_memory_evidence link
                   JOIN evidence e ON e.evidence_id=link.evidence_id
                   JOIN governed_memories m ON m.memory_id=link.memory_id
                   WHERE link.active=1 AND m.status='confirmed' AND m.deleted_at IS NULL
                     AND e.user_id=? AND e.source_id=g.source_id
                     AND e.conversation_id=g.conversation_id AND e.workspace IS g.workspace
                  )"""
                if has_memory_links
                else "0"
            )
            memory_parameters: tuple[object, ...] = (user_id,) if has_memory_links else ()
            total = connection.execute(
                f"""SELECT COUNT(*) FROM (
                    SELECT 1 FROM evidence
                    WHERE user_id=?{source_clause}
                    GROUP BY source_id, conversation_id, workspace
                )""",
                values,
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                WITH grouped AS (
                  SELECT source_id, conversation_id, workspace,
                         MAX(julianday(COALESCE(created_at, imported_at))) AS latest_sort,
                         SUM(CASE WHEN {visible_user} THEN 1 ELSE 0 END) AS turn_count
                  FROM evidence
                  WHERE user_id=?{source_clause}
                  GROUP BY source_id, conversation_id, workspace
                )
                SELECT g.*,
                  (SELECT evidence_id FROM evidence e
                   WHERE e.user_id=? AND e.source_id=g.source_id
                     AND e.conversation_id=g.conversation_id AND e.workspace IS g.workspace
                   ORDER BY julianday(COALESCE(e.created_at,e.imported_at)) DESC,
                            e.source_ordinal DESC, e.imported_at DESC, e.evidence_id DESC
                   LIMIT 1) AS evidence_id,
                  (SELECT verbatim FROM evidence e
                   WHERE e.user_id=? AND e.source_id=g.source_id
                     AND e.conversation_id=g.conversation_id AND e.workspace IS g.workspace
                     AND {selected_visible_user}
                   ORDER BY julianday(COALESCE(e.created_at,e.imported_at)),
                            e.source_ordinal, e.imported_at, e.evidence_id
                   LIMIT 1) AS title,
                  (SELECT COALESCE(created_at, imported_at) FROM evidence e
                   WHERE e.user_id=? AND e.source_id=g.source_id
                     AND e.conversation_id=g.conversation_id AND e.workspace IS g.workspace
                   ORDER BY julianday(COALESCE(e.created_at,e.imported_at)) DESC,
                            e.source_ordinal DESC, e.imported_at DESC, e.evidence_id DESC
                   LIMIT 1) AS created_at,
                  (SELECT project FROM evidence e
                   WHERE e.user_id=? AND e.source_id=g.source_id
                     AND e.conversation_id=g.conversation_id AND e.workspace IS g.workspace
                     AND e.project IS NOT NULL LIMIT 1) AS project,
                  {memory_count} AS memory_count
                FROM grouped g
                ORDER BY g.latest_sort DESC, g.source_id, g.conversation_id
                LIMIT ? OFFSET ?
                """,
                (*values, user_id, user_id, user_id, user_id, *memory_parameters, limit, offset),
            ).fetchall()
        return [
            ConversationSummaryRecord(
                evidence_id=str(row["evidence_id"]),
                source_id=str(row["source_id"]),
                conversation_id=str(row["conversation_id"]),
                workspace=str(row["workspace"]) if row["workspace"] is not None else None,
                project=str(row["project"]) if row["project"] is not None else None,
                title=(str(row["title"]).strip()[:120] if row["title"] else "未命名对话"),
                latest_at=datetime.fromisoformat(row["created_at"])
                if row["created_at"]
                else datetime.fromtimestamp(0, UTC),
                turn_count=int(row["turn_count"]),
                memory_count=int(row["memory_count"]),
                anchor_turn_offset=0,
            )
            for row in rows
        ], int(total)

    def conversation_summary(self, evidence_id: str) -> ConversationSummaryRecord | None:
        seed = self.get(evidence_id)
        if seed is None:
            return None
        with self._connect() as connection:
            has_memory_links = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governed_memory_evidence'"
            ).fetchone()
            memory_count = 0
            if has_memory_links:
                memory_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(DISTINCT link.memory_id)
                        FROM governed_memory_evidence link
                        JOIN evidence e ON e.evidence_id=link.evidence_id
                        JOIN governed_memories m ON m.memory_id=link.memory_id
                        WHERE link.active=1 AND m.status='confirmed' AND m.deleted_at IS NULL
                          AND e.user_id=? AND e.source_id=? AND e.conversation_id=?
                          AND e.workspace IS ?
                        """,
                        (seed.user_id, seed.source_id, seed.conversation_id, seed.workspace),
                    ).fetchone()[0]
                )
        rows = self.list_conversation(evidence_id)
        if not rows:
            return None
        users = [record for record in rows if _is_visible_user_message(record)]
        turn_index = 0
        anchor_turn = 0
        for record in rows:
            if _is_visible_user_message(record):
                turn_index += 1
            if record.evidence_id == evidence_id:
                anchor_turn = turn_index
        latest = max(
            rows, key=lambda record: (record.created_at or record.imported_at, record.evidence_id)
        )
        title = users[0].verbatim.strip()[:120] if users else "未命名对话"
        return ConversationSummaryRecord(
            evidence_id=latest.evidence_id,
            source_id=seed.source_id,
            conversation_id=seed.conversation_id,
            workspace=seed.workspace,
            project=next((record.project for record in rows if record.project), None),
            title=title,
            latest_at=latest.created_at or latest.imported_at,
            turn_count=len(users),
            memory_count=memory_count,
            anchor_turn_offset=max(0, len(users) - anchor_turn),
        )

    def list_conversation_turns(
        self, evidence_id: str, *, offset: int = 0, limit: int = 20
    ) -> ConversationTurnPage:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid conversation turn page")
        seed = self.get(evidence_id)
        if seed is None:
            return ConversationTurnPage((), {}, 0, offset, limit)
        visible_user = """
          role='user' AND NOT (
            lower(ltrim(verbatim)) LIKE '<codex_internal_context%' OR
            lower(ltrim(verbatim)) LIKE '<environment_context>%' OR
            lower(ltrim(verbatim)) LIKE '<heartbeat>%' OR
            lower(ltrim(verbatim)) LIKE '/goal %' OR
            lower(ltrim(verbatim)) LIKE '# agents.md instructions%'
          )
        """
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                WITH ordered AS (
                  SELECT evidence_id,
                    julianday(COALESCE(created_at, imported_at)) AS ordered_at,
                    source_ordinal,
                    imported_at,
                    MAX(1, SUM(CASE WHEN {visible_user} THEN 1 ELSE 0 END) OVER (
                      ORDER BY julianday(COALESCE(created_at, imported_at)),
                               source_ordinal, imported_at, evidence_id
                    )) AS turn_index
                  FROM evidence
                  WHERE user_id=? AND source_id=? AND conversation_id=? AND workspace IS ?
                ), bounds AS (SELECT COALESCE(MAX(turn_index),0) AS total_turns FROM ordered)
                SELECT ordered.evidence_id, ordered.turn_index, bounds.total_turns
                FROM ordered CROSS JOIN bounds
                WHERE ordered.turn_index > MAX(0, bounds.total_turns-?-?)
                  AND ordered.turn_index <= MAX(0, bounds.total_turns-?)
                ORDER BY ordered.turn_index, ordered.ordered_at,
                         ordered.source_ordinal, ordered.imported_at, ordered.evidence_id
                """,
                (
                    seed.user_id,
                    seed.source_id,
                    seed.conversation_id,
                    seed.workspace,
                    limit,
                    offset,
                    offset,
                ),
            ).fetchall()
            records = self._get_many_with_connection(
                connection, [str(row["evidence_id"]) for row in rows]
            )
            # Keep every read on the same checked-out connection. Leaving the
            # context returns this reader to the shared pool; using it after
            # that point can race another request that has already checked out
            # the same sqlite connection.
            last = connection.execute(
                """
                SELECT role, written_by, verbatim FROM evidence
                WHERE user_id=? AND source_id=? AND conversation_id=? AND workspace IS ?
                ORDER BY julianday(COALESCE(created_at, imported_at)) DESC,
                         source_ordinal DESC, imported_at DESC, evidence_id DESC
                LIMIT 1
                """,
                (seed.user_id, seed.source_id, seed.conversation_id, seed.workspace),
            ).fetchone()
        turns = {str(row["evidence_id"]): int(row["turn_index"]) for row in rows}
        total_turns = int(rows[0]["total_turns"]) if rows else 0
        return ConversationTurnPage(
            tuple(records),
            turns,
            total_turns,
            offset,
            limit,
            last_row_opens_an_unanswered_turn(
                None
                if last is None
                else (str(last["role"]), str(last["written_by"]), str(last["verbatim"]))
            ),
        )

    def list_conversation_seed_ids(self, *, limit: int | None = None) -> list[str]:
        """Enumerate eligible seeds without hydrating originals; None keeps all history."""
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be a positive integer or None")
        # Cursor names a session after the chat directory, so two projects can
        # legitimately share one conversation_id. Workspace is the source's
        # project-scoping identity there and keeps those histories separate;
        # every other source stores its own stable conversation id and retains
        # one scope per conversation. NULL is a real scope for imported rows
        # whose host never supplied a workspace.
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT (
                    SELECT first.evidence_id
                    FROM evidence AS first
                    WHERE first.user_id = evidence.user_id
                      AND first.source_id = evidence.source_id
                      AND first.conversation_id = evidence.conversation_id
                      AND first.workspace IS evidence.workspace
                      AND (
                        first.role = 'user'
                        OR first.content_kind = 'memory_content'
                      )
                    ORDER BY julianday(COALESCE(first.created_at, first.imported_at)),
                             first.source_ordinal, first.evidence_id
                    LIMIT 1
                ) AS seed_id,
                MAX(julianday(COALESCE(created_at, imported_at))) AS latest_at
                FROM evidence
                WHERE user_id = 'local'
                GROUP BY user_id, source_id, conversation_id, workspace
                HAVING seed_id IS NOT NULL
                ORDER BY latest_at DESC, user_id, source_id, conversation_id, workspace
                LIMIT ?
                """,
                (-1 if limit is None else limit,),
            ).fetchall()
        return [str(row["seed_id"]) for row in rows]

    def list_conversation_seeds(self, *, limit: int = 10_000) -> list[EvidenceRecord]:
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        identifiers = self.list_conversation_seed_ids(limit=limit)
        with self._connect() as connection:
            records: list[EvidenceRecord] = []
            for identifier in identifiers:
                record = self._get_with_connection(connection, identifier)
                if record is not None:
                    records.append(record)
            return records

    def release_sdk_staging(self, evidence_ids: tuple[str, ...]) -> int:
        """Remove post-cutover queue staging after SDK success.

        Historical rows referenced by legacy Governance remain for the
        compatibility release. SDK-authority imports create no such links and
        stop being MemHub raw storage once the job has SDK receipts.
        """
        ids = tuple(dict.fromkeys(evidence_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect(writing=True) as connection:
            has_governance = (
                connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='governed_memory_evidence'"
                ).fetchone()
                is not None
            )
            legacy_guard = (
                "AND NOT EXISTS ("
                "SELECT 1 FROM governed_memory_evidence governed "
                "WHERE governed.evidence_id=evidence.evidence_id"
                ")"
                if has_governance
                else ""
            )
            changed = connection.execute(
                f"DELETE FROM evidence WHERE evidence_id IN ({placeholders}) {legacy_guard}",
                ids,
            )
        return changed.rowcount

    def release_completed_sdk_staging(self, *, limit: int = 2_048) -> int:
        """Resume cleanup if Core stopped after job settlement."""
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        with self._connect(writing=True) as connection:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if (
                not {
                    "memory_write_jobs",
                    "memory_write_job_evidence",
                }
                <= tables
            ):
                return 0
            rows = connection.execute(
                "SELECT DISTINCT linked.evidence_id "
                "FROM memory_write_jobs job "
                "JOIN memory_write_job_evidence linked USING(job_id) "
                "WHERE job.state='completed' "
                "AND (job.sdk_raw_ids_json!='[]' OR job.sdk_memory_ids_json!='[]') "
                "ORDER BY linked.evidence_id LIMIT ?",
                (limit,),
            ).fetchall()
        return self.release_sdk_staging(tuple(str(row[0]) for row in rows))

    def stage_memory_dispatch(
        self,
        record: EvidenceRecord,
        *,
        file_key: str,
        connection: sqlite3.Connection,
        priority: Literal["normal", "live"] = "normal",
    ) -> None:
        connection.execute(
            "INSERT INTO evidence_pending_memory_dispatch "
            "(user_id, source_id, file_key, evidence_id, priority) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, source_id, file_key, evidence_id) DO UPDATE SET "
            "priority=CASE WHEN priority='live' OR excluded.priority='live' "
            "THEN 'live' ELSE 'normal' END",
            (record.user_id, record.source_id, file_key, record.evidence_id, priority),
        )

    def take_stable_file_dispatches(
        self,
        *,
        user_id: str,
        source_id: str,
        file_key: str,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[PendingMemoryDispatch, ...]:
        context = nullcontext(connection) if connection is not None else self._connect()
        with context as database:
            assert database is not None
            rows = database.execute(
                "SELECT evidence.user_id, evidence.source_id, evidence.conversation_id, "
                "evidence.workspace, "
                "MAX(CASE WHEN pending.priority='live' THEN 1 ELSE 0 END) AS live "
                "FROM evidence_pending_memory_dispatch pending "
                "JOIN evidence USING(evidence_id) "
                "WHERE pending.user_id=? AND pending.source_id=? AND pending.file_key=? "
                "GROUP BY evidence.user_id, evidence.source_id, "
                "evidence.conversation_id, evidence.workspace "
                "ORDER BY evidence.user_id, evidence.source_id, "
                "evidence.conversation_id, evidence.workspace",
                (user_id, source_id, file_key),
            ).fetchall()
            dispatches: list[PendingMemoryDispatch] = []
            for row in rows:
                latest = database.execute(
                    "SELECT evidence_id FROM evidence "
                    "WHERE user_id=? AND source_id=? AND conversation_id=? "
                    "AND workspace IS ? "
                    "ORDER BY COALESCE(created_at, imported_at) DESC, "
                    "imported_at DESC, source_ordinal DESC, evidence_id DESC LIMIT 1",
                    (
                        row["user_id"],
                        row["source_id"],
                        row["conversation_id"],
                        row["workspace"],
                    ),
                ).fetchone()
                if latest is not None:
                    record = self._get_with_connection(database, latest["evidence_id"])
                    if record is not None:
                        dispatches.append(
                            PendingMemoryDispatch(
                                record=record,
                                priority="live" if row["live"] else "normal",
                            )
                        )
        return tuple(dispatches)

    def take_stable_file_records(
        self,
        *,
        user_id: str,
        source_id: str,
        file_key: str,
        connection: sqlite3.Connection | None = None,
        consume: bool = True,
    ) -> tuple[EvidenceRecord, ...]:
        """Read stable records, optionally consuming their pending markers.

        The caller may need to await the downstream dispatch before it knows
        whether the records reached the durable queue. With ``consume=False``
        the pending rows stay in place; ``consume_stable_file_records`` is
        called only after that dispatch succeeds. The default stays consuming
        for existing synchronous callers.
        """
        context = nullcontext(connection) if connection is not None else self._connect(writing=True)
        with context as database:
            assert database is not None
            records = [
                dispatch.record
                for dispatch in self.take_stable_file_dispatches(
                    user_id=user_id,
                    source_id=source_id,
                    file_key=file_key,
                    connection=database,
                )
            ]
            if consume:
                self.consume_stable_file_records(
                    user_id=user_id,
                    source_id=source_id,
                    file_key=file_key,
                    connection=database,
                )
        return tuple(records)

    def consume_stable_file_records(
        self,
        *,
        user_id: str,
        source_id: str,
        file_key: str,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """Consume the pending marker after the downstream dispatch succeeded."""
        context = nullcontext(connection) if connection is not None else self._connect(writing=True)
        with context as database:
            assert database is not None
            changed = database.execute(
                "DELETE FROM evidence_pending_memory_dispatch "
                "WHERE user_id=? AND source_id=? AND file_key=?",
                (user_id, source_id, file_key),
            )
        return changed.rowcount

    def list_pending_dispatch_files(
        self,
        *,
        limit: int = 10_000,
    ) -> tuple[tuple[str, str, str], ...]:
        """List pending dispatch file identities without consuming any marker.

        Startup compensation must follow the same two-phase contract as the live
        runner: list, read with ``consume=False``, await the durable submit, then
        consume. This helper only performs the read-only first step so the caller
        can retry a failed or cancelled submission without losing the marker.
        """
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, source_id, file_key "
                "FROM evidence_pending_memory_dispatch "
                "GROUP BY user_id, source_id, file_key "
                "ORDER BY user_id, source_id, file_key LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            (str(row["user_id"]), str(row["source_id"]), str(row["file_key"])) for row in rows
        )

    def take_all_stable_records(self, *, limit: int = 10_000) -> tuple[EvidenceRecord, ...]:
        """Legacy consume-on-read startup drain.

        The ordinary path drains a file only when its end-of-file cursor lands in
        the same transaction. A crash between the Evidence commit and that cursor
        leaves a durable pending row; without this startup drain the row would
        survive forever and the committed Evidence would never enter processing.
        The per-file method keeps selection stable when a file has grown, and the
        primary key makes repeated startup drains idempotent.

        This method is retained for synchronous callers that already own the
        durable submission before calling it. New startup compensation must use
        ``list_pending_dispatch_files()`` followed by
        ``take_stable_file_records(consume=False)`` and consume only after the
        submit succeeds.
        """
        if not 1 <= limit <= 100_000:
            raise ValueError("limit must be between 1 and 100000")
        with self._connect(writing=True) as connection:
            files = connection.execute(
                "SELECT user_id, source_id, file_key "
                "FROM evidence_pending_memory_dispatch "
                "GROUP BY user_id, source_id, file_key "
                "ORDER BY user_id, source_id, file_key LIMIT ?",
                (limit,),
            ).fetchall()
            records: list[EvidenceRecord] = []
            seen: set[str] = set()
            for row in files:
                batch = self.take_stable_file_records(
                    user_id=str(row["user_id"]),
                    source_id=str(row["source_id"]),
                    file_key=str(row["file_key"]),
                    connection=connection,
                    consume=False,
                )
                for record in batch:
                    key = record.evidence_id
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append(record)
                self.consume_stable_file_records(
                    user_id=str(row["user_id"]),
                    source_id=str(row["source_id"]),
                    file_key=str(row["file_key"]),
                    connection=connection,
                )
        return tuple(records)

    def _migrate_cursor_host_authorship(self, connection: sqlite3.Connection) -> None:
        predicates = " OR ".join("verbatim LIKE ?" for _ in _CURSOR_HOST_GENERATED_PREFIXES)
        rows = connection.execute(
            f"""
            SELECT evidence_id, revision, verbatim FROM evidence
            WHERE source_id = 'cursor' AND role = 'user' AND written_by = 'person'
              AND (
                {predicates}
                OR (
                  verbatim LIKE '{{%'
                  AND verbatim LIKE '%"request_id"%'
                  AND (
                    (verbatim LIKE '%"type"%' AND verbatim LIKE '%"steps"%')
                    OR (
                      verbatim LIKE '%"reader"%'
                      AND verbatim LIKE '%"recommended_order"%'
                    )
                  )
                )
              )
            """,
            tuple(f"{prefix}%" for prefix in _CURSOR_HOST_GENERATED_PREFIXES),
        ).fetchall()
        rows = [
            row
            for row in rows
            if row["verbatim"].startswith(_CURSOR_HOST_GENERATED_PREFIXES)
            or _is_pipeline_telemetry(row["verbatim"])
        ]
        if not rows:
            return
        now = datetime.now(UTC).isoformat()
        for row in rows:
            revision = int(row["revision"]) + 1
            connection.execute(
                """
                UPDATE evidence
                SET written_by = 'agent', revision = ?, updated_at = ?
                WHERE evidence_id = ?
                """,
                (revision, now, row["evidence_id"]),
            )
            self._insert_current_version(
                connection,
                row["evidence_id"],
                revision=revision,
                changed_at=now,
            )
        _journal_maintenance(connection, tuple(str(row["evidence_id"]) for row in rows))

    @staticmethod
    def _migrate_cursor_conversation_attribution(connection: sqlite3.Connection) -> None:
        now = datetime.now(UTC).isoformat()
        affected: list[str] = []
        has_workspace_gap = connection.execute(
            """
            SELECT 1
            FROM evidence AS unscoped
            WHERE unscoped.source_id = 'cursor' AND unscoped.workspace IS NULL
              AND (
                SELECT COUNT(DISTINCT scoped.workspace)
                FROM evidence AS scoped
                WHERE scoped.user_id = unscoped.user_id
                  AND scoped.source_id = unscoped.source_id
                  AND scoped.conversation_id = unscoped.conversation_id
                  AND scoped.workspace IS NOT NULL
              ) = 1
            LIMIT 1
            """
        ).fetchone()
        if has_workspace_gap is not None:
            changed = connection.execute(
                """
                WITH conversation_scopes AS (
                    SELECT user_id, source_id, conversation_id, MIN(workspace) AS workspace
                    FROM evidence
                    WHERE source_id = 'cursor' AND workspace IS NOT NULL
                    GROUP BY user_id, source_id, conversation_id
                    HAVING COUNT(DISTINCT workspace) = 1
                )
                UPDATE evidence
                SET workspace = (
                        SELECT scope.workspace
                        FROM conversation_scopes AS scope
                        WHERE scope.user_id = evidence.user_id
                          AND scope.source_id = evidence.source_id
                          AND scope.conversation_id = evidence.conversation_id
                    ),
                    updated_at = ?
                WHERE source_id = 'cursor' AND workspace IS NULL
                  AND EXISTS (
                        SELECT 1
                        FROM conversation_scopes AS scope
                        WHERE scope.user_id = evidence.user_id
                          AND scope.source_id = evidence.source_id
                          AND scope.conversation_id = evidence.conversation_id
                    )
                RETURNING evidence_id
                """,
                (now,),
            ).fetchall()
            affected.extend(str(row["evidence_id"]) for row in changed)
        has_project_gap = connection.execute(
            """
            SELECT 1
            FROM evidence AS unscoped
            WHERE unscoped.source_id = 'cursor' AND unscoped.project IS NULL
              AND (
                SELECT COUNT(DISTINCT scoped.project)
                FROM evidence AS scoped
                WHERE scoped.user_id = unscoped.user_id
                  AND scoped.source_id = unscoped.source_id
                  AND scoped.conversation_id = unscoped.conversation_id
                  AND scoped.project IS NOT NULL
              ) = 1
            LIMIT 1
            """
        ).fetchone()
        if has_project_gap is None:
            _journal_maintenance(connection, tuple(affected))
            return
        changed = connection.execute(
            """
            WITH conversation_projects AS (
                SELECT user_id, source_id, conversation_id, MIN(project) AS project
                FROM evidence
                WHERE source_id = 'cursor' AND project IS NOT NULL
                GROUP BY user_id, source_id, conversation_id
                HAVING COUNT(DISTINCT project) = 1
            )
            UPDATE evidence
            SET project = (
                    SELECT scoped.project
                    FROM conversation_projects AS scoped
                    WHERE scoped.user_id = evidence.user_id
                      AND scoped.source_id = evidence.source_id
                      AND scoped.conversation_id = evidence.conversation_id
                ),
                updated_at = ?
            WHERE source_id = 'cursor' AND project IS NULL
              AND EXISTS (
                    SELECT 1
                    FROM conversation_projects AS scoped
                    WHERE scoped.user_id = evidence.user_id
                      AND scoped.source_id = evidence.source_id
                      AND scoped.conversation_id = evidence.conversation_id
                )
            RETURNING evidence_id
            """,
            (now,),
        ).fetchall()
        affected.extend(str(row["evidence_id"]) for row in changed)
        _journal_maintenance(connection, tuple(affected))

    @staticmethod
    def _has_revision_change(
        existing: sqlite3.Row,
        evidence: EvidenceInput,
    ) -> bool:
        return any(
            (
                existing["content_kind"] != evidence.content_kind,
                existing["role"] != evidence.role,
                existing["content_hash"] != evidence.content_hash,
            )
        )

    @staticmethod
    def _insert_current_version(
        connection: sqlite3.Connection,
        evidence_id: str,
        *,
        revision: int,
        changed_at: str,
    ) -> None:
        evidence = connection.execute(
            "SELECT * FROM evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if evidence is None:
            raise RuntimeError("Cannot version missing Evidence")
        connection.execute(
            """
            INSERT INTO evidence_versions (
                evidence_id, revision, verbatim, content_hash, role,
                written_by, created_at, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                revision,
                evidence["verbatim"],
                evidence["content_hash"],
                evidence["role"],
                evidence["written_by"],
                evidence["created_at"],
                changed_at,
            ),
        )


def _as_fts_phrase(query: str) -> str:
    """Wrap the whole query as one FTS5 phrase, operators and all.

    Everything a user types here is text they are looking for, so `AND`, `*`
    and `-` have to arrive at SQLite as characters rather than as syntax:
    unquoted they are a query language the interface never offered, and one
    stray character would come back as a parse error instead of an answer.
    """
    return '"{}"'.format(query.replace('"', '""'))


def _as_like_pattern(query: str) -> str:
    """Escape the wildcards, so `100%` looks for `100%` and not for `100`."""
    for character in ("\\", "%", "_"):
        query = query.replace(character, f"\\{character}")
    return query


def _is_pipeline_telemetry(value: str) -> bool:
    if _PIPELINE_JSON_SIGNATURE.match(value) is not None:
        return True
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("request_id"), str)
        and payload.get("type") in {"write", "search", "delete", "digest"}
        and isinstance(payload.get("steps"), list)
    )
