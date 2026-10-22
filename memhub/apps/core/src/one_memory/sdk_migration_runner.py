"""Offline, resumable backfill runner for SDK memory authority."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import suppress
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
    SdkMigrationClient,
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


@dataclass(frozen=True)
class SdkMigrationStaging:
    migration_id: str
    staging_home: Path
    live_root: Path
    staging_root: Path
    protective_root: Path
    state_file: Path


def sdk_migration_staging(
    *,
    data_dir: Path,
    migration_id: str,
    namespace: str,
) -> SdkMigrationStaging:
    """Return same-filesystem paths without trusting the migration ID as a path."""
    token = hashlib.sha256(migration_id.encode()).hexdigest()[:16]
    home = data_dir.resolve()
    staging_home = home / ".sdk-migration" / token
    live_root = home / "hy-memory" / namespace
    return SdkMigrationStaging(
        migration_id=migration_id,
        staging_home=staging_home,
        live_root=live_root,
        staging_root=staging_home / "hy-memory" / namespace,
        protective_root=live_root.parent / f".{namespace}.pre-migration-{token}",
        state_file=staging_home / "swap-state.json",
    )


def _write_staging_state(paths: SdkMigrationStaging, phase: str) -> None:
    paths.staging_home.mkdir(parents=True, exist_ok=True)
    temporary = paths.state_file.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"schema_version": 1, "migration_id": paths.migration_id, "phase": phase},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temporary.replace(paths.state_file)


def recover_sdk_migration_swap(
    *,
    paths: SdkMigrationStaging,
    database_path: Path,
) -> None:
    """Recover an interrupted directory swap before opening any SDK store."""
    if not paths.protective_root.exists():
        return
    authority = SdkDataPlaneMigrationStore(database_path).authority()
    if authority.value == "sdk":
        if not paths.live_root.is_dir():
            raise DataPlaneMigrationError("sdk_migration_committed_store_missing")
        shutil.rmtree(paths.protective_root)
        shutil.rmtree(paths.staging_home, ignore_errors=True)
        return
    if paths.live_root.exists():
        if paths.staging_root.exists():
            raise DataPlaneMigrationError("sdk_migration_swap_state_ambiguous")
        paths.staging_root.parent.mkdir(parents=True, exist_ok=True)
        paths.live_root.replace(paths.staging_root)
    paths.protective_root.replace(paths.live_root)
    _write_staging_state(paths, "recovered")


def prepare_sdk_migration_staging(
    *,
    paths: SdkMigrationStaging,
    database_path: Path,
) -> Path:
    """Build a private copy; incomplete retries restart from the unchanged live root."""
    recover_sdk_migration_swap(paths=paths, database_path=database_path)
    migration_store = SdkDataPlaneMigrationStore(database_path)
    try:
        progress = migration_store.progress(paths.migration_id)
        reuse_ready = progress.state == "ready" and progress.phase == "reconciled"
    except Exception:
        reuse_ready = False
    if paths.staging_root.exists():
        if paths.staging_root.is_symlink() or not paths.staging_root.is_dir():
            raise DataPlaneMigrationError("sdk_migration_staging_invalid")
        if reuse_ready:
            return paths.staging_home
        shutil.rmtree(paths.staging_root)
    paths.live_root.mkdir(parents=True, exist_ok=True)
    from one_memory.hy_sdk_migration import snapshot_data_plane

    snapshot_data_plane(
        source_root=paths.live_root,
        target_root=paths.staging_root,
        writers_stopped=True,
    )
    _write_staging_state(paths, "prepared")
    return paths.staging_home


def activate_sdk_migration_staging(
    *,
    paths: SdkMigrationStaging,
    database_path: Path,
    commit: Callable[[], CutoverMarker],
) -> CutoverMarker:
    """Atomically expose staged SDK files and roll them back if cutover fails."""
    recover_sdk_migration_swap(paths=paths, database_path=database_path)
    if not paths.staging_root.is_dir():
        raise DataPlaneMigrationError("sdk_migration_staging_missing")
    paths.live_root.mkdir(parents=True, exist_ok=True)
    _write_staging_state(paths, "activating")
    paths.live_root.replace(paths.protective_root)
    try:
        paths.staging_root.replace(paths.live_root)
        _write_staging_state(paths, "staging_live")
        marker = commit()
    except BaseException:
        if paths.live_root.exists():
            paths.staging_root.parent.mkdir(parents=True, exist_ok=True)
            paths.live_root.replace(paths.staging_root)
        if paths.protective_root.exists():
            paths.protective_root.replace(paths.live_root)
        _write_staging_state(paths, "rolled_back")
        raise
    with suppress(OSError):
        _write_staging_state(paths, "committed")
    # Authority is already committed; leftover paths are detected by
    # sdk-migration-status and removed by sdk-cleanup on the next startup.
    shutil.rmtree(paths.protective_root, ignore_errors=True)
    shutil.rmtree(paths.staging_home, ignore_errors=True)
    return marker


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
        from one_memory.hy_runtime_config import HY_MEMORY_NAMESPACE

        recover_sdk_migration_swap(
            paths=sdk_migration_staging(
                data_dir=data_dir,
                migration_id=marker.migration_id,
                namespace=HY_MEMORY_NAMESPACE,
            ),
            database_path=database_path,
        )
    return drop_legacy_tables(database_path)


async def run_sdk_data_plane_backfill(
    *,
    database_path: Path,
    client: SdkMigrationClient,
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
    sdk_memories = await read_sdk_memory_inventory(client)
    memory_decisions = record_memory_migration_plan(
        store,
        migration_id=migration_id,
        legacy=legacy_memories,
        sdk=sdk_memories,
    )
    legacy_raw = read_legacy_raw_inventory(database_path)
    sdk_raw = await read_sdk_raw_inventory(client)
    raw_decisions = record_raw_migration_plan(
        store,
        migration_id=migration_id,
        legacy=legacy_raw,
        sdk=sdk_raw,
    )

    store.set_phase(migration_id, phase="backfill", state="running")
    await execute_memory_migration_plan(
        client,
        store,
        migration_id=migration_id,
        legacy=legacy_memories,
        decisions=memory_decisions,
    )
    await execute_raw_migration_plan(
        client,
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
