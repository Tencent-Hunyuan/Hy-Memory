"""Read-only local library projection over the existing governed memory store."""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from one_memory.database_access import main_database
from one_memory.legacy_lifecycle_types import HyLifecycle
from one_memory.memory_evidence_freshness import register_memory_freshness
from one_memory.memory_governance import (
    GovernedMemory,
    MemoryGovernanceStore,
    MemoryStatus,
    lifecycle_predicate,
)

#: Read budget for one library page. 2026-10-12: raised from 5s (which starved
#: under a concurrent history import and surfaced as 503) to 15s, still well
#: under the 30s client timeout; past it the read degrades to a `stale` page.
READ_BUDGET_SECONDS = 15


class MemorySourceTime(BaseModel):
    """When the conversation a memory came from happened (D96).

    `basis` is `conversation` when the cited messages carry their own time,
    `import` when only the import time is known; the UI words them apart.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    at: str
    basis: Literal["conversation", "import"]


class MemoryLibraryPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[GovernedMemory]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    source_ids_by_memory: dict[str, list[str]] = Field(default_factory=dict)
    source_time_by_memory: dict[str, MemorySourceTime] = Field(default_factory=dict)
    #: True when the read budget ran out mid-page (typically during a history
    #: import): the counts and rows above are a partial snapshot, not an
    #: authoritative view. Clients should show "importing" instead of an error.
    stale: bool = False


def _sort_by_conversation() -> bool:
    """D99: the list reads as a timeline of the conversations, newest first.

    A memory generated today from a three-week-old dialogue belongs next to
    that week, not at the top. ONE_MEMORY_LIBRARY_SORT=updated brings back the
    old "newest generated first" order without a code change.
    """
    return os.environ.get("ONE_MEMORY_LIBRARY_SORT", "").strip().lower() != "updated"


def _origin_join() -> str:
    """The newest cited message per memory, as one joined aggregate.

    A correlated subquery would re-scan the link table for every candidate row;
    this is a single pass. Memories with no readable citation keep their old
    position through COALESCE.
    """
    if not _sort_by_conversation():
        return ""
    return """LEFT JOIN (
        SELECT link.memory_id AS memory_id,
               MAX(COALESCE(e.created_at, e.imported_at)) AS origin_at
        FROM governed_memory_evidence link
        JOIN evidence e ON e.evidence_id = link.evidence_id
        WHERE link.active = 1
        GROUP BY link.memory_id
    ) origin ON origin.memory_id = m.memory_id"""


def _order_by() -> str:
    if not _sort_by_conversation():
        return "m.updated_at DESC, m.memory_id ASC"
    # Conversations that share a timestamp keep the generated order between
    # themselves; ties would otherwise fall back to the id and look arbitrary.
    return "COALESCE(origin.origin_at, m.updated_at) DESC, m.updated_at DESC, m.memory_id ASC"


class _LibraryQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=256)
    query: str | None = None
    status: MemoryStatus | None = None
    active_only: bool = False
    source: str | None = Field(default=None, min_length=1)
    offset: int = Field(default=0, ge=0, le=2**63 - 1, strict=True)
    limit: int = Field(default=100, ge=1, le=500, strict=True)


def read_conversation_memories(
    database_path: Path,
    *,
    user_id: str,
    source_id: str,
    conversation_id: str,
    workspace: str | None,
    limit: int = 100,
) -> list[GovernedMemory]:
    """Current memories backed by any active Evidence in one conversation."""
    with main_database(database_path).read() as db:
        db.row_factory = sqlite3.Row
        freshness = register_memory_freshness(db)
        rows = db.execute(
            f"""
            SELECT DISTINCT m.* FROM governed_memories m
            JOIN governed_memory_evidence link ON link.memory_id=m.memory_id AND link.active=1
            JOIN evidence e ON e.evidence_id=link.evidence_id
            WHERE m.user_id=? AND m.status='confirmed' AND m.deleted_at IS NULL
              AND e.user_id=? AND e.source_id=? AND e.conversation_id=?
              AND e.workspace IS ? AND {freshness}
            ORDER BY m.updated_at DESC, m.memory_id
            LIMIT ?
            """,
            (user_id, user_id, source_id, conversation_id, workspace, limit),
        ).fetchall()
        return MemoryGovernanceStore.memories_from_rows(db, rows)


def read_memory_library(
    database_path: Path,
    *,
    user_id: str = "local",
    query: str | None = None,
    status: MemoryStatus | None = None,
    active_only: bool = False,
    source: str | None = None,
    offset: int = 0,
    limit: int = 100,
    lifecycle: HyLifecycle | None = None,
) -> MemoryLibraryPage:
    """Read one page and its exact readable total from the same SQLite snapshot.

    This is the local UI policy, not an external agent authorization entry point.
    Missing grants do not revoke already stored content; explicit revocations do.
    Read/parse failures propagate rather than masquerading as an empty library.
    Which memories are current is HY's `lifecycle` when given, as in
    `MemoryGovernanceStore.list`.
    """
    request = _LibraryQuery(
        user_id=user_id,
        query=query,
        status=status,
        active_only=active_only,
        source=source,
        offset=offset,
        limit=limit,
    )
    if request.active_only and request.status is not None:
        raise ValueError("active_only_conflicts_with_status")
    clauses = ["m.user_id = ?"]
    values: list[str | int] = [request.user_id]
    selected, selected_values = lifecycle_predicate(
        lifecycle, MemoryStatus.CONFIRMED if request.active_only else request.status, alias="m"
    )
    if selected is not None:
        clauses.append(selected)
        values.extend(selected_values)
    if request.status != MemoryStatus.DELETED:
        clauses.append("m.deleted_at IS NULL")

    # SourceReadAccess hides the whole memory if any active citation is revoked.
    # Do this before COUNT/LIMIT; a readable citation cannot rescue a mixed memory.
    clauses.append("""NOT EXISTS (
        SELECT 1 FROM governed_memory_evidence link
        JOIN evidence e ON e.evidence_id = link.evidence_id
        JOIN source_grants g ON g.user_id = e.user_id AND g.source_id = e.source_id
        WHERE link.memory_id = m.memory_id AND +link.active = 1
          AND g.revoked_at IS NOT NULL
    )""")
    # Governance normally guarantees ownership and referential integrity. Fail
    # closed for broken active links instead of exposing another user's citation.
    clauses.append("""NOT EXISTS (
        SELECT 1 FROM governed_memory_evidence link
        LEFT JOIN evidence e ON e.evidence_id = link.evidence_id
        WHERE link.memory_id = m.memory_id AND +link.active = 1
          AND (e.evidence_id IS NULL OR e.user_id != m.user_id)
    )""")
    if request.query:
        clauses.append("m.content LIKE ? ESCAPE '\\'")
        escaped = request.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        values.append(f"%{escaped}%")
    if request.source is not None:
        # Start from the compact active-link index, then resolve each Evidence
        # identity by primary key. The old correlated EXISTS let SQLite scan
        # every Evidence row for every candidate memory on large libraries.
        clauses.append("""m.memory_id IN (
            SELECT link.memory_id FROM governed_memory_evidence link
            JOIN evidence e ON e.evidence_id = link.evidence_id
            WHERE link.active = 1 AND e.user_id = ? AND e.source_id = ?
        )""")
        values.extend((request.user_id, request.source))
    predicate = " AND ".join(clauses)

    def _interrupted(error: Exception) -> bool:
        return isinstance(error, sqlite3.OperationalError) and "interrupted" in str(error).lower()

    with main_database(database_path).read() as db:
        # 2026-10-12: 5s starved the page whenever a history import saturated
        # SQLite, and the caller turned the deadline abort into a 503, so the
        # memory page read as broken for the whole import. 15s stays well under
        # the 30s client timeout; past it we return a `stale` page instead of
        # an error — an incomplete list is honest, an error is not.
        deadline = time.monotonic() + READ_BUDGET_SECONDS
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        # The total counts with the cheap permission filters only: the per-row
        # freshness UDF scales with the whole library and can burn the whole
        # read budget on its own (D22). Product accepted a small drift here;
        # the rows that are actually displayed are still checked row by row.
        try:
            total = db.execute(
                f"SELECT COUNT(*) FROM governed_memories m WHERE {predicate}", values
            ).fetchone()[0]
        except sqlite3.OperationalError as error:
            if not _interrupted(error):
                raise
            return MemoryLibraryPage(
                items=[],
                total=0,
                offset=request.offset,
                limit=request.limit,
                stale=True,
            )
        predicate += " AND " + register_memory_freshness(db)
        try:
            if request.query:
                current = db.execute(
                    f"""SELECT m.* FROM governed_memories m {_origin_join()}
                    WHERE {predicate}
                    ORDER BY {_order_by()}""",
                    values,
                ).fetchall()
                rows = current[request.offset : request.offset + request.limit]
            else:
                rows = db.execute(
                    f"""SELECT m.* FROM governed_memories m {_origin_join()}
                    WHERE {predicate}
                    ORDER BY {_order_by()} LIMIT ? OFFSET ?""",
                    [*values, request.limit, request.offset],
                ).fetchall()
        except sqlite3.OperationalError as error:
            if not _interrupted(error):
                raise
            # The count already succeeded: report it, flag the page stale.
            return MemoryLibraryPage(
                items=[],
                total=total,
                offset=request.offset,
                limit=request.limit,
                stale=True,
            )
        # Static read helper only: constructing the Store would initialize schema.
        items = MemoryGovernanceStore.memories_from_rows(db, rows, lifecycle=lifecycle)
        # Only the authorized page participates; source filters select memories,
        # not a subset of their provenance. Keep this in the count/page snapshot.
        source_ids_by_memory: dict[str, list[str]] = {item.memory_id: [] for item in items}
        # D96: the list shows when the cited conversation happened, not when
        # the memory was generated (which is "today" for a fresh import). Read
        # in the same query as the sources, so the page costs no extra round
        # trip and only this page's IDs are looked at, never the whole library.
        conversation_at: dict[str, str] = {}
        imported_at: dict[str, str] = {}
        if items:
            try:
                for link in db.execute(
                    # D104: the conversation time is when the person spoke, not
                    # when the turn ended. A memory's evidence covers the whole
                    # round (question, tool calls, reply), so MAX() lands on the
                    # reply and reads as a later, contradictory time next to the
                    # cited source. Prefer the earliest person-side evidence; only
                    # fall back to the earliest of any kind when the round has no
                    # person-authored row at all.
                    """SELECT link.memory_id, e.source_id,
                           COALESCE(
                               MIN(
                                   CASE WHEN e.role = 'user'
                                         AND e.role = 'user'
                                        THEN e.created_at END
                               ),
                               MIN(e.created_at)
                           ) AS conversation_at,
                           MAX(e.imported_at) AS imported_at
                    FROM governed_memory_evidence link
                    JOIN evidence e ON e.evidence_id = link.evidence_id
                    WHERE link.active = 1 AND e.user_id = ?
                      AND link.memory_id IN (SELECT value FROM json_each(?))
                    GROUP BY link.memory_id, e.source_id
                    ORDER BY link.memory_id, e.source_id""",
                    (request.user_id, json.dumps(list(source_ids_by_memory))),
                ):
                    memory_id = link["memory_id"]
                    source_ids_by_memory[memory_id].append(link["source_id"])
                    for seen, value in (
                        (conversation_at, link["conversation_at"]),
                        (imported_at, link["imported_at"]),
                    ):
                        if value and str(value) > seen.get(memory_id, ""):
                            seen[memory_id] = str(value)
            except sqlite3.OperationalError as error:
                if not _interrupted(error):
                    raise
                # Page rows are already read; ship them without source maps
                # rather than failing the whole request at the last step.
                return MemoryLibraryPage(
                    items=items,
                    total=total,
                    offset=request.offset,
                    limit=request.limit,
                    source_ids_by_memory=source_ids_by_memory,
                    stale=True,
                )
        source_time_by_memory = {
            memory_id: MemorySourceTime(at=conversation_at[memory_id], basis="conversation")
            if memory_id in conversation_at
            else MemorySourceTime(at=imported_at[memory_id], basis="import")
            for memory_id in source_ids_by_memory
            if memory_id in conversation_at or memory_id in imported_at
        }
        return MemoryLibraryPage(
            items=items,
            total=total,
            offset=request.offset,
            limit=request.limit,
            source_ids_by_memory=source_ids_by_memory,
            source_time_by_memory=source_time_by_memory,
        )
