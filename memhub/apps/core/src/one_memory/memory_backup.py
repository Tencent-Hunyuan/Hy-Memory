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
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import closing, suppress
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
RESTORE_JOURNAL = ".memhub-restore.json"
PREVIOUS_SUFFIX = ".memhub-previous"

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


#: A rough size of one exported memory line (content, metadata, no vector).
EXPORT_BYTES_PER_MEMORY = 1536


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
    data_dir: Path, *, backend: BackendIdentity, memories: int | None = None
) -> BackupEstimate:
    """What `create_backup` would write: no file is read, only sizes."""
    planned = plan_files(data_dir)
    local = sum(item.bytes for item in planned)
    export = (memories or 0) * EXPORT_BYTES_PER_MEMORY if backend.kind == "remote_vector" else 0
    free: int | None = None
    with suppress(OSError):
        free = shutil.disk_usage(data_dir.resolve()).free
    return BackupEstimate(local + export, len(planned), local, export, free, backend)


def resolve_target(data_dir: Path, target: str) -> Path:
    """The backup directory `target` names, inside the home and not taken yet.

    `backups/x.sqlite3` (the v1 habit) becomes `backups/x.memhub-backup`: a
    v2 backup is a directory, and its name says so.
    """
    home = data_dir.resolve()
    candidate = Path(target)
    resolved = (candidate if candidate.is_absolute() else home / candidate).resolve()
    if not (resolved == home or home in resolved.parents) or resolved == home:
        raise BackupError("backup_target_outside_data_dir")
    name = resolved.name
    if name.endswith(".sqlite3"):
        name = name.removesuffix(".sqlite3")
    if not name.endswith(BACKUP_SUFFIX):
        name = f"{name}{BACKUP_SUFFIX}"
    resolved = resolved.with_name(name)
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

    One line per memory: `{"user_id", "layer"|"scene", "memory"}` where
    `memory` is the SDK's own list item (content, ids, status, times, source).
    Paged per user and per layer, every status, raw turns included, written
    as it is read. hy-memory 2.0.15 has no public read that returns the
    vector, so `embeddings_included` is false: a restore re-embeds with the
    space's model (`docs/contracts/backup-v2.md`).
    """
    _owner_only_directory(target.parent)
    digest = hashlib.sha256()
    counts: dict[str, dict[str, int]] = {}
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as writer:

        def emit(record: dict[str, object]) -> None:
            line = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
            digest.update(line)
            writer.write(line)

        for user_id in users:
            per_user = counts.setdefault(user_id, {})
            for layer in VECTOR_LAYERS:
                per_user[layer] = 0
                offset = 0
                while True:
                    listed = client.list_memories(
                        user_id=user_id,
                        layers=[layer],
                        statuses=list(ALL_STATUSES),
                        limit=_EXPORT_PAGE,
                        offset=offset,
                        order="asc",
                    )
                    vdb = listed.get("vdb") if isinstance(listed, Mapping) else None
                    items = vdb.get("memories") if isinstance(vdb, Mapping) else None
                    if not items:
                        break
                    for item in items:
                        emit({"user_id": user_id, "layer": layer, "memory": item})
                    per_user[layer] += len(items)
                    offset += len(items)
                    if len(items) < _EXPORT_PAGE:
                        break
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
        "embeddings_included": False,
        "counts": counts,
        "sha256": digest.hexdigest(),
    }


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
    home = data_dir.resolve()
    backups = (home / "backups").resolve()
    candidate = Path(source)
    unresolved = candidate if candidate.is_absolute() else home / candidate
    if unresolved.is_symlink():
        raise BackupError("backup_restore_source_outside_home")
    resolved = unresolved.resolve()
    if resolved.parent != backups:
        raise BackupError("backup_restore_source_outside_home")
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


def prepare_restore(
    data_dir: Path,
    source: BackupSource,
    *,
    current: BackendIdentity,
    now: datetime | None = None,
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
        # Converting between a local store and a server needs an SDK write
        # API that keeps ids and vectors (2.0.15 has none): refuse instead of
        # restoring memories this home would not read.
        raise BackupError("backup_restore_backend_mismatch")
    if backend.kind == "remote_vector":
        raise BackupError("backup_restore_remote_sdk_unsupported")
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
        }


Quiesce = Callable[[], Awaitable[Callable[[], None]]]
CountLayers = Callable[[list[str]], Awaitable[dict[str, dict[str, int]] | None]]
ScheduleGraphRebuild = Callable[[tuple[str, ...], bool], None]


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
) -> RestoreResult:
    """Restore a v2 backup: stage, swap, verify the counts, roll back on any failure.

    `quiesce` closes every SDK client of this process and holds the stores
    offline, returning the release; a stopped Runtime needs none. The
    previous data is kept until the counts match, then stays as a protective
    copy (`previous_backup_path`).
    """
    import asyncio

    from one_memory.hy_sdk_migration import vector_store_counts

    started = time.monotonic()
    prepared = await asyncio.to_thread(prepare_restore, data_dir, source, current=current, now=now)
    manifest = prepared.manifest
    listed = manifest.get("users")
    users = [user for user in listed if isinstance(user, str)] if isinstance(listed, list) else []
    verification = Verification()
    # Set before the swap starts: its journal is the first thing it writes,
    # and a rollback from any later point puts the previous data back.
    swapped = False
    graph_rebuild: tuple[str, ...] = ()
    try:
        release = await quiesce()
        try:
            swapped = True
            await asyncio.to_thread(swap_in, prepared)
        finally:
            release()

        expected_vectors = _counts(manifest, "vector_store")
        actual_vectors = await vector_store_counts(prepared.home)
        compare("vector_store", expected_vectors or {}, actual_vectors, verification.mismatches)
        verification.vector_store = "verified"

        if prepared.backend.kind == "remote_graph":
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
    except BaseException as error:
        if swapped:
            release = await quiesce()
            try:
                await asyncio.to_thread(roll_back, prepared.home)
            finally:
                release()
        else:
            discard_prepared(prepared)
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
        schedule_graph_rebuild(graph_rebuild, prepared.backend.kind == "remote_graph")
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
