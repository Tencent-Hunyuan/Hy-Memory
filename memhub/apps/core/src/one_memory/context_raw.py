"""Bounded lexical reads of existing evidence; no processing or source scanning."""

import json
import math
import os
import re
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from one_memory.context_models import (
    ContextLaneResult,
    ContextLimit,
    ContextMessage,
    ContextSearchRequest,
    RawContextItem,
)
from one_memory.database_access import main_database
from one_memory.imports.storage import TRIGRAM_FLOOR, _as_fts_phrase, _as_like_pattern


def _check(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError("raw_context_timeout")
    return remaining


def _execute(
    db: sqlite3.Connection, deadline: float, sql: str, values: Sequence[object] = ()
) -> sqlite3.Cursor:
    # Refresh the connection-local lock budget for every statement, not every lane.
    db.execute(f"PRAGMA busy_timeout={math.ceil(min(_check(deadline), 30) * 1000)}")
    cursor = db.execute(sql, values)
    _check(deadline)
    return cursor


@contextmanager
def _connection(path: Path, deadline: float) -> Iterator[sqlite3.Connection]:
    wait_ms = math.ceil(min(_check(deadline), 30) * 1000)
    with main_database(path).read(busy_timeout_ms=wait_ms) as db:
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            _execute(db, deadline, "BEGIN")
            yield db
            _check(deadline)
        except sqlite3.Error as error:
            code = getattr(error, "sqlite_errorcode", 0) & 255
            if time.monotonic() >= deadline or code in {
                sqlite3.SQLITE_INTERRUPT,
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                raise TimeoutError("raw_context_timeout") from None
            raise
        finally:
            db.set_progress_handler(None, 0)


def _candidate_probe_cap() -> int:
    """Bounded lexical candidate probe size.

    The FTS subquery is an OR over the question's trigrams, so on a large
    corpus it can legally match nearly every row. Materializing all of them and
    temp-sorting the result is what made 430k reads blow the budget; it is a
    query shape problem, not an engine limitation. The probe takes a bounded
    prefix of the matches and ranks by how many of the question's terms a row
    actually contains, so the rows worth showing are found first. Exceeding the
    cap is reported as a coverage limit instead of a clean full-corpus result.
    """
    return int(os.environ.get("ONE_MEMORY_RAW_CANDIDATE_CAP", "4096"))


def _terms(query: str) -> tuple[str, ...]:
    return _terms_with_sampling(query)[0]


_HISTORICAL_REFERENCE = re.compile(
    r"继续|接着|续接|接上|做到哪|下一步|未完成|上次|之前|以前|历史|过去|"
    r"continue|resume|pick up|last time|before|history|earlier",
    re.I,
)
_FACT_STATEMENT = re.compile(
    r"改为|更新为|定为|设定为|采用|决定|更正|旧值作废|记为|记录为|"
    r"changed to|updated to|set to|decided|corrected",
    re.I,
)


def _prefers_declared_facts(query: str) -> bool:
    """History/continuation prompts should not return their own question text."""
    return (
        _HISTORICAL_REFERENCE.search(query) is not None or _FACT_STATEMENT.search(query) is not None
    )


def _terms_with_sampling(query: str) -> tuple[tuple[str, ...], bool]:
    # CJK trigrams use the existing index; short runs use literal LIKE. Other
    # scripts split at punctuation. Sample across the request, never just its head.
    chunks: list[str] = []
    for run in re.findall(r"[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+", query):
        if "\u3400" <= run[0] <= "\u9fff":
            chunks.extend(run[i : i + 3] for i in range(max(1, len(run) - 2)))
        else:
            chunks.extend(run[i : i + 64] for i in range(0, len(run), 64))
    if not chunks:
        chunks = [query.strip()[i : i + 64] for i in range(0, len(query.strip()), 64)]
    chunks = list(dict.fromkeys(chunks))
    sampled = len(chunks) > 16
    if sampled:
        chunks = [chunks[index * (len(chunks) - 1) // 15] for index in range(16)]
    return tuple(chunks), sampled


def _readable(source_ids: frozenset[str] | None) -> tuple[str, list[object]]:
    clause = """e.user_id='local' AND NOT EXISTS (
        SELECT 1 FROM source_grants g
        WHERE g.user_id=e.user_id AND g.source_id=e.source_id AND g.revoked_at IS NOT NULL
    )"""
    values: list[object] = []
    if source_ids is not None:
        clause += " AND e.source_id IN (SELECT value FROM json_each(?))"
        values.append(json.dumps(sorted(source_ids)))
    return clause, values


_METADATA = """
    substr(e.evidence_id,1,513) AS evidence_id, e.revision,
    substr(e.source_id,1,129) AS source_id,
    substr(e.conversation_id,1,513) AS conversation_id,
    substr(e.role,1,33) AS role, substr(e.written_by,1,33) AS written_by,
    substr(e.created_at,1,65) AS created_at
"""


def _message(row: sqlite3.Row) -> ContextMessage:
    return ContextMessage(
        evidence_id=row["evidence_id"],
        revision=row["revision"],
        role=row["role"],
        written_by=row["written_by"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        text=row["text"],
        truncated=bool(row["body_truncated"]),
    )


def read_raw_context(
    database_path: Path,
    request: ContextSearchRequest,
    *,
    source_ids: frozenset[str] | None,
    deadline: float,
) -> ContextLaneResult:
    if "raw" not in request.kinds:
        return ContextLaneResult(status="not_requested", reason="not_requested")
    if request.raw_source_ids is not None:
        requested = frozenset(request.raw_source_ids)
        source_ids = requested if source_ids is None else source_ids & requested
    if source_ids == frozenset():
        return ContextLaneResult(status="not_authorized", reason="permission_required")
    _check(deadline)
    terms, sampled = _terms_with_sampling(request.query)
    readable, authority = _readable(source_ids)
    matches: list[str] = []
    match_values: list[object] = []
    long_terms = [term for term in terms if len(term) >= TRIGRAM_FLOOR]
    if long_terms:
        # Bounded candidate probe: the OR over trigrams can match nearly every
        # row on a large corpus, and materializing all of them to sort is what
        # pushed 430k reads past the budget. Keep this probe as its own
        # MATERIALIZED CTE so SQLite drives from the FTS rowids instead of
        # choosing a broad source-index scan and temp-sorting the outer rows.
        match_values.append(" OR ".join(_as_fts_phrase(term) for term in long_terms))
        probe_truncated = True
    else:
        probe_truncated = False
    # Knowing whether the probe actually saturated needs one extra bounded read
    # of the match count; without it a truncated probe would look complete.
    probe_saturated = False
    for term in terms:
        if len(term) < TRIGRAM_FLOOR:
            matches.append("e.verbatim LIKE ? ESCAPE '\\'")
            match_values.append(f"%{_as_like_pattern(term)}%")
    coverage = " + ".join("(e.verbatim LIKE ? ESCAPE '\\')" for _ in terms)
    declarative = (
        "CASE WHEN e.verbatim LIKE '%？%' OR e.verbatim LIKE '%?%' "
        "OR e.verbatim LIKE '%只回答%' THEN 0 ELSE 1 END"
        if _prefers_declared_facts(request.query)
        else "1"
    )
    person_authored = "CASE WHEN e.role='user' THEN 1 ELSE 0 END"
    query_echo = (
        "CASE WHEN trim(e.verbatim)=? OR trim(e.verbatim)=? THEN 0 ELSE 1 END"
        if _prefers_declared_facts(request.query)
        else "1"
    )
    echo_values: list[object] = (
        [request.query.strip(), f"{request.query.strip()}。"]
        if _prefers_declared_facts(request.query)
        else []
    )
    limits: list[ContextLimit] = ["metadata_budget"] if sampled else []
    items: list[RawContextItem] = []
    body_limit = min(2000, request.character_budget)
    # The excerpt starts near a real lexical match, even at the end of a huge row.
    start = """max(1, coalesce((SELECT min(nullif(instr(lower(e.verbatim), lower(value)),0))
               FROM json_each(?)),1)-80)"""
    try:
        with _connection(database_path, deadline) as db:
            index = _execute(
                db,
                deadline,
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_search'",
            ).fetchone()
            if index is None:
                return ContextLaneResult(
                    status="unavailable",
                    reason="read_unavailable",
                    searched_channels=("lexical",),
                    index_complete=False,
                )
            if probe_truncated:
                # One extra bounded rowid read answers "did the probe fill up";
                # it is cheap because it stops as soon as the cap is exceeded.
                saturated = _execute(
                    db,
                    deadline,
                    f"""SELECT 1 FROM (SELECT rowid FROM evidence_search
                        WHERE evidence_search MATCH ? LIMIT {_candidate_probe_cap() + 1})
                        LIMIT 1 OFFSET {_candidate_probe_cap()}""",
                    [match_values[0]],
                ).fetchone()
                probe_saturated = saturated is not None
            rows = _execute(
                db,
                deadline,
                (
                    f"""
                    WITH probe AS MATERIALIZED (
                        SELECT rowid FROM evidence_search
                        WHERE evidence_search MATCH ?
                        LIMIT {_candidate_probe_cap()}
                    ), candidates AS MATERIALIZED (
                        SELECT e.rowid AS rid,
                               ({coverage}) AS term_coverage,
                               (e.verbatim LIKE ? ESCAPE '\\') AS exact_phrase,
                               ({declarative}) AS declarative,
                               ({person_authored}) AS person_authored,
                               ({query_echo}) AS query_echo,
                               e.created_at AS created_at,
                               e.evidence_id AS evidence_id
                        FROM probe CROSS JOIN evidence e ON e.rowid=probe.rowid
                        WHERE {readable}
                        ORDER BY person_authored DESC, declarative DESC,
                                 query_echo DESC, term_coverage DESC,
                                 exact_phrase DESC, created_at DESC, evidence_id
                        LIMIT ?
                    )
                    SELECT {_METADATA}, substr(e.verbatim,{start},?) AS text,
                           length(e.verbatim)>? AS body_truncated,
                           candidates.term_coverage,
                           candidates.exact_phrase,
                           candidates.declarative,
                           candidates.person_authored,
                           candidates.query_echo
                    FROM candidates JOIN evidence e ON e.rowid=candidates.rid
                    """
                    if long_terms
                    else f"""
                    SELECT {_METADATA}, substr(e.verbatim,{start},?) AS text,
                           length(e.verbatim)>? AS body_truncated,
                           ({coverage}) AS term_coverage,
                           (e.verbatim LIKE ? ESCAPE '\\') AS exact_phrase,
                           ({declarative}) AS declarative,
                           ({person_authored}) AS person_authored,
                           ({query_echo}) AS query_echo
                    FROM evidence e WHERE {readable} AND ({" OR ".join(matches)})
                    ORDER BY person_authored DESC, declarative DESC, query_echo DESC,
                             term_coverage DESC, exact_phrase DESC, e.created_at DESC,
                             e.evidence_id
                    LIMIT ?
                    """
                ),
                (
                    [
                        match_values[0],
                        *(f"%{_as_like_pattern(term)}%" for term in terms),
                        f"%{_as_like_pattern(request.query.strip())}%",
                        *echo_values,
                        *authority,
                        request.limit + 1,
                        json.dumps(terms),
                        body_limit,
                        body_limit,
                    ]
                    if long_terms
                    else [
                        json.dumps(terms),
                        body_limit,
                        body_limit,
                        *(f"%{_as_like_pattern(term)}%" for term in terms),
                        f"%{_as_like_pattern(request.query.strip())}%",
                        *echo_values,
                        *authority,
                        *match_values,
                        request.limit + 1,
                    ]
                ),
            ).fetchall()
            _check(deadline)
            if len(rows) > request.limit:
                limits.append("count_budget")
            for row in rows:
                _check(deadline)
                try:
                    anchor = _message(row)
                    # Validate identity before using it as a neighbor query key.
                    item = RawContextItem(
                        source_id=row["source_id"],
                        conversation_id=row["conversation_id"],
                        anchor_evidence_id=anchor.evidence_id,
                        messages=(anchor,),
                    )
                except (ValidationError, ValueError, TypeError):
                    limits.append("metadata_budget")
                    continue
                before: list[ContextMessage] = []
                after: list[ContextMessage] = []
                metadata_truncated = False
                for operator, direction in (("<", "DESC"), (">", "ASC")):
                    # Compare nullable ordinal as (present, text), preserving
                    # SQLite NULL-first/text ordering without numeric coercion.
                    # Keep full sort keys in SQLite, not in returned metadata.
                    neighbor = _execute(
                        db,
                        deadline,
                        f"""
                        SELECT {_METADATA}, substr(e.verbatim,1,?) AS text,
                               length(e.verbatim)>? AS body_truncated
                        FROM evidence e JOIN evidence seed ON seed.evidence_id=?
                        WHERE {readable}
                          AND e.source_id=? AND e.conversation_id=?
                          AND (coalesce(e.created_at,e.imported_at),
                               e.source_ordinal IS NOT NULL, coalesce(e.source_ordinal,''),
                               e.evidence_id) {operator}
                              (coalesce(seed.created_at,seed.imported_at),
                               seed.source_ordinal IS NOT NULL, coalesce(seed.source_ordinal,''),
                               seed.evidence_id)
                        ORDER BY coalesce(e.created_at,e.imported_at) {direction},
                                 e.source_ordinal {direction}, e.evidence_id {direction}
                        LIMIT 1
                    """,
                        [
                            body_limit,
                            body_limit,
                            anchor.evidence_id,
                            *authority,
                            item.source_id,
                            item.conversation_id,
                        ],
                    ).fetchone()
                    if neighbor is not None:
                        try:
                            message = _message(neighbor)
                        except (ValidationError, ValueError, TypeError):
                            limits.append("metadata_budget")
                            metadata_truncated = True
                            continue
                        if operator == "<":
                            before.append(message)
                        else:
                            after.append(message)
                # The lexical anchor is the result. Keep the canonical before/
                # after window, but emit the anchor first so budget clipping or
                # a host that renders only messages[0] cannot show an adjacent
                # historical value instead of the answer-bearing Evidence.
                messages = [anchor, *before, *after]
                truncated = any(message.truncated for message in messages)
                if truncated:
                    limits.append("character_budget")
                items.append(
                    item.model_copy(
                        update={
                            "messages": tuple(messages),
                            "truncated": truncated or metadata_truncated,
                        }
                    )
                )
            _check(deadline)
    except TimeoutError:
        raise
    except (sqlite3.Error, OSError):
        return ContextLaneResult(
            status="unavailable",
            reason="read_unavailable",
            searched_channels=("lexical",),
            index_complete=False,
        )
    if probe_saturated and "coverage_budget" not in limits:
        # A saturated probe means rows outside it were never ranked. Reporting
        # that as a plain ok/empty would claim whole-corpus coverage we did not
        # establish, so the lane degrades and says why. The v1 wire union is
        # frozen, so this rides metadata_budget's already-defined degraded
        # status while the reason stays visible in truncated_by.
        limits.append("metadata_budget")
    return ContextLaneResult(
        status=(
            "degraded"
            if {"metadata_budget", "candidate_budget"} & set(limits)
            else "ok"
            if items
            else "empty"
        ),
        reason="lexical_only",
        items=tuple(items),
        truncated_by=tuple(dict.fromkeys(limits)),
        searched_channels=("lexical",),
        index_complete=False,
    )


def revalidate_raw_context(
    database_path: Path,
    items: tuple[RawContextItem, ...],
    *,
    source_ids: frozenset[str] | None,
    deadline: float,
) -> tuple[RawContextItem, ...]:
    if not items or source_ids == frozenset():
        return ()
    if len(items) > 21:
        raise ValueError("raw_context_candidate_limit")
    readable, authority = _readable(source_ids)
    retained: list[RawContextItem] = []
    with _connection(database_path, deadline) as db:
        for item in items:
            _check(deadline)
            if item.anchor_evidence_id not in {message.evidence_id for message in item.messages}:
                continue
            for message in item.messages:
                row = _execute(
                    db,
                    deadline,
                    f"""
                    SELECT {_METADATA} FROM evidence e
                    WHERE {readable} AND e.evidence_id=? AND e.revision=?
                      AND e.source_id=? AND e.conversation_id=?
                """,
                    [
                        *authority,
                        message.evidence_id,
                        message.revision,
                        item.source_id,
                        item.conversation_id,
                    ],
                ).fetchone()
                if row is None:
                    break
                try:
                    created = (
                        datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
                    )
                except (ValueError, TypeError):
                    break
                if (row["role"], created) != (
                    message.role,
                    message.created_at,
                ):
                    break
            else:
                retained.append(item)
        _check(deadline)
    return tuple(retained)
