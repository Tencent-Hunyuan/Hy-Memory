from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime

import pytest

from one_memory.database_access import close_main_databases, main_database
from one_memory.sdk_migration_bundle import (
    MigrationBundleRejected,
    create_sdk_migration_bundle,
    inspect_sdk_migration_bundle,
    restore_sdk_migration_bundle,
)


def _seed_home(home):
    database = home / "one-memory.sqlite3"
    with main_database(database).write() as connection, connection:
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (55, 'sdk_data_plane_migration', 'now')"
        )
        connection.execute("CREATE TABLE durable (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        connection.execute("INSERT INTO durable VALUES ('one', 'management')")
    close_main_databases()

    sdk = home / "hy-memory" / "governed-v2"
    (sdk / "vector").mkdir(parents=True)
    (sdk / "vector" / "nodes.bin").write_bytes(b"sdk-vector")
    (sdk / "cache.sqlite3").write_bytes(b"sdk-cache")
    (home / "config.json").write_text('{"sync_enabled":true}\n', encoding="utf-8")


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _SnapshotRejected(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _snapshotter(*, source_root, target_root, writers_stopped):
    assert writers_stopped
    if any(path.is_symlink() for path in source_root.rglob("*")):
        raise _SnapshotRejected("data_plane_snapshot_symlink")
    shutil.copytree(source_root, target_root)


class _RestoreResult:
    def __init__(self, previous_root):
        self.previous_root = previous_root


def _restore_data_plane(
    *,
    snapshot_root,
    target_root,
    protective_root,
    writers_stopped,
):
    assert writers_stopped
    previous = None
    if target_root.exists():
        target_root.replace(protective_root)
        previous = protective_root
    shutil.copytree(snapshot_root, target_root)
    return _RestoreResult(previous)


def test_bundle_contains_management_sdk_config_and_verified_manifest(tmp_path):
    _seed_home(tmp_path)
    result = create_sdk_migration_bundle(
        data_dir=tmp_path,
        bundle_id="migration-1",
        runtime_build="build-13",
        runtime_stopped=True,
        now=datetime(2026, 10, 21, tzinfo=UTC),
        snapshotter=_snapshotter,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["bundle_id"] == "migration-1"
    assert manifest["sdk_namespace"] == "governed-v2"
    assert manifest["runtime_build"] == "build-13"
    assert manifest["snapshot_sdk_version"] == "2.0.5.dev2"
    paths = {item["path"]: item for item in manifest["files"]}
    assert {
        "management/one-memory.sqlite3",
        "management/config.json",
        "sdk/governed-v2/vector/nodes.bin",
        "sdk/governed-v2/cache.sqlite3",
    } <= set(paths)
    for relative, item in paths.items():
        copied = result.bundle_path / relative
        assert copied.stat().st_size == item["bytes"]
        assert _sha256(copied) == item["sha256"]
    assert result.file_count == manifest["file_count"]
    assert result.total_bytes == manifest["total_bytes"]
    assert inspect_sdk_migration_bundle(
        data_dir=tmp_path,
        bundle_id="migration-1",
    ) == manifest


def test_bundle_refuses_live_runtime(tmp_path):
    _seed_home(tmp_path)
    with pytest.raises(MigrationBundleRejected) as caught:
        create_sdk_migration_bundle(
            data_dir=tmp_path,
            bundle_id="migration-live",
            runtime_build="build-13",
            runtime_stopped=False,
        )
    assert caught.value.code == "sdk_migration_bundle_runtime_running"
    assert not (tmp_path / "backups" / "migration-live.sdk-bundle").exists()


def test_bundle_rejects_sdk_symlinks_without_publishing_partial_output(tmp_path):
    _seed_home(tmp_path)
    target = tmp_path / "outside"
    target.write_text("outside", encoding="utf-8")
    link = tmp_path / "hy-memory" / "governed-v2" / "linked"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(MigrationBundleRejected) as caught:
        create_sdk_migration_bundle(
            data_dir=tmp_path,
            bundle_id="migration-link",
            runtime_build="build-13",
            runtime_stopped=True,
            snapshotter=_snapshotter,
        )
    assert caught.value.code == "sdk_migration_bundle_symlink"
    assert not (tmp_path / "backups" / "migration-link.sdk-bundle").exists()
    assert not (tmp_path / "backups" / ".migration-link.sdk-bundle.tmp").exists()


@pytest.mark.parametrize("bundle_id", ("", ".", "..", "../escape", "nested/path"))
def test_bundle_id_cannot_escape_managed_backups(tmp_path, bundle_id):
    _seed_home(tmp_path)
    with pytest.raises(MigrationBundleRejected) as caught:
        create_sdk_migration_bundle(
            data_dir=tmp_path,
            bundle_id=bundle_id,
            runtime_build="build-13",
            runtime_stopped=True,
        )
    assert caught.value.code == "sdk_migration_bundle_id_invalid"


def test_restore_bundle_replaces_management_and_sdk_together(tmp_path):
    _seed_home(tmp_path)
    create_sdk_migration_bundle(
        data_dir=tmp_path,
        bundle_id="migration-restore",
        runtime_build="build-13",
        runtime_stopped=True,
        snapshotter=_snapshotter,
    )

    database = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(database) as connection, connection:
        connection.execute("UPDATE durable SET body='newer-management' WHERE id='one'")
    sdk_file = tmp_path / "hy-memory" / "governed-v2" / "vector" / "nodes.bin"
    sdk_file.write_bytes(b"newer-sdk")

    result = restore_sdk_migration_bundle(
        data_dir=tmp_path,
        source="backups/migration-restore.sdk-bundle",
        runtime_stopped=True,
        restore_data_plane=_restore_data_plane,
    )

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT body FROM durable").fetchone()[0] == "management"
    assert sdk_file.read_bytes() == b"sdk-vector"
    assert result.bundle_id == "migration-restore"
    assert result.previous_database_path.is_file()
    assert result.previous_sdk_root is not None


def test_restore_rejects_tampered_bundle_before_touching_live_home(tmp_path):
    _seed_home(tmp_path)
    created = create_sdk_migration_bundle(
        data_dir=tmp_path,
        bundle_id="migration-tampered",
        runtime_build="build-13",
        runtime_stopped=True,
        snapshotter=_snapshotter,
    )
    copied = created.bundle_path / "sdk" / "governed-v2" / "vector" / "nodes.bin"
    copied.write_bytes(b"tampered")

    with pytest.raises(MigrationBundleRejected) as caught:
        restore_sdk_migration_bundle(
            data_dir=tmp_path,
            source="backups/migration-tampered.sdk-bundle",
            runtime_stopped=True,
            restore_data_plane=_restore_data_plane,
        )
    assert caught.value.code == "sdk_migration_bundle_verify_failed"
    live = tmp_path / "hy-memory" / "governed-v2" / "vector" / "nodes.bin"
    assert live.read_bytes() == b"sdk-vector"
