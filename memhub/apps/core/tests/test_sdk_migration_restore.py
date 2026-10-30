"""A failed upgrade migration puts the home back exactly as it found it.

The fixed release that follows must migrate from the original 1.0.4 data, not
from a half-migrated home: every pre-cutover failure restores the database,
the SDK store and `config.json`, and a later run succeeds from there.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from one_memory import cli
from one_memory.config import Settings
from one_memory.database_access import close_main_databases, main_database
from one_memory.imports.storage import EvidenceStore
from one_memory.live_capture import sdk_migration_capture_fenced
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.runtime_client import RuntimeStatus
from one_memory.sdk_data_plane_migration import (
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_migration_log import log_path
from one_memory.sdk_migration_restore import (
    RESTORE_POINT,
    MigrationRestoreFailed,
    failed_state_path,
    finish_interrupted_restore,
    restore_pending,
)
from one_memory.sdk_migration_runner import run_startup_migration
from one_memory.sdk_migration_verify import MemoryReadbackFailed
from one_memory.target_schema import legacy_tables_present

NAMESPACE = "governed-v2"
CONFIG = {"schema_version": 1, "sources": {"cursor": {"sync_enabled": True}}}


@pytest.fixture
def profile(monkeypatch):
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

    snapshot = builtin_retrieval_snapshot()
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: snapshot)
    monkeypatch.setattr("one_memory.sdk_migration_verify.read_active_profile", lambda _db: snapshot)
    return snapshot


def _chroma(home: Path):
    from one_memory.sdk_vector_reattach import open_offline_chroma

    return open_offline_chroma(home / "hy-memory" / NAMESPACE / "vector")


@pytest.fixture
def home(tmp_path, profile) -> Path:
    """A 1.0.4-shaped home: legacy memories, queued legacy work, legacy vectors."""
    from one_memory.hy_sdk_runtime import vector_collection_bases

    database = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(database), MIGRATIONS))
    with main_database(database).write() as connection, connection:
        for memory_id in ("m1", "m2"):
            connection.execute(
                "INSERT INTO governed_memories (memory_id,user_id,kind,status,content,"
                "scopes_json,revision,created_at,updated_at) "
                "VALUES (?,'local','chat','confirmed','x','[]',1,'now','now')",
                (memory_id,),
            )
        verbatim = "text of e1"
        connection.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,"
            "revision,imported_at,updated_at,ingest_seq) "
            "VALUES ('e1','e1','local','cursor','session-1','raw_conversation','user',"
            "'person',?,?,1,'2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00',1)",
            (verbatim, hashlib.sha256(verbatim.encode()).hexdigest()),
        )
        connection.execute(
            "INSERT INTO memory_tasks "
            "(task_id,request_id,user_id,kind,input_ref,status,profile_id,"
            "profile_revision,profile_snapshot_json,runner_token,attempts,"
            "last_error_code,created_at,updated_at,duration_seconds,hy_invocation_count) "
            "VALUES ('t1','request-t1','local','extract','e1','pending','profile',1,'{}',"
            "NULL,0,NULL,'now','now',NULL,0)"
        )
    close_main_databases()
    assert EvidenceStore(database).get("e1") is not None
    close_main_databases()
    current, legacy = vector_collection_bases(profile)
    client = _chroma(tmp_path)
    for name, ids in ((f"{legacy[0]}_4", ["m1", "m2"]), (f"{current}_4", ["after"])):
        collection = client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
        collection.upsert(
            ids=ids,
            embeddings=[[float(index + 1)] * 4 for index, _ in enumerate(ids)],
            documents=[f"doc {identifier}" for identifier in ids],
        )
    client.close()
    (tmp_path / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    return tmp_path


def _collections(home: Path) -> dict[str, int]:
    client = _chroma(home)
    try:
        return {item.name: item.count() for item in client.list_collections()}
    finally:
        client.close()


def _state(home: Path) -> dict[str, object]:
    """Everything a migration may change, compared byte for byte or row for row."""
    close_main_databases()
    with closing(sqlite3.connect(home / "one-memory.sqlite3")) as connection:
        dump = list(connection.iterdump())
    sdk = home / "hy-memory" / NAMESPACE
    files = {
        path.relative_to(sdk).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(sdk.rglob("*"))
        if path.is_file()
    }
    from one_memory.sdk_vector_reattach import _stored_names

    return {
        "database": dump,
        "sdk_files": files,
        "collection_names": _stored_names(home),
        "config": (home / "config.json").read_bytes(),
    }


def _reading(readable: set[str]):
    class Client:
        def list_memories(self, *, user_id, statuses, limit, offset, order):
            del user_id, statuses, order
            ids = sorted(readable)
            rows = [{"memory_id": item} for item in ids[offset : offset + limit]]
            return {"vdb": {"memories": rows, "total": len(ids)}}

        def get(self, memory_id):
            return {"memory_id": memory_id} if memory_id in readable else None

    @contextmanager
    def open_client(_profile):
        yield Client()

    return open_client


def _run(home: Path, *, readable=frozenset({"m1", "m2"}), report=None):
    return run_startup_migration(
        data_dir=home,
        runtime_build="22",
        sdk_version="2.0.5",
        namespace=NAMESPACE,
        open_client=_reading(set(readable)),
        restore_report=report,
    )


def _events(home: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log_path(home).read_text().splitlines()]


def _fail_schema_migrate(monkeypatch, home):
    import one_memory.memory_server.migration_runner as runner_module

    async def half_migrated(store, migrations):
        del store, migrations
        with main_database(home / "one-memory.sqlite3").write() as connection, connection:
            connection.execute("CREATE TABLE half_applied_migration (x INTEGER)")
        raise DataPlaneMigrationError("sdk_migration_schema_failed")

    monkeypatch.setattr(runner_module, "migrate", half_migrated)


def _fail_legacy_work(monkeypatch, home):
    import one_memory.sdk_migration_runner as runner

    def broken(_database_path):
        raise DataPlaneMigrationError("sdk_data_plane_dispatch_failed")

    monkeypatch.setattr(runner, "move_pending_dispatches", broken)


def _fail_vectors(monkeypatch, home):
    import one_memory.sdk_migration_runner as runner

    real = runner.reattach_legacy_vectors

    def renamed_then_failed(data_dir, profile):
        real(data_dir, profile)
        raise DataPlaneMigrationError("sdk_migration_vector_reattach_incomplete")

    monkeypatch.setattr(runner, "reattach_legacy_vectors", renamed_then_failed)


FAILURES = {
    "prepare.schema_migrate": (_fail_schema_migrate, "sdk_migration_schema_failed"),
    "prepare.legacy_work": (_fail_legacy_work, "sdk_data_plane_dispatch_failed"),
    "vectors.reattach": (_fail_vectors, "sdk_migration_vector_reattach_incomplete"),
}


@pytest.mark.parametrize("step", sorted(FAILURES))
def test_a_failure_before_cutover_restores_the_home_exactly(home, monkeypatch, step):
    counts = _collections(home)
    before = _state(home)
    inject, code = FAILURES[step]
    inject(monkeypatch, home)
    report: dict[str, object] = {}

    with pytest.raises(DataPlaneMigrationError) as caught:
        _run(home, report=report)

    # The migration's own failure is what the caller sees.
    assert caught.value.code == code
    assert report == {"state": "restored"}
    assert _state(home) == before
    assert _collections(home) == counts
    store = SdkDataPlaneMigrationStore(home / "one-memory.sqlite3")
    assert store.authority() is MemoryAuthority.LEGACY
    # Nothing left to resume: the next run starts from the original data.
    assert not (home / RESTORE_POINT).exists()
    assert not restore_pending(home)
    # The failed state is kept aside for diagnosis; capture stays frozen.
    assert (failed_state_path(home) / "management" / "one-memory.sqlite3").is_file()
    assert sdk_migration_capture_fenced(home)
    events = _events(home)
    failed_at = next(
        index
        for index, line in enumerate(events)
        if line["event"] == "step.failed" and line["details"]["step"] == step
    )
    finished = next(line for line in events if line["event"] == "restore.finished")
    assert events.index(finished) > failed_at
    assert finished["details"]["files"] > 0 and finished["details"]["bytes"] > 0
    assert finished["details"]["reason"] == "migration_failed"
    assert "duration_ms" in finished["details"]

    monkeypatch.undo()
    _retry_succeeds(home, monkeypatch)


def test_a_readback_failure_restores_and_a_fixed_build_succeeds(home, monkeypatch):
    before = _state(home)
    report: dict[str, object] = {}

    with pytest.raises(MemoryReadbackFailed):
        _run(home, readable={"m1"}, report=report)

    assert report == {"state": "restored"}
    assert _state(home) == before
    # The ledger row the failure wrote went with the rest.
    with closing(sqlite3.connect(home / "one-memory.sqlite3")) as connection:
        assert not connection.execute(
            "SELECT 1 FROM sdk_data_plane_migrations WHERE state='blocked'"
        ).fetchall()
    _retry_succeeds(home, monkeypatch)


def _retry_succeeds(home: Path, monkeypatch) -> None:
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot, vector_collection_bases

    snapshot = builtin_retrieval_snapshot()
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: snapshot)
    restored_bundles = sorted((home / "backups").glob("*.sdk-bundle/restored.json"))
    assert restored_bundles

    result = _run(home)

    assert result.state == "cutover"
    store = SdkDataPlaneMigrationStore(home / "one-memory.sqlite3")
    assert store.authority() is MemoryAuthority.SDK
    current, _legacy = vector_collection_bases(snapshot)
    assert _collections(home) == {f"{current}_4": 3}
    assert not sdk_migration_capture_fenced(home)
    assert not (home / RESTORE_POINT).exists()
    assert not failed_state_path(home).exists()
    # The restored bundle was superseded by this run's verified one.
    assert not any(path.exists() for path in restored_bundles)
    assert len(list((home / "backups").glob("*-pre-cutover.sdk-bundle"))) == 1


def test_a_failure_after_the_cutover_commit_is_not_restored(home, monkeypatch):
    import one_memory.sdk_migration_runner as runner

    def broken_cleanup(**_options):
        raise DataPlaneMigrationError("sdk_data_plane_cleanup_failed")

    monkeypatch.setattr(runner, "resume_legacy_cleanup", broken_cleanup)
    report: dict[str, object] = {}

    with pytest.raises(DataPlaneMigrationError):
        _run(home, report=report)

    assert report == {}
    store = SdkDataPlaneMigrationStore(home / "one-memory.sqlite3")
    assert store.authority() is MemoryAuthority.SDK
    assert legacy_tables_present(home / "one-memory.sqlite3")
    assert not (home / RESTORE_POINT).exists()
    assert not any(line["event"].startswith("restore.") for line in _events(home))

    monkeypatch.undo()
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

    snapshot = builtin_retrieval_snapshot()
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: snapshot)
    assert _run(home).state == "complete"


def test_a_commit_that_landed_before_its_step_failed_is_not_restored(home, monkeypatch):
    import one_memory.sdk_migration_runner as runner

    real = runner.commit_sdk_cutover

    def committed_then_failed(**options):
        real(**options)
        raise DataPlaneMigrationError("sdk_data_plane_cutover_missing")

    monkeypatch.setattr(runner, "commit_sdk_cutover", committed_then_failed)
    report: dict[str, object] = {}

    with pytest.raises(DataPlaneMigrationError):
        _run(home, report=report)

    assert report == {"state": "skipped"}
    assert (
        SdkDataPlaneMigrationStore(home / "one-memory.sqlite3").authority() is MemoryAuthority.SDK
    )
    skipped = [line for line in _events(home) if line["event"] == "restore.skipped"]
    assert len(skipped) == 1


def test_a_failed_restore_is_logged_and_the_original_code_is_kept(home, monkeypatch):
    import one_memory.sdk_migration_runner as runner

    def corrupt_bundle_then_fail(_database_path):
        bundle = next((home / "backups").glob("*-pre-cutover.sdk-bundle"))
        (bundle / "management" / "one-memory.sqlite3").write_bytes(b"not the backup")
        raise DataPlaneMigrationError("sdk_data_plane_dispatch_failed")

    monkeypatch.setattr(runner, "move_pending_dispatches", corrupt_bundle_then_fail)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=home))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    output = io.StringIO()

    code = cli.run(["sdk-migrate", "--runtime-build", "22", "--yes", "--json"], output=output)

    document = json.loads(output.getvalue().splitlines()[-1])
    assert code == 1
    assert document["code"] == "sdk_data_plane_dispatch_failed"
    assert document["data_restored"] is False
    assert document["restore_code"] == "sdk_migration_restore_failed"
    events = _events(home)
    restore_failed = next(line for line in events if line["event"] == "restore.failed")
    assert restore_failed["code"] == "sdk_migration_restore_failed"
    assert restore_failed["error"]["chain"]
    migrate_failed = next(line for line in events if line["event"] == "migrate.failed")
    assert migrate_failed["code"] == "sdk_data_plane_dispatch_failed"
    assert migrate_failed["details"]["restore"] == "failed"
    # Nothing was swapped; the restore point stays and the next run refuses
    # to migrate over the half-migrated home until a restore succeeds.
    assert (home / RESTORE_POINT).exists()
    assert not restore_pending(home)
    monkeypatch.undo()
    with pytest.raises(MigrationRestoreFailed):
        _run(home)


def test_a_restore_killed_between_renames_is_finished_before_anything_reads(home, monkeypatch):
    import one_memory.sdk_migration_restore as restore_module

    before = _state(home)
    real_apply = restore_module._apply

    def killed_part_way(target, steps, done):
        real_apply(target, steps[:5], done)
        raise KeyboardInterrupt

    _fail_vectors(monkeypatch, home)
    monkeypatch.setattr(restore_module, "_apply", killed_part_way)

    with pytest.raises(KeyboardInterrupt):
        _run(home)

    assert restore_pending(home)
    # The database was renamed aside: Core must not serve, and the probe
    # must not call this a fresh home.
    assert not (home / "one-memory.sqlite3").exists()
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=home))
    status = io.StringIO()
    assert cli.run(["sdk-migration-status", "--json"], output=status) == 0
    assert json.loads(status.getvalue())["state"] == "required"
    with pytest.raises(SystemExit) as refused:
        cli._refuse_unmigrated_home(home)
    assert refused.value.code == cli.SDK_MIGRATION_REQUIRED_EXIT

    monkeypatch.undo()
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

    snapshot = builtin_retrieval_snapshot()
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: snapshot)

    assert finish_interrupted_restore(home)

    assert not restore_pending(home)
    assert _state(home) == before


def test_a_run_killed_after_its_restore_point_is_rolled_back_on_the_next_start(home, monkeypatch):
    import one_memory.sdk_migration_runner as runner

    before = _state(home)
    real = runner.reattach_legacy_vectors

    def renamed_then_killed(data_dir, profile):
        real(data_dir, profile)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "reattach_legacy_vectors", renamed_then_killed)
    with pytest.raises(KeyboardInterrupt):
        _run(home)
    assert (home / RESTORE_POINT).exists()
    monkeypatch.undo()
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

    snapshot = builtin_retrieval_snapshot()
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: snapshot)

    # The next start first puts the home back, then migrates it from there.
    def failing_readback(_profile):
        raise DataPlaneMigrationError("sdk_migration_secure_storage_unavailable")

    with pytest.raises(DataPlaneMigrationError):
        run_startup_migration(
            data_dir=home,
            runtime_build="22",
            sdk_version="2.0.5",
            namespace=NAMESPACE,
            open_client=failing_readback,
        )
    reasons = [
        line["details"]["reason"] for line in _events(home) if line["event"] == "restore.finished"
    ]
    assert reasons == ["interrupted_attempt", "migration_failed"]
    assert _state(home) == before


def test_a_preflight_failure_changes_nothing_and_needs_no_restore(home, monkeypatch):
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: None)
    before = _state(home)
    report: dict[str, object] = {}

    with pytest.raises(DataPlaneMigrationError, match="sdk_migration_model_profile_required"):
        _run(home, report=report)

    assert report == {}
    assert _state(home) == before
    assert not (home / "backups").exists() or not list((home / "backups").iterdir())


def test_a_frozen_capture_fence_holds_until_a_migration_lifts_it(tmp_path):
    marker = tmp_path / ".sdk-migration-capture-fence.json"
    marker.write_text(json.dumps({"schema_version": 1, "frozen": True}), encoding="utf-8")

    assert sdk_migration_capture_fenced(tmp_path)
    assert marker.exists()
