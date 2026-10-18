"""v2 context coordinator: lanes, fusion, budgets and the coverage receipt.

The permission model is deliberately symmetric:

- the raw reader only queries inside the caller's current source allow-list;
- the memory reader only queries inside the caller's current scopes, and skips
  any memory HY has hidden since the lane ran;
- after both lanes finish, selected identities are revalidated once more, so a
  revocation during a slower lane cannot release an earlier hit.

Empty never means "not searched", and a semantic failure never becomes empty.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from one_memory.background_work import interactive_read_pool
from one_memory.context_expand import ExpansionAuthority, ExpansionRefSigner
from one_memory.context_models import MemoryContextItem
from one_memory.context_raw import _check, _connection, _execute, _readable
from one_memory.retrieval.models import (
    EvidenceGroup,
    EvidenceSpan,
    GovernedEvidence,
    ObjectReference,
    RawEvidence,
    RetrievalPath,
    RetrievalResponse,
    TimeRange,
)
from one_memory.retrieval.query_plan import plan_local_query
from one_memory.retrieval.raw_candidates import RawCandidateBatch
from one_memory.retrieval.raw_evidence import materialize_raw_candidates


class RawIndexLookup(Protocol):
    def lookup_raw(
        self,
        query: str,
        *,
        source_ids: frozenset[str],
        deadline: float,
        time_range: TimeRange | None = None,
    ) -> tuple[RawCandidateBatch, bool]: ...


ExpandRefs = tuple[ExpansionRefSigner, ExpansionAuthority]


Kind = Literal["raw", "memory"]


_RAW_METADATA = """
    substr(e.evidence_id,1,513) AS evidence_id, e.revision,
    substr(e.source_id,1,129) AS source_id,
    substr(e.conversation_id,1,513) AS conversation_id,
    substr(e.role,1,33) AS role,
    substr(e.written_by,1,33) AS written_by,
    substr(e.created_at,1,65) AS created_at
"""


@dataclass(frozen=True)
class ContextAuthorityV2:
    raw_allowed: bool
    memory_allowed: bool
    raw_sources: frozenset[str] | None
    memory_scopes: frozenset[str] | None


@dataclass(frozen=True)
class CoordinatorQuery:
    request_id: str
    query: str
    kinds: tuple[Kind, ...]
    intent: str = "fact"
    limit: int = 8
    character_budget: int = 6000
    semantic_ids: tuple[str, ...] = ()
    semantic_outcome: Literal["answered", "failed", "timed_out", "absent"] = "absent"
    hidden_memory_ids: frozenset[str] | None = None
    lexical_terms: tuple[str, ...] = ()
    time_range: object | None = None


@dataclass(frozen=True)
class LaneResult:
    items: tuple[RawEvidence | GovernedEvidence, ...]
    status: Literal["ok", "empty", "unavailable", "not_searched", "degraded"]
    reason: str | None
    channels: tuple[str, ...]
    truncated: bool
    #: A hit existed but the shared budget dropped it whole.
    starved: bool = False
    #: Bounded candidate probing left authorized rows unranked.
    coverage_limited: bool = False


def _semantic_only_raw(item: RawEvidence) -> bool:
    """Semantic overlap alone is a candidate, not a confirmed recall item."""
    return set(item.matched_by) == {"semantic"}


@dataclass(frozen=True)
class StageTimings:
    raw_ms: float = 0.0
    memory_ms: float = 0.0
    raw_revalidation_ms: float = 0.0
    memory_revalidation_ms: float = 0.0
    fusion_ms: float = 0.0
    serialization_ms: float = 0.0


def _characters(item: RawEvidence | GovernedEvidence) -> int:
    if isinstance(item, RawEvidence):
        return sum(len(span.text) for span in item.spans)
    return len(item.content)


def _identity(item: RawEvidence | GovernedEvidence) -> tuple[str, str]:
    if isinstance(item, RawEvidence):
        return ("raw", item.evidence_id)
    return ("memory", item.memory_id)


def _object_refs(
    item: RawEvidence | GovernedEvidence, object_keys: tuple[str, ...]
) -> list[dict[str, object]]:
    if not isinstance(item, RawEvidence) or not object_keys:
        return []
    references = []
    for key in object_keys:
        for span in item.spans:
            position = span.text.find(key)
            if position >= 0:
                start = span.start_cp + position
                references.append(
                    ObjectReference(
                        kind="url" if "://" in key else "path",
                        evidence_id=item.evidence_id,
                        revision=item.revision,
                        span=EvidenceSpan(
                            start_cp=start,
                            end_cp=start + len(key),
                            text=key,
                        ),
                    ).model_dump(mode="json")
                )
                break
    return references


def _memory_request(query: CoordinatorQuery) -> Any:
    """Adapter so the governed reader can consume the v2 coordinator query."""
    from one_memory.context_models import ContextSearchRequest

    return ContextSearchRequest(
        query=query.query,
        kinds=("memory",),
        limit=query.limit,
        character_budget=query.character_budget,
    )


def _managed(
    items: tuple[RawEvidence | GovernedEvidence, ...], character_budget: int, limit: int
) -> tuple[list[RawEvidence | GovernedEvidence], bool]:
    selected: list[RawEvidence | GovernedEvidence] = []
    used = 0
    truncated = False
    for item in items:
        if len(selected) >= limit:
            truncated = True
            break
        size = _characters(item)
        if used + size > character_budget:
            truncated = True
            continue
        selected.append(item)
        used += size
    return selected, truncated


def _group_missing_requirements(item: RawEvidence | GovernedEvidence) -> list[str]:
    if isinstance(item, RawEvidence) and _semantic_only_raw(item):
        return ["relevance_confirmation"]
    return []


def _path(
    status: str,
    reason: str | None,
    channels: tuple[str, ...],
    coverage: str,
    truncated: bool,
    returned: int,
    *,
    coverage_limited: bool = False,
) -> RetrievalPath:
    limits = []
    if truncated:
        limits.append("character_budget")
    if coverage_limited:
        limits.append("coverage_budget")
    return RetrievalPath.model_validate(
        {
            "status": status,
            "reason": reason,
            "returned_count": returned,
            "searched_channels": list(channels),
            "coverage": coverage,
            "truncated_by": limits,
        }
    )


def read_lexical_raw_lane(
    database_path: Path,
    query: CoordinatorQuery,
    *,
    source_ids: frozenset[str],
    deadline: float,
) -> LaneResult:
    if not source_ids:
        return LaneResult((), "unavailable", "permission_required", (), False)
    if not (query.lexical_terms or query.query.strip()):
        return LaneResult((), "empty", None, ("lexical",), False)
    # The excerpt is bounded by the global character cap, not by the caller's
    # per-request budget: a budget of 3 must still find the hit and then drop
    # it whole, rather than shrink the SQL excerpt until nothing matches.
    body_limit = 20000
    plan = plan_local_query(query.query)
    try:
        with _connection(database_path, deadline) as db:
            # A revoked source must read as denied, not as a completed search
            # that happened to find nothing.
            revoked = _execute(
                db,
                deadline,
                """SELECT 1 FROM source_grants g
                WHERE g.user_id='local' AND g.revoked_at IS NOT NULL
                  AND g.source_id IN (SELECT value FROM json_each(?)) LIMIT 1""",
                [json.dumps(sorted(source_ids))],
            ).fetchone()
            if revoked is not None:
                return LaneResult((), "unavailable", "permission_required", ("lexical",), False)
            index = _execute(
                db,
                deadline,
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_search'",
            ).fetchone()
            if index is None:
                return LaneResult((), "unavailable", "read_unavailable", ("lexical",), False)
            rows, sampled, probe_saturated = _lexical_raw_rows(
                db,
                query,
                source_ids=source_ids,
                deadline=deadline,
                body_limit=body_limit,
            )
            _check(deadline)
            items: list[RawEvidence] = []
            truncated = bool(sampled)
            starved = False
            for row in rows:
                if len(items) >= query.limit:
                    truncated = True
                    continue
                channels = ("lexical", "object") if plan.object_keys else ("lexical",)
                item = _raw_item(row, row["start1"] - 1, channels)
                if _characters(item) > query.character_budget:
                    truncated = True
                    starved = True
                    continue
                if row["body_truncated"]:
                    truncated = True
                items.append(item)
    except TimeoutError:
        return LaneResult((), "unavailable", "timeout_budget", ("lexical",), True)
    except (sqlite3.Error, OSError, ValueError):
        return LaneResult((), "unavailable", "read_unavailable", ("lexical",), False)
    return LaneResult(
        tuple(items),
        "ok" if items else "empty",
        None,
        ("lexical", "object") if plan.object_keys else ("lexical",),
        truncated,
        # Starvation means: this lane matched, then dropped the item whole. An
        # ordinary no-match must stay empty, and an unconfigured semantic lane
        # is not starvation.
        starved and not items,
        probe_saturated,
    )


def _lexical_raw_rows(
    db: sqlite3.Connection,
    query: CoordinatorQuery,
    *,
    source_ids: frozenset[str],
    deadline: float,
    body_limit: int,
) -> tuple[Sequence[sqlite3.Row], bool, bool]:
    """Exact code-point spans for the current authorized lexical hits."""
    from one_memory import context_raw
    from one_memory.imports.storage import TRIGRAM_FLOOR, _as_fts_phrase

    readable, authority = context_raw._readable(source_ids)
    plan = plan_local_query(query.query)
    query_text = " ".join((*plan.object_keys, query.query))
    terms, sampled = context_raw._terms_with_sampling(query_text)
    candidate_probe_cap = context_raw._candidate_probe_cap
    matches: list[str] = []
    values: list[object] = []
    fts_probe: str | None = None
    time_clause = ""
    time_values: list[object] = []
    if plan.time_range is not None:
        # Filtering before LIMIT matters: newest-first rows outside the window
        # can otherwise consume the candidate budget and hide an older hit.
        # Stored timestamps may carry non-UTC offsets. Compare instants, not
        # their textual spellings; otherwise a +08:00 value can be rejected even
        # though it lies inside the UTC half-open window.
        time_clause = (
            "AND datetime(e.created_at) >= datetime(?) AND datetime(e.created_at) < datetime(?)"
        )
        time_values = [
            plan.time_range.start.isoformat(),
            plan.time_range.end.isoformat(),
        ]
    long_terms = [term for term in terms if len(term) >= TRIGRAM_FLOOR]
    declarative = (
        "CASE WHEN e.verbatim LIKE '%？%' OR e.verbatim LIKE '%?%' "
        "OR e.verbatim LIKE '%只回答%' THEN 0 ELSE 1 END"
        if context_raw._prefers_declared_facts(query.query)
        else "1"
    )
    person_authored = "CASE WHEN e.role='user' THEN 1 ELSE 0 END"
    query_echo = (
        "CASE WHEN trim(e.verbatim)=? OR trim(e.verbatim)=? THEN 0 ELSE 1 END"
        if context_raw._prefers_declared_facts(query.query)
        else "1"
    )
    echo_values: list[object] = (
        [query.query.strip(), f"{query.query.strip()}。"]
        if context_raw._prefers_declared_facts(query.query)
        else []
    )
    if long_terms:
        # The FTS OR can legally match nearly every row. Use the same bounded
        # candidate probe as v1 so v2 does not materialize and temp-sort the
        # whole authorized corpus. Saturation is reported by the caller.
        fts_probe = (
            f"SELECT rowid FROM evidence_search WHERE evidence_search MATCH ? "
            f"LIMIT {candidate_probe_cap()}"
        )
        values.append(" OR ".join(_as_fts_phrase(term) for term in long_terms))
    start = """max(1, coalesce((SELECT min(nullif(instr(lower(e.verbatim), lower(value)),0))
               FROM json_each(?)),1)-80)"""
    # Keep an explicit URL/path exact when possible; otherwise lexical terms can
    # fragment it into pieces that match unrelated conversations.
    object_values: list[object] = []
    if plan.object_keys:
        if len(plan.object_keys) == 1:
            # A single exact object is the common case. Drive the outer lookup
            # from its bounded FTS phrase so a 1m source index cannot be scanned
            # and temp-sorted before the object row is found.
            fts_probe = (
                f"SELECT rowid FROM evidence_search WHERE evidence_search MATCH ? "
                f"LIMIT {candidate_probe_cap()}"
            )
            matches = []
            values = [_as_fts_phrase(plan.object_keys[0])]
        else:
            fts_probe = None
        # FTS already indexes long URL/path strings. SQLite may prefer a source
        # index scan for a leading-wildcard LIKE and examine every authorized
        # row; the exact phrase keeps object lookup bounded by the trigram index.
        object_matches = [
            f"""e.rowid IN (SELECT rowid FROM evidence_search
                WHERE evidence_search MATCH ? LIMIT {candidate_probe_cap()})"""
            for _ in plan.object_keys
        ]
        # Short fragments from a URL/path (for example `v3`) must not fall back
        # to leading-wildcard LIKE over every authorized row. The exact object
        # phrase is the authoritative lookup; unrelated lexical fragments stay
        # out rather than turning an indexed object query into a full scan.
        lexical_matches = [
            match for term, match in zip(terms, matches, strict=False) if len(term) >= TRIGRAM_FLOOR
        ]
        # `matches` and `values` are aggregate expressions, not one per term.
        # With an exact object, drop short-term LIKE expressions by rebuilding
        # the lexical branch from long terms only.
        lexical_values: list[object] = []
        if long_terms:
            lexical_matches = [
                f"""e.rowid IN (SELECT rowid FROM evidence_search
                    WHERE evidence_search MATCH ? LIMIT {candidate_probe_cap()})"""
            ]
            lexical_values = [
                " OR ".join(_as_fts_phrase(term) for term in long_terms),
            ]
        if len(plan.object_keys) != 1:
            matches = [*object_matches, *lexical_matches]
            values = lexical_values
        object_values = [_as_fts_phrase(obj) for obj in plan.object_keys]
    probe_saturated = False
    if long_terms:
        # Bounded probes can fill before every authorized match was ranked.
        # One extra row tells us whether the probe saturated, so the caller
        # can expose `coverage_budget` instead of claiming complete coverage.
        saturated = _execute(
            db,
            deadline,
            f"""SELECT 1 FROM (SELECT rowid FROM evidence_search
                WHERE evidence_search MATCH ? LIMIT {candidate_probe_cap() + 1})
                LIMIT 1 OFFSET {candidate_probe_cap()}""",
            [" OR ".join(_as_fts_phrase(term) for term in long_terms)],
        ).fetchone()
        probe_saturated = saturated is not None
    if fts_probe:
        rows = _execute(
            db,
            deadline,
            f"""WITH probe AS MATERIALIZED ({fts_probe}),
                    candidates AS MATERIALIZED (
                        SELECT e.rowid AS rid,
                               ({declarative}) AS declarative,
                               ({person_authored}) AS person_authored,
                               ({query_echo}) AS query_echo,
                               e.created_at AS created_at,
                               e.evidence_id AS evidence_id
                        FROM probe CROSS JOIN evidence e ON e.rowid=probe.rowid
                        WHERE {readable} {time_clause}
                        ORDER BY person_authored DESC, declarative DESC,
                                 query_echo DESC, created_at DESC, evidence_id
                        LIMIT ?
                    )
                    SELECT {_RAW_METADATA}, substr(e.verbatim,{start},?) AS text,
                           length(e.verbatim)>? AS body_truncated, {start} AS start1,
                           candidates.declarative, candidates.person_authored,
                           candidates.query_echo
                    FROM candidates JOIN evidence e ON e.rowid=candidates.rid
                    """,
            [
                *values,
                *echo_values,
                *authority,
                *time_values,
                query.limit + 1,
                json.dumps(terms),
                body_limit,
                body_limit,
                json.dumps(terms),
            ],
        ).fetchall()
    else:
        rows = _execute(
            db,
            deadline,
            f"""SELECT {_RAW_METADATA}, substr(e.verbatim,{start},?) AS text,
                       length(e.verbatim)>? AS body_truncated, {start} AS start1,
                       ({declarative}) AS declarative,
                       ({person_authored}) AS person_authored,
                       ({query_echo}) AS query_echo
                FROM evidence e WHERE {readable} {time_clause}
                    AND ({" OR ".join(matches)})
                ORDER BY person_authored DESC, declarative DESC, query_echo DESC,
                         e.created_at DESC, e.evidence_id LIMIT ?""",
            [
                json.dumps(terms),
                body_limit,
                body_limit,
                json.dumps(terms),
                *echo_values,
                *authority,
                *time_values,
                *object_values,
                *values,
                query.limit + 1,
            ],
        ).fetchall()
    return rows, bool(sampled), probe_saturated


def _raw_item(row: sqlite3.Row, start1: int, matched_by: tuple[str, ...]) -> RawEvidence:
    start = max(0, int(start1) - 1)
    text = row["text"]
    created = row["created_at"]
    event_time = None
    if created:
        event_time = datetime.fromisoformat(created).astimezone(UTC)
    return RawEvidence.model_validate(
        {
            "evidence_id": row["evidence_id"],
            "revision": row["revision"],
            "source_id": row["source_id"],
            "conversation_id": row["conversation_id"],
            "role": row["role"],
            "written_by": "person" if row["role"] == "user" else "agent",
            "event_time": event_time.isoformat() if event_time else None,
            "spans": [{"start_cp": start, "end_cp": start + len(text), "text": text}],
            "matched_by": list(matched_by),
        }
    )


def warm_raw_read_path(
    database_path: Path,
    *,
    timeout_ms: int = 5000,
) -> bool:
    """Warm the raw FTS read path without side effects.

    C5 measured the first uncached raw FTS read at ~1.9s and every later read
    at ~470-520ms, which can push the first WorkBuddy turn over budget. Runtime
    startup may call this once, after the read pool is ready and before the
    service reports `runtime_verified`, to move that one-time cost off the hook
    path.

    Guarantees:
    - uses the same read-only connection path as a real recall;
    - uses a constant sentinel phrase, never user text;
    - writes no Evidence, no call event, and mutates no grant;
    - discards all rows; returns True when the probe completed without error.
    """
    warm_query = "__om_recall_warm__"
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000
    from one_memory import context_raw
    from one_memory.imports.storage import _as_fts_phrase

    try:
        with _connection(database_path, deadline) as db:
            index = _execute(
                db,
                deadline,
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_search'",
            ).fetchone()
            if index is None:
                # No FTS index yet: still warm the connection/statement cache.
                _execute(db, deadline, "SELECT 1 FROM evidence LIMIT 1").fetchall()
                return True
            # Same bounded shape as the real probe, hit or miss, so the FTS
            # b-tree, statement and page cache are exercised.
            _execute(
                db,
                deadline,
                f"""SELECT 1 FROM evidence_search
                    WHERE evidence_search MATCH ? LIMIT {context_raw._candidate_probe_cap()}""",
                [_as_fts_phrase(warm_query)],
            ).fetchall()
        return True
    except (TimeoutError, sqlite3.Error, OSError, ValueError):
        return False


def read_semantic_raw_lane(
    index: RawIndexLookup | None,
    database_path: Path,
    query: CoordinatorQuery,
    *,
    source_ids: frozenset[str],
    deadline: float,
) -> LaneResult:
    if not source_ids:
        return LaneResult((), "unavailable", "permission_required", (), False)
    # No derived index is the no-model baseline, not a failure: the lexical lane
    # must still answer. Semantic is genuinely unsearched in that state, so it
    # must not claim a channel or a coverage result it never produced.
    if index is None:
        return LaneResult((), "not_searched", None, (), False)
    try:
        batch, unpublished = index.lookup_raw(
            query.query,
            source_ids=source_ids,
            deadline=deadline,
            time_range=plan_local_query(query.query).time_range,
        )
    except Exception:
        return LaneResult((), "unavailable", "index_unavailable", ("semantic",), False)
    if not isinstance(batch, RawCandidateBatch):
        return LaneResult((), "unavailable", "index_unavailable", ("semantic",), False)
    if batch.reason == "not_authorized":
        return LaneResult((), "unavailable", "permission_required", ("semantic",), False)
    if batch.reason == "deadline":
        return LaneResult((), "unavailable", "timeout_budget", ("semantic",), True)
    if batch.reason in {"vector_unavailable", "index_unavailable"}:
        return LaneResult((), "unavailable", batch.reason, ("semantic",), False)
    store, generation = _index_state(index, database_path)
    from one_memory.retrieval.index_store import RetrievalIndexStore

    store = cast("RetrievalIndexStore | None", store)
    if store is None or generation is None:
        return LaneResult((), "unavailable", "index_unavailable", ("semantic",), False)
    try:
        materialized = materialize_raw_candidates(
            store,
            batch,
            generation_id=generation,
            source_ids=source_ids,
            deadline=deadline,
            character_budget=query.character_budget,
        )
    except Exception:
        return LaneResult((), "unavailable", "index_unavailable", ("semantic",), False)
    if materialized.reason == "deadline":
        return LaneResult((), "unavailable", "timeout_budget", ("semantic",), True)
    if materialized.reason in {"index_unavailable", "not_authorized"}:
        return LaneResult((), "unavailable", materialized.reason, ("semantic",), False)
    complete = materialized.complete and not unpublished
    if materialized.reason == "evidence_changed":
        status: Literal["ok", "empty", "unavailable", "not_searched"] = "unavailable"
        reason = "evidence_changed"
    elif not materialized.items and not complete:
        status, reason = "unavailable", "index_incomplete"
    elif materialized.reason == "character_budget":
        status, reason = "ok", None
    else:
        status, reason = ("ok" if materialized.items else "empty"), None
    return LaneResult(
        tuple(materialized.items),
        status,
        reason,
        ("semantic",),
        materialized.reason == "character_budget",
    )


def _index_state(index: RawIndexLookup, database_path: Path) -> tuple[object | None, str | None]:
    store = getattr(index, "_store", None)
    generation = None
    snapshot = getattr(index, "snapshot", None)
    if snapshot is not None:
        try:
            generation = snapshot().generation_id
        except Exception:
            generation = None
    if store is None:
        try:
            from one_memory.retrieval.index_store import RetrievalIndexStore

            store = RetrievalIndexStore(database_path)
        except Exception:
            store = None
    return store, generation


def read_memory_lane(
    database_path: Path,
    query: CoordinatorQuery,
    *,
    scopes: frozenset[str] | None,
    deadline: float,
) -> LaneResult:
    if scopes == frozenset():
        return LaneResult((), "unavailable", "permission_required", (), False)
    trusted = tuple(
        memory_id
        for memory_id in query.semantic_ids
        if isinstance(memory_id, str) and 1 <= len(memory_id) <= 512
    )
    _ = trusted
    plan = plan_local_query(query.query)
    # Only claim `governed_semantic` when HY/derived semantics actually answered.
    # A failed semantic call is unavailable, never a different-looking empty.
    channels = (
        ("lexical", "governed_semantic")
        if query.semantic_outcome in {"answered", "absent"}
        else ("lexical",)
    )
    # Reuse the measured governed reader: it registers the predicates and
    # freshness functions this lane depends on. Re-implementing the SQL here
    # fails with a missing function and would read as read_unavailable.
    from one_memory.context_memory import read_memory_context

    try:
        lane = read_memory_context(
            database_path,
            _memory_request(query),
            scopes=scopes,
            deadline=deadline,
            semantic_ids=query.semantic_ids,
            semantic_outcome=query.semantic_outcome,
            hidden_memory_ids=query.hidden_memory_ids,
            time_range=plan.time_range,
        )
    except TimeoutError:
        return LaneResult((), "unavailable", "timeout_budget", channels, True)
    except (sqlite3.Error, OSError, ValueError):
        return LaneResult((), "unavailable", "read_unavailable", channels, False)
    if query.semantic_outcome == "failed":
        return LaneResult((), "unavailable", "semantic_unavailable", channels, False)
    if query.semantic_outcome == "timed_out":
        return LaneResult((), "unavailable", "timeout_budget", channels, True)
    items: list[GovernedEvidence] = []
    for item in lane.items:
        if not isinstance(item, MemoryContextItem):
            continue
        matched_by = item.matched_by
        if not matched_by:
            # Older internal callers do not populate the origin field. A
            # governed reader that answered without a lexical anchor is still a
            # semantic augmentation; a lexical-only read must say lexical.
            matched_by = (
                ("governed_semantic",)
                if query.semantic_outcome == "answered" and trusted
                else ("lexical",)
            )
        items.append(
            GovernedEvidence.model_validate(
                {
                    "memory_id": item.memory_id,
                    "revision": item.revision,
                    "content": item.content[: query.character_budget],
                    "memory_kind": item.memory_kind,
                    "updated_at": item.updated_at.isoformat(),
                    "evidence_ids": list(item.evidence_ids),
                    "matched_by": list(matched_by),
                }
            )
        )
    return LaneResult(
        tuple(items),
        (
            "ok"
            if items
            else cast(
                Literal["ok", "empty", "unavailable", "not_searched"],
                "empty" if lane.status == "empty" or not lane.items else "degraded",
            )
        ),
        None,
        channels,
        bool(lane.truncated_by) or len(items) != len(lane.items),
    )


def revalidate_raw(
    database_path: Path,
    items: tuple[RawEvidence, ...],
    *,
    source_ids: frozenset[str] | None,
    deadline: float,
) -> tuple[RawEvidence, ...]:
    if not items or not source_ids:
        return ()
    readable, values = _readable(source_ids)
    try:
        with _connection(database_path, deadline) as db:
            # Only the sources this caller may read matter: a revocation
            # elsewhere must not empty this caller's retained raw hits.
            revoked = _execute(
                db,
                deadline,
                """SELECT 1 FROM source_grants g
                WHERE g.user_id='local' AND g.revoked_at IS NOT NULL
                  AND g.source_id IN (SELECT value FROM json_each(?)) LIMIT 1""",
                [json.dumps(sorted(source_ids))],
            ).fetchone()
            if revoked is not None:
                return ()
    except (TimeoutError, Exception):
        return ()
    kept = []
    try:
        with _connection(database_path, deadline) as db:
            for item in items:
                row = _execute(
                    db,
                    deadline,
                    f"""SELECT e.source_id,e.conversation_id,e.role,e.written_by,e.created_at
                    FROM evidence e WHERE {readable} AND e.evidence_id=? AND e.revision=?""",
                    [*values, item.evidence_id, item.revision],
                ).fetchone()
                if row is None or row["source_id"] != item.source_id:
                    continue
                created = row["created_at"]
                event_time = datetime.fromisoformat(created).astimezone(UTC) if created else None
                if (row["conversation_id"], row["role"], event_time) != (
                    item.conversation_id,
                    item.role,
                    item.event_time,
                ):
                    continue
                kept.append(item)
    except (TimeoutError, Exception):
        return ()
    return tuple(kept)


def revalidate_memory(
    database_path: Path,
    query: CoordinatorQuery,
    items: tuple[GovernedEvidence, ...],
    *,
    scopes: frozenset[str] | None,
    deadline: float,
) -> tuple[GovernedEvidence, ...]:
    if not items or scopes == frozenset():
        return ()
    plan = plan_local_query(query.query)
    # Reuse the measured governed revalidator: it owns the snapshot, registered
    # predicates and reference checks. Re-implementing the predicate here drops
    # valid memories because those functions are not registered.
    from one_memory.context_memory import revalidate_memory_context

    try:
        candidates = tuple(
            MemoryContextItem(
                memory_id=item.memory_id,
                revision=item.revision,
                content=item.content,
                memory_kind=item.memory_kind,
                updated_at=item.updated_at,
                evidence_ids=item.evidence_ids,
            )
            for item in items
        )
        kept = revalidate_memory_context(
            database_path,
            candidates,
            scopes=scopes,
            deadline=deadline,
            hidden_memory_ids=query.hidden_memory_ids,
            time_range=plan.time_range,
        )
    except Exception:
        return ()
    kept_ids = {item.memory_id for item in kept}
    return tuple(
        item
        for item in items
        if item.memory_id in kept_ids
        and (
            plan.time_range is None
            or any(
                current.memory_id == item.memory_id
                and plan.time_range.start <= current.updated_at < plan.time_range.end
                for current in kept
            )
        )
    )


def coordinate_context_v2(
    authority: ContextAuthorityV2,
    query: CoordinatorQuery,
    *,
    database_path: Path,
    index: RawIndexLookup | None = None,
    deadline: float,
    expand_refs: ExpandRefs | None = None,
) -> RetrievalResponse:
    started = time.monotonic()
    timings = StageTimings()
    plan = plan_local_query(query.query)
    raw = LaneResult((), "not_searched", "not_searched", (), False)
    memory = LaneResult((), "not_searched", "not_searched", (), False)
    if "raw" in query.kinds and authority.raw_allowed and authority.raw_sources:
        raw_started = time.monotonic()
        lexical = read_lexical_raw_lane(
            database_path, query, source_ids=authority.raw_sources, deadline=deadline
        )
        semantic = read_semantic_raw_lane(
            index, database_path, query, source_ids=authority.raw_sources, deadline=deadline
        )
        timings = StageTimings(raw_ms=(time.monotonic() - raw_started) * 1000)
        combined: dict[tuple[str, str], RawEvidence] = {}
        for item in (*lexical.items, *semantic.items):
            if not isinstance(item, RawEvidence):
                continue
            identity = _identity(item)
            previous = combined.get(identity)
            if previous is None or _semantic_only_raw(previous):
                combined[identity] = item
        # Semantic similarity is a candidate signal, not a verified recall
        # result. Keep semantic-only candidates visible in coverage, but do not
        # release their bodies for host injection unless the item is
        # independently confirmed by the lexical/object lane. A global distance
        # threshold is deliberately not used: the measured positive and
        # negative distributions overlap.
        semantic_only = {_identity(item) for item in combined.values() if _semantic_only_raw(item)}
        confirmed = {_identity(item) for item in lexical.items if isinstance(item, RawEvidence)}
        deferred_raw = semantic_only - confirmed
        if deferred_raw:
            combined = {
                identity: item
                for identity, item in combined.items()
                if identity not in deferred_raw
            }
        channels = tuple(dict.fromkeys((*lexical.channels, *semantic.channels)))
        if lexical.status == "unavailable":
            status: Literal["ok", "empty", "unavailable", "not_searched", "degraded"] = (
                "unavailable"
            )
            reason = lexical.reason
        elif semantic.status == "unavailable":
            status = "unavailable"
            reason = semantic.reason
        elif combined:
            status, reason = "ok", semantic.reason if semantic.status != "ok" else None
        elif deferred_raw:
            status, reason = "degraded", "relevance_confirmation"
        elif lexical.status == "empty":
            status, reason = ("empty" if semantic.status != "not_searched" else "ok"), None
        else:
            status, reason = "empty", None
        truncated = lexical.truncated or semantic.truncated
        raw = LaneResult(
            tuple(combined.values()),
            status,
            reason,
            channels,
            truncated,
            lexical.starved,
            lexical.coverage_limited,
        )
        if deferred_raw:
            object.__setattr__(raw, "coverage_limited", True)
    elif "raw" in query.kinds and not authority.raw_allowed:
        raw = LaneResult((), "unavailable", "permission_required", (), False)
    if "memory" in query.kinds:
        if authority.memory_allowed:
            memory_started = time.monotonic()
            memory = read_memory_lane(
                database_path, query, scopes=authority.memory_scopes, deadline=deadline
            )
            object.__setattr__(
                timings,
                "memory_ms",
                (time.monotonic() - memory_started) * 1000,
            )
        else:
            memory = LaneResult((), "unavailable", "permission_required", (), False)

    # A lane that was denied, failed or timed out must not release candidates
    # through the shared budget. The other lane keeps its own authorized result,
    # and the whole answer stays partial instead of looking complete.
    raw_release: tuple[RawEvidence, ...] = (
        ()
        if raw.status in {"unavailable", "not_searched"}
        else tuple(item for item in raw.items if isinstance(item, RawEvidence))
    )
    raw_revalidation_started = time.monotonic()
    final_raw = revalidate_raw(
        database_path, raw_release, source_ids=authority.raw_sources, deadline=deadline
    )
    object.__setattr__(
        timings,
        "raw_revalidation_ms",
        (time.monotonic() - raw_revalidation_started) * 1000,
    )
    memory_release: tuple[GovernedEvidence, ...] = tuple(
        item for item in memory.items if isinstance(item, GovernedEvidence)
    )
    memory_revalidation_started = time.monotonic()
    final_memory = revalidate_memory(
        database_path, query, memory_release, scopes=authority.memory_scopes, deadline=deadline
    )
    object.__setattr__(
        timings,
        "memory_revalidation_ms",
        (time.monotonic() - memory_revalidation_started) * 1000,
    )
    fusion_started = time.monotonic()
    fused_source: list[RawEvidence | GovernedEvidence] = []
    raw_list = list(final_raw)
    memory_list = list(final_memory)
    while raw_list or memory_list:
        if raw_list:
            fused_source.append(raw_list.pop(0))
        if memory_list:
            fused_source.append(memory_list.pop(0))
    selected, truncated = _managed(tuple(fused_source), query.character_budget, query.limit)
    object.__setattr__(timings, "fusion_ms", (time.monotonic() - fusion_started) * 1000)

    def raw_path() -> RetrievalPath:
        if "raw" not in query.kinds:
            return _path("not_requested", "not_requested", (), "not_searched", False, 0)
        returned = sum(1 for item in selected if isinstance(item, RawEvidence))
        dropped = len(final_raw) != len(raw.items)
        if raw.status == "unavailable":
            return _path("unavailable", raw.reason, raw.channels, "unavailable", True, 0)
        if raw.status == "not_searched":
            return _path("unavailable", raw.reason, raw.channels, "not_searched", False, 0)
        if dropped and not final_raw:
            return _path("unavailable", "permission_required", raw.channels, "unavailable", True, 0)
        # Lexical-only is complete when the lexical lane actually answered. An
        # unconfigured semantic lane is a capability absence, not proof that
        # less than the authorized range was searched.
        complete = (
            raw.status in {"ok", "empty"}
            and not dropped
            and not truncated
            and not raw.coverage_limited
            and returned == len(final_raw)
        )
        coverage = "complete" if complete else "partial"
        # A lane that found something but could not return it is degraded, not
        # empty: "empty" would claim the authorized range had no match.
        starved_raw = raw.starved or (bool(raw.items) and not returned)
        status = (
            "ok"
            if returned and not raw.coverage_limited
            else "empty"
            if complete and not starved_raw
            else "degraded"
        )
        reason = "permission_required" if dropped else None
        if status == "degraded" and reason is None:
            reason = (
                "count_budget"
                if truncated
                else "coverage_budget"
                if raw.coverage_limited and not deferred_raw
                else "relevance_confirmation"
                if deferred_raw
                else "character_budget"
            )
        return _path(
            status,
            reason,
            raw.channels,
            coverage,
            truncated,
            returned,
            coverage_limited=raw.coverage_limited,
        )

    def memory_path() -> RetrievalPath:
        if "memory" not in query.kinds:
            return _path("not_requested", "not_requested", (), "not_searched", False, 0)
        returned = sum(1 for item in selected if isinstance(item, GovernedEvidence))
        dropped = len(final_memory) != len(memory.items)
        if memory.status == "unavailable":
            return _path("unavailable", memory.reason, memory.channels, "unavailable", True, 0)
        # When no semantic capability exists, governed memories are simply not
        # searched: that is `empty`, and it must not be reported as degraded.
        unsearched = query.semantic_outcome == "absent" and not query.semantic_ids
        complete = (
            query.semantic_outcome in {"answered", "absent"}
            and not dropped
            and returned == len(final_memory)
        )
        coverage = "complete" if complete else "partial"
        # A raw hit dropped by the shared budget is still partial, so an empty
        # raw path must not coexist with retained memory evidence.
        starved = bool(raw.items) and not returned and truncated
        starved = starved or bool(memory.items) and not returned
        status = (
            "ok"
            if returned
            else ("empty" if (complete or unsearched) and not starved else "degraded")
        )
        reason = "permission_required" if dropped else None
        return _path(status, reason, memory.channels, coverage, False, returned)

    paths = (raw_path(), memory_path())
    searched = [path for path in paths if path.status in {"ok", "empty", "degraded"}]
    # `empty` is only honest when every requested lane actually completed. A
    # denied or unavailable lane makes the whole answer partial, never empty.
    lanes_ok = all(path.status in {"ok", "empty", "degraded", "not_requested"} for path in paths)
    complete_all = (
        bool(searched) and lanes_ok and all(path.coverage == "complete" for path in searched)
    )
    state = (
        "grounded"
        if selected and complete_all
        else "empty"
        if not selected and searched and complete_all
        else "partial"
        if searched
        else "unavailable"
    )

    # Finding text, finding the requested object, and confirming a decision are
    # separate states. The coordinator never marks an answer "supported"; it
    # only grounds evidence. Missing confirmation stays visible so the host can
    # ask rather than infer adoption from a lexical hit.
    def with_expand_ref(item: RawEvidence | GovernedEvidence) -> RawEvidence | GovernedEvidence:
        if not isinstance(item, RawEvidence) or expand_refs is None:
            return item
        signer, expand_authority = expand_refs
        if item.source_id not in expand_authority.source_ids:
            return item
        return item.model_copy(
            update={
                "expand_ref": signer.issue(
                    authority=expand_authority,
                    source_id=item.source_id,
                    conversation_id=item.conversation_id,
                    evidence_id=item.evidence_id,
                    evidence_revision=item.revision,
                )
            }
        )

    emitted = tuple(with_expand_ref(item) for item in selected)
    groups = tuple(
        EvidenceGroup.model_validate(
            {
                "group_id": f"group-{index}",
                "answer_target": query.intent,
                "decision_status": "missing_confirmation",
                "missing_requirements": _group_missing_requirements(item),
                "object_refs": _object_refs(item, plan.object_keys),
                "items": [group_item.model_dump(mode="json") for group_item in (item,)],
            }
        )
        for index, item in enumerate(emitted)
    )
    serialization_started = time.monotonic()
    response = RetrievalResponse.model_validate(
        {
            "request_id": query.request_id,
            "state": state,
            "paths": {
                "raw": paths[0].model_dump(mode="json"),
                "memory": paths[1].model_dump(mode="json"),
            },
            "plan": {
                "intent": query.intent,
                "interpreted_time_range": (
                    plan.time_range.model_dump(mode="json") if plan.time_range else None
                ),
                "time_origin": "rule" if plan.time_range else "none",
                "unresolved_constraints": list(plan.ambiguous_constraints),
            },
            "groups": [group.model_dump(mode="json") for group in groups],
            "budget": {
                "used_characters": sum(
                    _characters(item)
                    + sum(len(reference.span.text) for reference in group.object_refs)
                    for item, group in zip(selected, groups, strict=True)
                ),
                "total_ms": (time.monotonic() - started) * 1000,
                "truncated": bool(truncated),
                "stage_ms": {
                    "raw_ms": timings.raw_ms,
                    "memory_ms": timings.memory_ms,
                    "raw_revalidation_ms": timings.raw_revalidation_ms,
                    "memory_revalidation_ms": timings.memory_revalidation_ms,
                    "fusion_ms": timings.fusion_ms,
                    "serialization_ms": 0.0,
                },
            },
        }
    )
    response.model_dump_json()
    object.__setattr__(
        timings,
        "serialization_ms",
        (time.monotonic() - serialization_started) * 1000,
    )
    return response.model_copy(
        update={
            "budget": response.budget.model_copy(
                update={
                    "stage_ms": {
                        "raw_ms": timings.raw_ms,
                        "memory_ms": timings.memory_ms,
                        "raw_revalidation_ms": timings.raw_revalidation_ms,
                        "memory_revalidation_ms": timings.memory_revalidation_ms,
                        "fusion_ms": timings.fusion_ms,
                        "serialization_ms": timings.serialization_ms,
                    }
                }
            )
        }
    )


async def search_context_v2(
    authority: ContextAuthorityV2,
    query: CoordinatorQuery,
    *,
    database_path: Path,
    index: RawIndexLookup | None = None,
    deadline: float,
    expand_refs: ExpandRefs | None = None,
) -> RetrievalResponse:
    """Event-loop wrapper so blocking lane reads stay in `to_thread`."""
    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(
        interactive_read_pool(),
        partial(
            coordinate_context_v2,
            authority,
            query,
            database_path=database_path,
            index=index,
            deadline=deadline,
            expand_refs=expand_refs,
        ),
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # shield keeps the native worker alive. The second await keeps the
        # caller's cancellation attached until that worker reaches its own
        # deadline/progress-handler cleanup, so no unowned lane can finish and
        # release late content.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # The surrounding task is already cancelled, so a second
                # cancellation (or any further cancel) must not detach the
                # native worker before it reaches its own cleanup boundary.
                continue
        worker.exception()
        raise
