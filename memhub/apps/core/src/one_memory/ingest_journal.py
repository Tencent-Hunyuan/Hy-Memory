"""Explicit, caller-transaction ingestion identity journal; not a permission grant.

Call initialize before admitting policy or starting writers, and record after ALL
Evidence/origin changes. No constructor, connection creation, commit or migration
at import time. Committed events/tombstones must never be removed or renumbered.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.migrations import _split_statements, ingest_journal_ddl

Cause = Literal["import", "maintenance", "delete"]


@dataclass(frozen=True)
class IngestEvent:
    seq: int
    event_id: str
    incarnation: str
    incarnation_seq: int
    user_id: str
    evidence_id: str
    source_id: str
    evidence_revision: int
    kind: str
    generation_eligible: bool
    content_hash: str
    material_hash: str
    identity_hash: str
    origin_hash: str
    fingerprint: str
    recorded_at: str


@contextmanager
def _rows(connection: sqlite3.Connection) -> Iterator[None]:
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        yield
    finally:
        connection.row_factory = previous


@contextmanager
def _write(connection: sqlite3.Connection) -> Iterator[None]:
    if not connection.in_transaction:
        raise RuntimeError("ingest_journal_requires_caller_transaction")
    # A nested savepoint only rolls back our partial writes, never commits the
    # caller's transaction or discards its prior Evidence/checkpoint work.
    name = "ingest_" + uuid4().hex
    connection.execute(f"SAVEPOINT {name}")
    try:
        with _rows(connection):
            yield
    except BaseException:
        connection.execute(f"ROLLBACK TO {name}")
        connection.execute(f"RELEASE {name}")
        raise
    else:
        connection.execute(f"RELEASE {name}")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _state(connection: sqlite3.Connection, evidence_id: str) -> dict[str, str | int] | None:
    evidence = EvidenceStore._get_with_connection(connection, evidence_id)
    if evidence is None:
        return None
    value = evidence.model_dump(mode="json")
    # The stored normalized content_hash is insufficient for exact-content leases.
    content = _digest(value["verbatim"])
    material = _digest({key: value[key] for key in ("verbatim", "role", "content_kind")})
    origins = _digest(value["origin_meta"])
    identity = _digest(
        {
            key: item
            for key, item in value.items()
            if key
            not in {"verbatim", "written_by", "origin_meta", "imported_at", "updated_at"}
        }
    )
    return dict(
        user_id=evidence.user_id,
        evidence_id=evidence.evidence_id,
        source_id=evidence.source_id,
        evidence_revision=evidence.revision,
        content_hash=content,
        material_hash=material,
        identity_hash=identity,
        origin_hash=origins,
        fingerprint=_digest([content, material, identity, origins]),
    )


def _event(row: sqlite3.Row | None) -> IngestEvent | None:
    if row is None:
        return None
    values = dict(row)
    values["generation_eligible"] = bool(values["generation_eligible"])
    return IngestEvent(**values)


def _ready(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT version FROM evidence_ingest_journal_state WHERE singleton=1"
    ).fetchone()
    if row is None or row[0] != 1:
        raise RuntimeError("ingest_journal_not_initialized")


def current(
    connection: sqlite3.Connection, user_id: str, evidence_id: str | None = None
) -> IngestEvent | None:
    """Latest recorded identity, including tombstones; not proof of live authority."""
    with _rows(connection):
        _ready(connection)
        result = _event(
            connection.execute(
                "SELECT * FROM evidence_ingest_events WHERE evidence_id=? "
                "ORDER BY seq DESC LIMIT 1",
                (evidence_id if evidence_id is not None else user_id,),
            ).fetchone()
        )
        return (
            result if evidence_id is None or result is None or result.user_id == user_id else None
        )


def event(connection: sqlite3.Connection, seq: int) -> IngestEvent | None:
    with _rows(connection):
        _ready(connection)
        return _event(
            connection.execute(
                "SELECT * FROM evidence_ingest_events WHERE seq=?", (seq,)
            ).fetchone()
        )


def bound(connection: sqlite3.Connection) -> int:
    """Capture this inside the policy's writer transaction; zero is valid."""
    _ready(connection)
    return int(
        connection.execute("SELECT coalesce(max(seq),0) FROM evidence_ingest_events").fetchone()[0]
    )


def watermark(connection: sqlite3.Connection) -> int:
    """Public policy-boundary name; use in the same transaction as policy commit."""
    return bound(connection)


def matches_current(connection: sqlite3.Connection, observed: IngestEvent) -> bool:
    """Check journal head plus live fingerprint in the caller's read/write snapshot.

    Source/model permission is a separate check. Never treat eligible as a grant.
    """
    if not connection.in_transaction:
        raise RuntimeError("ingest_journal_requires_caller_transaction")
    with _rows(connection):
        head = current(connection, observed.user_id, observed.evidence_id)
        if head != observed or head.kind == "delete":
            return False
        live = _state(connection, observed.evidence_id)
        return live is not None and live["fingerprint"] == observed.fingerprint


def _append(
    connection: sqlite3.Connection,
    state: dict[str, str | int],
    *,
    previous: IngestEvent | None,
    kind: str,
    eligible: bool,
) -> IngestEvent:
    new_incarnation = previous is None or previous.kind == "delete"
    values = dict(state)
    values.update(
        event_id=uuid4().hex,
        incarnation=uuid4().hex if previous is None or new_incarnation else previous.incarnation,
        incarnation_seq=0 if previous is None or new_incarnation else previous.incarnation_seq,
        kind=kind,
        generation_eligible=int(eligible),
        recorded_at=datetime.now(UTC).isoformat(),
    )
    columns = ",".join(values)
    cursor = connection.execute(
        f"INSERT INTO evidence_ingest_events ({columns}) VALUES ({','.join('?' for _ in values)})",
        tuple(values.values()),
    )
    seq = cursor.lastrowid
    if new_incarnation:
        connection.execute(
            "UPDATE evidence_ingest_events SET incarnation_seq=? WHERE seq=?", (seq, seq)
        )
    result = _event(
        connection.execute("SELECT * FROM evidence_ingest_events WHERE seq=?", (seq,)).fetchone()
    )
    assert result is not None
    return result


def initialize(connection: sqlite3.Connection) -> None:
    """Explicit atomic baseline. Never reclassifies existing events on reopen."""
    with _write(connection):
        # execute, not executescript: sqlite3.executescript can commit an outer transaction.
        # Schema owned by `memory_server.migrations` (migration 16). No local
        # copy, so the journal cannot drift from what the migration declares.
        # Still executed one statement at a time, because executescript would
        # commit the caller's outer transaction.
        for statement in _split_statements(ingest_journal_ddl("sqlite")):
            connection.execute(statement)
        marker = connection.execute(
            "SELECT version FROM evidence_ingest_journal_state WHERE singleton=1"
        ).fetchone()
        if marker is not None:
            _ready(connection)
            return
        if connection.execute("SELECT 1 FROM evidence_ingest_events LIMIT 1").fetchone():
            raise RuntimeError("ingest_journal_partial_initialization")
        # Stream IDs, parsing one full canonical record at a time, never a body list.
        for row in connection.execute("SELECT evidence_id FROM evidence ORDER BY evidence_id"):
            state = _state(connection, row[0])
            assert state is not None
            _append(connection, state, previous=None, kind="migration_baseline", eligible=False)
        connection.execute("INSERT INTO evidence_ingest_journal_state VALUES (1,1)")


def record(
    connection: sqlite3.Connection, evidence_id: str, *, cause: Cause = "import"
) -> IngestEvent | None:
    """Finalize after origins/attribution; delete must be called AFTER deleting Evidence.

    Cause is trusted internal provenance, not a request field. All affected IDs
    must be recorded before caller commit; a failed finalize must abort that write.
    """
    if cause not in {"import", "maintenance", "delete"}:
        raise ValueError("invalid_ingest_cause")
    with _write(connection):
        previous = current(connection, evidence_id)
        state = _state(connection, evidence_id)
        if cause == "delete":
            if state is not None:
                raise ValueError("ingest_delete_requires_absent_evidence")
            if previous is None or previous.kind == "delete":
                return previous
            state = {
                key: getattr(previous, key)
                for key in (
                    "user_id",
                    "evidence_id",
                    "source_id",
                    "evidence_revision",
                    "content_hash",
                    "material_hash",
                    "identity_hash",
                    "origin_hash",
                    "fingerprint",
                )
            }
            return _append(connection, state, previous=previous, kind="delete", eligible=False)
        if state is None:
            raise KeyError(evidence_id)
        if (
            previous is not None
            and previous.kind != "delete"
            and previous.fingerprint == state["fingerprint"]
        ):
            return previous
        fresh = previous is None or previous.kind == "delete"
        material = previous is None or fresh or previous.material_hash != state["material_hash"]
        # Refining an already-eligible imported row's identity (for example
        # when file and hook provenance converge) must not revoke its place in
        # the active policy. A migration baseline remains ineligible.
        eligible = cause == "import" and (
            material or (previous is not None and previous.generation_eligible)
        )
        kind = "create" if fresh else "revise" if cause == "import" and material else "metadata"
        return _append(connection, state, previous=previous, kind=kind, eligible=eligible)
