"""Public, offline snapshot support for the SDK-owned local data plane."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class DataPlaneSnapshotRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DataPlaneSnapshot:
    source_root: Path
    snapshot_root: Path
    files: tuple[dict[str, object], ...]
    total_bytes: int


@dataclass(frozen=True)
class DataPlaneRestore:
    restored_root: Path
    previous_root: Path | None
    files_restored: int
    total_bytes: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_local_data_plane(
    *,
    source_root: Path,
    target_root: Path,
    writers_stopped: bool,
) -> DataPlaneSnapshot:
    """Copy one quiesced local SDK root and return a verified inventory.

    Remote providers require their own export implementation and must not call
    this local-path contract.
    """
    if not writers_stopped:
        raise DataPlaneSnapshotRejected("data_plane_snapshot_writers_running")
    source = source_root.resolve()
    target = target_root.resolve()
    if source.is_symlink() or not source.is_dir():
        raise DataPlaneSnapshotRejected("data_plane_snapshot_source_invalid")
    if target.exists():
        raise DataPlaneSnapshotRejected("data_plane_snapshot_target_exists")
    # The destination must not become part of the tree we are copying. Otherwise
    # os.walk also visits the newly created snapshot and copies it into itself.
    if source in target.parents:
        raise DataPlaneSnapshotRejected("data_plane_snapshot_target_inside_source")

    try:
        target.mkdir(parents=True)
        for root, directories, files in os.walk(source, followlinks=False):
            root_path = Path(root)
            relative = root_path.relative_to(source)
            target_directory = target / relative
            target_directory.mkdir(parents=True, exist_ok=True)
            for name in directories:
                if (root_path / name).is_symlink():
                    raise DataPlaneSnapshotRejected("data_plane_snapshot_symlink")
            for name in files:
                item = root_path / name
                if item.is_symlink():
                    raise DataPlaneSnapshotRejected("data_plane_snapshot_symlink")
                shutil.copy2(item, target_directory / name)

        inventory: list[dict[str, object]] = []
        for path in sorted(item for item in target.rglob("*") if item.is_file()):
            inventory.append(
                {
                    "path": path.relative_to(target).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        for item in inventory:
            copied = target / str(item["path"])
            if copied.stat().st_size != item["bytes"] or _sha256(copied) != item["sha256"]:
                raise DataPlaneSnapshotRejected("data_plane_snapshot_verify_failed")
    except DataPlaneSnapshotRejected:
        shutil.rmtree(target, ignore_errors=True)
        raise
    except OSError as error:
        shutil.rmtree(target, ignore_errors=True)
        raise DataPlaneSnapshotRejected("data_plane_snapshot_failed") from error

    return DataPlaneSnapshot(
        source_root=source,
        snapshot_root=target,
        files=tuple(inventory),
        total_bytes=sum(int(item["bytes"]) for item in inventory),
    )


def restore_local_data_plane(
    *,
    snapshot_root: Path,
    target_root: Path,
    writers_stopped: bool,
    protective_root: Path | None = None,
) -> DataPlaneRestore:
    """Replace one local SDK root from a verified snapshot with rollback."""
    if not writers_stopped:
        raise DataPlaneSnapshotRejected("data_plane_restore_writers_running")
    source = snapshot_root.resolve()
    target = target_root.resolve()
    protective = (
        protective_root.resolve()
        if protective_root is not None
        else target.parent / f".{target.name}.pre-restore"
    )
    staging = target.parent / f".{target.name}.restore.tmp"
    if target.is_symlink() or protective.exists() or staging.exists():
        raise DataPlaneSnapshotRejected("data_plane_restore_target_unsafe")

    copied = snapshot_local_data_plane(
        source_root=source,
        target_root=staging,
        writers_stopped=True,
    )
    previous: Path | None = None
    try:
        if target.exists():
            target.replace(protective)
            previous = protective
        staging.replace(target)
    except OSError as error:
        if previous is not None and previous.exists() and not target.exists():
            previous.replace(target)
            previous = None
        shutil.rmtree(staging, ignore_errors=True)
        raise DataPlaneSnapshotRejected("data_plane_restore_replace_failed") from error

    return DataPlaneRestore(
        restored_root=target,
        previous_root=previous,
        files_restored=len(copied.files),
        total_bytes=copied.total_bytes,
    )
