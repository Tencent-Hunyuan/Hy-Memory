"""Offline snapshot and restore of the HY SDK data plane for the App migration.

Runtime product code must use public ``HyMemoryClient`` methods.  The startup
migration bundle is the isolated exception: all writers are stopped, so it may
copy and restore the SDK-owned local stores as files.
"""

from __future__ import annotations

from pathlib import Path

from hy_memory import (  # type: ignore[import-untyped]
    DataPlaneRestore,
    DataPlaneSnapshot,
    restore_local_data_plane,
    snapshot_local_data_plane,
)


def snapshot_data_plane(
    *,
    source_root: Path,
    target_root: Path,
    writers_stopped: bool,
) -> DataPlaneSnapshot:
    """Snapshot an explicitly quiesced SDK-owned local data plane."""
    return snapshot_local_data_plane(
        source_root=source_root,
        target_root=target_root,
        writers_stopped=writers_stopped,
    )


def restore_data_plane(
    *,
    snapshot_root: Path,
    target_root: Path,
    writers_stopped: bool,
    protective_root: Path | None = None,
) -> DataPlaneRestore:
    """Restore an explicitly quiesced SDK-owned local data plane."""
    return restore_local_data_plane(
        snapshot_root=snapshot_root,
        target_root=target_root,
        writers_stopped=writers_stopped,
        protective_root=protective_root,
    )
