"""Bounded lexical context reads; no model, index repair or store initialization."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol, cast

from one_memory.context_models import (
    ContextLaneResult,
    ContextLimit,
    ContextReason,
    ContextSearchChannel,
    ContextSearchRequest,
    ContextSemanticStatus,
    MemoryContextItem,
)
from one_memory.database_access import main_database
from one_memory.memory_evidence_freshness import register_memory_freshness
from one_memory.recall import _terms
from one_memory.retrieval.models import Channel, RetrievalPath, TimeRange
from one_memory.semantic_deadline import SemanticMemoryHit, SemanticOutcome
from one_memory.source_read_access import memory_source_readable

_BODY_LIMIT = 20000
_REFERENCE_LIMIT = 128
_CANDIDATE_LIMIT = 21
_TERM_LIMIT = 32
_CANDIDATE_TERM_CAP = 5_000
_CORRECTION_DEPTH_LIMIT = 32
_CURRENT_QUERY_MARKERS = ("现在", "当前", "现行", "最新", "now", "current", "latest")


class _CandidatePlan(Protocol):
    predicate: str
    values: tuple[str, ...]
    complete: bool


class _CandidatePlanFunction(Protocol):
    def __call__(
        self,
        connection: sqlite3.Connection,
        terms: Sequence[str],
        *,
        alias: str = "m",
        limit: int = 200,
    ) -> _CandidatePlan | None: ...


class _CandidatePredicateFunction(Protocol):
    def __call__(
        self,
        connection: sqlite3.Connection,
        terms: Sequence[str],
        *,
        alias: str = "m",
    ) -> tuple[str | None, tuple[str, ...]]: ...


content_candidate_plan: _CandidatePlanFunction | None
content_candidate_predicate: _CandidatePredicateFunction | None

try:
    from one_memory.memory_governance import (
        CONTENT_CANDIDATE_PLAN_VERSION,
        CONTENT_CANDIDATE_UNION_VERSION,
    )
    from one_memory.memory_governance import content_candidate_plan as _content_candidate_plan
    from one_memory.memory_governance import (
        content_candidate_predicate as _content_candidate_predicate,
    )

    content_candidate_plan = cast(_CandidatePlanFunction, _content_candidate_plan)
    content_candidate_predicate = _content_candidate_predicate
except ImportError:  # C3's shadow API may land after this read path in a package.
    CONTENT_CANDIDATE_PLAN_VERSION = 0
    CONTENT_CANDIDATE_UNION_VERSION = 0
    content_candidate_plan = None
    content_candidate_predicate = None


def content_candidate_clause(
    db: sqlite3.Connection,
    terms: tuple[str, ...],
    *,
    threshold: int,
) -> tuple[str | None, tuple[str, ...]]:
    """Union each term's candidate set so multi-term OR semantics survive."""
    if not terms:
        return None, ()
    # C3's coverage-aware plan reports both truncation and whether the
    # selectivity filter retained every query term. Complete plans are safe
    # for any term count; incomplete plans fall back to the exact LIKE path.
    if CONTENT_CANDIDATE_PLAN_VERSION >= 1 and content_candidate_plan is not None:
        plan = content_candidate_plan(db, terms, alias="m")
        if plan is not None and plan.complete:
            # The plan may be complete but still contain a high-frequency term
            # alongside selective ones. Let C4's frequency split choose the
            # driving subset; the exact LIKE below remains authoritative.
            filtered = _all_term_candidate_union(db, terms, threshold=threshold)
            if filtered is not None:
                return filtered
            return plan.predicate, plan.values
        # C3's selectivity filter deliberately drops very common terms from an
        # otherwise usable shadow plan. That is correct for `complete=false`,
        # but falling all the way back to every governed row reintroduces the
        # full-table LIKE scan the shadow exists to avoid. Build the exact
        # all-term union here instead: it is still only a candidate narrowing;
        # the complete LIKE score and authority predicates below still decide
        # every returned row.
        union = _all_term_candidate_union(db, terms, threshold=threshold)
        if union is not None:
            return union
        if plan is None:
            return None, ()
        return None, ()
    # When only the legacy helper is available (or the shadow exists without
    # the coverage-aware plan API), C4 still owns a frequency-aware selective
    # union. It preserves all terms when no selective term exists; the exact
    # LIKE score below remains the authority for returned rows.
    if content_candidate_predicate is None:
        return _all_term_candidate_union(db, terms, threshold=threshold) or (None, ())
    if len(terms) != 1:
        return _all_term_candidate_union(db, terms, threshold=threshold) or (None, ())
    predicate, values = content_candidate_predicate(db, terms, alias="m")
    # The legacy helper does not expose completeness. A candidate SQL with
    # LIMIT may hide an authorized match, so it is not safe to consume.
    if predicate is None or "LIMIT" in predicate.upper():
        return None, ()
    return predicate, values


def _all_term_candidate_union(
    db: sqlite3.Connection,
    terms: tuple[str, ...],
    *,
    threshold: int,
) -> tuple[str, tuple[str, ...]] | None:
    """Candidate IDs from selective terms in C3's maintained shadow.

    The candidate table is an index, not an authorization source. This helper
    only removes rows that cannot contain a selective query term. If every
    term is common, it keeps the complete term set and the caller still has to
    decide with the exact LIKE plus authority predicates.
    """
    if not terms:
        return None
    present = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governed_memories_terms' LIMIT 1"
    ).fetchone()
    if present is None:
        return None
    counts = {
        str(term): int(count)
        for term, count in db.execute(
            "SELECT term,count(*) FROM governed_memories_terms "
            f"WHERE term IN ({','.join('?' * len(terms))}) GROUP BY term",
            terms,
        )
    }
    # Migration 32 owns `governed_memories_terms` and creates it eagerly, so an
    # empty table means "not backfilled yet", not "no content contains this
    # term". Treating an absent term as selective would narrow every query to
    # the empty candidate set and silently return nothing. C3's plan API
    # already returns None for this case; the union must fall back the same way
    # so the exact LIKE score below still runs.
    if not counts:
        return None
    # A term that matches most of the library cannot narrow anything; probing
    # it once per row is what made the 100k/430k path scan for seconds. Keep
    # only selective terms when at least one exists. The complete LIKE score
    # below still validates every returned row, so this cannot turn a real
    # match into a hit; it only avoids a full-table candidate scan.
    selective = tuple(term for term in terms if counts.get(term, 0) <= _CANDIDATE_TERM_CAP)
    # A final match may use any `threshold` terms. If all of those could be
    # non-selective terms, dropping the non-selective set would hide a valid
    # OR match. Otherwise every qualifying row must contain a selective term,
    # so that set is a safe narrowing while the complete LIKE score remains
    # the authority for the returned row.
    if not selective or len(terms) - len(selective) >= threshold:
        selective = terms
    placeholders = ",".join("?" * len(selective))
    return (
        "m.memory_id IN (SELECT t.memory_id FROM governed_memories_terms t "
        f"WHERE t.term IN ({placeholders}) GROUP BY t.memory_id)",
        selective,
    )


def candidate_driven_sql(
    predicate: str | None,
    *,
    candidate_values: tuple[str, ...],
    parameters: tuple[str, ...],
    patterns: tuple[str, ...],
    threshold: int,
    limit: int,
    offset: int = 0,
    authority_predicate: str,
    coverage: str,
) -> tuple[str, tuple[object, ...]]:
    """Build the governed-memory SQL with the candidate set as the driver."""
    if predicate is None:
        return (
            f"""WITH ranked AS MATERIALIZED (
            SELECT substr(m.memory_id,1,513) AS memory_id, m.revision,
                substr(m.kind,1,65) AS kind, substr(m.updated_at,1,128) AS updated_at,
                ({coverage}) AS score
            FROM governed_memories m WHERE {authority_predicate}
        )
        SELECT * FROM ranked WHERE score >= ?
        ORDER BY score DESC,updated_at DESC,memory_id ASC LIMIT ? OFFSET ?""",
            (*patterns, *parameters, threshold, limit, offset),
        )
    normalized = predicate.strip()
    upper = normalized.upper()
    marker = " IN ("
    if not (upper.startswith("M.MEMORY_ID") and marker in upper and normalized.endswith(")")):
        raise ValueError("candidate_predicate_not_drivable")
    source = normalized[upper.index(marker) + len(marker) : -1]
    if not source.lstrip().upper().startswith("SELECT"):
        raise ValueError("candidate_predicate_not_drivable")
    return (
        f"""WITH candidate AS MATERIALIZED ({source}), ranked AS MATERIALIZED (
        SELECT substr(m.memory_id,1,513) AS memory_id, m.revision,
            substr(m.kind,1,65) AS kind, substr(m.updated_at,1,128) AS updated_at,
            ({coverage}) AS score
        FROM candidate CROSS JOIN governed_memories m ON m.memory_id=candidate.memory_id
        WHERE {authority_predicate}
        )
        SELECT * FROM ranked WHERE score >= ?
        ORDER BY score DESC,updated_at DESC,memory_id ASC LIMIT ? OFFSET ?""",
        (*candidate_values, *patterns, *parameters, threshold, limit, offset),
    )


def _memory_row_is_fresh(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    freshness: str,
    deadline: float,
) -> bool:
    if freshness == "1":
        return True
    _check_deadline(deadline)
    result = db.execute(
        f"SELECT {freshness} FROM governed_memories m WHERE m.memory_id=?",
        (row["memory_id"],),
    ).fetchone()
    return result is not None and result[0] == 1


def _freshness_predicate(db: sqlite3.Connection) -> str:
    """Register freshness only when a proof row exists in this snapshot.

    The freshness UDF is intentionally exact and re-reads proofs per memory.
    With an empty proof table every call returns 1, but still does one indexed
    lookup per candidate row; on a 100k governed-memory scan that is tens of
    thousands of useless statements. An empty table in the same read snapshot
    has exactly the same semantics as the UDF's documented "no proof means not
    stale" rule, so the read lane can omit the predicate entirely.
    """
    has_proofs = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_task_outputs' LIMIT 1"
    ).fetchone()
    if has_proofs is None:
        return "1"
    has_rows = db.execute("SELECT 1 FROM memory_task_outputs LIMIT 1").fetchone()
    if has_rows is None:
        return "1"
    return register_memory_freshness(db)


def _memory_readable(freshness: str) -> str:
    if freshness == "1":
        return _READABLE
    return _READABLE + f" AND {freshness}"


# Match the local governed read policy before scoring or candidate LIMIT. That
# policy is `source_read_access`'s and is spelled once there so it cannot drift
# from the endpoint that answers 403; this module only adds its own status
# filter on top.
_READABLE = f"""
    m.user_id = 'local' AND m.status = 'confirmed' AND m.deleted_at IS NULL
    AND {memory_source_readable("m.memory_id", "m.user_id")}
"""

_SDK_MEMBER_READABLE = f"""
    m.user_id = 'local' AND m.status != 'deleted' AND m.deleted_at IS NULL
    AND {memory_source_readable("m.memory_id", "m.user_id")}
"""


def _trusted_scopes(scopes: frozenset[str] | None) -> frozenset[str] | None:
    if scopes is None:
        return None
    return frozenset(
        scope
        for scope in scopes
        if scope == "all-apps" or (scope.startswith("workspace:") and len(scope) > 10)
    )


def _check_deadline(deadline: float) -> None:
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise TimeoutError("context memory deadline")


@contextmanager
def _snapshot(database_path: Path, deadline: float) -> Iterator[sqlite3.Connection]:
    _check_deadline(deadline)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("context memory deadline")
    with main_database(database_path).read(busy_timeout_ms=math.ceil(remaining * 1000)) as db:
        try:
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            _check_deadline(deadline)
            db.execute("BEGIN")
            yield db
            _check_deadline(deadline)
        except sqlite3.Error as error:
            if time.monotonic() >= deadline or getattr(error, "sqlite_errorcode", None) in (
                sqlite3.SQLITE_INTERRUPT,
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            ):
                raise TimeoutError("context memory deadline") from None
            raise
        finally:
            db.set_progress_handler(None, 0)


def _predicate(
    freshness: str = "1",
    *,
    scopes: frozenset[str] | None,
    hidden_memory_ids: frozenset[str] | None = None,
    time_range: TimeRange | None = None,
) -> tuple[str, tuple[str, ...]]:
    readable = _memory_readable(freshness)
    parameters: list[str] = []
    if hidden_memory_ids is not None:
        readable += " AND m.memory_id NOT IN (SELECT value FROM json_each(?))"
        parameters.append(json.dumps(sorted(hidden_memory_ids)))
    if time_range is not None:
        # Storage strings may carry a local UTC offset. Compare instants, not
        # their textual spellings; string comparison would reject a +08:00
        # timestamp that is actually inside the UTC half-open window.
        readable += (
            " AND datetime(m.updated_at) >= datetime(?) AND datetime(m.updated_at) < datetime(?)"
        )
        parameters.extend([time_range.start.isoformat(), time_range.end.isoformat()])
    if scopes is None:
        return readable, tuple(parameters)
    # The authority set is already authenticated by the caller. JSON membership
    # preserves exact scopes, including all-apps (which is not a wildcard).
    return (
        readable
        + """
        AND CASE WHEN json_valid(m.scopes_json) THEN
            json_type(m.scopes_json) = 'array' AND EXISTS (
                SELECT 1 FROM json_each(m.scopes_json) scope
                WHERE scope.type = 'text' AND scope.value IN (SELECT value FROM json_each(?))
            ) ELSE 0 END
    """,
        (*parameters, json.dumps(sorted(scopes))),
    )


def _asks_for_current_state(query: str) -> bool:
    lowered = query.casefold()
    return any(marker in lowered for marker in _CURRENT_QUERY_MARKERS)


def _current_semantic_ids(
    db: sqlite3.Connection,
    memory_ids: tuple[str, ...],
    *,
    predicate: str,
    parameters: tuple[str, ...],
    deadline: float,
    follow_corrections: bool,
) -> tuple[str, ...]:
    """Resolve only explicit governance supersession edges, never date/title similarity."""
    current: list[str] = []
    for memory_id in memory_ids:
        _check_deadline(deadline)
        direct = db.execute(
            f"SELECT m.memory_id FROM governed_memories m WHERE m.memory_id=? AND {predicate}",
            (memory_id, *parameters),
        ).fetchone()
        if direct is not None:
            if direct[0] not in current:
                current.append(str(direct[0]))
            continue
        if not follow_corrections:
            continue
        rows = db.execute(
            f"""WITH RECURSIVE correction(memory_id,depth) AS (
                SELECT ?,0
                UNION
                SELECT edge.superseded_by,correction.depth+1
                FROM governed_memory_supersessions edge
                JOIN correction ON edge.memory_id=correction.memory_id
                WHERE correction.depth<?
            )
            SELECT m.memory_id FROM correction
            JOIN governed_memories m ON m.memory_id=correction.memory_id
            WHERE correction.depth>0 AND {predicate}
            ORDER BY correction.depth,m.updated_at DESC,m.memory_id
            LIMIT ?""",
            (memory_id, _CORRECTION_DEPTH_LIMIT, *parameters, _CANDIDATE_LIMIT),
        ).fetchall()
        for row in rows:
            if row[0] not in current:
                current.append(str(row[0]))
    return tuple(current[:_CANDIDATE_LIMIT])


def _references(db: sqlite3.Connection, memory_id: str) -> tuple[tuple[str, ...], bool]:
    rows = db.execute(
        """SELECT substr(evidence_id,1,513) AS evidence_id
        FROM governed_memory_evidence WHERE memory_id=? AND active=1
        ORDER BY evidence_id LIMIT ?""",
        (memory_id, _REFERENCE_LIMIT + 1),
    ).fetchall()
    # Never truncate an identity into a different reference. Bad metadata is a
    # failed read, not an empty result or a fabricated anchor.
    if any(not row["evidence_id"] or len(row["evidence_id"]) > 512 for row in rows):
        raise ValueError("invalid context reference")
    references = tuple(row["evidence_id"] for row in rows[:_REFERENCE_LIMIT])
    return references, len(rows) > _REFERENCE_LIMIT


def _memory_item(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    excerpt: sqlite3.Row,
    limits: list[ContextLimit],
    deadline: float,
    matched_by: tuple[Literal["lexical", "governed_semantic"], ...] = (),
    content: str | None = None,
    content_clipped: bool = False,
) -> MemoryContextItem:
    _check_deadline(deadline)
    references, clipped_refs = _references(db, row["memory_id"])
    if excerpt["clipped"] and "character_budget" not in limits:
        limits.append("character_budget")
    if clipped_refs and "metadata_budget" not in limits:
        limits.append("metadata_budget")
    return MemoryContextItem(
        memory_id=row["memory_id"],
        revision=row["revision"],
        content=content if content is not None else excerpt["content"],
        memory_kind=row["kind"],
        updated_at=row["updated_at"],
        evidence_ids=references,
        matched_by=matched_by,
        truncated=bool(excerpt["clipped"] or clipped_refs or content_clipped),
    )


def _sdk_formatted_content(
    db: sqlite3.Connection,
    hit: SemanticMemoryHit | None,
    *,
    root_memory_id: str,
    root_content: str,
    scopes: frozenset[str] | None,
    freshness: str,
    deadline: float,
) -> tuple[str | None, bool]:
    if (
        hit is None
        or not hit.sdk_formatted
        or not hit.formatting_complete
        or not hit.formatted_content
    ):
        return None, False
    identifiers = tuple(dict.fromkeys((root_memory_id, *hit.member_ids)))
    scope_clause = ""
    values: list[str] = []
    if scopes is not None:
        scope_clause = (
            " AND json_valid(m.scopes_json) AND json_type(m.scopes_json)='array' "
            "AND EXISTS (SELECT 1 FROM json_each(m.scopes_json) scope "
            "WHERE scope.type='text' AND scope.value IN (SELECT value FROM json_each(?)))"
        )
        values.append(json.dumps(sorted(scopes)))
    contents: list[str] = []
    for memory_id in identifiers:
        _check_deadline(deadline)
        row = db.execute(
            f"SELECT m.content FROM governed_memories m WHERE m.memory_id=? "
            f"AND {_SDK_MEMBER_READABLE} AND {freshness}{scope_clause}",
            (memory_id, *values),
        ).fetchone()
        if row is None:
            return None, False
        contents.append(str(row[0]))
    # The root row may have changed after HY answered. It must be represented
    # verbatim in the SDK rendering, as must every nested member released with it.
    if root_content not in hit.formatted_content or any(
        content not in hit.formatted_content for content in contents
    ):
        return None, False
    clipped = len(hit.formatted_content) > _BODY_LIMIT
    return hit.formatted_content[:_BODY_LIMIT], clipped


def _keyword_memory_items(
    db: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    terms: list[str],
    trusted_ids: tuple[str, ...],
    limits: list[ContextLimit],
    deadline: float,
) -> list[MemoryContextItem]:
    if not rows:
        return []
    identifiers = [str(row["memory_id"]) for row in rows]
    ids_json = json.dumps(identifiers)
    excerpts = {
        str(row["memory_id"]): row
        for row in db.execute(
            f"""WITH requested(memory_id) AS (SELECT value FROM json_each(?)),
            anchor AS (
                SELECT m.memory_id,
                    COALESCE(MIN(NULLIF(instr(lower(m.content), term.value),0)),1) AS pos
                FROM requested JOIN governed_memories m USING(memory_id), json_each(?) term
                GROUP BY m.memory_id
            )
            SELECT m.memory_id,
                substr(m.content,
                    min(max(1,anchor.pos-128),max(1,length(m.content)-{_BODY_LIMIT}+1)),
                    {_BODY_LIMIT}) AS content,
                length(m.content) > {_BODY_LIMIT} AS clipped
            FROM requested JOIN governed_memories m USING(memory_id)
            JOIN anchor USING(memory_id)""",
            (ids_json, json.dumps(terms)),
        )
    }
    references: dict[str, list[str]] = {identifier: [] for identifier in identifiers}
    clipped: set[str] = set()
    for memory_id, evidence_id in db.execute(
        """SELECT link.memory_id,substr(link.evidence_id,1,513)
        FROM governed_memory_evidence link JOIN json_each(?) requested
          ON requested.value=link.memory_id
        WHERE link.active=1 ORDER BY link.memory_id,link.evidence_id""",
        (ids_json,),
    ):
        if not evidence_id or len(evidence_id) > 512:
            raise ValueError("invalid context reference")
        bucket = references[str(memory_id)]
        if len(bucket) < _REFERENCE_LIMIT:
            bucket.append(str(evidence_id))
        else:
            clipped.add(str(memory_id))
    items: list[MemoryContextItem] = []
    for row in rows:
        _check_deadline(deadline)
        memory_id = str(row["memory_id"])
        excerpt = excerpts.get(memory_id)
        if excerpt is None:
            raise ValueError("context memory changed")
        if excerpt["clipped"] and "character_budget" not in limits:
            limits.append("character_budget")
        if memory_id in clipped and "metadata_budget" not in limits:
            limits.append("metadata_budget")
        items.append(
            MemoryContextItem(
                memory_id=memory_id,
                revision=row["revision"],
                content=excerpt["content"],
                memory_kind=row["kind"],
                updated_at=row["updated_at"],
                evidence_ids=tuple(references[memory_id]),
                matched_by=(
                    ("lexical", "governed_semantic") if memory_id in trusted_ids else ("lexical",)
                ),
                truncated=bool(excerpt["clipped"] or memory_id in clipped),
            )
        )
    return items


def merge_memory_candidates(
    lexical: Sequence[MemoryContextItem],
    semantic: Sequence[MemoryContextItem],
    *,
    limit: int,
) -> list[MemoryContextItem]:
    """Merge ranked lanes without allowing either one to consume every slot."""
    lexical_by_id = {item.memory_id: item for item in lexical}
    overlap: list[MemoryContextItem] = []
    semantic_only: list[MemoryContextItem] = []
    seen: set[str] = set()
    for item in semantic:
        if item.memory_id in seen:
            continue
        keyword = lexical_by_id.get(item.memory_id)
        if keyword is not None:
            matched_by = tuple(dict.fromkeys((*keyword.matched_by, *item.matched_by)))
            # Preserve HY's SDK-formatted content when the same memory also
            # matched lexically. The lexical item contributes provenance and
            # truncation state, not a replacement display rendering.
            overlap.append(
                item.model_copy(
                    update={
                        "matched_by": matched_by,
                        "truncated": item.truncated or keyword.truncated,
                    }
                )
            )
        else:
            semantic_only.append(item)
        seen.add(item.memory_id)
    lexical_only = [item for item in lexical if item.memory_id not in seen]
    merged = list(overlap)
    for index in range(max(len(lexical_only), len(semantic_only))):
        if index < len(semantic_only):
            merged.append(semantic_only[index])
        if index < len(lexical_only):
            merged.append(lexical_only[index])
    return merged[:limit]


def read_memory_context(
    database_path: Path,
    request: ContextSearchRequest,
    *,
    scopes: frozenset[str] | None,
    deadline: float,
    semantic_ids: tuple[str, ...] = (),
    semantic_hits: tuple[SemanticMemoryHit, ...] = (),
    semantic_outcome: SemanticOutcome = "absent",
    hidden_memory_ids: frozenset[str] | None = None,
    time_range: TimeRange | None = None,
) -> ContextLaneResult:
    """Return limit+1 authorized candidates; the aggregator owns shared budgets.

    HY's ids lead in HY's order and keyword matches HY did not return follow.
    When HY was asked and did not answer, the lane says so: `degraded` with
    `lexical_only`, the same pair the raw lane uses for a partial index.
    """
    hy_missing = semantic_outcome in ("failed", "timed_out")
    status_when_found: Literal["ok", "degraded"] = "degraded" if hy_missing else "ok"
    status_when_empty: Literal["empty", "degraded"] = "degraded" if hy_missing else "empty"
    reason: ContextReason | None = None if semantic_outcome == "answered" else "lexical_only"
    searched_channels: tuple[ContextSearchChannel, ...] = (
        ("lexical", "semantic") if semantic_outcome != "absent" else ("lexical",)
    )
    semantic_status = cast(
        ContextSemanticStatus,
        {
            "absent": "not_searched",
            "answered": "completed",
            "failed": "failed",
            "timed_out": "timed_out",
        }[semantic_outcome],
    )
    semantic_timed_out = semantic_outcome == "timed_out"
    if "memory" not in request.kinds:
        return ContextLaneResult(status="not_requested", reason="not_requested")
    scopes = _trusted_scopes(scopes)
    if scopes is not None and not scopes:
        return ContextLaneResult(status="not_authorized", reason="permission_required")
    semantic_by_id = {hit.memory_id: hit for hit in semantic_hits}
    trusted_ids = tuple(
        memory_id
        for memory_id in (*semantic_by_id, *semantic_ids)
        if isinstance(memory_id, str) and 1 <= len(memory_id) <= 512
    )
    # Filled by readable HY hits in HY's order, never cut by position: ids HY
    # ranked higher may be deleted or out of scope.
    hy_front_limit = min(request.limit + 1, _CANDIDATE_LIMIT)
    try:
        _check_deadline(deadline)
        query_terms = sorted(_terms(request.query))
        if not query_terms and not trusted_ids:
            return ContextLaneResult(
                status=status_when_empty,
                reason=reason,
                searched_channels=searched_channels,
                semantic_status=semantic_status,
                timed_out=semantic_timed_out,
            )
        terms = query_terms[:_TERM_LIMIT]
        limits: list[ContextLimit] = ["metadata_budget"] if len(query_terms) > _TERM_LIMIT else []
        if semantic_outcome == "timed_out":
            limits.append("timeout_budget")
        items: list[MemoryContextItem] = []
        with _snapshot(database_path, deadline) as db:
            freshness = _freshness_predicate(db)
            predicate, parameters = _predicate(
                "1",
                scopes=scopes,
                hidden_memory_ids=hidden_memory_ids,
                time_range=time_range,
            )
            fresh_predicate, fresh_parameters = _predicate(
                freshness,
                scopes=scopes,
                hidden_memory_ids=hidden_memory_ids,
                time_range=time_range,
            )
            trusted_ids = _current_semantic_ids(
                db,
                trusted_ids,
                predicate=fresh_predicate,
                parameters=fresh_parameters,
                deadline=deadline,
                follow_corrections=_asks_for_current_state(request.query),
            )
            if terms:
                patterns = tuple(
                    "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                    for term in terms
                )
                coverage = " + ".join("(m.content LIKE ? ESCAPE '\\')" for _ in terms)
                threshold = min(2, math.ceil(len(terms) / 4))
                candidate_predicate, candidate_values = content_candidate_clause(
                    db,
                    tuple(terms),
                    threshold=threshold,
                )
                target = min(request.limit + 1, _CANDIDATE_LIMIT)
                offset = 0
                fresh_rows: list[sqlite3.Row] = []
                while len(fresh_rows) < target:
                    if candidate_predicate is None:
                        sql, query_values = candidate_driven_sql(
                            None,
                            candidate_values=(),
                            parameters=parameters,
                            patterns=patterns,
                            threshold=threshold,
                            limit=target,
                            offset=offset,
                            authority_predicate=predicate,
                            coverage=coverage,
                        )
                    else:
                        try:
                            sql, query_values = candidate_driven_sql(
                                candidate_predicate,
                                candidate_values=candidate_values,
                                parameters=parameters,
                                patterns=patterns,
                                threshold=threshold,
                                limit=target,
                                offset=offset,
                                authority_predicate=predicate,
                                coverage=coverage,
                            )
                        except ValueError:
                            sql = f"""SELECT substr(m.memory_id,1,513) AS memory_id, m.revision,
                                substr(m.kind,1,65) AS kind,
                                substr(m.updated_at,1,128) AS updated_at,
                                CASE WHEN {predicate} THEN ({coverage}) ELSE 0 END AS score
                            FROM governed_memories m
                            WHERE score >= ? AND ({candidate_predicate})
                            ORDER BY score DESC,m.updated_at DESC,m.memory_id ASC
                            LIMIT ? OFFSET ?"""
                            query_values = (
                                *parameters,
                                *patterns,
                                threshold,
                                *candidate_values,
                                target,
                                offset,
                            )
                    rows = db.execute(sql, query_values).fetchall()
                    if not rows:
                        break
                    offset += len(rows)
                    for row in rows:
                        if not _memory_row_is_fresh(
                            db, row, freshness=freshness, deadline=deadline
                        ):
                            continue
                        fresh_rows.append(row)
                        if len(fresh_rows) >= target:
                            break
                    if len(rows) < target:
                        break
                items.extend(
                    _keyword_memory_items(
                        db,
                        fresh_rows,
                        terms=terms,
                        trusted_ids=trusted_ids,
                        limits=limits,
                        deadline=deadline,
                    )
                )
                if len(items) > request.limit:
                    limits.append("count_budget")
            # Exact keyword evidence must keep a seat even when HY returns a
            # full top-k. Put overlap first, then alternate the independent
            # lexical and semantic candidates. Otherwise a broad semantic
            # result can fill the shared result budget and hide the one memory
            # containing the user's unique identifier.
            by_keyword = {item.memory_id: item for item in items}
            semantic_items: list[MemoryContextItem] = []
            seen: set[str] = set()
            for memory_id in trusted_ids:
                if len(semantic_items) >= hy_front_limit:
                    break
                if memory_id in seen:
                    continue
                if memory_id in by_keyword:
                    keyword = by_keyword[memory_id]
                    formatted, clipped = _sdk_formatted_content(
                        db,
                        semantic_by_id.get(memory_id),
                        root_memory_id=memory_id,
                        root_content=keyword.content,
                        scopes=scopes,
                        freshness=freshness,
                        deadline=deadline,
                    )
                    semantic_items.append(
                        keyword.model_copy(
                            update={
                                "content": formatted or keyword.content,
                                "truncated": keyword.truncated or clipped,
                            }
                        )
                    )
                    seen.add(memory_id)
                    continue
                _check_deadline(deadline)
                row = db.execute(
                    f"""SELECT substr(m.memory_id,1,513) AS memory_id, m.revision,
                    substr(m.kind,1,65) AS kind, substr(m.updated_at,1,128) AS updated_at
                    FROM governed_memories m
                    WHERE m.memory_id=? AND {predicate}""",
                    (memory_id, *parameters),
                ).fetchone()
                if row is None:
                    continue
                excerpt = db.execute(
                    f"""SELECT substr(m.content,1,{_BODY_LIMIT}) AS content,
                           length(m.content) > {_BODY_LIMIT} AS clipped
                    FROM governed_memories m WHERE m.memory_id=?""",
                    (memory_id,),
                ).fetchone()
                if excerpt is None:
                    continue
                formatted, clipped = _sdk_formatted_content(
                    db,
                    semantic_by_id.get(memory_id),
                    root_memory_id=memory_id,
                    root_content=str(excerpt["content"]),
                    scopes=scopes,
                    freshness=freshness,
                    deadline=deadline,
                )
                item = _memory_item(
                    db,
                    row,
                    excerpt,
                    limits,
                    deadline,
                    ("governed_semantic",),
                    content=formatted,
                    content_clipped=clipped,
                )
                semantic_items.append(item)
                seen.add(item.memory_id)
            items = merge_memory_candidates(items, semantic_items, limit=_CANDIDATE_LIMIT)
        _check_deadline(deadline)
        return ContextLaneResult(
            status=status_when_found if items else status_when_empty,
            reason=reason,
            items=tuple(items),
            truncated_by=tuple(limits),
            searched_channels=searched_channels,
            semantic_status=semantic_status,
            timed_out=semantic_timed_out,
        )
    except TimeoutError:
        return ContextLaneResult(
            status="unavailable",
            reason="timeout_budget",
            truncated_by=("timeout_budget",),
            searched_channels=searched_channels,
            semantic_status=semantic_status,
            timed_out=True,
        )
    except (sqlite3.Error, OSError, ValueError) as error:
        return ContextLaneResult(
            status="unavailable",
            reason="read_unavailable",
            searched_channels=searched_channels,
            semantic_status=semantic_status,
            timed_out=semantic_timed_out,
            failure_site="memory_reader",
            error_class=type(error).__name__,
        )


def revalidate_memory_context(
    database_path: Path,
    items: tuple[MemoryContextItem, ...],
    *,
    scopes: frozenset[str] | None,
    deadline: float,
    hidden_memory_ids: frozenset[str] | None = None,
    time_range: TimeRange | None = None,
) -> tuple[MemoryContextItem, ...]:
    """Keep original items only; raise on read failure so it is not a false empty.

    The caller runs this after both lanes finish. A revision/permission/reference
    change drops the item; it never replaces the previously selected content.
    """
    scopes = _trusted_scopes(scopes)
    if (scopes is not None and not scopes) or not items:
        return ()
    if len(items) > _CANDIDATE_LIMIT:
        raise ValueError("too many context candidates")
    retained = []
    with _snapshot(database_path, deadline) as db:
        freshness = _freshness_predicate(db)
        predicate, parameters = _predicate(
            freshness,
            scopes=scopes,
            hidden_memory_ids=hidden_memory_ids,
            time_range=time_range,
        )
        for item in items:
            _check_deadline(deadline)
            row = db.execute(
                f"""SELECT m.memory_id FROM governed_memories m
                WHERE m.memory_id=? AND m.revision=? AND {predicate}""",
                (item.memory_id, item.revision, *parameters),
            ).fetchone()
            if row is not None:
                refs, _ = _references(db, item.memory_id)
                if refs == item.evidence_ids:
                    retained.append(item)
    _check_deadline(deadline)
    return tuple(retained)


def compose_memory_retrieval_path(result: ContextLaneResult) -> RetrievalPath:
    """Map the v1 lane observation without upgrading unknown HY coverage to complete."""
    channels = cast(
        tuple[Channel, ...],
        tuple(
            "governed_semantic" if channel == "semantic" else channel
            for channel in result.searched_channels
        ),
    )
    if result.status in {"not_requested", "not_authorized"}:
        return RetrievalPath(
            status=result.status,
            reason=result.reason,
            returned_count=0,
            coverage="not_searched",
        )
    if result.status == "unavailable":
        return RetrievalPath(
            status="unavailable",
            reason=result.reason or "read_unavailable",
            returned_count=0,
            searched_channels=channels,
            coverage="unavailable",
            truncated_by=result.truncated_by,
        )
    complete = result.index_complete is True and result.semantic_status == "completed"
    if not result.items and not complete:
        reason = (
            "timeout_budget"
            if result.semantic_status == "timed_out"
            else "semantic_unavailable"
            if result.semantic_status == "failed"
            else "index_incomplete"
        )
        return RetrievalPath(
            status="degraded",
            reason=reason,
            returned_count=0,
            searched_channels=channels,
            coverage="partial",
            truncated_by=tuple(dict.fromkeys((*result.truncated_by, "coverage_budget"))),
        )
    return RetrievalPath(
        status="ok" if result.items else "empty",
        reason=None if complete else "index_incomplete",
        returned_count=len(result.items),
        searched_channels=channels,
        coverage="complete" if complete else "partial",
        truncated_by=(
            result.truncated_by
            if complete
            else tuple(dict.fromkeys((*result.truncated_by, "coverage_budget")))
        ),
    )
