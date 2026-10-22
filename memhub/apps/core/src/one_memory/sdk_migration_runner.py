"""Offline, resumable backfill runner for SDK memory authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from one_memory.database_access import main_database
from one_memory.sdk_data_plane_migration import (
    CutoverMarker,
    DataPlaneMigrationError,
    MigrationProgress,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_migration_bundle import inspect_sdk_migration_bundle
from one_memory.sdk_migration_inventory import (
    SdkMigrationBridge,
    backfill_sdk_matter_references,
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


def sdk_cutover_blockers(database_path: Path) -> dict[str, int]:
    """Count legacy work that could write or duplicate data after cutover."""
    blockers: dict[str, int] = {}
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        tables = {
            str(row["name"])
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        checks = (
            (
                "write_jobs",
                "memory_write_jobs",
                "state NOT IN ('completed','failed','cancelled','dead')",
            ),
            (
                "memory_tasks",
                "memory_tasks",
                "status NOT IN ('completed','failed','cancelled')",
            ),
            (
                "deletions",
                "memory_deletions",
                "status IN ('pending','running')",
            ),
            (
                "pending_dispatch",
                "evidence_pending_memory_dispatch",
                "1=1",
            ),
            (
                "matter_runs",
                "matter_runs",
                "json_extract(body,'$.status')='running'",
            ),
        )
        for name, table, where in checks:
            if table not in tables:
                continue
            count = int(
                database.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {where}"
                ).fetchone()[0]
            )
            if count:
                blockers[name] = count
        if {"matters", "matter_sdk_versions"} <= tables:
            missing_matters = int(
                database.execute(
                    "SELECT COUNT(*) FROM matters current "
                    "LEFT JOIN matter_sdk_versions projected "
                    "ON projected.matter_id=current.matter_id "
                    "AND projected.version=current.version "
                    "WHERE projected.matter_id IS NULL"
                ).fetchone()[0]
            )
            if missing_matters:
                blockers["matter_references"] = missing_matters
    return blockers


def commit_reconciled_cutover(
    *,
    data_dir: Path,
    database_path: Path,
    migration_id: str,
    bundle_id: str,
    sdk_version: str,
    store_version: str = "1",
) -> CutoverMarker:
    """Verify backup/work quiescence and atomically commit SDK authority."""
    inspect_sdk_migration_bundle(data_dir=data_dir, bundle_id=bundle_id)
    blockers = sdk_cutover_blockers(database_path)
    if blockers:
        raise DataPlaneMigrationError(
            "sdk_data_plane_cutover_work_pending:"
            + json.dumps(blockers, sort_keys=True, separators=(",", ":"))
        )
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        rows = database.execute(
            "SELECT item_key,state,target_memory_id,target_hash "
            "FROM sdk_data_plane_migration_items WHERE migration_id=? "
            "ORDER BY item_key",
            (migration_id,),
        ).fetchall()
    digest = hashlib.sha256(
        json.dumps(
            [dict(row) for row in rows],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return SdkDataPlaneMigrationStore(database_path).commit_cutover(
        migration_id=migration_id,
        sdk_version=sdk_version,
        store_version=store_version,
        backup_bundle_id=bundle_id,
        reconciliation_digest=digest,
    )


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
    backfill_sdk_matter_references(
        database_path,
        store,
        migration_id=migration_id,
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
