"""Materialize internal semantic candidates from current authorized Evidence.

Not an arbitrary-ID endpoint or signed expansion capability. The combined v2
coordinator must revalidate this lane with memory and host identity at release.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC

from one_memory import ingest_journal as journal
from one_memory.context_raw import _check, _connection
from one_memory.imports.models import EvidenceRecord
from one_memory.imports.storage import EvidenceStore
from one_memory.retrieval.index_store import RetrievalIndexStore
from one_memory.retrieval.models import EvidenceSpan, RawEvidence
from one_memory.retrieval.raw_candidates import RawCandidateBatch


@dataclass(frozen=True)
class RawEvidenceBatch:
    items: tuple[RawEvidence, ...] = ()
    complete: bool = False
    reason: str | None = None
    used_characters: int = 0


def _merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if output and start <= output[-1][1]:
            output[-1] = (output[-1][0], max(end, output[-1][1]))
        else:
            output.append((start, end))
    return output


def materialize_raw_candidates(
    store: RetrievalIndexStore,
    candidates: RawCandidateBatch,
    *,
    generation_id: str,
    source_ids: frozenset[str],
    deadline: float,
    character_budget: int = 6000,
) -> RawEvidenceBatch:
    if not isinstance(source_ids, frozenset) or any(
        not isinstance(source, str) or not 1 <= len(source) <= 128 for source in source_ids
    ):
        raise ValueError("explicit_resolved_sources_required")
    if type(character_budget) is not int or not 1 <= character_budget <= 20000:
        raise ValueError("invalid_evidence_budget")
    if len(candidates.hits) > 64:
        raise ValueError("candidate_budget_exceeded")
    if not source_ids:
        return RawEvidenceBatch(reason="not_authorized")
    try:
        _check(deadline)
        expected = {hit.chunk.chunk_id: hit.chunk for hit in candidates.hits}
        if len(expected) != len(candidates.hits):
            raise ValueError("duplicate_candidates")
        if not expected:
            # Empty/excluded builds still carry an integrity claim. Validate
            # current authorized completed manifests, never only nonempty hits.
            current_chunks = store.active_chunks(
                generation_id,
                source_ids=source_ids,
                time_range=candidates.time_range,
                deadline=deadline,
                require_active=True,
            )
            _check(deadline)
            if current_chunks and candidates.reason == "no_eligible_chunks":
                return RawEvidenceBatch(reason="evidence_changed")
            return RawEvidenceBatch(complete=candidates.complete, reason=candidates.reason)
        changed = False
        grouped: dict[str, tuple[EvidenceRecord, list[tuple[int, int]]]] = {}
        with _connection(store.database_path, deadline) as db:
            db.execute("PRAGMA query_only=ON")
            rows = {
                row["chunk_id"]: row
                for row in db.execute(
                    """SELECT c.*, d.event_seq,d.grant_fingerprint,d.source_id
                    FROM retrieval_chunks c JOIN retrieval_documents d
                      ON d.generation_id=c.generation_id AND d.evidence_id=c.evidence_id
                      AND d.active_build_id=c.build_id AND d.build_id=c.build_id
                    JOIN retrieval_index_generations g ON g.generation_id=c.generation_id
                    WHERE c.generation_id=? AND g.state='active' AND d.state='ready'
                      AND c.chunk_id IN (SELECT value FROM json_each(?))""",
                    (generation_id, json.dumps(tuple(expected))),
                )
            }
            for key, chunk in expected.items():
                _check(deadline)
                row = rows.get(key)
                if row is None or row["source_id"] not in source_ids:
                    changed = True
                    continue
                event = journal.event(db, row["event_seq"])
                grant = store._grant(db, row["source_id"])
                if (
                    event is None
                    or event.user_id != "local"
                    or event.source_id != row["source_id"]
                    or event.evidence_id != chunk.evidence_id
                    or event.evidence_revision != chunk.revision
                    or grant is None
                    or grant != row["grant_fingerprint"]
                    or not journal.matches_current(db, event)
                ):
                    changed = True
                    continue
                record = EvidenceStore._get_with_connection(db, chunk.evidence_id)
                if (
                    record is None
                    or record.user_id != "local"
                    or record.source_id != row["source_id"]
                ):
                    changed = True
                    continue
                event_time = record.created_at
                if event_time is not None:
                    event_time = event_time.astimezone(UTC) if event_time.tzinfo else None
                if (
                    (row["build_id"], row["ordinal"], row["start_cp"], row["end_cp"])
                    != (chunk.build_id, chunk.ordinal, chunk.start_cp, chunk.end_cp)
                    or (record.source_id, event_time) != (chunk.source_id, chunk.event_time)
                    or not 0 <= chunk.start_cp < chunk.end_cp <= len(record.verbatim)
                ):
                    changed = True
                    continue
                if record.evidence_id not in grouped:
                    document = db.execute(
                        "SELECT * FROM retrieval_documents WHERE generation_id=? AND evidence_id=?",
                        (generation_id, record.evidence_id),
                    ).fetchone()
                    if document is None:
                        raise ValueError("published_document_missing")
                    store.validate_document(db, document, record, deadline=deadline)
                    grouped[record.evidence_id] = (record, [])
                grouped[record.evidence_id][1].append((chunk.start_cp, chunk.end_cp))
            items = []
            used = 0
            truncated = False
            for record, intervals in grouped.values():
                _check(deadline)
                spans: list[EvidenceSpan] = []
                for start, end in _merge(intervals):
                    if len(spans) >= 4 or end - start > min(20000, character_budget - used):
                        truncated = True
                        continue
                    spans.append(
                        EvidenceSpan(start_cp=start, end_cp=end, text=record.verbatim[start:end])
                    )
                    used += end - start
                if not spans:
                    continue
                event_time = record.created_at
                if event_time is not None:
                    event_time = event_time.astimezone(UTC) if event_time.tzinfo else None
                items.append(
                    RawEvidence(
                        evidence_id=record.evidence_id,
                        revision=record.revision,
                        source_id=record.source_id,
                        conversation_id=record.conversation_id,
                        role=record.role,
                        written_by="person" if record.role == "user" else "agent",
                        event_time=event_time,
                        spans=tuple(spans),
                        matched_by=("semantic",),
                    )
                )
            _check(deadline)
        return RawEvidenceBatch(
            tuple(items),
            candidates.complete and not changed and not truncated,
            "evidence_changed"
            if changed
            else "character_budget"
            if truncated
            else candidates.reason,
            used,
        )
    except TimeoutError:
        return RawEvidenceBatch(reason="deadline")
    except (sqlite3.Error, OSError, ValueError):
        return RawEvidenceBatch(reason="index_unavailable")
