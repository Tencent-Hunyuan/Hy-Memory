"""Offline reattachment of SDK vector collections named by builds before #145.

Those builds named a profile's embedding space `one_memory_<12 hex>`; #145
renamed it `memhub_<16 hex>` and dropped the step that carried the old
collections over, so every later Runtime opened an empty space beside the old
one. Runtime code only reaches vectors through the SDK. This module runs from
`sdk-migrate` while the Runtime is stopped, through Chroma's public client.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from one_memory.hy_sdk_runtime import vector_collection_bases
from one_memory.model_profiles import ModelProfileSnapshot
from one_memory.sdk_data_plane_migration import DataPlaneMigrationError

_BATCH = 500
#: Every name a MemHub build gave an embedding space's base collection.
_SPACE_NAME = re.compile(r"^(?:memhub_[0-9a-f]{16}|one_memory_[0-9a-f]{12})(?:_|$)")


def open_offline_chroma(directory: Path) -> Any:
    """A Chroma client on the SDK's vector store, configured as the SDK opens it.

    Chroma refuses a second client on a directory whose shared instance was
    created with different settings, so offline code must not pass its own.
    Telemetry is turned off the way the Runtime turns it off, by environment.
    """
    import os

    import chromadb

    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    return chromadb.PersistentClient(path=str(directory))


@dataclass(frozen=True)
class ReattachedCollection:
    legacy: str
    current: str
    legacy_count: int
    merged_count: int
    final_count: int


def _vector_directory(data_dir: Path) -> Path:
    return data_dir / "hy-memory" / "governed-v2" / "vector"


def _stored_names(data_dir: Path, *, filled: bool = False) -> tuple[str, ...]:
    """Collection names; with `filled`, only those holding at least one row."""
    database = _vector_directory(data_dir) / "chroma.sqlite3"
    if not database.is_file():
        return ()
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        try:
            rows = connection.execute(
                "SELECT DISTINCT collections.name FROM collections "
                "JOIN segments ON segments.collection = collections.id "
                "JOIN embeddings ON embeddings.segment_id = segments.id"
                if filled
                else "SELECT name FROM collections"
            ).fetchall()
        except sqlite3.Error:
            return ()
    return tuple(sorted(str(row[0]) for row in rows))


def filled_collection_names(data_dir: Path) -> tuple[str, ...]:
    """Every vector collection holding at least one record. Read-only.

    Safe beside a serving Core: it reads Chroma's own SQLite file, never the
    client system the SDK shares.
    """
    return _stored_names(data_dir, filled=True)


def stored_collection_width(data_dir: Path, name: str) -> int | None:
    """The vector width Chroma fixed for `name` on its first add; None if unset."""
    database = _vector_directory(data_dir) / "chroma.sqlite3"
    if not database.is_file():
        return None
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        try:
            row = connection.execute(
                "SELECT dimension FROM collections WHERE name=?", (name,)
            ).fetchone()
        except sqlite3.Error:
            return None
    return int(row[0]) if row is not None and row[0] is not None else None


def legacy_vector_collections(
    data_dir: Path, profile: ModelProfileSnapshot
) -> tuple[tuple[str, str], ...]:
    """(legacy name, current name) pairs still waiting to be reattached.

    Read-only, so `sdk-migration-status` can ask it.
    """
    current, legacy_bases = vector_collection_bases(profile)
    pairs: list[tuple[str, str]] = []
    for name in _stored_names(data_dir):
        for base in legacy_bases:
            if name == base or name.startswith(f"{base}_"):
                pairs.append((name, current + name[len(base) :]))
                break
    return tuple(pairs)


def vectors_belong_to_another_space(data_dir: Path, profile: ModelProfileSnapshot) -> bool:
    """Vectors exist, but only in embedding spaces `profile` does not name.

    Counted rows, not names: every Runtime opens its read collection with
    `get_or_create`, so an empty collection under the expected base proves
    nothing. Read-only, so a preflight can ask before anything changes.
    """
    current, legacy_bases = vector_collection_bases(profile)
    known = (current, *legacy_bases)
    filled = _stored_names(data_dir, filled=True)
    return not any(
        name == base or name.startswith(f"{base}_") for name in filled for base in known
    ) and any(_SPACE_NAME.match(name) for name in filled)


def stored_memory_ids(data_dir: Path) -> frozenset[str]:
    """Every id in the vector store as found, across all embedding spaces.

    All of them on purpose. A memory stranded under a space the Runtime will
    not read must count as present, so the readback then fails on it instead
    of excusing it. Both the point id and the SDK's `node_id` are kept: the
    SDK derives a point id from a node id that is not a UUID.

    Callers must have stopped every SDK writer. A store that cannot be read
    raises: "unknown" must never be taken for "absent".
    """
    if not (_vector_directory(data_dir) / "chroma.sqlite3").is_file():
        return frozenset()
    found: set[str] = set()
    try:
        client = open_offline_chroma(_vector_directory(data_dir))
        try:
            for collection in client.list_collections():
                offset = 0
                while True:
                    page = collection.get(include=["metadatas"], limit=_BATCH, offset=offset)
                    ids = list(page["ids"])
                    if not ids:
                        break
                    found.update(ids)
                    found.update(
                        str(metadata["node_id"])
                        for metadata in page["metadatas"] or ()
                        if metadata and metadata.get("node_id")
                    )
                    offset += len(ids)
        finally:
            client.close()
    except Exception as error:
        raise DataPlaneMigrationError("sdk_migration_vector_store_unreadable") from error
    return frozenset(found)


def _records(collection: Any) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        page = collection.get(
            include=["embeddings", "documents", "metadatas"],
            limit=_BATCH,
            offset=offset,
        )
        ids = list(page["ids"])
        if not ids:
            return
        yield {
            "ids": ids,
            "embeddings": page["embeddings"],
            "documents": page["documents"],
            "metadatas": page["metadatas"],
        }
        offset += len(ids)


def reattach_legacy_vectors(
    data_dir: Path, profile: ModelProfileSnapshot
) -> tuple[ReattachedCollection, ...]:
    """Rename legacy collections to the current names, keeping newer writes.

    A current collection that already exists (written after the rename broke)
    is merged into the legacy one first; on an id present in both, the newer
    current record wins. Callers must have stopped every SDK writer.
    """
    pairs = legacy_vector_collections(data_dir, profile)
    if not pairs:
        return ()
    client = open_offline_chroma(_vector_directory(data_dir))
    # Closed, not left to the process: Chroma keeps one shared instance per
    # directory, and the readback that follows opens the same directory
    # through the SDK. A leaked instance failed that open on every 1.0.4 home
    # with legacy vectors, so the first launch after upgrade always failed.
    try:
        existing = set(_stored_names(data_dir))
        done: list[ReattachedCollection] = []
        for legacy_name, current_name in pairs:
            legacy = client.get_collection(legacy_name)
            legacy_count = legacy.count()
            merged = 0
            if current_name in existing:
                current = client.get_collection(current_name)
                legacy_ids = set(_ids(legacy))
                current_ids: set[str] = set()
                for batch in _records(current):
                    legacy.upsert(**batch)
                    current_ids.update(batch["ids"])
                merged = len(current_ids)
                if legacy.count() != len(legacy_ids | current_ids):
                    raise DataPlaneMigrationError("sdk_migration_vector_merge_incomplete")
                client.delete_collection(current_name)
            legacy.modify(name=current_name)
            final = client.get_collection(current_name).count()
            if final < legacy_count:
                raise DataPlaneMigrationError("sdk_migration_vector_reattach_incomplete")
            existing.discard(legacy_name)
            existing.add(current_name)
            done.append(
                ReattachedCollection(
                    legacy=legacy_name,
                    current=current_name,
                    legacy_count=legacy_count,
                    merged_count=merged,
                    final_count=final,
                )
            )
    finally:
        client.close()
    return tuple(done)


def _ids(collection: Any) -> Iterator[str]:
    offset = 0
    while True:
        ids = list(collection.get(include=[], limit=_BATCH, offset=offset)["ids"])
        if not ids:
            return
        yield from ids
        offset += len(ids)
