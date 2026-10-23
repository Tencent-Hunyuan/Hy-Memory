from __future__ import annotations

import hashlib

import pytest

from hy_memory.snapshot import (
    DataPlaneSnapshotRejected,
    restore_local_data_plane,
    snapshot_local_data_plane,
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_snapshot_copies_and_verifies_local_sdk_tree(tmp_path):
    source = tmp_path / "source"
    (source / "vector").mkdir(parents=True)
    (source / "vector" / "nodes.bin").write_bytes(b"vector")
    (source / "cache.sqlite3").write_bytes(b"cache")
    target = tmp_path / "snapshot"

    result = snapshot_local_data_plane(
        source_root=source,
        target_root=target,
        writers_stopped=True,
    )

    assert {item["path"] for item in result.files} == {
        "cache.sqlite3",
        "vector/nodes.bin",
    }
    for item in result.files:
        copied = target / item["path"]
        assert copied.stat().st_size == item["bytes"]
        assert _sha256(copied) == item["sha256"]
    assert result.total_bytes == len(b"vector") + len(b"cache")


def test_snapshot_skips_top_level_runtime_logs(tmp_path):
    """Runtime logs sit beside the data plane but are not data.

    Copying them doubled a 15.8 GB migration snapshot, most of it rotated log
    files, and made migrations fail with ENOSPC on an otherwise healthy volume.
    Only the top-level directory is pruned: a business directory that happens to
    be called "logs" deeper in the tree must still be copied.
    """
    source = tmp_path / "source"
    (source / "logs").mkdir(parents=True)
    (source / "logs" / "hy_memory.log").write_bytes(b"x" * 4096)
    (source / "logs" / "hy_memory.log.2026-10-19").write_bytes(b"y" * 4096)
    (source / "vector" / "logs").mkdir(parents=True)
    (source / "vector" / "logs" / "keep.bin").write_bytes(b"keep")
    (source / "cache.sqlite3").write_bytes(b"cache")
    target = tmp_path / "snapshot"

    result = snapshot_local_data_plane(
        source_root=source,
        target_root=target,
        writers_stopped=True,
    )

    assert {item["path"] for item in result.files} == {
        "cache.sqlite3",
        "vector/logs/keep.bin",
    }
    assert not (target / "logs").exists()
    assert result.total_bytes == len(b"keep") + len(b"cache")


def test_snapshot_refuses_active_writers(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(DataPlaneSnapshotRejected) as caught:
        snapshot_local_data_plane(
            source_root=source,
            target_root=tmp_path / "snapshot",
            writers_stopped=False,
        )
    assert caught.value.code == "data_plane_snapshot_writers_running"


def test_snapshot_rejects_symlink_and_removes_partial_target(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    try:
        (source / "linked").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    target = tmp_path / "snapshot"

    with pytest.raises(DataPlaneSnapshotRejected) as caught:
        snapshot_local_data_plane(
            source_root=source,
            target_root=target,
            writers_stopped=True,
        )
    assert caught.value.code == "data_plane_snapshot_symlink"
    assert not target.exists()


def test_snapshot_target_must_not_exist(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "snapshot"
    source.mkdir()
    target.mkdir()
    with pytest.raises(DataPlaneSnapshotRejected) as caught:
        snapshot_local_data_plane(
            source_root=source,
            target_root=target,
            writers_stopped=True,
        )
    assert caught.value.code == "data_plane_snapshot_target_exists"


def test_restore_replaces_data_plane_and_keeps_protective_copy(tmp_path):
    snapshot = tmp_path / "snapshot"
    target = tmp_path / "live"
    (snapshot / "vector").mkdir(parents=True)
    (snapshot / "vector" / "nodes.bin").write_bytes(b"new")
    (target / "vector").mkdir(parents=True)
    (target / "vector" / "nodes.bin").write_bytes(b"old")
    protective = tmp_path / "previous"

    result = restore_local_data_plane(
        snapshot_root=snapshot,
        target_root=target,
        protective_root=protective,
        writers_stopped=True,
    )

    assert (target / "vector" / "nodes.bin").read_bytes() == b"new"
    assert (protective / "vector" / "nodes.bin").read_bytes() == b"old"
    assert result.previous_root == protective
    assert result.files_restored == 1


def test_restore_refuses_active_writers_without_touching_live_data(tmp_path):
    snapshot = tmp_path / "snapshot"
    target = tmp_path / "live"
    snapshot.mkdir()
    target.mkdir()
    (target / "value").write_text("old", encoding="utf-8")

    with pytest.raises(DataPlaneSnapshotRejected) as caught:
        restore_local_data_plane(
            snapshot_root=snapshot,
            target_root=target,
            writers_stopped=False,
        )
    assert caught.value.code == "data_plane_restore_writers_running"
    assert (target / "value").read_text(encoding="utf-8") == "old"
