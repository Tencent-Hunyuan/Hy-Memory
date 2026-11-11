"""Recoverable, collection-local lexical projection of Chroma memory metadata.

All authoritative reads and writes which affect this projection run while holding
the same filesystem lock.  The SQLite file is disposable; Chroma remains the
source of truth.  No connection or lock is shared across worker threads.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from filelock import FileLock, Timeout

from .chroma_lexical_analyzer import (
    ANALYZER_VERSION,
    analyze_document,
    compile_query,
)


logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1
PAGE_SIZE = 500
_FILTER_COLUMNS = (
    "isolation_key", "user_id", "agent_id", "layer", "status", "is_latest", "source",
)
# The adapter uses this same set to decide whether a payload update can change
# retrieval. All other payload fields are read fresh when hits are materialized.
PROJECTION_FIELDS = frozenset({"content", "tags", "node_id", *_FILTER_COLUMNS})


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _projection(point_id: str, metadata: Mapping[str, Any], document: Any = None) -> Dict[str, Any]:
    """Project actual Chroma values without filling missing filter fields."""
    content = metadata.get("content")
    if content is None:
        content = document or ""
    if not isinstance(content, str):
        raise ValueError("Memory content must be text")
    tags = metadata.get("tags") or []
    if isinstance(tags, str):
        tags = json.loads(tags)
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("Memory tags must be a list of strings")
    node_id = str(metadata.get("node_id") or point_id)
    fields = {key: metadata.get(key) for key in _FILTER_COLUMNS}
    text_hash = _digest([content, tags])
    return {
        "point_id": str(point_id),
        "node_id": node_id,
        "content": content,
        "tags": tags,
        "text_hash": text_hash,
        "projection_hash": _digest([node_id, text_hash, fields]),
        **fields,
    }


def _records(page: Mapping[str, Any]) -> List[Tuple[str, Dict[str, Any], Any]]:
    ids = list(page.get("ids") or [])
    metadata = page.get("metadatas")
    documents = page.get("documents")
    metadata = list(metadata) if metadata is not None else [{} for _ in ids]
    documents = list(documents) if documents is not None else [None for _ in ids]
    if len(metadata) != len(ids) or len(documents) != len(ids):
        raise ValueError("Chroma returned an incomplete lexical page")
    return [
        (str(point_id), dict(meta or {}), document)
        for point_id, meta, document in zip(ids, metadata, documents)
    ]


class ChromaLexicalIndex:
    """One local collection's derived index and synchronous operation interface.

    ``scope`` is a canonical ``{'in': {column: values}, 'not_in': ...}`` mapping.
    The Chroma adapter resolves user/isolation precedence once for both backends.
    An instance must complete ``audit`` before it can serve queries.
    """

    def __init__(
        self, directory: str, collection_name: str, collection_id: str,
        *, lock_timeout: float = 5.0,
    ) -> None:
        directory = os.path.normcase(os.path.realpath(str(directory)))
        basename = hashlib.sha256(collection_name.encode("utf-8")).hexdigest()
        self.path = Path(directory) / ".hy_lexical" / f"{basename}.sqlite3"
        self.lock_path = self.path.with_suffix(".lock")
        self.collection_name = collection_name
        self.collection_id = str(collection_id)
        self.lock_timeout = lock_timeout
        self._verified = False
        self._last_reason = "not_audited"

    def _lock(self, timeout: Optional[float] = None) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(
            str(self.lock_path), timeout=self.lock_timeout if timeout is None else timeout,
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=self.lock_timeout)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _meta(connection: sqlite3.Connection) -> Optional[sqlite3.Row]:
        return connection.execute("SELECT * FROM index_meta WHERE id=1").fetchone()

    def _identity_matches(self, metadata: Optional[Mapping[str, Any]]) -> bool:
        try:
            return bool(metadata) and (
                metadata["schema_version"] == SCHEMA_VERSION
                and metadata["analyzer_version"] == ANALYZER_VERSION
                and metadata["collection_id"] == self.collection_id
                and metadata["collection_name"] == self.collection_name
            )
        except (IndexError, KeyError):
            return False

    def _can_reuse_schema(self, connection: sqlite3.Connection) -> bool:
        """Only reuse this exact, complete version of the disposable schema."""
        metadata = self._meta(connection)
        if not self._identity_matches(metadata) or metadata["state"] != "ready":
            return False
        columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
        required = {"id", "point_id", "node_id", "text_hash", "projection_hash", *_FILTER_COLUMNS}
        if not required.issubset(columns):
            return False
        documents = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
        indexed = connection.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
        missing = connection.execute(
            "SELECT count(*) FROM documents d LEFT JOIN documents_fts f "
            "ON f.rowid=d.id WHERE f.rowid IS NULL"
        ).fetchone()[0]
        if documents != indexed or missing:
            return False
        connection.execute("INSERT INTO documents_fts(documents_fts) VALUES ('integrity-check')")
        connection.commit()
        return True

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS index_meta ("
            "id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL, "
            "analyzer_version TEXT NOT NULL, collection_id TEXT NOT NULL, "
            "collection_name TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            "id INTEGER PRIMARY KEY, point_id TEXT NOT NULL UNIQUE, node_id TEXT NOT NULL, "
            "text_hash TEXT NOT NULL, projection_hash TEXT NOT NULL, "
            "isolation_key TEXT, user_id TEXT, agent_id TEXT, layer TEXT, status TEXT, "
            "is_latest INTEGER, source TEXT)"
        )
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(terms, symbols)"
        )
        # Scope filtering accompanies every FTS candidate query. Keep its common
        # exact-user lookup indexed without indexing every possible combination.
        connection.execute(
            "CREATE INDEX IF NOT EXISTS documents_user ON documents(user_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS documents_isolation ON documents(isolation_key)"
        )
        connection.commit()

    @staticmethod
    def _mark_dirty(connection: sqlite3.Connection, reason: str = "mutation_pending") -> None:
        changed = connection.execute(
            "UPDATE index_meta SET state='dirty', reason=? WHERE id=1", (reason,),
        ).rowcount
        if changed != 1:
            connection.rollback()
            raise sqlite3.DatabaseError("Lexical metadata is missing")
        connection.commit()

    def _discard_locked(self) -> None:
        """Revoke a broken projection only while all cooperating readers are locked out."""
        for suffix in ("-journal", "-wal", "-shm", ""):
            target = Path(str(self.path) + suffix)
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        self._verified = False

    def _invalidate_locked(self) -> bool:
        if not self.path.exists():
            return False
        try:
            with self._connection() as connection:
                previous = self._meta(connection)
                clean = bool(previous and previous["state"] == "ready")
                self._mark_dirty(connection)
            return clean
        except sqlite3.Error:
            # If SQLite itself cannot persist invalidation, revoking the entire
            # disposable file is safe under this lock. Failure to revoke escapes
            # before the authoritative operation starts.
            self._discard_locked()
            self._last_reason = "projection_revoked"
            return False

    @staticmethod
    def _delete_ids(connection: sqlite3.Connection, point_ids: Iterable[str]) -> None:
        ids = list(point_ids)
        for start in range(0, len(ids), PAGE_SIZE):
            batch = ids[start:start + PAGE_SIZE]
            placeholders = ",".join("?" for _ in batch)
            connection.execute(
                "DELETE FROM documents_fts WHERE rowid IN "
                f"(SELECT id FROM documents WHERE point_id IN ({placeholders}))", batch,
            )
            connection.execute(
                f"DELETE FROM documents WHERE point_id IN ({placeholders})", batch,
            )

    def _sync_records(
        self, connection: sqlite3.Connection,
        records: List[Tuple[str, Dict[str, Any], Any]],
    ) -> None:
        if not records:
            return
        ids = [record[0] for record in records]
        placeholders = ",".join("?" for _ in ids)
        previous = {
            row["point_id"]: row for row in connection.execute(
                f"SELECT * FROM documents WHERE point_id IN ({placeholders})", ids,
            )
        }
        for point_id, metadata, document in records:
            projected = _projection(point_id, metadata, document)
            old = previous.get(point_id)
            if old is not None and old["projection_hash"] == projected["projection_hash"]:
                continue
            values = [projected[key] for key in _FILTER_COLUMNS]
            connection.execute(
                "INSERT INTO documents (point_id,node_id,text_hash,projection_hash,"
                + ",".join(_FILTER_COLUMNS) + ") VALUES ("
                + ",".join("?" for _ in range(4 + len(_FILTER_COLUMNS))) + ") "
                "ON CONFLICT(point_id) DO UPDATE SET "
                "node_id=excluded.node_id,text_hash=excluded.text_hash,"
                "projection_hash=excluded.projection_hash,"
                + ",".join(f"{key}=excluded.{key}" for key in _FILTER_COLUMNS),
                [point_id, projected["node_id"], projected["text_hash"],
                 projected["projection_hash"], *values],
            )
            if old is None or old["text_hash"] != projected["text_hash"]:
                row_id = connection.execute(
                    "SELECT id FROM documents WHERE point_id=?", (point_id,),
                ).fetchone()[0]
                analyzed = analyze_document(projected["content"], projected["tags"])
                connection.execute("DELETE FROM documents_fts WHERE rowid=?", (row_id,))
                connection.execute(
                    "INSERT INTO documents_fts(rowid,terms,symbols) VALUES (?,?,?)",
                    (row_id, analyzed.terms, analyzed.symbols),
                )

    def _sync_ids(
        self, connection: sqlite3.Connection, collection: Any, point_ids: Iterable[str],
    ) -> None:
        ids = list(dict.fromkeys(str(point_id) for point_id in point_ids))
        for start in range(0, len(ids), PAGE_SIZE):
            batch = ids[start:start + PAGE_SIZE]
            records = _records(collection.get(ids=batch, include=["metadatas", "documents"]))
            found = {record[0] for record in records}
            self._delete_ids(connection, (point_id for point_id in batch if point_id not in found))
            self._sync_records(connection, records)

    def audit(self, collection: Any, force: bool = False) -> Dict[str, Any]:
        """Validate all authoritative rows and repair/rebuild the derived projection.

        Failures disable this instance's lexical querying, leaving dense startup
        available. Unknown/partial builds never become readable.
        """
        self._verified = False
        try:
            with self._lock():
                # Corrupt/unrecognized SQLite layouts are disposable, not a
                # migration problem for the authoritative memory store.
                if self.path.exists():
                    try:
                        with self._connection() as connection:
                            reusable = not force and self._can_reuse_schema(connection)
                    except sqlite3.Error:
                        reusable = False
                    if not reusable:
                        self._discard_locked()
                with self._connection() as connection:
                    self._create_schema(connection)
                    metadata = self._meta(connection)
                    rebuild = force or not self._identity_matches(metadata)
                    connection.execute(
                        "INSERT OR REPLACE INTO index_meta VALUES (1,?,?,?,?,?,?)",
                        (SCHEMA_VERSION, ANALYZER_VERSION, self.collection_id,
                         self.collection_name, "building", "audit_in_progress"),
                    )
                    connection.commit()
                    if rebuild:
                        connection.execute("DELETE FROM documents_fts")
                        connection.execute("DELETE FROM documents")
                    expected = collection.count()
                    seen = set()
                    offset = 0
                    while True:
                        page = _records(collection.get(
                            limit=PAGE_SIZE, offset=offset, include=["metadatas", "documents"],
                        ))
                        if not page:
                            break
                        page_ids = {record[0] for record in page}
                        if len(page_ids) != len(page) or seen.intersection(page_ids):
                            raise ValueError("Authoritative lexical scan repeated IDs")
                        self._sync_records(connection, page)
                        seen.update(page_ids)
                        offset += len(page)
                    if len(seen) != expected or collection.count() != expected:
                        raise ValueError("Authoritative lexical scan changed during audit")
                    stale = [
                        row[0] for row in connection.execute("SELECT point_id FROM documents")
                        if row[0] not in seen
                    ]
                    self._delete_ids(connection, stale)
                    indexed = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
                    fts_count = connection.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
                    missing = connection.execute(
                        "SELECT count(*) FROM documents d LEFT JOIN documents_fts f "
                        "ON f.rowid=d.id WHERE f.rowid IS NULL"
                    ).fetchone()[0]
                    if indexed != expected or fts_count != expected or missing:
                        raise ValueError("Lexical projection validation failed")
                    connection.execute(
                        "INSERT INTO documents_fts(documents_fts) VALUES ('integrity-check')"
                    )
                    connection.execute(
                        "UPDATE index_meta SET state='ready',reason='' WHERE id=1"
                    )
                    connection.commit()
                    self._verified = True
                    self._last_reason = ""
                    return self._status(connection)
        except Exception as exc:
            self._last_reason = (
                "fts5_unavailable" if "no such module: fts5" in str(exc).lower()
                else "lock_busy" if isinstance(exc, Timeout) else "audit_failed"
            )
            logger.warning("Chroma lexical audit unavailable: %s", self._last_reason)
            return {"state": "unavailable", "reason": self._last_reason, "record_count": 0}

    def mutate(
        self, collection: Any, operation: Callable[[], Tuple[Any, Iterable[str]]],
        maintain: bool = True,
    ) -> Any:
        """Invalidate, execute one authoritative operation, then update its projection.

        The caller's primary exception is preserved. Once primary success is
        known, projection failure only disables lexical retrieval.
        """
        with self._lock():
            ready = self._invalidate_locked()
            result, point_ids = operation()
            if not (maintain and ready and self._verified and self.path.exists()):
                return result
            try:
                with self._connection() as connection:
                    if not self._identity_matches(self._meta(connection)):
                        self._last_reason = "identity_mismatch"
                        return result
                    self._sync_ids(connection, collection, point_ids)
                    connection.execute("UPDATE index_meta SET state='ready',reason='' WHERE id=1")
                    connection.commit()
            except Exception:
                self._last_reason = "index_update_failed"
                try:
                    with self._connection() as connection:
                        self._mark_dirty(connection, self._last_reason)
                except sqlite3.Error:
                    pass
                logger.warning("Chroma lexical update unavailable: index_update_failed")
            return result

    @staticmethod
    def _scope_sql(scope: Mapping[str, Any]) -> Tuple[str, List[Any]]:
        clauses = []
        values: List[Any] = []
        for kind, operator in (("in", "IN"), ("not_in", "NOT IN")):
            for column, selected in scope.get(kind, {}).items():
                if column not in _FILTER_COLUMNS:
                    raise ValueError("Unsupported lexical scope column")
                selected = list(selected)
                if not selected:
                    raise ValueError("Empty lexical scope condition")
                placeholders = ",".join("?" for _ in selected)
                predicate = f"d.{column} {operator} ({placeholders})"
                # Chroma's $nin includes rows without this metadata key. SQL's
                # three-valued NOT IN alone would silently exclude legacy rows.
                if kind == "not_in":
                    predicate = f"(d.{column} IS NULL OR {predicate})"
                clauses.append(predicate)
                values.extend(selected)
        return (" AND " + " AND ".join(clauses) if clauses else ""), values

    def search(
        self, collection: Any, raw_query: str, top_k: int,
        scope: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        """Retrieve and materialize scoped hits without loading Chroma embeddings."""
        if not self._verified or not raw_query or top_k <= 0:
            return []
        compiled = compile_query(raw_query)
        if not compiled.terms_match and not compiled.symbols_match:
            return []
        try:
            with self._lock(timeout=0):
                if not self.path.exists():
                    return []
                with self._connection() as connection:
                    metadata = self._meta(connection)
                    if not self._identity_matches(metadata) or metadata["state"] != "ready":
                        return []
                    filters, filter_values = self._scope_sql(scope)
                    candidates: Dict[str, Dict[str, Any]] = {}
                    for expression, exact in (
                        (compiled.terms_match, False), (compiled.symbols_match, True),
                    ):
                        if not expression:
                            continue
                        rows = connection.execute(
                            "SELECT d.point_id,d.node_id,d.projection_hash,"
                            "bm25(documents_fts) AS raw_score FROM documents_fts "
                            "JOIN documents d ON d.id=documents_fts.rowid "
                            "WHERE documents_fts MATCH ?" + filters
                            + " ORDER BY raw_score,d.node_id LIMIT ?",
                            [expression, *filter_values, top_k],
                        )
                        for row in rows:
                            b = max(0.0, -float(row["raw_score"]))
                            if not math.isfinite(b):
                                continue
                            score = 0.9 + 0.1 * b / (1.0 + b) if exact else 0.85 * b / (1.0 + b)
                            candidate = dict(row)
                            candidate["score"] = score
                            old = candidates.get(row["node_id"])
                            if old is None or score > old["score"]:
                                candidates[row["node_id"]] = candidate
                    ranked = sorted(
                        candidates.values(), key=lambda hit: (-hit["score"], hit["node_id"]),
                    )[:top_k]
                    if not ranked:
                        return []
                    records = _records(collection.get(
                        ids=[hit["point_id"] for hit in ranked],
                        include=["metadatas", "documents"],
                    ))
                    actual = {record[0]: record for record in records}
                    for hit in ranked:
                        record = actual.get(hit["point_id"])
                        projected = _projection(*record) if record is not None else None
                        if projected is None or projected["projection_hash"] != hit["projection_hash"]:
                            self._mark_dirty(connection, "materialization_mismatch")
                            return []
                        materialized = dict(record[1])
                        materialized["node_id"] = projected["node_id"]
                        materialized["content"] = projected["content"]
                        hit["metadata"] = materialized
                    return ranked
        except Timeout:
            return []
        except Exception:
            self._verified = False
            self._last_reason = "query_failed"
            try:
                with self._lock(timeout=0), self._connection() as connection:
                    self._mark_dirty(connection, self._last_reason)
            except (OSError, sqlite3.Error, Timeout):
                pass
            logger.warning("Chroma lexical query unavailable: query_failed")
            return []

    def _status(self, connection: sqlite3.Connection) -> Dict[str, Any]:
        metadata = self._meta(connection)
        if metadata is None:
            return {"state": "unavailable", "reason": "metadata_missing", "record_count": 0}
        return {
            "state": "unavailable" if metadata["state"] == "ready" and not self._verified else metadata["state"],
            "reason": metadata["reason"] or self._last_reason,
            "record_count": connection.execute("SELECT count(*) FROM documents").fetchone()[0],
        }

    def get_status(self) -> Dict[str, Any]:
        """Return non-sensitive diagnostic state without waiting for a writer."""
        try:
            with self._lock(timeout=0):
                if not self.path.exists():
                    return {"state": "unavailable", "reason": self._last_reason, "record_count": 0}
                with self._connection() as connection:
                    return self._status(connection)
        except Exception:
            return {"state": "unavailable", "reason": "status_unavailable", "record_count": 0}


@contextmanager
def raw_collection_mutation(
    directory: str, collection_name: str, *, discard: bool = False,
) -> Iterator[None]:
    """Coordinate SDK lifecycle code that writes a raw local Chroma collection.

    The lock covers the entire caller operation. A failed operation intentionally
    leaves the projection dirty (or absent); business writes are never replayed.
    ``discard=True`` is for collection deletion and never removes the lock file.
    """
    index = ChromaLexicalIndex(directory, collection_name, "")
    with index._lock():
        if discard:
            index._discard_locked()
        else:
            index._invalidate_locked()
        yield
