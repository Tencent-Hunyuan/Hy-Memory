"""Visibility decided when the library is written, read as a conclusion.

The row-by-row freshness check is the most expensive thing any read does:
measured on a 1,226-memory library it is 0.69 s over 160 memories, and while
the library is also being written to that is what pushed `/api/v1/memories`
and the memory map past their five second interrupt (D92, D90).

This module keeps the same answer, computed once per change instead of once
per read. It does not write a second opinion: recomputation calls the very
same predicate the read path uses, so the two cannot disagree about what the
rule is — only about how fresh the stored conclusion is.

The tables are derived caches, not schema. They are created idempotently at
startup and carry no migration version, because a newer schema version makes
`schema_gate.py` refuse to start and taking that away would leave no way back
to an older package.
"""

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from one_memory.database_access import main_database
from one_memory.memory_evidence_freshness import register_memory_freshness

#: `exact` keeps today's read path: every row checked as it is read.
DEFAULT_MODE = "exact"

_TABLES = """
CREATE TABLE IF NOT EXISTS memory_visibility (
    memory_id   TEXT PRIMARY KEY,
    visible     INTEGER NOT NULL,
    reason      TEXT,
    computed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_visibility_dirty (
    memory_id   TEXT PRIMARY KEY
);
"""

#: The triggers look a citation up by `evidence_id`, and the only index that
#: starts elsewhere is `(active, evidence_id, memory_id)` — so without this
#: every evidence insert walked the whole link table, measured at +49% on
#: import against the 10% the plan allows.
_INDEXES = """
CREATE INDEX IF NOT EXISTS memory_visibility_link_evidence
ON governed_memory_evidence(evidence_id);
"""


def mode() -> str:
    """Whether reads consult this cache at all."""
    return os.environ.get("ONE_MEMORY_READ_VISIBILITY", DEFAULT_MODE).strip() or DEFAULT_MODE


def ensure_tables(db: sqlite3.Connection) -> None:
    """Create the derived tables if they are missing. Safe to call often."""
    db.executescript(_TABLES)
    # The index is on a table this module does not own, so it is created only
    # once that table exists: a library opened before the store has built it
    # would otherwise fail here and lose the tables as well.
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    if any(
        str(row["name"]) == "governed_memory_evidence"
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='governed_memory_evidence'"
        ).fetchall()
    ):
        cursor.execute(_INDEXES)


def recompute(
    db: sqlite3.Connection,
    memory_ids: list[str],
    *,
    readable_sql: str,
) -> int:
    """Recompute the stored conclusion for these memories.

    `readable_sql` is the read path's own predicate: a `SELECT` taking
    (memory_id, revision, user_id, memory_id, user_id) and answering 0 or 1.
    Passing it in is what keeps this from becoming a second opinion.
    """
    if not memory_ids:
        return 0
    # The predicate names the freshness UDF, so it has to be registered on
    # this connection before it can be evaluated.
    register_memory_freshness(db)
    now = datetime.now(UTC).isoformat()
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    changed = 0
    for start in range(0, len(memory_ids), 100):
        batch = memory_ids[start : start + 100]
        for memory_id in batch:
            row = cursor.execute(
                "SELECT revision, user_id, status, deleted_at FROM governed_memories "
                "WHERE memory_id=?",
                (memory_id,),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO memory_visibility (memory_id, visible, reason, computed_at) "
                    "VALUES (?,0,'deleted',?) "
                    "ON CONFLICT(memory_id) DO UPDATE SET visible=0, reason='deleted', "
                    "computed_at=excluded.computed_at",
                    (memory_id, now),
                )
                changed += 1
                continue
            if row["deleted_at"] is not None:
                visible, reason = 0, "deleted"
            elif row["status"] != "confirmed":
                visible, reason = 0, "not_confirmed"
            else:
                readable = cursor.execute(
                    f"SELECT {readable_sql}",
                    (memory_id, row["revision"], row["user_id"], memory_id, row["user_id"]),
                ).fetchone()
                answer = bool(readable[0]) if readable is not None else False
                visible = 1 if answer else 0
                reason = "ok" if answer else "stale_evidence"
            db.execute(
                "INSERT INTO memory_visibility (memory_id, visible, reason, computed_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(memory_id) DO UPDATE SET visible=excluded.visible, "
                "reason=excluded.reason, computed_at=excluded.computed_at",
                (memory_id, visible, reason, now),
            )
            changed += 1
    db.executemany(
        "DELETE FROM memory_visibility_dirty WHERE memory_id=?",
        [(memory_id,) for memory_id in memory_ids],
    )
    return changed


def dirty(db: sqlite3.Connection, memory_ids: list[str]) -> int:
    """Mark these memories as needing recomputation. Called by the triggers."""
    if not memory_ids:
        return 0
    db.executemany(
        "INSERT OR IGNORE INTO memory_visibility_dirty (memory_id) VALUES (?)",
        [(memory_id,) for memory_id in memory_ids if memory_id],
    )
    return len(memory_ids)


def pending(db: sqlite3.Connection, *, limit: int = 100) -> list[str]:
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    return [
        str(row["memory_id"])
        for row in cursor.execute(
            "SELECT memory_id FROM memory_visibility_dirty LIMIT ?", (limit,)
        ).fetchall()
    ]


def _trigger_sql() -> list[tuple[str, str]]:
    """(table it depends on, statement) for each trigger."""
    return [
        (
            "evidence",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_evidence
        AFTER INSERT ON evidence
        BEGIN
            INSERT INTO memory_visibility_dirty (memory_id)
            SELECT memory_id FROM governed_memory_evidence WHERE evidence_id = NEW.evidence_id
            ON CONFLICT(memory_id) DO NOTHING;
        END;""",
        ),
        (
            "evidence",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_evidence_update
        AFTER UPDATE ON evidence
        BEGIN
            INSERT INTO memory_visibility_dirty (memory_id)
            SELECT memory_id FROM governed_memory_evidence WHERE evidence_id = NEW.evidence_id
            ON CONFLICT(memory_id) DO NOTHING;
        END;""",
        ),
        (
            "governed_memory_evidence",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_link
        AFTER INSERT ON governed_memory_evidence
        BEGIN
            INSERT OR IGNORE INTO memory_visibility_dirty (memory_id)
            VALUES (NEW.memory_id);
        END;""",
        ),
        (
            "governed_memory_evidence",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_link_update
        AFTER UPDATE ON governed_memory_evidence
        BEGIN
            INSERT INTO memory_visibility_dirty (memory_id)
            VALUES (NEW.memory_id)
            ON CONFLICT(memory_id) DO NOTHING;
            INSERT INTO memory_visibility_dirty (memory_id)
            VALUES (OLD.memory_id)
            ON CONFLICT(memory_id) DO NOTHING;
        END;""",
        ),
        (
            "governed_memories",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_memory
        AFTER INSERT ON governed_memories
        BEGIN
            INSERT OR IGNORE INTO memory_visibility_dirty (memory_id)
            VALUES (NEW.memory_id);
        END;""",
        ),
        (
            "governed_memories",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_memory_update
        AFTER UPDATE ON governed_memories
        BEGIN
            INSERT OR IGNORE INTO memory_visibility_dirty (memory_id)
            VALUES (NEW.memory_id);
        END;""",
        ),
        (
            "governed_memory_versions",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_versions
        AFTER INSERT ON governed_memory_versions
        BEGIN
            INSERT OR IGNORE INTO memory_visibility_dirty (memory_id)
            VALUES (NEW.memory_id);
        END;""",
        ),
        (
            "memory_task_outputs",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_outputs
        AFTER INSERT ON memory_task_outputs
        BEGIN
            INSERT OR IGNORE INTO memory_visibility_dirty (memory_id)
            VALUES (NEW.memory_id);
        END;""",
        ),
        (
            "source_grants",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_grants
        AFTER INSERT ON source_grants
        BEGIN
            INSERT INTO memory_visibility_dirty (memory_id)
            SELECT DISTINCT link.memory_id
            FROM governed_memory_evidence link
            JOIN evidence e ON e.evidence_id = link.evidence_id
            WHERE e.source_id = NEW.source_id
            ON CONFLICT(memory_id) DO NOTHING;
        END;""",
        ),
        (
            "source_grants",
            """
        CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_grants_update
        AFTER UPDATE ON source_grants
        BEGIN
            INSERT INTO memory_visibility_dirty (memory_id)
            SELECT DISTINCT link.memory_id
            FROM governed_memory_evidence link
            JOIN evidence e ON e.evidence_id = link.evidence_id
            WHERE e.source_id = NEW.source_id
            ON CONFLICT(memory_id) DO NOTHING;
        END;""",
        ),
    ]


def install_triggers(db: sqlite3.Connection) -> None:
    """Mark the affected memories dirty in the same transaction as the write.

    Triggers rather than calls at each write site: the writes are spread over
    six modules, and a missed one shows the reader a stale answer, which is
    worse than the cost. A new write path cannot escape this either.

    Installed one by one: a library that does not have one of the tables yet
    must still get the triggers it can, instead of losing all of them because
    one statement named a missing table.
    """
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    present = {
        str(row["name"])
        for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    # CREATE TRIGGER IF NOT EXISTS cannot repair the legacy definition. Replace
    # this derived-cache trigger transactionally on every startup so old
    # libraries get the row-level conflict handling without a schema migration.
    cursor.execute("DROP TRIGGER IF EXISTS memory_visibility_dirty_on_link_update")
    for table, statement in _trigger_sql():
        if table not in present:
            continue
        cursor.execute(statement)


def startup(database_path: Path) -> None:
    """Idempotent: create the derived tables and triggers. No version change.

    Runs through the write connection because the triggers are schema. The
    trigger replacement is one short transaction and happens once per start.
    """
    with main_database(database_path).write() as db, db:
        ensure_tables(db)
        # ensure_tables uses executescript(), which commits any pending legacy
        # transaction. Begin only afterward so replacing the installed trigger
        # is one atomic schema change and a failed CREATE restores the old one.
        db.execute("BEGIN IMMEDIATE")
        install_triggers(db)


def readable_ids(db: sqlite3.Connection) -> set[str]:
    """Memories whose stored conclusion is visible and not awaiting recompute.

    A memory absent from the table, or still in the dirty set, is simply not
    answered from here — the reader checks it exactly, as it does today. That
    is what makes the backfill safe: a half-filled cache never misinforms, it
    only helps less.

    Returns nothing at all when the tables are absent, which is the same
    answer: a library opened by a path that never ran startup is read exactly.
    """
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    names = {
        str(row["name"])
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('memory_visibility','memory_visibility_dirty')"
        ).fetchall()
    }
    if names != {"memory_visibility", "memory_visibility_dirty"}:
        return set()
    return {
        str(row["memory_id"])
        for row in cursor.execute(
            "SELECT memory_id FROM memory_visibility WHERE visible=1 "
            "AND memory_id NOT IN (SELECT memory_id FROM memory_visibility_dirty)"
        ).fetchall()
    }


def missing_ids(db: sqlite3.Connection, *, limit: int = 100) -> list[str]:
    """Memories with no stored conclusion yet, for the first backfill."""
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    return [
        str(row["memory_id"])
        for row in cursor.execute(
            "SELECT memory_id FROM governed_memories "
            "WHERE memory_id NOT IN (SELECT memory_id FROM memory_visibility) LIMIT ?",
            (limit,),
        ).fetchall()
    ]


def sample_ids(db: sqlite3.Connection, *, limit: int = 200) -> list[str]:
    """A random slice of decided memories, for the ongoing audit.

    The triggers are what decide when to recompute, and a trigger that never
    fires for some change would quietly leave a wrong conclusion in place.
    Recomputing a random slice and comparing with what is stored proves the
    bookkeeping continuously, not just in tests.
    """
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    return [
        str(row["memory_id"])
        for row in cursor.execute(
            "SELECT memory_id FROM memory_visibility "
            "WHERE memory_id NOT IN (SELECT memory_id FROM memory_visibility_dirty) "
            "ORDER BY RANDOM() LIMIT ?",
            (limit,),
        ).fetchall()
    ]


def audit(db: sqlite3.Connection, *, readable_sql: str, limit: int = 200) -> int:
    """Recompute a random slice and report how many conclusions disagree.

    Returns the number that disagreed. Anything above zero means the
    bookkeeping missed a change, and the read path should stop trusting the
    cache.
    """
    from one_memory.memory_evidence_freshness import register_memory_freshness

    register_memory_freshness(db)
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    disagreements = 0
    for memory_id in sample_ids(db, limit=limit):
        row = cursor.execute(
            "SELECT revision, user_id, status, deleted_at FROM governed_memories WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
        if row is None or row["deleted_at"] is not None or row["status"] != "confirmed":
            answer = 0
        else:
            exact = cursor.execute(
                f"SELECT {readable_sql}",
                (memory_id, row["revision"], row["user_id"], memory_id, row["user_id"]),
            ).fetchone()
            answer = 1 if exact is not None and exact[0] else 0
        stored_row = cursor.execute(
            "SELECT visible FROM memory_visibility WHERE memory_id=?", (memory_id,)
        ).fetchone()
        if stored_row is not None and int(stored_row["visible"]) != answer:
            disagreements += 1
    return disagreements
