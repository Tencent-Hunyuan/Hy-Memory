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
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    RUNNING,
    UNKNOWN,
    DurableWriteQueue,
)
from one_memory.memory_server.unified_entry import UnifiedWriteIntake
from one_memory.model_profiles import ModelProfileSnapshot
from one_memory.sdk_data_plane_migration import (
    CutoverMarker,
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_migration_bundle import inspect_sdk_migration_bundle
from one_memory.sdk_migration_verify import (
    MemoryReadbackFailed,
    ReadbackReport,
    legacy_memory_ids_from,
    legacy_memory_ids_from_bundle,
    read_active_profile,
    runtime_sdk_client,
    verify_readback,
)
from one_memory.sdk_vector_reattach import (
    ReattachedCollection,
    legacy_vector_collections,
    reattach_legacy_vectors,
)
from one_memory.target_schema import drop_legacy_tables, legacy_tables_present

PREPARED_PHASE = "prepared"


class _StepLog:
    """Each runner step, its duration, its counts and how it ended.

    The CLI only sees the exception that escapes `run_startup_migration` and
    names it with a generic code when it is not one of ours; these lines say
    which step raised it. Content-free: only names, counts and codes.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.migration_id: str | None = None

    def check(self, step: str, code: str | None) -> None:
        """One preflight check that is a decision, not a call: its result code."""
        from one_memory.sdk_migration_log import write_migration_log

        write_migration_log(
            self.data_dir,
            writer="core-sdk-migrate",
            event="step.failed" if code else "step.finished",
            outcome="failed" if code else "succeeded",
            migration_id=self.migration_id,
            code=code,
            details={"step": step, "result": code or "passed"},
        )

    @contextmanager
    def step(self, name: str) -> Iterator[dict[str, object]]:
        from one_memory.sdk_migration_log import write_migration_log

        started = time.monotonic()
        details: dict[str, object] = {}
        try:
            yield details
        except BaseException as error:
            code = getattr(error, "code", None)
            write_migration_log(
                self.data_dir,
                writer="core-sdk-migrate",
                event="step.failed",
                outcome="failed",
                migration_id=self.migration_id,
                code=code if isinstance(code, str) else None,
                error=error,
                details={
                    "step": name,
                    "duration_ms": (time.monotonic() - started) * 1000,
                    **details,
                },
            )
            raise
        write_migration_log(
            self.data_dir,
            writer="core-sdk-migrate",
            event="step.finished",
            outcome="succeeded",
            migration_id=self.migration_id,
            details={
                "step": name,
                "duration_ms": (time.monotonic() - started) * 1000,
                **details,
            },
        )


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
        str(row[0]) for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
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
            tuple(database.execute("SELECT job_id,state,payload_json FROM memory_write_jobs"))
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


#: Why a pre-cutover write job that may have reached HY was closed.
ABANDONED_AT_CUTOVER = "abandoned_at_cutover"


def settle_legacy_unknown_write_jobs(database_path: Path) -> int:
    """Close write jobs the legacy pipeline left possibly-sent. Safe to repeat.

    They ran under the legacy pipeline, so the SDK's `get_write_outcome` knows
    none of them and the unknown reconcile can never settle them: a migrated
    1.0.4 home showed 1000+ unknown forever. They are not retried, since they
    may already have written and the SDK has no idempotency key. `cancelled`,
    not `failed`: the upgrade ended them, nothing broke, and a `failed` count
    would put the home in the failed state for good. `hy_invoked` is kept, so
    no readmission path can pick one up again.
    """
    with main_database(database_path).read(row_factory=sqlite3.Row) as database:
        if "memory_write_jobs" not in _tables(database):
            return 0
    now = datetime.now(UTC).isoformat()
    with main_database(database_path).write(autocommit=True) as database:
        # A `running` row already marked invoked would be reclaimed to
        # `unknown` on the next open, so it is the same case.
        return int(
            database.execute(
                "UPDATE memory_write_jobs SET state=?,lease_owner=NULL,"
                "lease_expires_at=NULL,last_error=?,updated_at=? "
                "WHERE state=? OR (state=? AND hy_invoked=1)",
                (CANCELLED, ABANDONED_AT_CUTOVER, now, UNKNOWN, RUNNING),
            ).rowcount
        )


def abandoned_legacy_work(database_path: Path) -> dict[str, int]:
    """Count legacy work that ends with the cutover instead of blocking it.

    A task that may have reached the SDK is not replayed: the SDK already holds
    whatever it committed. Pending user deletions are not carried over.
    Settled write jobs are counted by their code, so a repeated preparation
    reports the same number.
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
            (
                "unknown_write_jobs",
                "memory_write_jobs",
                f"state='{CANCELLED}' AND last_error='{ABANDONED_AT_CUTOVER}'",
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


def prepare_legacy_work_for_cutover(
    database_path: Path, trace: dict[str, object] | None = None
) -> CutoverPreparation:
    """Make the surviving queue self-contained. Safe to repeat.

    `trace` (the migration log's step details) learns which sub-step is
    running, so a failure names it.
    """
    trace = {} if trace is None else trace
    # First: a possibly-sent job never runs again, so its input is not
    # frozen, and input released since must not block the cutover.
    trace["sub_step"] = "unknown_write_jobs"
    settle_legacy_unknown_write_jobs(database_path)
    trace["sub_step"] = "write_queue"
    frozen, moved = prepare_legacy_write_queue_for_cutover(database_path)
    trace["sub_step"] = "pending_dispatches"
    dispatches = move_pending_dispatches(database_path)
    trace["sub_step"] = "abandoned_work"
    abandoned = abandoned_legacy_work(database_path)
    trace.pop("sub_step", None)
    return CutoverPreparation(
        frozen_jobs=frozen,
        moved_tasks=moved,
        moved_dispatches=dispatches,
        abandoned=abandoned,
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
    reattached: tuple[ReattachedCollection, ...] = ()
    readback: ReadbackReport | None = None

    def as_document(self) -> dict[str, object]:
        document: dict[str, object] = {"schema_version": 1, "state": self.state}
        if self.migration_id is not None:
            document["migration_id"] = self.migration_id
            document["bundle_id"] = self.bundle_id
        if self.preparation is not None:
            document.update(self.preparation.as_document())
        if self.reattached:
            document["reattached_vectors"] = [
                {
                    "collection": item.current,
                    "legacy_count": item.legacy_count,
                    "merged_count": item.merged_count,
                    "final_count": item.final_count,
                }
                for item in self.reattached
            ]
        if self.readback is not None:
            document["readback"] = self.readback.as_document()
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


def _write_capture_fence(data_dir: Path, document: dict[str, object]) -> None:
    marker = data_dir / CAPTURE_FENCE
    temporary = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(marker)


@contextmanager
def capture_fence(data_dir: Path) -> Iterator[None]:
    """Host capture hooks return silently while this process is alive.

    A failed run leaves the fence frozen instead of lifting it: the home was
    put back (or is half-migrated) and must not take new writes until the next
    migration attempt replaces the fence with its own and succeeds.
    """
    marker = data_dir / CAPTURE_FENCE
    _write_capture_fence(data_dir, {"schema_version": 1, "pid": os.getpid()})
    try:
        yield
    except BaseException:
        try:
            _write_capture_fence(
                data_dir, {"schema_version": 1, "frozen": True, "reason": "sdk_migration_failed"}
            )
        except OSError:
            marker.unlink(missing_ok=True)
        raise
    else:
        marker.unlink(missing_ok=True)
    finally:
        marker.with_name(f"{marker.name}.{os.getpid()}.tmp").unlink(missing_ok=True)


def _ensure_bundle(
    *, data_dir: Path, bundle_id: str, runtime_build: str
) -> tuple[dict[str, object], bool]:
    """The verified bundle, and whether this call created it."""
    from one_memory.sdk_migration_bundle import (
        MigrationBundleRejected,
        create_sdk_migration_bundle,
    )

    created = True
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
        created = False
    return inspect_sdk_migration_bundle(data_dir=data_dir, bundle_id=bundle_id), created


def _restore_point_bundle(
    *, data_dir: Path, migration_id: str, bundle_id: str, created: bool, runtime_build: str
) -> str:
    """The bundle that holds this home exactly as this run found it.

    The cutover bundle qualifies when this run created it. One left by an
    earlier run (an older build's blocked ledger row) may predate later writes,
    so restoring it could drop them; a fresh restore-point bundle is taken.
    """
    if created:
        return bundle_id
    from one_memory.sdk_migration_bundle import create_sdk_migration_bundle

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    restore_id = f"{migration_id}-restore-point-{stamp}"
    create_sdk_migration_bundle(
        data_dir=data_dir,
        bundle_id=restore_id,
        runtime_build=runtime_build,
        runtime_stopped=True,
    )
    inspect_sdk_migration_bundle(data_dir=data_dir, bundle_id=restore_id)
    return restore_id


def _recover_interrupted_attempt(data_dir: Path, database_path: Path, log: _StepLog) -> None:
    """Roll back what a killed earlier run left, before anything reads the home."""
    from one_memory.sdk_migration_restore import (
        MigrationRestoreFailed,
        clear_restore_point,
        finish_interrupted_restore,
        read_restore_point,
        restore_pre_migration_state,
    )

    finish_interrupted_restore(data_dir)
    point = read_restore_point(data_dir)
    if point is None:
        return
    with log.step("preflight.interrupted_attempt") as step:
        try:
            authority = SdkDataPlaneMigrationStore(database_path).authority()
        except Exception as error:
            # Never restore over a home that may already be on SDK authority.
            raise MigrationRestoreFailed("sdk_migration_restore_authority_unknown") from error
        step["authority"] = authority.value
        if authority is MemoryAuthority.SDK:
            clear_restore_point(data_dir)
            step["restored"] = False
            return
        restore_pre_migration_state(
            data_dir,
            bundle_id=point["bundle_id"],
            migration_id=point["migration_id"],
            reason="interrupted_attempt",
        )
        step["restored"] = True


def _vacuum(database_path: Path) -> None:
    """Return the dropped tables' pages to the disk; a full disk only skips it."""
    try:
        with main_database(database_path).write(autocommit=True) as database:
            database.execute("VACUUM")
    except sqlite3.OperationalError:
        return


def run_startup_migration(
    *,
    data_dir: Path,
    runtime_build: str,
    sdk_version: str,
    namespace: str,
    progress: Callable[[str], None] = lambda _step: None,
    open_client: Callable[[ModelProfileSnapshot], AbstractContextManager[Any]] | None = None,
    restore_report: dict[str, object] | None = None,
) -> StartupMigrationResult:
    """Bring one stopped home to SDK authority. Safe to repeat after any failure.

    The caller must stop Runtime and keep its service from restarting.
    `open_client` yields a public SDK client configured as the Runtime will be;
    tests replace it.

    A failure before SDK authority is committed puts the home back exactly as
    this run found it (`sdk_migration_restore`), so the next run, possibly of a
    fixed release, starts from the original data. The original exception is
    re-raised either way; `restore_report` learns what the restore did
    (`state`: `restored`, `failed` with its own `code`, or `skipped`).
    """
    from one_memory.sdk_migration_restore import read_restore_point, restore_pending

    database_path = data_dir / "one-memory.sqlite3"
    log = _StepLog(data_dir)
    # Before anything reads the home: a run killed part-way is rolled back.
    if restore_pending(data_dir) or read_restore_point(data_dir) is not None:
        with capture_fence(data_dir):
            _recover_interrupted_attempt(data_dir, database_path, log)
    if not database_path.is_file():
        return StartupMigrationResult(state="not_required")

    def client_for(profile: ModelProfileSnapshot) -> AbstractContextManager[Any]:
        if open_client is not None:
            return open_client(profile)
        return runtime_sdk_client(database_path, data_dir, profile)

    store = SdkDataPlaneMigrationStore(database_path)
    with log.step("preflight.authority_read") as step:
        authority, authority_migration = store.authority_state()
        step["authority"] = authority.value
    log.migration_id = authority_migration
    with log.step("preflight.profile_read") as step:
        profile = read_active_profile(database_path)
        step["profile_present"] = profile is not None
    if authority is MemoryAuthority.SDK:
        with log.step("preflight.leftovers_scan") as step:
            vectors_waiting = profile is not None and bool(
                legacy_vector_collections(data_dir, profile)
            )
            leftovers = bool(legacy_tables_present(database_path)) or abandoned_staging_present(
                data_dir=data_dir, namespace=namespace
            )
            step["vectors_waiting"] = vectors_waiting
            step["leftovers"] = leftovers
        if not vectors_waiting and not leftovers:
            return StartupMigrationResult(state="not_required")
        reattached: tuple[ReattachedCollection, ...] = ()
        readback: ReadbackReport | None = None
        if vectors_waiting:
            assert profile is not None
            progress("vectors")
            with log.step("vectors.reattach") as step:
                reattached = reattach_legacy_vectors(data_dir, profile)
                step["collections"] = len(reattached)
            with log.step("verify.legacy_ids_from_bundle") as step:
                legacy = legacy_memory_ids_from_bundle(data_dir, authority_migration)
                step["bundle_ids_present"] = legacy is not None
            if legacy is not None and not legacy.empty:
                # Authority already moved, so nothing is left to protect by
                # refusing; record what the repaired home reads back.
                progress("verify")
                with log.step("verify.readback") as step, client_for(profile) as client:
                    readback = verify_readback(client, legacy)
                    step.update(readback.as_document())
        dropped: tuple[str, ...] = ()
        if leftovers:
            progress("cleanup")
            with log.step("cleanup.drop_legacy_tables") as step:
                dropped = resume_legacy_cleanup(data_dir=data_dir, database_path=database_path)
                step["dropped_tables"] = len(dropped)
            if dropped:
                with log.step("cleanup.vacuum"):
                    _vacuum(database_path)
        return StartupMigrationResult(
            state="complete",
            migration_id=authority_migration,
            bundle_id=f"{authority_migration}-pre-cutover" if authority_migration else None,
            dropped_tables=dropped,
            reattached=reattached,
            readback=readback,
        )

    from one_memory.database_access import close_main_databases
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS
    from one_memory.sdk_migration_restore import (
        clear_restore_point,
        discard_failed_state,
        prune_restored_bundles,
        record_restore_point,
    )

    with log.step("preflight.resumable_identity") as step:
        migration_id, bundle_id = _resumable_identity(database_path)
        step["resumed"] = migration_id != f"{MIGRATION_PREFIX}-{database_generation(database_path)}"
    log.migration_id = migration_id
    with log.step("preflight.legacy_ids_scan") as step:
        legacy = legacy_memory_ids_from(database_path)
        step["chat"] = len(legacy.chat)
        step["productivity"] = len(legacy.productivity)
        step["users"] = len(legacy.users)
    if profile is None and not legacy.empty:
        # Without the embedding profile the Runtime's collections cannot be
        # named, so neither reattached nor read back. Legacy stays intact.
        log.check("preflight.model_profile_check", "sdk_migration_model_profile_required")
        raise DataPlaneMigrationError("sdk_migration_model_profile_required")
    log.check("preflight.model_profile_check", None)
    with capture_fence(data_dir):
        # Builds through 19 left a staged SDK copy beside the original store.
        # Settling it before the bundle means the bundle holds the original
        # store, and a restore never brings the staged copy back.
        with log.step("prepare.recover_abandoned_staging"):
            recover_abandoned_staging(
                data_dir=data_dir,
                database_path=database_path,
                namespace=namespace,
            )
        progress("bundle")
        with log.step("bundle.ensure") as step:
            manifest, created = _ensure_bundle(
                data_dir=data_dir,
                bundle_id=bundle_id,
                runtime_build=runtime_build,
            )
            step["bundle_id"] = bundle_id
            step["created"] = created
        with log.step("bundle.restore_point") as step:
            restore_bundle = _restore_point_bundle(
                data_dir=data_dir,
                migration_id=migration_id,
                bundle_id=bundle_id,
                created=created,
                runtime_build=runtime_build,
            )
            record_restore_point(data_dir, migration_id=migration_id, bundle_id=restore_bundle)
            step["bundle_id"] = restore_bundle
            step["pruned"] = prune_restored_bundles(data_dir, keep={bundle_id, restore_bundle})
        # From here until SDK authority is committed, a failure puts the
        # restore point back. Only `cutover.commit` can fail after its own
        # commit; the database decides then.
        cutover_started = False
        try:
            progress("prepare")
            with log.step("prepare.schema_migrate") as step:
                asyncio.run(migrate(SqliteMigrationStore(database_path), MIGRATIONS))
                close_main_databases()
                step["schema_version"] = MIGRATIONS[-1].version
            with log.step("prepare.ledger_start"):
                store.start(
                    migration_id=migration_id,
                    source_runtime_build=str(manifest.get("runtime_build") or ""),
                    target_sdk_version=sdk_version,
                    source_schema_version=MIGRATIONS[-1].version,
                    namespace_fingerprint=hashlib.sha256(namespace.encode()).hexdigest(),
                    backend_fingerprint=hashlib.sha256(b"chroma:kuzu:sqlite").hexdigest(),
                    backup_bundle_id=bundle_id,
                )
            with log.step("prepare.legacy_work") as step:
                preparation = prepare_legacy_work_for_cutover(database_path, step)
                step.update(preparation.as_document())
            reattached = ()
            readback = None
            if profile is not None:
                progress("vectors")
                with log.step("vectors.reattach") as step:
                    reattached = reattach_legacy_vectors(data_dir, profile)
                    step["collections"] = len(reattached)
            if not legacy.empty:
                assert profile is not None
                progress("verify")
                with log.step("verify.readback") as step:
                    with client_for(profile) as client:
                        readback = verify_readback(client, legacy)
                    step.update(readback.as_document())
                    if not readback.passed:
                        store.set_phase(migration_id, phase="readback_failed", state="blocked")
                        raise MemoryReadbackFailed(readback)
            with log.step("prepare.mark_prepared"):
                store.set_phase(migration_id, phase=PREPARED_PHASE, state="ready")
            progress("cutover")
            cutover_started = True
            with log.step("cutover.commit"):
                commit_sdk_cutover(
                    data_dir=data_dir,
                    database_path=database_path,
                    migration_id=migration_id,
                    bundle_id=bundle_id,
                    sdk_version=sdk_version,
                )
        except Exception:
            _restore_after_failure(
                data_dir=data_dir,
                database_path=database_path,
                migration_id=migration_id,
                bundle_id=restore_bundle,
                cutover_started=cutover_started,
                report=restore_report,
            )
            raise
        # SDK authority is committed: from here on the home is never restored.
        clear_restore_point(data_dir)
        if restore_bundle != bundle_id:
            shutil.rmtree(
                data_dir.resolve() / "backups" / f"{restore_bundle}.sdk-bundle",
                ignore_errors=True,
            )
        discard_failed_state(data_dir)
        _repair_metadata_after_cutover(data_dir, database_path, log)
        progress("cleanup")
        with log.step("cleanup.drop_legacy_tables") as step:
            dropped = resume_legacy_cleanup(data_dir=data_dir, database_path=database_path)
            step["dropped_tables"] = len(dropped)
        if dropped:
            with log.step("cleanup.vacuum"):
                _vacuum(database_path)
    return StartupMigrationResult(
        state="cutover",
        migration_id=migration_id,
        bundle_id=bundle_id,
        preparation=preparation,
        dropped_tables=dropped,
        reattached=reattached,
        readback=readback,
    )


def _repair_metadata_after_cutover(data_dir: Path, database_path: Path, log: _StepLog) -> None:
    """Restore lost metadata here, where Runtime is stopped and no Chroma client is open.

    Writes the repair receipt, so Core's startup fallback finds it and does
    nothing. Authority is already committed: a failure is logged and left to
    that fallback instead of failing the migration.
    """
    from one_memory.hy_sdk_migration import repair_sdk_metadata_from_bundle

    try:
        with log.step("cutover.metadata_repair") as step:
            repair = repair_sdk_metadata_from_bundle(data_dir=data_dir, database_path=database_path)
            step["mapped"] = repair.mapped
            step["vector_rows"] = repair.vector_rows
            step["coding_rows"] = repair.coding_rows
    except Exception:
        return


def _restore_after_failure(
    *,
    data_dir: Path,
    database_path: Path,
    migration_id: str,
    bundle_id: str,
    cutover_started: bool,
    report: dict[str, object] | None,
) -> None:
    """Put the restore point back after a pre-cutover failure. Never raises.

    The caller re-raises the migration's own error: the restore is secondary,
    so its outcome only goes to the log and to `report`.
    """
    from one_memory.sdk_migration_log import write_migration_log
    from one_memory.sdk_migration_restore import restore_pre_migration_state

    report = {} if report is None else report
    if cutover_started:
        try:
            committed = SdkDataPlaneMigrationStore(database_path).authority() is MemoryAuthority.SDK
        except Exception:  # noqa: BLE001 - never restore over an unknown authority
            committed = True
        if committed:
            write_migration_log(
                data_dir,
                writer="core-sdk-migrate",
                event="restore.skipped",
                outcome="succeeded",
                migration_id=migration_id,
                details={"reason": "authority_committed_or_unknown"},
            )
            report["state"] = "skipped"
            return
    try:
        restore_pre_migration_state(
            data_dir,
            bundle_id=bundle_id,
            migration_id=migration_id,
            reason="migration_failed",
        )
    except Exception as error:  # noqa: BLE001 - the migration's own failure is raised
        code = getattr(error, "code", None)
        report["state"] = "failed"
        report["code"] = code if isinstance(code, str) else "sdk_migration_restore_failed"
        return
    report["state"] = "restored"
