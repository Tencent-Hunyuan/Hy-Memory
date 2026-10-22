"""Offline, resumable backfill runner for SDK memory authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from one_memory.database_access import main_database
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.contracts import (
    BatchIdentity,
    WriteRequest,
    internal_session_id_for,
)
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.sdk_data_plane_migration import (
    CutoverMarker,
    DataPlaneMigrationError,
    MigrationProgress,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_migration_bundle import inspect_sdk_migration_bundle
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
from one_memory.target_schema import drop_legacy_tables


@dataclass(frozen=True)
class BackfillResult:
    migration_id: str
    state: str
    phase: str
    counts: dict[str, int]
    memory_items: int
    raw_items: int


def prepare_legacy_write_queue_for_cutover(database_path: Path) -> tuple[int, int]:
    """Freeze old jobs and move unstarted task rows into the surviving queue."""
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        tables = {
            str(row["name"])
            for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "evidence" not in tables:
            return (0, 0)
        jobs = (
            tuple(database.execute("SELECT job_id,payload_json FROM memory_write_jobs"))
            if "memory_write_jobs" in tables
            else ()
        )
    evidence = EvidenceStore(database_path)
    frozen = 0
    for row in jobs:
        request = WriteRequest.model_validate_json(str(row["payload_json"]))
        if request.input_records:
            continue
        records = tuple(evidence.get(identifier) for identifier in request.evidence_ids)
        if any(record is None for record in records):
            raise DataPlaneMigrationError(f"sdk_data_plane_write_job_input_missing:{row['job_id']}")
        materialized = tuple(record for record in records if record is not None)
        updated = request.model_copy(update={"input_records": materialized})
        with main_database(database_path).write(autocommit=True) as database:
            database.execute(
                "UPDATE memory_write_jobs SET payload_json=? WHERE job_id=?",
                (updated.model_dump_json(), str(row["job_id"])),
            )
        frozen += 1

    if not {"memory_tasks", "memory_task_inputs"} <= tables:
        return (frozen, 0)
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        tasks = tuple(
            database.execute(
                "SELECT task.task_id,task.input_ref,task.status,"
                "task.hy_invocation_count,inputs.refs_json "
                "FROM memory_tasks task "
                "LEFT JOIN memory_task_inputs inputs USING(task_id) "
                "WHERE task.status IN "
                "('pending','waiting_for_permission','waiting_for_model',"
                "'deferred_outside_window','running')"
            )
        )
    queue = DurableWriteQueue(database_path)
    moved = 0
    for task in tasks:
        if int(task["hy_invocation_count"] or 0) > 0:
            continue
        references = json.loads(str(task["refs_json"])) if task["refs_json"] else []
        identifiers = [
            str(reference["evidence_id"])
            for reference in references
            if isinstance(reference, dict) and reference.get("evidence_id")
        ] or [str(task["input_ref"])]
        records = tuple(evidence.get(identifier) for identifier in identifiers)
        if any(record is None for record in records):
            raise DataPlaneMigrationError(
                f"sdk_data_plane_memory_task_input_missing:{task['task_id']}"
            )
        materialized = tuple(record for record in records if record is not None)
        source = materialized[0]
        internal_session_id = internal_session_id_for(
            source.conversation_id,
            source.source_id,
        )
        queue.submit(
            WriteRequest(
                internal_session_id=internal_session_id,
                source_id=source.source_id,
                batch_identity=BatchIdentity(
                    internal_session_id=internal_session_id,
                    first_round=1,
                    last_round=max(1, len(materialized)),
                ),
                evidence_ids=[record.evidence_id for record in materialized],
                input_records=materialized,
                idempotency_key=f"legacy-task:{task['task_id']}",
                priority="normal",
            )
        )
        with main_database(database_path).write(autocommit=True) as database:
            database.execute(
                "UPDATE memory_tasks SET status='cancelled',runner_token=NULL,"
                "last_error_code='migrated_to_memory_write_job',updated_at=CURRENT_TIMESTAMP "
                "WHERE task_id=?",
                (str(task["task_id"]),),
            )
        moved += 1
    return (frozen, moved)


def sdk_cutover_blockers(database_path: Path) -> dict[str, int]:
    """Count legacy work that could write or duplicate data after cutover."""
    blockers: dict[str, int] = {}
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        tables = {
            str(row["name"])
            for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        checks = (
            (
                "memory_tasks",
                "memory_tasks",
                "status='unknown' OR (status='running' AND hy_invocation_count > 0)",
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
        )
        for name, table, where in checks:
            if table not in tables:
                continue
            count = int(
                database.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
            )
            if count:
                blockers[name] = count
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
    prepare_legacy_write_queue_for_cutover(database_path)
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
    marker = SdkDataPlaneMigrationStore(database_path).commit_cutover(
        migration_id=migration_id,
        sdk_version=sdk_version,
        store_version=store_version,
        backup_bundle_id=bundle_id,
        reconciliation_digest=digest,
    )
    inspect_sdk_migration_bundle(data_dir=data_dir, bundle_id=marker.backup_bundle_id)
    drop_legacy_tables(database_path)
    return marker


def resume_legacy_cleanup(*, data_dir: Path, database_path: Path) -> tuple[str, ...]:
    """Finish a cleanup interrupted after the durable authority switch."""
    store = SdkDataPlaneMigrationStore(database_path)
    if store.authority().value != "sdk":
        raise DataPlaneMigrationError("sdk_data_plane_cleanup_before_cutover")
    marker = store.cutover()
    if marker is not None:
        inspect_sdk_migration_bundle(
            data_dir=data_dir,
            bundle_id=marker.backup_bundle_id,
        )
    return drop_legacy_tables(database_path)


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
