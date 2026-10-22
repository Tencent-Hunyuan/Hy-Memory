"""Offline full-home bundle for the SDK-authority migration.

This is deliberately separate from the historical SQLite-only product backup.
The caller must stop Runtime/Core writers before invoking it.  The bundle is
published by one directory rename only after every copied file is hashed and
verified.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from one_memory.database_access import close_main_databases, main_database
from one_memory.hy_runtime_config import (
    HY_MEMORY_NAMESPACE,
    HY_MEMORY_VERSION,
)


class MigrationBundleRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MigrationBundleResult:
    bundle_path: Path
    manifest_path: Path
    file_count: int
    total_bytes: int
    bundle_id: str


@dataclass(frozen=True)
class MigrationBundleRestoreResult:
    bundle_id: str
    restored_files: int
    restored_bytes: int
    previous_database_path: Path
    previous_sdk_root: Path | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return files


def create_sdk_migration_bundle(
    *,
    data_dir: Path,
    bundle_id: str,
    runtime_build: str,
    runtime_stopped: bool,
    now: datetime | None = None,
    snapshotter: Callable[..., object] | None = None,
) -> MigrationBundleResult:
    """Create and verify one management + SDK data-plane migration bundle."""
    if not runtime_stopped:
        raise MigrationBundleRejected("sdk_migration_bundle_runtime_running")
    if (
        not bundle_id
        or len(bundle_id) > 128
        or bundle_id in {".", ".."}
        or "/" in bundle_id
        or "\\" in bundle_id
    ):
        raise MigrationBundleRejected("sdk_migration_bundle_id_invalid")
    home = data_dir.resolve()
    database = home / "one-memory.sqlite3"
    if not database.is_file() or database.is_symlink():
        raise MigrationBundleRejected("sdk_migration_bundle_database_missing")

    backups = home / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    target = backups / f"{bundle_id}.sdk-bundle"
    staging = backups / f".{bundle_id}.sdk-bundle.tmp"
    if target.exists() or staging.exists():
        raise MigrationBundleRejected("sdk_migration_bundle_exists")

    created_at = (now or datetime.now(UTC)).isoformat()
    try:
        (staging / "management").mkdir(parents=True)
        main_database(database).back_up(staging / "management" / database.name)

        sdk_root = home / "hy-memory" / HY_MEMORY_NAMESPACE
        if sdk_root.exists():
            if not sdk_root.is_dir():
                raise MigrationBundleRejected("sdk_migration_bundle_sdk_root_invalid")
            if snapshotter is None:
                try:
                    from one_memory.hy_memory_bridge import snapshot_hy_data_plane
                except ImportError as error:
                    raise MigrationBundleRejected("sdk_migration_snapshot_unavailable") from error
                snapshotter = snapshot_hy_data_plane
            try:
                snapshotter(
                    source_root=sdk_root,
                    target_root=staging / "sdk" / HY_MEMORY_NAMESPACE,
                    writers_stopped=True,
                )
            except Exception as error:
                sdk_code = getattr(error, "code", "")
                code = (
                    "sdk_migration_bundle_symlink"
                    if sdk_code == "data_plane_snapshot_symlink"
                    else "sdk_migration_bundle_sdk_snapshot_failed"
                )
                raise MigrationBundleRejected(code) from error
        else:
            (staging / "sdk" / HY_MEMORY_NAMESPACE).mkdir(parents=True)

        config = home / "config.json"
        if config.exists():
            if not config.is_file() or config.is_symlink():
                raise MigrationBundleRejected("sdk_migration_bundle_config_invalid")
            (staging / "management").mkdir(parents=True, exist_ok=True)
            shutil.copy2(config, staging / "management" / config.name)

        files = _inventory(staging)
        manifest = {
            "schema_version": 1,
            "bundle_id": bundle_id,
            "created_at": created_at,
            "runtime_build": runtime_build,
            "snapshot_sdk_version": HY_MEMORY_VERSION,
            "sdk_namespace": HY_MEMORY_NAMESPACE,
            "management_database": "management/one-memory.sqlite3",
            "sdk_root": f"sdk/{HY_MEMORY_NAMESPACE}",
            "config": "management/config.json" if config.exists() else None,
            "files": files,
            "file_count": len(files),
            "total_bytes": sum(int(str(item["bytes"])) for item in files),
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        for item in files:
            copied = staging / str(item["path"])
            if copied.stat().st_size != item["bytes"] or _sha256(copied) != item["sha256"]:
                raise MigrationBundleRejected("sdk_migration_bundle_verify_failed")
        staging.replace(target)
    except MigrationBundleRejected:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (OSError, ValueError, RuntimeError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise MigrationBundleRejected("sdk_migration_bundle_create_failed") from error

    return MigrationBundleResult(
        bundle_path=target,
        manifest_path=target / "manifest.json",
        file_count=len(files),
        total_bytes=sum(int(str(item["bytes"])) for item in files),
        bundle_id=bundle_id,
    )


def _verified_manifest(bundle: Path) -> dict[str, object]:
    manifest_path = bundle / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid")
    total = 0
    for item in files:
        if not isinstance(item, dict):
            raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative:
            raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid")
        candidate = bundle / path
        try:
            size = int(item["bytes"])
            digest = str(item["sha256"])
        except (KeyError, TypeError, ValueError) as error:
            raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid") from error
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or candidate.stat().st_size != size
            or _sha256(candidate) != digest
        ):
            raise MigrationBundleRejected("sdk_migration_bundle_verify_failed")
        total += size
    try:
        file_count = int(manifest.get("file_count", -1))
        total_bytes = int(manifest.get("total_bytes", -1))
    except (TypeError, ValueError) as error:
        raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid") from error
    if file_count != len(files) or total_bytes != total:
        raise MigrationBundleRejected("sdk_migration_bundle_verify_failed")
    return manifest


def inspect_sdk_migration_bundle(
    *,
    data_dir: Path,
    bundle_id: str,
) -> dict[str, object]:
    home = data_dir.resolve()
    backups = (home / "backups").resolve()
    if not bundle_id or "/" in bundle_id or "\\" in bundle_id or bundle_id in {".", ".."}:
        raise MigrationBundleRejected("sdk_migration_bundle_id_invalid")
    bundle = (backups / f"{bundle_id}.sdk-bundle").resolve()
    if bundle.parent != backups or bundle.is_symlink() or not bundle.is_dir():
        raise MigrationBundleRejected("sdk_migration_bundle_source_invalid")
    manifest = _verified_manifest(bundle)
    if manifest.get("bundle_id") != bundle_id:
        raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid")
    return manifest


def restore_sdk_migration_bundle(
    *,
    data_dir: Path,
    source: str,
    runtime_stopped: bool,
    now: datetime | None = None,
    restore_data_plane: Callable[..., object] | None = None,
) -> MigrationBundleRestoreResult:
    """Restore a verified management + SDK bundle while all writers are stopped."""
    if not runtime_stopped:
        raise MigrationBundleRejected("sdk_migration_bundle_runtime_running")
    home = data_dir.resolve()
    backups = (home / "backups").resolve()
    candidate = Path(source)
    bundle = (candidate if candidate.is_absolute() else home / candidate).resolve()
    if (
        bundle.is_symlink()
        or bundle.parent != backups
        or not bundle.is_dir()
        or not bundle.name.endswith(".sdk-bundle")
    ):
        raise MigrationBundleRejected("sdk_migration_bundle_source_invalid")
    manifest = _verified_manifest(bundle)
    database_source = bundle / "management" / "one-memory.sqlite3"
    sdk_source = bundle / "sdk" / HY_MEMORY_NAMESPACE
    if not database_source.is_file() or not sdk_source.is_dir():
        raise MigrationBundleRejected("sdk_migration_bundle_manifest_invalid")

    if restore_data_plane is None:
        try:
            from one_memory.hy_memory_bridge import restore_hy_data_plane
        except ImportError as error:
            raise MigrationBundleRejected("sdk_migration_restore_unavailable") from error
        restore_data_plane = restore_hy_data_plane

    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    database_live = home / "one-memory.sqlite3"
    database_previous = backups / f"pre-sdk-cutover-{stamp}.sqlite3"
    sdk_live = home / "hy-memory" / HY_MEMORY_NAMESPACE
    sdk_previous = backups / f"pre-sdk-cutover-{stamp}.sdk-data"

    try:
        main_database(database_live).back_up(database_previous)
        close_main_databases()
        main_database(database_live).restore_from(
            database_source,
            required_tables=frozenset({"schema_migrations"}),
        )
        close_main_databases()
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as error:
        close_main_databases()
        raise MigrationBundleRejected("sdk_migration_bundle_database_restore_failed") from error

    try:
        restored = restore_data_plane(
            snapshot_root=sdk_source,
            target_root=sdk_live,
            protective_root=sdk_previous,
            writers_stopped=True,
        )
    except Exception as error:
        try:
            close_main_databases()
            main_database(database_live).restore_from(
                database_previous,
                required_tables=frozenset({"schema_migrations"}),
            )
            close_main_databases()
        except Exception as rollback_error:
            raise MigrationBundleRejected(
                "sdk_migration_bundle_restore_rollback_failed"
            ) from rollback_error
        code = getattr(error, "code", "")
        raise MigrationBundleRejected(
            "sdk_migration_restore_unavailable"
            if code == "data_plane_restore_unavailable"
            else "sdk_migration_bundle_sdk_restore_failed"
        ) from error

    return MigrationBundleRestoreResult(
        bundle_id=str(manifest["bundle_id"]),
        restored_files=int(str(manifest["file_count"])),
        restored_bytes=int(str(manifest["total_bytes"])),
        previous_database_path=database_previous,
        previous_sdk_root=getattr(restored, "previous_root", None),
    )
