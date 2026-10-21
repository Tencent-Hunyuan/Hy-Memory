"""Offline, resumable backfill runner for SDK memory authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from one_memory.sdk_data_plane_migration import (
    MigrationProgress,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_migration_inventory import (
    SdkMigrationBridge,
    execute_memory_migration_plan,
    execute_raw_migration_plan,
    read_legacy_memory_inventory,
    read_legacy_raw_inventory,
    read_sdk_memory_inventory,
    read_sdk_raw_inventory,
    record_memory_migration_plan,
    record_raw_migration_plan,
)


@dataclass(frozen=True)
class BackfillResult:
    migration_id: str
    state: str
    phase: str
    counts: dict[str, int]
    memory_items: int
    raw_items: int


async def run_sdk_data_plane_backfill(
    *,
    database_path: Path,
    bridge: SdkMigrationBridge,
    migration_id: str,
    bundle_id: str,
    source_runtime_build: str,
    target_sdk_version: str,
    source_schema_version: int,
    namespace_fingerprint: str,
    backend_fingerprint: str,
) -> BackfillResult:
    """Backfill both lanes but never commit the authority cutover marker."""
    store = SdkDataPlaneMigrationStore(database_path)
    store.start(
        migration_id=migration_id,
        source_runtime_build=source_runtime_build,
        target_sdk_version=target_sdk_version,
        source_schema_version=source_schema_version,
        namespace_fingerprint=namespace_fingerprint,
        backend_fingerprint=backend_fingerprint,
        backup_bundle_id=bundle_id,
    )
    store.set_phase(migration_id, phase="inventory", state="running")

    legacy_memories = read_legacy_memory_inventory(database_path)
    sdk_memories = await read_sdk_memory_inventory(bridge)
    memory_decisions = record_memory_migration_plan(
        store,
        migration_id=migration_id,
        legacy=legacy_memories,
        sdk=sdk_memories,
    )
    legacy_raw = read_legacy_raw_inventory(database_path)
    sdk_raw = await read_sdk_raw_inventory(bridge)
    raw_decisions = record_raw_migration_plan(
        store,
        migration_id=migration_id,
        legacy=legacy_raw,
        sdk=sdk_raw,
    )

    store.set_phase(migration_id, phase="backfill", state="running")
    await execute_memory_migration_plan(
        bridge,
        store,
        migration_id=migration_id,
        legacy=legacy_memories,
        decisions=memory_decisions,
    )
    await execute_raw_migration_plan(
        bridge,
        store,
        migration_id=migration_id,
        legacy=legacy_raw,
        decisions=raw_decisions,
    )
    progress: MigrationProgress = store.progress(migration_id)
    if progress.counts.get("quarantine", 0):
        store.set_phase(migration_id, phase="review_required", state="blocked")
    else:
        store.set_phase(migration_id, phase="reconciled", state="ready")
    progress = store.progress(migration_id)
    return BackfillResult(
        migration_id=migration_id,
        state=progress.state,
        phase=progress.phase,
        counts=progress.counts,
        memory_items=len(memory_decisions),
        raw_items=len(raw_decisions),
    )
