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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from one_memory.database_access import main_database
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
                    from hy_memory.snapshot import snapshot_local_data_plane
                except ImportError as error:
                    raise MigrationBundleRejected(
                        "sdk_migration_snapshot_unavailable"
                    ) from error
                snapshotter = snapshot_local_data_plane
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
            "sdk_version": HY_MEMORY_VERSION,
            "sdk_namespace": HY_MEMORY_NAMESPACE,
            "management_database": "management/one-memory.sqlite3",
            "sdk_root": f"sdk/{HY_MEMORY_NAMESPACE}",
            "config": "management/config.json" if config.exists() else None,
            "files": files,
            "file_count": len(files),
            "total_bytes": sum(int(item["bytes"]) for item in files),
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
        total_bytes=sum(int(item["bytes"]) for item in files),
        bundle_id=bundle_id,
    )
