"""Backups that contain the memories: format v2 (`docs/contracts/backup-v2.md`).

Until 1.0.7 a MemHub backup was one SQLite file, the management database. The
memories and raw turns live in the SDK's stores under `<home>/hy-memory/`
(Chroma, Kuzu, the SDK's SQLite caches), or on servers a `storage_backends`
setting names, and were never in it. A v2 backup is a directory:

```
<name>.memhub-backup/
  manifest.json                      format, version, backend, space, counts, files
  management/one-memory.sqlite3      the management database (SQLite backup API)
  sdk/governed-v2/...                every SDK file of this home (local stores)
  export/memories.jsonl              Qdrant only: the memories, through the SDK
```

The SDK files are copied while SDK writes are paused and every SDK client is
closed (Kuzu checkpoints on the SDK's close) with the stores held offline;
SQLite files go through SQLite's backup API, the rest is streamed. A restore
stages everything next to the live data, swaps it in with renames under a
journal, verifies the counts the manifest recorded and rolls back on any
mismatch. Old backups (one `.sqlite3` file) still restore as before.

The SDK is reached only through `hy_sdk_migration` (offline store maintenance)
and public client methods; this module imports no SDK code.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, closing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from one_memory.database_access import close_main_databases, main_database
from one_memory.hy_runtime_config import HY_MEMORY_NAMESPACE, HY_MEMORY_VERSION
from one_memory.storage_backends import StorageBackends

logger = logging.getLogger(__name__)

BACKUP_FORMAT = "memhub-backup"
#: v1 was the bare management database file (no manifest). v2 holds the memories.
BACKUP_SCHEMA_VERSION = 2
BACKUP_SUFFIX = ".memhub-backup"
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "one-memory.sqlite3"
MANAGEMENT_PATH = f"management/{DATABASE_NAME}"
SDK_PREFIX = f"sdk/{HY_MEMORY_NAMESPACE}"
EXPORT_PATH = "export/memories.jsonl"
#: The `node` lines of an export: `MemoryNode.to_dict()` with its vector, as
#: hy-memory 2.0.16's `export_user_memories` returns it. An export without it
#: (MemHub before 2.0.16: list items, no vectors) cannot be written back.
EXPORT_NODE_FORMAT = "hy-memory-node-v1"
RESTORE_JOURNAL = ".memhub-restore.json"
PREVIOUS_SUFFIX = ".memhub-previous"
#: Beside the protective copy: this home's Qdrant memories before a restore.
PREVIOUS_VECTORS_SUFFIX = ".memhub-previous-vectors.jsonl"

#: Restoring onto Qdrant: what hy-memory 2.0.16's export/import cannot carry.
#: Coding/Working experiences keep their vectors in collections of their own
#: (`coding_keys*`, `working_facts*`), which no public SDK call exports or
#: writes back with their ids; their SQLite rows would come back without them.
RESTORE_QDRANT_EXPERIENCES = "backup_restore_qdrant_experiences_unsupported"
#: The hidden retrieval clauses of long L2 memories (`<memory>__retrieval_clauses`)
#: are not in `export_user_memories` and `import_user_memories` does not rebuild them.
RESTORE_QDRANT_CLAUSES = "backup_restore_qdrant_retrieval_clauses_unsupported"
#: A Qdrant backup written before 2.0.16: its export holds no vectors or node ids.
RESTORE_EXPORT_WITHOUT_VECTORS = "backup_restore_export_without_vectors"
#: The embedding space changed since the backup: the backup's vectors belong to
#: another space's collections, and the live space's would be merged back in.
RESTORE_QDRANT_SPACE_CHANGED = "backup_restore_qdrant_space_changed"
#: No SDK client could be built to write the memories back (an offline restore).
RESTORE_VECTOR_CLIENT_UNAVAILABLE = "backup_restore_vector_client_unavailable"
#: `import_user_memories` refused or wrote fewer memories than the backup holds.
RESTORE_IMPORT_FAILED = "backup_restore_import_failed"
#: The Qdrant roles whose records name a Coding/Working experience or fact.
_EXPERIENCE_ROLES = ("coding_keys", "coding_keys_cv", "working_facts", "working_facts_cv")

#: The SDK's vector layers (`MemoryLayer`), raw turns included. L5-L7 live in
#: the graph and are counted there (`count_graph_memories`).
VECTOR_LAYERS = ("l0_basic_info", "l1_raw", "l2_fact", "l3_summary", "l4_identity")
#: Every lifecycle state the SDK keeps (`MemoryStatus`); deletes are physical.
ALL_STATUSES = ("active", "superseded", "negated", "conflicted", "shadow", "archived")
#: The SDK's Coding / Working experience scenes (`list_memories(scene=...)`).
EXPERIENCE_SCENES = ("coding", "working")

_COPY_CHUNK = 1024 * 1024
_EXPORT_PAGE = 500
_SQLITE_HEADER = b"SQLite format 3\x00"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
#: Never copied: SDK logs (open for append), temporaries, locks.
_SKIPPED_DIRECTORIES = frozenset({"logs"})
_SKIPPED_SUFFIXES = (".tmp", ".lock", ".partial", ".pid")
#: A quarantined graph (#413) and an archived mismatched graph are kept for
#: diagnosis only; the backup holds the graph the SDK opens.
_QUARANTINE_INFIX = ".quarantined-"
_ARCHIVED_GRAPH_SUFFIX = ".bak"

BackendKind = Literal["local", "remote_graph", "remote_vector"]


class BackupError(RuntimeError):
    """A backup or restore refusal with a stable, product-facing code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ----------------------------------------------------------------------
# Backend identity
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class BackendIdentity:
    """Where this home's memories live, as a backup records it."""

    vector: str = "chroma"
    graph: str = "kuzu"
    #: The Qdrant collection prefix (this home's namespace); None on Chroma.
    vector_prefix: str | None = None

    @classmethod
    def of(cls, settings: StorageBackends) -> BackendIdentity:
        return cls(
            vector=settings.vector.provider,
            graph=settings.graph.provider,
            vector_prefix=settings.collection_prefix,
        )

    @property
    def kind(self) -> BackendKind:
        if self.vector != "chroma":
            return "remote_vector"
        if self.graph != "kuzu":
            return "remote_graph"
        return "local"

    def document(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "vector": self.vector,
            "graph": self.graph,
            "vector_prefix": self.vector_prefix,
        }

    @classmethod
    def from_document(cls, value: object) -> BackendIdentity:
        if not isinstance(value, Mapping):
            raise BackupError("backup_restore_manifest_invalid")
        vector, graph, prefix = value.get("vector"), value.get("graph"), value.get("vector_prefix")
        if not isinstance(vector, str) or not isinstance(graph, str):
            raise BackupError("backup_restore_manifest_invalid")
        if prefix is not None and not isinstance(prefix, str):
            raise BackupError("backup_restore_manifest_invalid")
        return cls(vector=vector, graph=graph, vector_prefix=prefix)


# ----------------------------------------------------------------------
# What a backup copies
# ----------------------------------------------------------------------


def sdk_root(data_dir: Path) -> Path:
    return data_dir.resolve() / "hy-memory" / HY_MEMORY_NAMESPACE


@dataclass(frozen=True)
class PlannedFile:
    source: Path
    #: Path inside the backup directory, POSIX separators.
    relative: str
    sqlite: bool
    bytes: int


def _skipped(name: str) -> bool:
    return (
        name.startswith(".")
        or _QUARANTINE_INFIX in name
        or name.endswith(_SKIPPED_SUFFIXES)
        or (".dim" in name and name.endswith(_ARCHIVED_GRAPH_SUFFIX))
    )


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
    except OSError:
        return False


def _walk(root: Path) -> Iterator[Path]:
    """Every file under `root` a backup copies, in a stable order."""
    for current, directories, files in os.walk(root, followlinks=False):
        here = Path(current)
        kept = []
        for name in sorted(directories):
            path = here / name
            if path.is_symlink():
                raise BackupError("backup_sdk_root_symlink")
            if here == root and name in _SKIPPED_DIRECTORIES:
                continue
            if _skipped(name):
                continue
            kept.append(name)
        directories[:] = kept
        names = set(files)
        for name in sorted(files):
            path = here / name
            if path.is_symlink():
                raise BackupError("backup_sdk_root_symlink")
            if _skipped(name):
                continue
            # A SQLite sidecar is folded into its database by the backup API.
            if any(
                name.endswith(suffix) and name[: -len(suffix)] in names
                for suffix in _SQLITE_SIDECARS
            ):
                continue
            yield path


def plan_files(data_dir: Path) -> list[PlannedFile]:
    """The management database first, then every SDK file of the home."""
    home = data_dir.resolve()
    database = home / DATABASE_NAME
    if not database.is_file() or database.is_symlink():
        raise BackupError("backup_database_missing")
    planned = [PlannedFile(database, MANAGEMENT_PATH, True, _size(database))]
    root = sdk_root(home)
    if root.is_symlink():
        raise BackupError("backup_sdk_root_symlink")
    if root.is_dir():
        for path in _walk(root):
            relative = path.relative_to(root).as_posix()
            planned.append(
                PlannedFile(path, f"{SDK_PREFIX}/{relative}", _is_sqlite(path), _size(path))
            )
    return planned


def _size(path: Path) -> int:
    with suppress(OSError):
        size = path.stat().st_size
        for suffix in ("-wal",):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.is_file():
                size += sidecar.stat().st_size
        return size
    return 0


#: A rough size of one exported memory line without its vector.
EXPORT_BYTES_PER_MEMORY = 1536
#: A float of a vector as JSON text (`-0.012345678901234567,`), on average.
EXPORT_BYTES_PER_DIMENSION = 21
#: The width assumed for an estimate when the live space has none recorded.
_ESTIMATE_DIMENSIONS = 1024


@dataclass(frozen=True)
class BackupEstimate:
    bytes: int
    files: int
    local_bytes: int
    export_bytes: int
    free_bytes: int | None
    backend: BackendIdentity

    def document(self) -> dict[str, object]:
        return {
            "bytes": self.bytes,
            "files": self.files,
            "local_bytes": self.local_bytes,
            "export_bytes": self.export_bytes,
            "free_bytes": self.free_bytes,
            "memories_included": True,
            "backend": self.backend.document(),
        }


def estimate(
    data_dir: Path,
    *,
    backend: BackendIdentity,
    memories: int | None = None,
    dims: int | None = None,
) -> BackupEstimate:
    """What `create_backup` would write: no file is read, only sizes.

    On Qdrant the export carries every memory's vector, `dims` floats each.
    """
    planned = plan_files(data_dir)
    local = sum(item.bytes for item in planned)
    per_memory = EXPORT_BYTES_PER_MEMORY + EXPORT_BYTES_PER_DIMENSION * (
        dims or _ESTIMATE_DIMENSIONS
    )
    export = (memories or 0) * per_memory if backend.kind == "remote_vector" else 0
    free: int | None = None
    with suppress(OSError):
        free = shutil.disk_usage(data_dir.resolve()).free
    return BackupEstimate(local + export, len(planned), local, export, free, backend)


def confined_backup_path(data_dir: Path, value: str, *, code: str) -> Path:
    """The one place a person-supplied backup path becomes a filesystem path.

    `value` may be a bare name, `backups/<name>`, or an absolute path; it is
    canonicalised (`..` collapsed, symlinks followed) and must then name a
    direct child of `<home>/backups`. A symlink at that name is refused, as
    is a `backups` directory that is itself a link elsewhere: the canonical
    path is compared with the literal `<home>/backups`, so either escape
    lands outside it. Anything else raises `code`.
    """
    if not value or "\x00" in value or len(value) > 4096:
        raise BackupError(code)
    home = os.path.realpath(data_dir)
    backups = os.path.join(home, "backups")
    # Lexically first: nothing outside `backups/` is even looked at.
    joined = os.path.normpath(os.path.join(home, value))
    if not joined.startswith(backups + os.sep) or os.path.dirname(joined) != backups:
        raise BackupError(code)
    # Then on disk: no link at the name, and no link on the way to it.
    if os.path.islink(joined):
        raise BackupError(code)
    real = os.path.realpath(joined)
    if not real.startswith(backups + os.sep) or real != joined:
        raise BackupError(code)
    return Path(real)


def resolve_target(data_dir: Path, target: str) -> Path:
    """The backup directory `target` names: a new entry of `<home>/backups`.

    `backups/x.sqlite3` (the v1 habit) becomes `backups/x.memhub-backup`: a
    v2 backup is a directory, and its name says so.
    """
    head, name = os.path.split(target.rstrip("/"))
    if name.endswith(".sqlite3"):
        name = name.removesuffix(".sqlite3")
    if name and name not in {".", ".."} and not name.endswith(BACKUP_SUFFIX):
        name = f"{name}{BACKUP_SUFFIX}"
    resolved = confined_backup_path(
        data_dir, os.path.join(head, name), code="backup_target_outside_data_dir"
    )
    if resolved.exists() or resolved.is_symlink():
        raise BackupError("backup_target_exists")
    return resolved


# ----------------------------------------------------------------------
# Writing a backup
# ----------------------------------------------------------------------


def _owner_only_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise BackupError("backup_target_invalid")
    path.chmod(0o700)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync(path: Path) -> None:
    with suppress(OSError):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _sqlite_copy(source: Path, target: Path) -> None:
    """SQLite's online backup API, then an integrity check of the copy.

    For the SDK's own SQLite files (Chroma's, the cache, history, coding):
    the main database goes through `MainDatabase.back_up` instead.
    """
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    # One open per statement: the main-database gate reads each one's mode.
    with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as live:  # noqa: SIM117
        with closing(sqlite3.connect(target)) as copy:
            live.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise BackupError("backup_sqlite_copy_invalid")
            # A self-contained file: no WAL left beside the copy.
            copy.execute("PRAGMA journal_mode=DELETE")
    target.chmod(0o600)


def _stream_copy(source: Path, target: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
        for chunk in iter(lambda: reader.read(_COPY_CHUNK), b""):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def copy_files(planned: Iterable[PlannedFile], staging: Path) -> list[dict[str, object]]:
    """Copy every planned file into `staging`; the caller has quiesced the SDK.

    Returns the inventory (path, bytes, sha256) of what was written.
    """
    inventory: list[dict[str, object]] = []
    for item in planned:
        target = staging / item.relative
        _owner_only_directory(target.parent)
        if not item.source.exists():
            # Removed by the SDK between the plan and the copy (a temporary).
            continue
        if item.relative == MANAGEMENT_PATH:
            # Through the access layer, like every other open of the main database.
            main_database(item.source).back_up(target)
            digest = _sha256(target)
        elif item.sqlite:
            _sqlite_copy(item.source, target)
            digest = _sha256(target)
        else:
            digest = _stream_copy(item.source, target)
        inventory.append({"path": item.relative, "bytes": target.stat().st_size, "sha256": digest})
    return inventory


def count_layers(client: Any, users: Iterable[str]) -> dict[str, dict[str, int]]:
    """Per user, the SDK's count of every vector layer (every status) and scene.

    Public SDK reads only (`list_memories` totals, `memory_stats`). Raw turns
    are the `l1_raw` layer, active and shadow included.
    """
    counts: dict[str, dict[str, int]] = {}
    for user_id in users:
        per_user: dict[str, int] = {}
        for layer in VECTOR_LAYERS:
            listed = client.list_memories(
                user_id=user_id, layers=[layer], statuses=list(ALL_STATUSES), limit=1
            )
            vdb = listed.get("vdb") if isinstance(listed, Mapping) else None
            total = vdb.get("total") if isinstance(vdb, Mapping) else None
            per_user[layer] = int(total) if isinstance(total, int) else 0
        stats = client.memory_stats(user_id=user_id)
        scenes = stats.get("scenes") if isinstance(stats, Mapping) else None
        for scene in EXPERIENCE_SCENES:
            entry = scenes.get(scene) if isinstance(scenes, Mapping) else None
            count = entry.get("count") if isinstance(entry, Mapping) else None
            per_user[f"scene:{scene}"] = int(count) if isinstance(count, int) else 0
        counts[user_id] = per_user
    return counts


def write_export(client: Any, users: Iterable[str], target: Path) -> dict[str, object]:
    """The memories on a vector server, as portable JSONL, through the SDK.

    Two kinds of line. `{"user_id", "node"}`: one memory of the vector store,
    exactly as the SDK's `export_user_memories` returns it (hy-memory 2.0.16:
    `MemoryNode.to_dict()` with its id, layer, status, evolution links, times
    and vector), every status and every layer, raw turns included; a restore
    writes these back with `import_user_memories`. `{"user_id", "scene",
    "memory"}`: a Coding/Working experience as `list_memories(scene=...)`
    lists it. Those have no SDK export with their vectors, which is why a
    restore onto Qdrant refuses a home that has any
    (`backup_restore_qdrant_experiences_unsupported`). Written as it is read.
    """
    _owner_only_directory(target.parent)
    digest = hashlib.sha256()
    counts: dict[str, dict[str, int]] = {}
    dims: int | None = None
    model: str | None = None
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as writer:

        def emit(record: dict[str, object]) -> None:
            line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
            digest.update(line)
            writer.write(line)

        for user_id in users:
            per_user = counts.setdefault(user_id, {layer: 0 for layer in VECTOR_LAYERS})
            exported = client.export_user_memories(user_id, include_embeddings=True)
            if not isinstance(exported, Mapping) or exported.get("success") is False:
                raise BackupError("backup_sdk_unavailable")
            nodes = exported.get("nodes")
            for node in nodes if isinstance(nodes, list) else ():
                if not isinstance(node, Mapping):
                    raise BackupError("backup_sdk_unavailable")
                emit({"user_id": user_id, "node": dict(node)})
                layer = str(node.get("layer") or "")
                per_user[layer] = per_user.get(layer, 0) + 1
            exported_dims = exported.get("embedding_dims")
            if isinstance(exported_dims, int) and not isinstance(exported_dims, bool):
                dims = exported_dims
            if isinstance(exported.get("embedding_model"), str):
                model = str(exported["embedding_model"])
            for scene in EXPERIENCE_SCENES:
                key = f"scene:{scene}"
                per_user[key] = 0
                offset = 0
                while True:
                    listed = client.list_memories(
                        user_id=user_id,
                        scene=scene,
                        statuses=list(ALL_STATUSES),
                        limit=_EXPORT_PAGE,
                        offset=offset,
                    )
                    items = (
                        listed.get("experiences") or listed.get("memories")
                        if isinstance(listed, Mapping)
                        else None
                    )
                    if not items:
                        break
                    for item in items:
                        emit({"user_id": user_id, "scene": scene, "memory": item})
                    per_user[key] += len(items)
                    offset += len(items)
                    if len(items) < _EXPORT_PAGE:
                        break
        writer.flush()
        os.fsync(writer.fileno())
    return {
        "path": EXPORT_PATH,
        "format": "jsonl",
        "node_format": EXPORT_NODE_FORMAT,
        "embeddings_included": True,
        "embedding_dims": dims,
        "embedding_model": model,
        "counts": counts,
        "sha256": digest.hexdigest(),
    }


def read_export(path: Path, sha256: str) -> dict[str, list[dict[str, object]]]:
    """The exported memories per user, after checking the file's hash.

    Only the `node` lines: what `import_user_memories` writes back.
    """
    digest = hashlib.sha256()
    nodes: dict[str, list[dict[str, object]]] = {}
    try:
        with path.open("rb") as reader:
            for line in reader:
                digest.update(line)
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("user_id"), str):
                    raise BackupError("backup_restore_verify_failed")
                node = record.get("node")
                if node is None:
                    continue
                if not isinstance(node, dict) or node.get("user_id") != record["user_id"]:
                    raise BackupError("backup_restore_verify_failed")
                nodes.setdefault(record["user_id"], []).append(node)
    except (OSError, ValueError) as error:
        raise BackupError("backup_restore_verify_failed") from error
    if digest.hexdigest() != sha256:
        raise BackupError("backup_restore_verify_failed")
    return nodes


@dataclass
class BackupResult:
    target: Path
    bytes: int
    files: int
    pause_ms: int
    copy_ms: int
    manifest: dict[str, object]

    def document(self) -> dict[str, object]:
        manifest = self.manifest
        return {
            "target": str(self.target),
            "bytes": self.bytes,
            "files": self.files,
            "schema_version": BACKUP_SCHEMA_VERSION,
            "memories_included": True,
            "pause_ms": self.pause_ms,
            "copy_ms": self.copy_ms,
            "backend": manifest.get("backend"),
            "users": manifest.get("users"),
            "counts": manifest.get("counts"),
        }


def staging_for(target: Path) -> Path:
    return target.with_name(f".{target.name}.partial")


def finish_backup(
    *,
    staging: Path,
    target: Path,
    inventory: list[dict[str, object]],
    manifest: dict[str, object],
) -> BackupResult:
    """Write the manifest and publish the backup with one rename."""
    document = {
        **manifest,
        "files": inventory,
        "file_count": len(inventory),
        "total_bytes": sum(int(str(item["bytes"])) for item in inventory),
    }
    path = staging / MANIFEST_NAME
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
        writer.write(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        writer.flush()
        os.fsync(writer.fileno())
    for directory in sorted({path.parent for path in staging.rglob("*")}, reverse=True):
        directory.chmod(0o700)
        _fsync(directory)
    staging.chmod(0o700)
    if target.exists():
        raise BackupError("backup_target_exists")
    staging.rename(target)
    _fsync(target.parent)
    return BackupResult(
        target=target,
        bytes=int(str(document["total_bytes"])),
        files=len(inventory),
        pause_ms=int(str(manifest.get("pause_ms", 0))),
        copy_ms=int(str(manifest.get("copy_ms", 0))),
        manifest=document,
    )


def base_manifest(
    *,
    created_at: datetime,
    backend: BackendIdentity,
    live_space: Mapping[str, object] | None,
    spaces: list[Mapping[str, object]],
    memory_mode: str,
    users: list[str],
    runtime_build: str | None,
) -> dict[str, object]:
    return {
        "format": BACKUP_FORMAT,
        "schema_version": BACKUP_SCHEMA_VERSION,
        "created_at": created_at.isoformat(),
        "runtime_build": runtime_build,
        "sdk_version": HY_MEMORY_VERSION,
        "sdk_namespace": HY_MEMORY_NAMESPACE,
        "memories_included": True,
        "backend": backend.document(),
        "memory_mode": memory_mode,
        "embedding_space": dict(live_space) if live_space is not None else None,
        "embedding_spaces": [dict(space) for space in spaces],
        "users": users,
        "contents": {
            "management": MANAGEMENT_PATH,
            "sdk_root": SDK_PREFIX,
            "export": EXPORT_PATH if backend.kind == "remote_vector" else None,
        },
    }


# ----------------------------------------------------------------------
# Reading a backup
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class BackupSource:
    path: Path
    #: 1: the bare management database; 2: the directory with a manifest.
    schema_version: int
    manifest: dict[str, object] = field(default_factory=dict)


def locate(data_dir: Path, source: str) -> BackupSource:
    """The managed backup `source` names: a v1 file or a v2 directory in `backups/`."""
    resolved = confined_backup_path(data_dir, source, code="backup_restore_source_outside_home")
    if resolved.is_file():
        return BackupSource(resolved, 1)
    if not resolved.is_dir():
        raise BackupError("backup_restore_source_outside_home")
    return BackupSource(resolved, BACKUP_SCHEMA_VERSION, read_manifest(resolved))


def read_manifest(backup: Path) -> dict[str, object]:
    try:
        manifest = json.loads((backup / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise BackupError("backup_restore_manifest_invalid") from error
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
        raise BackupError("backup_restore_manifest_invalid")
    version = manifest.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise BackupError("backup_restore_manifest_invalid")
    if version > BACKUP_SCHEMA_VERSION:
        raise BackupError("backup_restore_source_too_new")
    if version != BACKUP_SCHEMA_VERSION:
        raise BackupError("backup_restore_manifest_invalid")
    if not isinstance(manifest.get("files"), list):
        raise BackupError("backup_restore_manifest_invalid")
    return manifest


def _files(manifest: Mapping[str, object]) -> list[dict[str, object]]:
    listed = manifest.get("files")
    files: list[dict[str, object]] = []
    for item in listed if isinstance(listed, list) else ():
        if not isinstance(item, dict):
            raise BackupError("backup_restore_manifest_invalid")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative:
            raise BackupError("backup_restore_manifest_invalid")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise BackupError("backup_restore_manifest_invalid")
        if not isinstance(item.get("bytes"), int) or not isinstance(item.get("sha256"), str):
            raise BackupError("backup_restore_manifest_invalid")
        files.append(item)
    return files


def verify_files(root: Path, files: list[dict[str, object]], *, prefix: str = "") -> None:
    """Every listed file under `prefix` is present with its size and hash, and nothing else."""
    wanted = {
        str(item["path"])[len(prefix) :]: item
        for item in files
        if str(item["path"]).startswith(prefix)
    }
    present = (
        {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        if root.is_dir()
        else set()
    )
    if prefix == "":
        present.discard(MANIFEST_NAME)
    if present != set(wanted):
        raise BackupError("backup_restore_verify_failed")
    for relative, item in wanted.items():
        path = root / relative
        if (
            path.is_symlink()
            or path.stat().st_size != int(str(item["bytes"]))
            or _sha256(path) != item["sha256"]
        ):
            raise BackupError("backup_restore_verify_failed")


# ----------------------------------------------------------------------
# Restoring a v2 backup
# ----------------------------------------------------------------------


@dataclass
class PreparedRestore:
    """A verified backup staged next to the live data, ready to be swapped in."""

    home: Path
    source: BackupSource
    manifest: dict[str, object]
    backend: BackendIdentity
    staged_sdk: Path
    #: A copy of the backup's management database, checked and restored from.
    staged_database: Path
    previous: Path
    stamp: str
    bytes: int
    files: int
    required_tables: frozenset[str] = frozenset()


def _journal(home: Path) -> Path:
    return home / RESTORE_JOURNAL


def restore_pending(data_dir: Path) -> bool:
    return _journal(data_dir.resolve()).is_file()


def _write_journal(home: Path, document: dict[str, object]) -> None:
    path = _journal(home)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as writer:
        writer.write(json.dumps(document, sort_keys=True) + "\n")
        writer.flush()
        os.fsync(writer.fileno())
    temporary.replace(path)
    _fsync(home)


def _role_records(counts: object, roles: Iterable[str]) -> int:
    """Records in `roles` across every space of a `vector_store_counts` document."""
    wanted = set(roles)
    total = 0
    for space in counts.values() if isinstance(counts, Mapping) else ():
        for role, per_user in space.items() if isinstance(space, Mapping) else ():
            if role in wanted and isinstance(per_user, Mapping):
                total += sum(value for value in per_user.values() if isinstance(value, int))
    return total


def qdrant_restore_refusal(
    manifest: Mapping[str, object],
    *,
    current_space_id: str | None,
    current_vectors: Mapping[str, object] | None,
) -> str | None:
    """Why a Qdrant backup cannot be written back onto this home's server; None if it can.

    hy-memory 2.0.16 exports and imports the memory collection only (ids,
    statuses, evolution links, times and vectors, raw turns included). It
    has nothing for Coding/Working experience vectors or the retrieval-clause
    index, so a backup or a live server holding either is refused rather than
    restored without them. Checked before anything changes.
    """
    export = manifest.get("export")
    if (
        not isinstance(export, Mapping)
        or export.get("embeddings_included") is not True
        or export.get("node_format") != EXPORT_NODE_FORMAT
        or not isinstance(export.get("sha256"), str)
    ):
        return RESTORE_EXPORT_WITHOUT_VECTORS
    space = manifest.get("embedding_space")
    space_id = space.get("space_id") if isinstance(space, Mapping) else None
    if current_space_id is None or space_id != current_space_id:
        return RESTORE_QDRANT_SPACE_CHANGED
    layers = _counts(manifest, "layers")
    for per_user in layers.values() if isinstance(layers, Mapping) else ():
        if not isinstance(per_user, Mapping):
            continue
        if any(per_user.get(f"scene:{scene}") for scene in EXPERIENCE_SCENES):
            return RESTORE_QDRANT_EXPERIENCES
    for counts in (_counts(manifest, "vector_store"), current_vectors):
        if _role_records(counts, _EXPERIENCE_ROLES):
            return RESTORE_QDRANT_EXPERIENCES
    for counts in (_counts(manifest, "vector_store"), current_vectors):
        if _role_records(counts, ("retrieval_clauses",)):
            return RESTORE_QDRANT_CLAUSES
    return None


def prepare_restore(
    data_dir: Path,
    source: BackupSource,
    *,
    current: BackendIdentity,
    now: datetime | None = None,
    current_space_id: str | None = None,
    current_vectors: Mapping[str, object] | None = None,
) -> PreparedRestore:
    """Verify a v2 backup against this home and stage its SDK files; nothing live changes."""
    home = data_dir.resolve()
    if restore_pending(home):
        raise BackupError("backup_restore_pending")
    manifest = source.manifest
    if manifest.get("sdk_namespace") != HY_MEMORY_NAMESPACE:
        raise BackupError("backup_restore_manifest_invalid")
    backend = BackendIdentity.from_document(manifest.get("backend"))
    if backend.kind != current.kind or (
        backend.vector != current.vector or backend.graph != current.graph
    ):
        # Converting between a local store and a server is a different job:
        # every memory written into another store with its id and vector.
        # Refused instead of restoring memories this home would not read.
        raise BackupError("backup_restore_backend_mismatch")
    if backend.kind == "remote_vector":
        if backend.vector_prefix != current.vector_prefix:
            # Another home's namespace on the server (or another server's).
            raise BackupError("backup_restore_backend_mismatch")
        refusal = qdrant_restore_refusal(
            manifest, current_space_id=current_space_id, current_vectors=current_vectors
        )
        if refusal is not None:
            raise BackupError(refusal)
    files = _files(manifest)
    verify_files(source.path, files)
    total = sum(int(str(item["bytes"])) for item in files)
    with suppress(OSError):
        if shutil.disk_usage(home).free < total + 64 * 1024 * 1024:
            raise BackupError("backup_restore_disk_space_insufficient")

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    staged = home / "hy-memory" / f".{HY_MEMORY_NAMESPACE}.backup-restore-{stamp}"
    _owner_only_directory(staged.parent)
    # The backup itself is never opened: SQLite would leave `-wal`/`-shm`
    # files beside a WAL database it reads, and the backup would no longer
    # match its manifest. Its management database is checked as a copy.
    staged_database = staged_database_path(home, stamp)
    try:
        shutil.copyfile(source.path / MANAGEMENT_PATH, staged_database)
        staged_database.chmod(0o600)
        verify_files_at(staged_database, files, MANAGEMENT_PATH)
        required_tables = _check_database(staged_database)
    except BaseException:
        _remove_database(staged_database)
        raise
    try:
        shutil.copytree(source.path / SDK_PREFIX, staged, symlinks=True, dirs_exist_ok=False)
    except FileNotFoundError:
        staged.mkdir(mode=0o700)
    except OSError as error:
        shutil.rmtree(staged, ignore_errors=True)
        _remove_database(staged_database)
        raise BackupError("backup_restore_stage_failed") from error
    try:
        verify_files(staged, files, prefix=f"{SDK_PREFIX}/")
    except BackupError:
        shutil.rmtree(staged, ignore_errors=True)
        _remove_database(staged_database)
        raise
    (staged / "logs").mkdir(mode=0o700, exist_ok=True)
    previous = home / "backups" / f"pre-restore-{stamp}{PREVIOUS_SUFFIX}"
    return PreparedRestore(
        home=home,
        source=source,
        manifest=manifest,
        backend=backend,
        staged_sdk=staged,
        staged_database=staged_database,
        previous=previous,
        stamp=stamp,
        bytes=total,
        files=len(files),
        required_tables=required_tables,
    )


def staged_database_path(home: Path, stamp: str) -> Path:
    return home / "hy-memory" / f".management.backup-restore-{stamp}.sqlite3"


def _remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def verify_files_at(path: Path, files: list[dict[str, object]], relative: str) -> None:
    """One staged copy of a listed file has the listed size and hash."""
    for item in files:
        if item["path"] == relative:
            if path.stat().st_size != int(str(item["bytes"])) or _sha256(path) != item["sha256"]:
                raise BackupError("backup_restore_verify_failed")
            return
    raise BackupError("backup_restore_manifest_invalid")


def _check_database(path: Path) -> frozenset[str]:
    """The backup's management database is One Memory's, and not from a newer build.

    Returns the tables that prove it, for the atomic replace to check again.
    """
    from one_memory.managed_restore import database_identity

    identity = database_identity(path)
    if identity is None:
        raise BackupError("backup_restore_source_invalid")
    if identity.too_new:
        raise BackupError("backup_restore_source_too_new")
    return identity.required_tables


def discard_prepared(prepared: PreparedRestore) -> None:
    shutil.rmtree(prepared.staged_sdk, ignore_errors=True)
    close_main_databases()
    _remove_database(prepared.staged_database)


def swap_in(prepared: PreparedRestore) -> None:
    """Replace the live management database and SDK files; the caller quiesced the SDK.

    The journal is written first: a process killed in here is rolled back to
    the previous data by the next start (`recover_interrupted_restore`).
    """
    home = prepared.home
    live_sdk = sdk_root(home)
    previous_sdk = prepared.previous / SDK_PREFIX
    previous_database = prepared.previous / MANAGEMENT_PATH
    _owner_only_directory(previous_database.parent)
    _owner_only_directory(previous_sdk.parent)
    _write_journal(
        home,
        {
            "schema_version": 1,
            "stamp": prepared.stamp,
            "previous": prepared.previous.relative_to(home).as_posix(),
            "staged": prepared.staged_sdk.relative_to(home).as_posix(),
            "staged_database": prepared.staged_database.relative_to(home).as_posix(),
        },
    )
    live_database = home / DATABASE_NAME
    if live_database.exists():
        main_database(live_database).back_up(previous_database)
    from one_memory.hy_sdk_migration import release_chroma_systems

    release_chroma_systems()
    if live_sdk.exists():
        live_sdk.replace(previous_sdk)
    prepared.staged_sdk.replace(live_sdk)
    _fsync(live_sdk.parent)
    _replace_database(live_database, prepared.staged_database, prepared.required_tables)


#: How long a replace waits for in-process database checkouts to end.
_CHECKOUT_WAIT_S = 30.0


def _replace_database(live: Path, source: Path, required_tables: frozenset[str]) -> None:
    """`restore_from`, waiting out a checkout another task holds for a moment.

    `restore_from` refuses (before changing anything) while any connection of
    this process is checked out; a maintenance Core still answers status
    reads, so one may be for a few milliseconds.
    """
    deadline = time.monotonic() + _CHECKOUT_WAIT_S
    while True:
        close_main_databases()
        try:
            main_database(live).restore_from(source, required_tables=required_tables)
        except RuntimeError as error:
            if "checkouts" not in str(error) or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
            continue
        finally:
            close_main_databases()
        return


def roll_back(data_dir: Path) -> bool:
    """Put the previous data back after a failed or interrupted swap. True if it did."""
    home = data_dir.resolve()
    journal = _journal(home)
    try:
        document = json.loads(journal.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as error:
        raise BackupError("backup_restore_rollback_failed") from error
    previous_name = document.get("previous") if isinstance(document, dict) else None
    staged_name = document.get("staged") if isinstance(document, dict) else None
    if not isinstance(previous_name, str) or not isinstance(staged_name, str):
        raise BackupError("backup_restore_rollback_failed")
    previous = home / previous_name
    live_sdk = sdk_root(home)
    previous_sdk = previous / SDK_PREFIX
    previous_database = previous / MANAGEMENT_PATH
    from one_memory.hy_sdk_migration import release_chroma_systems

    release_chroma_systems()
    try:
        if previous_sdk.exists():
            if live_sdk.exists():
                discarded = home / "hy-memory" / f".{HY_MEMORY_NAMESPACE}.restore-discarded"
                shutil.rmtree(discarded, ignore_errors=True)
                live_sdk.replace(discarded)
                shutil.rmtree(discarded, ignore_errors=True)
            previous_sdk.replace(live_sdk)
        if previous_database.is_file():
            _replace_database(home / DATABASE_NAME, previous_database, frozenset())
        shutil.rmtree(home / staged_name, ignore_errors=True)
        shutil.rmtree(previous, ignore_errors=True)
        staged_database = document.get("staged_database")
        if isinstance(staged_database, str):
            close_main_databases()
            _remove_database(home / staged_database)
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
        raise BackupError("backup_restore_rollback_failed") from error
    journal.unlink(missing_ok=True)
    _fsync(home)
    return True


def commit(prepared: PreparedRestore) -> None:
    """The restore stands: drop the journal; the previous data stays as a protective copy."""
    close_main_databases()
    _remove_database(prepared.staged_database)
    _journal(prepared.home).unlink(missing_ok=True)
    _fsync(prepared.home)


def recover_interrupted_restore(data_dir: Path) -> bool:
    """At startup: a restore killed between its renames is rolled back."""
    return roll_back(data_dir)


@dataclass
class Verification:
    vector_store: Literal["verified", "skipped"] = "skipped"
    layers: Literal["verified", "skipped"] = "skipped"
    layers_reason: str | None = None
    graph: Literal["verified", "quarantined", "absent", "rebuild_scheduled", "skipped"] = "skipped"
    mismatches: list[str] = field(default_factory=list)

    def document(self) -> dict[str, object]:
        return {
            "vector_store": self.vector_store,
            "layers": self.layers,
            "layers_reason": self.layers_reason,
            "graph": self.graph,
            "mismatches": list(self.mismatches),
        }


def _counts(manifest: Mapping[str, object], key: str) -> Any:
    counts = manifest.get("counts")
    return counts.get(key) if isinstance(counts, Mapping) else None


def compare(name: str, expected: object, actual: object, into: list[str]) -> None:
    if expected != actual:
        into.append(name)


@dataclass
class RestoreResult:
    path: Path
    previous: Path
    bytes: int
    files: int
    duration_ms: int
    verification: Verification
    graph_rebuild_users: tuple[str, ...]
    source_schema_version: int
    backend: BackendIdentity
    #: On Qdrant: the server's memories as they were before the restore.
    previous_vectors: Path | None = None

    def document(self) -> dict[str, object]:
        return {
            "state": "restored",
            "restored_bytes": self.bytes,
            "restored_files": self.files,
            "previous_backup_path": str(self.previous),
            "source_schema_version": self.source_schema_version,
            "backup_schema_version": BACKUP_SCHEMA_VERSION,
            "memories_included": True,
            "backend": self.backend.document(),
            "verification": self.verification.document(),
            "graph_rebuild_scheduled": bool(self.graph_rebuild_users),
            "duration_ms": self.duration_ms,
            "note": None,
            "previous_vectors_path": (
                str(self.previous_vectors) if self.previous_vectors is not None else None
            ),
        }


Quiesce = Callable[[], Awaitable[Callable[[], None]]]
CountLayers = Callable[[list[str]], Awaitable[dict[str, dict[str, int]] | None]]
ScheduleGraphRebuild = Callable[[tuple[str, ...], bool], None]


@dataclass(frozen=True)
class QdrantRestore:
    """What a restore onto this home's Qdrant server needs from the running Core.

    `client` opens a public SDK client on the live space's Qdrant collections
    whose local files (graph, caches) are a scratch directory of its own: its
    `delete_all` and `import_user_memories` touch the server's memory
    collection and nothing of the home's restored files.
    """

    client: Callable[[], AbstractAsyncContextManager[Any]]
    #: This home's Qdrant records per space, role and user (`vector_store_counts`).
    vectors: Callable[[], Awaitable[Mapping[str, object]]]
    #: Every user this home knows now; their Qdrant memories are replaced too.
    current_users: tuple[str, ...]
    current_space_id: str | None


def _export_nodes(client: Any, users: Iterable[str]) -> dict[str, list[dict[str, object]]]:
    nodes: dict[str, list[dict[str, object]]] = {}
    for user_id in users:
        exported = client.export_user_memories(user_id, include_embeddings=True)
        if not isinstance(exported, Mapping) or exported.get("success") is False:
            raise BackupError(RESTORE_IMPORT_FAILED)
        listed = exported.get("nodes")
        nodes[user_id] = (
            [dict(node) for node in listed if isinstance(node, Mapping)]
            if isinstance(listed, list)
            else []
        )
    return nodes


def _write_nodes(path: Path, nodes: Mapping[str, list[dict[str, object]]]) -> None:
    _owner_only_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as writer:
        for user_id, items in nodes.items():
            for node in items:
                record = {"user_id": user_id, "node": node}
                writer.write(
                    (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
                )
        writer.flush()
        os.fsync(writer.fileno())


def _replace_nodes(
    client: Any, users: Iterable[str], nodes: Mapping[str, list[dict[str, object]]]
) -> None:
    """Make the server's memory collection hold exactly `nodes` for `users`.

    Every user's memories are deleted (`delete_all`), then each user's nodes
    are written back with their ids and vectors (`import_user_memories`,
    which requires an empty user and rolls its own partial write back).
    """
    for user_id in users:
        deleted = client.delete_all(user_id=user_id)
        if isinstance(deleted, Mapping) and deleted.get("success") is False:
            raise BackupError(RESTORE_IMPORT_FAILED)
    for user_id, items in nodes.items():
        if not items:
            continue
        imported = client.import_user_memories(user_id, items, require_empty=True)
        if (
            not isinstance(imported, Mapping)
            or imported.get("success") is not True
            or imported.get("imported_count") != len(items)
        ):
            logger.warning(
                "backup_restore_import_failed user_count=%d error=%s",
                len(items),
                imported.get("error_message") if isinstance(imported, Mapping) else None,
            )
            raise BackupError(RESTORE_IMPORT_FAILED)


@asynccontextmanager
async def _no_client() -> AsyncIterator[None]:
    yield None


async def _no_quiesce() -> Callable[[], None]:
    return lambda: None


async def run_restore(
    data_dir: Path,
    source: BackupSource,
    *,
    current: BackendIdentity,
    quiesce: Quiesce = _no_quiesce,
    count_layers_after: CountLayers | None = None,
    schedule_graph_rebuild: ScheduleGraphRebuild | None = None,
    graph_probe: Callable[[Path], str] | None = None,
    now: datetime | None = None,
    qdrant: QdrantRestore | None = None,
) -> RestoreResult:
    """Restore a v2 backup: stage, swap, verify the counts, roll back on any failure.

    `quiesce` closes every SDK client of this process and holds the stores
    offline, returning the release; a stopped Runtime needs none. The
    previous data is kept until the counts match, then stays as a protective
    copy (`previous_backup_path`).

    Onto Qdrant (`qdrant`), the local files are swapped as on any backend and
    the server's memory collection is written back through the SDK: the
    server's current memories are exported first (`previous_vectors_path`),
    every user's memories are deleted and the backup's are imported with their
    ids, statuses, links, times and vectors. Any failure writes the exported
    ones back before the files are rolled back.
    """
    import asyncio

    from one_memory.hy_sdk_migration import vector_store_counts

    started = time.monotonic()
    remote = (
        current.kind == "remote_vector"
        and BackendIdentity.from_document(source.manifest.get("backend")).kind == "remote_vector"
    )
    if remote and qdrant is None:
        raise BackupError(RESTORE_VECTOR_CLIENT_UNAVAILABLE)
    current_vectors = await qdrant.vectors() if remote and qdrant is not None else None
    prepared = await asyncio.to_thread(
        prepare_restore,
        data_dir,
        source,
        current=current,
        now=now,
        current_space_id=qdrant.current_space_id if qdrant is not None else None,
        current_vectors=current_vectors,
    )
    manifest = prepared.manifest
    listed = manifest.get("users")
    users = [user for user in listed if isinstance(user, str)] if isinstance(listed, list) else []
    verification = Verification()
    # Set before the swap starts: its journal is the first thing it writes,
    # and a rollback from any later point puts the previous data back.
    swapped = False
    graph_rebuild: tuple[str, ...] = ()
    previous_vectors: Path | None = None
    restored_nodes: dict[str, list[dict[str, object]]] = {}
    protective: dict[str, list[dict[str, object]]] | None = None
    replaced_users: list[str] = []
    vectors_kept = False
    try:
        if remote:
            export = manifest.get("export")
            sha256 = str(export.get("sha256")) if isinstance(export, Mapping) else ""
            restored_nodes = await asyncio.to_thread(read_export, source.path / EXPORT_PATH, sha256)
        opened = qdrant.client() if remote and qdrant is not None else _no_client()
    except BaseException:
        discard_prepared(prepared)
        raise
    try:
        async with opened as vectors:
            try:
                if remote:
                    assert qdrant is not None
                    replaced_users = sorted({*qdrant.current_users, *users, *restored_nodes})
                    protective = await asyncio.to_thread(_export_nodes, vectors, replaced_users)
                    previous_vectors = prepared.previous.with_name(
                        prepared.previous.name.removesuffix(PREVIOUS_SUFFIX)
                        + PREVIOUS_VECTORS_SUFFIX
                    )
                    await asyncio.to_thread(_write_nodes, previous_vectors, protective)
                release = await quiesce()
                try:
                    swapped = True
                    await asyncio.to_thread(swap_in, prepared)
                finally:
                    release()

                if remote:
                    await asyncio.to_thread(_replace_nodes, vectors, replaced_users, restored_nodes)
                    # Every node the backup holds was written back with its id.
                    verification.vector_store = "verified"
                else:
                    expected_vectors = _counts(manifest, "vector_store")
                    actual_vectors = await vector_store_counts(prepared.home)
                    compare(
                        "vector_store",
                        expected_vectors or {},
                        actual_vectors,
                        verification.mismatches,
                    )
                    verification.vector_store = "verified"

                if prepared.backend.graph != "kuzu":
                    # A graph server is derived data the restore cannot replace: its
                    # projection of the restored facts is rebuilt from scratch.
                    verification.graph = "rebuild_scheduled"
                    graph_rebuild = tuple(users)
                else:
                    graph_rebuild = await asyncio.to_thread(
                        _check_graph, prepared, verification, users, graph_probe
                    )

                if count_layers_after is not None:
                    actual_layers = await count_layers_after(users)
                    if actual_layers is None:
                        verification.layers_reason = "sdk_client_unavailable"
                    else:
                        compare(
                            "layers",
                            _counts(manifest, "layers") or {},
                            actual_layers,
                            verification.mismatches,
                        )
                        verification.layers = "verified"
                else:
                    verification.layers_reason = "runtime_stopped"
                if verification.mismatches:
                    raise BackupError("backup_restore_counts_mismatch")
            except BaseException:
                if protective is not None and swapped:
                    try:
                        await asyncio.to_thread(_replace_nodes, vectors, replaced_users, protective)
                    except Exception as rollback_error:  # noqa: BLE001 - files still roll back
                        # The server's previous memories stay in the export
                        # file beside the protective copy, for a manual retry.
                        vectors_kept = True
                        logger.error(
                            "backup_restore_vectors_rollback_failed error_type=%s kept=%s",
                            type(rollback_error).__name__,
                            previous_vectors,
                        )
                        raise BackupError("backup_restore_rollback_failed") from rollback_error
                raise
    except BaseException as error:
        if swapped:
            release = await quiesce()
            try:
                await asyncio.to_thread(roll_back, prepared.home)
            finally:
                release()
        else:
            discard_prepared(prepared)
        if previous_vectors is not None and not vectors_kept:
            # Nothing on the server changed, or it was written back.
            previous_vectors.unlink(missing_ok=True)
        if isinstance(error, BackupError) or not isinstance(error, Exception):
            raise
        from one_memory.sdk_migration_log import error_chain_text

        logger.warning(
            "backup_restore_failed error_type=%s chain=%s",
            type(error).__name__,
            error_chain_text(error),
        )
        raise BackupError("backup_restore_replace_failed") from error

    commit(prepared)
    if graph_rebuild and schedule_graph_rebuild is not None:
        schedule_graph_rebuild(graph_rebuild, prepared.backend.graph != "kuzu")
    return RestoreResult(
        path=source.path,
        previous=prepared.previous,
        bytes=prepared.bytes,
        files=prepared.files,
        duration_ms=int((time.monotonic() - started) * 1000),
        verification=verification,
        graph_rebuild_users=graph_rebuild,
        source_schema_version=_schema_version(prepared.home / DATABASE_NAME),
        backend=prepared.backend,
        previous_vectors=previous_vectors,
    )


def _schema_version(database: Path) -> int:
    from one_memory.managed_restore import database_identity

    identity = database_identity(database)
    return identity.schema_version if identity is not None else 0


def _check_graph(
    prepared: PreparedRestore,
    verification: Verification,
    users: list[str],
    graph_probe: Callable[[Path], str] | None,
) -> tuple[str, ...]:
    """The #413 probe on the restored live graph, then its counts.

    A graph that cannot be opened is quarantined (renamed aside, never
    deleted) and rebuilt from the restored memories after the next start: the
    graph is derived data, so it does not fail the restore.
    """
    from one_memory.embedding_spaces import read_live_space
    from one_memory.graph_open_guard import GraphOpenGuard, count_graph_memories
    from one_memory.hy_graph_admin import live_graph_path

    expected = _counts(prepared.manifest, "graph")
    live = read_live_space(prepared.home / DATABASE_NAME)
    graph = live_graph_path(prepared.home, live.graph_dir if live is not None else None)
    if not graph.exists():
        verification.graph = "absent"
        if expected:
            verification.mismatches.append("graph")
        return ()
    if graph_probe is not None:
        outcome = graph_probe(graph)
    else:
        outcome = GraphOpenGuard(prepared.home).open_at_startup(graph, switch_in_progress=False)
    if outcome == "quarantined":
        verification.graph = "quarantined"
        return tuple(users)
    # `expected` is None when the backup could not read its own graph copy:
    # nothing to compare, but the probe above still ran.
    actual = count_graph_memories(graph) if expected is not None else None
    if actual is None:
        # Opened by the probe, but not counted: leave it to the SDK.
        verification.graph = "skipped"
        return ()
    compare("graph", expected, actual, verification.mismatches)
    verification.graph = "verified"
    return ()


def old_backup_note() -> str:
    return "management_database_only_no_memories"
