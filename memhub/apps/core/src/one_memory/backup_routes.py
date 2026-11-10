"""HTTP surface of backups: estimate, create, restore (`docs/contracts/backup-v2.md`).

`POST /api/v1/backup` writes a v2 backup (`memory_backup`): the management
database and every memory. SDK writes are paused for the copy the way an
embedding switch pauses them (`writes_open`), every SDK client is closed and
the stores are held offline while files are copied, and writes resume on
every exit path. `GET /api/v1/backup/estimate` reports the size first.

`POST /api/v1/backup/restore` is only served by a non-runtime (maintenance)
Core. A v1 backup (one `.sqlite3` file) restores the management database
exactly as before and says that it holds no memories; a v2 backup restores
everything, verifies the counts its manifest recorded and rolls back on a
mismatch. Errors keep Core's shape `{"detail": code}`.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.params import Depends
from pydantic import BaseModel, ConfigDict, Field

from one_memory.diagnostic_log import diagnostic_event
from one_memory.embedding_spaces import EmbeddingSpace
from one_memory.graph_open_guard import count_graph_memories
from one_memory.hy_graph_admin import live_graph_path
from one_memory.memory_backup import (
    EXPORT_PATH,
    SDK_PREFIX,
    BackendIdentity,
    BackupError,
    base_manifest,
    copy_files,
    count_layers,
    estimate,
    finish_backup,
    locate,
    old_backup_note,
    plan_files,
    resolve_target,
    run_restore,
    staging_for,
    write_export,
)
from one_memory.storage_backends import StorageBackends

#: How long a backup waits for an evolve / mode change to finish, and for the
#: write drain to put its running job down, before it gives up.
LIFECYCLE_WAIT_S = 120.0
DRAIN_WAIT_S = 120.0


@dataclass
class BackupHooks:
    """What the routes need from the running Core; set on `app.state.backup_hooks`."""

    data_dir: Path
    manage_runtime: bool
    storage: Callable[[], StorageBackends]
    known_users: Callable[[], tuple[str, ...]]
    read_client: Callable[[], Awaitable[Any]]
    memory_mode: Callable[[], str]
    live_space: Callable[[], EmbeddingSpace | None]
    spaces: Callable[[], list[EmbeddingSpace]]
    #: False while an embedding switch or merge holds writes.
    switch_writes_open: Callable[[], bool]
    lifecycle_lock: asyncio.Lock
    set_pause: Callable[[bool], None]
    #: No model write runs and none holds a client (the switch's own check).
    drained: Callable[[], Awaitable[bool]] | None
    #: Close every SDK client and hold the stores offline; returns the release.
    quiesce: Callable[[], Awaitable[Callable[[], None]]] | None
    #: After a restore: per-user layer counts through a fresh SDK client.
    count_layers_after_restore: Callable[[list[str]], Awaitable[dict[str, dict[str, int]] | None]]
    schedule_graph_rebuild: Callable[[tuple[str, ...], bool], None]
    after_restore: Callable[[], None]
    runtime_build: str | None = None


class BackupRequest(BaseModel):
    target: str = Field(min_length=1, max_length=4096)


class BackupResult(BaseModel):
    target: str
    bytes: int
    files: int
    schema_version: Literal[2] = 2
    memories_included: Literal[True] = True
    #: How long SDK writes were paused, and how long the file copy took.
    pause_ms: int
    copy_ms: int
    backend: dict[str, object]
    users: list[str]
    counts: dict[str, object]


class BackupEstimate(BaseModel):
    #: The directory a backup would be written to, when one was named.
    target: str | None = None
    bytes: int
    files: int
    local_bytes: int
    export_bytes: int
    free_bytes: int | None
    memories_included: Literal[True] = True
    backend: dict[str, object]


class BackupRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=4096)


class BackupRestoreResponse(BaseModel):
    state: Literal["restored"]
    restored_bytes: int
    previous_backup_path: str
    source_schema_version: int
    #: 1: an old backup (management database only); 2: memories included.
    backup_schema_version: Literal[1, 2] = 1
    memories_included: bool = False
    restored_files: int | None = None
    backend: dict[str, object] | None = None
    verification: dict[str, object] | None = None
    graph_rebuild_scheduled: bool = False
    duration_ms: int | None = None
    #: `management_database_only_no_memories` for an old backup.
    note: str | None = None


def _hooks(request: Request) -> BackupHooks:
    hooks = getattr(request.app.state, "backup_hooks", None)
    if not isinstance(hooks, BackupHooks):
        raise HTTPException(status_code=503, detail="core_not_ready")
    return hooks


_STATUS = {
    "backup_target_outside_data_dir": 422,
    "backup_target_exists": 409,
    "backup_target_invalid": 422,
    "backup_in_progress": 409,
    "backup_embedding_maintenance_in_progress": 409,
    "backup_lifecycle_busy": 409,
    "backup_writes_busy": 409,
    "backup_clients_busy": 409,
    "backup_disk_space_insufficient": 507,
    "backup_sdk_unavailable": 503,
}


def _refuse(code: str, *, restore: bool = False) -> HTTPException:
    status = _STATUS.get(code, 422 if restore else 500)
    if code == "backup_restore_runtime_running":
        status = 409
    return HTTPException(status_code=status, detail=code)


@asynccontextmanager
async def write_pause(hooks: BackupHooks) -> AsyncIterator[float]:
    """Pause SDK writes until the block ends, on every exit path; yields the start."""
    if not hooks.switch_writes_open():
        raise BackupError("backup_embedding_maintenance_in_progress")
    started = time.monotonic()
    hooks.set_pause(True)
    try:
        try:
            await asyncio.wait_for(hooks.lifecycle_lock.acquire(), LIFECYCLE_WAIT_S)
        except TimeoutError:
            raise BackupError("backup_lifecycle_busy") from None
        try:
            if hooks.drained is not None:
                deadline = time.monotonic() + DRAIN_WAIT_S
                while not await hooks.drained():
                    if time.monotonic() >= deadline:
                        raise BackupError("backup_writes_busy")
                    await asyncio.sleep(0.2)
            yield started
        finally:
            hooks.lifecycle_lock.release()
    finally:
        hooks.set_pause(False)


async def quiesced(hooks: BackupHooks) -> Callable[[], None]:
    if hooks.quiesce is None:
        return lambda: None
    try:
        return await hooks.quiesce()
    except RuntimeError as error:
        raise BackupError("backup_clients_busy") from error


def _space_document(space: EmbeddingSpace) -> dict[str, object]:
    return space.model_dump(mode="json")


async def create(hooks: BackupHooks, target_name: str, *, lock: asyncio.Lock) -> BackupResult:
    if lock.locked():
        raise BackupError("backup_in_progress")
    async with lock:
        return await _create(hooks, target_name)


async def _create(hooks: BackupHooks, target_name: str) -> BackupResult:
    data_dir = hooks.data_dir
    target = resolve_target(data_dir, target_name)
    backend = BackendIdentity.of(hooks.storage())
    planned_size = await asyncio.to_thread(estimate, data_dir, backend=backend)
    if planned_size.free_bytes is not None and planned_size.free_bytes < planned_size.bytes * 1.05:
        raise BackupError("backup_disk_space_insufficient")
    staging = staging_for(target)
    shutil.rmtree(staging, ignore_errors=True)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    staging.mkdir(mode=0o700)
    created_at = datetime.now(UTC)
    try:
        async with write_pause(hooks) as paused_at:
            users = list(await asyncio.to_thread(hooks.known_users))
            # In a task of their own: a read client is held for the task
            # that asked for it (`ActiveProfileReadHandle`), and this one
            # goes on to close every client.
            layer_counts, export = await asyncio.create_task(
                _read_memories(hooks, backend, users, staging)
            )
            vector_counts: dict[str, object] = {}
            if backend.vector == "chroma":
                from one_memory.hy_sdk_migration import vector_store_counts

                vector_counts = dict(await vector_store_counts(data_dir))
            planned = await asyncio.to_thread(plan_files, data_dir)
            release = await quiesced(hooks)
            copy_started = time.monotonic()
            try:
                inventory = await asyncio.to_thread(copy_files, planned, staging)
            finally:
                release()
            copy_ms = int((time.monotonic() - copy_started) * 1000)
            live = hooks.live_space()
            spaces = hooks.spaces()
            memory_mode = hooks.memory_mode()
        pause_ms = int((time.monotonic() - paused_at) * 1000)

        graph_counts: dict[str, dict[str, int]] | None = None
        if backend.graph == "kuzu":
            # Counted on the copy, in a read-only child: the live graph is
            # the SDK's again by now.
            graph_name = live_graph_path(data_dir, live.graph_dir if live else None).name
            graph_counts = await asyncio.to_thread(
                count_graph_memories, staging / SDK_PREFIX / graph_name
            )
        if export is not None:
            path = staging / EXPORT_PATH
            inventory.append(
                {"path": EXPORT_PATH, "bytes": path.stat().st_size, "sha256": export["sha256"]}
            )
        manifest = base_manifest(
            created_at=created_at,
            backend=backend,
            live_space=_space_document(live) if live is not None else None,
            spaces=[_space_document(space) for space in spaces],
            memory_mode=memory_mode,
            users=users,
            runtime_build=hooks.runtime_build,
        )
        counts: dict[str, object] = {
            "layers": layer_counts,
            "vector_store": vector_counts if backend.vector == "chroma" else None,
            "graph": graph_counts,
            "export": export["counts"] if export is not None else None,
        }
        manifest["counts"] = counts
        manifest["export"] = (
            {key: value for key, value in export.items() if key != "counts"}
            if export is not None
            else None
        )
        manifest["pause_ms"] = pause_ms
        manifest["copy_ms"] = copy_ms
        result = await asyncio.to_thread(
            finish_backup, staging=staging, target=target, inventory=inventory, manifest=manifest
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    diagnostic_event(
        level="info",
        component="core.backup",
        event="backup.created",
        outcome="succeeded",
        duration_ms=pause_ms,
        details={
            "bytes": result.bytes,
            "files": result.files,
            "copy_ms": copy_ms,
            "backend": backend.kind,
            "users": len(users),
        },
    )
    document = result.document()
    return BackupResult(
        target=str(document["target"]),
        bytes=result.bytes,
        files=result.files,
        pause_ms=pause_ms,
        copy_ms=copy_ms,
        backend=backend.document(),
        users=users,
        counts=counts,
    )


async def _read_memories(
    hooks: BackupHooks, backend: BackendIdentity, users: list[str], staging: Path
) -> tuple[dict[str, dict[str, int]] | None, dict[str, object] | None]:
    """The SDK's per-layer counts and, on a vector server, the export."""
    try:
        client = await hooks.read_client()
        layer_counts = await asyncio.to_thread(count_layers, client, users)
    except Exception:  # noqa: BLE001 - the local stores are still copied
        if backend.kind == "remote_vector":
            raise BackupError("backup_sdk_unavailable") from None
        return None, None
    export: dict[str, object] | None = None
    if backend.kind == "remote_vector":
        export = await asyncio.to_thread(write_export, client, users, staging / EXPORT_PATH)
    return layer_counts, export


async def restore(hooks: BackupHooks, source_name: str) -> BackupRestoreResponse:
    if hooks.manage_runtime:
        raise BackupError("backup_restore_runtime_running")
    source = await asyncio.to_thread(locate, hooks.data_dir, source_name)
    if source.schema_version == 1:
        from one_memory.managed_restore import RestoreRejected, restore_managed_backup

        try:
            result = await asyncio.to_thread(
                restore_managed_backup, data_dir=hooks.data_dir, source=str(source.path)
            )
        except RestoreRejected as error:
            raise BackupError(error.code) from error
        hooks.after_restore()
        return BackupRestoreResponse(
            state="restored",
            restored_bytes=result.restored_bytes,
            previous_backup_path=str(result.previous_backup_path),
            source_schema_version=result.source_schema_version,
            backup_schema_version=1,
            memories_included=False,
            note=old_backup_note(),
        )
    current = BackendIdentity.of(hooks.storage())
    async with write_pause(hooks):
        try:
            restored = await run_restore(
                hooks.data_dir,
                source,
                current=current,
                quiesce=lambda: quiesced(hooks),
                count_layers_after=hooks.count_layers_after_restore,
                schedule_graph_rebuild=hooks.schedule_graph_rebuild,
            )
        finally:
            hooks.after_restore()
    document = restored.document()
    diagnostic_event(
        level="info",
        component="core.backup",
        event="backup.restored",
        outcome="succeeded",
        duration_ms=restored.duration_ms,
        details={
            "bytes": restored.bytes,
            "files": restored.files,
            "graph": restored.verification.graph,
            "layers": restored.verification.layers,
        },
    )
    return BackupRestoreResponse(
        state="restored",
        restored_bytes=restored.bytes,
        previous_backup_path=str(restored.previous),
        source_schema_version=restored.source_schema_version,
        backup_schema_version=2,
        memories_included=True,
        restored_files=restored.files,
        backend=restored.backend.document(),
        verification=restored.verification.document(),
        graph_rebuild_scheduled=bool(document["graph_rebuild_scheduled"]),
        duration_ms=restored.duration_ms,
    )


def backup_router(dependencies: Sequence[Depends]) -> APIRouter:
    router = APIRouter(dependencies=dependencies)
    lock = asyncio.Lock()

    @router.get("/api/v1/backup/estimate", response_model=BackupEstimate)
    async def backup_estimate(request: Request, target: str | None = None) -> BackupEstimate:
        """How large a backup would be (memories included) and the free space.

        With `target`, the path `POST /api/v1/backup` would write is checked
        and named too, so a preview refuses exactly what the backup would.
        """
        hooks = _hooks(request)
        resolved: str | None = None
        if target is not None:
            try:
                resolved = str(resolve_target(hooks.data_dir, target))
            except BackupError as error:
                raise _refuse(error.code) from error
        backend = BackendIdentity.of(hooks.storage())
        memories: int | None = None
        if backend.kind == "remote_vector":
            memories = await _memory_total(hooks)
        try:
            planned = await asyncio.to_thread(
                estimate, hooks.data_dir, backend=backend, memories=memories
            )
        except BackupError as error:
            raise _refuse(error.code) from error
        return BackupEstimate(target=resolved, **planned.document())  # type: ignore[arg-type]

    @router.post("/api/v1/backup", response_model=BackupResult)
    async def create_backup(request: Request, body: BackupRequest) -> BackupResult:
        """Write a v2 backup, memories included, at `target` inside the data dir.

        A target outside the data directory is refused: the CLI can name any
        path, and an unrestricted server-side write would turn a read-only
        client into a file-write primitive.
        """
        try:
            return await create(_hooks(request), body.target, lock=lock)
        except BackupError as error:
            raise _refuse(error.code) from error

    @router.post("/api/v1/backup/restore", response_model=BackupRestoreResponse)
    async def restore_backup(request: Request, body: BackupRestoreRequest) -> BackupRestoreResponse:
        """Restore a managed backup; only a non-runtime maintenance Core may do so."""
        hooks = _hooks(request)
        if hooks.manage_runtime:
            raise HTTPException(status_code=409, detail="backup_restore_runtime_running")
        try:
            return await restore(hooks, body.source)
        except BackupError as error:
            raise _refuse(error.code, restore=True) from error

    return router


async def _memory_total(hooks: BackupHooks) -> int | None:
    """Every memory the export would write (raw turns included), for its size."""

    async def total() -> int:
        client = await hooks.read_client()
        users = await asyncio.to_thread(hooks.known_users)
        counts = await asyncio.to_thread(count_layers, client, users)
        return sum(sum(layers.values()) for layers in counts.values())

    try:
        # In a task: a read client is held until the asking task ends.
        return await asyncio.create_task(total())
    except Exception:  # noqa: BLE001 - an estimate without the export part
        return None
