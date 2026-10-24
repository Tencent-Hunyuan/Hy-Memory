from __future__ import annotations

import asyncio
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import cli
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.database_access import close_main_databases, main_database
from one_memory.hy_sdk_types import HySdkRawPage
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.runtime_client import RuntimeStatus
from one_memory.sdk_data_plane_migration import (
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_memory_runtime import SdkMemoryRuntime
from one_memory.sdk_migration_bundle import create_sdk_migration_bundle
from one_memory.sdk_migration_runner import (
    PREPARED_PHASE,
    abandoned_legacy_work,
    commit_sdk_cutover,
    move_pending_dispatches,
    prepare_legacy_write_queue_for_cutover,
    recover_abandoned_staging,
    resume_legacy_cleanup,
    run_startup_migration,
)
from one_memory.secrets import InMemorySecretStore
from one_memory.target_schema import legacy_tables_present


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(database), MIGRATIONS))
    close_main_databases()
    return SdkDataPlaneMigrationStore(database)


def _start(store, migration_id="m1"):
    return store.start(
        migration_id=migration_id,
        source_runtime_build="build-13",
        target_sdk_version="2.0.5",
        source_schema_version=55,
        namespace_fingerprint="namespace",
        backend_fingerprint="backend",
        backup_bundle_id="bundle-1",
    )


def _insert_evidence(store, evidence_id, *, conversation="session-1", role="user", seq=1):
    verbatim = f"text of {evidence_id}"
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,"
            "revision,imported_at,updated_at,ingest_seq) "
            "VALUES (?,?,'local','cursor',?,'raw_conversation',?,?,?,?,1,"
            "'2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00',?)",
            (
                evidence_id,
                evidence_id,
                conversation,
                role,
                "person" if role == "user" else "agent",
                verbatim,
                content_hash(verbatim),
                seq,
            ),
        )


def _insert_task(store, task_id, *, status, invocations):
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO memory_tasks "
            "(task_id,request_id,user_id,kind,input_ref,status,profile_id,"
            "profile_revision,profile_snapshot_json,runner_token,attempts,"
            "last_error_code,created_at,updated_at,duration_seconds,hy_invocation_count) "
            "VALUES (?,?,'local','extract','e-none',?,'profile',1,'{}',NULL,0,NULL,"
            "'now','now',NULL,?)",
            (task_id, f"request-{task_id}", status, invocations),
        )


def test_authority_stays_legacy_without_explicit_cutover(store):
    assert store.authority() is MemoryAuthority.LEGACY
    progress = _start(store)
    assert progress.phase == "preflight"
    assert progress.state == "running"
    assert store.authority() is MemoryAuthority.LEGACY


def test_start_resumes_an_earlier_run_but_keeps_its_bundle(store):
    first = _start(store)
    resumed = store.start(
        migration_id="m1",
        source_runtime_build="build-19",
        target_sdk_version="2.0.6",
        source_schema_version=60,
        namespace_fingerprint="other",
        backend_fingerprint="other",
        backup_bundle_id="bundle-1",
    )
    assert resumed == first

    with pytest.raises(DataPlaneMigrationError) as caught:
        store.start(
            migration_id="m1",
            source_runtime_build="build-13",
            target_sdk_version="2.0.5",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
            backup_bundle_id="bundle-2",
        )
    assert caught.value.code == "sdk_data_plane_migration_bundle_conflict"


def test_cutover_requires_the_prepared_phase(store):
    _start(store)
    commit = {
        "migration_id": "m1",
        "sdk_version": "2.0.5",
        "store_version": "1",
        "backup_bundle_id": "bundle-1",
        "reconciliation_digest": "digest",
    }
    with pytest.raises(DataPlaneMigrationError) as caught:
        store.commit_cutover(**commit)
    assert caught.value.code == "sdk_data_plane_migration_not_ready"

    store.set_phase("m1", phase=PREPARED_PHASE, state="ready")
    marker = store.commit_cutover(**commit)
    assert marker.migration_id == "m1"
    assert store.authority() is MemoryAuthority.SDK


def test_startup_cutover_freezes_pending_job_input_and_preserves_the_queue(store):
    _start(store)
    store.set_phase("m1", phase=PREPARED_PHASE, state="ready")
    data_dir = store.database_path.parent
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,"
            "revision,imported_at,updated_at,ingest_seq) "
            "VALUES ('e1','e1','local','cursor','session-1','raw_conversation',"
            "'user','person','pending input',?,1,"
            "'2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00',1)",
            (content_hash("pending input"),),
        )
    queue = DurableWriteQueue(store.database_path)
    request = WriteRequest(
        internal_session_id="session-1",
        source_id="cursor",
        batch_identity=BatchIdentity(
            internal_session_id="session-1",
            first_round=1,
            last_round=1,
        ),
        evidence_ids=["e1"],
        idempotency_key="request-1",
    )
    job = queue.submit(request)
    create_sdk_migration_bundle(
        data_dir=data_dir,
        bundle_id="bundle-1",
        runtime_build="build-13",
        runtime_stopped=True,
    )
    marker = commit_sdk_cutover(
        data_dir=data_dir,
        database_path=store.database_path,
        migration_id="m1",
        bundle_id="bundle-1",
        sdk_version="2.0.5",
    )
    assert marker.migration_id == "m1"
    assert legacy_tables_present(store.database_path)
    resume_legacy_cleanup(data_dir=data_dir, database_path=store.database_path)
    assert legacy_tables_present(store.database_path) == ()
    assert store.authority() is MemoryAuthority.SDK
    preserved = DurableWriteQueue(store.database_path, sdk_authority=True)
    assert preserved.get("request-1").state == "pending"
    assert preserved.payload_for(job.job_id).input_records[0].verbatim == "pending input"


def test_unstarted_legacy_task_moves_to_surviving_write_queue(store):
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,"
            "revision,imported_at,updated_at,ingest_seq) "
            "VALUES ('e2','e2','local','cursor','session-2','raw_conversation',"
            "'user','person','legacy task input',?,1,"
            "'2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00',2)",
            (content_hash("legacy task input"),),
        )
        database.execute(
            "INSERT INTO memory_tasks "
            "(task_id,request_id,user_id,kind,input_ref,status,profile_id,"
            "profile_revision,profile_snapshot_json,runner_token,attempts,"
            "last_error_code,created_at,updated_at,duration_seconds,hy_invocation_count) "
            "VALUES ('task-legacy','request-legacy','local','extract','e2','pending',"
            "'profile',1,'{}',NULL,0,NULL,'now','now',NULL,0)"
        )
        database.execute(
            "INSERT INTO memory_task_inputs(task_id,refs_json) VALUES (?,?)",
            (
                "task-legacy",
                json.dumps(
                    [
                        {
                            "evidence_id": "e2",
                            "revision": 1,
                            "content_hash": content_hash("legacy task input"),
                        }
                    ]
                ),
            ),
        )

    assert prepare_legacy_write_queue_for_cutover(store.database_path) == (0, 1)

    queue = DurableWriteQueue(store.database_path)
    migrated = queue.get("legacy-task:task-legacy")
    assert migrated is not None and migrated.state == "pending"
    assert queue.payload_for(migrated.job_id).input_records[0].verbatim == "legacy task input"
    with main_database(store.database_path).read() as database:
        assert database.execute(
            "SELECT status,last_error_code FROM memory_tasks WHERE task_id='task-legacy'"
        ).fetchone() == ("cancelled", "migrated_to_memory_write_job")


def test_finished_jobs_without_input_do_not_stop_the_migration(store):
    """Seen on a real home: completed jobs whose payload the queue released."""
    from one_memory.memory_server.durable_queue import REAPED_PAYLOAD_MARKER

    queue = DurableWriteQueue(store.database_path)

    def job(key: str) -> str:
        return queue.submit(
            WriteRequest(
                internal_session_id="session-1",
                source_id="cursor",
                batch_identity=BatchIdentity(
                    internal_session_id="session-1", first_round=1, last_round=1
                ),
                evidence_ids=["gone"],
                idempotency_key=key,
            )
        ).job_id

    reaped, finished = job("reaped"), job("finished")
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "UPDATE memory_write_jobs SET state='completed',payload_json=? WHERE job_id=?",
            (json.dumps({REAPED_PAYLOAD_MARKER: "digest"}), reaped),
        )
        database.execute(
            "UPDATE memory_write_jobs SET state='completed' WHERE job_id=?", (finished,)
        )

    assert prepare_legacy_write_queue_for_cutover(store.database_path) == (0, 0)

    job("pending-without-input")
    with pytest.raises(DataPlaneMigrationError, match="write_job_input_missing"):
        prepare_legacy_write_queue_for_cutover(store.database_path)


def _chroma(home: Path):
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.PersistentClient(
        path=str(home / "hy-memory" / "governed-v2" / "vector"),
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def _fill(client, name: str, ids: list[str], *, text: str = "old") -> None:
    collection = client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
    collection.upsert(
        ids=ids,
        embeddings=[[float(index + 1)] * 4 for index, _ in enumerate(ids)],
        documents=[f"{text} {identifier}" for identifier in ids],
        metadatas=[{"layer": "l2_fact"} for _ in ids],
    )


def test_legacy_vectors_are_renamed_and_newer_writes_are_kept(tmp_path) -> None:
    """Build 20: 3317 memories in `one_memory_*`, the Runtime reading an empty `memhub_*`."""
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot, vector_collection_bases
    from one_memory.sdk_vector_reattach import (
        legacy_vector_collections,
        reattach_legacy_vectors,
    )

    profile = builtin_retrieval_snapshot()
    current, legacy = vector_collection_bases(profile)
    client = _chroma(tmp_path)
    _fill(client, f"{legacy[0]}_4", ["a", "b", "both"])
    _fill(client, f"{legacy[0]}_coding_keys_cv_4", ["k1"])
    _fill(client, f"{current}_4", ["new", "both"], text="newer")
    del client

    assert len(legacy_vector_collections(tmp_path, profile)) == 2
    done = reattach_legacy_vectors(tmp_path, profile)

    assert {(item.current, item.final_count) for item in done} == {
        (f"{current}_4", 4),
        (f"{current}_coding_keys_cv_4", 1),
    }
    assert legacy_vector_collections(tmp_path, profile) == ()
    client = _chroma(tmp_path)
    assert sorted(collection.name for collection in client.list_collections()) == [
        f"{current}_4",
        f"{current}_coding_keys_cv_4",
    ]
    chat = client.get_collection(f"{current}_4").get(ids=["both"], include=["documents"])
    assert chat["documents"] == ["newer both"]
    assert reattach_legacy_vectors(tmp_path, profile) == ()


def _confirmed(store, *memory_ids: str, kind: str = "chat") -> None:
    with main_database(store.database_path).write() as database, database:
        for memory_id in memory_ids:
            database.execute(
                "INSERT INTO governed_memories (memory_id,user_id,kind,status,content,"
                "scopes_json,revision,created_at,updated_at) "
                "VALUES (?,'local',?,'confirmed','x','[]',1,'now','now')",
                (memory_id, kind),
            )


class _ReadingClient:
    """Public-API shaped SDK client that holds only `readable` ids."""

    def __init__(self, readable: set[str]) -> None:
        self.readable = readable

    def list_memories(self, *, user_id, statuses, limit, offset, order):
        del user_id, statuses, order
        ids = sorted(self.readable)
        rows = [{"memory_id": memory_id} for memory_id in ids[offset : offset + limit]]
        return {"vdb": {"memories": rows, "total": len(ids)}}

    def get(self, memory_id):
        return {"memory_id": memory_id} if memory_id in self.readable else None


def _reading(readable: set[str]):
    from contextlib import contextmanager

    @contextmanager
    def open_client(_profile):
        yield _ReadingClient(readable)

    return open_client


@pytest.fixture
def active_profile(monkeypatch):
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

    profile = builtin_retrieval_snapshot()
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: profile)
    monkeypatch.setattr("one_memory.sdk_migration_verify.read_active_profile", lambda _db: profile)
    return profile


def test_cutover_waits_until_the_new_runtime_reads_every_chat_memory(store, active_profile):
    """Build 20 reported success on 37 of 3317. Now the legacy tables stay."""
    from one_memory.sdk_migration_verify import MemoryReadbackFailed

    _confirmed(store, "m1", "m2", "m3")
    home = store.database_path.parent

    with pytest.raises(MemoryReadbackFailed) as caught:
        run_startup_migration(
            data_dir=home,
            runtime_build="21",
            sdk_version="2.0.5",
            namespace="governed-v2",
            open_client=_reading({"m1"}),
        )

    assert caught.value.code == "sdk_migration_memory_unreadable"
    assert caught.value.report.as_document() == {
        "chat_expected": 3,
        "chat_readable": 1,
        "productivity_expected": 0,
        "productivity_readable": 0,
        "passed": False,
    }
    assert store.authority() is MemoryAuthority.LEGACY
    assert "governed_memories" in legacy_tables_present(store.database_path)

    result = run_startup_migration(
        data_dir=home,
        runtime_build="21",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"m1", "m2", "m3"}),
    )

    assert result.state == "cutover"
    assert result.readback is not None and result.readback.passed
    assert store.authority() is MemoryAuthority.SDK


def test_retired_coding_experiences_do_not_block_but_an_empty_store_does(store, active_profile):
    _confirmed(store, "chat-1")
    _confirmed(store, "exp-kept", "exp-retired", kind="coding")
    home = store.database_path.parent
    from one_memory.sdk_migration_verify import MemoryReadbackFailed

    with pytest.raises(MemoryReadbackFailed):
        run_startup_migration(
            data_dir=home,
            runtime_build="21",
            sdk_version="2.0.5",
            namespace="governed-v2",
            open_client=_reading({"chat-1"}),
        )
    result = run_startup_migration(
        data_dir=home,
        runtime_build="21",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"chat-1", "exp-kept"}),
    )
    assert result.readback is not None
    assert (result.readback.productivity_expected, result.readback.productivity_readable) == (2, 1)


def test_memories_without_a_model_profile_keep_legacy_authority(store, monkeypatch):
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: None)
    _confirmed(store, "m1")

    with pytest.raises(DataPlaneMigrationError, match="sdk_migration_model_profile_required"):
        run_startup_migration(
            data_dir=store.database_path.parent,
            runtime_build="21",
            sdk_version="2.0.5",
            namespace="governed-v2",
        )
    assert store.authority() is MemoryAuthority.LEGACY


def test_a_build_20_home_gets_its_vectors_back_and_is_checked_against_its_backup(
    store, active_profile, monkeypatch
):
    from one_memory.hy_sdk_runtime import vector_collection_bases

    home = store.database_path.parent
    current, legacy_bases = vector_collection_bases(active_profile)
    client = _chroma(home)
    _fill(client, f"{legacy_bases[0]}_512", ["m1", "m2"])
    _fill(client, f"{current}_512", ["after-upgrade"], text="newer")
    del client
    _confirmed(store, "m1", "m2")
    first = run_startup_migration(
        data_dir=home,
        runtime_build="20",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"m1", "m2"}),
    )
    assert first.state == "cutover"
    # Reproduce build 20: cut over while the legacy collection stayed stranded.
    client = _chroma(home)
    client.get_collection(f"{current}_512").modify(name=f"{legacy_bases[0]}_512")
    _fill(client, f"{current}_512", ["after-upgrade"], text="newer")
    del client
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=home))
    status = io.StringIO()
    assert cli.run(["sdk-migration-status", "--json"], output=status) == 0
    assert json.loads(status.getvalue())["state"] == "required"

    repaired = run_startup_migration(
        data_dir=home,
        runtime_build="21",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"m1", "m2", "after-upgrade"}),
    )

    assert repaired.state == "complete"
    assert [(item.current, item.final_count) for item in repaired.reattached] == [
        (f"{current}_512", 3)
    ]
    assert repaired.readback is not None and repaired.readback.passed
    assert repaired.readback.chat_expected == 2
    status = io.StringIO()
    assert cli.run(["sdk-migration-status", "--json"], output=status) == 0
    assert json.loads(status.getvalue())["state"] == "not_required"


def test_pending_dispatch_becomes_one_write_job_per_conversation(store):
    _insert_evidence(store, "a1", conversation="a", seq=1)
    _insert_evidence(store, "a2", conversation="a", role="assistant", seq=2)
    _insert_evidence(store, "b1", conversation="b", seq=3)
    with main_database(store.database_path).write() as database, database:
        for evidence_id in ("a1", "a2", "b1"):
            database.execute(
                "INSERT INTO evidence_pending_memory_dispatch "
                "(user_id,source_id,file_key,evidence_id) VALUES ('local','cursor','f',?)",
                (evidence_id,),
            )

    assert move_pending_dispatches(store.database_path) == 2
    assert move_pending_dispatches(store.database_path) == 2

    with main_database(store.database_path).read() as database:
        payloads = [
            json.loads(row[0])
            for row in database.execute("SELECT payload_json FROM memory_write_jobs")
        ]
    assert sorted(
        [record["evidence_id"] for record in payload["input_records"]] for payload in payloads
    ) == [["a1", "a2"], ["b1"]]


def test_legacy_work_that_may_have_reached_the_sdk_does_not_block_cutover(tmp_path, store):
    """Build 19 stopped here forever: a blocker no startup could ever clear."""
    _insert_task(store, "sent", status="unknown", invocations=1)
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO governed_memories (memory_id,user_id,kind,status,content,"
            "scopes_json,revision,created_at,updated_at) "
            "VALUES ('g1','local','chat','confirmed','x','[]',1,'now','now')"
        )
        database.execute(
            "INSERT INTO memory_deletions (memory_id,status,updated_at) "
            "VALUES ('g1','pending','now')"
        )
    with main_database(store.database_path).write() as database, database:
        database.execute("INSERT INTO matters(matter_id,version,needs_update) VALUES ('m1',1,0)")
    assert abandoned_legacy_work(store.database_path) == {"invoked_tasks": 1, "deletions": 1}

    _start(store)
    store.set_phase("m1", phase=PREPARED_PHASE, state="ready")
    create_sdk_migration_bundle(
        data_dir=store.database_path.parent,
        bundle_id="bundle-1",
        runtime_build="build-13",
        runtime_stopped=True,
    )
    commit_sdk_cutover(
        data_dir=store.database_path.parent,
        database_path=store.database_path,
        migration_id="m1",
        bundle_id="bundle-1",
        sdk_version="2.0.5",
    )
    assert store.authority() is MemoryAuthority.SDK


def _protective(home: Path) -> Path:
    return home / "hy-memory" / ".governed-v2.pre-migration-0123456789abcdef"


def test_interrupted_swap_before_authority_restores_the_original_store(tmp_path, store):
    home = store.database_path.parent
    live = home / "hy-memory" / "governed-v2"
    live.mkdir(parents=True)
    (live / "store.txt").write_text("staged", encoding="utf-8")
    _protective(home).mkdir()
    (_protective(home) / "store.txt").write_text("original", encoding="utf-8")
    (home / ".sdk-migration" / "token").mkdir(parents=True)

    recover_abandoned_staging(
        data_dir=home,
        database_path=store.database_path,
        namespace="governed-v2",
    )

    assert (live / "store.txt").read_text(encoding="utf-8") == "original"
    assert not _protective(home).exists()
    assert not (home / ".sdk-migration").exists()


def test_stale_protective_root_after_authority_is_discarded(tmp_path, store):
    home = store.database_path.parent
    live = home / "hy-memory" / "governed-v2"
    live.mkdir(parents=True)
    (live / "store.txt").write_text("live", encoding="utf-8")
    _protective(home).mkdir()
    _start(store)
    store.set_phase("m1", phase=PREPARED_PHASE, state="ready")
    store.commit_cutover(
        migration_id="m1",
        sdk_version="2.0.5",
        store_version="1",
        backup_bundle_id="bundle-1",
        reconciliation_digest="digest",
    )

    recover_abandoned_staging(
        data_dir=home,
        database_path=store.database_path,
        namespace="governed-v2",
    )

    assert (live / "store.txt").read_text(encoding="utf-8") == "live"
    assert not _protective(home).exists()


def test_host_capture_is_fenced_for_the_whole_migration(store):
    from one_memory.live_capture import sdk_migration_capture_fenced

    home = store.database_path.parent
    fenced: dict[str, bool] = {}

    result = run_startup_migration(
        data_dir=home,
        runtime_build="20",
        sdk_version="2.0.5",
        namespace="governed-v2",
        progress=lambda step: fenced.__setitem__(step, sdk_migration_capture_fenced(home)),
    )

    assert result.state == "cutover"
    assert fenced == {"bundle": True, "prepare": True, "cutover": True, "cleanup": True}
    assert not sdk_migration_capture_fenced(home)


def test_a_home_blocked_by_build_19_finishes_on_the_next_startup(tmp_path, monkeypatch, store):
    """The quarantined ledger, its staging copy and pending queue work all resolve."""
    home = store.database_path.parent
    live = home / "hy-memory" / "governed-v2"
    live.mkdir(parents=True)
    (live / "coding.sqlite3").write_text("sdk data", encoding="utf-8")
    staging = home / ".sdk-migration" / "token" / "hy-memory" / "governed-v2"
    staging.mkdir(parents=True)
    store.start(
        migration_id="sdk-authority-v2-73b3561be69371e0",
        source_runtime_build="19",
        target_sdk_version="2.0.6",
        source_schema_version=60,
        namespace_fingerprint="old",
        backend_fingerprint="old-with-embedding",
        backup_bundle_id="sdk-authority-v2-73b3561be69371e0-pre-cutover",
    )
    store.set_phase(
        "sdk-authority-v2-73b3561be69371e0",
        phase="review_required",
        state="blocked",
    )
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO sdk_data_plane_migration_items (migration_id,item_key,"
            "source_kind,source_id,source_hash,operation_key,state,error_code,updated_at) "
            "VALUES ('sdk-authority-v2-73b3561be69371e0','memory:exp-1',"
            "'memory:quarantine','exp-1','hash','op','quarantine',"
            "'mapped_productivity_memory_missing','now')"
        )
    _insert_evidence(store, "e1")
    DurableWriteQueue(store.database_path).submit(
        WriteRequest(
            internal_session_id="session-1",
            source_id="cursor",
            batch_identity=BatchIdentity(
                internal_session_id="session-1", first_round=1, last_round=1
            ),
            evidence_ids=["e1"],
            idempotency_key="pending-job",
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=home))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    output = io.StringIO()
    code = cli.run(["sdk-migrate", "--runtime-build", "20", "--yes", "--json"], output=output)
    result = json.loads(output.getvalue().splitlines()[-1])

    assert (code, result["state"]) == (0, "cutover"), output.getvalue()
    assert result["migration_id"] == "sdk-authority-v2-73b3561be69371e0"
    assert result["bundle_id"] == "sdk-authority-v2-73b3561be69371e0-pre-cutover"
    assert result["frozen_jobs"] == 1
    assert store.authority() is MemoryAuthority.SDK
    assert legacy_tables_present(store.database_path) == ()
    with main_database(store.database_path).read() as database:
        assert not database.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'evidence%'"
        ).fetchall()
    assert (live / "coding.sqlite3").read_text(encoding="utf-8") == "sdk data"
    assert not (home / ".sdk-migration").exists()
    queue = DurableWriteQueue(store.database_path, sdk_authority=True)
    job = queue.get("pending-job")
    assert job is not None and job.state == "pending"
    assert queue.payload_for(job.job_id).input_records[0].verbatim == "text of e1"


def test_raw_http_dto_uses_the_unified_memory_list_shape():
    page = HySdkRawPage.from_memory_list(
        {
            "vdb": {
                "memories": [
                    {
                        "memory_id": "raw-1",
                        "content": "verbatim",
                        "layer": "l1_raw",
                        "status": "active",
                        "user_id": "local",
                        "agent_id": "one-memory",
                        "session_id": "session",
                        "source": "cursor",
                        "custom": {"role": "user"},
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
            }
        },
        limit=100,
        offset=0,
    )
    assert page.raw[0].raw_id == "raw-1"
    assert page.raw[0].custom == {"role": "user"}


def test_sdk_raw_retention_keeps_only_the_latest_three_days():
    now = datetime(2026, 10, 23, tzinfo=UTC)

    class Client:
        def __init__(self):
            self.rows = [
                {
                    "memory_id": "old",
                    "memory_at": int((now - timedelta(days=4)).timestamp()),
                },
                {
                    "memory_id": "recent",
                    "memory_at": int((now - timedelta(days=2)).timestamp()),
                },
            ]

        def list_memories(self, **_kwargs):
            return {
                "vdb": {
                    "memories": list(self.rows),
                    "total": len(self.rows),
                    "limit": 500,
                    "offset": 0,
                }
            }

        def delete(self, memory_id):
            before = len(self.rows)
            self.rows = [row for row in self.rows if row["memory_id"] != memory_id]
            return {
                "success": True,
                "deleted_count": before - len(self.rows),
            }

    client = Client()

    class Handle:
        async def client(self):
            return client

    runtime = SdkMemoryRuntime(
        coordinator=None,
        read_handle=Handle(),
        write_queue=None,
        extraction_settings=None,
        data_dir=Path("."),
    )
    assert asyncio.run(runtime.prune_expired_raw(now=now)) == 1
    assert [row["memory_id"] for row in client.rows] == ["recent"]


# The UI asks which memory data plane is authoritative and must get the
# answer the API gates use.


def _app_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        source_home=tmp_path,
        api_token=SecretStr("authority-test"),
        imports_enabled=False,
        automatic_memory_processing=False,
        live_sync_enabled=False,
    )


async def _authority(tmp_path) -> tuple[dict[str, object], str]:
    app = create_app(_app_settings(tmp_path), secret_store=InMemorySecretStore())
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer authority-test"},
        ) as client,
    ):
        response = await client.get("/api/v2/memory-authority")
        assert response.status_code == 200
        return response.json(), app.state.memory_authority.value


@pytest.mark.integration
@pytest.mark.fresh_sdk
def test_a_fresh_home_reports_sdk_authority(tmp_path) -> None:
    document, gate = asyncio.run(_authority(tmp_path))

    assert document == {"authority": "sdk", "migration_id": None}
    assert gate == "sdk"


@pytest.mark.integration
def test_a_migrated_home_still_reports_sdk_after_its_receipt_is_dropped(
    tmp_path, monkeypatch
) -> None:
    """Build 20: the cleanup dropped the cutover receipt, the UI fell back to legacy."""
    data_dir = tmp_path / "data"
    EvidenceStore(data_dir / "one-memory.sqlite3")
    monkeypatch.setattr(cli, "load_settings", lambda: _app_settings(tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    output = io.StringIO()
    assert cli.run(["sdk-migrate", "--runtime-build", "21", "--yes", "--json"], output=output) == 0
    migration_id = json.loads(output.getvalue().splitlines()[-1])["migration_id"]
    assert legacy_tables_present(data_dir / "one-memory.sqlite3") == ()

    document, gate = asyncio.run(_authority(tmp_path))

    assert document == {"authority": "sdk", "migration_id": migration_id}
    assert gate == "sdk"


@pytest.mark.integration
def test_a_legacy_home_reports_legacy_authority(tmp_path) -> None:
    document, gate = asyncio.run(_authority(tmp_path))

    assert document == {"authority": "legacy", "migration_id": None}
    assert gate == "legacy"
