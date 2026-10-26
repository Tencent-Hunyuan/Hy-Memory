"""Current local-owner titles for metadata-only call references; never a recorder."""

import sqlite3
import time
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from one_memory.call_event_store import CallEvent, CallReference
from one_memory.database_access import main_database
from one_memory.memory_evidence_freshness import register_memory_freshness
from one_memory.source_read_access import memory_source_readable


class SourceRecordRef(CallReference):
    available: bool
    title: str | None = Field(max_length=120)
    reason: Literal["changed", "unavailable"] | None

    @model_validator(mode="after")
    def consistent_availability(self) -> Self:
        if self.available:
            if self.title is None or self.reason is not None:
                raise ValueError("invalid_reference_availability")
        elif self.title is not None or self.reason is None:
            raise ValueError("invalid_reference_availability")
        return self


class CallReferencesUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("call_references_unavailable")


def _hidden(
    reference: CallReference, reason: Literal["changed", "unavailable"] = "unavailable"
) -> SourceRecordRef:
    return SourceRecordRef(**reference.model_dump(), available=False, title=None, reason=reason)


def _revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("invalid_reference_revision")
    return value


def _raw(db: sqlite3.Connection, reference: CallReference) -> SourceRecordRef:
    # Evidence has no active/tombstone column: its canonical row is the current
    # record; evidence_versions is history and must never supply a title.
    row = db.execute(
        "SELECT e.revision, e.source_id, g.revoked_at FROM evidence e "
        "LEFT JOIN source_grants g ON g.user_id=e.user_id AND g.source_id=e.source_id "
        "WHERE e.evidence_id=? AND e.user_id='local'",
        (reference.id,),
    ).fetchone()
    if row is None or row["revoked_at"] is not None or row["source_id"] != reference.source_id:
        return _hidden(reference)
    if _revision(row["revision"]) != reference.revision:
        return _hidden(reference, "changed")
    title = db.execute(
        "SELECT substr(verbatim,1,120) FROM evidence WHERE evidence_id=?", (reference.id,)
    ).fetchone()[0]
    return SourceRecordRef(**reference.model_dump(), available=True, title=title, reason=None)


def _memory(db: sqlite3.Connection, reference: CallReference) -> SourceRecordRef:
    row = db.execute(
        "SELECT revision, status, deleted_at FROM governed_memories "
        "WHERE memory_id=? AND user_id='local'",
        (reference.id,),
    ).fetchone()
    if row is None:
        return _hidden(reference)
    if row["status"] not in ("confirmed", "deleted"):
        raise ValueError("invalid_reference_status")
    if row["status"] == "deleted" or row["deleted_at"] is not None:
        return _hidden(reference)

    # Same local revocation/ownership predicate as SourceReadAccess.memories,
    # taken from there rather than copied: its public methods open their own
    # connections, so the predicate is the part that can be shared.
    access = db.execute(
        "SELECT EXISTS (SELECT 1 FROM governed_memory_evidence "
        "WHERE memory_id=? AND active=1), " + memory_source_readable("?", "?"),
        (reference.id, reference.id, "local"),
    ).fetchone()
    if not all(access):
        return _hidden(reference)
    if (
        reference.source_id is not None
        and db.execute(
            "SELECT 1 FROM governed_memory_evidence link "
            "JOIN evidence e ON e.evidence_id=link.evidence_id "
            "WHERE link.memory_id=? AND +link.active=1 AND e.source_id=? LIMIT 1",
            (reference.id, reference.source_id),
        ).fetchone()
        is None
    ):
        return _hidden(reference)
    if _revision(row["revision"]) != reference.revision:
        return _hidden(reference, "changed")
    if not db.execute(
        "SELECT is_memory_evidence_current(?,?,'local')", (reference.id, reference.revision)
    ).fetchone()[0]:
        return _hidden(reference, "changed")
    # Do not instantiate MemoryGovernanceStore (schema writes), or materialize
    # its full GovernedMemory body just to display a bounded title.
    title = db.execute(
        "SELECT substr(content,1,120) FROM governed_memories WHERE memory_id=?", (reference.id,)
    ).fetchone()[0]
    return SourceRecordRef(**reference.model_dump(), available=True, title=title, reason=None)


def resolve_call_references(database_path: Path, event: CallEvent) -> list[SourceRecordRef]:
    """Resolve at one read-snapshot point, preserving input identities and order.

    Local-owner only: event host attribution is NOT authorization. Explicit local
    source revocations deny access; absent grants retain legacy local semantics.
    A memory needs at least one accessible active citation. Evidence freshness
    proofs are retired and do not affect availability.

    Any read/parse/budget failure raises a fixed whole-request error, never a
    partial result or per-item deletion. The route owner must map this separately
    from successfully resolved references with available=False and must not cache
    returned titles. No store initialization, schema repair, or retention writes.
    """
    try:
        value = CallEvent.model_validate(event)
        deadline = time.monotonic() + 5
        with main_database(database_path).read() as db:
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            # Validate required read schema even for an empty event. No table
            # creation or silent fallback to an empty library on an old/bad DB.
            db.execute("SELECT evidence_id, user_id, source_id, revision FROM evidence LIMIT 0")
            db.execute("SELECT user_id, source_id, revoked_at FROM source_grants LIMIT 0")
            db.execute(
                "SELECT memory_id, user_id, revision, status, deleted_at "
                "FROM governed_memories LIMIT 0"
            )
            db.execute(
                "SELECT memory_id, evidence_id, active FROM governed_memory_evidence LIMIT 0"
            )
            register_memory_freshness(db)
            result = []
            for reference in value.references:
                if time.monotonic() >= deadline:
                    raise TimeoutError("reference_read_budget")
                result.append(
                    _raw(db, reference) if reference.kind == "raw" else _memory(db, reference)
                )
            if time.monotonic() >= deadline:
                raise TimeoutError("reference_read_budget")
        return result
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        raise CallReferencesUnavailable() from None
