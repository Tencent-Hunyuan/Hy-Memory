from __future__ import annotations

import hashlib

import pytest

from hy_memory.snapshot import (
    DataPlaneSnapshotRejected,
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
