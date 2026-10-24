"""Offline startup cutover from the legacy Governance mirror to SDK authority.

Every released build wrote memory and raw turns through the SDK first; the
Evidence/Governance tables only mirrored them. The cutover therefore copies no
memory. It makes the surviving write queue independent of the legacy tables,
commits SDK authority and drops those tables.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from one_memory.database_access import main_database
from one_memory.imports.models import EvidenceRecord
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.contracts import (
    BatchIdentity,
    WriteRequest,
    internal_session_id_for,
)
from one_memory.memory_server.durable_queue import (
    CANCELLED,
    COMPLETED,
    FAILED,
    REAPED_PAYLOAD_MARKER,
    DurableWriteQueue,
)
from one_memory.memory_server.unified_entry import UnifiedWriteIntake
from one_memory.sdk_data_plane_migration import (
    CutoverMarker,
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_migration_bundle import inspect_sdk_migration_bundle
from one_memory.target_schema import drop_legacy_tables, legacy_tables_present

PREPARED_PHASE = "prepared"


@dataclass(frozen=True)
class CutoverPreparation:
    """What the cutover did to legacy work, for the receipt and the dialog."""

    frozen_jobs: int
    moved_tasks: int
    moved_dispatches: int
    abandoned: dict[str, int]

    def as_document(self) -> dict[str, object]:
        return {
            "frozen_jobs": self.frozen_jobs,
            "moved_tasks": self.moved_tasks,
            "moved_dispatches": self.moved_dispatches,
            "abandoned": dict(self.abandoned),
        }


def _tables(database: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def recover_abandoned_staging(*, data_dir: Path, database_path: Path, namespace: str) -> None:
    """Undo or discard the SDK staging copy that builds through 19 created.

    Those builds staged a full SDK copy and swapped directories at cutover. The
    live root was untouched until the swap, so before SDK authority the
    protective root is the original store; after it, the protective root is
    stale.
    """
    home = data_dir.resolve()
    live_root = home / "hy-memory" / namespace
    authority = SdkDataPlaneMigrationStore(database_path).authority()
    protective_roots = sorted(live_root.parent.glob(f".{namespace}.pre-migration-*"))
    if authority.value != "sdk" and len(protective_roots) > 1:
        raise DataPlaneMigrationError("sdk_migration_swap_state_ambiguous")
    for protective_root in protective_roots:
        if authority.value == "sdk":
            if not live_root.is_dir():
                raise DataPlaneMigrationError("sdk_migration_committed_store_missing")
            shutil.rmtree(protective_root)
            continue
        if live_root.exists():
            shutil.rmtree(live_root)
        protective_root.replace(live_root)
    shutil.rmtree(home / ".sdk-migration", ignore_errors=True)


def abandoned_staging_present(*, data_dir: Path, namespace: str) -> bool:
    home = data_dir.resolve()
    return (home / ".sdk-migration").exists() or any(
        (home / "hy-memory").glob(f".{namespace}.pre-migration-*")
    )


def prepare_legacy_write_queue_for_cutover(database_path: Path) -> tuple[int, int]:
    """Freeze old jobs and move unstarted task rows into the surviving queue."""
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        tables = _tables(database)
        if "evidence" not in tables:
            return (0, 0)
        jobs = (
            tuple(
                database.execute("SELECT job_id,state,payload_json FROM memory_write_jobs")
            )
            if "memory_write_jobs" in tables
            else ()
        )
    evidence = EvidenceStore(database_path)
    frozen = 0
    for row in jobs:
        decoded = json.loads(str(row["payload_json"]))
        if isinstance(decoded, dict) and REAPED_PAYLOAD_MARKER in decoded:
            # A finished job whose input the queue already released.
            continue
        request = WriteRequest.model_validate(decoded)
        if request.input_records:
            continue
        records = tuple(evidence.get(identifier) for identifier in request.evidence_ids)
        if any(record is None for record in records):
            if str(row["state"]) in {COMPLETED, FAILED, CANCELLED}:
                continue
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


def move_pending_dispatches(database_path: Path) -> int:
    """Queue Evidence that was waiting for its source file to finish.

    Its source cursor has already moved past these rows, so History catch-up
    after the cutover would not read them again.
    """
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        if not {"evidence", "evidence_pending_memory_dispatch"} <= _tables(database):
            return 0
        identifiers = tuple(
            str(row["evidence_id"])
            for row in database.execute(
                "SELECT DISTINCT pending.evidence_id "
                "FROM evidence_pending_memory_dispatch pending "
                "JOIN evidence USING(evidence_id) "
                "ORDER BY COALESCE(evidence.created_at,evidence.imported_at),"
                "COALESCE(evidence.ingest_seq,evidence.rowid),pending.evidence_id"
            )
        )
    evidence = EvidenceStore(database_path)
    conversations: dict[tuple[str, str, str, str | None], list[EvidenceRecord]] = {}
    for identifier in identifiers:
        record = evidence.get(identifier)
        if record is None:
            continue
        key = (record.user_id, record.source_id, record.conversation_id, record.workspace)
        conversations.setdefault(key, []).append(record)
    intake = UnifiedWriteIntake(DurableWriteQueue(database_path))
    for records in conversations.values():
        intake.submit_records(tuple(records))
    return len(conversations)


def abandoned_legacy_work(database_path: Path) -> dict[str, int]:
    """Count legacy work that ends with the cutover instead of blocking it.

    A task that may have reached the SDK is not replayed: the SDK already holds
    whatever it committed. Pending user deletions are not carried over.
    """
    abandoned: dict[str, int] = {}
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        tables = _tables(database)
        checks = (
            (
                "invoked_tasks",
                "memory_tasks",
                "status='unknown' OR (status='running' AND hy_invocation_count > 0)",
            ),
            ("deletions", "memory_deletions", "status IN ('pending','running')"),
        )
        for name, table, where in checks:
            if table not in tables:
                continue
            count = int(
                database.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
            )
            if count:
                abandoned[name] = count
    return abandoned


def prepare_legacy_work_for_cutover(database_path: Path) -> CutoverPreparation:
    """Make the surviving queue self-contained. Safe to repeat."""
    frozen, moved = prepare_legacy_write_queue_for_cutover(database_path)
    dispatches = move_pending_dispatches(database_path)
    return CutoverPreparation(
        frozen_jobs=frozen,
        moved_tasks=moved,
        moved_dispatches=dispatches,
        abandoned=abandoned_legacy_work(database_path),
    )


def commit_sdk_cutover(
    *,
    data_dir: Path,
    database_path: Path,
    migration_id: str,
    bundle_id: str,
    sdk_version: str,
    store_version: str = "1",
) -> CutoverMarker:
    """Verify the backup, settle legacy work and commit SDK authority."""
    inspect_sdk_migration_bundle(data_dir=data_dir, bundle_id=bundle_id)
    # Repeated here because a CLI-managed Runtime may have run on legacy
    # authority since `sdk-migrate`.
    preparation = prepare_legacy_work_for_cutover(database_path)
    digest = hashlib.sha256(
        json.dumps(
            preparation.as_document(),
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

    recover_abandoned_staging(
        data_dir=data_dir,
        database_path=database_path,
        namespace=HY_MEMORY_NAMESPACE,
    )
    return drop_legacy_tables(database_path)


MIGRATION_PREFIX = "sdk-authority-v2"
CAPTURE_FENCE = ".sdk-migration-capture-fence.json"


@dataclass(frozen=True)
class StartupMigrationResult:
    """`not_required`, `cutover` (this run switched authority) or `complete`."""

    state: str
    migration_id: str | None = None
    bundle_id: str | None = None
    preparation: CutoverPreparation | None = None
    dropped_tables: tuple[str, ...] = ()

    def as_document(self) -> dict[str, object]:
        document: dict[str, object] = {"schema_version": 1, "state": self.state}
        if self.migration_id is not None:
            document["migration_id"] = self.migration_id
            document["bundle_id"] = self.bundle_id
        if self.preparation is not None:
            document.update(self.preparation.as_document())
        if self.dropped_tables:
            document["dropped_tables"] = list(self.dropped_tables)
        return document


def database_generation(database_path: Path) -> str:
    stat = database_path.stat()
    return hashlib.sha256(f"{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:16]


def _resumable_identity(database_path: Path) -> tuple[str, str]:
    """Resume the newest unfinished run, including one build 19 left blocked."""
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        row = (
            database.execute(
                "SELECT migration_id,backup_bundle_id FROM sdk_data_plane_migrations "
                "WHERE state IN ('running','blocked','ready') AND migration_id LIKE ? "
                "ORDER BY created_at DESC LIMIT 1",
                (f"{MIGRATION_PREFIX}-%",),
            ).fetchone()
            if "sdk_data_plane_migrations" in _tables(database)
            else None
        )
    if row is not None and row["backup_bundle_id"] == f"{row['migration_id']}-pre-cutover":
        return str(row["migration_id"]), str(row["backup_bundle_id"])
    migration_id = f"{MIGRATION_PREFIX}-{database_generation(database_path)}"
    return migration_id, f"{migration_id}-pre-cutover"


@contextmanager
def capture_fence(data_dir: Path) -> Iterator[None]:
    """Host capture hooks return silently while this process is alive."""
    marker = data_dir / CAPTURE_FENCE
    temporary = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps({"schema_version": 1, "pid": os.getpid()}) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(marker)
    try:
        yield
    finally:
        marker.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def _ensure_bundle(*, data_dir: Path, bundle_id: str, runtime_build: str) -> dict[str, object]:
    from one_memory.sdk_migration_bundle import (
        MigrationBundleRejected,
        create_sdk_migration_bundle,
    )

    try:
        create_sdk_migration_bundle(
            data_dir=data_dir,
            bundle_id=bundle_id,
            runtime_build=runtime_build,
            runtime_stopped=True,
        )
    except MigrationBundleRejected as error:
        if error.code != "sdk_migration_bundle_exists":
            raise
    return inspect_sdk_migration_bundle(data_dir=data_dir, bundle_id=bundle_id)


def run_startup_migration(
    *,
    data_dir: Path,
    runtime_build: str,
    sdk_version: str,
    namespace: str,
    progress: Callable[[str], None] = lambda _step: None,
) -> StartupMigrationResult:
    """Bring one stopped home to SDK authority. Safe to repeat after any failure.

    The caller must stop Runtime and keep its service from restarting.
    """
    database_path = data_dir / "one-memory.sqlite3"
    if not database_path.is_file():
        return StartupMigrationResult(state="not_required")
    store = SdkDataPlaneMigrationStore(database_path)
    if store.authority() is MemoryAuthority.SDK:
        if not legacy_tables_present(database_path) and not abandoned_staging_present(
            data_dir=data_dir, namespace=namespace
        ):
            return StartupMigrationResult(state="not_required")
        progress("cleanup")
        return StartupMigrationResult(
            state="complete",
            dropped_tables=resume_legacy_cleanup(data_dir=data_dir, database_path=database_path),
        )

    from one_memory.database_access import close_main_databases
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    migration_id, bundle_id = _resumable_identity(database_path)
    with capture_fence(data_dir):
        progress("bundle")
        manifest = _ensure_bundle(
            data_dir=data_dir,
            bundle_id=bundle_id,
            runtime_build=runtime_build,
        )
        progress("prepare")
        asyncio.run(migrate(SqliteMigrationStore(database_path), MIGRATIONS))
        close_main_databases()
        recover_abandoned_staging(
            data_dir=data_dir,
            database_path=database_path,
            namespace=namespace,
        )
        store.start(
            migration_id=migration_id,
            source_runtime_build=str(manifest.get("runtime_build") or ""),
            target_sdk_version=sdk_version,
            source_schema_version=MIGRATIONS[-1].version,
            namespace_fingerprint=hashlib.sha256(namespace.encode()).hexdigest(),
            backend_fingerprint=hashlib.sha256(b"chroma:kuzu:sqlite").hexdigest(),
            backup_bundle_id=bundle_id,
        )
        preparation = prepare_legacy_work_for_cutover(database_path)
        store.set_phase(migration_id, phase=PREPARED_PHASE, state="ready")
        progress("cutover")
        commit_sdk_cutover(
            data_dir=data_dir,
            database_path=database_path,
            migration_id=migration_id,
            bundle_id=bundle_id,
            sdk_version=sdk_version,
        )
        progress("cleanup")
        dropped = resume_legacy_cleanup(data_dir=data_dir, database_path=database_path)
    return StartupMigrationResult(
        state="cutover",
        migration_id=migration_id,
        bundle_id=bundle_id,
        preparation=preparation,
        dropped_tables=dropped,
    )
