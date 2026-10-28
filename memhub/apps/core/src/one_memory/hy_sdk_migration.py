"""Offline maintenance of the HY SDK data plane for App migrations.

Runtime product code must use public ``HyMemoryClient`` methods.  The startup
migration boundary is the isolated exception: all writers are stopped, so it
may copy, restore and repair SDK-owned local stores.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hy_memory import (  # type: ignore[import-untyped]
    DataPlaneRestore,
    DataPlaneSnapshot,
    restore_local_data_plane,
    snapshot_local_data_plane,
)

from one_memory.database_access import main_database
from one_memory.hy_runtime_config import HY_MEMORY_NAMESPACE

# v2 reads the backfill's governed_memory_evidence links; v1 missed them.
_METADATA_REPAIR_VERSION = 2
_BATCH = 500


@dataclass(frozen=True)
class RepairMetadata:
    source: str | None
    memory_at: int | None
    created_at: int | None


@dataclass(frozen=True)
class MetadataRepairResult:
    migration_id: str | None
    mapped: int
    vector_rows: int
    coding_rows: int


def snapshot_data_plane(
    *,
    source_root: Path,
    target_root: Path,
    writers_stopped: bool,
) -> DataPlaneSnapshot:
    """Snapshot an explicitly quiesced SDK-owned local data plane."""
    return snapshot_local_data_plane(
        source_root=source_root,
        target_root=target_root,
        writers_stopped=writers_stopped,
    )


def restore_data_plane(
    *,
    snapshot_root: Path,
    target_root: Path,
    writers_stopped: bool,
    protective_root: Path | None = None,
) -> DataPlaneRestore:
    """Restore an explicitly quiesced SDK-owned local data plane."""
    return restore_local_data_plane(
        snapshot_root=snapshot_root,
        target_root=target_root,
        writers_stopped=writers_stopped,
        protective_root=protective_root,
    )


def repair_sdk_metadata_from_bundle(
    *,
    data_dir: Path,
    database_path: Path,
    now: datetime | None = None,
) -> MetadataRepairResult:
    """Restore metadata lost by historical cutovers from their verified bundles.

    Offline only: no SDK client may be open on this home (see `_repair_vector`).
    """
    migration_id = _sdk_migration_id(database_path)
    if migration_id is None:
        return MetadataRepairResult(None, 0, 0, 0)
    receipt = data_dir / f".sdk-metadata-repair-v{_METADATA_REPAIR_VERSION}-{migration_id}.json"
    if receipt.is_file():
        document = json.loads(receipt.read_text(encoding="utf-8"))
        return MetadataRepairResult(
            migration_id,
            int(document.get("mapped", 0)),
            int(document.get("vector_rows", 0)),
            int(document.get("coding_rows", 0)),
        )
    # A home can carry several cutovers (v1 and then v2). A later bundle may
    # have been taken after an earlier cutover had already retired the legacy
    # tables, so every bundle is read and their links are pooled.
    legacy = sorted(
        (data_dir / "backups").glob("*-pre-cutover.sdk-bundle/management/one-memory.sqlite3")
    )
    if not legacy:
        # No bundle yet is not a finished repair: try again on the next start.
        return MetadataRepairResult(migration_id, 0, 0, 0)
    current = now or datetime.now(UTC)
    pools: dict[str, _Pool] = {}
    for path in legacy:
        _merge_pools(pools, _legacy_pools(path, now=current))
    mapping = _decide(pools)
    vector_rows = _repair_vector(data_dir, mapping, now=current)
    coding_rows = _repair_coding(data_dir, mapping)
    result = MetadataRepairResult(
        migration_id,
        len(mapping),
        vector_rows,
        coding_rows,
    )
    temporary = receipt.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "migration_id": migration_id,
                "mapped": result.mapped,
                "vector_rows": vector_rows,
                "coding_rows": coding_rows,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(receipt)
    return result


def _sdk_migration_id(database_path: Path) -> str | None:
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        row = database.execute(
            "SELECT authority,migration_id FROM memory_authority_state WHERE singleton=1"
        ).fetchone()
    if row is None or row["authority"] != "sdk":
        return None
    value = row["migration_id"]
    return str(value) if isinstance(value, str) and value else None


@dataclass
class _Pool:
    """Everything the legacy library knew about one SDK memory."""

    sources: set[str] = field(default_factory=set)
    times: list[datetime] = field(default_factory=list)
    created: list[datetime] = field(default_factory=list)


def _legacy_metadata(path: Path, *, now: datetime) -> dict[str, RepairMetadata]:
    return _decide(_legacy_pools(path, now=now))


def _legacy_pools(path: Path, *, now: datetime) -> dict[str, _Pool]:
    """Link each SDK memory id to the legacy evidence it was made from.

    Three legacy paths wrote memories the SDK now holds:

    - the cutover backfill (`import_exact`) copied every governed memory into the
      SDK under its legacy id, or under the id `memory_task_outputs` recorded,
      without a source or a conversation time; `governed_memory_evidence` is the
      link from that memory to its evidence;
    - extraction tasks recorded their evidence as `support_refs`;
    - the write queue recorded the SDK ids each job produced.
    """
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as database:
        database.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "evidence" not in tables:
            return {}
        links: dict[str, set[str]] = {}
        created: dict[str, list[datetime]] = {}
        canonical: dict[str, str] = {}
        if "memory_task_outputs" in tables:
            for row in database.execute("SELECT body FROM memory_task_outputs"):
                try:
                    body = json.loads(str(row["body"]))
                except (TypeError, ValueError):
                    continue
                legacy_id = body.get("memory_id")
                canonical_id = body.get("hy_memory_id")
                refs = body.get("support_refs")
                if not isinstance(legacy_id, str) or not isinstance(canonical_id, str):
                    continue
                canonical[legacy_id] = canonical_id
                links.setdefault(canonical_id, set()).update(
                    str(ref["evidence_id"])
                    for ref in (refs if isinstance(refs, list) else ())
                    if isinstance(ref, dict) and isinstance(ref.get("evidence_id"), str)
                )
        if "governed_memories" in tables:
            for row in database.execute(
                "SELECT memory_id,created_at,updated_at FROM governed_memories"
            ):
                legacy_id = str(row["memory_id"])
                canonical_id = canonical.get(legacy_id, legacy_id)
                links.setdefault(canonical_id, set())
                legacy_created = _plausible_time(
                    row["created_at"], fallback=row["updated_at"], now=now
                )
                if legacy_created is not None:
                    created.setdefault(canonical_id, []).append(legacy_created)
            if "governed_memory_evidence" in tables:
                for row in database.execute(
                    "SELECT memory_id,evidence_id FROM governed_memory_evidence"
                ):
                    legacy_id = str(row["memory_id"])
                    links.setdefault(canonical.get(legacy_id, legacy_id), set()).add(
                        str(row["evidence_id"])
                    )
        if "memory_write_jobs" in tables and "sdk_memory_ids_json" in _columns(
            database, "memory_write_jobs"
        ):
            job_evidence: dict[str, set[str]] = {}
            if "memory_write_job_evidence" in tables:
                for row in database.execute(
                    "SELECT job_id,evidence_id FROM memory_write_job_evidence"
                ):
                    job_evidence.setdefault(str(row["job_id"]), set()).add(str(row["evidence_id"]))
            for row in database.execute(
                "SELECT job_id,sdk_memory_ids_json FROM memory_write_jobs "
                "WHERE sdk_memory_ids_json IS NOT NULL"
            ):
                try:
                    memory_ids = json.loads(str(row["sdk_memory_ids_json"]))
                except (TypeError, ValueError):
                    continue
                for memory_id in memory_ids if isinstance(memory_ids, list) else ():
                    if isinstance(memory_id, str) and memory_id:
                        links.setdefault(memory_id, set()).update(
                            job_evidence.get(str(row["job_id"]), ())
                        )
        evidence: dict[str, tuple[str, datetime | None]] = {}
        wanted = sorted({identifier for refs in links.values() for identifier in refs})
        for batch in _batches(wanted):
            placeholders = ",".join("?" for _ in batch)
            for row in database.execute(
                "SELECT evidence_id,source_id,created_at,imported_at "
                f"FROM evidence WHERE evidence_id IN ({placeholders})",
                batch,
            ):
                evidence[str(row["evidence_id"])] = (
                    str(row["source_id"]),
                    _plausible_time(row["created_at"], fallback=row["imported_at"], now=now),
                )
    pools: dict[str, _Pool] = {}
    for canonical_id, refs in links.items():
        pool = pools.setdefault(canonical_id, _Pool())
        for identifier in refs:
            if identifier not in evidence:
                continue
            source, timestamp = evidence[identifier]
            if source and source != "default":
                pool.sources.add(source)
            if timestamp is not None:
                pool.times.append(timestamp)
        pool.created.extend(created.get(canonical_id, ()))
    return pools


def _merge_pools(into: dict[str, _Pool], other: dict[str, _Pool]) -> None:
    for canonical_id, pool in other.items():
        merged = into.setdefault(canonical_id, _Pool())
        merged.sources |= pool.sources
        merged.times.extend(pool.times)
        merged.created.extend(pool.created)


def _decide(pools: dict[str, _Pool]) -> dict[str, RepairMetadata]:
    # Several legacy memories can fold into one SDK memory. Their evidence is
    # pooled before deciding, so a disagreeing source stays unknown instead of
    # whichever link happened to come last.
    return {
        canonical_id: RepairMetadata(
            source=next(iter(pool.sources)) if len(pool.sources) == 1 else None,
            memory_at=int(max(pool.times).timestamp()) if pool.times else None,
            created_at=int(min(pool.created).timestamp()) if pool.created else None,
        )
        for canonical_id, pool in pools.items()
    }


def _columns(database: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in database.execute(f"PRAGMA table_info({table})")}


def _plausible_time(
    value: object,
    *,
    fallback: object,
    now: datetime,
) -> datetime | None:
    for candidate in (value, fallback):
        if not isinstance(candidate, str) or not candidate:
            continue
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        parsed = parsed.astimezone(UTC)
        if parsed <= now + timedelta(days=1):
            return parsed
    return None


def _repair_vector(
    data_dir: Path,
    mapping: dict[str, RepairMetadata],
    *,
    now: datetime,
) -> int:
    directory = data_dir / "hy-memory" / HY_MEMORY_NAMESPACE / "vector"
    if not (directory / "chroma.sqlite3").is_file():
        return 0
    from one_memory.sdk_vector_reattach import open_offline_chroma

    client = open_offline_chroma(directory)
    # This must be the only Chroma client on the directory: Chroma shares one
    # System per path, and this client's close() drops it from under any
    # other (KeyError on its next lookup). `sdk-migrate` runs it after the
    # cutover with Runtime stopped; Core's startup fallback runs it while
    # `hold_stores_offline` keeps every SDK client waiting.
    try:
        repaired = 0
        ids = tuple(mapping)
        for collection in client.list_collections():
            for batch in _batches(ids):
                page = collection.get(ids=list(batch), include=["metadatas"])
                updated_ids: list[str] = []
                updated_metadata: list[dict[str, object]] = []
                for memory_id, metadata in zip(
                    page.get("ids") or (),
                    page.get("metadatas") or (),
                    strict=True,
                ):
                    if not isinstance(metadata, dict):
                        continue
                    desired = mapping.get(str(memory_id))
                    if desired is None:
                        continue
                    next_metadata = dict(metadata)
                    changed = False
                    if desired.source and next_metadata.get("source") in {
                        None,
                        "",
                        "default",
                    }:
                        next_metadata["source"] = desired.source
                        changed = True
                    if (
                        desired.memory_at is not None
                        and next_metadata.get("memory_at") != desired.memory_at
                    ):
                        next_metadata["memory_at"] = desired.memory_at
                        changed = True
                    if (
                        desired.created_at is not None
                        and next_metadata.get("gmt_created") != desired.created_at
                    ):
                        next_metadata["gmt_created"] = desired.created_at
                        changed = True
                    if changed:
                        updated_ids.append(str(memory_id))
                        updated_metadata.append(next_metadata)
                if updated_ids:
                    collection.update(ids=updated_ids, metadatas=updated_metadata)
                    repaired += len(updated_ids)
            repaired += _settle_future_times(collection, now=now)
    finally:
        client.close()
    return repaired


def _settle_future_times(collection: Any, *, now: datetime) -> int:
    """Move conversation times that are still in the future back to the write time.

    A historical Cursor bug stored a file hash as the message time (years like
    2254). The SDK lists newest first by `memory_at`, so those rows hold the
    first page. Rows the bundles could date were fixed above; for the rest the
    write time is the closest true moment that is known.
    """
    cutoff = int((now + timedelta(days=1)).timestamp())
    page = collection.get(where={"memory_at": {"$gt": cutoff}}, include=["metadatas"])
    updated_ids: list[str] = []
    updated_metadata: list[dict[str, object]] = []
    for memory_id, metadata in zip(page.get("ids") or (), page.get("metadatas") or (), strict=True):
        if not isinstance(metadata, dict):
            continue
        created = metadata.get("gmt_created")
        settled = (
            created
            if isinstance(created, int) and not isinstance(created, bool) and created <= cutoff
            else int(now.timestamp())
        )
        updated_ids.append(str(memory_id))
        updated_metadata.append({**metadata, "memory_at": settled})
    if updated_ids:
        collection.update(ids=updated_ids, metadatas=updated_metadata)
    return len(updated_ids)


def _repair_coding(
    data_dir: Path,
    mapping: dict[str, RepairMetadata],
) -> int:
    root = data_dir / "hy-memory" / HY_MEMORY_NAMESPACE
    repaired = 0
    for name in ("coding.sqlite3", "coding_cv.sqlite3"):
        path = root / name
        if not path.is_file():
            continue
        _ensure_coding_schema(path)
        with closing(sqlite3.connect(path)) as database:
            tables = {
                str(row[0])
                for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table, identifier in (
                ("coding_memory_meta", "experience_id"),
                ("working_fact", "fact_id"),
            ):
                if table not in tables:
                    continue
                # _ensure_coding_schema added it; an SDK without the column
                # (before 2.0.7) leaves the time to the vector repair alone.
                has_memory_at = "memory_at" in _columns(database, table)
                rows = database.execute(
                    f"SELECT {identifier},source,created_at,"
                    f"{'memory_at' if has_memory_at else 'NULL'} FROM {table}"
                ).fetchall()
                for memory_id, source, created_at, memory_at in rows:
                    desired = mapping.get(str(memory_id))
                    if desired is None:
                        continue
                    updates: list[str] = []
                    values: list[object] = []
                    if desired.source and source in {None, "", "default"}:
                        updates.append("source=?")
                        values.append(desired.source)
                    if (
                        has_memory_at
                        and desired.memory_at is not None
                        and memory_at != desired.memory_at
                    ):
                        updates.append("memory_at=?")
                        values.append(desired.memory_at)
                    if desired.created_at is not None:
                        desired_created_at = (
                            datetime.fromtimestamp(desired.created_at, tz=UTC)
                            .replace(tzinfo=None)
                            .isoformat()
                        )
                        if created_at != desired_created_at:
                            updates.append("created_at=?")
                            values.append(desired_created_at)
                    if not updates:
                        continue
                    values.append(memory_id)
                    database.execute(
                        f"UPDATE {table} SET {','.join(updates)} WHERE {identifier}=?",
                        values,
                    )
                    repaired += 1
            database.commit()
    return repaired


def _ensure_coding_schema(path: Path) -> None:
    """Bring a Coding/Working store to the installed SDK's schema.

    A migrated home's stores were written by an older SDK and may lack columns
    the repair writes (``memory_at`` since hy-memory 2.0.7). The repair runs
    before any SDK client opens them, so the SDK's own schema step is run here
    rather than repeating its DDL. ``CodingMemoryStore`` has no public
    schema-only entry point: ``initialize()`` also opens its Chroma
    collections, which must stay closed during the offline repair. So only its
    SQLite step runs, on a store object that carries nothing but that path.
    """
    from hy_memory.coding.store import (  # type: ignore[import-untyped]
        CodingMemoryStore,
    )

    # Relies on the pinned SDK's internals (hy-memory==2.0.8 is pinned exactly).
    store = CodingMemoryStore.__new__(CodingMemoryStore)
    store._db_path = str(path)
    store._conn = None
    try:
        store._init_sqlite_sync()
    finally:
        if store._conn is not None:
            store._conn.close()


def _batches[T](values: list[T] | tuple[T, ...]) -> Iterator[tuple[T, ...]]:
    for start in range(0, len(values), _BATCH):
        yield tuple(values[start : start + _BATCH])
