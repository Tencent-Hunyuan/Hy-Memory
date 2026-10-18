"""A3-01/A3-04: the single front door for a write, and its idempotency rules.

Every entry (Function/HTTP/MCP, the five-minute scanner, and Hook/Plugin/Host
events) submits here rather than calling HY. That is the whole point of D52:
if any entry can reach the SDK directly, then durability, dedupe, ordering and
`unknown` handling must be re-implemented per entry, and each copy will be
slightly wrong.

This module decides the *submission contract* — what a request must look like,
and what happens when the same key shows up twice. Storage is A2's job.

The idempotency rule is deliberately two-sided, because "same key" alone is not
enough to decide anything:

- same key + same payload -> return the existing request, enqueue nothing;
- same key + different payload -> refuse. Silently overwriting would discard
  the earlier intent, and silently accepting would leave two different writes
  claiming one identity.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from one_memory.database_access import main_database
from one_memory.imports.models import (
    EvidenceContentKind,
    EvidenceOrigin,
    EvidenceRecord,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.contracts import (
    BatchIdentity,
    WriteRequest,
    internal_session_id_for,
)


class WriteConflictError(ValueError):
    """Same idempotency key, different payload."""


@dataclass(frozen=True)
class Submission:
    """The result of submitting a write."""

    request: WriteRequest
    request_id: str
    #: False when an identical submission was already on record.
    enqueued: bool


def payload_fingerprint(request: WriteRequest) -> str:
    """Stable digest of what makes two submissions 'the same write'.

    Evidence ids are part of it: the same batch with different evidence is a
    different write, and treating it as identical would drop the new evidence.
    """
    import hashlib

    material = "|".join(
        [
            request.internal_session_id,
            request.batch_identity.batch_id,
            *sorted(request.evidence_ids),
            request.priority,
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()


def submit_write(
    request: WriteRequest,
    *,
    known: dict[str, str] | None = None,
) -> Submission:
    """Accept a write, or reconcile it against one already on record.

    `known` maps idempotency key -> payload fingerprint, standing in for
    whatever A2 stores. It is a plain dict so this stays testable without a
    database.
    """
    known = known or {}
    fingerprint = payload_fingerprint(request)
    existing = known.get(request.request_key)

    if existing is None:
        return Submission(request=request, request_id=request.request_key, enqueued=True)

    if existing != fingerprint:
        raise WriteConflictError(
            f"idempotency key {request.request_key} already exists with different content"
        )

    return Submission(request=request, request_id=request.request_key, enqueued=False)


def build_request(
    *,
    internal_session_id: str,
    first_round: int,
    last_round: int,
    evidence_ids: list[str],
    input_records: tuple[EvidenceRecord, ...] = (),
    source_id: str | None = None,
    priority: Literal["live", "normal"] = "normal",
    idempotency_key: str | None = None,
) -> WriteRequest:
    """Build a `WriteRequest` for a range of rounds.

    The default key is the batch id, which is what makes a replay safe: the
    same session and round range always produce the same key, so re-deriving it
    later finds the original instead of creating a duplicate.
    """
    batch = BatchIdentity(
        internal_session_id=internal_session_id,
        first_round=first_round,
        last_round=last_round,
    )
    return WriteRequest(
        internal_session_id=internal_session_id,
        batch_identity=batch,
        evidence_ids=evidence_ids,
        input_records=input_records,
        source_id=source_id,
        idempotency_key=idempotency_key or batch.batch_id,
        priority=priority,
    )


class SubmittedEvidenceMismatch(ValueError):
    """The supplied Evidence records cannot form the requested write batch."""


def _evidence_records_by_id(
    connection: sqlite3.Connection, evidence_ids: list[str]
) -> dict[str, EvidenceRecord]:
    """Hydrate the requested Evidence rows in one read connection.

    The adapter sits on the write-entry boundary and therefore must not do a
    per-id `EvidenceStore.get()` followed by a per-row conversation read:
    `max_attempts` is three, but one batch can hold hundreds of records, and
    the N+1 would turn a queue submission into hundreds of pool checkouts.
    """
    if not evidence_ids:
        return {}
    placeholders = ",".join("?" for _ in evidence_ids)
    rows = connection.execute(
        f"SELECT * FROM evidence WHERE evidence_id IN ({placeholders})",
        tuple(evidence_ids),
    ).fetchall()
    ids = [str(row["evidence_id"]) for row in rows]
    if not ids:
        return {}
    origin_placeholders = ",".join("?" for _ in ids)
    origin_rows = connection.execute(
        "SELECT evidence_id, origin, raw_meta_json, source_version "
        f"FROM evidence_origins WHERE evidence_id IN ({origin_placeholders}) "
        "ORDER BY evidence_id, origin",
        tuple(ids),
    ).fetchall()
    # Re-key by origin so the record can expose the map the rest of Core uses.
    origin_meta: dict[str, dict[EvidenceOrigin, OriginMetadata]] = {
        evidence_id: {} for evidence_id in ids
    }
    for item in origin_rows:
        evidence_id = str(item["evidence_id"])
        origin_meta[evidence_id][EvidenceOrigin(item["origin"])] = OriginMetadata(
            raw_meta=json.loads(item["raw_meta_json"]),
            source_version=item["source_version"],
        )
    records: dict[str, EvidenceRecord] = {}
    for row in rows:
        evidence_id = str(row["evidence_id"])
        records[evidence_id] = EvidenceRecord(
            evidence_id=evidence_id,
            user_id=str(row["user_id"]),
            source_id=str(row["source_id"]),
            workspace=row["workspace"],
            project=row["project"],
            conversation_id=str(row["conversation_id"]),
            message_id=row["message_id"],
            correlation_id=row["correlation_id"],
            source_ordinal=row["source_ordinal"],
            content_kind=EvidenceContentKind(row["content_kind"]),
            role=str(row["role"]),
            written_by=WrittenBy(row["written_by"]),
            created_at=(datetime.fromisoformat(row["created_at"]) if row["created_at"] else None),
            verbatim=str(row["verbatim"]),
            origin_meta=origin_meta[evidence_id],
            content_hash=str(row["content_hash"]),
            revision=int(row["revision"]),
            imported_at=datetime.fromisoformat(row["imported_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    return records


def build_request_from_evidence(
    evidence_ids: list[str],
    *,
    evidence: EvidenceStore,
    first_round: int,
    last_round: int,
    priority: Literal["live", "normal"] = "normal",
    idempotency_key: str | None = None,
) -> WriteRequest:
    """Build a write from Evidence that is already durable.

    Scanner, Hook and manual-note callers all reach the write boundary with
    Evidence identifiers, but the durable queue requires one session and one
    round range. Keeping that conversion here gives those callers one shared
    implementation, and keeps the durable boundary from trusting a caller's
    stale list: every id is re-read, must still exist, and must belong to one
    conversation.

    The caller owns the product decision that the supplied ids form a complete
    round range; this function validates and normalises the identity, then
    delegates to :func:`build_request`. It deliberately does not guess turns or
    round numbers from IDs, because that decision belongs to the scanner's
    sessionizer rather than to the queue.
    """
    if not evidence_ids:
        raise SubmittedEvidenceMismatch("a write needs evidence to point at")

    requested = set(evidence_ids)
    with main_database(evidence.database_path).read(row_factory=sqlite3.Row) as connection:
        by_id = _evidence_records_by_id(
            connection,
            list(dict.fromkeys(evidence_ids)),
        )
        missing = sorted(requested - set(by_id))
        if missing:
            raise SubmittedEvidenceMismatch(f"write points at missing evidence: {missing[0]}")
        seed = by_id[evidence_ids[0]]
        for record in by_id.values():
            if (
                record.user_id != seed.user_id
                or record.source_id != seed.source_id
                or record.conversation_id != seed.conversation_id
            ):
                raise SubmittedEvidenceMismatch(
                    "a write batch must contain Evidence from one conversation"
                )
        ordered_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT evidence_id FROM evidence "
                "WHERE user_id=? AND source_id=? AND conversation_id=? "
                "ORDER BY julianday(COALESCE(created_at, imported_at)), "
                "source_ordinal, evidence_id",
                (seed.user_id, seed.source_id, seed.conversation_id),
            ).fetchall()
            if str(row[0]) in requested
        ]

    if set(ordered_ids) != requested:
        missing = sorted(requested - set(ordered_ids))
        raise SubmittedEvidenceMismatch(
            f"Evidence disappeared while building the write: {missing[0]}"
        )
    ordered = {evidence_id: by_id[evidence_id] for evidence_id in ordered_ids}

    return build_request(
        # Keep the exact conversation id when it fits the 128-char contract;
        # otherwise derive the versioned session id, so a long branch id does
        # not reject the whole history import.
        internal_session_id=internal_session_id_for(seed.conversation_id, seed.source_id),
        first_round=first_round,
        last_round=last_round,
        evidence_ids=list(ordered),
        input_records=tuple(ordered.values()),
        source_id=seed.source_id,
        priority=priority,
        idempotency_key=idempotency_key,
    )


def build_reconcile_request(
    evidence_ids: list[str],
    *,
    evidence: EvidenceStore,
    first_round: int,
    last_round: int,
) -> WriteRequest:
    """Build the replay-safe request for a committed orphan batch.

    `unsubmitted_evidence_ids()` discovers Evidence that crashed between the
    Evidence commit and durable submit. Retrying that discovery must not create
    a second write, so the idempotency key has to be derived from the Evidence
    identity rather than chosen by each caller.

    Grouping is still a caller decision: the batch is the ids passed here and
    the round range is supplied by the scanner/sessionizer. This helper only
    makes the key deterministic and reuses the same validation/order rules as
    `build_request_from_evidence`.
    """
    requested = list(dict.fromkeys(evidence_ids))
    if not requested:
        raise SubmittedEvidenceMismatch("a reconcile write needs evidence to point at")
    if len(requested) == 1:
        idempotency_key = f"reconcile:{requested[0]}"
    else:
        import hashlib

        digest = hashlib.sha256("\0".join(sorted(requested)).encode()).hexdigest()
        idempotency_key = f"reconcile:{digest}"
    return build_request_from_evidence(
        requested,
        evidence=evidence,
        first_round=first_round,
        last_round=last_round,
        idempotency_key=idempotency_key,
    )
