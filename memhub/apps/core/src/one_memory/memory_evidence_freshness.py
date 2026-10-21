"""Read-only, exact-revision support checks inside the caller's SQLite snapshot.

No proof is legacy/unknown, not stale. One complete fresh proof suffices; all
complete proofs stale means false. Malformed records raise. An exceeded budget
means the memory is too large to verify, not that it is stale: it reads as
current, the same as having no proof, and is reported once in a while as a
content-free diagnostic. One such memory used to fail every COUNT over the
library, so the homepage returned 503 for as long as it existed (D76).
Set ONE_MEMORY_FRESHNESS_BUDGET_RAISES=1 to restore the old raising behavior.
The caller owns BEGIN/deadline/progress-handler and error-to-unavailable mapping.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from one_memory.database_access import register_function
from one_memory.diagnostic_log import diagnostic_event
from one_memory.imports.models import EvidenceRecord
from one_memory.memory_tasks import MemoryTaskOutput, TaskEvidenceRef, matches_evidence_ref

_PROOFS = 64
_REFERENCES = 128
_BODY_BYTES = 65_536
_EVIDENCE_BYTES = 8 * 1024 * 1024
_SCOPE_STEPS = 64

#: Keys a support reference may carry. Derive them from the TypedDict so a new
#: optional field (for example the segment_* fields added for split evidence)
#: cannot make every proof "invalid" here.
_SUPPORT_REF_KEYS = frozenset(
    TaskEvidenceRef.__required_keys__ | TaskEvidenceRef.__optional_keys__
)

#: Budget refusals: the check could not finish, the data is not malformed.
BUDGET_ERRORS = frozenset(
    {
        "memory_freshness_proof_budget",
        "memory_freshness_reference_budget",
        "memory_freshness_evidence_budget",
        "memory_freshness_scope_budget",
    }
)
_REPORT_INTERVAL_SECONDS = 60.0
_reported: dict[str, tuple[float, int]] = {}
_reported_lock = threading.Lock()


def budget_errors_raise() -> bool:
    """Opt back into the pre-D76 behavior, where one large memory fails the query."""
    return os.environ.get("ONE_MEMORY_FRESHNESS_BUDGET_RAISES", "").strip() == "1"


def _report_budget(code: str) -> None:
    """Count every budget skip; write at most one line per reason per interval."""
    now = time.monotonic()
    with _reported_lock:
        last, pending = _reported.get(code, (float("-inf"), 0))
        pending += 1
        if now - last < _REPORT_INTERVAL_SECONDS:
            _reported[code] = (last, pending)
            return
        _reported[code] = (now, 0)
    diagnostic_event(
        level="warn",
        component="memory.freshness",
        event="memory_freshness.budget_exceeded",
        outcome="skipped",
        details={"reason": code, "count": pending},
    )


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("memory_freshness_invalid_proof")
        result[key] = value
    return result


class _Prefetched:
    """Prefetched rows for the current batch, private to thread and connection.

    Scoped, never global: a cache that outlived the transaction would answer
    from rows a later write had already replaced.
    """

    def __init__(self) -> None:
        self._local = threading.local()

    def get(self) -> dict[str, list[sqlite3.Row]] | None:
        return getattr(self._local, "rows", None)

    def evidence(self) -> dict[str, sqlite3.Row] | None:
        return getattr(self._local, "evidence", None)

    @contextmanager
    def using(
        self,
        rows: dict[str, list[sqlite3.Row]],
        evidence: dict[str, sqlite3.Row] | None = None,
    ) -> Iterator[None]:
        previous = getattr(self._local, "rows", None)
        previous_evidence = getattr(self._local, "evidence", None)
        self._local.rows = rows
        self._local.evidence = evidence
        try:
            yield
        finally:
            self._local.rows = previous
            self._local.evidence = previous_evidence


_prefetched_proofs = _Prefetched()


@contextmanager
def freshness_proofs_prefetched(
    db: sqlite3.Connection,
    memory_ids: Sequence[str],
    *,
    evidence_ids: Sequence[str] = (),
) -> Iterator[None]:
    """Serve the freshness check's proof reads from one query.

    The decisions are unchanged — the rows come from the same transaction —
    but the per-memory reads collapse into the one in `proof_rows_for`.
    """
    with _prefetched_proofs.using(
        proof_rows_for(db, memory_ids),
        evidence_rows_for(db, evidence_ids) if evidence_ids else None,
    ):
        yield


def has_proof_table(db: sqlite3.Connection) -> bool:
    """Whether this database keeps proofs at all.

    Without the table the registered UDF answers 1 unconditionally, so there
    is nothing to prefetch and nothing to read.
    """
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_task_outputs'"
        ).fetchone()
        is not None
    )


_EVIDENCE_COLUMNS = (
    "evidence_id,user_id,source_id,workspace,project,conversation_id,"
    "message_id,correlation_id,source_ordinal,content_kind,role,written_by,"
    "created_at,verbatim,content_hash,revision,imported_at,updated_at"
)

#: The budget measure the single read applies, kept as one string so the
#: batched read cannot compute it differently.
_EVIDENCE_SIZE_SQL = (
    "length(CAST(verbatim AS BLOB)) + "
    "length(CAST(json_array(evidence_id,user_id,source_id,workspace,project,"
    "conversation_id,message_id,correlation_id,source_ordinal,content_kind,"
    "role,written_by,created_at,content_hash,revision,imported_at,updated_at) "
    "AS BLOB))"
)


def evidence_rows_for(
    db: sqlite3.Connection,
    evidence_ids: Sequence[str],
) -> dict[str, sqlite3.Row]:
    """Read the cited evidence rows for a page in one query.

    The check reads every citation twice — once for its size, against the
    evidence budget, and once for the row it validates against the proof's
    reference. Both are per citation, so both grow with the page.

    The size comes from the same expression the single read uses, so the
    budget arithmetic cannot drift between the two paths.
    """
    if not evidence_ids:
        return {}
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    by_id: dict[str, sqlite3.Row] = {}
    for start in range(0, len(evidence_ids), 900):
        batch = list(dict.fromkeys(evidence_ids[start : start + 900]))
        placeholders = ",".join("?" for _ in batch)
        for row in cursor.execute(
            f"SELECT {_EVIDENCE_COLUMNS},{_EVIDENCE_SIZE_SQL} AS measure "
            f"FROM evidence WHERE evidence_id IN ({placeholders})",
            batch,
        ):
            by_id[str(row["evidence_id"])] = row
    return by_id


def proof_rows_for(
    db: sqlite3.Connection,
    memory_ids: Sequence[str],
) -> dict[str, list[sqlite3.Row]]:
    """Read the proof rows for a whole page in one query.

    The freshness check reads these per memory, and that is the read path's
    whole cost: measured on the old-user library, 160 memories cost 0.69 s in
    the UDF against 0.08 s for the citation subquery, and grew further while
    the library was also being written to — enough to cross the five second
    interrupt on `/api/v1/memories` and on the map.

    Returns only ids that have rows; a caller falling back to a single read
    for anything absent keeps the same answer.
    """
    if not memory_ids:
        return {}
    if not has_proof_table(db):
        return {}
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    by_id: dict[str, list[sqlite3.Row]] = {}
    for start in range(0, len(memory_ids), 400):
        batch = list(memory_ids[start : start + 400])
        placeholders = ",".join("?" for _ in batch)
        for row in cursor.execute(
            "SELECT task_id,memory_id,length(CAST(body AS BLOB)) AS size,"
            "substr(CAST(body AS BLOB),1,?) AS body FROM memory_task_outputs "
            f"WHERE memory_id IN ({placeholders})",
            (_BODY_BYTES + 1, *batch),
        ):
            by_id.setdefault(str(row["memory_id"]), []).append(row)
    return by_id


def _proof_revision(
    cursor: sqlite3.Cursor,
    memory_id: str,
    revision: int,
    user_id: str,
    available: set[int],
) -> int:
    if revision in available or not available:
        return revision
    if (
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='governed_memory_versions'"
        ).fetchone()
        is None
    ):
        return revision
    original = revision
    for step in range(_SCOPE_STEPS + 1):
        if revision in available:
            return revision
        row = cursor.execute(
            "SELECT v.action, typeof(v.content)='text' AND typeof(p.content)='text' "
            "AND CAST(v.content AS BLOB)=CAST(p.content AS BLOB) AS same_content, "
            "v.status=p.status AS same_status, "
            "CAST(v.content AS BLOB)=CAST(m.content AS BLOB) AS current_content "
            "FROM governed_memory_versions v "
            "JOIN governed_memory_versions p ON p.memory_id=v.memory_id "
            "AND p.revision=v.revision-1 "
            "JOIN governed_memories m ON m.memory_id=v.memory_id "
            "WHERE v.memory_id=? AND v.revision=? AND m.user_id=? AND m.revision=?",
            (memory_id, revision, user_id, original),
        ).fetchone()
        if row is None or row[0] != "set_scopes" or not all(row[index] == 1 for index in (1, 2, 3)):
            return original
        if step == _SCOPE_STEPS:
            raise ValueError("memory_freshness_scope_budget")
        revision -= 1
    raise ValueError("memory_freshness_scope_budget")


def register_memory_freshness(db: sqlite3.Connection) -> str:
    """Register UDF; return a predicate for alias m. Does not change row_factory.

    Production initialization supplies memory_task_outputs(memory_id) index.
    Old no-index databases depend on the caller's SQL deadline. Per evaluation:
    65 rows / 64KiB each, 129 active IDs, 8MiB total Evidence material. No library
    bodies are loaded; Evidence objects live only for this evaluation. No cache
    persists across transactions (safe when a caller reuses its connection).
    """
    if not has_proof_table(db):
        register_function(db, "is_memory_evidence_current", 3, lambda *_: 1)
        return "1"

    raising = budget_errors_raise()

    def current(memory_id: str, revision: int, user_id: str) -> int:
        try:
            return checked(memory_id, revision, user_id)
        except ValueError as error:
            code = str(error)
            if raising or code not in BUDGET_ERRORS:
                raise
            _report_budget(code)
            return 1
        except sqlite3.Error as error:
            # A freshness proof is an enhancement, never the reason a person
            # cannot see their library. Under a concurrent history import the
            # 5s read deadline interrupts this UDF mid-statement; the old
            # behaviour let that escape, and the caller turned it into
            # `memory_library_unavailable`, so the memory list stayed empty for
            # as long as the import ran. Answer "current" instead and record
            # why, which is the same trade the D76 budget skip already makes.
            _report_budget("memory_freshness_sql_error")
            logging.getLogger(__name__).warning(
                "memory_freshness_udf_failed %s: %s", type(error).__name__, error
            )
            return 1

    def checked(memory_id: str, revision: int, user_id: str) -> int:
        if not isinstance(memory_id, str) or type(revision) is not int or revision < 1:
            raise ValueError("memory_freshness_invalid_identity")
        if not isinstance(user_id, str):
            raise ValueError("memory_freshness_invalid_identity")
        cursor = db.cursor()
        cursor.row_factory = sqlite3.Row
        prefetched = _prefetched_proofs.get()
        if prefetched is not None:
            rows = prefetched.get(memory_id, ())
        else:
            rows = cursor.execute(
                "SELECT task_id,memory_id,length(CAST(body AS BLOB)) AS size,"
                "substr(CAST(body AS BLOB),1,?) AS body FROM memory_task_outputs "
                "WHERE memory_id=? LIMIT ?",
                (_BODY_BYTES + 1, memory_id, _PROOFS + 1),
            ).fetchall()
        rows = list(rows)
        if len(rows) > _PROOFS:
            raise ValueError("memory_freshness_proof_budget")
        decoded = []
        for row in rows:
            if type(row["size"]) is not int or row["size"] > _BODY_BYTES:
                raise ValueError("memory_freshness_proof_budget")
            body = json.loads(row["body"], object_pairs_hook=_object)
            if not isinstance(body, dict) or type(body.get("memory_revision")) is not int:
                raise ValueError("memory_freshness_invalid_proof")
            decoded.append((row, body))
        revision = _proof_revision(
            cursor,
            memory_id,
            revision,
            user_id,
            {body["memory_revision"] for _, body in decoded},
        )
        applicable = []
        for row, body in decoded:
            if body["memory_revision"] != revision:
                continue
            raw_refs = body.get("support_refs", [])
            if not isinstance(raw_refs, list) or any(
                not isinstance(ref, dict)
                or set(ref) - _SUPPORT_REF_KEYS
                for ref in raw_refs
            ):
                raise ValueError("memory_freshness_invalid_reference")
            output = MemoryTaskOutput.model_validate_json(row["body"], strict=True)
            if output.memory_id != memory_id or output.task_id != row["task_id"]:
                raise ValueError("memory_freshness_proof_identity")
            if len(output.support_refs) > _REFERENCES:
                raise ValueError("memory_freshness_reference_budget")
            for ref in output.support_refs:
                if (
                    set(ref) - _SUPPORT_REF_KEYS
                    or not ref["evidence_id"]
                    or ref["revision"] < 1
                    or any(
                        len(ref[key]) != 64
                        or any(char not in "0123456789abcdef" for char in ref[key])
                        for key in ("content_hash", "identity_hash")
                        if key in ref
                    )
                ):
                    raise ValueError("memory_freshness_invalid_reference")
            proof_ids = [ref["evidence_id"] for ref in output.support_refs]
            if len(proof_ids) != len(set(proof_ids)):
                raise ValueError("memory_freshness_duplicate_reference")
            applicable.append(output)
        if not applicable:
            return 1
        active = cursor.execute(
            "SELECT evidence_id FROM governed_memory_evidence "
            "WHERE memory_id=? AND active=1 LIMIT ?",
            (memory_id, _REFERENCES + 1),
        ).fetchall()
        if len(active) > _REFERENCES:
            raise ValueError("memory_freshness_reference_budget")
        ids = {row[0] for row in active}
        complete = [
            output
            for output in applicable
            if output.support_refs and {ref["evidence_id"] for ref in output.support_refs} == ids
        ]
        if not complete:
            return 1
        records: dict[str, EvidenceRecord | None] = {}
        used = 0
        prefetched_evidence = _prefetched_proofs.evidence()
        for identifier in ids:
            evidence_row = (
                prefetched_evidence.get(identifier) if prefetched_evidence is not None else None
            )
            if evidence_row is not None:
                size = evidence_row["measure"]
                if type(size) is not int or size < 0:
                    raise ValueError("memory_freshness_invalid_evidence")
            else:
                measured = cursor.execute(
                    f"SELECT {_EVIDENCE_SIZE_SQL} FROM evidence WHERE evidence_id=?",
                    (identifier,),
                ).fetchone()
                if measured is None:
                    records[identifier] = None
                    continue
                if type(measured[0]) is not int or measured[0] < 0:
                    raise ValueError("memory_freshness_invalid_evidence")
                size = measured[0]
            used += size
            if used > _EVIDENCE_BYTES:
                raise ValueError("memory_freshness_evidence_budget")
            if evidence_row is None:
                evidence_row = cursor.execute(
                    f"SELECT {_EVIDENCE_COLUMNS} FROM evidence WHERE evidence_id=?",
                    (identifier,),
                ).fetchone()
                if evidence_row is None:
                    records[identifier] = None
                    continue
            # evidence_ref explicitly excludes origin_meta/imported_at/updated_at.
            # `sqlite3.Row` iterates values, not column names, so pair the
            # names with the values explicitly.
            fields = {
                name: value
                for name, value in zip(evidence_row.keys(), tuple(evidence_row), strict=False)
                if name != "measure"
            }
            records[identifier] = EvidenceRecord.model_validate({**fields, "origin_meta": {}})
        for output in complete:
            if all(
                (record := records[ref["evidence_id"]]) is not None
                and record.user_id == user_id
                and matches_evidence_ref(record, ref)
                for ref in output.support_refs
            ):
                return 1
        return 0

    register_function(db, "is_memory_evidence_current", 3, current)
    return "is_memory_evidence_current(m.memory_id,m.revision,m.user_id)"
